import subprocess
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from services import document_ocr, paddle_ocr
from services.ocr_layout import layout_text
from services.ocr_models import verified_model_paths
from test_workflow import RECEIPT, Z_REPORT, image_bytes


def output(text, weak=None):
    lines = text.splitlines()
    boxes = np.array([[[10, n*30], [600, n*30], [600, n*30+20], [10, n*30+20]] for n in range(len(lines))])
    return SimpleNamespace(boxes=boxes, txts=lines, scores=[.4 if weak and weak in line else .99 for line in lines])


def test_default_reader_dispatches_to_paddle_without_tesseract(monkeypatch):
    monkeypatch.delenv('OCR_ENGINE', raising=False)
    monkeypatch.setattr(paddle_ocr, 'read_document', lambda *args: {'engine': 'paddle', 'args': args})
    result = document_ocr.read_document('receipt.png', 0, 'receipts', 1)
    assert result['engine'] == 'paddle'


@pytest.mark.parametrize('text,kind', [(RECEIPT, 'receipts'), (Z_REPORT, 'z-reports')])
def test_local_neural_reader_preserves_two_verified_reads(tmp_path, text, kind):
    path = tmp_path / 'source.png'
    path.write_bytes(image_bytes().read())
    result = paddle_ocr.read_in_process(path, 0, kind, 1, engine=lambda image: output(text))
    assert not result['issues']
    assert result['total_amount'] == '120.00'
    assert len(result['ocr_reads']) == 2
    assert result['engine'].startswith('PaddleOCR')


def test_neural_read_does_not_hide_conflicting_amounts(tmp_path):
    path = tmp_path / 'source.png'
    path.write_bytes(image_bytes().read())
    reads = iter([output(RECEIPT), output(RECEIPT.replace('TOPLAM 120,00', 'TOPLAM 720,00'))])
    result = paddle_ocr.read_in_process(path, 0, 'receipts', 1, engine=lambda image: next(reads))
    assert any('İki okuma' in issue for issue in result['issues'])
    assert 'total_amount' in result['conflicting_fields']
    assert any('Genel toplam' in issue for issue in result['issues'])


def test_weak_fiscal_number_in_either_read_requires_review(tmp_path):
    path = tmp_path / 'source.png'
    path.write_bytes(image_bytes().read())
    reads = iter([output(Z_REPORT, weak='AB00000123'), output(Z_REPORT)])
    result = paddle_ocr.read_in_process(path, 0, 'z-reports', 1, engine=lambda image: next(reads))
    assert any('Mali sicil numarası okuma güveni düşük' in issue for issue in result['issues'])


def test_cropped_seller_is_not_replaced_by_an_unconfirmed_fragment(tmp_path):
    path = tmp_path / 'source.png'
    path.write_bytes(image_bytes().read())
    reads = iter([output(Z_REPORT.replace('ORNEK MARKET\n', '')), output(Z_REPORT.replace('ORNEK MARKET', 'CroST'))])
    result = paddle_ocr.read_in_process(path, 0, 'z-reports', 1, engine=lambda image: next(reads))
    assert result['seller_name'] == ''
    assert result['total_amount'] == '120.00'
    assert any('unvanı iki okumada doğrulanamadı' in issue for issue in result['issues'])
    assert result['conflicting_fields'] == ['seller_name']


def test_two_office_reads_can_agree_after_label_scoped_glyph_repair(tmp_path):
    path = tmp_path / 'source.png'
    path.write_bytes(image_bytes().read())
    reads = iter([output(RECEIPT.replace('VERGİ DAİRESİ: Kadıköy', label))
                  for label in ('$15L1 V D 1234567890', '$1$L1 V D 1234567890')])
    result = paddle_ocr.read_in_process(path, 0, 'receipts', 1, engine=lambda image: next(reads))
    assert result['tax_office'] == 'SISLI'
    assert not result['issues']
    assert not result['conflicting_fields']


def test_different_offices_still_require_review_after_glyph_repair(tmp_path):
    path = tmp_path / 'source.png'
    path.write_bytes(image_bytes().read())
    reads = iter([output(RECEIPT.replace('VERGİ DAİRESİ: Kadıköy', '$15L1 V D 1234567890')), output(RECEIPT)])
    result = paddle_ocr.read_in_process(path, 0, 'receipts', 1, engine=lambda image: next(reads))
    assert result['tax_office'] == ''
    assert result['conflicting_fields'] == ['tax_office']
    assert any('Vergi dairesi' in issue for issue in result['issues'])


def test_row_reconstruction_keeps_slanted_amount_with_label():
    boxes = [[[10, 60], [160, 54], [160, 74], [10, 80]],
             [[300, 48], [400, 44], [400, 64], [300, 68]],
             [[10, 90], [160, 84], [160, 104], [10, 110]]]
    text, _ = layout_text(boxes, ['TOPLAM', '*4.550,00', 'NAKIT'], [.99]*3)
    assert text == 'TOPLAM *4.550,00\nNAKIT'


def test_missing_model_fails_without_network(tmp_path, monkeypatch):
    monkeypatch.setenv('OCR_MODEL_DIR', str(tmp_path))
    with pytest.raises(RuntimeError, match='model dosyaları eksik'):
        verified_model_paths()


def test_runtime_passes_explicit_local_model_paths_and_disables_telemetry(monkeypatch):
    captured = {}
    monkeypatch.setattr(paddle_ocr, 'verified_model_paths', lambda: {'Det': '/local/det.onnx', 'Rec': '/local/rec.onnx', 'Cls': '/local/cls.onnx'})
    monkeypatch.setitem(sys.modules, 'onnxruntime', SimpleNamespace(disable_telemetry_events=lambda: captured.update(telemetry=False)))
    def make_engine(params):
        captured.update(params)
        return 'engine'
    monkeypatch.setitem(sys.modules, 'rapidocr', SimpleNamespace(RapidOCR=make_engine, ModelType=SimpleNamespace(MOBILE='mobile', SMALL='small'), OCRVersion=SimpleNamespace(PPOCRV5='v5', PPOCRV6='v6')))
    assert paddle_ocr.create_engine() == 'engine'
    assert captured['telemetry'] is False
    assert captured['Rec.model_path'] == '/local/rec.onnx'
    assert captured['Det.model_path'] == '/local/det.onnx'
    assert captured['Cls.model_path'] == '/local/cls.onnx'


def test_worker_timeout_is_bounded_and_retryable(tmp_path, monkeypatch):
    monkeypatch.setattr(paddle_ocr, 'verified_model_paths', lambda: {})
    def timeout(command, **kwargs):
        assert kwargs['timeout'] < 600
        assert kwargs['cwd'] == paddle_ocr.BASE_DIR
        raise subprocess.TimeoutExpired(command, kwargs['timeout'])
    monkeypatch.setattr(paddle_ocr.subprocess, 'run', timeout)
    with pytest.raises(document_ocr.RetryableOCRError):
        paddle_ocr.read_document(tmp_path / 'source.png', 0, 'z-reports', 1)
