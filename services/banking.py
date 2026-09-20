"""Bank identities and payment evidence. EFT codes are not ledger subaccounts."""
from __future__ import annotations

import re
from decimal import Decimal

from services.document_extraction import MONEY, decimal_money, folded

BANK_SOURCE = "https://www.tcmb.gov.tr/wps/wcm/connect/9fa62a85-5b6d-46c5-9b01-eb461d43723d/TCMB%2B%C3%96deme%2BSistemleri%2BKat%C4%B1l%C4%B1mc%C4%B1lar%C4%B1%2B(072025).pdf?MOD=AJPERES"
BANK_VERIFIED_AT = "2026-09-13"
# TCMB's document currently contains the 2026 participants, despite its URL.
_BANK_DATA = """0215|Adil Katılım|ADIL KATILIM
0046|Akbank|AKBANK
0143|Aktif Yatırım Bankası|AKTIF BANK,AKTIFBANK,AKTIF YATIRIM
0203|Albaraka Türk|ALBARAKA
0124|Alternatifbank|ALTERNATIFBANK,ALTERNATIF BANK
0135|Anadolubank|ANADOLUBANK
0091|Arap Türk Bankası|ARAP TURK BANKASI
0161|Aytemiz Yatırım Bankası|AYTEMIZ YATIRIM
0129|Bank of America Yatırım Bank|BANK OF AMERICA
0149|Bank of China Turkey|BANK OF CHINA
0142|Bankpozitif|BANKPOZITIF
0029|Birleşik Fon Bankası|BIRLESIK FON BANKASI
0125|Burgan Bank|BURGAN
0092|Citibank|CITIBANK
0158|Colendi Bank|COLENDI
0151|D Yatırım Bankası|D YATIRIM BANKASI
0134|Denizbank|DENIZBANK,DENIZ BANK
0152|Destek Yatırım Bankası|DESTEK YATIRIM
0115|Deutsche Bank|DEUTSCHE BANK
0138|Diler Yatırım Bankası|DILER YATIRIM
0214|Dünya Katılım|DUNYA KATILIM
0157|Enpara Bank|ENPARA
0103|Fibabanka|FIBABANKA,FIBA BANKA
0159|FUPS Bank|FUPS
0150|Golden Global Yatırım Bankası|GOLDEN GLOBAL
0139|GSD Yatırım Bankası|GSD YATIRIM
0212|Hayat Finans Katılım|HAYAT FINANS
0156|Hedef Yatırım Bankası|HEDEF YATIRIM
0137|Hepsi Bank|HEPSI BANK,RABOBANK
0123|HSBC|HSBC
0109|ICBC Turkey|ICBC
0099|ING Bank|ING BANK,INGBANK
0148|Intesa Sanpaolo|INTESA SANPAOLO
0216|İktisat Katılım|IKTISAT KATILIM
0004|İller Bankası|ILLER BANKASI,ILBANK
0132|İstanbul Takas ve Saklama Bankası|TAKASBANK,TAKAS VE SAKLAMA
0098|JPMorgan Chase Bank|JPMORGAN,JP MORGAN
0205|Kuveyt Türk|KUVEYT TURK
0806|Merkezi Kayıt Kuruluşu|MERKEZI KAYIT KURULUSU
0153|Misyon Yatırım Bankası|MISYON YATIRIM
0147|MUFG Bank Turkey|MUFG
0141|Nurol Yatırım Bankası|NUROL
0146|Odea Bank|ODEABANK,ODEA BANK
0116|Pasha Yatırım Bank|PASHA
0807|Posta ve Telgraf Teşkilatı|PTT,POSTA VE TELGRAF
0122|Societe Generale|SOCIETE GENERALE
0121|Standard Chartered Yatırım Bankası|STANDARD CHARTERED
0059|Şekerbank|SEKERBANK,SEKER BANK
0032|Türk Ekonomi Bankası|TURK EKONOMI BANKASI,TEB
0016|Türk Eximbank|EXIMBANK
0062|Garanti BBVA|GARANTI
0012|Halkbank|HALKBANK,HALK BANKASI
0064|Türkiye İş Bankası|IS BANKASI,ISBANKASI,ISBANK,IS BANK
0017|Türkiye Kalkınma ve Yatırım Bankası|KALKINMA BANKASI,KALKINMA VE YATIRIM
0014|Türkiye Sınai Kalkınma Bankası|SINAI KALKINMA,TSKB
0015|VakıfBank|VAKIFBANK,VAKIFLAR BANKASI
0001|Türkiye Cumhuriyet Merkez Bankası|MERKEZ BANKASI,TCMB
0010|Ziraat Bankası|ZIRAAT BANKASI,ZIRAATBANK
0154|Tera Yatırım Bankası|TERA YATIRIM
0213|T.O.M. Katılım|T.O.M.,TOM KATILIM
0096|Turkish Bank|TURKISH BANK
0108|Turkland Bank|TURKLAND,T-BANK
0060|Türk Ticaret Bankası|TURK TICARET BANKASI
0211|Türkiye Emlak Katılım|EMLAK KATILIM
0206|Türkiye Finans Katılım|TURKIYE FINANS
0210|Vakıf Katılım|VAKIF KATILIM
0067|Yapı Kredi|YAPI KREDI,YAPI VE KREDI,YAPIKREDI
0160|Ziraat Dinamik Banka|ZIRAAT DINAMIK
0209|Ziraat Katılım|ZIRAAT KATILIM
0155|Q Yatırım Bankası|Q YATIRIM
0111|QNB Bank|QNB,FINANSBANK"""
BANKS = [{"code": code, "name": name, "aliases": aliases.split(",")} for code, name, aliases in (line.split("|") for line in _BANK_DATA.splitlines())]
BANK_BY_CODE = {bank["code"]: bank for bank in BANKS}
PAYMENT_LABELS = {"cash": "Nakit", "card": "Kredi kartı", "debit_card": "Banka kartı", "bank_transfer": "Havale / EFT / FAST", "pos": "Kart / POS tahsilatı", "unknown": "Ödeme belirtilmemiş", "other": "Diğer ödeme", "mixed": "Parçalı ödeme"}
PAYMENT_LABELS["meal_card"] = "Yemek kartı"


