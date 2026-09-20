import json
import time

import pytest
from PIL import Image

from test_workflow import RECEIPT, Z_REPORT, client, image_bytes, server, successful_read, upload
from services.document_extraction import extract_document
from services.document_ocr import RetryableOCRError, read_tesseract_document as read_document
from services.ocr_text import clean_ocr_line
from services.queue_worker import process_next
from services.storage import database, initialize


@pytest.mark.parametrize('text', [
    'iakiv 0/i851/026', 'SAi 0181202', 'Fdv 20 99,12', 'TOPLAM 550,00',
    'SATIS TOPLAM 550,00', 'Fll Bellef oplaai 861 118,88',
    '81811 y 388co/9485', 'KDV %20.941,66', 'TOPLAM *435.650,00',
    'VKN 0234567890', 'TARIH 17/08/2027', 'KREDI 2 120,00',
])
def test_cleanup_does_not_invent_digits(text):
    assert clean_ocr_line(text) == text


def test_receipt_footer_z_number_does_not_change_document_type():
    data = extract_document(RECEIPT + '\nEKU NO: 0001 Z NO: 1880\nMF JH 20004135', 'receipts')
    assert not data['issues']
    assert data['document_type'] == 'Yazar Kasa Fişi'
    assert data['document_no'] == '000123' and data['product_name'] == 'DEFTER'


def test_decimal_vat_rate_is_not_a_product_price():
    data = extract_document(RECEIPT.replace('%20', '%20.00'), 'receipts')
    assert not data['issues']
    assert data['items'][0]['name'] == 'DEFTER'
    assert data['items'][0]['amount'] == '120.00'


def test_zero_rate_with_printed_zero_tax_is_supported():
    data = extract_document(RECEIPT.replace('%20', '%0').replace('TOPKDV 20,00', 'TOPKDV 0,00'), 'receipts')
    assert not data['issues']
    assert data['vat_breakdown'] == [{'rate': 0, 'base': '120.00', 'tax': '0.00'}]


@pytest.mark.parametrize('payment', ['', 'NAKIT 0,00', 'NAKIT 0,00\nKREDI OKUNAMADI', 'NAKIT 0,00\nKREDI 4120,00'])
def test_z_missing_or_wrong_payments_are_not_filled_from_total(payment):
    data = extract_document(Z_REPORT.replace('NAKIT 50,00\nKREDI KARTI 70,00', payment), 'z-reports')
    assert data['issues']
    assert data['card_amount'] != '120.00'


def test_equal_meal_card_payments_keep_distinct_providers():
    data = extract_document(Z_REPORT.replace('NAKIT 50,00\nKREDI KARTI 70,00', 'MULTINET 60,00\nSODEXO 60,00'), 'z-reports')
    assert not data['issues']
    assert data['meal_card_amount'] == '120.00' and data['card_amount'] == ''
    assert len(data['payment_entries']) == 2


def test_repeated_equal_partial_payments_are_not_dropped():
    data = extract_document(RECEIPT.replace('NAKIT 120,00', 'KREDI KARTI 60,00\nKREDI KARTI 60,00'), 'receipts')
    assert not data['issues'] and len(data['payment_entries']) == 2


def test_disagreeing_daily_totals_require_review():
    data = extract_document(Z_REPORT + '\nSATIS TOPLAMI 120,00\nGENEL TOPLAM 240,00', 'z-reports')
    assert any('Toplam tutar' in issue for issue in data['issues'])


def test_z_cancellation_totals_are_not_daily_sales_or_payments():
    text = Z_REPORT + ('\nBELGE TIPLERI\nOKC FISLERI 2\n-SATIS TOPLAMI 120,00\n'
                       '-NAKIT 50,00\n-KREDI 70,00\nIPTAL 1\n-KDV TOPLAMI 4,00\n'
                       '-SATIS TOPLAMI 24,00\n-KREDI 24,00\nSAYACLAR\nSATIS IPTAL TUTAR 24,00')
    data = extract_document(text, 'z-reports')
    assert not data['issues'], data['issues']
    assert data['total_amount'] == '120.00' and data['vat_amount'] == '20.00'
    assert data['card_amount'] == '70.00'


