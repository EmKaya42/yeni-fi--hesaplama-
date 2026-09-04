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
    cleaned = normalize_digits(value).replace(" ", "").replace("*", "").replace("₺", "").replace("TL", "")
    m = re.search(r"(\d+)[.,](\d{2})", cleaned)
    if m:
        return f"{m.group(1)},{m.group(2)}"
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
    # Tarih yakalama: GG.AA.YYYY veya GG/AA/YYYY veya GG-AA-YYYY
    date_match = re.search(r"\b([0-3]?\d)[\./\-]([0-1]?\d)[\./\-](20\d{2}|\d{2})\b", text)
    if not date_match:
        return ""

    day = int(date_match.group(1))
    month = int(date_match.group(2))
    year_str = date_match.group(3)
    year = int(year_str) if len(year_str) == 4 else int(f"20{year_str}")

    if not (1 <= day <= 31 and 1 <= month <= 12):
        return ""

    # Saat yakalama: iki nokta (:) ile ayrilmis saat araniyor
    time_match = re.search(r"\b([0-2]?\d)[:]([0-5]\d)\b", text)
    if not time_match:
        time_match = re.search(r"(?:SAAT|TIME)\s*[:\.\-]?\s*([0-2]?\d)[:\.\-]([0-5]\d)", text, re.IGNORECASE)

    clock = f"{int(time_match.group(1)):02d}:{int(time_match.group(2)):02d}" if time_match else "00:00"
    return f"{year:04d}-{month:02d}-{day:02d}T{clock}"


