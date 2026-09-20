"""Regression matrix for parsing, source verification and export safety."""
import json
import io
from decimal import Decimal
from types import SimpleNamespace

import pytest
from PIL import Image

from services.accounting import journal_rows
from services.document_extraction import extract_document
from services.document_ocr import inspect_file, load_source
from services.paddle_ocr import read_in_process, reread_missing_date
from services.queue_worker import process_next
from services.storage import database, initialize
from test_paddle_ocr import output
from test_workflow import RECEIPT, Z_REPORT, client, profile, server, upload, successful_read


@pytest.mark.parametrize('name', ['FAST YAPISTIRICI', 'POS RULOSU', 'DIGER URUN', 'NAKIT DEFTERI', 'KREDI KARTI KILIFI'])
@pytest.mark.parametrize('wrapped', [False, True])
def test_payment_words_in_products_do_not_create_payments(name, wrapped):
    data = extract_document(RECEIPT.replace('DEFTER ', name + ('\n' if wrapped else ' ')), 'receipts')
    assert not data['issues'], data['issues']
    assert data['product_name'] == name
    assert len(data['payment_entries']) == 1
    assert data['payment_method'] == 'cash'


@pytest.mark.parametrize('label', ['MUSTERI BILGILERI', 'ALICI', 'MÜŞTERİ: TEST KİŞİSİ'])
def test_buyer_block_does_not_replace_seller_identity(label):
    buyer = label + '\nVERGI DAIRESI: USKUDAR\nVKN: 11111111111\n'
    data = extract_document(RECEIPT.replace('FIS NO:', buyer + 'FIS NO:'), 'receipts')
    assert not data['issues']
    assert data['tax_id'] == '1234567890' and data['tax_office'] == 'Kadıköy'
    missing = extract_document(RECEIPT.replace('VKN: 1234567890\n', '').replace('FIS NO:', buyer + 'FIS NO:'), 'receipts')
    assert not missing['tax_id']


@pytest.mark.parametrize('rate', [0, 1, 8, 10, 18, 20])
@pytest.mark.parametrize('payment', ['NAKIT', 'KREDI KARTI', 'BANKA KARTI'])
@pytest.mark.parametrize('quantity', [1, 3])
def test_printed_amount_matrix_balances_and_rejects_changed_totals(rate, payment, quantity):
    tax = rate * quantity
    total = (100 + rate) * quantity
    money = lambda value: f'{value:.2f}'.replace('.', ',')
    text = RECEIPT.replace('DEFTER %20 *120,00', f'DEFTER {quantity} ADET X {money(100+rate)} %{rate} *{money(total)}')
    text = text.replace('KDV %20', f'KDV %{rate}').replace('TOPKDV 20,00', f'TOPKDV {money(tax)}')
    text = text.replace('TOPLAM 120,00', f'TOPLAM {money(total)}').replace('NAKIT 120,00', f'{payment} {money(total)}')
    data = extract_document(text, 'receipts')
    assert not data['issues'], data['issues']
    assert data['vat_amount'] == f'{tax:.2f}'
    document = dict(result=data, kind='receipts', direction='expense', filename='synthetic.png')
    rows = journal_rows([document], profile())
    assert sum((row['debit'] - row['credit'] for row in rows), Decimal(0)) == 0
    assert extract_document(text.replace(f'TOPLAM {money(total)}', f'TOPLAM {money(total+1)}'), 'receipts')['issues']


def test_time_disagreement_does_not_erase_verified_date(tmp_path):
    path = tmp_path / 'receipt.png'
    Image.new('RGB', (30, 30)).save(path)
    reads = iter([output(RECEIPT), output(RECEIPT.replace('14:25:36', '14:26:36'))])
    data = read_in_process(path, 0, 'receipts', 1, engine=lambda image: next(reads))
    assert data['document_datetime'] == '2026-09-13'
    assert data['document_time'] == '' and data['conflicting_fields'] == ['document_time']
    assert data['issues']


def test_real_date_disagreement_still_clears_date(tmp_path):
    path = tmp_path / 'receipt.png'
    Image.new('RGB', (30, 30)).save(path)
    reads = iter([output(RECEIPT), output(RECEIPT.replace('13.09.2026', '14.09.2026'))])
    data = read_in_process(path, 0, 'receipts', 1, engine=lambda image: next(reads))
    assert data['document_datetime'] == ''
    assert 'document_datetime' in data['conflicting_fields']


@pytest.mark.parametrize('second,confidence,expected', [('14:25:36', .98, True), ('14:26:36', .98, False), ('14:25:36', .6, False)])
def test_line_recognition_only_accepts_two_confident_agreements(second, confidence, expected):
    data = [extract_document(RECEIPT, 'receipts'), extract_document(RECEIPT.replace('14:25:36', '14:2'), 'receipts')]
    class Engine:
        def __init__(self):
            self.reads = iter([('14:25:36', .99), (second, confidence)])
        def __call__(self, image):
            return output('13.09.2026 SAAT: 14:2')
        def recognize_txt(self, images):
            clock, score = next(self.reads)
            return SimpleNamespace(txts=['13.09.2026Saat: ' + clock], scores=[score])
    with Image.new('RGB', (800, 300)) as source:
        reread_missing_date(source, data, [[0, 0, 800, 300]], Engine(), 'receipts', [[0, 0, 800, 100]])
    assert bool(data[1]['document_time']) == expected
    if expected:
        assert data[1]['document_time'] == '14:25:36' and not data[1]['issues']


