from __future__ import annotations

from pathlib import Path
from typing import Any
import cv2

_reader: Any = None
_engine = ""


def _get_reader() -> tuple[Any, str]:
    global _reader, _engine
    if _reader is not None:
        return _reader, _engine

    easy_err = None
    try:
        import easyocr
        # Sadece tr ve en modelleri, gpu kapali
        _reader = easyocr.Reader(["tr", "en"], gpu=False, verbose=False)
        _engine = "EasyOCR"
        return _reader, _engine
    except Exception as err:
        easy_err = err

    raise RuntimeError(f"EasyOCR yuklenemedi: {easy_err}")


def read_image(image_path: Path) -> tuple[str, str]:
    reader, engine = _get_reader()
    
    # Gorsel cok buyukse (ornegin telefondan 12MP cekildiyse), CPU ve RAM tasarrufu icin boyutlandir
    img = cv2.imread(str(image_path))
    if img is not None:
        h, w = img.shape[:2]
        max_dim = 1600
        if max(h, w) > max_dim:
            scale = max_dim / max(h, w)
            img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
        raw_results = reader.readtext(img, detail=0)
    else:
        raw_results = reader.readtext(str(image_path), detail=0)

    lines = [str(line).strip() for line in raw_results if str(line).strip()]
    return "\n".join(lines), engine