def extract_receipt(text: str) -> dict[str, Any]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]

    # VKN / TCKN: 10 veya 11 haneli
    tax_id = find_first(text, [
        r"(?:VKN|TCKN|VERG[İI]\s*NO|TC\s*NO)\s*[:#=\-]?\s*([0-9]{10,11})",
        r"\b([0-9]{10,11})\b"
    ])

    # Fiş No
    receipt_no = find_first(text, [
        r"(?:F[İI]Ş|FIS|BELGE|RECEIPT)\s*(?:NO|N[O0]|NUMARASI)?\s*[:#=\-]?\s*([0-9]{3,8})",
        r"\bNO\s*[:#=\-]?\s*([0-9]{3,8})\b"
    ])

    # Fatura No
    invoice_no = find_first(text, [
        r"(?:FATURA|INVOICE)\s*(?:NO|N[O0]|NUMARASI)?\s*[:#=\-]?\s*([A-Z0-9\-]{6,16})",
    ])

    # Tarih - Saat
    receipt_datetime = extract_datetime(text)

    # Toplam Tutar
    total_amount = find_money(text, [
        r"(?:GENEL\s+TOPLAM|TOPLAM|TOTAL|ÖDENECEK|ODENECEK)[^\n\d]*?(\*?\s*\d+[\.,]\d{2})",
        r"(?:GENEL\s+TOPLAM|TOPLAM|TOTAL|ÖDENECEK|ODENECEK)[^\n]*?\n[^\n\d]*?(\*?\s*\d+[\.,]\d{2})",
        r"(?:TL|₺)\s*(\d+[\.,]\d{2})",
    ])

    # KDV Oranı
    vat_rate = find_first(text, [
        r"(?:KDV|VAT)\s*[%]?\s*(20|10|1)\b",
        r"[%]\s*(20|10|1)\b",
    ])

    # KDV Tutarı
    vat_amount = find_money(text, [
        r"(?:TOPKDV|TOPLAM\s+KDV|KDV\s+TUTARI|KDV)[^\n\d]*?(\*?\s*\d+[\.,]\d{2})",
        r"(?:KDV\s*(?:%?\s*(?:20|10|1))?)[^\n]*?\n[^\n\d]*?(\*?\s*\d+[\.,]\d{2})",
    ])

    # KDV Matrahı
    vat_base = find_money(text, [
        r"(?:MATRAH|KDV\s+MATRAHI)[^\n\d]*?(\*?\s*\d+[\.,]\d{2})",
    ])

    # Ürün / Hizmet Adı
    product_name = ""
    ignore_patterns = r"TOPLAM|KDV|TARIH|TARİH|SAAT|FIS|FİŞ|VKN|TCKN|VERGI|VERGİ|MERSIS|MERSİS|ADRES|TEL|CAD|SOK|MAH|İSTANBUL|ANKARA|IZMIR|FATURA|BILGI|TEŞEKKÜR|TESIKKUR"
    for line in lines:
        if len(line) >= 3 and not re.search(ignore_patterns, line, re.IGNORECASE):
            if not re.fullmatch(r"[\d\s\.,\*\-\:\/]+", line):
                product_name = re.sub(r"\s+\*?\d+[\.,]\d{2}.*$", "", line).strip()
                if len(product_name) >= 3:
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
    # Z Rapor No
    report_no = find_first(text, [
        r"(?:Z\s*RAPORU|RAPOR|Z\s*NO|U\s*NO)\s*(?:NO|NUMARASI)?\s*[:#=\-]?\s*([0-9]{3,8})",
        r"\bZ\s*([0-9]{3,8})\b"
    ])

    # Tarih - Saat
    report_datetime = extract_datetime(text)

    # Günlük Toplam Ciro
    daily_turnover = find_money(text, [
        r"(?:GÜNLÜK\s+TOPLAM\s+C[İI]RO|TOPLAM\s+C[İI]RO|GÜNLÜK\s+C[İI]RO)[^\n\d]*?(\*?\s*\d+[\.,]\d{2})",
        r"(?:GENEL\s+TOPLAM|TOPLAM)[^\n\d]*?(\*?\s*\d+[\.,]\d{2})",
        r"(?:GÜNLÜK\s+TOPLAM|C[İI]RO)[^\n]*?\n[^\n\d]*?(\*?\s*\d+[\.,]\d{2})",
    ])

    # İlk Fiş / Son Fiş No
    first_receipt_no = find_first(text, [
        r"(?:[İI]LK\s+F[İI]Ş|[İI]LK\s+FIS)\s*(?:NO)?\s*[:#=\-]?\s*([0-9]{1,8})",
    ])
    last_receipt_no = find_first(text, [
        r"(?:SON\s+F[İI]Ş|SON\s+FIS)\s*(?:NO)?\s*[:#=\-]?\s*([0-9]{1,8})",
    ])

    # KDV Dağılımı (%1, %10, %20)
    vat1_base = find_money(text, [r"(?:%?\s*1\s+MATRAH|KDV\s*%?1\s+MATRAH)[^\n\d]*?(\*?\s*\d+[\.,]\d{2})"])
    vat1_amount = find_money(text, [r"(?:%?\s*1\s+TUTAR|KDV\s*%?1\s+TUTAR)[^\n\d]*?(\*?\s*\d+[\.,]\d{2})"])

    vat10_base = find_money(text, [r"(?:%?\s*10\s+MATRAH|KDV\s*%?10\s+MATRAH)[^\n\d]*?(\*?\s*\d+[\.,]\d{2})"])
    vat10_amount = find_money(text, [r"(?:%?\s*10\s+TUTAR|KDV\s*%?10\s+TUTAR)[^\n\d]*?(\*?\s*\d+[\.,]\d{2})"])

    vat20_base = find_money(text, [r"(?:%?\s*20\s+MATRAH|KDV\s*%?20\s+MATRAH)[^\n\d]*?(\*?\s*\d+[\.,]\d{2})"])
    vat20_amount = find_money(text, [r"(?:%?\s*20\s+TUTAR|KDV\s*%?20\s+TUTAR)[^\n\d]*?(\*?\s*\d+[\.,]\d{2})"])

    # Ödeme Türleri (Nakit, Kredi Kartı, vb.)
    cash_amount = find_money(text, [
        r"(?:NAK[İI]T|ILAKIT)[^\n\d]*?(\*?\s*\d+[\.,]\d{2})",
        r"(?:NAK[İI]T|ILAKIT)[^\n]*?\n[^\n\d]*?(\*?\s*\d+[\.,]\d{2})",
    ])
    card_amount = find_money(text, [
        r"(?:KRED[İI]\s*KARTI|KRED[İI]|POS|BANKA\s*KARTI)[^\n\d]*?(\*?\s*\d+[\.,]\d{2})",
        r"(?:KRED[İI]\s*KARTI|KRED[İI]|POS)[^\n]*?\n[^\n\d]*?(\*?\s*\d+[\.,]\d{2})",
    ])
    other_payment = find_money(text, [
        r"(?:D[İI]ĞER\s+ÖDEME|D[İI]ĞER|DIGER)[^\n\d]*?(\*?\s*\d+[\.,]\d{2})",
    ])
    cancel_amount = find_money(text, [
        r"(?:[İI]PTAL|IPTAL)[^\n\d]*?(\*?\s*\d+[\.,]\d{2})",
    ])
    refund_amount = find_money(text, [
        r"(?:[İI]ADE|IADE)[^\n\d]*?(\*?\s*\d+[\.,]\d{2})",
    ])
    discount_amount = find_money(text, [
        r"(?:[İI]SKONTO|ISKONTO|[İI]ND[İI]R[İI]M)[^\n\d]*?(\*?\s*\d+[\.,]\d{2})",
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
        "product_name": "",
        "tax_id": "",
        "invoice_no": "",
        "raw_text": text,
    }
