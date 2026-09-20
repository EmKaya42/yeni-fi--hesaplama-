"""Download public model weights at install/build time; never send documents."""
from __future__ import annotations

import urllib.request
from pathlib import Path

from services.ocr_models import MODEL_BASE, MODELS, model_directory, valid_model, verified_model_paths


def main():
    target = model_directory()
    target.mkdir(parents=True, exist_ok=True)
    for relative, digest in MODELS.values():
        path = target / Path(relative).name
        if valid_model(path, digest):
            print(f'Verified: {path.name}', flush=True)
            continue
        partial = path.with_suffix('.onnx.part')
        print(f'Downloading: {path.name}', flush=True)
        try:
            with urllib.request.urlopen(MODEL_BASE + relative, timeout=120) as response, partial.open('wb') as output:
                while chunk := response.read(1024 * 1024):
                    output.write(chunk)
            if not valid_model(partial, digest):
                raise RuntimeError(f'Model checksum mismatch: {path.name}')
            partial.replace(path)
        finally:
            partial.unlink(missing_ok=True)
    verified_model_paths()
    print('PaddleOCR models ready. Documents are processed offline.', flush=True)


if __name__ == '__main__':
    main()
