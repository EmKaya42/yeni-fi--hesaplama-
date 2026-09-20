"""Printed product and VAT tables on retail e-archive invoices."""
import re
from decimal import Decimal, ROUND_HALF_UP


def retail_table(original, total, discount, issues, notes):
    from services.document_extraction import decimal_money, folded

    lines = [folded(line) for line in original]
    product_header = r'^URUN\s+\d{8,14}\s*$'
    starts = [i for i, line in enumerate(lines) if re.fullmatch(product_header, line)]
    if not starts or not any(re.search(r'\bE[- ]?ARSIV\b', line) for line in lines):
        return None
    # Turkish money columns can touch after OCR; require two complete amounts.
    money = r'(?:\d{1,3}(?:\.\d{3})+|\d+),\d{2}'
    vat_lines, table_rates, fiscal_lines = [], [], []
    in_vat = False
    for raw, line in zip(original, lines):
        if re.match(r'^VERGI\s+DAHIL\s*:', line):
            in_vat = True
            continue
        if in_vat and re.match(r'^\d', line):
            match = re.fullmatch(r'(?:\d+\s+)?(\d{1,2})(?:[.,]00)?\s*%\s*(' + money + r')\s*(' + money + r')', line)
            if match:
                rate, gross, tax = match.groups()
                table_rates.append(int(rate))
                vat_lines.append(f'KDV %{rate} {gross} {tax}')
            else:
                issues.append('Vergi dahil tablosundaki oran ve tutarlar ayrılamadı.')
            continue
        if in_vat and re.search(r'[A-Z]', line):
            in_vat = False
        fiscal_lines.append(line)
    if not vat_lines:
        issues.append('Faturanın vergi dahil tablosu okunamadı.')

    items, names, active = [], [], False
    price_row = re.compile(r'^(' + money + r')\s*[^\d\s]{0,2}\s*(\d+(?:[.,]\d+)?)\s+(?:TRY|TL)\s+(' + money + r')$')
    footer_start = len(original)
    for index in range(starts[0], len(original)):
        raw, line = original[index], lines[index]
        if re.match(r'^(?:BRUT\s+TOPLAM|TOPLAM|GENEL\s+TOPLAM|ARA\s*TOPLAM)\b', line):
            footer_start = index
            break
        if re.fullmatch(product_header, line):
            if active:
                issues.append('Bir fatura ürününün fiyat / miktar satırı okunamadı.')
            names, active = [], True
            continue
        if not active:
            if line.strip(' ._-'):
                issues.append('Fatura ürün tablosunda ayrılamayan satır var.')
            continue
        price = price_row.fullmatch(line)
        if price:
            unit_price, quantity, amount = price.groups()
            quantity = Decimal(quantity.replace(',', '.'))
            amount, unit_price = decimal_money(amount), decimal_money(unit_price)
            if quantity <= 0 or amount <= 0 or (quantity * unit_price).quantize(Decimal('.01'), rounding=ROUND_HALF_UP) != amount:
                issues.append('Ürün miktarı × birim fiyat, kalem tutarına eşit değil.')
            if not names:
                issues.append('Bir fatura ürününün adı okunamadı.')
            items.append({'name': ' '.join(names), 'quantity': str(quantity), 'unit': '',
                          'unit_price': str(unit_price), 'amount': str(amount), 'rate': None})
            names, active = [], False
        elif re.search(r'[A-Z]{2}', line) and not re.search(r'@|https?://|\b(?:ETTN|SADAKAT|MUSTERI|LOYALTY)\b', line, re.I):
            names.append(raw)
        elif line.strip(' ._-'):
            issues.append('Fatura ürününün fiyat / miktar sütunları ayrılamadı.')
    if active or not items:
        issues.append('Fatura ürün tablosu tam okunamadı.')
    summed = sum((decimal_money(item['amount']) for item in items), Decimal(0))
    if total and summed - discount != decimal_money(total):
        issues.append('Ürün satırlarının toplamı belge toplamıyla uyuşmuyor; eksik ürün olabilir.')
    if len(set(table_rates)) == 1 and total and summed - discount == decimal_money(total):
        for item in items:
            item['rate'] = table_rates[0]
        notes.append('Ürünlerin KDV oranı, faturanın tek oranlı vergi tablosundan alındı; toplamlar karşılaştırıldı.')
    else:
        issues.append('Fatura ürünlerinin KDV oranı dağılımı doğrulanamadı.')
    return {'items': items, 'fiscal_lines': fiscal_lines + vat_lines,
            'payment_lines': ['E-ARSIV'] + original[footer_start:]}
