import pytest

from services.banking import extract_payments


@pytest.mark.parametrize('metadata', ['ISYERI 123456789 POS P1234567', 'POS NO: P1234567', 'POS ID 1234567', 'POS P1234567'])
def test_pos_terminal_number_is_not_an_unread_payment(metadata):
    data, issues = extract_payments('KREDI KARTI 118,47\n' + metadata, '118.47', 'receipts')
    assert not issues
    assert data['payment_entries'][0]['amount'] == '118.47'
    assert len(data['payment_entries']) == 1


@pytest.mark.parametrize('label,method', [('TROY/Kredi/OnUs', 'card'), ('ROY/K/ed1/OnUs', 'card'),
    ('TROY/KIed1/OnUs', 'card'), ('VISA/CREDIT/OFFUS', 'card'), ('MASTERCARD/DEBIT/ONUS', 'debit_card')])
def test_matching_approved_slip_resolves_combined_card_label(label, method):
    data, issues = extract_payments('Banka/Kredi Karti *118,47\n' + label + '\nTOPLAM 118,47 TL\nISLEM ONAYLANDI', '118.47', 'receipts')
    assert not issues
    assert data['payment_method'] == method
    assert len(data['payment_entries']) == 1
    assert data['payment_entries'][0]['amount'] == '118.47'


@pytest.mark.parametrize('slip', ['TROY/Kredi/OnUs\nTOPLAM 218,47 TL\nISLEM ONAYLANDI',
    'TROY/Kredi/OnUs\nTOPLAM 118,47 TL\nISLEM REDDEDILDI',
    'TROY/BELIRSIZ/OnUs\nTOPLAM 118,47 TL\nISLEM ONAYLANDI',
    'TROY/Kredi/OnUs\nTOPLAM OKUNAMADI\nISLEM ONAYLANDI'])
def test_unverified_slip_does_not_resolve_card_type(slip):
    data, issues = extract_payments('Banka/Kredi Karti *118,47\n' + slip, '118.47', 'receipts')
    assert data['payment_method'] == 'pos'
    assert any('Kartın banka kartı' in issue for issue in issues)


def test_ambiguous_multiple_pos_payments_are_not_assigned_one_slip_type():
    data, issues = extract_payments('POS 118,47\nPOS 118,47\nTROY/Kredi/OnUs\nTOPLAM 118,47 TL\nISLEM ONAYLANDI', '236.94', 'receipts')
    assert all(entry['method'] == 'pos' for entry in data['payment_entries'])
    assert issues


def test_genuine_pos_payment_with_missing_amount_still_blocks():
    _, issues = extract_payments('NAKIT 118,47\nPOS OKUNAMADI', '118.47', 'receipts')
    assert 'Ödeme alanı var ancak tutarı okunamadı.' in issues
