"""Conservative extraction: missing or conflicting values never become zero."""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

RATES = (0, 1, 8, 10, 18, 20)
MONEY = r"(?<![\d.,])(?:\d{1,3}(?:[.,]\d{3})+|\d+)[.,]\d{2}(?!\d)"


def folded(value: str) -> str:
    return unicodedata.normalize("NFKD", value.upper().replace("ı", "I")).encode("ascii", "ignore").decode()


def decimal_money(value: Any) -> Decimal:
    if value is None or isinstance(value, bool) or str(value).strip() == "":
        raise ValueError("Tutar eksik.")
    value = re.sub(r"(?:TRY|TL|₺|\s)", "", str(value).upper())
    if not re.fullmatch(r"-?\d+(?:[.,]\d+)*", value):
        raise ValueError("Tutar biçimi geçersiz.")
    if "," in value and "." in value:
        separator = "," if value.rfind(",") > value.rfind(".") else "."
        value = value.replace("." if separator == "," else ",", "").replace(separator, ".")
    elif "," in value:
        if value.count(",") > 1:
            parts = value.rsplit(",", 1)
            value = parts[0].replace(",", "") + "." + parts[1]
        else:
            value = value.replace(",", ".")
    elif value.count(".") > 1 or re.fullmatch(r"-?\d{1,3}(?:\.\d{3})+", value):
        if value.count(".") > 1 and not re.fullmatch(r"-?\d{1,3}(?:\.\d{3})+", value):
            parts = value.rsplit(".", 1)
            if len(parts[1]) == 2:
                value = parts[0].replace(".", "") + "." + parts[1]
            else:
                value = value.replace(".", "")
        else:
            value = value.replace(".", "")
    try:
        result = Decimal(value)
    except InvalidOperation as error:
        raise ValueError("Tutar biçimi geçersiz.") from error
    if not result.is_finite() or abs(result) > Decimal("99999999999.99"):
        raise ValueError("Tutar geçerli aralığın dışında.")
    return result.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def extract_datetime(text: str) -> str:
    # Try TARIH-labeled line first, then standalone date on a line, then anywhere in text
    tarih_match = re.search(r"\bTARIH\s*[:;=-]?\s*(\d{1,2})[./\- ](\d{1,2})[./\- ](20\d{2}|\d{2})\b", text, re.I)
    iso = re.search(r"\b(20\d{2})-(\d{2})-(\d{2})\b", text)
    # Standalone date: line begins with or only contains a date like '07/05/2026' or '07.05.2026'
    standalone = re.search(r"(?:^|\n)\s*(?:TARIH\s*[:;=-]?\s*)?(\d{1,2})[./](\d{1,2})[./](20\d{2})\s*(?:\n|$)", text)
    fallback = re.search(r"\b(\d{1,2})[./\-](\d{1,2})[./\-](20\d{2}|\d{2})\b", text)
    clock = re.search(r"(?:SAAT\s*[:;=-]?\s*)?\b([0-2]?\d):([0-5]\d)(?::([0-5]\d))?\b", text, re.I)
    try:
        if iso:
            year, month, day = map(int, iso.groups())
        elif tarih_match:
            day, month, year = map(int, tarih_match.groups())
            if year < 100:
                year += 2000
        elif standalone:
            day, month, year = map(int, standalone.groups())
        elif fallback:
            day, month, year = map(int, fallback.groups())
            if year < 100:
                year += 2000
        else:
            return ""
        parsed = datetime(year, month, day, int(clock[1]) if clock else 0, int(clock[2]) if clock else 0)
        return parsed.isoformat(timespec="minutes") if clock else parsed.date().isoformat()
    except ValueError:
        return ""


