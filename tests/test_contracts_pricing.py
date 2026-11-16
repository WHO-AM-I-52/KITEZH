# ╔══════════════════════════════════════════════════════════════╗
# ║        tests/test_contracts_pricing.py                        ║
# ║  Расчёт по приказу 81/26/КРНО: тарифы, комплекс, НДС,         ║
# ║  площадь, копейки, сумма прописью.                            ║
# ╚══════════════════════════════════════════════════════════════╝

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from contracts import pricing
from contracts.pricing import (
    _to_kop, rub, service_amount, vat_from_total, council_package_total,
    council_services_total, rub_in_words,
)


def test_to_kop_and_rub():
    assert _to_kop(160000) == 16000000
    assert _to_kop('160000.00') == 16000000
    assert str(rub(16000000)) == '160000.00'


def test_vat_from_160k_22():
    # Контрольный пример из п. 2.1 образца: НДС 28 852,46
    assert vat_from_total(16000000, 22) == 2885246


def test_vat_rounding():
    # 30 000 руб., НДС 22%: 30000*22/122 = 5409,8360... -> 5409,84
    assert vat_from_total(_to_kop(30000), 22) == 540984


def test_area_1_1_proportional():
    # 20 000 за 1 га; 1,25 га -> 25 000
    assert service_amount(_to_kop(20000), area_ha='1.25') == 2500000
    # 1,3333 га -> 26666 руб. (26666,60 коп. -> 26666,60)
    assert service_amount(_to_kop(20000), area_ha="1.3333") == 2666660


def test_qty():
    assert service_amount(_to_kop(160000)) == 16000000
    assert service_amount(_to_kop(160000), qty='2') == 32000000


def test_council_full_package_160k():
    codes = ['2.1', '2.2', '2.3', '2.4', '2.5']
    assert council_package_total(codes) == 16000000


def test_council_full_package_with_submission_free():
    # Полный комплект 2.1-2.5 + 3.1: подача бесплатна, итог 160 000
    lines = [
        {'code': '2.1', 'amount_kop': 3000000},
        {'code': '2.2', 'amount_kop': 4000000},
        {'code': '2.3', 'amount_kop': 1500000},
        {'code': '2.4', 'amount_kop': 6000000},
        {'code': '2.5', 'amount_kop': 1500000},
        {'code': '3.1', 'amount_kop': 500000},
    ]
    assert council_services_total(lines) == 16000000


def test_council_partial_with_submission():
    # Неполный комплект: 2.1 + 3.1 -> 30 000 + 5 000
    lines = [
        {'code': '2.1', 'amount_kop': 3000000},
        {'code': '3.1', 'amount_kop': 500000},
    ]
    assert council_services_total(lines) == 3500000


def test_council_package_not_triggered():
    assert council_package_total(['2.1', '2.2']) is None


def test_rub_in_words_160000():
    assert rub_in_words(16000000) == (
        'сто шестьдесят тысяч рублей 00 копеек')


def test_rub_in_words_28852_46():
    assert rub_in_words(2885246) == (
        'двадцать восемь тысяч восемьсот пятьдесят два рубля 46 копеек')


def test_rub_in_words_zero():
    assert rub_in_words(0) == 'ноль рублей 00 копеек'


def test_rub_in_words_million():
    assert rub_in_words(_to_kop(1600000)) == (
        'один миллион шестьсот тысяч рублей 00 копеек')
