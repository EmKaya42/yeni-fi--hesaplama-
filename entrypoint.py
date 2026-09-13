import os
import sys

port = os.getenv("PORT", "5000")
if not port.isdigit():
    port = "5000"

args = sys.argv[1:]
if not args:
    args = ["gunicorn", "-c", "gunicorn.conf.py", "app:app"]
else:
    for i, arg in enumerate(args):
        if "$PORT" in arg:
            args[i] = arg.replace("$PORT", port)
        if "${PORT}" in arg:
            args[i] = arg.replace("${PORT}", port)

os.execvp(args[0], args)
