from __future__ import annotations

from pathlib import Path
from typing import Any

_reader: Any = None
_engine = ""


def _get_reader() -> tuple[Any, str]:
    global _reader, _engine
    if _reader is not None:
        return _reader, _engine

    easy_err = None
    try:
        import easyocr
        _reader = easyocr.Reader(["tr", "en"], gpu=False, verbose=False)
        _engine = "EasyOCR"
        return _reader, _engine
    except Exception as err:
        easy_err = err

    raise RuntimeError(f"EasyOCR yuklenemedi: {easy_err}")


def read_image(image_path: Path) -> tuple[str, str]:
    reader, engine = _get_reader()
    # detail=0 ile tum satirlar liste olarak gelir, newline ile birlestiriyoruz
    raw_results = reader.readtext(str(image_path), detail=0)
    lines = [str(line).strip() for line in raw_results if str(line).strip()]
    return "\n".join(lines), engine