@pytest.mark.parametrize('angle', [90, 180, 270])
def test_rotation_requires_fiscal_anchors_and_two_readings(tmp_path, angle):
    path = tmp_path / 'rotated.png'
    Image.new('RGB', (30, 60)).save(path)
    probes = [output('BELIRSIZ METIN')]
    probes.extend(output(Z_REPORT if candidate == angle else 'BELIRSIZ METIN') for candidate in (90, 180, 270))
    probes.append(output(Z_REPORT))
    reads = iter(probes)
    data = read_in_process(path, 0, 'z-reports', 1, engine=lambda image: next(reads))
    assert data['orientation_degrees'] == angle
    assert data['total_amount'] == '120.00' and not data['issues']


def test_report_copy_is_not_classified_as_information_invoice():
    data = extract_document(Z_REPORT + '\nZ RAPOR KOPYASI\nMALI DEGERI YOKTUR', 'z-reports')
    assert data['document_type'] == 'Z Raporu'
    assert any('asıl Z raporunu' in issue for issue in data['issues'])
    assert not any('asıl faturayı' in issue for issue in data['issues'])


def test_empty_detector_and_failed_line_recovery_preserve_full_page_result():
    data = [extract_document(RECEIPT, 'receipts'), extract_document(RECEIPT.replace('14:25:36', '14:2'), 'receipts')]
    class Engine:
        def __call__(self, image):
            return SimpleNamespace(boxes=None, txts=[])
        def recognize_txt(self, images):
            raise RuntimeError('No text in optional crop')
    with Image.new('RGB', (800, 300)) as source:
        reads = reread_missing_date(source, data, [[0, 0, 800, 300]], Engine(), 'receipts', [[0, 0, 800, 100]])
    assert reads and not reads[-1]['verified']
    assert data[0]['total_amount'] == data[1]['total_amount'] == '120.00'
    assert data[1]['document_time'] == ''


def test_v6_results_reprocess_once_preserving_export_history(client):
    identity = upload(client)
    process_next(server.DB_PATH, successful_read)
    with database(server.DB_PATH) as db:
        data = extract_document(RECEIPT, 'receipts')
        data['extraction_version'] = 6
        db.execute('UPDATE documents SET result=?,reprocess_version=6,export_count=2 WHERE id=?', (json.dumps(data), identity))
    initialize(server.DB_PATH)
    doc = client.get(f'/api/documents/{identity}', headers=client.auth).json
    assert doc['status'] == 'queued' and doc['export_count'] == 2
    process_next(server.DB_PATH, lambda *args: (_ for _ in ()).throw(RuntimeError('Test failure')))
    initialize(server.DB_PATH)
    assert client.get(f'/api/documents/{identity}', headers=client.auth).json['status'] == 'failed'


@pytest.mark.parametrize('format,suffix', [('JPEG','jpg'),('PNG','png'),('WEBP','webp'),('TIFF','tiff'),('BMP','bmp')])
def test_supported_upload_formats_reach_ocr_with_pixels_intact(client, tmp_path, format, suffix):
    stream = io.BytesIO()
    with Image.new('RGB', (100, 160), 'white') as image:
        image.paste('black', (10, 50, 90, 60))
        image.save(stream, format=format)
    stream.seek(0)
    identity = upload(client, name='fixture.' + suffix, source=stream)
    def reader(path, page, kind, attempt):
        with load_source(path, page) as source:
            assert source.size == (100,160)
            assert source.getpixel((0,0)) == (255,255,255)
            assert max(source.getpixel((50,55))) < 15
        return successful_read(path, page, kind, attempt)
    assert process_next(server.DB_PATH, reader)
    assert client.get(f'/api/documents/{identity}', headers=client.auth).json['status'] == 'success'


@pytest.mark.parametrize('mode', ['RGBA', 'LA', 'P'])
def test_transparency_is_composited_on_white_before_ocr(tmp_path, mode):
    path = tmp_path / 'transparent.png'
    with Image.new('RGBA', (100,160), (0,0,0,0)) as image:
        image.paste((0,0,0,255), (10,50,90,60))
        image.convert(mode).save(path)
    assert inspect_file(path) == 1
    with load_source(path, 0) as source:
        assert source.getpixel((0,0)) == (255,255,255)
        assert source.getpixel((50,55)) == (0,0,0)


def test_corrupt_upload_is_rejected_without_orphaned_file(client):
    before = set(server.UPLOAD_DIR.iterdir())
    response = client.post('/api/documents', headers=client.auth, data={'kind':'receipts','file':(io.BytesIO(b'not-an-image'),'bad.jpg')})
    assert response.status_code == 400
    assert set(server.UPLOAD_DIR.iterdir()) == before
    assert client.get('/api/documents', headers=client.auth).json['total'] == 0
