from __future__ import annotations

import re
from typing import Any

MISSING = ""


def normalize_digits(value: str) -> str:
    if not value:
        return ""
    return value.translate(str.maketrans({"O": "0", "o": "0", "I": "1", "l": "1", "|": "1"})).strip()


def normalize_money(value: str) -> str:
    if not value:
        return ""
    cleaned = normalize_digits(value).replace(" ", "").replace("*", "").replace("+", "").replace("£", "").replace("#", "").replace("₺", "").replace("TL", "")
    # Ornek: 35.650,00 -> 35650.00 veya 145,50
    m = re.search(r"(\d{1,3}(?:\.\d{3})*|\d+)[.,](\d{2})\b", cleaned)
    if m:
        main_part = m.group(1).replace(".", "")
        return f"{main_part},{m.group(2)}"
    m2 = re.search(r"\d+", cleaned)
    return m2.group(0) if m2 else ""


def find_first(text: str, patterns: list[str]) -> str:
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE | re.MULTILINE)
        if m:
            val = m.group(1).strip()
            if val:
                return val
    return ""


def find_money(text: str, patterns: list[str]) -> str:
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE | re.MULTILINE)
        if m:
            val = normalize_money(m.group(1))
            if val:
                return val
    return ""


def extract_datetime(text: str) -> str:
    # Tarih: GG/AA/YYYY veya GG.AA.YYYY veya GG-AA-YYYY
    date_match = re.search(r"\b([0-3]?\d)[\./\-]([0-1]?\d)[\./\-](20\d{2}|\d{2})\b", text)
    if not date_match:
        return ""

    day = int(date_match.group(1))
    month = int(date_match.group(2))
    year_str = date_match.group(3)
    year = int(year_str) if len(year_str) == 4 else int(f"20{year_str}")

    if not (1 <= day <= 31 and 1 <= month <= 12):
        return ""

    # Saat: 04:21:42 veya 14:30
    time_match = re.search(r"\b([0-2]?\d)[:]([0-5]\d)(?:[:][0-5]\d)?\b", text)
    if not time_match:
        time_match = re.search(r"(?:SAAT|TIME)\s*[:\.\-]?\s*([0-2]?\d)[:\.\-]([0-5]\d)", text, re.IGNORECASE)

    clock = f"{int(time_match.group(1)):02d}:{int(time_match.group(2)):02d}" if time_match else "00:00"
    return f"{year:04d}-{month:02d}-{day:02d}T{clock}"


def extract_receipt(text: str) -> dict[str, Any]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]

    # VKN / TCKN: 10 veya 11 hane
    tax_id = find_first(text, [
        r"(?:VKN|TCKN|VERG[İI]|TC)\s*[:#=\-]?\s*([0-9]{10,11})",
        r"\b([0-9]{10,11})\b"
    ])

    # Fiş No
    receipt_no = find_first(text, [
        r"(?:D?F[İI][ŞS]|FIS|BELGE|RECEIPT)\s*(?:NO|N[O0]|NUMARASI)?\s*[:#=\-]?\s*([0-9]{3,8})",
        r"\bNO\s*[:#=\-]?\s*([0-9]{3,8})\b"
    ])

    # Fatura No
    invoice_no = find_first(text, [
        r"(?:FATURA|INVOICE)\s*(?:NO|N[O0]|NUMARASI)?\s*[:#=\-]?\s*([A-Z0-9\-]{6,16})",
    ])

    receipt_datetime = extract_datetime(text)

    # Toplam Tutar
    total_amount = find_money(text, [
        r"(?:GENEL\s+TOPLA[MN]|TOPLA[MN]|TOTAL|ÖDENECEK|ODENECEK)[^\n\d]*?(\*?[£#]?\s*\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2}))",
        r"(?:GENEL\s+TOPLA[MN]|TOPLA[MN])[^\n]*?\n[^\n\d]*?(\*?[£#]?\s*\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2}))",
        r"(?:TL|₺)\s*(\d+[\.,]\d{2})",
    ])

    # KDV Oranı
    vat_rate = find_first(text, [
        r"(?:K[OD]V|VAT)\s*[%]?\s*(20|10|1)\b",
        r"[%]\s*(20|10|1)\b",
    ])

    # KDV Tutarı
    vat_amount = find_money(text, [
        r"(?:TOPK[OD]V|K[OD]V\s*TOPLAM[Iİ]|TOPLAM\s*K[OD]V|K[OD]V\s*TUTAR[Iİ])[^\n\d]*?(\*?[£#]?\s*\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2}))",
        r"(?:K[OD]V\s*(?:%?\s*(?:20|10|1))?)[^\n]*?\n[^\n\d]*?(\*?[£#]?\s*\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2}))",
    ])

    # KDV Matrahı
    vat_base = find_money(text, [
        r"(?:MATRAH|K[OD]V\s*MATRAH[Iİ])[^\n\d]*?(\*?[£#]?\s*\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2}))",
    ])

    # Ürün / Firma Adı
    product_name = ""
    ignore_patterns = r"TOPLA[MN]|K[OD]V|TAR[İI]H|SAAT|F[İI][ŞS]|FIS|VKN|TCKN|VERG[İI]|MERS[İI]S|ADRES|TEL|CAD|SOK|MAH|İSTANBUL|ANKARA|IZMIR|FATURA|B[İI]LG[İI]|TEŞEKKÜR"
    for line in lines:
        if len(line) >= 3 and not re.search(ignore_patterns, line, re.IGNORECASE):
            if not re.fullmatch(r"[\d\s\.,\*\-\:\/]+", line):
                candidate = re.sub(r"\s+\*?\d+[\.,]\d{2}.*$", "", line).strip()
                if len(candidate) >= 3:
                    product_name = candidate
                    break

    return {
        "product_name": product_name,
        "tax_id": tax_id,
        "invoice_no": invoice_no,
        "receipt_no": receipt_no,
        "receipt_datetime": receipt_datetime,
        "vat_rate": vat_rate,
        "vat_base": vat_base,
        "vat_amount": vat_amount,
        "total_amount": total_amount,
        "raw_text": text,
    }


