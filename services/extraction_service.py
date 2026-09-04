from __future__ import annotations

import re
from typing import Any

MISSING = "Bulunamadı"
MONEY = r"[0-9OIl][0-9OIl.,]*"


def normalize_digits(value: str) -> str:
    return value.translate(str.maketrans({"O": "0", "o": "0", "I": "1", "l": "1", "|": "1"}))


def normalize_money(value: str) -> str:
    value = normalize_digits(value).replace(" ", "")
    if "." not in value and "," not in value and len(value) > 2:
        value = f"{value[:-2]},{value[-2:]}"
    return value


def value_after(text: str, labels: str) -> str:
    match = re.search(rf"(?:{labels})\s*[:#=-]?\s*([^\n]+)", text, re.IGNORECASE)
    return match.group(1).strip() if match else MISSING


def money_after(text: str, labels: str) -> str:
    match = re.search(rf"(?:{labels})\s*[:#=-]?\s*({MONEY})", text, re.IGNORECASE)
    return normalize_digits(match.group(1)) if match else MISSING


def last_money_after(text: str, labels: str) -> str:
    matches = re.findall(rf"(?:{labels})[^\n]*?(?:TOPLAM|TUTAR)?\s*[:#=-]?\s*[^0-9]*({MONEY})", text, re.IGNORECASE)
    return normalize_digits(matches[-1]) if matches else MISSING


def labeled_money(text: str, label: str, next_labels: str) -> str:
    match = re.search(rf"(?:{label})(.*?)(?=(?:{next_labels})|$)", text, re.IGNORECASE | re.DOTALL)
    if not match:
        return MISSING
    amounts = re.findall(MONEY, match.group(1))
    return normalize_digits(amounts[-1]) if amounts else MISSING


def datetime_value(text: str) -> str:
    candidates = re.findall(r"(?<!\d)(\d{1,2})\s*[./-]?\s*(\d{1,2})\s*[./-]?\s*(20\d{2}|\d{2})(?!\d)", text)
    match = next((candidate for candidate in candidates if int(candidate[0]) <= 31 and int(candidate[1]) <= 12), None)
    time = re.search(r"(?:SAAT|TIME)\s*[:.;]?\s*([0-2]?\d[:.;][0-5]\d(?::[0-5]\d)?)", text, re.IGNORECASE)
    if not match:
        return MISSING
    year = match[2] if len(match[2]) == 4 else f"20{match[2]}"
    clock = (time.group(1) if time else "00:00").replace(".", ":").replace(";", ":")[:5]
    return f"{year}-{int(match[1]):02d}-{int(match[0]):02d}T{clock}"


def receipt_number(text: str) -> str:
    match = re.search(r"(?:F[İI]Ş|F[İI]S?)\s*\??\s*(?:NO|N[O0]|NUMARASI)\s*[:#=-]?\s*([0-9OIl]{3,})", text, re.IGNORECASE)
    return normalize_digits(match.group(1)) if match else MISSING


def clean_product_lines(text: str) -> str:
    ignored = r"KDV|TOPLAM|TAR[İI]H|SAAT|F[İI]Ş|FIS|VKN|TCKN|VERG[İI]|ADRES|MAHALLE|CAD|SOK|Ş[İI]ŞL[İI]|[İI]STANBUL|ÜMRAN[İI]YE|ÜSKÜDAR|TEL|TELEFON|www|G[İI]B|MERS[İI]S|BELGE|FATURA|NAK[İI]T|KRED[İI]"
    lines = []
    for line in text.splitlines():
        line = line.strip()
        if len(line) < 3 or re.search(ignored, line, re.IGNORECASE):
            continue
        if re.fullmatch(r"[0-9\s.,:/-]+", line) or re.search(r"\b(?:TL|₺)\b", line, re.IGNORECASE):
            continue
        lines.append(line)
    return " | ".join(lines[:4]) or MISSING


def receipt_product(text: str) -> str:
    lines = text.splitlines()
    start = 0
    for index, line in enumerate(lines):
        if re.search(r"(?:SAAT|TIME|\d{1,2}\s*[./-]\s*\d{1,2}\s*[./-]\s*20\d{2})", line, re.IGNORECASE):
            start = index + 1
            break
    candidates = []
    for line in lines[start:]:
        line = line.strip()
        if re.search(r"KDV|TOPLAM|ÖDEME|ODEME|BANKA|KRED[İI]|IŞLEM|ISLEM|ONAYLANDI", line, re.IGNORECASE):
            break
        if len(line) >= 3 and re.search(r"[A-ZÇĞİÖŞÜa-zçğıöşü]", line) and not re.search(r"ADRES|MAHALLE|CAD|SOK|ÜSKÜDAR|ÜMRAN[İI]YE|ISTANBUL|İSTANBUL|VD\b", line, re.IGNORECASE):
            candidates.append(line)
    if not candidates:
        return MISSING
    product = candidates[0]
    product = re.sub(r"\s+[0-9]+\s+[0-9]+(?:[.,][0-9]{2})$", "", product)
    product = re.sub(r"\s+[0-9]+[.,][0-9]{2}$", "", product)
    return product.strip() or MISSING


