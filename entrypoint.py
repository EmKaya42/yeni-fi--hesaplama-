import os
import shlex
import sys

port = os.getenv("PORT", "5000").strip()
if not port.isdigit() or not 1 <= int(port) <= 65535:
    port = "5000"

args = sys.argv[1:]

# If arguments were passed as a single string (e.g. from Docker/shell)
if len(args) == 1 and (" " in args[0] or "\t" in args[0]):
    args = shlex.split(args[0])

if not args:
    args = ["gunicorn", "-c", "gunicorn.conf.py", "app:app"]
else:
    for i, arg in enumerate(args):
        if "$PORT" in arg:
            args[i] = arg.replace("$PORT", port)
        if "${PORT}" in arg:
            args[i] = arg.replace("${PORT}", port)

print(f"[entrypoint] Running command: {args}", flush=True)
os.execvp(args[0], args)
