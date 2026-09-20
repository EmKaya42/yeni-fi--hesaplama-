"""Local neural OCR with a bounded worker process; no document network calls."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from PIL import Image, ImageOps

from services.document_extraction import extract_document, folded
from services.ocr_layout import layout_text
from services.ocr_models import BASE_DIR, verified_model_paths

ENGINE_NAME = 'PaddleOCR · PP-OCRv6 · yerel'
FIELD_LABELS = {
    'seller_name': 'Firma unvanı', 'items': 'Ürün satırları', 'document_type': 'Belge türü',
    'document_series': 'Belge serisi', 'document_no': 'Belge numarası',
    'document_datetime': 'Tarih / saat', 'document_time': 'Saat', 'tax_id': 'VKN / TCKN',
    'tax_office': 'Vergi dairesi', 'fiscal_id': 'Mali sicil numarası', 'device_no': 'Cihaz numarası',
    'transaction_count': 'Fiş / işlem adedi', 'adjustments': 'İndirim / iptal / iade',
    'cumulative_sales': 'Kümülatif satış', 'cumulative_vat': 'Kümülatif KDV',
    'total_amount': 'Genel toplam', 'vat_amount': 'Toplam KDV', 'vat_breakdown': 'KDV dağılımı',
    'payment_entries': 'Ödeme dağılımı', 'bank_evidence': 'Banka bilgileri',
}


def create_engine():
    paths = verified_model_paths()  # Fail locally; never auto-download while reading a document.
    try:
        import onnxruntime
        from rapidocr import RapidOCR, ModelType, OCRVersion
    except ImportError as error:
        raise RuntimeError('PaddleOCR paketleri eksik. requirements.txt bağımlılıklarını kurun.') from error
    onnxruntime.disable_telemetry_events()
    return RapidOCR(params={
        'Global.log_level': 'error', 'Global.max_side_len': 4096, 'Global.text_score': .35,
        'Det.model_path': paths['Det'], 'Cls.model_path': paths['Cls'], 'Rec.model_path': paths['Rec'],
        'Det.model_type': ModelType.MOBILE, 'Det.ocr_version': OCRVersion.PPOCRV5,
        'Rec.model_type': ModelType.SMALL, 'Rec.ocr_version': OCRVersion.PPOCRV6,
        'EngineConfig.onnxruntime.intra_op_num_threads': 2,
        'EngineConfig.onnxruntime.inter_op_num_threads': 1,
    })


def uncertain_fields(data, rows):
    issues = []
    for field, label in (('tax_id', 'VKN / TCKN'), ('document_no', 'Belge numarası'),
                         ('fiscal_id', 'Mali sicil numarası'), ('device_no', 'Cihaz numarası')):
        value = re.sub(r'[^A-Z0-9]', '', folded(str(data.get(field) or '')))
        if len(value) < 4:
            continue
        for row in rows:
            for region in row['regions']:
                compact = re.sub(r'[^A-Z0-9]', '', folded(region['text']))
                if value in compact and region['confidence'] < 65:
                    issues.append(f'{label} okuma güveni düşük. Kaynak belgeyi kontrol edip daha net bir görselle yeniden deneyin.')
    return issues


def read_in_process(path, page, kind, attempt, engine=None):
    import numpy as np
    from services.document_ocr import load_source, VERIFIED_FIELDS, _canonical

    engine = engine or create_engine()
    start = time.monotonic()
    candidates, reads, confidence_issues = [], [], []
    with load_source(path, page) as source:
        factor = min(1, 2400 / source.width, 6000 / source.height,
                     (12_000_000 / (source.width * source.height)) ** .5)
        if factor < 1:
            source.thumbnail((max(1, int(source.width * factor)), max(1, int(source.height * factor))))
        # Keep color and character shapes on the first pass; use contrast on the second.
        for variant in ('original', 'contrast'):
            if variant == 'original':
                prepared = source.copy()
            else:
                with ImageOps.grayscale(source) as gray:
                    prepared = ImageOps.autocontrast(gray, cutoff=.5).convert('RGB')
            if attempt > 1 and prepared.width * prepared.height < 6_000_000:
                enlarged = prepared.resize((int(prepared.width * 1.3), int(prepared.height * 1.3)), Image.Resampling.LANCZOS)
                prepared.close()
                prepared = enlarged
            with prepared:
                raw = engine(np.asarray(prepared)[:, :, ::-1].copy())
            if raw.boxes is None or not raw.txts:
                raise RuntimeError('Görselde okunabilir yazı bulunamadı. Belgenin tamamını gösteren daha net bir fotoğraf yükleyin.')
            text, rows = layout_text(raw.boxes, raw.txts, raw.scores)
            data = extract_document(text, kind)
            confidence_issues.extend(uncertain_fields(data, rows))
            score = round(sum(float(value) for value in raw.scores) / len(raw.scores) * 100, 1)
            if score < 65:
                confidence_issues.append('Görselin okuma güveni düşük. Daha net bir dosya yükleyin.')
            data.update(confidence=score, engine=ENGINE_NAME)
            candidates.append(data)
            reads.append({'variant': variant, 'engine': ENGINE_NAME, 'confidence': score, 'raw_text': text})
    conflicts = [field for field in VERIFIED_FIELDS
                 if _canonical(candidates[0].get(field)) != _canonical(candidates[1].get(field))]
    result = dict(min(candidates, key=lambda data: (len(data['issues']), -data['confidence'])))
    result['issues'] = list(result['issues']) + confidence_issues
    if conflicts:
        # Do not present a partial/cropped name from one read as an established identity.
        for field in ('seller_name', 'tax_office', 'tax_id', 'document_no', 'document_datetime',
                      'document_time', 'fiscal_id', 'device_no'):
            if field in conflicts:
                result[field] = ''
        if 'seller_name' in conflicts:
            result['issues'].insert(0, 'Firma unvanı iki okumada doğrulanamadı. Unvanın tamamının göründüğü bir fotoğraf yükleyin.')
        other_conflicts = [FIELD_LABELS[field] for field in conflicts if field != 'seller_name']
        if other_conflicts:
            result['issues'].append('İki okuma sonucu birlikte doğrulanamadı: ' + ', '.join(other_conflicts) + '. Kaynak belgeyi kontrol edin.')
    result['issues'] = list(dict.fromkeys(result['issues']))
    result.update(ocr_reads=reads, conflicting_fields=conflicts,
                  processing_seconds=round(time.monotonic() - start, 2))
    return result


def read_document(path, page, kind, attempt):
    from services.document_ocr import RetryableOCRError

    verified_model_paths()
    with tempfile.TemporaryDirectory(prefix='fis-ocr-') as temporary:
        output = Path(temporary) / 'result.json'
        command = [sys.executable, '-m', 'services.paddle_ocr', str(Path(path).resolve()),
                   str(page), kind, str(attempt), str(output)]
        env = {**os.environ, 'PYTHONUTF8': '1', 'OMP_NUM_THREADS': '2', 'OPENBLAS_NUM_THREADS': '1'}
        try:
            process = subprocess.run(command, cwd=BASE_DIR, env=env, capture_output=True,
                                     timeout=240, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        except subprocess.TimeoutExpired as error:
            raise RetryableOCRError('PaddleOCR okuması süre sınırına ulaştı. Otomatik yeniden denenecek.') from error
        if not output.exists():
            raise RuntimeError('PaddleOCR işlemi tamamlanamadı. Sunucunun bağımlılıklarını ve kullanılabilir belleğini kontrol edin.')
        payload = json.loads(output.read_text(encoding='utf-8'))
        if process.returncode or 'error' in payload:
            raise RuntimeError(payload.get('error', 'PaddleOCR işlemi tamamlanamadı.'))
        return payload


if __name__ == '__main__':
    path, page, kind, attempt, output = sys.argv[1:]
    try:
        result = read_in_process(Path(path), int(page), kind, int(attempt))
    except Exception as error:
        message = str(error) if isinstance(error, (RuntimeError, ValueError)) else 'PaddleOCR okuması tamamlanamadı. Sunucunun OCR kurulumunu kontrol edin.'
        Path(output).write_text(json.dumps({'error': message}, ensure_ascii=False), encoding='utf-8')
        raise SystemExit(1)
    Path(output).write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