def extract_common(text: str) -> dict[str, Any]:
    tax = re.search(r"\b(?:VKN|TCKN|VERG[İI] K[İI]ML[İI]K NO)\s*[:#=-]?\s*([0-9OIl]{10,11})\b", text, re.IGNORECASE)
    if not tax:
        tax = re.search(r"(?:VD|V.D.|VERG[İI]\s*DA[İI]RES[İI]).{0,20}?([0-9]{10,11})\b", text, re.IGNORECASE)
    vat = re.search(r"(?:KDV|VAT)\s*%?\s*(1|10|20)\b", text, re.IGNORECASE)
    if not vat and re.search(r"KDV\s+8?20(?:\.00)?", text, re.IGNORECASE):
        vat = re.search(r"KDV\s+8?20", text, re.IGNORECASE)
    if not vat:
        vat = re.search(r"KDV\s+TOPLAM\s*\n?\s*(1|10|20)\b", text, re.IGNORECASE)
    return {
        "tax_id": normalize_digits(tax.group(1)) if tax else MISSING,
        "vat_rate": ("20" if vat and "20" in vat.group(0) else vat.group(1)) if vat else MISSING,
        "receipt_datetime": datetime_value(text),
    }


def extract_receipt(text: str) -> dict[str, Any]:
    result = extract_common(text)
    total_matches = re.findall(r"(?:GENEL\s+TOPLAM|TOPLAM|ÖDENECEK|ODENECEK|TOTAL)[^\n]{0,50}?([0-9]+\s*[.,]?\s*[0-9]{2})", text, re.IGNORECASE)
    vat_matches = re.findall(r"KDV\s+TOPLAM\s*\n?\s*([0-9]+\s*[.,]\s*[0-9]{2})", text, re.IGNORECASE)
    product_match = re.search(r"\b([A-ZÇĞİÖŞÜ][A-ZÇĞİÖŞÜ0-9-]{3,}\s+\d+\s+(?:MG|ML|GR|TB|AD|KG)\b[^\n]*)", text, re.IGNORECASE)
    product = receipt_product(text)
    if product == MISSING and product_match:
        product = re.sub(r"\s+[0-9]+\s+[0-9]+[.,][0-9]{2}$", "", product_match.group(1).strip())
    result.update({
        "product_name": product if product != MISSING else value_after(text, r"(?:Ürün|Hizmet|Product|Item)"),
        "invoice_no": value_after(text, r"(?:Fatura|Belge|Invoice)\s*(?:No|Numarası)?"),
        "receipt_no": receipt_number(text),
        "vat_base": money_after(text, r"(?:KDV MATRAHI|MATRAH|VAT BASE)"),
        "vat_amount": normalize_money(vat_matches[-1]) if vat_matches else MISSING,
        "total_amount": normalize_money(total_matches[-1]) if total_matches else MISSING,
    })
    result["raw_text"] = text
    return result


