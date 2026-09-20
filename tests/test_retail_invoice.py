"""Synthetic retail invoice data; no customer documents or personal details."""
import pytest
from PIL import Image

from services.document_extraction import extract_document
from services.paddle_ocr import reread_missing_date
from test_paddle_ocr import output


INVOICE = '''ORNEK
ORNEK KOZMETIK A.S.
VERGI DAIRESI: KADIKOY/VKN:1234567890
E-ARSIV
FATURA NO:ABC2026000012345
ETTN:
11111111-2222-3333-4444-555555555555
ALICI TEST KISISI
TEST@EXAMPLE.COM
SADAKAT KARTI WHITE
Loyalty Points (beforehand) 0
Ürün 1234567890123
ORNEK PARFUM 50.ML
120,00 * 1 TRY 120,00
Brüt toplam TRY 120,00
Toplam indirimler TRY 0,00
Toplam TRY 120,00
Toplam KDV'siz TRY 100,00
ISBANKASI TEK TRY 120,00
Vergi dahil:
1 20,00 % 120,0020,00
Tarih Saat Magaza Kas Islem
6.07.2026 18:42 1234 4 5678'''


def test_retail_invoice_separates_customer_product_and_tax_columns():
    data = extract_document(INVOICE, 'receipts')
    assert data['seller_name'] == 'ORNEK KOZMETIK A.S.'
    assert data['product_name'] == 'ORNEK PARFUM 50.ML'
    assert data['items'] == [dict(name='ORNEK PARFUM 50.ML', quantity='1', unit='', unit_price='120.00', amount='120.00', rate=20)]
    assert data['total_amount'] == '120.00'
    assert data['vat_breakdown'] == [dict(rate=20, base='100.00', tax='20.00')]
    assert data['document_datetime'] == '2026-07-06T18:42'
    assert data['payment_entries'] == [dict(method='pos', amount='120.00', bank_code='0064', bank_role='acquirer')]
    assert data['issues'] == ['Kartın banka kartı mı kredi kartı mı olduğu okunamadı.']


@pytest.mark.parametrize('label', ["Toplam KDV'siz", 'Toplam KDV’siz', 'Toplam KDV SIZ', 'KDV HARIC'])
def test_net_total_is_never_used_as_vat(label):
    data = extract_document(INVOICE.replace("Toplam KDV'siz", label), 'receipts')
    assert data['vat_amount'] == '20.00'
    assert len(data['issues']) == 1
    wrong = extract_document(INVOICE.replace("Toplam KDV'siz TRY 100,00", label + ' TRY 99,00'), 'receipts')
    assert any('matrah' in issue for issue in wrong['issues'])


def test_multiple_products_reconcile_without_recipient_or_barcode_in_name():
    text = INVOICE.replace('120,00 * 1 TRY 120,00', '60,00 * 1 TRY 60,00\nÜrün 9999999999999\nDIGER PARFUM\n30,00 * 2 TRY 60,00')
    data = extract_document(text, 'receipts')
    assert [item['quantity'] for item in data['items']] == ['1', '2']
    assert data['product_name'] == 'ORNEK PARFUM 50.ML; DIGER PARFUM'
    assert len(data['issues']) == 1


@pytest.mark.parametrize('old,new', [
    ('120,00 * 1 TRY 120,00', '120,00 * 2 TRY 120,00'),
    ('120,00 * 1 TRY 120,00', '120,00 * 1 TRY 110,00'),
    ('120,00 * 1 TRY 120,00', 'FIYAT OKUNAMADI'),
    ('ORNEK PARFUM 50.ML\n', ''),
    ('120,0020,00', '120,0021,00'),
    ('120,0020,00', '120,0020,0'),
    ('1 20,00 % 120,0020,00', ''),
    ('1 20,00 % 120,0020,00', '1 20,00 % 60,0010,00\n2 10,00 % 60,005,45'),
])
def test_incomplete_or_inconsistent_invoice_stays_in_review(old, new):
    data = extract_document(INVOICE.replace(old, new), 'receipts')
    assert any('Kartın banka kartı' not in issue for issue in data['issues'])


def candidates():
    return [extract_document(INVOICE, 'receipts'), extract_document(INVOICE.replace('6.07.2026', '6.07.20261'), 'receipts')]


def test_date_recovery_requires_two_matching_source_reads():
    data = candidates()
    with Image.new('RGB', (800, 300)) as source:
        reads = reread_missing_date(source, data, [[0, 0, 800, 300]], lambda image: output('6.07.2026 18:42'), 'receipts')
    assert len(reads) == 2 and all(read['verified'] for read in reads)
    assert data[0]['document_datetime'] == data[1]['document_datetime'] == '2026-07-06T18:42'
    assert 'Tarih okunamadı.' not in data[1]['issues']


@pytest.mark.parametrize('second,weak', [('7.07.2026 18:42', None), ('6.07.20261 18:42', None), ('6.07.2026 18:42', '2026'), ('6.07.2026 18:43', None)])
def test_unconfirmed_date_crop_does_not_fill_missing_date(second, weak):
    data = candidates()
    outputs = iter([output('6.07.2026 18:42'), output(second, weak=weak)])
    with Image.new('RGB', (800, 300)) as source:
        reread_missing_date(source, data, [[0, 0, 800, 300]], lambda image: next(outputs), 'receipts')
    assert not data[1]['document_datetime']
    assert 'Tarih okunamadı.' in data[1]['issues']


def test_conflicting_valid_dates_are_not_overwritten_by_crop():
    data = candidates()
    data[1]['document_datetime'] = '2026-07-07T18:42'
    with Image.new('RGB', (800, 300)) as source:
        reads = reread_missing_date(source, data, [[0, 0, 800, 300]], lambda image: pytest.fail('Must not reread conflicting valid dates'), 'receipts')
    assert reads == []
    assert data[1]['document_datetime'] == '2026-07-07T18:42'
