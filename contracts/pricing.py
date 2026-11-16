# ╔════════════════════════════════════════════════════════════════════════╗
# ║                          pricing.py                                       ║
# ║  Расчёт стоимости услуг по приказу № 81/26/КРНО от 03.06.2026.         ║
# ║                                                                        ║
# ║  Правила:                                                              ║
# ║  - деньги хранятся в копейках (int), float для сумм не используется;    ║
# ║  - 1.1 — за 1 га, пропорционально, округление до копейки;              ║
# ║  - комплекс 2.1-2.5 — 160 000 руб. и подача при полном комплекте        ║
# ║    бесплатна; 3.1 (5 000) начисляется только при неполном комплекте;    ║
# ║  - НДС выделяется из цены: НДС = цена × ставка / (ставка + 100).       ║
# ║    Контроль из образца: 160 000,00 руб., НДС 22% -> 28 852,46 руб.     ║
# ╚══════════════════════════════════════════════════════════════════════╝

from decimal import Decimal, ROUND_HALF_UP


KOP = Decimal('0.01')


def _to_kop(amount) -> int:
    """Рубли (число или строка) в копейки; округление до копейки."""
    d = Decimal(str(amount)) * 100
    return int(d.quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def rub(kop: int) -> Decimal:
    """Копейки в рубли для отображения: без float."""
    return (Decimal(kop) / 100).quantize(KOP)


def service_amount(tariff_kop: int, qty: Decimal = Decimal('1'),
                   area_ha: Decimal = None) -> int:
    """Сумма строки услуги.

    Для позиций «за 1 га» передаётся площадь: сумма = тариф × площадь.
    Для прочих — тариф × количество.
    """
    if qty is None:
        qty = Decimal('1')
    if area_ha is not None:
        base = Decimal(str(tariff_kop)) * Decimal(str(area_ha))
    else:
        base = Decimal(str(tariff_kop)) * Decimal(str(qty))
    return int(base.quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def vat_from_total(total_kop: int, vat_percent) -> int:
    """НДС из суммы, включающей НДС. Проверка: 160 000 -> 28 852,46."""
    if total_kop is None or total_kop <= 0:
        return 0
    p = Decimal(str(vat_percent))
    vat = (Decimal(total_kop) * p / (Decimal('100') + p))
    return int(vat.quantize(Decimal('1'), rounding=ROUND_HALF_UP))


# ─── КОМПЛЕКС НА СОВЕТ (пункты 2.1-2.5 приказа) ─────────────────────────────

COUNCIL_PACKAGE_CODES = ('2.1', '2.2', '2.3', '2.4', '2.5')
COUNCIL_PACKAGE_TOTAL_KOP = _to_kop(160000)
SUBMISSION_CODE = '3.1'
SUBMISSION_KOP = _to_kop(5000)


def council_package_total(service_codes) -> int:
    """Сумма услуг, если выбран полный комплекс 2.1-2.5.

    При полном комплексе цена фиксируется как 160 000 руб., а подача
    документов (3.1) не начисляется: при полном комплекте подача бесплатна.
    """
    codes = set(service_codes or [])
    if set(COUNCIL_PACKAGE_CODES) <= codes:
        return COUNCIL_PACKAGE_TOTAL_KOP
    return None


def council_services_total(lines) -> int:
    """Итоговая цена возмездного договора на основе строк услуг.

    lines — список словарей: code, amount_kop (уже рассчитанный).
    Правило полного комплекта:
      - выбраны все 2.1-2.5 -> эти позиции дают 160 000 руб. суммарно;
      - 3.1 в этом случае не добавляется (подача бесплатна);
      - при неполном комплекте 3.1 стоит 5 000 руб.
    """
    codes = [l.get('code') for l in lines]
    full = council_package_total(codes)
    total = 0
    for line in lines:
        code = line.get('code')
        amount = int(line.get('amount_kop') or 0)
        if full is not None and code in COUNCIL_PACKAGE_CODES:
            continue
        if full is not None and code == SUBMISSION_CODE:
            continue
        total += amount
    if full is not None:
        total += full
    return total


# ─── СУММА ПРОПИСЬЮ ──────────────────────────────────────────────────────────

_FEM = ('одна', 'две', 'три', 'четыре', 'одна', 'две', 'три', 'четыре')
_M = ('один', 'два', 'три', 'четыре', 'пять', 'шесть', 'семь', 'восемь',
      'девять', 'десять', 'одиннадцать', 'двенадцать', 'тринадцать',
      'четырнадцать', 'пятнадцать', 'шестнадцать', 'семнадцать',
      'восемнадцать', 'девятнадцать')
_TENS = ('двадцать', 'тридцать', 'сорок', 'пятьдесят', 'шестьдесят',
         'семьдесят', 'восемьдесят', 'девяносто')
_HUNDREDS = ('сто', 'двести', 'триста', 'четыреста', 'пятьсот',
             'шестьсот', 'семьсот', 'восемьсот', 'девятьсот')


def _three_to_words(n: int, female: bool = False) -> str:
    """0..999 словами; female=True для единиц и десятков женского рода."""
    words = []
    if n >= 100:
        words.append(_HUNDREDS[n // 100 - 1])
        n %= 100
    if n >= 20:
        t, u = divmod(n, 10)
        words.append(_TENS[t - 2])
        n = u
    if n > 0:
        if n < 5 and female:
            words.append(_FEM[n - 1])
        else:
            words.append(_M[n - 1])
    return ' '.join(words)


def _plural(n: int, one: str, few: str, many: str) -> str:
    if 10 <= n % 100 <= 20:
        return many
    d = n % 10
    if d == 1:
        return one
    if 2 <= d <= 4:
        return few
    return many


def rub_in_words(total_kop: int) -> str:
    """Сумма прописью, например «Сто шестьдесят тысяч рублей 00 копеек»."""
    if total_kop is None or total_kop < 0:
        return ''
    total_kop = int(total_kop)
    rubles, kopecks = divmod(total_kop, 100)

    if rubles == 0:
        rub_part = 'ноль рублей'
    else:
        groups = []
        rest = rubles
        units = [(10**9, 'миллиард', 'миллиарда', 'миллиардов', False),
                 (10**6, 'миллион', 'миллиона', 'миллионов', False),
                 (10**3, 'тысяча', 'тысячи', 'тысяч', True)]
        parts = []
        for value, one, few, many, female in units:
            cnt, rest = divmod(rest, value)
            if cnt == 0:
                continue
            words = _three_to_words(cnt, female)
            parts.append(f"{words} {_plural(cnt, one, few, many)}")
        if rest > 0:
            parts.append(_three_to_words(rest))
        groups = parts
        rub_word = _plural(rubles, 'рубль', 'рубля', 'рублей')
        rub_part = (' '.join(groups) + ' ' + rub_word).strip()

    kop_word = _plural(kopecks, 'копейка', 'копейки', 'копеек')
    return f"{rub_part} {kopecks:02d} {kop_word}"


# ─── ВАЛИДАЦИЯ УСЛОВИЙ ДОГОВОРА ─────────────────────────────────────────────

PAYMENT_SCHEMES = ('prepay_100', 'prepay_partial', 'postpay', 'guarantee_letter')
WORK_START_BASES = ('payment', 'signed_contract', 'guarantee_letter')
ACT_SIGN_METHODS = ('edo_upp', 'paper', 'sbis')


def vat_check(total_kop: int, vat_percent) -> bool:
    """Контрольная сверка: 160 000 руб. при 22% даёт 28 852,46 руб."""
    return vat_from_total(total_kop, vat_percent) == _to_kop('28852.46') \
        if (total_kop == _to_kop(160000) and str(vat_percent) == '22') \
        else None