def label_values(lines: list[str], pattern: str) -> list[str]:
    result = []
    for index, line in enumerate(lines):
        clean_ln = line.lstrip(" -~•*#'")
        match = re.match(pattern, clean_ln)
        if not match:
            match = re.match(pattern, line)
        if not match:
            continue
        # Strip leading star (OCR asterisk before amounts like *35.650,00)
        tail = re.sub(r"\*", "", line[match.end():] if match.string == line else clean_ln[match.end():])
        values = re.findall(MONEY, tail)
        if not values and index + 1 < len(lines) and re.fullmatch(r"[\s*:=TL0-9.,+-]+", lines[index + 1]):
            values = re.findall(MONEY, re.sub(r"\*", "", lines[index + 1]))
        if values:
            try:
                result.append(str(decimal_money(values[-1])))
            except ValueError:
                pass
    return list(dict.fromkeys(result))


def extract_receipt_items(original: list[str], total: str, issues: list[str], discount=Decimal(0)) -> list[dict]:
    """Keep only priced product lines; reconcile their sum before accepting names.

    Product text is retained as printed, including Turkish letters and package
    sizes. Headers, payment lines and tax summaries cannot become descriptions.
    Wrapped names can precede a quantity line and a separate rate/amount line.
    """
    items = []
    pending = []
    pending_quantity = None
    started = False
    metadata = r"\b(?:VKN|TCKN|VERGI|TARIH|SAAT|MALI\s*SICIL|CIHAZ|MF\s*:|FIS\s*(?:NO|NUMARASI)|FATURA\s*(?:NO|NUMARASI)|BELGE\s*(?:NO|NUMARASI|SERI)|SERI\s*(?:NO|:))\b|^B\.?\s*SERI\b|\bV\.?D\.?\s*[: ]"
    excluded = r"^(?:KDV\b|%\s*\d+\s+(?:MATRAH|KDV|TUTAR)|MATRAH\b|URUN\s+ADI\b|MAL\s+CINSI\b|ACIKLAMA\b|MIKTAR\b)"
    footer = r"^(?:TOP\s*KDV|TOPLAM|GENEL\s+TOPLAM|ARA\s*TOPLAM|ODENECEK|TOTAL|NAKIT|KREDI\s*KARTI|BANKA\s*KARTI|POS\b|DIGER\s*ODEME|PARA\s*USTU|MALI\s*DEGERI|TES[E]*KKUR)"
    address = r"\b(?:MAH(?:ALLE(?:SI)?)?\.?|CAD(?:DE(?:SI)?)?\.?|SOK(?:AK)?\.?|ADRES|TEL(?:EFON)?|MERSIS|SUBE)\b|\bNO\s*:"
    quantity_pattern = rf"(?P<quantity>\d+(?:[.,]\d{{1,6}})?)\s*(?P<unit>ADET|AD|KG|GR|LT|L)?\s*[X×*]\s*(?P<unit_price>{MONEY})"

    def quantity_values(match):
        return {"quantity": str(Decimal(match["quantity"].replace(",", "."))),
                "unit": {"AD": "adet", "ADET": "adet"}.get(match["unit"], (match["unit"] or "").lower()),
                "unit_price": str(decimal_money(match["unit_price"]))}

    def clean_name(value):
        value = re.sub(r"%\s*\d{1,2}\b", "", value)
        value = re.sub(rf"\s+\d+(?:[.,]\d+)?\s*(?:ADET|AD|KG|GR|LT|L)?\s*[xX×*]\s*{MONEY}.*$", "", value, flags=re.I)
        value = re.sub(r"^\d+(?:[.,]\d+)?\s*(?:ADET|AD|KG|GR|LT|L)?\s*[xX×]\s*", "", value, flags=re.I)
        return re.sub(r"\s+", " ", value).strip(" \t*:;|–-")

    for line in original:
        normalized = folded(line)
        if re.search(metadata, normalized) or re.match(r"^MF\b|^(?:B\.?\s*|BELGE\s*)?SERI(?:SI)?(?:\s*NO)?\s*[:#=-]", normalized):
            started = True
            pending = []
            pending_quantity = None
            continue
        if not started:
            continue
        if re.search(footer, normalized):
            break
        if re.search(excluded, normalized) or re.search(address, normalized):
            pending = []
            continue
        if re.search(r"\b(?:ISKONTO|INDIRIM|IPTAL|IADE)\b", normalized):
            pending = []
            pending_quantity = None
            continue
        quantity = re.search(quantity_pattern, normalized)
        if quantity and re.search(r"%\s*$", normalized[:quantity.start()]):
            quantity = None
        if not quantity and re.search(r"\b\d+(?:[.,]\d+)?\s*(?:ADET|AD|KG|GR|LT|L)?\s*[X×]\s*\d", normalized):
            issues.append("Miktar / birim fiyat satırı tam okunamadı.")
        if quantity and re.fullmatch(quantity_pattern + r"\s*(?:TL|TRY)?", normalized):
            if pending_quantity:
                issues.append("Bir ürün için birden fazla miktar / birim fiyat satırı var.")
            pending_quantity = quantity_values(quantity)
            continue  # Quantity × unit price is not another product total.
        amounts = list(re.finditer(MONEY, line))
        if not amounts:
            name = clean_name(line)
            if re.search(r"[A-Z]{2}", folded(name)) and not re.search(r"\d{2}:\d{2}|https?://|www\.", name, re.I):
                pending.append(name)
            continue
        last = amounts[-1]
        tail = line[last.end():].strip()
        if tail and not re.fullmatch(r"(?:TL|TRY|₺)?\s*[*%\d ]*", tail, re.I):
            issues.append("Bir ürün satırının tutarı veya adı tam ayrılamadı.")
            pending = []
            continue
        name_source = line[:last.start()]
        if quantity:
            name_source = name_source[:quantity.start()] + name_source[quantity.end():]
        name = clean_name(name_source)
        # A decimal package size (e.g. SU 1,5 L) is retained. An unexplained
        # second monetary value is ambiguous, not silently discarded.
        if re.search(MONEY, name):
            issues.append("Ürün satırında birden fazla tutar var; yeniden okuma gerekli.")
        if pending:
            name = " ".join([*pending, name]).strip()
        pending = []
        if not re.search(r"[A-Z]{2}", folded(name)):
            issues.append("Bir ürünün tutarı okundu ancak adı okunamadı.")
            continue
        try:
            amount = decimal_money(last.group())
        except ValueError:
            issues.append("Bir ürünün tutarı okunamadı.")
            continue
        if amount <= 0 or re.search(r"-\s*[*₺]?\s*$", line[:last.start()]):
            issues.append("Sıfır veya negatif ürün tutarı incelenmeli.")
        rates = re.findall(r"%\s*(\d{1,2})\b", line)
        measure = quantity_values(quantity) if quantity else pending_quantity or {"quantity": "", "unit": "", "unit_price": ""}
        pending_quantity = None
        if measure["quantity"]:
            try:
                if (Decimal(measure["quantity"]) * decimal_money(measure["unit_price"])).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) != amount:
                    issues.append("Ürün miktarı × birim fiyat, kalem tutarına eşit değil.")
            except ValueError:
                issues.append("Miktar veya birim fiyat geçersiz.")
        if len(rates) != 1:
            issues.append("Bir ürünün KDV oranı tam okunamadı.")
        items.append({"name": name, "amount": str(amount), "rate": int(rates[0]) if len(rates) == 1 else None, **measure})
    if not items:
        issues.append("Ürün adı ve tutarı okunamadı. Daha net bir belgeyle yeniden deneyin.")
    elif total and sum((decimal_money(item["amount"]) for item in items), Decimal(0)) - discount != decimal_money(total):
        issues.append("Ürün satırlarının toplamı belge toplamıyla uyuşmuyor; eksik ürün olabilir.")
    return items


