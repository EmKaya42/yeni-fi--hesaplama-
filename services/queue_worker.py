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

logger = logging.getLogger(__name__)


def process_next(db_path: Path, reader=read_document) -> bool:
    with database(db_path) as db:
        db.execute("BEGIN IMMEDIATE")
        # A killed deployment's claimed job becomes available after its lease expires.
        db.execute("UPDATE documents SET status='queued', error='Kesilen okuma yeniden başlatılıyor.' WHERE status='processing' AND started_at < ?", (time.time() - 600,))
        row = db.execute("SELECT * FROM documents WHERE status='queued' AND retry_after<=? ORDER BY created_at, page LIMIT 1", (time.time(),)).fetchone()
        if not row:
            return False
        db.execute("UPDATE documents SET status='processing', started_at=?, attempts=attempts+1, error='' WHERE id=?", (time.time(), row["id"]))
    try:
        result = reader(Path(row["path"]), row["page"], row["kind"], row["attempts"] + 1)
        status = "review" if result["issues"] else "success"
        fingerprint = None
        duplicate_of = None
        if status == "success":
            identity = "|".join((result["tax_id"], result["document_no"], result["document_datetime"][:10], result["total_amount"]))
            if result.get("fiscal_id") or result.get("device_no"):
                identity += "|" + (result.get("fiscal_id") or result["device_no"])
            if row["chart_id"]:
                identity = row["chart_id"] + "|" + identity
            fingerprint = hashlib.sha256(identity.encode()).hexdigest()
        with database(db_path) as db:
            db.execute("BEGIN IMMEDIATE")
            if fingerprint:
                duplicate = db.execute("SELECT id FROM documents WHERE user_id=? AND kind=? AND fingerprint=? AND id!=?", (row["user_id"], row["kind"], fingerprint, row["id"])).fetchone()
                if duplicate:
                    status, duplicate_of, fingerprint = "duplicate", duplicate["id"], None
            db.execute("UPDATE documents SET status=?,result=?,fingerprint=?,duplicate_of=?,error='' WHERE id=? AND status='processing'", (status, json.dumps(result, ensure_ascii=False), fingerprint, duplicate_of, row["id"]))
            if status == "review" and result.get("engine", "").startswith("Tesseract") and row["auto_retries"] < 1:
                db.execute("UPDATE documents SET status='queued',auto_retries=auto_retries+1,retry_after=?,error='Alternatif okuma otomatik deneniyor.' WHERE id=? AND status='review'", (time.time()+2, row["id"]))
    except Exception as error:
        logger.exception("Document processing failed: %s", row["id"])
        message = str(error) if isinstance(error, (RuntimeError, ValueError)) else "Dosya okunurken sunucu hatası oluştu. Yeniden deneyin."
        with database(db_path) as db:
            if isinstance(error, RetryableOCRError) and row["auto_retries"] < 2:
                db.execute("UPDATE documents SET status='queued',auto_retries=auto_retries+1,retry_after=?,error=? WHERE id=? AND status='processing'", (time.time()+5*(row["auto_retries"]+1), "Geçici okuma hatası; otomatik yeniden denenecek.", row["id"]))
            else:
                db.execute("UPDATE documents SET status='failed',error=? WHERE id=? AND status='processing'", (message[:500], row["id"]))
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