def identify_banks(text):
    value = folded(text)
    matches = []
    for bank in BANKS:
        for alias in bank["aliases"]:
            for match in re.finditer(r"(?<![A-Z0-9])" + re.escape(alias) + r"(?![A-Z0-9])", value):
                matches.append((match.start(), match.end(), bank["code"]))
    # A specific name takes precedence over a contained parent-bank name.
    codes = {code for start, end, code in matches if not any(left <= start and right >= end and (right-left) > (end-start) for left, right, _ in matches)}
    return sorted(codes)


def normalize_iban(value):
    iban = re.sub(r"\s", "", str(value)).upper()
    if not re.fullmatch(r"TR\d{7}0[A-Z0-9]{16}", iban):
        raise ValueError("IBAN biçimi geçersiz; Türkiye IBAN'ı 26 karakter olmalı.")
    numeric = "".join(str(ord(char)-55) if char.isalpha() else char for char in iban[4:] + iban[:4])
    if int(numeric) % 97 != 1:
        raise ValueError("IBAN kontrol basamakları uyuşmuyor.")
    return iban


def bank_from_iban(iban):
    code = normalize_iban(iban)[4:9]
    return code[1:] if code.startswith("0") and code[1:] in BANK_BY_CODE else ""


def bank_evidence(text):
    """Keep issuing-bank evidence distinct from a merchant's POS bank."""
    banks = identify_banks(text)
    ibans, issues = [], []
    for match in re.finditer(r"\bTR\s*\d{2}(?:\s*[A-Z0-9]){22}\b", folded(text)):
        try:
            ibans.append(normalize_iban(match.group()))
        except ValueError as error:
            issues.append(str(error))
    issuer, sender_ibans, recipient_ibans = [], [], []
    for line in text.splitlines():
        if re.search(r"KARTI\s+VEREN\s+BANKA|KART\s+BANKASI|ISSUER", folded(line)):
            issuer.extend(identify_banks(line))
        compact_line = re.sub(r"\s", "", folded(line))
        for iban in ibans:
            if iban not in compact_line:
                continue
            if re.search(r"GONDEREN|ODEYEN|BORCLANDIRILAN", folded(line)):
                sender_ibans.append(iban)
            if re.search(r"ALICI|LEHDAR", folded(line)):
                recipient_ibans.append(iban)
    last4 = list(dict.fromkeys(re.findall(r"(?:[*Xx•]{2,}[ *Xx•]*|KART\s+SON\s*(?:4|DORT)\s*(?:HANE)?\s*[:=]?)\s*(\d{4})\b(?![.,]\d)", folded(text))))
    return {"bank_codes": banks, "ibans": list(dict.fromkeys(ibans)), "sender_ibans": sorted(set(sender_ibans)), "recipient_ibans": sorted(set(recipient_ibans)), "issuer_codes": sorted(set(issuer)), "card_last4": last4, "issues": issues}


