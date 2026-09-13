import os

port = os.getenv("PORT", "5000")
bind = f"0.0.0.0:{port}"
workers = 1
threads = 4
timeout = 120
graceful_timeout = 30
accesslog = "-"
errorlog = "-"