@pytest.mark.parametrize('count', [0, 3])
def test_cancellation_counter_with_missing_final_s_in_sales_label(count):
    data = extract_document(Z_REPORT + f'\nSAYACLAR\nSATI IPTAL {count}\nSATIS IPTAL TUTAR *24,00', 'z-reports')
    assert data['adjustments']['cancellation'] == {'amount': '24.00', 'count': count}
    assert data['total_amount'] == '120.00'


def test_absent_cancellation_counter_is_not_filled_from_amount():
    data = extract_document(Z_REPORT + '\nSAYACLAR\nSATIS IPTAL TUTAR *0,00', 'z-reports')
    assert data['adjustments']['cancellation'] == {'amount': '0.00', 'count': None}


def test_z_different_payment_sections_are_not_silently_replaced():
    text = Z_REPORT + '\nBELGE TIPLERI\n-NAKIT 0,00\n-KREDI 120,00'
    data = extract_document(text, 'z-reports')
    assert any('tahsilatlar çelişiyor' in issue for issue in data['issues'])
    assert data['cash_amount'] == '50.00' and data['card_amount'] == '70.00'


def test_tax_office_is_not_guessed_from_address_and_eku_is_not_fiscal_id():
    text = Z_REPORT.replace('VERGİ DAİRESİ: Kadıköy', 'KADIKOY / ISTANBUL').replace('MALİ SİCİL NO: AB00000123', 'EKU NO: 0001')
    data = extract_document(text, 'z-reports')
    assert data['tax_office'] == '' and data['fiscal_id'] == ''
    assert data['issues']


def test_missing_tax_number_is_not_replaced_by_phone():
    text = RECEIPT.replace('VKN: 1234567890', 'TEL 2123456789')
    assert extract_document(text, 'receipts')['tax_id'] == ''


def test_z_identifiers_must_agree_even_when_one_is_suffix_of_other():
    data = extract_document(Z_REPORT + '\nZ NO: 456', 'z-reports')
    assert any('birden fazla numara' in issue for issue in data['issues'])


def test_discount_does_not_borrow_refund_amount():
    data = extract_document(Z_REPORT + '\nINDIRIM ADET 1\nIADE TUTARI 12,00', 'z-reports')
    assert data['adjustments']['discount']['amount'] == ''
    assert any('İndirim' in issue for issue in data['issues'])


def tokens(text):
    words, line_numbers = [], []
    for index, line in enumerate(text.splitlines()):
        words.extend(line.split())
        line_numbers.extend([index] * len(line.split()))
    return {'text': words, 'conf': [98] * len(words), 'block_num': [0] * len(words),
            'par_num': [0] * len(words), 'line_num': line_numbers}


@pytest.mark.parametrize('field_value,notice', [('1234567890', 'VKN / TCKN'), ('000456', 'Belge numarası'), ('ABO0000123', 'Mali sicil numarası')])
def test_low_confidence_identifier_is_not_hidden_by_high_page_average(tmp_path, monkeypatch, field_value, notice):
    from services import document_ocr
    path = tmp_path / 'z.png'
    path.write_bytes(image_bytes().read())
    text = Z_REPORT.replace('AB00000123', 'ABO0000123')
    first, second = tokens(text), tokens(text)
    first['conf'][first['text'].index(field_value)] = 39
    reads = iter([first, second])
    monkeypatch.setattr(document_ocr.pytesseract, 'get_languages', lambda **kwargs: ['tur', 'eng'])
    monkeypatch.setattr(document_ocr.pytesseract, 'image_to_data', lambda *args, **kwargs: next(reads))
    result = read_document(path, 0, 'z-reports', 1)
    assert result['confidence'] > 90
    assert any(notice + ' okuma güveni düşük' in issue for issue in result['issues'])
    assert result['fiscal_id'] == 'ABO0000123'  # Uncertain characters are not fabricated.


@pytest.mark.parametrize('changed', [
    RECEIPT.replace('VERGİ DAİRESİ: Kadıköy\n', ''),
    RECEIPT.replace('NAKIT 120,00', 'KREDI KARTI 120,00'),
    RECEIPT.replace('DEFTER %20', 'DEFTER 2 ADET X 60,00 %20'),
    RECEIPT.replace('TOPLAM 120,00', 'TOPLAM 720,00'),
])
def test_second_incomplete_or_conflicting_read_blocks_success(tmp_path, monkeypatch, changed):
    from services import document_ocr
    path = tmp_path / 'receipt.png'
    path.write_bytes(image_bytes().read())
    reads = iter([RECEIPT, changed])
    monkeypatch.setattr(document_ocr.pytesseract, 'get_languages', lambda **kwargs: ['tur', 'eng'])
    monkeypatch.setattr(document_ocr.pytesseract, 'image_to_data', lambda *args, **kwargs: tokens(next(reads)))
    data = read_document(path, 0, 'receipts', 1)
    assert any('İki okuma' in issue for issue in data['issues'])
    assert len(data['ocr_reads']) == 2


