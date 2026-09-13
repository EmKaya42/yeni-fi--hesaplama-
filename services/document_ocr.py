from __future__ import annotations

import os
from pathlib import Path

from PIL import Image, ImageEnhance, ImageOps
import pytesseract

from services.document_extraction import extract_document


class RetryableOCRError(RuntimeError):
    """Transient engine failure, safe to retry without another upload."""

Image.MAX_IMAGE_PIXELS = 25_000_000
if os.getenv("TESSERACT_CMD"):
    pytesseract.pytesseract.tesseract_cmd = os.environ["TESSERACT_CMD"]


def inspect_file(path: Path) -> int:
    if path.suffix == ".pdf":
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


def signature(data: dict) -> tuple:
    return tuple(str(data.get(key, "")) for key in ("seller_name", "items", "document_type", "document_series", "document_no", "document_datetime", "document_time", "tax_id", "tax_office", "fiscal_id", "device_no", "transaction_count", "adjustments", "cumulative_sales", "cumulative_vat", "total_amount", "vat_amount", "vat_breakdown", "payment_entries", "bank_evidence"))


_EASYOCR_READER = None


def _get_easyocr_reader():
    global _EASYOCR_READER
    if _EASYOCR_READER is None:
        import easyocr
        _EASYOCR_READER = easyocr.Reader(["tr", "en"], gpu=False, verbose=False)
    return _EASYOCR_READER


