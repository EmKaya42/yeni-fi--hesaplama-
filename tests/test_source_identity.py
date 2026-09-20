import json

import pytest

from services.accounting import export_workbook
from services.document_extraction import extract_document
from services.queue_worker import process_next
from services.storage import database, initialize
from test_workflow import RECEIPT, Z_REPORT, client, profile, server, upload


def read(text, kind):
    result = extract_document(text, kind)
    result['ocr_reads'] = [{'raw_text': text}, {'raw_text': text}]
    return result


@pytest.mark.parametrize('kind', ['receipts', 'z-reports'])
def test_matching_tax_id_never_fills_missing_seller(client, kind):
    upload(client)
    process_next(server.DB_PATH, lambda *args: read(RECEIPT, 'receipts'))
    target = upload(client, index=1, kind=kind)
    text = (RECEIPT if kind == 'receipts' else Z_REPORT).replace('ORNEK MARKET\n', '')
    process_next(server.DB_PATH, lambda *args: read(text, kind))
    doc = client.get(f'/api/documents/{target}', headers=client.auth).json
    assert doc['status'] == 'review'
    assert doc['result']['seller_name'] == ''
    assert doc['result']['total_amount'] == '120.00'
    assert not doc['result'].get('field_sources')
    with pytest.raises(ValueError):
        export_workbook([doc], profile())


def test_later_good_document_does_not_change_missing_seller(client):
    target = upload(client, kind='z-reports')
    process_next(server.DB_PATH, lambda *args: read(Z_REPORT.replace('ORNEK MARKET\n', ''), 'z-reports'))
    upload(client, index=1)
    process_next(server.DB_PATH, lambda *args: read(RECEIPT, 'receipts'))
    doc = client.get(f'/api/documents/{target}', headers=client.auth).json
    assert doc['status'] == 'review' and doc['result']['seller_name'] == ''
    assert not process_next(server.DB_PATH)


@pytest.mark.parametrize('status', ['success', 'duplicate', 'review', 'processing'])
def test_previous_inferred_names_are_cleared_and_requeued_only_once(client, status):
    identity = upload(client)
    data = read(RECEIPT, 'receipts')
    source = {'method': 'same_tax_id', 'document_id': 'old-source', 'value': 'ORNEK MARKET'}
    data['field_sources'] = {'seller_name': source}
    data['notes'] = ['Firma unvanı aynı VKN / TCKN (1234567890) bulunan belgeden otomatik tamamlandı.', 'Diğer okuma notu.']
    with database(server.DB_PATH) as db:
        db.execute('UPDATE documents SET result=?, status=?, fingerprint=?, export_count=2, started_at=42 WHERE id=?',
                   (json.dumps(data), status, 'previous-fingerprint', identity))
        before = dict(db.execute('SELECT * FROM documents WHERE id=?', (identity,)).fetchone())
    initialize(server.DB_PATH)
    with database(server.DB_PATH) as db:
        after = dict(db.execute('SELECT * FROM documents WHERE id=?', (identity,)).fetchone())
        result = json.loads(after['result'])
        assert after['status'] == 'queued' and after['fingerprint'] is None and after['started_at'] is None
        assert result['seller_name'] == '' and result['issues']
        assert not result['field_sources']
        assert result['retracted_field_sources']['seller_name'] == source
        assert result['notes'] == ['Diğer okuma notu.']
        assert after['path'] == before['path'] and after['export_count'] == 2
        db.execute("UPDATE documents SET status='failed' WHERE id=?", (identity,))
    initialize(server.DB_PATH)
    assert client.get(f'/api/documents/{identity}', headers=client.auth).json['status'] == 'failed'


def test_inferred_seller_is_rejected_even_without_issue_flag():
    data = read(RECEIPT, 'receipts')
    data['field_sources'] = {'seller_name': {'method': 'same_tax_id'}}
    with pytest.raises(ValueError, match='başka belgeden'):
        export_workbook([{'kind': 'receipts', 'direction': 'expense', 'filename': 'test.png', 'result': data}], profile())