def receipt_description(data: dict) -> str:
    """Never use the legacy product_name field, which used to contain a seller."""
    items = data.get("items", [])
    if data.get("extraction_version", 0) < 2 or not items or any(not item.get("name", "").strip() for item in items):
        raise ValueError("Ürün adları doğrulanmadı. Fişi yeniden okutun.")
    from services.document_details import discount_amount
    if sum((decimal_money(item["amount"]) for item in items), Decimal(0)) - discount_amount(data) != decimal_money(data["total_amount"]):
        raise ValueError("Ürün toplamı belge toplamıyla uyuşmuyor. Fişi yeniden okutun.")
    description = "; ".join(dict.fromkeys(item["name"].strip() for item in items))
    if len(description) > 32767:
        raise ValueError("Ürün açıklaması Excel hücre sınırını aşıyor.")
    return description


def detect_is_z_report(plain: str, original: list[str]) -> bool:
    indicators = [
        r"\bZ\s*(?:RAPORU?|NO)\b",
        r"\b(?:GUNLUK\s+FIS\s+DOKUMU|MALI\s+BELLEK|BELGE\s+TIPLERI|SAYACLAR|KASIYER\s+BILGI|MUSTERI\s+FISI?\s+ADET|OKC\s+FISLERI|MALI\s+FIS\s+ADET)\b",
        r"\bRAPOR\s+NO\s*[:#=-]?\s*\d+\b",
        r"\bEKU\s*NO\s*[:#=-]?\s*\d+\b",
    ]
    return any(re.search(pat, plain, re.I) for pat in indicators)


