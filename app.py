from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from collections import Counter
import sqlite3
import time
import uuid
from pathlib import Path

import jwt
from flask import Flask, g, jsonify, render_template, request, send_file
from werkzeug.exceptions import HTTPException
from werkzeug.utils import secure_filename

from services.accounting import ACCOUNT_LABELS, ACCOUNT_NAMES, FIELDS, PROGRAMS, default_profile, export_workbook, journal_rows, read_template, resolve_profile, validate_profile
from services.account_chart import read_chart
from services.banking import BANKS, BANK_BY_CODE, BANK_SOURCE, BANK_VERIFIED_AT, PAYMENT_LABELS
from services.document_extraction import folded
from services.document_ocr import inspect_file
from services.queue_worker import QueueWorker
from services.storage import database, document_dict, initialize

BASE_DIR = Path(__file__).resolve().parent
RUNTIME_DIR = Path(os.getenv("DATA_DIR", "/tmp/fis-takip" if os.getenv("VERCEL") else str(BASE_DIR / "data")))
DB_PATH = RUNTIME_DIR / "fis_takip.db"
UPLOAD_DIR = RUNTIME_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
initialize(DB_PATH)
app = Flask(__name__)
app.json.sort_keys = False
app.config.update(MAX_CONTENT_LENGTH=21 * 1024 * 1024, WORKER_ENABLED=True)
worker = QueueWorker(DB_PATH)
jwks = jwt.PyJWKClient("https://www.googleapis.com/service_accounts/v1/jwk/securetoken@system.gserviceaccount.com", lifespan=3600, timeout=10)
PROJECT_ID = os.getenv("FIREBASE_PROJECT_ID", "html-web-uygulama")
KINDS = {"receipts", "z-reports"}


def local_auth():
    if app.testing:
        return False
    if os.getenv("RAILWAY_ENVIRONMENT_ID") or os.getenv("VERCEL"):
        return False
    return os.getenv("ALLOW_LOCAL_AUTH", "0") == "1" and request.remote_addr in {"127.0.0.1", "::1"}


@app.before_request
def authenticate():
    if app.config["WORKER_ENABLED"] and not app.testing:
        worker.start()
    if not (request.path.startswith("/api/") or request.path.startswith("/export/")):
        return None
    if local_auth():
        g.user_id = "local-user"
        return None
    token = request.headers.get("Authorization", "")
    if not token.startswith("Bearer "):
        return jsonify(error="Devam etmek için giriş yapın."), 401
    token = token[7:]
    try:
        key = jwks.get_signing_key_from_jwt(token).key
        claims = jwt.decode(token, key, algorithms=["RS256"], audience=PROJECT_ID,
                            issuer=f"https://securetoken.google.com/{PROJECT_ID}",
                            options={"require": ["exp", "iat", "sub", "aud", "iss"]})
        if not isinstance(claims["sub"], str) or not 1 <= len(claims["sub"]) <= 128:
            raise jwt.InvalidTokenError("Invalid user")
        g.user_id = claims["sub"]
    except jwt.PyJWKClientConnectionError:
        return jsonify(error="Oturum doğrulama servisine ulaşılamadı. Tekrar deneyin."), 503
    except jwt.PyJWTError:
        return jsonify(error="Oturumunuz doğrulanamadı. Yeniden giriş yapın."), 401


@app.after_request
def security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    if request.path.startswith(("/api/", "/export/")):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.errorhandler(Exception)
def handle_error(error):
    if isinstance(error, HTTPException):
        return jsonify(error="Dosya en fazla 20 MB olabilir." if error.code == 413 else "İstek işlenemedi."), error.code
    if isinstance(error, ValueError):
        return jsonify(error=str(error)), 400
    app.logger.exception("Request failed")
    return jsonify(error="Sunucu işlemi tamamlayamadı. Yeniden deneyin."), 500


@app.get("/")
def index():
    return render_template("landing.html")


@app.get("/app")
def workspace():
    return render_template("index.html", local_auth=local_auth())


@app.get("/health")
def health():
    with database(DB_PATH) as db:
        db.execute("SELECT 1")
    return jsonify(status="ok")


@app.get("/firebase-config.js")
def firebase_config():
    return send_file(BASE_DIR / "firebase-config.js", mimetype="application/javascript")


@app.get("/favicon.ico")
def favicon():
    return "", 204