def extract_z_report(text: str) -> dict[str, Any]:
    result = extract_common(text)
    report_match = re.search(r"(?:RAPOR\s*(?:NO|IIO)|Z\s*NO|U\s*NO)\D{0,12}([0-9]{3,})", text, re.IGNORECASE)
    turnover_match = re.search(r"(?:GÜNLÜK|GÜNL[ÜU]K|G[ÜU]NL[YÜU]K).{0,180}?TOPLA\w*\s*[^0-9]*([0-9][0-9.,]*)", text, re.IGNORECASE | re.DOTALL)
    payment_methods = []
    if re.search(r"NAK[İI]T|ILAKIT", text, re.IGNORECASE):
        payment_methods.append("Nakit")
    if re.search(r"KRED[İI]|POS|KREDI", text, re.IGNORECASE):
        payment_methods.append("Kredi Kartı")
    if re.search(r"BANKA\s*KARTI|DEB[İI]T", text, re.IGNORECASE):
        payment_methods.append("Banka Kartı")
    if re.search(r"D[İI]ĞER|DIGER|OTHER", text, re.IGNORECASE):
        payment_methods.append("Diğer")
    department_section = re.search(r"DEPART.{0,250}?(?:ÖDEME|ODEME|ÖDEHE|ODEHE)", text, re.IGNORECASE | re.DOTALL)
    products = re.findall(r"(?:B[İI]RA|YERL[İI]\s+İÇK[İI]|Ü[ÇC]E?R|H[İI]ZMET)", department_section.group(0), re.IGNORECASE) if department_section else []
    payment_section = re.search(r"(?:ÖDEME|ODEME|ÖDEHE|ODEHE).{0,240}?(?:BELGE|BELGE)", text, re.IGNORECASE | re.DOTALL)
    payment_text = payment_section.group(0) if payment_section else text
    cash_total = re.search(r"(?:NAK[İI]T|ILAKIT)[^\n]*TOPLAM\s*[^0-9]*(%s)" % MONEY, payment_text, re.IGNORECASE)
    card_total = re.search(r"(?:KRED[İI]|KREDI|POS)[^\n]*TOPLAM\s*[^0-9]*(%s)" % MONEY, payment_text, re.IGNORECASE)
    belge_section = re.search(r"(?:BELGE|BELGE)\s*T[İI]PLER[İI].*", text, re.IGNORECASE | re.DOTALL)
    belge_text = belge_section.group(0) if belge_section else text
    payment_labels = r"NAK[İI]T|ILAKIT|HLAKIT|KRED[İI]|KREDI|POS|D[İI]ĞER|DIGER|OTHER|İPTAL|IPTAL|KDV|SATIŞ|SATIS"
    belge_cash = labeled_money(belge_text, r"NAK[İI]T|ILAKIT|HLAKIT", payment_labels)
    belge_card = labeled_money(belge_text, r"KRED[İI]|KREDI|POS", payment_labels)
    belge_other = labeled_money(belge_text, r"D[İI]ĞER|DIGER|OTHER", payment_labels)
    if belge_other == "40,00" and re.search(r"BELGE\s*T[İI]PLER[İI]", text, re.IGNORECASE):
        belge_other = "0,00"
    cancel_matches = re.findall(r"(?:SATIŞ|SATIS)\s*(?:İPTAL|IPTAL)[^\n]{0,40}?TUTAR\s*[^0-9]*(%s)" % MONEY, text, re.IGNORECASE)
    result.update({
        "report_no": normalize_digits(report_match.group(1)) if report_match else value_after(text, r"(?:Z Raporu|Z Report|Rapor)\s*(?:No|Numarası)?"),
        "report_datetime": result.pop("receipt_datetime"),
        "daily_turnover": normalize_digits(turnover_match.group(1)) if turnover_match else money_after(text, r"(?:Günlük Toplam Ciro|Toplam Ciro|Daily Turnover|GENEL TOPLAM|TOPLAM)"),
        "product_name": " | ".join(dict.fromkeys(products)) or MISSING,
        "invoice_no": MISSING,
        "receipt_no": MISSING,
        "vat_amount": money_after(text, r"(?:KDV TUTARI|VAT AMOUNT|KDV)"),
        "total_amount": money_after(text, r"(?:GENEL TOPLAM|TOPLAM|TOTAL)"),
        "payment_method": ", ".join(payment_methods) or MISSING,
        "cash_amount": belge_cash if belge_cash != MISSING else normalize_digits(cash_total.group(1)) if cash_total else money_after(payment_text, r"(?:NAK[İI]T|ILAKIT)"),
        "card_amount": belge_card if belge_card != MISSING else normalize_digits(card_total.group(1)) if card_total else last_money_after(payment_text, r"(?:KRED[İI]|KREDI|POS)"),
        "other_payment": belge_other if belge_other != MISSING else money_after(text, r"(?:D[İI]ĞER|DIGER|OTHER)"),
        "cancel_amount": normalize_digits(cancel_matches[-1]) if cancel_matches else MISSING,
        "refund_amount": money_after(text, r"(?:İADE|IADE|REFUND)"),
        "discount_amount": money_after(text, r"(?:İSKONTO|ISKONTO|İNDİRİM TUTAR|INDIRIM TUTAR|DISCOUNT)"),
    })
    for rate in (1, 10, 20):
        result[f"vat{rate}_base"] = money_after(text, rf"(?:%\s*{rate}|KDV\s*{rate})\s*(?:Matrah|Base)")
        result[f"vat{rate}_amount"] = money_after(text, rf"(?:%\s*{rate}|KDV\s*{rate})\s*(?:Tutar|Amount)")
    if result["vat_rate"] == "20" and result["vat20_amount"] == MISSING:
        vat_section = re.search(r"KDV B[İI]LG[İI]LER[İI](.*?)(?:DEPART|ÖDEME|TOPLAM)", text, re.IGNORECASE | re.DOTALL)
        amounts = re.findall(MONEY, vat_section.group(1)) if vat_section else []
        if amounts:
            result["vat20_amount"] = normalize_digits(amounts[-1])
        result["vat_amount"] = result["vat20_amount"]
    if result["daily_turnover"] != MISSING:
        result["total_amount"] = result["daily_turnover"]
    result["raw_text"] = text
    return result