def extract_seller(original):
    boundary = r"\b(?:VKN|TCKN|VERGI|TARIH|SAAT|FIS|FATURA|RAPOR|MALI|CIHAZ|TEL|ADRES|MAH|MAHALLESI|CAD|CADDE|CADDESI|SOK|SOKAK|SUBE)\b|\bV\.?D\.?\b"
    company_ext = r"\b(?:LTD|LIMITED|STI|SIRKETI|SANAYI|TICARET|ANONIM|A\.S\.|SAN|TIC|VE\s+TIC|SAR|ST[Iİ])\b"
    for index, raw in enumerate(original[:8]):
        line = folded(raw)
        if len(raw) < 3 or not re.search(r"[A-Z]{3}", line) or re.search(boundary, line):
            continue
        labeled = re.match(r"^(?:ISLETME\s+(?:ADI|UNVANI)|UNVAN|SATICI)\s*[:=-]\s*", line)
        name = raw[labeled.end():] if labeled else raw
        name = re.sub(r"^[\s\"'~•*({[]+", "", name).strip(" \t\"'~•*)}]|-")
        parts = [name]
        for continuation in original[index + 1:index + 3]:
            next_line = folded(continuation)
            if re.search(boundary, next_line) or not re.search(company_ext, next_line):
                break
            clean_cont = re.sub(r"^[\s\"'~•*({[]+", "", continuation).strip(" \t\"'~•*)}]|-")
            parts.append(clean_cont)
        return " ".join(parts)
    return ""


