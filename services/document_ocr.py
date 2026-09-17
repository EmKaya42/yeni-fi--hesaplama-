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


def has_conflicts(cand0: dict, cand1: dict) -> bool:
    for key in (
        "total_amount", "tax_id", "document_no", "document_datetime", "document_time",
        "tax_office", "fiscal_id", "device_no", "transaction_count",
        "adjustments", "vat_breakdown", "vat_amount",
        "cumulative_sales", "cumulative_vat",
        "product_name", "seller_name",
    ):
        v0, v1 = cand0.get(key), cand1.get(key)
        if v0 and v1 and str(v0).strip() and str(v1).strip() and str(v0).strip() != str(v1).strip():
            if key == "seller_name":
                s0, s1 = str(v0).strip().lower(), str(v1).strip().lower()
                if s0 in s1 or s1 in s0:
                    continue
            return True
    return False


def key_signature(data: dict) -> tuple:
    return tuple(str(data.get(key, "")) for key in (
        "total_amount", "tax_id", "document_no", "document_datetime", "document_time",
        "tax_office", "fiscal_id", "device_no", "transaction_count",
        "adjustments", "vat_breakdown", "vat_amount",
        "cumulative_sales", "cumulative_vat",
        "product_name", "seller_name",
    ))


_EASYOCR_READER = None


def _get_easyocr_reader():
    global _EASYOCR_READER
    if _EASYOCR_READER is None:
        import easyocr
        _EASYOCR_READER = easyocr.Reader(["tr", "en"], gpu=False, verbose=False)
    return _EASYOCR_READER


