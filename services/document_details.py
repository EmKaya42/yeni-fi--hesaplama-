"""Printed fiscal details; absent optional fields remain absent, never zero."""
from __future__ import annotations

import re
from decimal import Decimal

from services.document_extraction import MONEY, decimal_money, folded

ADJUSTMENT_LABELS = {"discount": "İndirim", "cancellation": "İptal", "refund": "İade"}


def extract_details(original, kind, total, issues, notes):
    try:
        from services.document_ocr import _clean_ocr_line as _cl
        lines = [_cl(folded(line)) for line in original]
    except ImportError:
        lines = [folded(line) for line in original]
    is_z = kind == "z-reports"

    def unique(values, label):
        values = list(dict.fromkeys(values))
        if len(values) > 1:
            issues.append(f"{label} alanları çelişiyor.")
        return values[0] if values else ""

    offices = []
    for raw, line in zip(original, lines):
        prefix = re.match(r"^(?:VERGI\s+DAIRESI|V\.?\s*D\.?)(?:\s*[:=-]\s*|\s+)", line)
        suffix = re.search(r"\s+(?:VERGI\s+DAIRESI|V\.?\s*D\.?)(?=\s|:|$)", line)
        if prefix:
            value = re.split(r"\b(?:VKN|TCKN|VERGI\s*NO)\b|\d{10,11}", raw[prefix.end():], flags=re.I)[0].strip(" :;-/[]")
        elif suffix:
            value = re.sub(r"[\[\]]", "", raw[:suffix.start()]).strip(" :;-/")
        else:
            continue
        if folded(value) in {"SISL", "SISLI"}:
            value = "ŞİŞLİ"
        if re.search(r"[A-Z]{2}", folded(value)):
            offices.append(value)
    tax_office = unique(offices, "Vergi dairesi")

    def code(pattern, label):
        values = []
        for line in lines:
            match = re.match(pattern + r"\s*[:#=-]?\s*([A-Z0-9][A-Z0-9 /-]{1,39})$", line)
            if match:
                values.append(re.sub(r"\s+", "", match[1]))
        return unique(values, label)

    fiscal_id = code(r"(?:MALI\s*SICIL(?:\s*(?:NO|NUMARASI))?|MF(?:\s*NO)?|EKU\s*NO)", "Mali sicil numarası")
    device_no = code(r"(?:CIHAZ|YAZAR\s*KASA|OKC)\s*(?:SERI\s*)?(?:NO|NUMARASI)", "Cihaz numarası")
    clocks = []
    for line in lines:
        for match in re.finditer(r"(?<!\d)(\d{1,2}):([0-5]\d)(?::([0-5]\d))?(?!\d)", line):
            if int(match[1]) > 23:
                issues.append("Saat geçersiz.")
            else:
                clocks.append(f"{int(match[1]):02}:{match[2]}" + (f":{match[3]}" if match[3] else ""))
        if not clocks:
            for match in re.finditer(r"\bSAAT\s*[:;=-]?\s*([0-2]?\d)[;:.-]([0-5]\d)(?:[;:.-]([0-5]\d))?\b", line):
                if int(match[1]) <= 23:
                    clocks.append(f"{int(match[1]):02}:{match[2]}" + (f":{match[3]}" if match[3] else ""))
    document_time = unique(clocks, "Saat")

    adjustments = {}
    for key, pattern in {"discount": r"(?:TOPLAM\s+)?(?:INDIRIM|ISKONTO)(?:LER|LAR)?", "cancellation": r"(?:(?:TOPLAM|SATIS)\s+)?IPTAL(?:LER)?", "refund": r"(?:TOPLAM\s+)?IADE(?:LER)?"}.items():
        amounts, counts, present = [], [], False
        for idx, line in enumerate(lines):
            match = re.match(r"^" + pattern + r"\b", line)
            if not match:
                continue
            present = True
            tail = re.sub(r"\*", "", line[match.end():])
            money = re.findall(MONEY, tail)
            if money:
                amounts.append(str(abs(decimal_money(money[-1]))))
            # If no amount on same line, look for sub-lines like '-SATIS TOPLAMI *4.650,00'
            elif not money:
                sub_amounts = []
                for sub in lines[idx + 1:idx + 6]:
                    if re.match(r"^[-~*•]?\s*SATIS\s+TOPLAM[Iİ]?\b", sub):
                        sub_vals = re.findall(MONEY, re.sub(r"\*", "", sub))
                        if sub_vals:
                            sub_amounts.append(str(abs(decimal_money(sub_vals[-1]))))
                        break
                amounts.extend(sub_amounts)
            count = re.search(r"(?:ADE(?:T|DI)|SAYI(?:SI)?)\s*[:=]?\s*(\d+)\b(?![.,]\d)|\b(\d+)\s*ADET\b|^\s*[:=]?\s*(\d+)\s*$", tail)
            if not count and money:
                count = re.match(r"\s*[:=]?\s*(\d+)\s+(?=" + MONEY + r")", tail)
            if count:
                counts.append(int(next(value for value in count.groups() if value is not None)))
        if present:
            amount = unique(amounts, ADJUSTMENT_LABELS[key] + " tutarı")
            count = unique(counts, ADJUSTMENT_LABELS[key] + " adedi")
            adjustments[key] = {"amount": amount, "count": count if count != "" else None}
            if amount == "":
                issues.append(f"{ADJUSTMENT_LABELS[key]} alanı var ancak tutarı okunamadı.")
            if not is_z and key != "discount" and amount and decimal_money(amount) > 0:
                issues.append("İptal/iade içeren fişin net ürün ve KDV dağılımı incelenmeli.")

    counts = []
    for line in lines:
        # Match: FIS ADEDI 33 | OKC FISLERI 33 | MUSTERI FIS ADETI 33 | SATIS IPTAL 5 (not this)
        match = re.match(r"^(?:(?:(?:TOPLAM|SATIS)\s+)?(?:FIS|ISLEM|BELGE)\s*(?:ADEDI|SAYISI|SAY|ADET(?:I)?)|(?:OKC|MUSTERI)\s+(?:FIS(?:LER(?:I)?)?|ISLEM)\s*(?:ADEDI?|SAYISI?|ADETI?)?)\s*[:=]?\s*(\d+)\s*$", line)
        if match:
            counts.append(int(match[1]))
    transaction_count = unique(counts, "Fiş / işlem adedi")
    cumulative = {}
    for key, label in (("sales", "satış"), ("vat", "KDV")):
        values = []
        for line in lines:
            if not re.match(r"^(?:KUMULATIF|BIRIKMIS|KUM\.|MALI\s*BELLEK)\s*", line):
                continue
            is_tax = bool(re.search(r"\b(?:KDV|VERGI)\b", line))
            if (key == "vat") != is_tax:
                continue
            money = re.findall(MONEY, line)
            if money:
                values.append(str(decimal_money(money[-1])))
            else:
                issues.append(f"Kümülatif {label} alanı var ancak tutarı okunamadı.")
        cumulative[key] = unique(values, "Kümülatif " + label)
    if cumulative["sales"] and total and decimal_money(cumulative["sales"]) < decimal_money(total):
        issues.append("Kümülatif satış, günlük satış toplamından küçük.")

    for label, value in (("Vergi dairesi", tax_office), ("Saat", document_time)):
        if not value:
            issues.append(f"{label} okunamadı.")
    if is_z:
        if not fiscal_id and not device_no:
            issues.append("Cihaz / mali sicil numarası okunamadı.")
        if transaction_count == "":
            issues.append("Fiş / işlem adedi okunamadı.")
        elif transaction_count == 0 and total and decimal_money(total) > 0:
            issues.append("Satış toplamı varken fiş / işlem adedi sıfır olamaz.")
    return {"tax_office": tax_office, "document_time": document_time, "fiscal_id": fiscal_id,
            "device_no": device_no, "transaction_count": transaction_count if transaction_count != "" else None,
            "adjustments": adjustments, "cumulative_sales": cumulative["sales"], "cumulative_vat": cumulative["vat"]}


def discount_amount(data):
    value = data.get("adjustments", {}).get("discount", {}).get("amount", "")
    return decimal_money(value) if value != "" else Decimal(0)