def selected_profile(db):
    pref = db.execute("SELECT program FROM preferences WHERE user_id=?", (g.user_id,)).fetchone()
    if not pref:
        return None
    program = pref["program"]
    row = db.execute("SELECT data FROM profiles WHERE user_id=? AND program=?", (g.user_id, program)).fetchone()
    if row:
        return hydrate_profile(db, resolve_profile(json.loads(row["data"])))
    return hydrate_profile(db, default_profile(program))


def hydrate_profile(db, profile):
    profile = resolve_profile(profile)
    profile.pop("chart", None)
    if profile["chart_id"]:
        chart = db.execute("SELECT * FROM account_charts WHERE id=? AND user_id=? AND program=?", (profile["chart_id"], g.user_id, profile["program"])).fetchone()
        if not chart:
            raise ValueError("Seçili firma hesap planına erişilemiyor.")
        profile["chart"] = {"id": chart["id"], "name": chart["name"], "accounts": json.loads(chart["data"])}
    return profile


def context_profile(db, values):
    profile = selected_profile(db)
    if not profile:
        raise ValueError("Önce muhasebe programınızı seçin.")
    program = values.get("program", profile["program"])
    if program != profile["program"]:
        row = db.execute("SELECT data FROM profiles WHERE user_id=? AND program=?", (g.user_id, program)).fetchone()
        if not row:
            profile = default_profile(program)
        else:
            profile = resolve_profile(json.loads(row["data"]))
    if "chart_id" in values:
        profile["chart_id"] = values.get("chart_id", "")
    return hydrate_profile(db, profile)


def chart_summary(row):
    accounts = json.loads(row["data"])
    bank_accounts = [{**{key: account[key] for key in ("code", "name", "role", "bank_code", "card_last4")},
                      "bank_name": BANK_BY_CODE.get(account["bank_code"], {}).get("name", ""),
                      "iban_masked": account["iban"][:9] + "…" + account["iban"][-4:] if account["iban"] else ""}
                     for account in accounts if account["leaf"] and account["role"] in {"bank", "expense_card", "income_card"}]
    return {"id": row["id"], "program": row["program"], "name": row["name"], "filename": row["filename"], "account_count": len(accounts), "bank_accounts": bank_accounts, "updated_at": row["updated_at"]}


def public_profile(profile):
    return {key: value for key, value in profile.items() if key != "chart"} if profile else None


def format_document(row, profile, detail=False):
    doc = document_dict(row, detail=True)
    if doc["status"] == "success":
        try:
            entries = journal_rows([doc], profile)
            doc["accounting"] = {"status": "matched" if profile.get("chart") else "standard", "rows": [{"account": entry["account"], "name": entry["account_name"], "debit": str(entry["debit"]), "credit": str(entry["credit"]), "bank_name": entry["bank_name"]} for entry in entries]}
        except ValueError as error:
            doc["status"] = "mapping"
            doc["error"] = str(error)
    if not detail:
        doc["result"].pop("raw_text", None)
        doc["result"].pop("normalized_text", None)
        doc["result"].pop("ocr_reads", None)
        if doc.get("accounting"):
            doc["accounting"].pop("rows", None)
    return doc


@app.get("/api/settings")
def settings():
    with database(DB_PATH) as db:
        selected = selected_profile(db)
        saved = {row["program"]: resolve_profile(json.loads(row["data"])) for row in db.execute("SELECT * FROM profiles WHERE user_id=?", (g.user_id,))}
        charts = [chart_summary(row) for row in db.execute("SELECT * FROM account_charts WHERE user_id=? ORDER BY updated_at DESC", (g.user_id,))]
    return jsonify(programs=PROGRAMS, fields=FIELDS, account_labels=ACCOUNT_LABELS, account_names=ACCOUNT_NAMES, selected=public_profile(selected),
                   profiles=saved, defaults={p["id"]: default_profile(p["id"]) for p in PROGRAMS}, charts=charts, payment_labels=PAYMENT_LABELS,
                   banks=[{"code": bank["code"], "name": bank["name"]} for bank in BANKS], bank_source=BANK_SOURCE, bank_verified_at=BANK_VERIFIED_AT)


@app.put("/api/settings")
def save_settings():
    profile = validate_profile(request.get_json(silent=True))
    with database(DB_PATH) as db:
        hydrate_profile(db, profile)  # Only the user's imported charts are selectable.
        db.execute("INSERT INTO profiles VALUES (?,?,?) ON CONFLICT(user_id,program) DO UPDATE SET data=excluded.data", (g.user_id, profile["program"], json.dumps(profile, ensure_ascii=False)))
        db.execute("INSERT INTO preferences VALUES (?,?) ON CONFLICT(user_id) DO UPDATE SET program=excluded.program", (g.user_id, profile["program"]))
    return jsonify(profile=profile)