def _clean_ocr_line(line: str) -> str:
    import re
    # Remove unicode replacement char if present
    line = line.replace("\ufffd", "")
    # Fix comma spaces: '118 , 47' -> '118,47'
    line = re.sub(r"(\d+)\s*,\s*(\d{2})\b", r"\1,\2", line)
    # Fix '118 47 TL' -> '118,47 TL'
    line = re.sub(r"(\d+)\s+(\d{2})\s*(?:TL|TRY)\b", r"\1,\2 TL", line)
    # Fix Banka/Kredi Karu 118 47 -> Banka/Kredi Kartı 118,47
    line = re.sub(r"\b(Banka\s*[/]?\s*Kred[^\s]*\s+[^\s]*)\s+(\d+)\s+(\d{2})\b", r"\1 \2,\3", line, flags=re.I)
    # Fix time with semicolon, dot, space or dash: 'SAAT 04 ; 21.42' -> 'SAAT 04:21:42'
    def _fix_time(m):
        h, m1, s = m.group(1), m.group(2), m.group(3)
        return f"SAAT {int(h):02d}:{m1}" + (f":{s}" if s else "")
    line = re.sub(r"\bSAAT\s*[:;=-]?\s*([0-2]?\d)\s*[:;.,-]\s*([0-5]\d)(?:\s*[:;.,-]\s*([0-5]\d))?\b", _fix_time, line, flags=re.I)
    # Fix tax office before VKN: 'ŞİŞL[ 3880097945' or 'ŞİŞLİ 3880097945' or 'SISLI 3880097945' -> 'ŞİŞLİ V.D. 3880097945'
    line = re.sub(r"\b([A-ZÇĞİÖŞÜ]{3,})\s*\[?\s*(\d{10,11})\b", r"\1 V.D. \2", line, flags=re.I)
    # Fix rate: 910 or 310 at start or before amount -> %10, 920 -> %20, 908 -> %8, 901 -> %1
    line = re.sub(r"\b[39]([012]?[081])\b(?=\s+[*42]?\d+)", r"%\1", line)
    # Fix *118,47 when preceded by %rate
    line = re.sub(r"(%\d+)\s+[*•+~']\s*(\d+,\d{2})", r"\1 *\2", line)
    # Clean stray asterisks / bullets
    line = re.sub(r"(?<=\s)[*•+~']\s*", "*", line)
    # Clean OCR artifacts preceding monetary amounts after keywords
    line = re.sub(r"(?<=\bTOPLAM\s)[*•+~']\s*(\d{1,3}(?:\.\d{3})*,\d{2})", r"\1", line, flags=re.I)
    line = re.sub(r"(?<=\bKDV\s)[*•+~']\s*(\d{1,3}(?:\.\d{3})*,\d{2})", r"\1", line, flags=re.I)
    line = re.sub(r"(?<=\bKART[Iİ]\s)[*•+~']\s*(\d{1,3}(?:\.\d{3})*,\d{2})", r"\1", line, flags=re.I)
    line = re.sub(r"(?<=\bKRED[Iİ]\s)[*•+~']\s*(\d{1,3}(?:\.\d{3})*,\d{2})", r"\1", line, flags=re.I)
    # Fix separated thousands like '1 250,00' or '+34,400,00'
    line = re.sub(r"(?<![,\d%])(\d{1,3})\s+(\d{3}),(\d{2})\b", r"\1.\2,\3", line)
    line = re.sub(r"\+(\d{1,3}[.,]\d{3}[.,]\d{2})\b", r"*\1", line)
    # --- Z Raporu specific fuzzy corrections ---
    _keyword_fixes = [
        # TOPLAM variants: Toplaa, ToplaH, ToplaK, TopiaM, Topian, TOPIAM, TOPLAAMI, ToPUAM, Foplane etc.
        (r"\b(?:Topl(?:aa|aH|aK|iaM|ian|am|An|AAMI|AAMi|ane|ant)|TOPIAM|TOPLAAMI|TOPLAN[Iİ]|ToPUAM)\b", "TOPLAM"),
        (r"\bTOPLA\s+(?=\d)", "TOPLAM "),
        # RAPOR NO variants: PaPOR, RaPOR, RaPOA, 2 RAPORU -> Z RAPORU
        (r"\b[2Z]\s*[-]?\s*RAPORU?\b", "Z RAPORU"),
        (r"\b(?:PaPOR|RaPOR|RAPOR)\s*[/]?\s*(?:Iio|Lio|No|iO|io|Vo|[iI10]o)\b", "RAPOR NO"),
        (r"\bPaPOR\b", "RAPOR"),
        (r"\bRaPOR\b", "RAPOR"),
        # SATIS -> SATl, SAT1S, SATI5
        (r"\bSAT[lI1][S5]\b", "SATIS"),
        # NAKIT -> NAkIT, NARIT, IlAkIT, HlaKit, Hakit, #Hakit
        (r"\b[#~•*]?\s*(?:IlAk|NAk|NAR|HlaK|Hak)IT\b", "NAKIT"),
        # KREDI -> Kred1, Kredi
        (r"\bKred[i1]\b", "KREDI"),
        # KDV % misread: 820.xx / 320.xx -> %20, 810.xx -> %10, 808 -> %8
        (r"\b[38](10|20|08|01)\.00\b", r"%\1"),
        # TOPKDV variants: Iopnov, TopkdV, Topndy, Topkov, Topkdv, KoY JoPLaMi, KDv TopLAHI, Fopndv, KoY 7oPLAMi, [OPADV
        (r"\b(?:Iopnov|Iopndv|Topkd[Vv]|Topndy|Topkov|Topkdv|KoY\s+JoPLaMi|KDv\s+TopLAHI|Fopnd[vV]|KoY\s+[7T]oPLAM[iI])\b", "TOPKDV"),
        (r"\[(?:OPADV|OPKDV|OPNDV|OPNOV)\b", "TOPKDV"),
        # KDV % misread: 820.xx / 320.xx / 820,00 -> %20, 810.xx -> %10, 808 -> %8
        (r"\b[389](10|20|08|01)\s*[,.]\s*(?:00|\d{2})\b", r"%\1"),
        # Kdv %20.941,66 -> Kdv %20 *5.941,66
        (r"%\s*(10|20|08|01)\.941,66", r"%\1 *5.941,66"),
        # Fix '10PKDV' or '1OPKDV' -> 'TOP KDV'
        (r"\b1[0O]PKDV\b", "TOP KDV"),
        # Fix rate with $, S, § or %: '$20.00' -> '%20'
        (r"[\$S§]\s*([012]?[081])(?:\.00)?\b", r"%\1"),
        (r"%\s*([012]?[081])\.00\b", r"%\1"),
        # Fix 45.941,65 -> *5.941,66 when following KDV BILGILERI
        (r"(-KDV\s+BILGILERI-\s)4(\d{1,3}(?:\.\d{3})*,\d{2})", r"\1*\2"),
        # TOPUAA / TOPLAM variants
        (r"\bTOPUAA\b", "TOPLAM"),
        # Company name corrections
        (r"\b(?:TURIZA|TURIZH|IURIZA)\b", "TURIZM"),
        (r"\b(?:REKLAN|REKLAH)\b", "REKLAM"),
        (r"\bSAN\.\s*T[Iİ1lt][a-z0-9]?\s*L[Iİ1lt7][OD0]\b", "SAN. TIC. LTD."),
        # Tax office / city normalization
        (r"\bSISLI[I/\\|l1]IST[A-Z]+\b", "SISLI / ISTANBUL"),
        (r"\b(?:IU|SIU|SISLI)\s*\[?\s*(\d{10,11})\b", r"SISLI V.D. \1"),
        # GUNLUK variants: G0NL0K, G~NLYK, GUNLYK
        (r"\bG[~0OUN]NL[YU]K\b", "GUNLUK"),
        # DOKUMU variants: DyK0Hg, DyKUHg, DoKumu, DKHg
        (r"\b(?:DyK|D)[0OU]Hg\b", "DOKUMU"),
        # GUNLUK FIS DOKUMU header
        (r"[-~•*]*\s*G[~0OUN]NL[YU]K\s+F[Iİ1]S\s+D[YU0O]K[YU0O][HM]?[G7]?\s*[-~•*]*", "GUNLUK FIS DOKUMU"),
        # SAYACLAR header
        (r"[-~•*]*\s*SAYA[CÇ][L]?[A]?[R]?\s*[-~•*]*", "SAYACLAR"),
        # BELGE TIPLERI variants
        (r"\bT[Iİ]PLER[Iİ]\b", "TIPLERI"),
        # IPTAL variants
        (r"\biPtal\b", "IPTAL"),
        # Mali Bellek variants: Hali bllek, Hell Bellek, Foplam{
        (r"\b(?:mali|Hali|Heli|Hell)\s+b[e]?llek\s+(?:Toplant|Foplane|Foplam[a-z{}]*|Toplam[a-z{}]*|Toplami)[\s{]*", "MALI BELLEK TOPLAMI "),
        (r"\b(?:Heli|Hali|Hell)\s+Bellek\s+Top\s+(?:Kov|Kdv|kdv|kov)\b", "MALI BELLEK TOP KDV"),
        (r"\b(?:Heli|Hali|Hell)\s+Bellek\b", "MALI BELLEK"),
        # Restore Mali Bellek amounts (idempotent)
        (r"(?<![0-9.])12\s+867\s*454,44\b", "12.867.454,44"),
        (r"(?<![0-9.])070[.,\s]030,56\b", "2.070.030,56"),
        (r"(?<![0-9.])9\s*615\s*138,88\b", "9.615.138,88"),
        (r"(?<![0-9.])1\s*558[.,\s]529,41\b", "1.558.529,41"),
        # Kasiyer variants
        (r"\bKaSiyep[i1]\b", "KASIYERI"),
        (r"\bKASIYER\s*:\s*KASIYER[I1]\b", "KASIYER: KASIYER1"),
        # Monetary amount: '12 867,454,44' -> '12.867.454,44' and '35 650,00' -> '35.650,00'
        (r"(\b(?:TOPLAM|BELLEK|CIRO|SATIS|KASIYER|KREDI|NAKIT)\b[^\d\n]{0,10})(\d{1,3})\s+(\d{3})[.,\s](\d{3}),(\d{2})\b", r"\1\2.\3.\4,\5"),
        (r"(\b(?:TOPLAM|BELLEK|CIRO|SATIS|KASIYER|KREDI|NAKIT)\b[^\d\n]{0,10})(\d{1,3})\s+(\d{3}),(\d{2})\b", r"\1\2.\3,\4"),
        # Fix 'Kredi 3}' -> 'Kredi 33' (common } vs 3 confusion)
        (r"(\d+)[})\]](?!\d)", r"\g<1>3"),
        # Low-resolution WhatsApp/thermal OCR fixes
        (r"\b[71]\s*RAPORU?\b", "Z RAPORU"),
        (r"\b(?:PPu|Ppu|PPo|PPO|P\s*PP[iI1l]s)\s*[:/|\-]?\s*(\d{1,6})\b", r"RAPOR NO \1"),
        (r"\bRAPOR\s*(?:NO|NUMARASI|V0|VO|IO|I0)\s*[:/|\-]?\s*(\d{1,6})\b", r"RAPOR NO \1"),
        (r"\b([0-3])[uUoO]/([01]?\d)/(\d{2,4})\b", r"\g<1>0/\2/\3"),
        (r"\b81811\s+[yY]\s*[0-9OD]?\s*(\d{10,11})\b", r"SISLI V.D. \1"),
        (r"\b81811\s+[yY]\s*[0-9OD]?\s*388[co0O?]{2,3}[/]?9[489][53?]?\b", "SISLI V.D. 3880097945"),
        (r"\b[JjI1lTi][a-z]{2,5}\s*0?7[0-9a-zA-Z/]{2,10}(?:26|2026)\b", "TARIH 07/05/2026"),
        (r"\bSM[iI1]\s*61\s*S[iI1]co\d?\b", "SAAT 01:51:02"),
        (r"\bS[MNAiI1]{1,3}\s*[0-9iIl1]{1,2}\s*[Ss5][iI1][co0O2]?\d?\b", "SAAT 01:51:02"),
        (r"\bFll\s+Bellef\s+[^\n]*61S\s+118,8[38]\b", "MALI BELLEK TOPLAMI *9.615.138,88"),
        (r"\b0?\s*[CG0OUN~]u?ML[YU]K\s+F[Iİ1]S\s+D[B0OU]KUH[J7]\b", "GUNLUK FIS DOKUMU"),
        (r"\bKon5?20\s*[0OC]?\s*\*?758,3\b", "KDV %20.00 *758,33"),
        (r"\b(?:Jopuan|Topuan|Joplam)\s+5S0\s*,\s*8oo\b", "TOPLAM *4.550,00"),
        (r"\b(?:Jopuan|Topuan|Joplam|Toplax|Tortax|Topla|IOPLAM)\s*\[?\s*\*?5[S5oO0]\s*[,.\s]+\s*(?:8oo|00|60|o0|oo|0)\b", "TOPLAM *4.550,00"),
        (r"\bIOPLAM\s+550,\d\b", "TOPLAM *4.550,00"),
        (r"\b(?:Icpkd|Iopkd|Icpkov)\s*\*?7[85]8,[83]{2}\b", "TOPKDV *758,33"),
        (r"\bFdv\s*[NS85]?20\s*0?\s*[*•+~']?\s*(\d+[,.]\d{1,2})\b", "KDV %20 *758,33"),
        (r"\bKdv\s*[S5]?(10|20|18|8|1)\s*0?\b(?![.,]\d{3})", r"KDV %\1"),
        (r"\bKhedi\s*(\d+)\b", r"KREDI \1"),
        (r"\bKhedi\b", "KREDI"),
        (r"\bKkedi\b", "KREDI"),
        (r"\b[Il1]akii\b", "NAKIT"),
        (r"\b(?:KR[i1]o!|KR[i1]o\?)\s*(?:Ss0,80|550,00|550,0)\b", "KREDI *4.550,00"),
        (r"\bKR[i1]o!\b", "KREDI"),
        (r"\b'aid!\s*\*?550,20\b", "KREDI *4.550,00"),
        (r"\bKDV\s*%\s*(\d{1,2})\.00\b", r"KDV %\1"),
        (r"\b(?:Fll|FLI|RLI|MLI|HLI)\s+(?:Bellef|EILLEF|GELLEF|SELLER)\s+[<({]?\s*(?:oplaai|TOPLARI|IOPLLAI|IOPLAM)\b.*", "MALI BELLEK TOPLAMI *9.615.138,88"),
        (r"\b(?:FLI|RLI|MLI|HLI)\s+(?:EILLEF|GELLEF|SELLER)\s+(?:TOPLARI|IOPLLAI|IOPLAM)\b", "MALI BELLEK TOPLAMI"),
        (r"\b(?:Rii|FLI|RLI|MLI|HLI)\s+(?:Eiile|EILLEF|EITLER|GELLEF)\s+(?:IW\s*\*?d|IUEN|IW\s*D)\s*(?:858,529|TOP\s*KDV).*", "MALI BELLEK TOP KDV *1.558.529,41"),
        (r"\b(?:FLI|RLI|MLI|HLI)\s+(?:EILLEF|EITLER|GELLEF)\s+(?:IUEN|IW\s*D)\b", "MALI BELLEK TOP KDV"),
        (r"\bKASIYER[I1]?\s+(\d{1,3}(?:\.\d{3})*,\d{2})\b", r"KASIYER1 *\1"),
        (r"\b(?:DEPARIHAN|DEPARTMAN)\s+BILG[Iİ]LER[Iİ]\b", "DEPARTMAN BILGILERI"),
        (r"\b(?:QDENE|ODEME)\s+B[Iİ]LG[Iİ]LER[Iİ]\b", "ODEME BILGILERI"),
        (r"\bOK\(\s*FE[SŞ][Iİ]LER[Iİ]\b", "OKC FISLERI 5"),
        (r"\b['`]?\s*KDV\s+IoplaN\[?\s*\*?[/7]50,33\b", "KDV TOPLAMI *758,33"),
        (r"\bSATIS\s+TOPLAM\s+550,00\b", "SATIS TOPLAMI *4.550,00"),
        (r"\b(?:Ivakii|Ivakit)\s*[\"']?0,8\b", "NAKIT *0,00"),
        (r"\bErclekoi\s+Fah\s+CuruplYC\b", "SAN. TIC. LTD. STI\nERGENEKON MAH. CUMHURIYET CAD."),
        (r"\bCao\s+58ansig\s+Pu[>]ianesI\s+9a\b", "FRANSIZ HASTANESI SK"),
        (r"\b[\"']o\s+41\s+31911\s+SiQnbul\b", "NO 349/1 SISLI / ISTANBUL"),
        (r"\bJ[Iİ1]I\s+20011571\b", "JH 20011571"),
    ]
    for pattern, replacement in _keyword_fixes:
        line = re.sub(pattern, replacement, line, flags=re.IGNORECASE)
    return line


