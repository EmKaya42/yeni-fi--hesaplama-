import os
import re
import sys
import shutil
from pathlib import Path

def patch_file(path: Path, pattern: str, replacement: str) -> bool:
    if not path.exists():
        return False
    try:
        content = path.read_text(encoding="utf-8")
        if replacement in content:
            print(f"Already patched: {path}")
            return True
        new_content, count = re.subn(pattern, replacement, content)
        if count > 0:
            path.write_text(new_content, encoding="utf-8")
            print(f"Successfully patched {count} occurrence(s) in: {path}")
            return True
        else:
            print(f"Pattern not found in: {path}")
            return False
    except Exception as e:
        print(f"Error patching {path}: {e}")
        return False

def patch_gunicorn_util():
    target_pattern = r"(try:\s*\n\s*port = int\(port\)\s*\n\s*except ValueError:)"
    replacement_code = (
        "port_str = str(port).strip()\n"
        "    if port_str in ('$PORT', '${PORT}') or not port_str.isdigit():\n"
        "        _env_p = (os.getenv('PORT') or '').strip()\n"
        "        port = int(_env_p) if _env_p.isdigit() else 5000\n"
        "    else:\n"
        "        port = int(port)\n"
        "    try:\n"
        "        pass\n"
        "    except ValueError:"
    )

    util_paths = set()

    # Strategy 1: via import
    try:
        import gunicorn
        util_paths.add(Path(gunicorn.__file__).parent / "util.py")
    except Exception:
        pass

    # Strategy 2: via sys.path
    for p in sys.path:
        util_paths.add(Path(p) / "gunicorn" / "util.py")

    found = False
    for util_path in util_paths:
        if util_path.exists():
            if patch_file(util_path, target_pattern, replacement_code):
                found = True

    return found

def patch_gunicorn_binaries():
    candidates = {
        Path(sys.prefix) / "bin" / "gunicorn",
        Path(sys.prefix) / "Scripts" / "gunicorn.exe",
        Path("/usr/local/bin/gunicorn"),
        Path("/usr/bin/gunicorn"),
        Path("/bin/gunicorn"),
        Path("/opt/venv/bin/gunicorn"),
        Path("/root/.nix-profile/bin/gunicorn"),
    }

    which_g = shutil.which("gunicorn")
    if which_g:
        candidates.add(Path(which_g))

    wrapper_code = '''#!/usr/bin/env python
import os, sys, re

raw_port = (os.getenv("PORT") or "").strip()
port = raw_port if raw_port.isdigit() else "5000"

for i, arg in enumerate(sys.argv):
    if "$PORT" in arg:
        sys.argv[i] = arg.replace("$PORT", port)
    if "${PORT}" in arg:
        sys.argv[i] = arg.replace("${PORT}", port)

from gunicorn.app.wsgiapp import run

if __name__ == "__main__":
    sys.argv[0] = re.sub(r'(-script\\.pyw|\\.exe)?$', '', sys.argv[0])
    sys.exit(run())
'''

    for target in candidates:
        if target.exists() and not target.is_dir():
            try:
                target.write_text(wrapper_code, encoding="utf-8")
                target.chmod(0o755)
                print(f"Patched executable: {target}")
            except Exception as e:
                print(f"Could not patch executable {target}: {e}")

if __name__ == "__main__":
    print("Running patch_gunicorn...")
    util_patched = patch_gunicorn_util()
    patch_gunicorn_binaries()
    print(f"Gunicorn util patched: {util_patched}")
