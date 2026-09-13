import os

raw_port = (os.getenv("PORT") or "").strip()
ports = set()
if raw_port.isdigit():
    ports.add(raw_port)
ports.add("5000")
ports.add("8080")

bind = [f"0.0.0.0:{p}" for p in sorted(ports)]
workers = 1
threads = 4
timeout = 120
graceful_timeout = 30
accesslog = "-"
errorlog = "-"
