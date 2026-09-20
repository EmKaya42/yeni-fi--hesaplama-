"""Complete missing seller names from verified documents in the same workspace."""
from __future__ import annotations

import json
import re

from services.document_extraction import EXTRACTION_VERSION, extract_document
from services.document_ocr import _canonical

SELLER_ISSUES = {
    'Firma adı okunamadı.',
    'Firma unvanı iki okumada doğrulanamadı. Unvanın tamamının göründüğü bir fotoğraf yükleyin.',
    'Firma unvanı iki okumada doğrulanamadı. Fotoğrafın üst kısmını ve unvanın tamamını kontrol edin.',
}


def identity_reads(data, kind):
    reads = data.get('ocr_reads', [])
    if len(reads) != 2 or any(not read.get('raw_text') for read in reads):
        return []
    return [extract_document(read['raw_text'], kind) for read in reads]


def queue_missing_sellers(db, source, result):
    """Upload order must not require the user to retry the earlier cropped file."""
    if result.get('field_sources', {}).get('seller_name') or not result.get('seller_name'):
        return
    reads = identity_reads(result, source['kind'])
    if len(reads) != 2 or any(read.get('tax_id') != result.get('tax_id') or
            _canonical(read.get('seller_name')) != _canonical(result['seller_name']) for read in reads):
        return
    db.execute("""UPDATE documents SET status='queued', retry_after=0, auto_retries=0,
            started_at=NULL, fingerprint=NULL, duplicate_of=NULL,
            error='Aynı vergi kimliğiyle firma unvanı bulundu; otomatik yeniden değerlendiriliyor.'
            WHERE user_id=? AND chart_id=? AND status='review' AND id!=?
            AND json_extract(result, '$.tax_id')=?
            AND COALESCE(json_extract(result, '$.seller_name'), '')=''""",
            (source['user_id'], source['chart_id'], source['id'], result['tax_id']))


def complete_seller(db, document, result):
    tax_id = result.get('tax_id', '')
    if result.get('seller_name') or not re.fullmatch(r'\d{10,11}', tax_id):
        return result
    # A weak or conflicting tax identity must never select another company's name.
    if any('VKN' in issue or 'vergi kimliği' in issue for issue in result.get('issues', [])):
        return result
    reads = identity_reads(result, document['kind'])
    if len(reads) != 2 or any(read.get('tax_id') != tax_id for read in reads):
        return result
    candidates = []
    for source in db.execute("""SELECT id, filename, kind, result FROM documents
            WHERE user_id=? AND chart_id=? AND status='success' AND id!=?
            AND json_extract(result, '$.tax_id')=? ORDER BY created_at DESC, id""",
            (document['user_id'], document['chart_id'], document['id'], tax_id)):
        data = json.loads(source['result'])
        if data.get('issues') or data.get('extraction_version', 0) < EXTRACTION_VERSION:
            continue
        # Do not build chains of inferred names: use direct, agreeing source reads.
        if data.get('field_sources', {}).get('seller_name'):
            continue
        source_reads = identity_reads(data, source['kind'])
        name = data.get('seller_name', '')
        if name and len(source_reads) == 2 and all(
                read.get('tax_id') == tax_id and _canonical(read.get('seller_name')) == _canonical(name)
                for read in source_reads):
            candidates.append((source, name))
    if not candidates:
        return result
    if len({_canonical(name) for _, name in candidates}) != 1:
        result.setdefault('notes', []).append('Aynı VKN / TCKN için farklı firma unvanları bulundu; otomatik tamamlama yapılmadı.')
        return result
    source, name = candidates[0]
    result['seller_name'] = name
    result['issues'] = [issue for issue in result['issues'] if issue not in SELLER_ISSUES]
    result['conflicting_fields'] = [field for field in result.get('conflicting_fields', []) if field != 'seller_name']
    result.setdefault('field_sources', {})['seller_name'] = {
        'method': 'same_tax_id', 'document_id': source['id'], 'filename': source['filename'],
        'tax_id': tax_id, 'value': name,
    }
    result.setdefault('notes', []).append(
        f'Firma unvanı aynı VKN / TCKN ({tax_id}) bulunan, kontrolleri geçmiş “{source["filename"]}” belgesinden otomatik tamamlandı.')
    return result
