from __future__ import annotations

from pathlib import Path
from typing import Any
from PIL import Image, ImageEnhance, ImageFilter
import pytesseract


def read_image(image_path: Path) -> tuple[str, str]:
    try:
        with Image.open(image_path) as img:
            # RGB'ye donustur
            img = img.convert("L")
            # Kontrasti hafif artir (fis yazilarini belirginlestir)
            enhancer = ImageEnhance.Contrast(img)
            img = enhancer.enhance(1.8)
            
            # Tesseract OCR ile Turkce ve Ingilizce tara
            custom_config = r"--oem 3 --psm 6"
            try:
                text = pytesseract.image_to_string(img, lang="tur+eng", config=custom_config)
            except Exception:
                # Turkce dil paketi henuz yoksa eng fallback
                text = pytesseract.image_to_string(img, config=custom_config)

            lines = [line.strip() for line in text.splitlines() if line.strip()]
            return "\n".join(lines), "TesseractOCR"
    except Exception as err:
        raise RuntimeError(f"OCR tarama hatasi: {err}")
