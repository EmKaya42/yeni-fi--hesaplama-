import os

raw_port = (os.getenv("PORT") or "").strip()
port = raw_port if raw_port.isdigit() and 1 <= int(raw_port) <= 65535 else "5000"

bind = f"0.0.0.0:{port}"
workers = 1
threads = 4
timeout = 120
graceful_timeout = 30
accesslog = "-"
errorlog = "-"
