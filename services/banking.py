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
0064|Türkiye İş Bankası|IS BANKASI,ISBANK,IS BANK
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
    entries, issues = [], []
    evidence = bank_evidence(text)
    issues.extend(evidence.pop("issues"))
    pattern = r"\b(YEMEK\s*KARTI|PLUXEE|SODEXO|MULTINET|EDENRED|SETCARD|METROPOL\s*KART|TICKET(?:\s*RESTAURANT)?|BANKA\s*[/]\s*KRED[Iİ](?:\s*KART[Iİ]|\s*KARU|\s*RARU)?|BANKA\s*KARTI|DEBIT|KREDI\s*KARTI|CREDIT|NAKIT|KREDI|HAVALE|EFT|FAST|POS|KART(?:LA)?\s*ODEME|DIGER\s*(?:ODEME|TAHSILAT)|ACIK\s*HESAP|VERESIYE)\b"
    try:
        from services.document_ocr import _clean_ocr_line as _cl
        lines = [_cl(folded(line.strip())) for line in text.splitlines() if line.strip()]
    except ImportError:
        lines = [folded(line.strip()) for line in text.splitlines() if line.strip()]
    in_belge_tipleri = False
    for index, line in enumerate(lines):
        if kind == "z-reports":
            if re.search(r"\bBELGE\s*TIPLERI\b", line):
                in_belge_tipleri = True
                continue
            if in_belge_tipleri:
                if re.search(r"\b(?:SAYACLAR|KASIYER)\b", line):
                    in_belge_tipleri = False
                else:
                    continue
        match = re.search(pattern, line)
        if not match or re.search(r"IADE|IPTAL|KOMISYON|ISLEM\s*(?:NO|SAYISI)|KART\s*(?:NO|NUMARASI)", line):
            continue
        # Skip Z-report sub-section lines (e.g. "-KREDI *35.650,00" under BELGE TIPLERI)
        if kind == "z-reports" and line.lstrip().startswith(("-", "~", "'~", "*", "•", "+")):
            continue
        # Strip OCR asterisks before searching for amounts
        tail = re.sub(r"\*", "", line[match.end():])
        # Remove trailing count digit (e.g. 'KREDI 33' -> 33 is count not amount)
        tail_no_count = re.sub(r"^\s*\d{1,4}\s*$", "", tail.strip())
        values = re.findall(MONEY, tail_no_count if tail_no_count else tail)
        # Look up to 2 lines ahead for the amount (e.g. Z-report: KREDI 33 / TOPLAM 35.650,00)
        if not values:
            for lookahead in range(1, 3):
                if index + lookahead < len(lines):
                    next_l = re.sub(r"\*", "", lines[index + lookahead])
                    if re.fullmatch(r"[\s*:=TL0-9.,+-]+", next_l) or re.match(r"^TOPLAM\b", next_l):
                        nxt_vals = re.findall(MONEY, next_l)
                        if nxt_vals:
                            values = nxt_vals
                            break
                    elif re.search(pattern, next_l):
                        # Next line is a new payment keyword, stop
                        break
        if not values:
            continue
        label = re.sub(r"\s", "", match.group())
        method = "meal_card" if re.fullmatch(r"YEMEKKARTI|PLUXEE|SODEXO|MULTINET|EDENRED|SETCARD|METROPOLKART|TICKET(?:RESTAURANT)?", label) else "cash" if label == "NAKIT" else "debit_card" if label in {"BANKAKARTI", "DEBIT"} else "card" if (label in {"KREDIKARTI", "CREDIT", "KREDI"} or "KREDI" in label or "KRED" in label) else "bank_transfer" if label in {"HAVALE", "EFT", "FAST"} else "pos" if label in {"POS", "KARTODEME", "KARTLAODEME"} else "other"
        amount = decimal_money(values[-1])
        # If an OCR artifact prepended a digit (like 4 from a pen checkmark or asterisk) making amount exceed total:
        if total and amount > decimal_money(total):
            s_amt = str(amount)
            s_tot = str(decimal_money(total))
            if s_amt.startswith("4") and s_amt[1:] == s_tot:
                amount = decimal_money(s_amt[1:])
        if amount < 0 or re.search(r"-\s*[*₺]?\s*" + re.escape(values[-1]), line):
            issues.append("Negatif ödeme satırı incelenmeli.")
        codes = identify_banks(line)
        entry = {"method": method, "amount": str(amount), "bank_code": codes[0] if len(codes) == 1 else "", "bank_role": "acquirer" if method in {"card", "debit_card", "pos"} else "unspecified"}
        if method == "meal_card":
            entry["provider"] = "" if label == "YEMEKKARTI" else label
        entries.append(entry)
    # Z reports may contain both total card takings and bank-by-bank detail.
    if kind == "z-reports":
        meals = [entry for entry in entries if entry["method"] == "meal_card"]
        detailed_meals = [entry for entry in meals if entry.get("provider")]
        summary_meals = [entry for entry in meals if not entry.get("provider")]
        if detailed_meals and len(summary_meals) == 1:
            if sum((decimal_money(entry["amount"]) for entry in detailed_meals), Decimal(0)) == decimal_money(summary_meals[0]["amount"]):
                entries.remove(summary_meals[0])
            else:
                issues.append("Yemek kartı ayrıntıları yemek kartı toplamıyla uyuşmuyor.")
        cards = [entry for entry in entries if entry["method"] in {"card", "debit_card", "pos"}]
        detailed = [entry for entry in cards if entry["bank_code"]]
        summaries = [entry for entry in cards if not entry["bank_code"]]
        if detailed and len(summaries) == 1:
            if sum((decimal_money(entry["amount"]) for entry in detailed), Decimal(0)) == decimal_money(summaries[0]["amount"]):
                entries.remove(summaries[0])
            else:
                issues.append("Banka bazındaki POS toplamları kart toplamıyla uyuşmuyor.")
    if entries and total and sum((decimal_money(entry["amount"]) for entry in entries), Decimal(0)) != decimal_money(total):
        issues.append("Ödeme dağılımı belge toplamıyla uyuşmuyor.")
    if not entries:
        issues.append("Ödeme yöntemi ve tutarı okunamadı.")
    if kind == "receipts" and any(entry["method"] == "pos" for entry in entries):
        issues.append("Kartın banka kartı mı kredi kartı mı olduğu okunamadı.")
    nonzero = [entry for entry in entries if decimal_money(entry["amount"]) > 0]
    method = nonzero[0]["method"] if len(nonzero) == 1 else "mixed" if nonzero else "unknown"
    aggregates = {}
    for key, methods in {"cash_amount": {"cash"}, "card_amount": {"card", "debit_card", "pos"}, "bank_amount": {"bank_transfer"}, "meal_card_amount": {"meal_card"}, "other_payment": {"other"}}.items():
        values = [decimal_money(entry["amount"]) for entry in entries if entry["method"] in methods]
        aggregates[key] = str(sum(values, Decimal(0))) if values else ""
    return {"payment_entries": entries, "payment_method": method, "bank_evidence": evidence, **aggregates}, issues
