"""Local neural OCR with a bounded worker process; no document network calls."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from contextlib import ExitStack
from pathlib import Path

from PIL import Image, ImageOps

from services.document_extraction import extract_document, folded
from services.ocr_layout import layout_text
from services.ocr_models import BASE_DIR, verified_model_paths
from services.ocr_process import memory_snapshot, process_failure, report_stage

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
        'Rec.rec_batch_num': 1,
        'Cls.cls_batch_num': 1,
        # Compact mode bounds detection to RapidOCR's maximum 2000px side;
        # recognition still uses crops of the original-resolution input.
        'Det.limit_type': 'max' if os.getenv('OCR_COMPACT') == '1' else 'min',
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


def reread_missing_date(source, candidates, boxes, engine, kind, line_boxes=()):
    """Resolve one missing date only with two matching reads of its source crop."""
    import numpy as np

    missing_time = any(not data.get('document_time') for data in candidates)
    known = {data.get('document_datetime') for data in candidates
             if data.get('document_datetime') and (not missing_time or data.get('document_time'))}
    if len(known) != 1 or (not missing_time and all(data.get('document_datetime') for data in candidates)):
        return []  # Never choose between two different valid dates.
    if any(any('birden fazla tarih' in issue for issue in data['issues']) for data in candidates):
        return []
    expected = next(iter(known))
    if any(data.get('document_datetime') and data['document_datetime'][:10] != expected[:10]
           for data in candidates):
        return []
    valid_boxes = [box for box in boxes if box[3] > box[1] + 3]
    if not valid_boxes:
        return []
    box = valid_boxes[0]
    reads = []
    with source.crop(box) as crop:
        for variant in ('original', 'contrast'):
            prepared = crop.copy() if variant == 'original' else ImageOps.autocontrast(ImageOps.grayscale(crop), cutoff=.5).convert('RGB')
            with prepared:
                raw = engine(np.asarray(prepared)[:, :, ::-1].copy())
            if raw.boxes is None or not raw.txts:
                break
            text, rows = layout_text(raw.boxes, raw.txts, raw.scores)
            parsed = extract_document(text, kind)
            date_rows = [row for row in rows if re.search(r'\b\d{1,2}[./-]\d{1,2}[./-]\d{4}\b', row['text'])]
            verified = (parsed['document_datetime'] == expected and date_rows
                        and min(row['confidence'] for row in date_rows) >= 65
                        and not any('birden fazla tarih' in issue for issue in parsed['issues']))
            reads.append({'field': 'document_datetime', 'variant': variant, 'raw_text': text,
                          'crop_box': box, 'verified': bool(verified)})
    confirmed = len(reads) == 2 and all(read['verified'] for read in reads)
    # The detector can truncate a faint final digit. Re-recognize the complete
    # physical row directly, using two variants and no guessed character edits.
    if not confirmed and line_boxes and hasattr(engine, 'recognize_txt'):
        line_reads = []
        box = line_boxes[0]
        with source.crop(box) as crop:
            for variant in ('original', 'contrast'):
                prepared = crop.copy() if variant == 'original' else ImageOps.autocontrast(ImageOps.grayscale(crop), cutoff=.5).convert('RGB')
                try:
                    with prepared:
                        raw = engine.recognize_txt([np.asarray(prepared)[:, :, ::-1].copy()])
                except Exception:
                    # Optional recovery must not discard the full-page result.
                    line_reads.append({'field': 'document_datetime', 'variant': variant,
                                       'strategy': 'line_recognition', 'raw_text': '',
                                       'crop_box': box, 'verified': False})
                    break
                text = '\n'.join(raw.txts or [])
                parsed = extract_document(text, kind)
                verified = (parsed['document_datetime'] == expected and raw.scores
                            and min(raw.scores) >= .85
                            and not any('birden fazla tarih' in issue for issue in parsed['issues']))
                line_reads.append({'field': 'document_datetime', 'variant': variant,
                                   'strategy': 'line_recognition', 'raw_text': text,
                                   'crop_box': box, 'verified': bool(verified)})
        confirmed = len(line_reads) == 2 and all(read['verified'] for read in line_reads)
        reads.extend(line_reads)
    if confirmed:
        for candidate in candidates:
            candidate['document_datetime'] = expected
            if missing_time:
                candidate['document_time'] = expected.split('T')[1]
            candidate['issues'] = [issue for issue in candidate['issues'] if issue not in {'Tarih okunamadı.', 'Saat okunamadı.'}]
            candidate['notes'].append('Tarih, aynı görseldeki tarih satırı iki kez yakından okunarak doğrulandı.')
    return reads


def prepare_image(source, variant, attempt):
    prepared = source.copy() if variant == 'original' else ImageOps.autocontrast(ImageOps.grayscale(source), cutoff=.5).convert('RGB')
    if attempt > 1 and prepared.width * prepared.height < 6_000_000:
        enlarged = prepared.resize((int(prepared.width * 1.3), int(prepared.height * 1.3)), Image.Resampling.LANCZOS)
        prepared.close()
        prepared = enlarged
    return prepared


def orientation_score(raw, kind):
    if raw.boxes is None or not raw.txts:
        return 0
    text, _ = layout_text(raw.boxes, raw.txts, raw.scores)
    data = extract_document(text, kind)
    # Decide reading direction using complete fiscal anchors, never an amount
    # or a guessed company name alone. The chosen image still needs two reads.
    return sum(bool(data.get(field)) for field in ('tax_id', 'document_no', 'total_amount', 'vat_amount', 'payment_entries'))


def read_in_process(path, page, kind, attempt, engine=None):
    import numpy as np
    from services.document_ocr import load_source, VERIFIED_FIELDS, _canonical

    report_stage('model_loading')
    engine = engine or create_engine()
    start = time.monotonic()
    candidates, reads, confidence_issues, date_boxes, line_boxes = [], [], [], [], []
    rotation = 0
    with ExitStack() as stack:
        report_stage('image_loading')
        source = stack.enter_context(load_source(path, page))
        factor = min(1, 2400 / source.width, 6000 / source.height,
                     (12_000_000 / (source.width * source.height)) ** .5)
        if factor < 1:
            source.thumbnail((max(1, int(source.width * factor)), max(1, int(source.height * factor))))
        report_stage('reading')
        with prepare_image(source, 'original', attempt) as prepared:
            initial = engine(np.asarray(prepared)[:, :, ::-1].copy())
        initial_score = orientation_score(initial, kind)
        if initial_score < 3:
            best_score = initial_score
            for angle in (90, 180, 270):
                with source.rotate(angle, expand=True) as rotated, prepare_image(rotated, 'original', attempt) as prepared:
                    probe = engine(np.asarray(prepared)[:, :, ::-1].copy())
                score = orientation_score(probe, kind)
                if score >= 3 and score > best_score:
                    initial, rotation, best_score = probe, angle, score
            if rotation:
                source = stack.enter_context(source.rotate(rotation, expand=True))
        # Keep color and character shapes on the first pass; use contrast on the second.
        for variant in ('original', 'contrast'):
            with prepare_image(source, variant, attempt) as prepared:
                image_scale = prepared.width / source.width
                raw = initial if variant == 'original' else engine(np.asarray(prepared)[:, :, ::-1].copy())
            if raw.boxes is None or not raw.txts:
                raise RuntimeError('Görselde okunabilir yazı bulunamadı. Belgenin tamamını gösteren daha net bir fotoğraf yükleyin.')
            text, rows = layout_text(raw.boxes, raw.txts, raw.scores)
            for row in rows:
                if re.search(r'\b\d{1,2}[./-]\d{1,2}[./-]\d{4,}\b', row['text']):
                    top = min(point[1] for region in row['regions'] for point in region['box']) / image_scale
                    bottom = max(point[1] for region in row['regions'] for point in region['box']) / image_scale
                    pad = (bottom - top) * .65
                    date_boxes.append([0, max(0, int(top - pad)), source.width, min(source.height, int(bottom + pad))])
                    left = min(point[0] for region in row['regions'] for point in region['box']) / image_scale
                    right = max(point[0] for region in row['regions'] for point in region['box']) / image_scale
                    line_boxes.append([max(0, int(left - pad)), max(0, int(top - pad * .35)),
                                       min(source.width, int(right + pad)), min(source.height, int(bottom + pad * .35))])
            data = extract_document(text, kind)
            confidence_issues.extend(uncertain_fields(data, rows))
            score = round(sum(float(value) for value in raw.scores) / len(raw.scores) * 100, 1)
            if score < 65:
                confidence_issues.append('Görselin okuma güveni düşük. Daha net bir dosya yükleyin.')
            data.update(confidence=score, engine=ENGINE_NAME)
            candidates.append(data)
            reads.append({'variant': variant, 'engine': ENGINE_NAME, 'confidence': score, 'raw_text': text})
        report_stage('verifying')
        detail_reads = reread_missing_date(source, candidates, date_boxes, engine, kind, line_boxes)
    conflicts = [field for field in VERIFIED_FIELDS
                 if _canonical(candidates[0].get(field)) != _canonical(candidates[1].get(field))]
    dates = {data.get('document_datetime', '')[:10] for data in candidates}
    same_date = len(dates) == 1 and '' not in dates
    if same_date and 'document_datetime' in conflicts:
        conflicts.remove('document_datetime')
    result = dict(min(candidates, key=lambda data: (len(data['issues']), -data['confidence'])))
    result['issues'] = list(result['issues']) + confidence_issues
    if same_date and 'document_time' in conflicts:
        result['document_datetime'] = next(iter(dates))
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
    result.update(ocr_reads=reads, detail_reads=detail_reads, conflicting_fields=conflicts, orientation_degrees=rotation,
                  processing_seconds=round(time.monotonic() - start, 2))
    return result


def read_document(path, page, kind, attempt):
    from services.document_ocr import RetryableOCRError

    verified_model_paths()
    with tempfile.TemporaryDirectory(prefix='fis-ocr-') as temporary:
        output = Path(temporary) / 'result.json'
        progress = Path(temporary) / 'stage.txt'
        command = [sys.executable, '-m', 'services.paddle_ocr', str(Path(path).resolve()),
                   str(page), kind, str(attempt), str(output)]
        env = {**os.environ, 'PYTHONUTF8': '1', 'OMP_NUM_THREADS': '2', 'OPENBLAS_NUM_THREADS': '1',
               'OCR_STATUS_FILE': str(progress)}
        before = memory_snapshot()
        # A memory-killed process cannot write a result. Retry once with a
        # smaller detector, within the original deadline and queue lease.
        deadline = time.monotonic() + 240
        for run in range(2):
            try:
                process = subprocess.run(command, cwd=BASE_DIR, env=env, capture_output=True,
                                         timeout=max(1, deadline - time.monotonic()),
                                         creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            except subprocess.TimeoutExpired as error:
                raise RetryableOCRError('PaddleOCR okuması süre sınırına ulaştı. Yeniden deneyin. [OCR_TIMEOUT]') from error
            if output.exists() and process.returncode == 0:
                break
            if not output.exists() or process.returncode < 0 or process.returncode >= 128:
                after = memory_snapshot()
                message, retry = process_failure(process.returncode, process.stderr, before, after,
                                                 progress.read_text(encoding='utf-8') if progress.exists() else 'starting')
                if retry and run == 0 and env.get('OCR_COMPACT') != '1' and time.monotonic() < deadline - 10:
                    env['OCR_COMPACT'] = '1'
                    before = after
                    output.unlink(missing_ok=True)
                    progress.unlink(missing_ok=True)
                    continue
                raise (RetryableOCRError if retry else RuntimeError)(message)
            break  # A normal Python exception has a structured error payload.
        try:
            payload = json.loads(output.read_text(encoding='utf-8'))
            if not isinstance(payload, dict) or ('error' not in payload and not isinstance(payload.get('issues'), list)):
                raise ValueError('Invalid worker response')
        except (ValueError, OSError) as error:
            raise RetryableOCRError('OCR sonuç dosyası tamamlanamadı. Otomatik yeniden denenecek. [OCR_RESULT_INVALID]') from error
        if process.returncode or 'error' in payload:
            raise RuntimeError(payload.get('error', 'PaddleOCR işlemi tamamlanamadı.'))
        return payload


if __name__ == '__main__':
    path, page, kind, attempt, output = sys.argv[1:]
    report_stage('starting')
    try:
        result = read_in_process(Path(path), int(page), kind, int(attempt))
    except Exception as error:
        message = str(error) if isinstance(error, (RuntimeError, ValueError)) else 'PaddleOCR okuması tamamlanamadı. Sunucunun OCR kurulumunu kontrol edin.'
        Path(output).write_text(json.dumps({'error': message}, ensure_ascii=False), encoding='utf-8')
        raise SystemExit(1)
    report_stage('writing')
    Path(output).write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