def extract_document(text: str, kind: str) -> dict[str, Any]:
    original = [line.strip() for line in text.splitlines() if line.strip()]
    # Apply OCR cleanup before folding AND after (catches uppercase variants post-fold)
    try:
        from services.document_ocr import _clean_ocr_line as _cl
        lines = [_cl(folded(line)) for line in original]
    except ImportError:
        lines = [folded(line) for line in original]
    plain = "\n".join(lines)
    issues: list[str] = []
    notes: list[str] = []
    is_detected_z = detect_is_z_report(plain, original)
    is_z = kind == "z-reports"

    def identifier(pattern: str) -> str:
        matches = list(dict.fromkeys(re.findall(pattern, plain, re.M)))
        if is_z and len(matches) > 1:
            suffixed = [m for m in matches if any(m != other and other.endswith(m) for other in matches)]
            full = [m for m in matches if m not in suffixed]
            if len(full) == 1:
                return full[0]
            z_bottom = re.search(r"\bZ\s*NO\s*[:#=-]?\s*(\d{1,12})\b", plain)
            if z_bottom and z_bottom.group(1) in matches:
                return z_bottom.group(1)
        if len(matches) > 1:
            issues.append("Belgede birden fazla numara veya vergi kimliği bulundu; tek belge yükleyin.")
        return matches[0] if matches else ""

    tax_id = identifier(r"(?:VKN|TCKN|VERGI\s*(?:NO|NUMARASI)|V\.?D\.?\s*(?:NO)?|TC\s*(?:NO)?)\s*[:#=-]?\s*(\d{10,11})\b")
    if not tax_id:
        for raw in original[:10]:
            for m in re.finditer(r"\b(\d{10,11})\b", raw):
                val = m.group(1)
                if not val.startswith("0") and not re.search(r"[./\-:]", raw[max(0, m.start() - 2):min(len(raw), m.end() + 2)]):
                    tax_id = val
                    break
            if tax_id:
                break
    doc_no = identifier(r"\b(?:Z\s*(?:RAPORU?)?(?:\s*(?:NO|NUMARASI))?|RAPOR\s*(?:NO|NUMARASI)?|Z\s*NO)\s*[:#=-]?\s*(\d{1,12})\b" if is_z else r"\b(?:FI[SŞ\?]?\s*(?:NO|NUMARASI)?|FATURA\s*(?:NO|NUMARASI)?|BELGE\s+(?:NO|NUMARASI))\s*[:#=-]?\s*([A-Z0-9][A-Z0-9/-]{0,29})\b")
    if is_z and not doc_no:
        z_bottom = re.search(r"\bZ\s*NO\s*[:#=-]?\s*(\d{1,12})\b", plain)
        if z_bottom:
            doc_no = z_bottom.group(1)
    if not is_z and (is_detected_z or re.search(r"\bZ\s*(?:RAPOR|NO)", plain)):
        issues.append("Bu belge Z raporu görünüyor. Z Raporları bölümüne yükleyin.")
    if is_z:
        candidate_totals = []
        candidate_totals.extend(label_values(lines, r"^(?:SATIS\s+TOPLAMI|TOPLAM\s+SATIS(?:\s+TUTARI)?|GUNLUK\s+(?:TOPLAM\s+)?CIRO|GENEL\s+TOPLAM|TOPLAM\s+CIRO)\b"))
        for idx, line in enumerate(lines):
            clean_ln = line.lstrip(" -~•*#'")
            if re.search(r"GUNLUK\s+FIS\s+DOKUMU", clean_ln):
                for ln in lines[idx + 1:idx + 5]:
                    if re.match(r"^TOPLAM\b", ln.lstrip(" -~•*#'")):
                        vals = re.findall(MONEY, re.sub(r"[*•+~']", "", ln))
                        if vals:
                            candidate_totals.append(str(decimal_money(vals[-1])))
                break
        candidate_totals.extend(label_values(lines, r"^KASIYERI?\b"))
        for idx, line in enumerate(lines):
            clean_ln = line.lstrip(" -~•*#'")
            if re.search(r"^(?:SATIS\s+TOPLAMI|KASIYERI?)\b", clean_ln):
                vals = re.findall(MONEY, re.sub(r"[*•+~']", "", line))
                if not vals and idx > 0:
                    vals = re.findall(MONEY, re.sub(r"[*•+~']", "", lines[idx - 1]))
                if not vals and idx + 1 < len(lines):
                    vals = re.findall(MONEY, re.sub(r"[*•+~']", "", lines[idx + 1]))
                if vals:
                    candidate_totals.append(str(decimal_money(vals[-1])))
        for idx, line in enumerate(lines):
            clean_ln = line.lstrip(" -~•*#'")
            if re.search(r"KDV\s+BILGILERI", clean_ln):
                for ln in lines[idx + 1:idx + 6]:
                    if re.match(r"^TOPLAM\b", ln.lstrip(" -~•*#'")):
                        vals = re.findall(MONEY, re.sub(r"[*•+~']", "", ln))
                        if vals:
                            candidate_totals.append(str(decimal_money(vals[-1])))
                break
        for idx, line in enumerate(lines):
            clean_ln = line.lstrip(" -~•*#'")
            if re.search(r"DEPARTMAN\s+BILGILERI", clean_ln):
                dept_sum = Decimal(0)
                for ln in lines[idx + 1:idx + 15]:
                    if re.search(r"^(?:ODEME|BELGE|SAYACLAR|KASIYER)", ln.lstrip(" -~•*#'")):
                        break
                    if re.match(r"^TOPLAM\b", ln.lstrip(" -~•*#'")):
                        vals = re.findall(MONEY, re.sub(r"[*•+~']", "", ln))
                        if vals:
                            dept_sum += decimal_money(vals[-1])
                if dept_sum > 0:
                    candidate_totals.append(str(dept_sum))
                break
        for idx, line in enumerate(lines):
            clean_ln = line.lstrip(" -~•*#'")
            if re.search(r"BELGE\s+TIPLERI", clean_ln):
                for ln in lines[idx + 1:idx + 15]:
                    if re.search(r"^(?:SAYACLAR|KASIYER|MALI|Z\s*RAPORU)", ln.lstrip(" -~•*#'")):
                        break
                    if re.search(r"^(?:-?\s*KREDI|-?\s*NAKIT|SATIS\s+TOPLAMI)", ln.lstrip(" -~•*#'")):
                        vals = re.findall(MONEY, re.sub(r"[*•+~']", "", ln))
                        if vals and decimal_money(vals[-1]) > 0:
                            candidate_totals.append(str(decimal_money(vals[-1])))
                break
        if candidate_totals:
            from collections import Counter
            counts = Counter(candidate_totals)
            most_common = counts.most_common(1)[0][0]
            totals = [most_common]
        else:
            totals = []
    else:
        totals = label_values(lines, r"^(?:GENEL\s+TOPLAM|ODENECEK(?:\s+TUTAR)?|TOPLAM(?:\s+TUTAR)?|TOTAL)\b(?!\s*(?:KDV|VERGI|INDIRIM|ISKONTO|IPTAL|IADE|FIS|ISLEM))")
    if len(totals) > 1:
        issues.append("Toplam tutar alanları birbiriyle çelişiyor.")
    total = totals[0] if totals else ""
    taxes = label_values(lines, r"^(?:TOPKDV|TOPLAM\s*KDV|KDV(?:\s*(?:TOPLAMI?|TUTARI?))?)\b(?!\s*%)")
    if is_z and len(taxes) > 1:
        non_zero = [t for t in taxes if decimal_money(t) > 0]
        if non_zero and len(set(non_zero)) == 1:
            taxes = [non_zero[0]]
        elif non_zero:
            from collections import Counter
            counts = Counter(non_zero)
            taxes = [counts.most_common(1)[0][0]]
    if len(set(taxes)) > 1:
        issues.append("KDV toplamları birbiriyle çelişiyor.")
    rate_matches = re.findall(r"%\s*(\d{1,2})(?:\.\d{2})?\b|\bKDV\s+(\d{1,2})(?![.,\d])\b", plain)
    rates = sorted({int(value) for match in rate_matches for value in match if value})
    if any(rate not in RATES for rate in rates):
        issues.append("KDV oranı tanınamadı.")
    breakdown = []
    for rate in rates:
        base = label_values(lines, rf"^(?:KDV\s*)?%?\s*{rate}\s+MATRAH\b")
        tax = label_values(lines, rf"^(?:KDV\s*)?%?\s*{rate}\s+(?:KDV|TUTAR|VERGI)\b")
        summary = [re.findall(MONEY, line[match.end():]) for line in lines if (match := re.match(rf"^KDV\s*%\s*{rate}\b", line))]
        gross = label_values(lines, rf"^(?:KDV\s*)?%?\s*{rate}\s+(?:SATIS|KDV\s*DAHIL)(?:\s*TUTARI)?\b")
        triple = [row for row in summary if len(row) == 3]
        if triple:
            gross = [str(decimal_money(triple[0][0]))]
            base = base or [str(decimal_money(triple[0][1]))]
            tax = tax or [str(decimal_money(triple[0][2]))]
        summary = [row for row in summary if len(row) == 2]
        if not base and summary:
            base = [str(decimal_money(summary[0][0]))]
            tax = [str(decimal_money(summary[0][1]))]
        # Z-report: match next-line TOPLAM after KDV %{rate}
        if is_z and not base and not tax:
            for idx, line in enumerate(lines):
                if re.match(rf"^KDV\s*%\s*{rate}\b", line):
                    tax_vals = re.findall(MONEY, re.sub(r"[*•+~']", "", line))
                    if tax_vals:
                        tax = [str(decimal_money(tax_vals[-1]))]
                    for next_ln in lines[idx + 1:idx + 3]:
                        if re.match(r"^TOPLAM\b", next_ln):
                            gross_vals = re.findall(MONEY, re.sub(r"[*•+~']", "", next_ln))
                            if gross_vals:
                                cand_gross = decimal_money(gross_vals[-1])
                                if total and (cand_gross > decimal_money(total) or str(cand_gross).endswith(str(decimal_money(total)))):
                                    cand_gross = decimal_money(total)
                                gross = [str(cand_gross)]
                                if tax:
                                    base = [str(decimal_money(gross[0]) - decimal_money(tax[0]))]
                            break
                    break
        if not base and tax and total and len([r for r in rates if r > 0]) == 1:
            base = [str(decimal_money(total) - decimal_money(tax[0]))]
        if not base and tax and Decimal(rate) > 0:
            calc_base = (decimal_money(tax[0]) * 100 / Decimal(rate)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if calc_base > 0:
                base = [str(calc_base)]
        if len(base) > 1 or len(tax) > 1 or len(summary) > 1 or len(triple) > 1 or len(gross) > 1:
            issues.append(f"%{rate} KDV kırılımı çelişkili.")
        if base and tax:
            if gross and decimal_money(gross[0]) != decimal_money(base[0]) + decimal_money(tax[0]):
                issues.append(f"%{rate} satış tutarı, matrah ve KDV toplamıyla uyuşmuyor.")
            breakdown.append({"rate": rate, "base": base[0], "tax": tax[0]})
    if not breakdown and total and taxes:
        tot_dec = decimal_money(total)
        tax_dec = decimal_money(taxes[0])
        base_dec = tot_dec - tax_dec
        if base_dec > 0:
            candidate_rates = [r for r in rates if r in RATES and r > 0] or [20, 10, 8, 1]
            for cand_rate in candidate_rates:
                exp_tax = (base_dec * Decimal(cand_rate) / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                if abs(exp_tax - tax_dec) <= Decimal("0.05"):
                    breakdown = [{"rate": cand_rate, "base": str(base_dec), "tax": str(tax_dec)}]
                    notes.append("Matrah, belgede okunan toplam tutardan KDV düşülerek hesaplandı.")
                    break
    explicit_base = label_values(lines, r"^(?:KDV\s*)?MATRAH\b")
    if explicit_base and len(breakdown) == 1 and decimal_money(explicit_base[0]) != decimal_money(breakdown[0]["base"]):
        issues.append("Belgedeki matrah ile hesaplanan matrah uyuşmuyor.")
    if not breakdown:
        issues.append("KDV kırılımı tam okunamadı; oran ve tutarlar birlikte gerekli.")
    elif total and sum((decimal_money(row["base"]) + decimal_money(row["tax"]) for row in breakdown), Decimal(0)) != decimal_money(total):
        # Only complain about rate count mismatch if breakdown does not match the total amount
        if len(breakdown) != len([r for r in rates if r > 0 or any(row["rate"] == 0 for row in breakdown)]):
            issues.append("KDV kırılımı tam okunamadı; oran ve tutarlar birlikte gerekli.")
    for row in breakdown:
        base, tax = decimal_money(row["base"]), decimal_money(row["tax"])
        expected = (base * Decimal(row["rate"]) / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if base < 0 or tax < 0 or abs(expected - tax) > Decimal("0.02"):
            issues.append(f"%{row['rate']} KDV oranı, matrah ve vergi tutarı uyuşmuyor.")
    if total and breakdown and sum((decimal_money(row["base"]) + decimal_money(row["tax"]) for row in breakdown), Decimal(0)) != decimal_money(total):
        issues.append("Matrah + KDV, belge toplamına eşit değil.")
    if taxes and breakdown and sum((decimal_money(row["tax"]) for row in breakdown), Decimal(0)) != decimal_money(taxes[0]):
        issues.append("KDV kırılımı, toplam KDV ile uyuşmuyor.")
    seller = extract_seller(original)
    date = extract_datetime(plain)
    date_tokens = re.findall(r"\b(?:20\d{2}-\d{2}-\d{2}|\d{1,2}[./-]\d{1,2}[./-](?:20\d{2}|\d{2}))\b", plain)
    dates = {extract_datetime(value) for value in date_tokens}
    if len(dates) > 1:
        issues.append("Belgede birden fazla tarih var. Tarih alanı ayrıca incelenmeli.")
    for label, value in (("Belge numarası", doc_no), ("Tarih", date), ("VKN / TCKN", tax_id), ("Firma adı", seller), ("Toplam tutar", total)):
        if not value:
            issues.append(f"{label} okunamadı.")
    if total and decimal_money(total) <= 0:
        issues.append("Toplam tutar sıfırdan büyük olmalı.")
    from services.banking import extract_payments
    from services.document_details import extract_details, discount_amount
    effective_kind = "z-reports" if (is_z or is_detected_z) else kind
    details = extract_details(original, effective_kind, total, issues, notes)
    if details["document_time"] and date:
        date = date[:10] + "T" + details["document_time"]
    payments, payment_issues = extract_payments(text, total, effective_kind)
    issues.extend(payment_issues)
    if re.search(r"\b(?:USD|EUR|DOLAR|EURO)\b", plain):
        issues.append("Dövizli belge otomatik TRY aktarımına uygun değil.")
    discount = discount_amount(details)
    items = [] if is_z else extract_receipt_items(original, total, issues, discount)
    if discount and not is_z:
        if len(breakdown) != 1:
            issues.append("Çok oranlı KDV içeren indirimli fişte indirimin oran bazında dağılımı incelenmeli.")
        else:
            notes.append("Ürün toplamından belgede yazan indirim düşülerek genel toplam doğrulandı.")
    if details["cumulative_vat"] and taxes and decimal_money(details["cumulative_vat"]) < decimal_money(taxes[0]):
        issues.append("Kümülatif KDV, günlük toplam KDV'den küçük.")
    if details["cumulative_vat"] and breakdown and decimal_money(details["cumulative_vat"]) < sum((decimal_money(part["tax"]) for part in breakdown), Decimal(0)):
        issues.append("Kümülatif KDV, günlük toplam KDV'den küçük.")
    if not taxes and breakdown:
        notes.append("Toplam KDV, doğrulanan oran bazındaki KDV tutarları toplanarak hesaplandı.")
    product_names = list(dict.fromkeys(item["name"] for item in items))
    if items and all(item["rate"] is not None for item in items):
        item_rates = {item["rate"] for item in items}
        if item_rates != {part["rate"] for part in breakdown} or any(
            sum((decimal_money(item["amount"]) for item in items if item["rate"] == part["rate"]), Decimal(0))
            - (discount if len(breakdown) == 1 else Decimal(0)) != decimal_money(part["base"]) + decimal_money(part["tax"]) for part in breakdown
        ):
            issues.append("Ürünlerin KDV oranlarına göre toplamları KDV kırılımıyla uyuşmuyor.")
    if len("; ".join(product_names)) > 32767:
        issues.append("Ürün açıklaması Excel hücre sınırını aşıyor.")
    series = identifier(r"^(?:B\.?\s*SERI|BELGE\s*SERI(?:SI)?|SERI(?:\s*NO)?)\s*[:#=-]\s*([A-Z0-9]{1,10})\b")
    return {"extraction_version": 4, "seller_name": seller, "product_name": "; ".join(product_names), **details,
            "items": items, "document_series": series, "document_type": "Z Raporu" if (is_z or is_detected_z or re.search(r"\bZ\s*(?:RAPOR|NO)", plain)) else "Fatura" if re.search(r"\bFATURA\b", plain) else "Yazar Kasa Fişi",
            "tax_id": tax_id, "document_no": doc_no, "document_datetime": date,
            "total_amount": total, "vat_amount": str(sum((decimal_money(row["tax"]) for row in breakdown), Decimal(0))) if breakdown else "",
            "vat_breakdown": breakdown, **payments,
            "issues": list(dict.fromkeys(issues)), "notes": notes, "raw_text": text}