def test_missing_tesseract_gives_actionable_error_without_optional_imports(tmp_path, monkeypatch):
    from services import document_ocr
    path = tmp_path / 'receipt.png'
    path.write_bytes(image_bytes().read())
    def unavailable(**kwargs):
        raise document_ocr.pytesseract.TesseractNotFoundError()
    monkeypatch.setattr(document_ocr.pytesseract, 'get_languages', unavailable)
    with pytest.raises(RuntimeError, match='Tesseract bulunamadı'):
        read_document(path, 0, 'receipts', 1)


def test_second_read_timeout_is_retryable_not_false_success(tmp_path, monkeypatch):
    from services import document_ocr
    path = tmp_path / 'receipt.png'
    path.write_bytes(image_bytes().read())
    count = 0
    def read(*args, **kwargs):
        nonlocal count
        count += 1
        if count == 2:
            raise RuntimeError('Tesseract process timeout')
        return tokens(RECEIPT)
    monkeypatch.setattr(document_ocr.pytesseract, 'get_languages', lambda **kwargs: ['tur', 'eng'])
    monkeypatch.setattr(document_ocr.pytesseract, 'image_to_data', read)
    with pytest.raises(RetryableOCRError):
        read_document(path, 0, 'receipts', 1)


def test_new_read_does_not_restore_old_success(client):
    identity = upload(client)
    process_next(server.DB_PATH, successful_read)
    client.post(f'/api/documents/{identity}/retry', headers=client.auth)
    process_next(server.DB_PATH, lambda *args: extract_document(RECEIPT.replace('TOPLAM 120,00', ''), 'receipts'))
    doc = client.get(f'/api/documents/{identity}', headers=client.auth).json
    assert doc['status'] == 'review' and doc['result']['total_amount'] == ''


def test_active_ocr_is_not_reclaimed_after_sixty_seconds(client):
    identity = upload(client)
    with database(server.DB_PATH) as db:
        db.execute("UPDATE documents SET status='processing',started_at=? WHERE id=?", (time.time() - 80, identity))
    assert process_next(server.DB_PATH, successful_read) is False


def test_expired_reader_cannot_overwrite_new_claim(client):
    identity = upload(client)
    def stale_read(*args):
        with database(server.DB_PATH) as db:
            db.execute('UPDATE documents SET started_at=started_at+1 WHERE id=?', (identity,))
        return successful_read(*args)
    process_next(server.DB_PATH, stale_read)
    doc = client.get(f'/api/documents/{identity}', headers=client.auth).json
    assert doc['status'] == 'processing' and doc['result'].get('total_amount') is None


def test_v4_results_requeue_once_even_if_read_fails(client):
    identity = upload(client)
    process_next(server.DB_PATH, successful_read)
    with database(server.DB_PATH) as db:
        data = successful_read(None, 0, 'receipts', 1)
        data['extraction_version'] = 4
        db.execute('UPDATE documents SET result=?,export_count=2 WHERE id=?', (json.dumps(data), identity))
    initialize(server.DB_PATH)
    def failed(*args):
        raise RuntimeError('Missing source')
    process_next(server.DB_PATH, failed)
    initialize(server.DB_PATH)
    doc = client.get(f'/api/documents/{identity}', headers=client.auth).json
    assert doc['status'] == 'failed' and doc['export_count'] == 2


def test_switch_kind_cannot_interrupt_ocr_and_updates_direction(client):
    identity = upload(client)
    assert client.post(f'/api/documents/{identity}/switch-kind', headers=client.auth).status_code == 409
    process_next(server.DB_PATH, successful_read)
    assert client.post(f'/api/documents/{identity}/switch-kind', headers=client.auth).status_code == 200
    doc = client.get(f'/api/documents/{identity}', headers=client.auth).json
    assert doc['kind'] == 'z-reports' and doc['direction'] == 'income' and doc['status'] == 'queued'
    assert not doc['result'].get('total_amount')