def _clean_ocr_line(line: str) -> str:
    import re
    # Fix comma spaces: '118 , 47' -> '118,47'
    line = re.sub(r"(\d+)\s*,\s*(\d{2})\b", r"\1,\2", line)
    # Fix '118 47 TL' -> '118,47 TL'
    line = re.sub(r"(\d+)\s+(\d{2})\s*(?:TL|TRY)\b", r"\1,\2 TL", line)
    # Fix Banka/Kredi Karu 118 47 -> Banka/Kredi Kartı 118,47
    line = re.sub(r"\b(Banka\s*[/]?\s*Kred[^\s]*\s+[^\s]*)\s+(\d+)\s+(\d{2})\b", r"\1 \2,\3", line, flags=re.I)
    # Fix time with semicolon or dot: 'SAAT 04;21.42' -> 'SAAT 04:21:42'
    def _fix_time(m):
        h, m1, s = m.group(1), m.group(2), m.group(3)
        return f"SAAT {h}:{m1}" + (f":{s}" if s else "")
    line = re.sub(r"\bSAAT\s*[:;=-]?\s*([0-2]?\d)[;:.-]([0-5]\d)(?:[;:.-](\d{2}))?\b", _fix_time, line, flags=re.I)
    # Fix tax office bracket misread before VKN: 'ŞIŞL[  3880097945' -> 'ŞİŞLİ V.D. 3880097945'
    line = re.sub(r"\b([A-ZÇĞİÖŞÜ]{3,})\s*\[\s*(\d{10,11})\b", r"\1İ V.D. \2", line, flags=re.I)
    # Fix rate: 910 or 310 at start or before amount -> %10, 920 -> %20, 908 -> %8, 901 -> %1
    line = re.sub(r"\b[39]([012]?[081])\b(?=\s+[*42]?\d+)", r"%\1", line)
    # Fix *118,47 or 4118,47 when preceded by %rate
    line = re.sub(r"(%\d+)\s+[*42]?(\d+,\d{2})", r"\1 *\2", line)
    # Clean stray asterisks / bullets
    line = re.sub(r"(?<=\s)[*•]\s*", "*", line)
    # Clean OCR artifacts preceding monetary amounts after keywords
    line = re.sub(r"(?<=\bTOPLAM\s)[*•+~']\s*(\d{1,3}(?:\.\d{3})*,\d{2})", r"\1", line, flags=re.I)
    line = re.sub(r"(?<=\bKDV\s)[*•+~']\s*(\d{1,3}(?:\.\d{3})*,\d{2})", r"\1", line, flags=re.I)
    line = re.sub(r"(?<=\bKART[Iİ]\s)[*•+~']\s*(\d{1,3}(?:\.\d{3})*,\d{2})", r"\1", line, flags=re.I)
    line = re.sub(r"(?<=\bKRED[Iİ]\s)[*•+~']\s*(\d{1,3}(?:\.\d{3})*,\d{2})", r"\1", line, flags=re.I)
    # --- Z Raporu specific fuzzy corrections ---
    # Fix common keyword misreads (case-insensitive, word-boundary aware)
    _keyword_fixes = [
        # TOPLAM variants: Toplaa, ToplaH, ToplaM, TopiaM, Topian, TOPIAM, TOPLAAMI etc.
        (r"\bTopl(?:aa|aH|aK|iaM|ian|am|An|AAMI|AAMi)\b", "TOPLAM"),
        (r"\bTOPIAM\b", "TOPLAM"),
        (r"\bTOPLAAMI\b", "TOPLAMI"),
        (r"\bTOPLAN[Iİ]\b", "TOPLAMI"),
        (r"\bTOPLA\s+(?=\d)", "TOPLAM "),
        # RAPOR NO variants: PaPOR, RaPOR, RaPOA, 2 RAPORU -> Z RAPORU
        (r"\b2\s+RAPORU\b", "Z RAPORU"),
        (r"\bPaPOR\s+(?:Iio|Lio|No|iO|io)\b", "RAPOR NO"),
        (r"\bPaPOR\b", "RAPOR"),
        (r"\bRaPOR\b", "RAPOR"),
        # SATIS -> SATl, SAT1S, SATI5
        (r"\bSAT[lI1][S5]\b", "SATIS"),
        # NAKIT -> NAkIT, NARIT, IlAkIT, HlaKit
        (r"\b(?:IlAk|NAk|NAR|HlaK)IT\b", "NAKIT"),
        # KREDI -> Kred1, Kredi
        (r"\bKred[i1]\b", "KREDI"),
        # KDV % misread: 820.xx -> %20, 810.xx -> %10, 808 -> %8
        # Only replace when 8xx is NOT followed by a comma+digits (price context)
        (r"\b8(10|20|08|01)\.00\b", r"%\1"),
        # TOPKDV variants: Iopnov, TopkdV
        (r"\bIopnov\b", "TOPKDV"),
        (r"\bTopkd[Vv]\b", "TOPKDV"),
        # GUNLUK variants: G0NL0K, G~NLYK, GUNLYK
        (r"\bG[~0OUN]NL[YU]K\b", "GUNLUK"),
        # DOKUMU variants: DyK0Hg, DyKUHg, DoKumu
        (r"\bDyK[0OU]Hg\b", "DOKUMU"),
        # BELGE TIPLERI variants
        (r"\bT[Iİ]PLER[Iİ]\b", "TIPLERI"),
        # IPTAL variants
        (r"\biPtal\b", "IPTAL"),
        # Mali Bellek variants
        (r"\bmali\s+bellek\s+Toplant\b", "MALI BELLEK TOPLAMI"),
        (r"\bHeli\s+Bellek\b", "MALI BELLEK"),
        # Kasiyer variants
        (r"\bKaSiyep[i1]\b", "KASIYERI"),
        (r"\bKASIYER\s*:\s*KASIYER[I1]\b", "KASIYER: KASIYER1"),
        # Monetary amount: '12 867,454,44' -> '12.867.454,44' and '35 650,00' -> '35.650,00'
        (r"(\b(?:TOPLAM|BELLEK|CIRO|SATIS|KASIYER|KREDI|NAKIT)\b[^\d\n]{0,10})(\d{1,3})\s+(\d{3})[.,\s](\d{3}),(\d{2})\b", r"\1\2.\3.\4,\5"),
        (r"(\b(?:TOPLAM|BELLEK|CIRO|SATIS|KASIYER|KREDI|NAKIT)\b[^\d\n]{0,10})(\d{1,3})\s+(\d{3}),(\d{2})\b", r"\1\2.\3,\4"),
        # Fix 'Kredi 3}' -> 'Kredi 33' (common } vs 3 confusion)
        (r"(\d+)[})\]](?!\d)", r"\g<1>3"),
    ]
    for pattern, replacement in _keyword_fixes:
        line = re.sub(pattern, replacement, line, flags=re.IGNORECASE)
    return line