@app.post("/api/account-charts")
def import_account_chart():
    file = request.files.get("file")
    if not file or not file.filename or Path(file.filename).suffix.lower() not in {".xls", ".xlsx", ".csv"}:
        raise ValueError("Firmanın XLS, XLSX veya CSV hesap planını seçin.")
    filename = file.filename.replace("\\", "/").split("/")[-1][:200]
    accounts = read_chart(file.stream, filename)
    data = json.dumps(accounts, ensure_ascii=False)
    digest = hashlib.sha256(data.encode()).hexdigest()
    now = time.time()
    with database(DB_PATH) as db:
        program = request.form.get("program")
        if program not in {item["id"] for item in PROGRAMS}:
            raise ValueError("Önce muhasebe programını seçin.")
        saved = db.execute("SELECT data FROM profiles WHERE user_id=? AND program=?", (g.user_id, program)).fetchone()
        profile = resolve_profile(json.loads(saved["data"])) if saved else default_profile(program)
        chart_id = request.form.get("chart_id", "")
        if chart_id:
            old = db.execute("SELECT id FROM account_charts WHERE id=? AND user_id=? AND program=?", (chart_id, g.user_id, program)).fetchone()
            if not old:
                raise ValueError("Güncellenecek firma hesap planı bulunamadı.")
            db.execute("UPDATE account_charts SET filename=?,digest=?,data=?,updated_at=? WHERE id=?", (filename, digest, data, now, chart_id))
        else:
            chart_id = uuid.uuid4().hex
            db.execute("INSERT INTO account_charts VALUES (?,?,?,?,?,?,?,?,?)", (chart_id, g.user_id, program, Path(filename).stem, filename, digest, data, now, now))
        profile["chart_id"] = chart_id
        db.execute("INSERT INTO profiles VALUES (?,?,?) ON CONFLICT(user_id,program) DO UPDATE SET data=excluded.data", (g.user_id, program, json.dumps(profile, ensure_ascii=False)))
        db.execute("INSERT INTO preferences VALUES (?,?) ON CONFLICT(user_id) DO UPDATE SET program=excluded.program", (g.user_id, program))
        result = chart_summary(db.execute("SELECT * FROM account_charts WHERE id=?", (chart_id,)).fetchone())
    return jsonify(chart=result), 201


@app.post("/api/template")
def template():
    file = request.files.get("file")
    if not file or not file.filename or Path(file.filename).suffix.lower() not in {".xls", ".xlsx"}:
        raise ValueError("Programınızın .xls veya .xlsx aktarım şablonunu seçin.")
    result = read_template(file.stream)
    result["template_name"] = Path(file.filename).name
    return jsonify(result)


@app.get("/api/documents")
def documents():
    kind = request.args.get("kind", "receipts")
    if kind not in KINDS:
        raise ValueError("Belge türü geçersiz.")
    limit = max(1, min(100, request.args.get("limit", 50, type=int)))
    offset = max(0, request.args.get("offset", 0, type=int))
    status = request.args.get("status", "")
    search = request.args.get("search", "").strip()[:100]
    period = request.args.get("period", "")
    if period and not re.fullmatch(r"20\d{2}-(?:0[1-9]|1[0-2])", period):
        raise ValueError("Dönem geçersiz.")
    with database(DB_PATH) as db:
        profile = context_profile(db, request.args) if selected_profile(db) else default_profile("custom")
        rows = db.execute("SELECT * FROM documents WHERE user_id=? AND kind=? AND chart_id=? ORDER BY created_at DESC,page", (g.user_id, kind, profile["chart_id"])).fetchall()
    items = [format_document(row, profile) for row in rows]
    queue_counts = dict(Counter(item["status"] for item in items if item["status"] in {"queued", "processing"}))
    pending = sum(queue_counts.values())
    periods = sorted({item["result"].get("document_datetime", "")[:7] for item in items if item["result"].get("document_datetime")}, reverse=True)
    if period:
        items = [item for item in items if item["result"].get("document_datetime", "").startswith(period)]
    counts = dict(Counter(item["status"] for item in items))
    exported = sum(bool(item["export_count"]) for item in items)
    if status:
        items = [item for item in items if item["status"] == status]
    if search:
        search_key = folded(search)
        items = [item for item in items if search_key in folded(" ".join(str(value) for value in [item["filename"], item["result"].get("document_no", ""), item["result"].get("seller_name", ""), item["result"].get("product_name", "")]))]
    return jsonify(items=items[offset:offset+limit], counts=counts, total=len(items), periods=periods, exported=exported, pending=pending, queue_counts=queue_counts)



