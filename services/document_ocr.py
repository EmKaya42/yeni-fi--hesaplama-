from __future__ import annotations

import json
import os
import re
from contextlib import closing
from pathlib import Path

from PIL import Image, ImageChops, ImageEnhance, ImageFilter, ImageOps
import pytesseract

from services.document_extraction import extract_document, folded
from services.ocr_text import clean_ocr_line as _clean_ocr_line


class RetryableOCRError(RuntimeError):
    """Transient engine failure, safe to retry without another upload."""


Image.MAX_IMAGE_PIXELS = 25_000_000
if os.getenv("TESSERACT_CMD"):
    pytesseract.pytesseract.tesseract_cmd = os.environ["TESSERACT_CMD"]


def inspect_file(path: Path) -> int:
    if path.suffix.lower() == ".pdf":
        import pypdfium2 as pdfium
        try:
            with pdfium.PdfDocument(path) as pdf:
                count = len(pdf)
                if not 1 <= count <= 100:
                    raise ValueError("Bir PDF en fazla 100 sayfa içerebilir. Dosyayı bölerek ekleyin.")
                return count
        except ValueError:
            raise
        except Exception as error:
            raise ValueError("PDF açılamadı. Şifreli veya bozuk olmayan bir PDF ekleyin.") from error
    try:
        with Image.open(path) as source:
            if source.format not in {"JPEG", "PNG", "WEBP", "TIFF", "BMP"}:
                raise ValueError("Desteklenmeyen görsel biçimi.")
            if getattr(source, "n_frames", 1) != 1:
                raise ValueError("Çok sayfalı görselleri PDF olarak ekleyin.")
            source.verify()
        return 1
    except Exception as error:
        raise ValueError("Görsel açılamadı veya çok büyük. Geçerli JPG, PNG, WEBP, TIFF veya BMP ekleyin.") from error


VERIFIED_FIELDS = (
    "seller_name", "items", "document_type", "document_series", "document_no",
    "document_datetime", "document_time", "tax_id", "tax_office", "fiscal_id",
    "device_no", "transaction_count", "adjustments", "cumulative_sales",
    "cumulative_vat", "total_amount", "vat_amount", "vat_breakdown",
    "payment_entries", "bank_evidence",
)


def _canonical(value):
    if isinstance(value, str):
        return " ".join(folded(value).split())
    if isinstance(value, dict):
        return {key: _canonical(item) for key, item in sorted(value.items())}
    if isinstance(value, list):
        return [_canonical(item) for item in value]
    return value


def signature(data: dict) -> tuple:
    return tuple(json.dumps(_canonical(data.get(key)), sort_keys=True) for key in VERIFIED_FIELDS)


def has_conflicts(first: dict, second: dict) -> bool:
    # Missing values, zero, item prices, quantities and payment splits all matter.
    return signature(first) != signature(second)


def _tokens_to_text(tokens):
    lines, scores = {}, []
    positioned = {}
    for index, word in enumerate(tokens["text"]):
        if not word.strip():
            continue
        key = (tokens["block_num"][index], tokens["par_num"][index], tokens["line_num"][index])
        lines.setdefault(key, []).append(word)
        if all(field in tokens for field in ("left", "top", "width", "height")):
            height = int(tokens["height"][index])
            positioned.setdefault(key, []).append((int(tokens["top"][index]) + height / 2, int(tokens["left"][index]), height, word))
        confidence = float(tokens["conf"][index])
        if confidence >= 0:
            scores.append(confidence)
    if positioned:
        # PSM 4 can emit a right-hand amount block before the left-hand labels.
        # Restore physical rows rather than trusting OCR block traversal order.
        rows = []
        ordered_lines = sorted(positioned.values(), key=lambda row: sum(word[0] for word in row) / len(row))
        for line in ordered_lines:
            center = sum(word[0] for word in line) / len(line)
            height = sum(word[2] for word in line) / len(line)
            if rows and abs(center - sum(item[0] for item in rows[-1]) / len(rows[-1])) <= max(3, min(height, sum(item[2] for item in rows[-1]) / len(rows[-1])) * .5):
                rows[-1].extend(line)
            else:
                rows.append(line)
        text = "\n".join(" ".join(word[3] for word in sorted(row, key=lambda word: word[1])) for row in rows)
    else:
        text = "\n".join(" ".join(words) for words in lines.values())
    return text, round(sum(scores) / len(scores), 1) if scores else 0


