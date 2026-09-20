import pytest

from services.document_extraction import extract_document
from services.accounting import journal_rows
from test_workflow import RECEIPT, profile


@pytest.mark.parametrize('notice', ['BILGI FISI', 'BİLGİ FİŞİ', 'BILGt FI$t', 'MALI DEGERI YOKTUR', 'MALI DEČERI YOKTUR'])
def test_information_receipts_are_read_but_never_exported(notice):
    data = extract_document(RECEIPT + '\n' + notice, 'receipts')
    assert data['document_type'] == 'Bilgi Fişi'
    assert data['total_amount'] == '120.00'
    assert any('asıl faturayı' in issue for issue in data['issues'])
    with pytest.raises(ValueError, match='kontrolleri'):
        journal_rows([dict(result=data, kind='receipts', direction='expense')], profile())


@pytest.mark.parametrize('label', ['Müşteri VKN', 'ALICI VKN', 'MÜŞTERİ TCKN'])
def test_customer_identity_is_not_a_conflicting_seller_identity(label):
    data = extract_document(RECEIPT.replace('FIS NO:', label + ': 11111111111\nFIS NO:'), 'receipts')
    assert data['tax_id'] == '1234567890'
    assert not data['issues']
    missing = extract_document(RECEIPT.replace('VKN: 1234567890', label + ': 11111111111'), 'receipts')
    assert missing['tax_id'] == ''
    assert 'VKN / TCKN okunamadı.' in missing['issues']


def test_conflicting_seller_identities_still_block():
    data = extract_document(RECEIPT + '\nVKN: 9876543210', 'receipts')
    assert any('birden fazla numara' in issue for issue in data['issues'])


def test_brand_before_wrapped_legal_name():
    data = extract_document(RECEIPT.replace('ORNEK MARKET', 'ORNEK MAGAZA\nDENEME GIYIM VE TEKSTIL\nSAN. VE TIC. LTD. STI.'), 'receipts')
    assert data['seller_name'] == 'DENEME GIYIM VE TEKSTIL SAN. VE TIC. LTD. STI.'
    assert not data['issues']


def test_missing_time_requires_two_matching_source_crop_reads():
    from PIL import Image
    from services.paddle_ocr import reread_missing_date
    from test_paddle_ocr import output
    data = [extract_document(RECEIPT, 'receipts'), extract_document(RECEIPT.replace('14:25:36', '14:2'), 'receipts')]
    assert not data[1]['document_time']
    with Image.new('RGB', (800, 300)) as source:
        reads = reread_missing_date(source, data, [[0, 0, 800, 300]], lambda image: output('13.09.2026 SAAT: 14:25:36'), 'receipts')
    assert len(reads) == 2 and all(read['verified'] for read in reads)
    assert data[0]['document_time'] == data[1]['document_time'] == '14:25:36'
