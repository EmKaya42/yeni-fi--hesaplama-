from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def database(path: Path):
    connection = sqlite3.connect(path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=30000")
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def initialize(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with database(path) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript("""
            CREATE TABLE IF NOT EXISTS profiles (
                user_id TEXT NOT NULL, program TEXT NOT NULL, data TEXT NOT NULL,
                PRIMARY KEY(user_id, program)
            );
            CREATE TABLE IF NOT EXISTS preferences (user_id TEXT PRIMARY KEY, program TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS account_charts (
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL, program TEXT NOT NULL,
                name TEXT NOT NULL, filename TEXT NOT NULL, digest TEXT NOT NULL,
                data TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS charts_owner ON account_charts(user_id, program);
            CREATE TABLE IF NOT EXISTS export_log (
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL, chart_id TEXT NOT NULL,
                program TEXT NOT NULL, kind TEXT NOT NULL, document_ids TEXT NOT NULL,
                digest TEXT NOT NULL, created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS documents (
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL, kind TEXT NOT NULL,
                filename TEXT NOT NULL, path TEXT NOT NULL, digest TEXT NOT NULL, page INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued', attempts INTEGER NOT NULL DEFAULT 0,
                result TEXT NOT NULL DEFAULT '{}', error TEXT NOT NULL DEFAULT '',
                direction TEXT NOT NULL DEFAULT 'expense', account_code TEXT NOT NULL DEFAULT '',
                fingerprint TEXT, duplicate_of TEXT, created_at REAL NOT NULL, started_at REAL,
                UNIQUE(user_id, kind, digest, page)
            );
            CREATE INDEX IF NOT EXISTS documents_queue ON documents(status, created_at);
            CREATE INDEX IF NOT EXISTS documents_user ON documents(user_id, kind, created_at);
            CREATE UNIQUE INDEX IF NOT EXISTS documents_fingerprint ON documents(user_id, kind, fingerprint) WHERE fingerprint IS NOT NULL;
        """)
        columns = {row[1] for row in db.execute("PRAGMA table_info(documents)")}
        for name, definition in {"chart_id": "TEXT NOT NULL DEFAULT ''", "retry_after": "REAL NOT NULL DEFAULT 0", "auto_retries": "INTEGER NOT NULL DEFAULT 0", "export_count": "INTEGER NOT NULL DEFAULT 0", "exported_at": "REAL"}.items():
            if name not in columns:
                db.execute(f"ALTER TABLE documents ADD COLUMN {name} {definition}")
        db.execute("CREATE INDEX IF NOT EXISTS documents_chart ON documents(user_id, chart_id, kind)")
        # Before extraction v2, product_name held the seller. Preserve original
        # results and files, but require another read before exporting products.
        db.execute("""UPDATE documents SET status='review', fingerprint=NULL,
                      error='Ürün adları için yeniden okuma gerekli. Yeniden dene düğmesini kullanın.'
                      WHERE kind='receipts' AND status='success'
                      AND COALESCE(json_extract(result, '$.extraction_version'), 0) < 2""")
        # New fiscal fields must be read from the source, not filled from old
        # results. Preserve files, previous values and export history.
        db.execute("""UPDATE documents SET status='queued', fingerprint=NULL, duplicate_of=NULL,
                      retry_after=0, auto_retries=0, started_at=NULL,
                      error='Yeni belge alanları kaynak dosyadan otomatik okunacak.'
                      WHERE status IN ('success','duplicate')
                      AND COALESCE(json_extract(result, '$.extraction_version'), 0) BETWEEN 2 AND 3""")


def document_dict(row, detail=False):
    doc = dict(row)
    doc.pop("user_id", None)
    doc.pop("path", None)
    doc.pop("digest", None)
    doc.pop("fingerprint", None)
    doc["result"] = json.loads(doc["result"])
    if doc["result"].get("extraction_version", 0) < 2:
        doc["result"]["seller_name"] = doc["result"].get("product_name", "")
        doc["result"]["product_name"] = ""
    if not detail:
        doc["result"].pop("raw_text", None)
    return doc
