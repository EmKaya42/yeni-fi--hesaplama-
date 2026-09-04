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

    paddle_err = None
    try:
        from paddleocr import PaddleOCR
        _reader = PaddleOCR(lang="tr", use_doc_orientation_classify=False, use_doc_unwarping=False, use_textline_orientation=False)
        _engine = "PaddleOCR"
        return _reader, _engine
    except Exception as err:
        paddle_err = err

    raise RuntimeError(
        f"OCR motorlari yuklenemedi. "
        f"[EasyOCR hatasi: {easy_err}] | [PaddleOCR hatasi: {paddle_err}]"
    )


def read_image(image_path: Path) -> tuple[str, str]:
    reader, engine = _get_reader()
    if engine == "EasyOCR":
        lines = reader.readtext(str(image_path), detail=0, paragraph=True)
        return "\n".join(str(line) for line in lines), engine

    try:
        result = reader.predict(str(image_path))
        lines: list[str] = []
        for item in result:
            data = item.json if hasattr(item, "json") else item
            if isinstance(data, dict):
                data = data.get("res", data)
                texts = data.get("rec_texts", [])
                lines.extend(str(text) for text in texts)
        return "\n".join(lines), engine
    except Exception:
        import easyocr
        fallback = easyocr.Reader(["tr", "en"], gpu=False, verbose=False)
        lines = fallback.readtext(str(image_path), detail=0, paragraph=True)
        return "\n".join(str(line) for line in lines), "EasyOCR"