def _remove_shadows(gray):
    """Local contrast preserves thermal text under folds and uneven lighting."""
    with gray.filter(ImageFilter.BoxBlur(max(5, gray.width // 40))) as background:
        with ImageChops.subtract(gray, background, offset=128) as contrast:
            return contrast.point(lambda value: 0 if value < 119 else 255)


def _identifier_confidence_issues(data, tokens):
    # A page average can hide one misread VKN or device serial (e.g. O versus 0).
    # Compare only exact, contiguous OCR tokens; never substitute guessed digits.
    labels = {"tax_id": "VKN / TCKN", "document_no": "Belge numarası",
              "fiscal_id": "Mali sicil numarası", "device_no": "Cihaz numarası"}
    lines = {}
    for index, word in enumerate(tokens["text"]):
        compact = re.sub(r"[^A-Z0-9]", "", folded(word))
        if compact:
            key = tuple(tokens[field][index] for field in ("block_num", "par_num", "line_num"))
            lines.setdefault(key, []).append((compact, float(tokens["conf"][index])))
    issues = []
    for field, label in labels.items():
        target = re.sub(r"[^A-Z0-9]", "", folded(str(data.get(field) or "")))
        if len(target) < 4:
            continue
        for words in lines.values():
            for start in range(len(words)):
                value, confidences = "", []
                for word, confidence in words[start:start + 8]:
                    value += word
                    confidences.append(confidence)
                    if value == target and any(0 <= score < 60 for score in confidences):
                        issues.append(f"{label} okuma güveni düşük. Kaynak belgeyi kontrol edip daha net bir görselle yeniden deneyin.")
                    if len(value) >= len(target):
                        break
    return list(dict.fromkeys(issues))


def _prepare_image(source):
    # Bound dimensions and pixel count; long Z reports can be very tall.
    gray = ImageOps.grayscale(source)
    factor = min(3.0, max(1.0, 1400 / gray.width), 3600 / gray.width,
                 8000 / gray.height, (16_000_000 / (gray.width * gray.height)) ** 0.5)
    if factor != 1:
        resized = gray.resize((max(1, int(gray.width * factor)), max(1, int(gray.height * factor))), Image.Resampling.LANCZOS)
        gray.close()
        gray = resized
    return gray


def read_document(path: Path, page: int, kind: str, attempt: int) -> dict:
    backend = os.getenv('OCR_ENGINE', 'paddle').lower()
    if backend == 'paddle':
        from services.paddle_ocr import read_document as read_paddle
        return read_paddle(path, page, kind, attempt)
    if backend == 'tesseract':
        return read_tesseract_document(path, page, kind, attempt)
    raise RuntimeError('OCR_ENGINE ayarı geçersiz. paddle veya tesseract kullanın.')


def load_source(path: Path, page: int):
    if path.suffix.lower() == ".pdf":
        import pypdfium2 as pdfium
        with pdfium.PdfDocument(path) as pdf:
            with closing(pdf[page]) as pdf_page:
                width, height = pdf_page.get_size()
                scale = min(3, 3600 / width, 8000 / height, (16_000_000 / (width * height)) ** 0.5)
                bitmap = pdf_page.render(scale=scale)
                try:
                    source = bitmap.to_pil().convert("RGB")
                finally:
                    bitmap.close()
    else:
        with Image.open(path) as image:
            with ImageOps.exif_transpose(image) as oriented:
                if oriented.mode in {'RGBA', 'LA'} or 'transparency' in oriented.info:
                    with oriented.convert('RGBA') as foreground, Image.new('RGBA', oriented.size, 'white') as background:
                        with Image.alpha_composite(background, foreground) as composite:
                            source = composite.convert('RGB')
                else:
                    source = oriented.convert("RGB")
    return source


def read_tesseract_document(path: Path, page: int, kind: str, attempt: int) -> dict:
    source = load_source(path, page)
    with source, _prepare_image(source) as gray:
        try:
            languages = pytesseract.get_languages(config="")
            if "tur" not in languages or "eng" not in languages:
                raise RuntimeError("OCR dil paketleri eksik. Sunucuya Tesseract tur ve eng paketlerini kurun.")
            oriented = gray
            if attempt > 1:
                try:
                    orientation = pytesseract.image_to_osd(gray, output_type=pytesseract.Output.DICT, timeout=15)
                    rotation = orientation.get("rotate", 0)
                    if float(orientation.get("orientation_conf", 0)) >= 5 and rotation in (90, 180, 270):
                        oriented = gray.rotate(-rotation, expand=True)
                except (RuntimeError, pytesseract.TesseractError):
                    pass
            candidates, failures, uncertain_identifiers = [], [], []
            try:
                for psm in (6, 4):
                    processed = (_remove_shadows(oriented) if psm == 6 else ImageOps.autocontrast(oriented)) if attempt > 1 else (ImageOps.autocontrast(oriented) if psm == 6 else ImageEnhance.Contrast(oriented).enhance(1.3))
                    with processed:
                        config = f"--oem 3 --psm {psm} --dpi 300"
                        if attempt > 1:
                            config += " -c thresholding_method=2"
                        try:
                            tokens = pytesseract.image_to_data(processed, lang="tur+eng", config=config,
                                                             output_type=pytesseract.Output.DICT, timeout=75)
                        except (RuntimeError, pytesseract.TesseractError) as error:
                            failures.append(error)
                            continue
                    text, confidence = _tokens_to_text(tokens)
                    data = extract_document(text, kind)
                    uncertain_identifiers.extend(_identifier_confidence_issues(data, tokens))
                    if confidence < 55 or not text.strip():
                        data["issues"].append("Görselin okuma güveni düşük. Daha net bir dosya yükleyin.")
                    data.update(confidence=confidence, engine="Tesseract · tur+eng")
                    candidates.append(data)
            finally:
                if oriented is not gray:
                    oriented.close()
            if failures:
                raise RetryableOCRError("OCR tamamlanamadı veya süre sınırı aşıldı. Yeniden deneyin.") from failures[0]
            best = min(candidates, key=lambda data: (len(data["issues"]), -data["confidence"]))
            best["issues"].extend(uncertain_identifiers)
            if has_conflicts(*candidates):
                best["issues"].append("İki okuma sonucu birlikte doğrulanamadı. Belgeyi inceleyip yeniden deneyin.")
            best["ocr_reads"] = [{"psm": psm, "confidence": data["confidence"], "raw_text": data["raw_text"]}
                                 for psm, data in zip((6, 4), candidates)]
            best["issues"] = list(dict.fromkeys(best["issues"]))
            return best
        except pytesseract.TesseractNotFoundError as error:
            raise RuntimeError("Tesseract bulunamadı. Sunucu kurulumunu veya TESSERACT_CMD ayarını kontrol edin.") from error