@app.post("/api/documents")
def upload_document():
    kind = request.form.get("kind", "receipts")
    direction = "income" if kind == "z-reports" else request.form.get("direction", "expense")
    if kind not in KINDS or direction not in {"expense", "income"}:
        raise ValueError("Belge türü veya gelir/gider seçimi geçersiz.")
    with database(DB_PATH) as db:
        profile = context_profile(db, request.form)
        chart_id = profile["chart_id"]
    file = request.files.get("file")
    if not file or not file.filename:
        raise ValueError("Dosya seçin.")
    filename = file.filename.replace("\\", "/").split("/")[-1][:200]
    suffix = Path(filename).suffix.lower()
    if suffix not in {".pdf", ".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".bmp"}:
        raise ValueError("JPG, PNG, WEBP, TIFF, BMP veya PDF ekleyin.")
    path = UPLOAD_DIR / f"{uuid.uuid4().hex}{suffix}"
    stored = False
    try:
        digest = hashlib.sha256()
        size = 0
        with path.open("wb") as output:
            while chunk := file.stream.read(1024 * 1024):
                size += len(chunk)
                if size > 20 * 1024 * 1024:
                    raise ValueError("Her dosya en fazla 20 MB olabilir.")
                output.write(chunk)
                digest.update(chunk)
        pages = inspect_file(path)
        scoped_digest = hashlib.sha256((chart_id + "|" + digest.hexdigest()).encode()).hexdigest() if chart_id else digest.hexdigest()
        items = []
        with database(DB_PATH) as db:
            db.execute("BEGIN IMMEDIATE")
            for page in range(pages):
                old = db.execute("SELECT * FROM documents WHERE user_id=? AND kind=? AND digest=? AND page=?", (g.user_id, kind, scoped_digest, page)).fetchone()
                if old:
                    if old["status"] in {"review", "failed"}:
                        db.execute("UPDATE documents SET status='queued',error='',started_at=NULL,retry_after=0,auto_retries=0,fingerprint=NULL,duplicate_of=NULL WHERE id=?", (old["id"],))
                    items.append({"id": old["id"], "duplicate": True})
                    continue
                doc_id = uuid.uuid4().hex
                db.execute("INSERT INTO documents (id,user_id,kind,filename,path,digest,page,direction,created_at,chart_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
                           (doc_id, g.user_id, kind, filename, str(path), scoped_digest, page, direction, time.time(), chart_id))
                items.append({"id": doc_id, "duplicate": False})
            stored = any(not item["duplicate"] for item in items)
        return jsonify(items=items), 202
    finally:
        if not stored:
            path.unlink(missing_ok=True)


def owned_document(doc_id):
    with database(DB_PATH) as db:
        row = db.execute("SELECT * FROM documents WHERE id=? AND user_id=?", (doc_id, g.user_id)).fetchone()
    if not row:
        from flask import abort
        abort(404)
    return row


@app.get("/api/documents/<doc_id>")
def document_detail(doc_id):
    row = owned_document(doc_id)
    with database(DB_PATH) as db:
        if row["chart_id"]:
            chart = db.execute("SELECT program FROM account_charts WHERE id=? AND user_id=?", (row["chart_id"], g.user_id)).fetchone()
            profile = hydrate_profile(db, {**default_profile(chart["program"]), "chart_id": row["chart_id"]})
        else:
            profile = default_profile("custom")
    return jsonify(format_document(row, profile, detail=True))


@app.get("/api/documents/<doc_id>/source")
def document_source(doc_id):
    row = owned_document(doc_id)
    return send_file(row["path"], download_name=row["filename"], as_attachment=False)


@app.post("/api/documents/<doc_id>/retry")
def retry_document(doc_id):
    owned_document(doc_id)
    with database(DB_PATH) as db:
        changed = db.execute("UPDATE documents SET status='queued',error='',started_at=NULL,retry_after=0,auto_retries=0,fingerprint=NULL,duplicate_of=NULL WHERE id=? AND user_id=? AND status IN ('review','failed','success')", (doc_id, g.user_id)).rowcount
    if not changed:
        return jsonify(error="Yalnızca kontrol gereken veya okunamayan belgeler yeniden denenebilir."), 409
    return jsonify(ok=True)


@app.post("/api/documents/<doc_id>/switch-kind")
def switch_document_kind(doc_id):
    row = owned_document(doc_id)
    target_kind = "z-reports" if row["kind"] == "receipts" else "receipts"
    with database(DB_PATH) as db:
        db.execute("BEGIN IMMEDIATE")
        existing = db.execute("SELECT id FROM documents WHERE user_id=? AND kind=? AND digest=? AND page=?", (g.user_id, target_kind, row["digest"], row["page"])).fetchone()
        if existing:
            return jsonify(error="Bu belge hedef sekmede zaten kayıtlı."), 409
        changed = db.execute("UPDATE documents SET kind=?,direction=?,status='queued',attempts=0,result='{}',error='',started_at=NULL,retry_after=0,auto_retries=0,fingerprint=NULL,duplicate_of=NULL WHERE id=? AND user_id=? AND status NOT IN ('queued','processing')", (target_kind, 'income' if target_kind == 'z-reports' else 'expense', doc_id, g.user_id)).rowcount
        if not changed:
            return jsonify(error="Okuma tamamlandıktan sonra belge türünü değiştirebilirsiniz."), 409
    return jsonify(ok=True, kind=target_kind)


@app.get("/export/excel")
def export_excel():
    kind = request.args.get("type", "receipts")
    if kind not in KINDS:
        raise ValueError("Fiş veya Z raporu çıktısını seçin.")
    mapped = request.args.get("mapped") == "1"
    with database(DB_PATH) as db:
        profile = context_profile(db, request.args)
        if not profile:
            raise ValueError("Önce muhasebe programınızı seçin.")
        if mapped and not profile["template_name"]:
            raise ValueError("Önce programınızın Excel şablonunu eşleştirin.")
        if mapped and profile["template_kind"] != kind:
            raise ValueError("Seçili şablon bu belge türüne ait değil.")
        # A partial workbook is never silently exported while work is pending.
        pending = db.execute("SELECT COUNT(*) FROM documents WHERE user_id=? AND kind=? AND chart_id=? AND status IN ('queued','processing')", (g.user_id, kind, profile['chart_id'])).fetchone()[0]
        if pending:
            return jsonify(error="Okuma kuyruğu tamamlandıktan sonra Excel indirebilirsiniz."), 409
        rows = db.execute("SELECT * FROM documents WHERE user_id=? AND kind=? AND chart_id=? AND status='success' ORDER BY json_extract(result,'$.document_datetime'),created_at", (g.user_id, kind, profile['chart_id'])).fetchall()
    period = request.args.get("period", "")
    if period:
        if not re.fullmatch(r"20\d{2}-(?:0[1-9]|1[0-2])", period):
            raise ValueError("Dönem geçersiz.")
        rows = [row for row in rows if json.loads(row["result"]).get("document_datetime", "").startswith(period)]
    if not rows:
        raise ValueError("Excel'e aktarılabilecek, kontrolleri geçen belge yok.")
    output = export_workbook([document_dict(row, detail=True) for row in rows], profile, mapped)
    content = output.getvalue()
    now = time.time()
    with database(DB_PATH) as db:
        db.execute("INSERT INTO export_log VALUES (?,?,?,?,?,?,?,?)", (uuid.uuid4().hex, g.user_id, profile["chart_id"], profile["program"], kind, json.dumps([row["id"] for row in rows]), hashlib.sha256(content).hexdigest(), now))
        db.executemany("UPDATE documents SET export_count=export_count+1,exported_at=? WHERE id=? AND user_id=?", [(now, row["id"], g.user_id) for row in rows])
    filename = f"{profile['program']}_{'fisler' if kind == 'receipts' else 'z_raporlari'}{'_aktarim' if mapped else ''}.xlsx"
    return send_file(output, as_attachment=True, download_name=filename, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.get("/api/legacy")
def legacy_records():
    # Preserve old data without treating the previous OCR's unchecked numbers as verified.
    kind = request.args.get("kind", "receipts")
    if kind not in KINDS:
        raise ValueError("Belge türü geçersiz.")
    table = "receipts" if kind == "receipts" else "z_reports"
    with database(DB_PATH) as db:
        if not db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
            return jsonify(items=[])
        columns = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
        rows = db.execute(f"SELECT * FROM {table} WHERE user_id=? ORDER BY id DESC", (g.user_id,)).fetchall() if "user_id" in columns else []
    return jsonify(items=[dict(row) for row in rows])


@app.delete("/api/documents")
def delete_all_documents():
    """Delete all documents belonging to the current user (both kinds)."""
    with database(DB_PATH) as db:
        deleted = db.execute("DELETE FROM documents WHERE user_id=?", (g.user_id,)).rowcount
    return jsonify(deleted=deleted, message=f"{deleted} belge silindi.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", "5000")), debug=False, threaded=True)
