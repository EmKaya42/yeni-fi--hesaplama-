import json

import pytest
from openpyxl import load_workbook

from services.accounting import export_workbook
from services.document_extraction import extract_document
from services.queue_worker import process_next
from services.storage import database
from test_workflow import RECEIPT, Z_REPORT, client, profile, server, upload


def verified(text, kind):
    data = extract_document(text, kind)
    data['ocr_reads'] = [{'variant': mode, 'raw_text': text} for mode in ('original', 'contrast')]
    return data


def source_document(client, text=RECEIPT, index=0):
    source = upload(client, index=index)
    assert process_next(server.DB_PATH, lambda *args: verified(text, 'receipts'))
    return source


def missing_name(kind):
    text = (RECEIPT if kind == 'receipts' else Z_REPORT).replace('ORNEK MARKET\n', '')
    if kind == 'receipts':
        text = text.replace('000123', '000124')
    return verified(text, kind)


@pytest.mark.parametrize('kind', ['receipts', 'z-reports'])
def test_missing_seller_is_completed_and_exported_with_provenance(client, kind):
    source = source_document(client)
    target = upload(client, index=1, kind=kind)
    original = missing_name(kind)
    process_next(server.DB_PATH, lambda *args: original)
    doc = client.get(f'/api/documents/{target}', headers=client.auth).json
    assert doc['status'] == 'success'
    data = doc['result']
    assert data['seller_name'] == 'ORNEK MARKET'
    assert data['field_sources']['seller_name']['document_id'] == source
    assert not data['issues']
    assert not data['raw_text'].startswith('ORNEK MARKET')
    assert data['total_amount'] == '120.00'
    book = load_workbook(export_workbook([doc], profile()))
    rows = list(book['Belge Bilgileri'].values)
    exported = dict(zip(rows[0], rows[1]))
    assert exported['Otomatik firma adı kaynağı'] == 'fis-0.png'
    assert 'otomatik tamamlandı' in exported['Okuma notları']


@pytest.mark.parametrize('change', ['user', 'chart', 'tax_id', 'review', 'inferred', 'disagrees', 'old'])
def test_unsuitable_sources_cannot_complete_name(client, change):
    source = source_document(client)
    with database(server.DB_PATH) as db:
        row = db.execute('SELECT * FROM documents WHERE id=?', (source,)).fetchone()
        data = json.loads(row['result'])
        if change in ('user', 'chart', 'review'):
            column, value = {'user': ('user_id', 'bob'), 'chart': ('chart_id', 'other'), 'review': ('status', 'review')}[change]
            db.execute(f'UPDATE documents SET {column}=? WHERE id=?', (value, source))
        elif change == 'tax_id':
            data['tax_id'] = '9999999999'
        elif change == 'inferred':
            data['field_sources'] = {'seller_name': {'document_id': 'earlier'}}
        elif change == 'disagrees':
            data['ocr_reads'][1]['raw_text'] = RECEIPT.replace('ORNEK MARKET', 'BASKA MARKET')
        else:
            data['extraction_version'] = 1
        db.execute('UPDATE documents SET result=? WHERE id=?', (json.dumps(data), source))
    target = upload(client, index=1)
    process_next(server.DB_PATH, lambda *args: missing_name('receipts'))
    doc = client.get(f'/api/documents/{target}', headers=client.auth).json
    assert doc['status'] == 'review'
    assert doc['result']['seller_name'] == ''


def test_ambiguous_names_are_not_selected_by_recency(client):
    source_document(client)
    source_document(client, RECEIPT.replace('ORNEK MARKET', 'BASKA MARKET').replace('000123', '000125'), 1)
    target = upload(client, index=2)
    process_next(server.DB_PATH, lambda *args: missing_name('receipts'))
    doc = client.get(f'/api/documents/{target}', headers=client.auth).json
    assert doc['status'] == 'review' and doc['result']['seller_name'] == ''
    assert any('farklı firma unvanları' in note for note in doc['result']['notes'])


def test_cropped_name_conflict_is_resolved_without_changing_raw_reads(client):
    source_document(client)
    target = upload(client, index=1, kind='z-reports')
    data = missing_name('z-reports')
    data['ocr_reads'][1]['raw_text'] = 'CROST\n' + data['ocr_reads'][1]['raw_text']
    data['conflicting_fields'] = ['seller_name']
    data['issues'] = ['Firma unvanı iki okumada doğrulanamadı. Unvanın tamamının göründüğü bir fotoğraf yükleyin.']
    process_next(server.DB_PATH, lambda *args: data)
    doc = client.get(f'/api/documents/{target}', headers=client.auth).json
    assert doc['status'] == 'success'
    assert not doc['result']['conflicting_fields']
    assert doc['result']['ocr_reads'][1]['raw_text'].startswith('CROST\n')


def test_automatic_completion_preserves_duplicate_detection(client):
    source = source_document(client)
    target = upload(client, index=1)
    data = verified(RECEIPT.replace('ORNEK MARKET\n', ''), 'receipts')
    process_next(server.DB_PATH, lambda *args: data)
    doc = client.get(f'/api/documents/{target}', headers=client.auth).json
    assert doc['status'] == 'duplicate'
    assert doc['duplicate_of'] == source


def test_later_complete_document_automatically_requeues_earlier_missing_name(client):
    target = upload(client, kind='z-reports')
    process_next(server.DB_PATH, lambda *args: missing_name('z-reports'))
    assert client.get(f'/api/documents/{target}', headers=client.auth).json['status'] == 'review'
    source_document(client, index=1)
    assert client.get(f'/api/documents/{target}', headers=client.auth).json['status'] == 'queued'
    process_next(server.DB_PATH, lambda *args: missing_name('z-reports'))
    assert client.get(f'/api/documents/{target}', headers=client.auth).json['status'] == 'success'
    assert not process_next(server.DB_PATH, lambda *args: pytest.fail('Unexpected retry loop'))


@pytest.mark.parametrize('change', ['tax_conflict', 'weak_tax', 'other_issue', 'existing_name'])
def test_completion_keeps_tax_and_other_field_checks(client, change):
    source_document(client)
    target = upload(client, index=1)
    data = missing_name('receipts')
    if change == 'tax_conflict':
        data['ocr_reads'][1]['raw_text'] = data['ocr_reads'][1]['raw_text'].replace('1234567890', '9999999999')
    elif change == 'weak_tax':
        data['issues'].append('VKN / TCKN okuma güveni düşük.')
    elif change == 'other_issue':
        data['issues'].append('KDV kırılımı, toplam KDV ile uyuşmuyor.')
    else:
        data['seller_name'] = 'OKUNAN FIRMA'
    process_next(server.DB_PATH, lambda *args: data)
    doc = client.get(f'/api/documents/{target}', headers=client.auth).json
    assert doc['status'] == 'review'
    if change == 'other_issue':
        assert doc['result']['seller_name'] == 'ORNEK MARKET'
        assert doc['result']['issues'] == ['KDV kırılımı, toplam KDV ile uyuşmuyor.']
    elif change == 'existing_name':
        assert doc['result']['seller_name'] == 'OKUNAN FIRMA'
    else:
        assert doc['result']['seller_name'] == ''