def extract_z_report(text: str) -> dict[str, Any]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]

    # Z Rapor No
    report_no = find_first(text, [
        r"(?:Z\s*NO|Z\s*RAPORU|[PR]APOR\s*(?:NO|110|IIO)|U\s*NO)\s*[:#=\-]?\s*([0-9]{3,8})",
        r"\bZ\s*NO\s*[:#=\-]?\s*([0-9]{3,8})\b",
        r"\bZ\s*([0-9]{3,8})\b"
    ])

    # Tarih - Saat
    report_datetime = extract_datetime(text)

    # VKN / TCKN: 10 veya 11 hane
    tax_id = find_first(text, [
        r"(?:VKN|TCKN|VERG[İI]|TC)\s*[:#=\-]?\s*([0-9]{10,11})",
        r"\b([0-9]{10,11})\b"
    ])

    # Günlük Toplam Ciro: Öncelik 'SATIŞ TOPLAMI' veya 'GÜNLÜK FİŞ DÖKÜMÜ TOPLAM'
    daily_turnover = find_money(text, [
        r"SATI[ŞS]\s*TOPLAM[Iİ][^\n\d]*?(\*?[£#]?\s*\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2}))",
        r"G[ÜU]NL[ÜU]K\s*F[İI][ŞS]\s*D[ÖO]K[ÜU]M[ÜU][^\n]*\n\s*TOPLA[MN][^\n\d]*?(\*?[£#]?\s*\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2}))",
        r"G[ÜU]NL[ÜU]K\s*TOPLAM\s*C[İI]RO[^\n\d]*?(\*?[£#]?\s*\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2}))",
        r"GENEL\s+TOPLA[MN][^\n\d]*?(\*?[£#]?\s*\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2}))",
    ])

    # İlk / Son Fiş No
    first_receipt_no = find_first(text, [
        r"(?:[İI]LK\s+F[İI][ŞS]|EK[ÜU],\s*NO|D?F[İI][ŞS]\s*NO)\s*[:#=\-]?\s*([0-9]{1,8})",
    ])
    last_receipt_no = find_first(text, [
        r"(?:SON\s+F[İI][ŞS]|HAL[İI]\s*F[İI]S\s*ADET)\s*[:#=\-]?\s*([0-9]{1,8})",
    ])

    # KDV Dağılımı (%1, %10, %20)
    vat1_base = find_money(text, [r"(?:%?\s*1\s+MATRAH|K[OD]V\s*%?1\s+MATRAH)[^\n\d]*?(\*?[£#]?\s*\d+[\.,]\d{2})"])
    vat1_amount = find_money(text, [r"(?:%?\s*1\s+TUTAR|K[OD]V\s*%?1\s+TUTAR)[^\n\d]*?(\*?[£#]?\s*\d+[\.,]\d{2})"])

    vat10_base = find_money(text, [r"(?:%?\s*10\s+MATRAH|K[OD]V\s*%?10\s+MATRAH)[^\n\d]*?(\*?[£#]?\s*\d+[\.,]\d{2})"])
    vat10_amount = find_money(text, [r"(?:%?\s*10\s+TUTAR|K[OD]V\s*%?10\s+TUTAR)[^\n\d]*?(\*?[£#]?\s*\d+[\.,]\d{2})"])

    vat20_base = find_money(text, [
        r"(?:%?\s*20\s+MATRAH|K[OD]V\s*%?20\s+MATRAH)[^\n\d]*?(\*?[£#]?\s*\d+[\.,]\d{2})",
        r"B[İI]RA\s*%20[^\n]*\n[^\n]*TOPLA[MN][^\n\d]*?(\*?[£#]?\s*\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2}))"
    ])
    vat20_amount = find_money(text, [
        r"(?:TOPK[OD]V|K[OD]V\s*TOPLAM[Iİ]|K[OD]V\s*%?20\s+TUTAR)[^\n\d]*?(\*?[£#]?\s*\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2}))",
    ])

    # Ödeme Türleri (Nakit, Kredi Kartı, Diğer)
    cash_amount = find_money(text, [
        r"[HN]AK[İI]T[^\n\d]*?(\*?[£#]?\s*\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2}))",
    ])
    card_amount = find_money(text, [
        r"KRED[İI][^\n\d]*?(\*?[£#]?\s*\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2}))",
        r"POS[^\n\d]*?(\*?[£#]?\s*\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2}))",
    ])
    other_payment = find_money(text, [
        r"D[İI][ĞG]ER[^\n\d]*?(\*?[£#]?\s*\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2}))",
    ])
    cancel_amount = find_money(text, [
        r"SAT[İI][ŞS]\s*[İI]PTAL\s*TUTAR[^\n\d]*?(\*?[£#]?\s*\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2}))",
        r"[İI]PTAL\s*TUTAR[^\n\d]*?(\*?[£#]?\s*\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2}))",
    ])
    refund_amount = find_money(text, [r"[İI]ADE[^\n\d]*?(\*?[£#]?\s*\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2}))"])
    discount_amount = find_money(text, [r"[İI]ND[İI]R[İI][KM]\s*TUTAR[^\n\d]*?(\*?[£#]?\s*\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2}))"])

    # Departman / Ürün Adları
    depts = re.findall(r"([A-ZÇĞİÖŞÜa-zçğıöşü\s]{3,25})\s*%\s*20", text)
    products = [d.strip() for d in depts if not re.search(r"K[OD]V", d, re.I)]
    product_name = " | ".join(dict.fromkeys(products)) if products else ""

    # Fatura No (Yoksa Sicil no veya Eku no)
    invoice_no = find_first(text, [
        r"(?:T[İI]C\.?S[İI]C[İI]LNO|S[İI]C[İI]L\s*NO)\s*[:#=\-]?\s*([0-9]{4,10})",
        r"(?:EK[ÜU],\s*NO|EKU\s*NO)\s*[:#=\-]?\s*([0-9]{3,8})"
    ])

    return {
        "report_no": report_no,
        "report_datetime": report_datetime,
        "daily_turnover": daily_turnover,
        "first_receipt_no": first_receipt_no,
        "last_receipt_no": last_receipt_no,
        "vat1_base": vat1_base,
        "vat1_amount": vat1_amount,
        "vat10_base": vat10_base,
        "vat10_amount": vat10_amount,
        "vat20_base": vat20_base,
        "vat20_amount": vat20_amount,
        "cash_amount": cash_amount,
        "card_amount": card_amount,
        "other_payment": other_payment,
        "cancel_amount": cancel_amount,
        "refund_amount": refund_amount,
        "discount_amount": discount_amount,
        "product_name": product_name,
        "tax_id": tax_id,
        "invoice_no": invoice_no,
        "raw_text": text,
    }