def test_wrong_kind_remains_visible_for_user_review(client):
    identity = upload(client)
    process_next(server.DB_PATH, lambda *args: extract_document(Z_REPORT, 'receipts'))
    doc = client.get(f'/api/documents/{identity}', headers=client.auth).json
    assert doc['kind'] == 'receipts' and doc['status'] == 'review'


def test_spatial_rows_rejoin_label_and_amount_from_different_blocks():
    from services.document_ocr import _tokens_to_text
    data = {'text': ['120,00', 'TOPLAM', '20,00', 'TOPKDV'], 'conf': [95] * 4,
            'block_num': [1, 2, 3, 4], 'par_num': [1] * 4, 'line_num': [1] * 4,
            'left': [300, 10, 300, 10], 'top': [60, 59, 30, 29], 'width': [50] * 4, 'height': [20] * 4}
    text, confidence = _tokens_to_text(data)
    assert text == 'TOPKDV 20,00\nTOPLAM 120,00'


def test_multirate_receipt_can_reconcile_from_items_and_printed_total_tax():
    text = RECEIPT.replace('DEFTER %20 *120,00', 'DEFTER %20 *120,00\nKİTAP %10 *110,00')
    text = text.replace('TOPKDV 20,00', 'TOPKDV 30,00').replace('TOPLAM 120,00', 'TOPLAM 230,00').replace('NAKIT 120,00', 'NAKIT 230,00')
    data = extract_document(text, 'receipts')
    assert not data['issues']
    assert data['vat_breakdown'] == [{'rate': 10, 'base': '100.00', 'tax': '10.00'}, {'rate': 20, 'base': '100.00', 'tax': '20.00'}]
    assert extract_document(text.replace('TOPKDV 30,00', 'TOPKDV 35,00'), 'receipts')['issues']


def test_clock_noise_does_not_shift_hour_into_minutes():
    data = extract_document(Z_REPORT.replace('SAAT: 23:15:42', 'SAAT 2 04:21:42'), 'z-reports')
    assert data['document_time'] == '04:21:42'


def test_cropped_seller_is_not_replaced_by_city_or_product():
    data = extract_document(RECEIPT.replace('ORNEK MARKET', 'ÜSKÜDAR / İSTANBUL'), 'receipts')
    assert data['seller_name'] == ''
    assert any('Firma adı' in issue for issue in data['issues'])


def test_missing_tax_rate_is_not_inferred_from_total_ratio():
    data = extract_document(Z_REPORT.replace('KDV %20 100,00 20,00', 'TOPKDV 20,00'), 'z-reports')
    assert data['vat_breakdown'] == []
    assert any('oranı okunamadı' in issue for issue in data['issues'])


def test_retry_uses_180_degree_orientation_and_different_preprocessing(tmp_path, monkeypatch):
    from services import document_ocr
    path = tmp_path / 'receipt.png'
    Image.new('RGB', (50, 120), 'white').save(path)
    monkeypatch.setattr(document_ocr.pytesseract, 'get_languages', lambda **kwargs: ['tur', 'eng'])
    monkeypatch.setattr(document_ocr.pytesseract, 'image_to_osd', lambda *args, **kwargs: {'rotate': 180, 'orientation_conf': 20})
    seen = []
    def read(image, **kwargs):
        seen.append(kwargs['config'])
        return tokens(RECEIPT)
    monkeypatch.setattr(document_ocr.pytesseract, 'image_to_data', read)
    assert not read_document(path, 0, 'receipts', 2)['issues']
    assert len(seen) == 2 and all('thresholding_method=2' in config for config in seen)


def test_local_auth_requires_opt_in_and_is_disabled_on_railway(client, monkeypatch):
    monkeypatch.setitem(server.app.config, 'TESTING', False)
    monkeypatch.delenv('ALLOW_LOCAL_AUTH', raising=False)
    monkeypatch.delenv('RAILWAY_ENVIRONMENT_ID', raising=False)
    with server.app.test_request_context('/', environ_base={'REMOTE_ADDR': '127.0.0.1'}):
        assert not server.local_auth()
        monkeypatch.setenv('ALLOW_LOCAL_AUTH', '1')
        assert server.local_auth()
        monkeypatch.setenv('RAILWAY_ENVIRONMENT_ID', 'production')
        assert not server.local_auth()