def extract_payments(text, total, kind):
    from services.ocr_text import clean_ocr_line, z_sections

    issues = []
    evidence = bank_evidence(text)
    issues.extend(evidence.pop("issues"))
    pattern = r"\b(YEMEK\s*KARTI|PLUXEE|SODEXO|MULTINET|EDENRED|SETCARD|METROPOL\s*KART|TICKET(?:\s*RESTAURANT)?|BANKA\s*[/]\s*KREDI(?:\s*KARTI|\s*KARU)?|BANKA\s*KARTI|DEBIT|KREDI\s*KARTI|CREDIT|NAKIT|KREDI|HAVALE|EFT|FAST|POS|KART(?:LA)?\s*ODEME|DIGER(?:\s*(?:ODEME|TAHSILAT))?|ACIK\s*HESAP|VERESIYE)\b"
    lines = [folded(clean_ocr_line(line)) for line in text.splitlines() if line.strip()]
    is_z = kind == "z-reports"
    sections = list(z_sections(lines)) if is_z else [("general", line) for line in lines]
    groups = {"main": [], "documents": []}
    missing = {"main": [], "documents": []}
    slip_type_pattern = (r'(?:T?ROY|VISA|MASTER(?:CARD)?|AMEX)\s*/\s*'
                         r'(K(?:R|I|/)ED[I1]|CREDIT|DEBIT|BANKA)\s*/\s*(?:ONUS|OFFUS)')
    for index, (section, line) in enumerate(sections):
        if section not in {"general", "daily", "payments", "documents"}:
            continue
        if re.fullmatch(slip_type_pattern, line):
            continue  # Card type metadata; its following total is not a second payment.
        if not is_z and (re.search(r'%\s*\d{1,2}\b', line) or
                         (index + 1 < len(lines) and re.match(r'^%\s*\d{1,2}\b', lines[index + 1]))):
            continue  # Priced merchandise such as "FAST YAPISTIRICI" or "POS RULOSU".
        bank_tender = re.fullmatch(r'([A-Z ]+)\s+TEK(?:\s+CEKIM)?\s+(?:TRY|TL)\s+(' + MONEY + r')', line)
        if bank_tender and not is_z and any(re.search(r'\bE[- ]?ARSIV\b', value) for value in lines):
            banks = identify_banks(bank_tender[1])
            if len(banks) == 1:
                groups['main'].append({'method': 'pos', 'amount': str(decimal_money(bank_tender[2])),
                                       'bank_code': banks[0], 'bank_role': 'acquirer'})
                continue
        match = re.search(pattern, line)
        if not match or re.search(r"IADE|IPTAL|KOMISYON|ISLEM\s*(?:NO|SAYISI)|KART\s*(?:NO|NUMARASI)", line):
            continue
        group = "documents" if section == "documents" else "main"
        tail = line[match.end():]
        values = re.findall(MONEY, tail)
        # POS terminal identifiers are metadata, not an additional payment.
        if match.group() == 'POS' and not values and (
                re.search(r'\bISYERI\b', line) or
                re.match(r'\s*(?:NO\b|ID\b|NUMARASI\b|[A-Z]\d{4,}\b)', tail)):
            continue
        amount_line = tail
        if not values and index + 1 < len(sections):
            next_section, next_line = sections[index + 1]
            # Only the immediately following numeric/TOPLAM/TUTAR line belongs
            # to this payment. Never borrow from another method or report section.
            amount_only = re.fullmatch(r"[\s*:=TLRY₺0-9.,+-]+", next_line)
            labeled_amount = is_z and re.match(r"^(?:TOPLAM|TUTAR)\b", next_line)
            if next_section == section and (amount_only or labeled_amount):
                values = re.findall(MONEY, next_line)
                amount_line = next_line
        label = re.sub(r"\s", "", match.group())
        if not values:
            missing[group].append(label)
            continue
        if len(values) != 1:
            issues.append("Ödeme satırında birden fazla tutar var; sütunlar incelenmeli.")
        meal = re.fullmatch(r"YEMEKKARTI|PLUXEE|SODEXO|MULTINET|EDENRED|SETCARD|METROPOLKART|TICKET(?:RESTAURANT)?", label)
        if meal:
            method = "meal_card"
        elif label == "NAKIT":
            method = "cash"
        elif label in {"BANKAKARTI", "DEBIT"}:
            method = "debit_card"
        elif label.startswith("BANKA/") or label in {"POS", "KARTODEME", "KARTLAODEME"}:
            method = "pos"
        elif label in {"KREDIKARTI", "CREDIT", "KREDI"}:
            method = "card"
        elif label in {"HAVALE", "EFT", "FAST"}:
            method = "bank_transfer"
        else:
            method = "other"
        amount = decimal_money(values[-1])
        if re.search(r"-\s*[*₺]?\s*" + re.escape(values[-1]), amount_line):
            issues.append("Negatif ödeme satırı incelenmeli.")
        codes = identify_banks(line)
        if len(codes) > 1:
            issues.append("Ödeme satırındaki banka bilgileri çelişiyor.")
        entry = {"method": method, "amount": str(amount), "bank_code": codes[0] if len(codes) == 1 else "",
                 "bank_role": "acquirer" if method in {"card", "debit_card", "pos"} else "unspecified"}
        if meal:
            entry["provider"] = "" if label == "YEMEKKARTI" else label
        groups[group].append(entry)

    def reconcile_detail(entries):
        if not is_z:
            return
        for methods, identity, message in (
            ({"meal_card"}, "provider", "Yemek kartı ayrıntıları yemek kartı toplamıyla uyuşmuyor."),
            ({"card", "debit_card", "pos"}, "bank_code", "Banka bazındaki POS toplamları kart toplamıyla uyuşmuyor."),
        ):
            selected = [entry for entry in entries if entry["method"] in methods]
            details = [entry for entry in selected if entry.get(identity)]
            summaries = [entry for entry in selected if not entry.get(identity)]
            if details and len(summaries) == 1:
                if sum((decimal_money(entry["amount"]) for entry in details), Decimal(0)) == decimal_money(summaries[0]["amount"]):
                    entries.remove(summaries[0])
                else:
                    issues.append(message)

    for entries in groups.values():
        reconcile_detail(entries)
    entries = groups["main"] or groups["documents"]
    selected_group = "main" if groups["main"] else "documents"
    if groups["main"] and groups["documents"]:
        def totals_by_method(values):
            totals = {}
            for entry in values:
                method = "pos" if entry["method"] in {"card", "debit_card", "pos"} else entry["method"]
                totals[method] = totals.get(method, Decimal(0)) + decimal_money(entry["amount"])
            return {key: value for key, value in totals.items() if value}
        if totals_by_method(groups["main"]) != totals_by_method(groups["documents"]):
            issues.append("Ödeme bilgileri ile belge tiplerindeki tahsilatlar çelişiyor.")
    if missing[selected_group]:
        issues.append("Ödeme alanı var ancak tutarı okunamadı.")
    # A combined "Banka/Kredi Kartı" label is ambiguous on its own. A printed
    # network/type/routing line on the attached approved slip can resolve it,
    # but only when its own total matches exactly one unspecified card payment.
    if not is_z:
        generic_cards = [entry for entry in entries if entry['method'] == 'pos']
        if len(generic_cards) == 1:
            types = set()
            for index, line in enumerate(lines):
                card_type = re.fullmatch(slip_type_pattern, line)
                if not card_type or index + 1 >= len(lines):
                    continue
                amount_line = lines[index + 1]
                amounts = re.findall(MONEY, amount_line)
                approved = any(re.fullmatch(r'ISLEM\s+ONAYLANDI', following)
                               for following in lines[index + 2:index + 5])
                if (approved and re.match(r'^TOPLAM\b', amount_line) and len(amounts) == 1
                        and decimal_money(amounts[0]) == decimal_money(generic_cards[0]['amount'])):
                    types.add('debit_card' if card_type[1] in {'DEBIT', 'BANKA'} else 'card')
            if len(types) == 1:
                generic_cards[0]['method'] = types.pop()
            elif len(types) > 1:
                issues.append('POS slipindeki kart türü bilgileri çelişiyor.')
    if not entries:
        issues.append("Ödeme yöntemi ve tutarı okunamadı.")
    elif total and sum((decimal_money(entry["amount"]) for entry in entries), Decimal(0)) != decimal_money(total):
        issues.append("Ödeme dağılımı belge toplamıyla uyuşmuyor.")
    if not is_z and any(entry["method"] == "pos" for entry in entries):
        issues.append("Kartın banka kartı mı kredi kartı mı olduğu okunamadı.")
    nonzero = [entry for entry in entries if decimal_money(entry["amount"]) > 0]
    method = nonzero[0]["method"] if len(nonzero) == 1 else "mixed" if nonzero else "unknown"
    aggregates = {}
    for key, methods in {"cash_amount": {"cash"}, "card_amount": {"card", "debit_card", "pos"},
                         "bank_amount": {"bank_transfer"}, "meal_card_amount": {"meal_card"}, "other_payment": {"other"}}.items():
        values = [decimal_money(entry["amount"]) for entry in entries if entry["method"] in methods]
        aggregates[key] = str(sum(values, Decimal(0))) if values else ""
    return {"payment_entries": entries, "payment_method": method, "bank_evidence": evidence, **aggregates}, issues
