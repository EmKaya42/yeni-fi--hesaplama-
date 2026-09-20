from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
import time
from pathlib import Path

from services.document_ocr import read_document, RetryableOCRError
from services.storage import database
from services.seller_identity import complete_seller, queue_missing_sellers

logger = logging.getLogger(__name__)


def process_next(db_path: Path, reader=read_document) -> bool:
    with database(db_path) as db:
        db.execute("BEGIN IMMEDIATE")
        # A killed deployment's claimed job becomes available after its lease expires.
        db.execute("UPDATE documents SET status='queued', error='Kesilen okuma yeniden başlatılıyor.' WHERE status='processing' AND started_at < ?", (time.time() - 600,))
        row = db.execute("SELECT * FROM documents WHERE status='queued' AND retry_after<=? ORDER BY created_at, page LIMIT 1", (time.time(),)).fetchone()
        if not row:
            return False
        claim_time = time.time()
        db.execute("UPDATE documents SET status='processing', started_at=?, attempts=attempts+1, error='' WHERE id=?", (claim_time, row["id"]))
    try:
        result = reader(Path(row["path"]), row["page"], row["kind"], row["attempts"] + 1)
        with database(db_path) as db:
            db.execute("BEGIN IMMEDIATE")
            active = db.execute("SELECT 1 FROM documents WHERE id=? AND status='processing' AND started_at=?", (row["id"], claim_time)).fetchone()
            if not active:
                return True
            final_result = complete_seller(db, row, result)
            effective_kind = row['kind']
            status = 'review' if final_result['issues'] else 'success'
            fingerprint = None
            duplicate_of = None
            if status == 'success':
                identity = '|'.join((final_result['tax_id'], final_result['document_no'], final_result['document_datetime'][:10], final_result['total_amount']))
                if final_result.get('fiscal_id') or final_result.get('device_no'):
                    identity += '|' + (final_result.get('fiscal_id') or final_result['device_no'])
                if row['chart_id']:
                    identity = row['chart_id'] + '|' + identity
                fingerprint = hashlib.sha256(identity.encode()).hexdigest()
            if fingerprint:
                duplicate = db.execute("SELECT id FROM documents WHERE user_id=? AND kind=? AND fingerprint=? AND id!=?", (row["user_id"], effective_kind, fingerprint, row["id"])).fetchone()
                if duplicate:
                    status, duplicate_of, fingerprint = "duplicate", duplicate["id"], None
            db.execute("UPDATE documents SET status=?,result=?,fingerprint=?,duplicate_of=?,error='' WHERE id=? AND status='processing'", (status, json.dumps(final_result, ensure_ascii=False), fingerprint, duplicate_of, row["id"]))
            if status == 'success':
                queue_missing_sellers(db, row, final_result)
            if status == "review" and final_result.get("engine", "").startswith(("Tesseract", "PaddleOCR")) and row["auto_retries"] < 1:
                db.execute("UPDATE documents SET status='queued',auto_retries=auto_retries+1,retry_after=?,error='Alternatif okuma otomatik deneniyor.' WHERE id=? AND status='review'", (time.time()+5, row["id"]))
    except Exception as error:
        logger.exception("Document processing failed: %s", row["id"])
        message = str(error) if isinstance(error, (RuntimeError, ValueError)) else "Dosya okunurken sunucu hatası oluştu. Yeniden deneyin."
        with database(db_path) as db:
            if isinstance(error, RetryableOCRError) and row["auto_retries"] < 2:
                db.execute("UPDATE documents SET status='queued',auto_retries=auto_retries+1,retry_after=?,error=? WHERE id=? AND status='processing' AND started_at=?", (time.time()+5*(row["auto_retries"]+1), "Geçici okuma hatası; otomatik yeniden denenecek.", row["id"], claim_time))
            else:
                db.execute("UPDATE documents SET status='failed',error=? WHERE id=? AND status='processing' AND started_at=?", (message[:500], row["id"], claim_time))
    return True


class QueueWorker:
    def __init__(self, db_path):
        self.db_path = db_path
        self.thread = None
        self.lock = threading.Lock()
        self.wake = threading.Event()

    def start(self):
        with self.lock:
            if not self.thread or not self.thread.is_alive():
                self.thread = threading.Thread(target=self.run, daemon=True, name="document-ocr")
                self.thread.start()
        self.wake.set()

    def run(self):
        while True:
            try:
                if process_next(self.db_path):
                    continue
            except (sqlite3.Error, OSError):
                logger.exception("Document queue unavailable")
            self.wake.wait(3)
            self.wake.clear()
