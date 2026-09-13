import os
import shlex
import sys
import subprocess

# Ensure gunicorn is patched before running anything
try:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    patch_script = os.path.join(script_dir, "patch_gunicorn.py")
    if os.path.exists(patch_script):
        subprocess.run([sys.executable, patch_script], check=False)
except Exception as e:
    print(f"Warning: could not run patch_gunicorn.py: {e}")

port = os.getenv("PORT", "5000")
if not port.isdigit():
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