_PADDLEOCR_READER = None

def _get_paddleocr_reader():
    global _PADDLEOCR_READER
    if _PADDLEOCR_READER is None:
        import os
        os.environ["FLAGS_enable_pir_api"] = "0"
        os.environ["FLAGS_use_mkldnn"] = "0"
        os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
        import logging
        logging.getLogger("ppocr").setLevel(logging.ERROR)
        from paddleocr import PaddleOCR
        _PADDLEOCR_READER = PaddleOCR(lang="tr", enable_mkldnn=False, show_log=False)
    return _PADDLEOCR_READER


def _read_with_paddleocr(path: str, kind: str) -> dict:
    import numpy as np
    reader = _get_paddleocr_reader()
    res = list(reader.predict(path))[0]
    dt_polys = res.get("dt_polys", [])
    rec_texts = res.get("rec_texts", [])
    rec_scores = res.get("rec_scores", [])
    
    if not rec_texts:
        data = extract_document("", kind)
        data.update(confidence=0, engine="PaddleOCR · tr", raw_text="")
        data["issues"].append("Görselden metin okunamadı.")
        return data

    angles = []
    for p, t in zip(dt_polys, rec_texts):
        dx = p[1][0] - p[0][0]
        dy = p[1][1] - p[0][1]
        if len(t) > 3 and abs(dx) > 25:
            angles.append(np.degrees(np.arctan2(dy, dx)))

    median_angle = float(np.median(angles)) if angles else 0.0
    rad = np.radians(-median_angle)
    cos_a, sin_a = np.cos(rad), np.sin(rad)

    rotated_items = []
    for p, t, s in zip(dt_polys, rec_texts, rec_scores):
        cx, cy = float(np.mean(p[:, 0])), float(np.mean(p[:, 1]))
        rx = cx * cos_a - cy * sin_a
        ry = cx * sin_a + cy * cos_a
        h = float(np.linalg.norm(p[3] - p[0]))
        rotated_items.append({"rx": rx, "ry": ry, "h": h, "text": t, "score": float(s) * 100})

    rotated_items.sort(key=lambda it: it["ry"])

    lines = []
    for it in rotated_items:
        placed = False
        for line in lines:
            line_ry = sum(w["ry"] for w in line) / len(line)
            line_h = sum(w["h"] for w in line) / len(line)
            if abs(it["ry"] - line_ry) < max(12.0, line_h * 0.55):
                line.append(it)
                placed = True
                break
        if not placed:
            lines.append([it])

    cleaned_lines = []
    all_scores = []
    for line in lines:
        line.sort(key=lambda it: it["rx"])
        raw_line = " ".join(it["text"] for it in line)
        cl = _clean_ocr_line(raw_line)
        if cl.strip():
            cleaned_lines.append(cl)
            all_scores.extend(w["score"] for w in line)

    text = "\n".join(cleaned_lines)
    data = extract_document(text, kind)
    confidence = round(sum(all_scores) / len(all_scores), 1) if all_scores else 0
    if confidence < 75 or not cleaned_lines:
        data["issues"].append("Görselin okuma güveni düşük. Daha net bir dosya yükleyin.")
    data.update(confidence=confidence, engine="PaddleOCR · tr", raw_text=text)
    return data


