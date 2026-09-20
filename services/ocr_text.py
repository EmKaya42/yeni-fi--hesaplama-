"""Normalize OCR typography and labels without inventing document values."""
from __future__ import annotations

import re


def clean_ocr_line(line: str) -> str:
    line = line.replace("\ufffd", "").replace("\u00a0", " ")
    line = re.sub(r'(\d)(?=SAAT\b)', r'\1 ', line, flags=re.I)
    line = re.sub(r"(\d)\s*,\s*(\d{2})(?!\d)", r"\1,\2", line)
    # Join printed thousands only after an amount marker or a total label.
    # 'KREDI 2 120,00' can contain a transaction count, so leave it alone.
    def grouped(match):
        return match[1] + re.sub(r"\s+", ".", match[2])
    line = re.sub(
        r"([*₺+]|\b(?:TOPLAM[Iİ]?|TOPKDV|TUTAR[Iİ]?)\s*[:=]?\s*)(\d{1,3}(?:[.\s]\d{3})+,\d{2})(?!\d)",
        grouped, line, flags=re.I,
    )
    # Only repair known label shapes. Never substitute a date, number or name.
    for pattern, replacement in (
        (r"\b(?:TOPLAN|TOPIAM|TOPLAH|TOPLAK|TOPUAM)\b", "TOPLAM"),
        (r"\[(?:OPADV|OPKDV|OPNDV|OPNOV)\b", "TOPKDV"),
        (r"\b(?:TOP[ -]?(?:KOV|KOY|KDU|KOW|K0V)|[1I][0O]PKDV)\b", "TOPKDV"),
        (r"\b(?:KOV|KOY|KDU|K0V)\b", "KDV"),
        (r"\bPAPOR\b", "RAPOR"),
        (r"\bRAPOR\s+(?:N0|tO)\b", "RAPOR NO"),
        (r"\bZ\s+N0\b", "Z NO"),
        (r"\bF[Iİ]G\s+NO\b", "FIS NO"),
        (r"\b2\s+RAPORU?\b", "Z RAPORU"),
        (r"\b[OÖ]DEHE\b", "ODEME"),
        (r"\bHAKIT\b", "NAKIT"),
        (r"\bDEPARTHAN\b", "DEPARTMAN"),
        (r"\bG[UÜ]NL[UÜ]K\s+F[Iİ][SŞ]\s+D[OÖ]K[UÜ][NH][UÜ]\b", "GUNLUK FIS DOKUMU"),
        (r"\b(?:HALI|PALI|PALL)\s+BELLEK\b", "MALI BELLEK"),
        (r"\bHAL[Iİ]\s+F[Iİ][SŞ]\b", "MALI FIS"),
        (r"\b[Iİ]ND[Iİ]R[Iİ]K\b", "INDIRIM"),
        (r"^SAT[Iİ]\s+(?=[Iİ]PTAL\b)", "SATIS "),
    ):
        line = re.sub(pattern, replacement, line, flags=re.I)
    line = re.sub(
        r"([*₺+$]|\b(?:TOPLAM[Iİ]?|TOPKDV|TUTAR[Iİ]?)\s*[:=]?\s*)(\d{1,3}(?:[.\s]\d{3})+,\d{2})(?!\d)",
        grouped, line, flags=re.I,
    )
    return line.strip()


def z_sections(lines):
    """Keep repeated Z-report sections separate from daily sales and refunds."""
    section = "general"
    for line in lines:
        clean = re.sub(r"^[^A-Z0-9%]+", "", line)
        if re.search(r"\bGUNLUK\s+FIS\s+DOKUMU\b", clean):
            section = "daily"
        elif re.search(r"\bKDV\s+BILGILERI\b", clean):
            section = "vat"
        elif re.search(r"\bDEPARTMAN\s+BILGILERI\b", clean):
            section = "department"
        elif re.search(r"\bODEME\s+BILGILERI\b", clean):
            section = "payments"
        elif re.search(r"\bBELGE\s+TIPLERI\b", clean):
            section = "documents"
        elif re.search(r"\bSAYACLAR\b", clean):
            section = "counters"
        elif re.search(r"\bKASIYER\s+BILGI\b", clean):
            section = "cashier"
        elif re.match(r"(?:(?:PR|FIS|SATIS)\s*)?(?:IPTAL|IADE)\b", clean) and section != "counters":
            section = "adjustment"
        elif re.match(r"(?:OKC\s+FISLERI|E[- ]?(?:FATURA|ARSIV)|FATURALAR)\b", clean) and section == "adjustment":
            section = "documents"
        yield section, clean