def test_processed_pdf_uses_requested_page(tmp_path, monkeypatch):
    from services import document_ocr
    from PIL import Image
    path = tmp_path / 'pages.pdf'
    first = Image.new('RGB', (50, 50), 'white')
    second = Image.new('RGB', (50, 50), 'black')
    first.save(path, save_all=True, append_images=[second])
    seen = []
    def read(image, **kwargs):
        seen.append(image.getpixel((image.width // 2, image.height // 2)))
        return tokens(RECEIPT)
    monkeypatch.setattr(document_ocr.pytesseract, 'get_languages', lambda **kwargs: ['tur', 'eng'])
    monkeypatch.setattr(document_ocr.pytesseract, 'image_to_data', read)
    read_document(path, 1, 'receipts', 1)
    assert seen == [0, 0]


def test_printer_tax_office_and_id_header_without_vd_label():
    text = Z_REPORT.replace('VERGİ DAİRESİ: Kadıköy\nVKN: 1234567890', 'ŞİŞLİ 3880097945')
    data = extract_document(text, 'z-reports')
    assert not data['issues']
    assert data['tax_id'] == '3880097945' and data['tax_office'] == 'ŞİŞLİ'


def test_v4_result_cannot_be_exported_without_new_read():
    from services.accounting import export_workbook
    from test_workflow import profile
    data = extract_document(RECEIPT, 'receipts')
    data['extraction_version'] = 4
    with pytest.raises(ValueError, match='Güncel okuma'):
        export_workbook([{'kind': 'receipts', 'direction': 'expense', 'filename': 'test.png', 'result': data}], profile())


def test_clear_thermal_z_layout_keeps_daily_cumulative_and_cancelled_sales_separate():
    # Transcription of the printed layout, with distinct daily and cancelled totals.
    text = '''ÖRNEK TURİZM
SAN. TİC. LTD. ŞTİ.
ŞİŞLİ 3880097945
TARIH 30/05/2026
SAAT 04:21:42
FİŞ NO 0041
Z RAPORU
RAPOR NO 1880
MALİ BELLEK TOPLAMI *12.867.454,44
MALİ BELLEK TOP KDV *2.070.030,56
GÜNLÜK FİŞ DÖKÜMÜ
TOPLAM *35.650,00
TOPKDV *5.941,66
KDV BİLGİLERİ
KDV %20.00 *5.941,66
TOPLAM *35.650,00
DEPARTMAN BİLGİLERİ
BİRA %20.00
TOPLAM *34.400,00
MİKTARI 32,0000
YERLİ İÇKİ %20.00
TOPLAM *1.250,00
MİKTARI 1,0000
ÖDEME BİLGİLERİ
NAKİT 0
TOPLAM *0,00
KREDİ 33
TOPLAM *35.650,00
BELGE TİPLERİ
ÖKC FİŞLERİ 33
-KDV TOPLAMI *5.941,66
-SATIŞ TOPLAMI *35.650,00
-NAKİT *0,00
-KREDİ *35.650,00
-DİĞER *0,00
İPTAL 5
-KDV TOPLAMI *775,00
-SATIŞ TOPLAMI *4.650,00
SAYAÇLAR
İNDİRİM ADET 0
İNDİRİM TUTAR *0,00
MALİ FİŞ ADET 34
MÜŞTERİ FİŞİ ADETİ 33
SATIŞ İPTAL 5
SATIŞ İPTAL TUTAR *4.650,00
KASİYER BİLGİ
KASIYER1 *35.650,00
EKÜ NO: 0001 Z NO: 1880
MF JH 20004135'''
    data = extract_document(text, 'z-reports')
    assert not data['issues'], data['issues']
    assert data['transaction_count'] == 33
    assert data['total_amount'] == data['card_amount'] == '35650.00'
    assert data['vat_amount'] == '5941.66'
    assert data['cumulative_sales'] == '12867454.44' and data['cumulative_vat'] == '2070030.56'
    assert data['adjustments']['cancellation'] == {'count': 5, 'amount': '4650.00'}


@pytest.mark.parametrize('port,expected', [('8080', '8080'), (' 9123 ', '9123'), ('', '5000'), ('$PORT', '5000'), ('70000', '5000')])
def test_railway_port_configuration(monkeypatch, port, expected):
    import runpy
    from pathlib import Path
    monkeypatch.setenv('PORT', port)
    config = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'gunicorn.conf.py'))
    assert config['bind'] == f'0.0.0.0:{expected}'
    assert config['workers'] == 1 and config['threads'] == 4
