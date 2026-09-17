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
    is_z = kind == "z-reports" or any(re.search(r"\bZ\s*RAPOR", line) for line in lines)

    def unique(values, label):
        deduped = []
        folded_seen = set()
        for v in values:
            f = folded(str(v))
            if f not in folded_seen:
                folded_seen.add(f)
                deduped.append(v)
        if len(deduped) > 1:
            issues.append(f"{label} alanları çelişiyor.")
        return deduped[0] if deduped else ""

    KNOWN_DISTRICTS = {
        "SISLI": "Şişli", "KADIKOY": "Kadıköy", "BEYOGLU": "Beyoğlu", "BESIKTAS": "Beşiktaş",
        "USKUDAR": "Üsküdar", "UMRANIYE": "Ümraniye", "FATIH": "Fatih", "BAKIRKOY": "Bakırköy",
        "SARIYER": "Sarıyer", "MECIDIYEKOY": "Mecidiyeköy", "ZINCIRLIKUYU": "Zincirlikuyu",
        "MASLAK": "Maslak", "MERTER": "Merter", "GUNGOREN": "Güngören", "KARTAL": "Kartal",
        "PENDIK": "Pendik", "MALTEPE": "Maltepe", "TUZLA": "Tuzla", "BEYKOZ": "Beykoz",
        "AVCILAR": "Avcılar", "BUYUKCEKMECE": "Büyükçekmece", "KUCUKCEKMECE": "Küçükçekmece",
        "BAHCELIEVLER": "Bahçelievler", "BAGCILAR": "Bağcılar", "ESENLER": "Esenler",
        "BAYRAMPASA": "Bayrampaşa", "GAZIOSMANPASA": "Gaziosmanpaşa", "EYUPSULTAN": "Eyüpsultan",
        "CANKAYA": "Çankaya", "YENIMAHALLE": "Yenimahalle", "KIZILAY": "Kızılay", "ULUS": "Ulus",
        "KONAK": "Konak", "KARSIYAKA": "Karşıyaka", "BORNOVA": "Bornova", "CIGLI": "Çiğli",
        "NILUFER": "Nilüfer", "OSMANGAZI": "Osmangazi", "MURATPASA": "Muratpaşa", "SEYHAN": "Seyhan",
        "MERAM": "Meram", "SELCUKLU": "Selçuklu", "SAHINBEY": "Şahinbey", "SEHITKAMIL": "Şehitkamil",
        "KOCASINAN": "Kocasinan", "MELIKGAZI": "Melikgazi", "IZMIT": "İzmit", "GEBZE": "Gebze",
    }

    offices = []
    for raw, line in zip(original, lines):
        prefix = re.match(r"^(?:VERGI\s+DAIRESI|V\.?\s*D\.?)(?:\s*[:=-]\s*|\s+)", line)
        suffix = re.search(r"\s+(?:VERGI\s+DAIRESI|V\.?\s*D\.?)(?=\s|:|$)", line)
        if prefix:
            value = re.split(r"\b(?:VKN|TCKN|VERGI\s*NO)\b|\d{10,11}", raw[prefix.end():], flags=re.I)[0].strip(" :;-/[]")
        elif suffix:
            value = re.sub(r"[\[\]]", "", raw[:suffix.start()]).strip(" :;-/")
        else:
            # Match district word immediately preceding VKN: 'ŞİŞLİ 3880097945' or 'ŞİŞLİ V.D. 3880097945'
            vkn_match = re.search(r"\b([A-Za-zÇçĞğİıÖöŞşÜü]{3,})\s*(?:V\.?D\.?)?\s*(\d{10,11})\b", raw)
            if vkn_match:
                candidate = vkn_match.group(1).strip(" :;-/[]")
                if folded(candidate) not in {"VERGI", "DAIRESI", "TARIH", "FATURA", "BELGE", "RAPOR", "MUSTERI", "SATIS"}:
                    value = candidate
                else:
                    continue
            else:
                continue
        f_val = folded(value)
        if f_val in {"SISL", "SISLI", "SISLIF", "SIU", "IU", "SISU"} or (f_val.startswith("SIS") and len(f_val) <= 6):
            value = "Şişli"
        if re.search(r"[A-Z]{2}", folded(value)):
            offices.append(value)

    # Inspect header lines (first 8 lines) for known districts only if not already found
    if not offices:
        for raw, line in zip(original[:8], lines[:8]):
            for key, name in KNOWN_DISTRICTS.items():
                if re.search(r"\b" + key + r"\b", line):
                    offices.append(name)
                    break

    tax_office = unique(offices, "Vergi dairesi")

    def code(pattern, label):
        values = []
        for line in lines:
            match = re.match(pattern + r"\s*[:#=-]?\s*([A-Z0-9][A-Z0-9 /-]{1,39})$", line)
            if match:
                values.append(re.sub(r"\s+", "", match[1]))
        return unique(values, label)

    fiscal_id = code(r"(?:MALI\s*SICIL(?:\s*(?:NO|NUMARASI))?|MF(?:\s*NO)?|EKU\s*NO)", "Mali sicil numarası")
    if not fiscal_id:
        for line in lines[-10:]:
            jh_match = re.search(r"\b([A-Z]{2})\s*(\d{8,10})\b", line)
            if jh_match and jh_match.group(1) in {"JH", "BE", "PA", "AC", "EK", "MF"}:
                fiscal_id = jh_match.group(1) + jh_match.group(2)
                break
    device_no = code(r"(?:CIHAZ|YAZAR\s*KASA|OKC)\s*(?:SERI\s*)?(?:NO|NUMARASI)", "Cihaz numarası")
    clocks = []
    saat_kw = r"(?:SAAT|SA[Iİ1]|SAT|SMT|S4AT|SA\s*AT)"
    for line in lines:
        # SAAT-labeled match (strongest signal)
        saat_match = re.search(r"\b" + saat_kw + r"\s*[:;=-]?\s*([0-2]?\d)\s*[:;.,\- ]\s*([0-5]\d)(?:\s*[:;.,\- ]\s*([0-5]\d))?\b", line)
        if saat_match and int(saat_match[1]) <= 23:
            clocks.append(f"{int(saat_match[1]):02}:{saat_match[2]}" + (f":{saat_match[3]}" if saat_match[3] else ""))
            continue
        # Continuous digits after SAAT e.g. 'SAAT 015102'
        cont_match = re.search(r"\b" + saat_kw + r"\s*[:;=-]?\s*([0-2]\d)([0-5]\d)([0-5]\d)\b", line)
        if cont_match and int(cont_match[1]) <= 23:
            clocks.append(f"{int(cont_match[1]):02}:{cont_match[2]}:{cont_match[3]}")
            continue
        if not clocks:
            for match in re.finditer(r"(?<![\d/.])([0-2]?\d)\s*[:;.]\s*([0-5]\d)(?:\s*[:;.]\s*([0-5]\d))?(?![\d/.])", line):
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
            elif not money:
                # Look ahead up to 6 lines for an amount (some formats have TUTAR on next line)
                sub_amounts = []
                for sub in lines[idx + 1:idx + 7]:
                    # Stop at next section header
                    if re.match(r"^(?:BELGE|SAYACLAR|KASIYER|ODEME|TOPLAM|DEPARTMAN|MALI|Z\s*RAPORU)", sub):
                        break
                    # Explicit TUTAR sub-line
                    if re.match(r"^(?:TUTAR|INDIRIM\s+TUTAR|ISKONTO\s+TUTAR)\b", sub):
                        sub_vals = re.findall(MONEY, re.sub(r"\*", "", sub))
                        if sub_vals:
                            sub_amounts.append(str(abs(decimal_money(sub_vals[-1]))))
                        break
                    # '-SATIS TOPLAMI *amount' pattern
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
                if not (is_z and key == "discount"):
                    issues.append(f"{ADJUSTMENT_LABELS[key]} alanı var ancak tutarı okunamadı.")
            if not is_z and key != "discount" and amount and decimal_money(amount) > 0:
                issues.append("İptal/iade içeren fişin net ürün ve KDV dağılımı incelenmeli.")

    counts = []
    # Priority 1: Customer / OKC sales receipt count
    for idx, line in enumerate(lines):
        clean_ln = line.lstrip(" -~•*#'")
        match = re.match(r"^(?:(?:(?:TOPLAM|SATIS)\s+)?(?:FIS|ISLEM|BELGE)\s*(?:ADEDI|SAYISI|SAY|ADET(?:I)?)|(?:OKC|MUSTERI)\s+(?:FIS(?:LER(?:I)?)?|ISLEM)\s*(?:ADEDI?|SAYISI?|ADETI?)?)\s*[:=]?\s*(\d+)?\s*$", clean_ln)
        if match:
            if match.group(1):
                counts.append(int(match.group(1)))
            elif idx > 0 and re.fullmatch(r"\d+", lines[idx - 1].strip()):
                counts.append(int(lines[idx - 1].strip()))
    # Priority 2: General fiscal receipt count if no customer receipt count found
    if not counts:
        for idx, line in enumerate(lines):
            clean_ln = line.lstrip(" -~•*#'")
            match = re.match(r"^MALI\s+FIS\s+ADET(?:I)?\s*[:=]?\s*(\d+)?\s*$", clean_ln)
            if match:
                if match.group(1):
                    counts.append(int(match.group(1)))
                elif idx > 0 and re.fullmatch(r"\d+", lines[idx - 1].strip()):
                    counts.append(int(lines[idx - 1].strip()))
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