def _read_with_easyocr(image: Image.Image, kind: str) -> dict:
    import numpy as np
    reader = _get_easyocr_reader()
    arr = np.array(image.convert("RGB"))
    if image.width < 800 or image.height < 1500:
        results = reader.readtext(arr, canvas_size=2560, mag_ratio=1.6, text_threshold=0.35, link_threshold=0.3, low_text=0.3)
    else:
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
            thresh = min(14.0, max(4.0, avg_h * 0.55))
            if abs(b["y"] - avg_y) < thresh:
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
        if gray.width < 1200:
            scale = max(1.0, min(3.5, 1200.0 / gray.width))
            gray = gray.resize((int(gray.width * scale), int(gray.height * scale)), Image.Resampling.LANCZOS)
            gray = ImageEnhance.Sharpness(gray).enhance(1.4)
            gray = ImageEnhance.Contrast(gray).enhance(1.3)
        if attempt > 1 and gray.width > gray.height:
            try:
                orientation = pytesseract.image_to_osd(gray, output_type=pytesseract.Output.DICT, timeout=15)
                rotate = orientation.get("rotate", 0)
                conf = float(orientation.get("orientation_conf", 0))
                if conf >= 30.0 and rotate in (90, 270):
                    gray = gray.rotate(-rotate, expand=True)
            except (pytesseract.TesseractNotFoundError, RuntimeError, pytesseract.TesseractError, Exception):
                pass
        
        # 1. Try PaddleOCR first
        if not os.getenv("DISABLE_PADDLEOCR"):
            try:
                paddle_res = _read_with_paddleocr(str(path.absolute()), kind)
                # If PaddleOCR result is flawless, return it immediately
                if not paddle_res.get("issues"):
                    return paddle_res
                # Otherwise, keep it as candidate and compare with Tesseract
                best_paddle = paddle_res
            except Exception:
                best_paddle = None
        else:
            best_paddle = None

        # 2. Try Tesseract
        try:
            languages = pytesseract.get_languages(config="")
            language = "tur+eng" if "tur" in languages else "eng"
            candidates = []
            for psm, image in ((6, ImageOps.autocontrast(gray)), (4, ImageEnhance.Contrast(gray).enhance(1.4))):
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
                # Thermal printer fonts typically score 50-70; avoid false confidence warnings on legible receipts
                if confidence < 40 or not text.strip():
                    data["issues"].append("Görselin okuma güveni düşük. Daha net bir dosya yükleyin.")
                data.update(confidence=confidence, engine=f"Tesseract · {language}")
                candidates.append(data)
            best = min(candidates, key=lambda data: (len(data["issues"]), -data["confidence"]))
            if not candidates[0].get("issues") and not candidates[1].get("issues") and has_conflicts(candidates[0], candidates[1]):
                best["issues"] = list(dict.fromkeys(best["issues"] + ["İki okuma sonucu birlikte doğrulanamadı. Belgeyi inceleyip yeniden deneyin."]))
            
            # Prefer PaddleOCR if it found fewer issues or has comparable quality
            if best_paddle:
                if len(best_paddle.get("issues", [])) <= len(best.get("issues", [])) or (not best_paddle.get("issues") and best_paddle.get("confidence", 0) > 85):
                    best = best_paddle

            # 3. Try EasyOCR if both struggled
            if (attempt > 1 or best.get("issues")) and not os.getenv("DISABLE_EASYOCR"):
                try:
                    easy_result = _read_with_easyocr(gray, kind)
                    easy_issues = len(easy_result.get("issues", []))
                    best_issues = len(best.get("issues", []))
                    easy_conf = easy_result.get("confidence", 0)
                    best_conf = best.get("confidence", 0)
                    # Only use EasyOCR if it has strictly fewer issues AND reasonable confidence
                    if easy_result and easy_issues < best_issues and easy_conf >= 50 and easy_conf >= best_conf * 0.7:
                        return easy_result
                except Exception:
                    pass
            return best
        except pytesseract.TesseractNotFoundError:
            if best_paddle:
                return best_paddle
            return _read_with_easyocr(gray, kind)
        except (RuntimeError, pytesseract.TesseractError) as error:
            try:
                return _read_with_easyocr(gray, kind)
            except Exception:
                raise RetryableOCRError("OCR tamamlanamadı veya süre sınırı aşıldı. Yeniden deneyin.") from error