def _read_with_easyocr(image: Image.Image, kind: str) -> dict:
    import numpy as np
    reader = _get_easyocr_reader()
    arr = np.array(image.convert("RGB"))
    results = reader.readtext(arr)
    if not results:
        data = extract_document("", kind)
        data.update(confidence=0, engine="EasyOCR · tr+en", raw_text="")
        data["issues"].append("Görselden metin okunamadı.")
        return data

    boxes = []
    for bbox, text, score in results:
        cleaned_text = str(text).strip()
        if not cleaned_text:
            continue
        ys = [pt[1] for pt in bbox]
        xs = [pt[0] for pt in bbox]
        y_center = (min(ys) + max(ys)) / 2
        height = max(ys) - min(ys)
        boxes.append({
            "y": y_center,
            "x": min(xs),
            "h": height,
            "text": cleaned_text,
            "score": float(score) * 100
        })

    boxes.sort(key=lambda b: b["y"])
    grouped_lines = []
    for b in boxes:
        if not grouped_lines:
            grouped_lines.append([b])
        else:
            last_line = grouped_lines[-1]
            avg_y = sum(x["y"] for x in last_line) / len(last_line)
            avg_h = sum(x["h"] for x in last_line) / len(last_line)
            if abs(b["y"] - avg_y) < max(18, avg_h * 0.65):
                last_line.append(b)
            else:
                grouped_lines.append([b])

    cleaned_lines = []
    all_scores = []
    for line in grouped_lines:
        line.sort(key=lambda b: b["x"])
        line_str = " ".join(b["text"] for b in line)
        cleaned_lines.append(_clean_ocr_line(line_str))
        all_scores.extend(b["score"] for b in line)

    text = "\n".join(cleaned_lines)
    data = extract_document(text, kind)
    confidence = round(sum(all_scores) / len(all_scores), 1) if all_scores else 0
    if confidence < 40 or not cleaned_lines:
        data["issues"].append("Görselin okuma güveni düşük. Daha net bir dosya yükleyin.")
    data.update(confidence=confidence, engine="EasyOCR · tr+en", raw_text=text)
    return data


def read_document(path: Path, page: int, kind: str, attempt: int) -> dict:
    if path.suffix == ".pdf":
        import pypdfium2 as pdfium
        with pdfium.PdfDocument(path) as pdf:
            with pdf[page] as pdf_page:
                width, height = pdf_page.get_size()
                scale = min(3, 3000 / max(width, height))
                bitmap = pdf_page.render(scale=scale)
                source = bitmap.to_pil().copy()
                bitmap.close()
    else:
        with Image.open(path) as image:
            source = ImageOps.exif_transpose(image).convert("RGB")
    with source:
        source.thumbnail((3000, 6000))
        raw_gray = ImageOps.grayscale(source)
        gray = raw_gray
        if gray.width < 1400:
            scale = min(2, 1400 / gray.width, 6000 / gray.height)
            gray = gray.resize((int(gray.width * scale), int(gray.height * scale)), Image.Resampling.LANCZOS)
        if attempt > 1:
            try:
                orientation = pytesseract.image_to_osd(gray, output_type=pytesseract.Output.DICT, timeout=15)
                gray = gray.rotate(-orientation.get("rotate", 0), expand=True)
            except pytesseract.TesseractNotFoundError:
                pass
            except (RuntimeError, pytesseract.TesseractError):
                pass
        try:
            languages = pytesseract.get_languages(config="")
            language = "tur+eng" if "tur" in languages else "eng"
            candidates = []
            for psm, image in ((6, ImageOps.autocontrast(gray)), (4 if attempt == 1 else 11, ImageEnhance.Contrast(gray).enhance(1.6))):
                tokens = pytesseract.image_to_data(image, lang=language, config=f"--oem 3 --psm {psm}", output_type=pytesseract.Output.DICT, timeout=75)
                lines: dict[tuple, list[str]] = {}
                scores = []
                for index, word in enumerate(tokens["text"]):
                    if not word.strip():
                        continue
                    key = (tokens["block_num"][index], tokens["par_num"][index], tokens["line_num"][index])
                    lines.setdefault(key, []).append(word)
                    scores.append(float(tokens["conf"][index]))
                text = "\n".join(" ".join(words) for words in lines.values())
                data = extract_document(text, kind)
                confidence = round(sum(scores) / len(scores), 1) if scores else 0
                if confidence < 80 or (scores and sum(score < 50 for score in scores) / len(scores) > .10):
                    data["issues"].append("Görselin okuma güveni düşük. Daha net bir dosya yükleyin.")
                data.update(confidence=confidence, engine=f"Tesseract · {language}")
                candidates.append(data)
            best = min(candidates, key=lambda data: (len(data["issues"]), -data["confidence"]))
            if signature(candidates[0]) != signature(candidates[1]) or any(candidate["issues"] for candidate in candidates):
                best["issues"] = list(dict.fromkeys(best["issues"] + ["İki okuma sonucu birlikte doğrulanamadı. Belgeyi inceleyip yeniden deneyin."]))
            return best
        except pytesseract.TesseractNotFoundError:
            return _read_with_easyocr(raw_gray, kind)
        except (RuntimeError, pytesseract.TesseractError) as error:
            raise RetryableOCRError("OCR tamamlanamadı veya süre sınırı aşıldı. Yeniden deneyin.") from error
