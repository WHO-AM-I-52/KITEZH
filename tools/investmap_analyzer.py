# ╔══════════════════════════════════════════════════════════════╗
# ║             tools/investmap_analyzer.py                     ║
# ║  Оценка заполняемости карточки инвестплощадки               ║
# ║  Методика: Минэкономразвития РФ, 08.08.2023 №28301-МК/Д28и  ║
# ║  + публичный светофор invest.gov.ru                         ║
# ║  Версия алгоритма: 1.3.0                                    ║
# ╚══════════════════════════════════════════════════════════════╝

from __future__ import annotations

SMS_THRESHOLD = 75  # % — ниже этого порога формируется SMS

# ---------------------------------------------------------------------------
# БЛОК 1. СВЕДЕНИЯ ОБ ОБЪЕКТЕ (вес 15%)
# ---------------------------------------------------------------------------
BLOCK1_FIELDS = [
    ('Название площадки',                           'Название площадки',                True,  6),
    ('Преференциальный режим',                      'Преференциальный режим',           True,  5),
    ('Объект инфраструктуры поддержки',             'Объект инфраструктуры поддержки',  True,  5),
    ('Регион',                                      'Регион',                           True,  4),
    ('Муниципальное образование',                   'Муниципальное образование',        True,  4),
    ('Адрес объекта',                               'Адрес объекта',                    True,  4),
    ('Ближайший город',                             'Ближайший город',                  True,  3),
    ('Формат площадки',                             'Формат площадки',                  True,  5),
    ('Тип площадки',                                'Тип площадки (браун/гринфилд)',    True,  4),
    ('Фотографии',                                  'Фотографии объекта',               True,  8),
    ('Документы по объекту',                        'Документы по объекту',             False, 5),
    ('Координаты',                                  'Геопривязка',                      True,  10),
]
BLOCK1_MAX = 63
BLOCK1_WEIGHT = 0.15

# ---------------------------------------------------------------------------
# БЛОК 2. СВОБОДНЫЕ ПЛОЩАДИ И КОММЕРЧЕСКИЕ УСЛОВИЯ (вес 20%)
# ---------------------------------------------------------------------------
BLOCK2_FIELDS = [
    ('Форма собственности',                         'Форма собственности',              True,  5),
    ('Форма сделки',                                'Форма сделки',                     True,  6),
    ('Стоимость объекта',                           'Стоимость объекта, руб.',          True,  8),
    ('Стоимость руб./год за га',                    'Стоимость руб./год за га',         True,  6),
    ('Стоимость руб./год за кв.м',                  'Стоимость руб./год за кв.м',       True,  6),
    ('Сроки аренды',                                'Сроки аренды',                     True,  5),
    ('Порядок определения стоимости',               'Порядок определения стоимости',    True,  5),
    ('Класс опасности',                             'Класс опасности',                  False, 4),
    ('Характеристики ОКС',                          'Характеристики ОКС',               False, 5),
    ('Свободная площадь ЗУ',                        'Свободная площадь ЗУ, га',         True,  8),
    ('Кадастровый номер ЗУ',                        'Кадастровый номер ЗУ',             True,  5),
    ('Варианты разрешённого использования',         'ВРИ',                              True,  5),
    ('Межевание',                                   'Межевание',                        True,  4),
    ('Категория земель',                            'Категория земель',                 True,  4),
    ('Свободная площадь здания',                    'Свободная площадь здания, кв.м',   True,  8),
    ('Кадастровый номер здания',                    'Кадастровый номер здания',         True,  5),
    ('Технические характеристики здания',           'Тех. характеристики здания',       True,  5),
]
BLOCK2_MAX = 76
BLOCK2_WEIGHT = 0.20

BLOCK2_ZU_KEYS = {
    'Свободная площадь ЗУ', 'Кадастровый номер ЗУ',
    'Варианты разрешённого использования', 'Межевание', 'Категория земель',
}
BLOCK2_BUILDING_KEYS = {
    'Свободная площадь здания', 'Кадастровый номер здания', 'Технические характеристики здания',
}

# ---------------------------------------------------------------------------
# БЛОК 3. ТЕХНИЧЕСКОЕ ПРИСОЕДИНЕНИЕ (вес 30%)
# Ключи БЕЗ точки после названия ресурса — совпадает с форматом портала:
# «Водоснабжение Наличие (Да/Нет)», «Водоснабжение Тариф на питьевую воду» и т.д.
# ---------------------------------------------------------------------------
BLOCK3_RESOURCES = [
    ('Водоснабжение Наличие', [
        ('Водоснабжение Наличие',                      4),
        ('Водоснабжение Тариф на питьевую воду',       3),
        ('Водоснабжение Тариф на транспортировку',     3),
        ('Объекты водоснабжения Максимально допустимая мощность', 3),
        ('Объекты водоснабжения Свободная мощность',   3),
        ('Водоснабжение Плата за подключение',         2),
        ('Объекты водоснабжения Иные характеристики',  2),
    ]),
    ('Водоотведение Наличие', [
        ('Водоотведение Наличие',                      4),
        ('Водоотведение Тариф на водотведение',        3),
        ('Водоотведение Плата за подключение',         2),
        ('Объекты водоотведения Иные характеристики',  2),
    ]),
    ('Газоснабжение Наличие', [
        ('Газоснабжение Наличие',                      4),
        ('Газоснабжение Тариф на потребление',         3),
        ('Газоснабжение Тариф на услуги по транспортировке', 3),
        ('Объекты газоснабжения Величина максимального расхода', 3),
        ('Объекты газоснабжения Свободная мощность',   3),
        ('Газоснабжение Плата за подключение',         2),
        ('Объекты газоснабжения Иные характеристики',  2),
    ]),
    ('Электроснабжение Наличие', [
        ('Электроснабжение Наличие',                   4),
        ('Электроснабжение Тариф на потребление',      3),
        ('Объекты электроснабжения Тариф на услуги по передаче', 3),
        ('Объекты электроснабжения Максимальная мощность', 3),
        ('Объекты электроснабжения Свободная мощность', 3),
        ('Электроснабжение Плата за подключение',      2),
        ('Объекты электроснабжения Иные характеристики', 2),
    ]),
    ('Теплоснабжение Наличие', [
        ('Теплоснабжение Наличие',                     4),
        ('Теплоснабжение Тариф на потребление',        3),
        ('Теплоснабжение Тариф на услуги по передаче', 3),
        ('Теплоснабжение Максимальная мощность',       3),
        ('Теплоснабжение Свободная мощность',          3),
        ('Теплоснабжение Плата за подключение',        2),
        ('Объекты теплоснабжения Иные характеристики', 2),
    ]),
    ('Вывоз ТКО Наличие', [
        ('Вывоз ТКО Наличие',                          4),
        ('Тариф на вывоз ТКО',                         3),
        ('Объекты ТКО Иные характеристики',            2),
    ]),
]
BLOCK3_MAX = 100
BLOCK3_WEIGHT = 0.30

# ---------------------------------------------------------------------------
# БЛОК 4. ТРАНСПОРТНАЯ ДОСТУПНОСТЬ (вес 15%)
# «Наличие ж/д» — сокращение, используемое на портале invest.gov.ru
# ---------------------------------------------------------------------------
BLOCK4_FIELDS = [
    ('Наличие подъездных путей',        'Наличие подъездных путей',               True,  15),
    ('Наличие ж/д',                     'Наличие ж/д путей',                      True,  15),
    ('Наличие парковки',                'Наличие парковки грузового транспорта',  True,  10),
    ('Транспортная доступность',        'Иные характеристики транспорта',         False, 10),
]
BLOCK4_MAX = 50
BLOCK4_WEIGHT = 0.15

# ---------------------------------------------------------------------------
# БЛОК 5. КОНТАКТЫ И ИНВЕСТ. ПРИВЛЕКАТЕЛЬНОСТЬ (вес 20%)
# «Перечень видов экономической» — уникальная часть длинного названия поля портала
# ---------------------------------------------------------------------------
BLOCK5_FIELDS = [
    ('Наименование собственника',               'Наименование собственника',                    True,  6),
    ('ИНН собственника',                        'ИНН собственника',                             False, 3),
    ('Контактное лицо',                         'Контактное лицо',                              False, 4),
    ('Телефон',                                 'Телефон / e-mail',                             True,  8),
    ('Сайт',                                    'Сайт',                                         False, 4),
    ('Примечание',                              'Примечание',                                   False, 3),
    ('Описание процедуры подачи заявки',        'Описание процедуры подачи заявки',             True,  8),
    ('Перечень документов',                     'Перечень документов для заявки',               False, 5),
    ('Ссылка на форму подачи заявки',           'Ссылка на форму подачи заявки',                False, 4),
    ('Адрес эл. почты для подачи заявки',       'Email для подачи заявки',                      True,  7),
    ('Перечень видов экономической',            'Перечень ОКВЭД',                               True,  6),
    ('Градостроительные характеристики',        'Градостроительные характеристики',             False, 4),
    ('Документы территориального планирования', 'Документы территориального планирования',      True,  6),
    ('Иные сведения',                           'Иные сведения',                                False, 4),
    ('Фотографии',                              'Фотографии (блок 5)',                          False, 5),
    ('Наличие обременений',                     'Обременения',                                  True,  5),
]
BLOCK5_MAX = 82
BLOCK5_WEIGHT = 0.20

# ---------------------------------------------------------------------------
# ПОРТАЛЬНЫЙ РАСЧЁТ (invest.gov.ru)
# ---------------------------------------------------------------------------
PORTAL_GREEN = 75
PORTAL_YELLOW = 60

PORTAL_FIELDS = [
    ('Статус', None),
    ('Преференциальный режим', None),
    ('Наименование объекта преференциального режима', None),
    ('Объект инфраструктуры поддержки', None),
    ('Наименование объекта инфраструктуры поддержки', None),
    ('Регион', None),
    ('Муниципальное образование', None),
    ('Адрес объекта', None),
    ('Ближайший город', None),
    ('Формат площадки', None),
    ('Тип площадки', None),
    ('Форма собственности', None),
    ('Форма сделки', None),
    ('Стоимость объекта', None),
    ('Сроки аренды', None),
    ('Порядок определения стоимости', None),
    ('Свободная площадь ЗУ', 'ЗУ'),
    ('Свободная площадь здания', 'здание'),
    ('Варианты разрешённого использования', 'ЗУ'),
    ('Межевание', 'ЗУ'),
    ('Категория земель', 'ЗУ'),
    ('Кадастровый номер ЗУ', 'ЗУ'),
    ('Кадастровый номер здания', 'здание'),
    ('Технические характеристики здания', 'здание'),
    ('Наименование собственника', None),
    # FIX: был 'Водоснабжение. Наличие' — точка нарушала поиск подстроки
    ('Водоснабжение Наличие', None),
    ('Наличие подъездных путей', None),
    # FIX: был 'Наличие железнодорожных путей' — портал использует сокращение 'ж/д'
    ('Наличие ж/д', None),
    ('Наличие парковки', None),
    ('Описание процедуры подачи заявки', None),
    ('Перечень документов', None),
    # FIX: был 'Перечень видов деятельности' — слово 'экономической' разрывало подстроку
    ('Перечень видов экономической', None),
]


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------

def _find(data: dict, key: str) -> str:
    key_lower = key.lower()
    for k, v in data.items():
        if key_lower in k.lower():
            return v
    return 'ПУСТО'


def _find_exact(data: dict, key: str) -> str:
    key_lower = key.lower()
    for k, v in data.items():
        if k.lower() == key_lower:
            return v
    return 'ПУСТО'


def _is_filled(val: str) -> bool:
    return val not in ('ПУСТО', '', None)


def _presence_factor(val: str) -> float:
    if not _is_filled(val):
        return 0.0
    v = val.lower()
    if 'да' in v:
        return 1.0
    if 'возможно' in v:
        return 0.5
    if 'нет' in v:
        return 0.0
    return 1.0


def _get_format(data: dict) -> str:
    val = _find(data, 'Формат площадки').lower()
    if 'земельн' in val or 'участок' in val or 'зу' in val:
        return 'ЗУ'
    if 'здание' in val or 'сооружение' in val or 'помещение' in val:
        return 'здание'
    return 'неизвестно'


def _get_status(data: dict) -> str:
    val = _find(data, 'Статус').lower()
    if 'своб' in val:
        return 'свободна'
    if 'реализ' in val or 'снята' in val:
        return 'реализована'
    return 'иное'


def _get_id(data: dict) -> str:
    val = _find_exact(data, 'global_id')
    if _is_filled(val):
        return str(val)
    for key in ('Название площадки', 'Код во внешнем источнике'):
        val = _find(data, key)
        if _is_filled(val):
            return str(val)[:60]
    return 'ID не определён'


def _score_portal(data: dict, fmt: str) -> tuple[int, list[str]]:
    filled_count = 0
    total_count = 0
    missing = []

    for key, only_fmt in PORTAL_FIELDS:
        if only_fmt is not None and only_fmt != fmt:
            continue

        total_count += 1

        if key == 'Водоснабжение Наличие':
            comm_keys = [
                'Водоснабжение Наличие',
                'Водоотведение Наличие',
                'Газоснабжение Наличие',
                'Теплоснабжение Наличие',
                'Электроснабжение Наличие',
            ]
            filled = any(_is_filled(_find(data, k)) for k in comm_keys)
        else:
            filled = _is_filled(_find(data, key))

        if filled:
            filled_count += 1
        else:
            missing.append(key)

    score = round(filled_count / max(total_count, 1) * 100)
    return score, missing


# ---------------------------------------------------------------------------
# Оценка блоков (методика МЭР остаётся для таблицы детализации)
# ---------------------------------------------------------------------------

def _score_block1(data: dict) -> tuple[float, list[str]]:
    earned = 0
    missing = []
    for key, label, required, points in BLOCK1_FIELDS:
        if key == 'Координаты':
            geo_val = (
                _find(data, 'Координаты (полигон)') +
                _find(data, 'Координаты (точка)') +
                _find(data, 'Координаты (линия)')
            ).replace('ПУСТО', '').strip()
            filled = bool(geo_val)
        else:
            filled = _is_filled(_find(data, key))
        if filled:
            earned += points
        elif required:
            missing.append(label)
    score = round(earned / BLOCK1_MAX * 100)
    return score, missing


def _score_block2(data: dict, fmt: str) -> tuple[float, list[str]]:
    earned = 0
    missing = []
    is_zu = fmt == 'ЗУ'
    is_building = fmt == 'здание'
    for key, label, required, points in BLOCK2_FIELDS:
        if key in BLOCK2_ZU_KEYS and not is_zu:
            continue
        if key in BLOCK2_BUILDING_KEYS and not is_building:
            continue
        filled = _is_filled(_find(data, key))
        if filled:
            earned += points
        elif required:
            missing.append(label)
    active_max = sum(
        p for k, l, r, p in BLOCK2_FIELDS
        if not (k in BLOCK2_ZU_KEYS and not is_zu)
        and not (k in BLOCK2_BUILDING_KEYS and not is_building)
    )
    score = round(earned / max(active_max, 1) * 100)
    return score, missing


def _score_block3(data: dict) -> tuple[float, list[str]]:
    earned = 0
    missing = []
    for presence_key, sub_fields in BLOCK3_RESOURCES:
        presence_val = _find(data, presence_key)
        factor = _presence_factor(presence_val)
        for field_key, pts in sub_fields:
            if field_key == presence_key:
                earned += pts * factor
                if factor == 0:
                    missing.append(field_key)
            else:
                val = _find(data, field_key)
                if _is_filled(val):
                    earned += pts
                elif factor == 1.0:
                    missing.append(field_key)
    score = round(earned / BLOCK3_MAX * 100)
    return score, missing


def _score_block4(data: dict) -> tuple[float, list[str]]:
    earned = 0
    missing = []
    for key, label, required, points in BLOCK4_FIELDS:
        if _is_filled(_find(data, key)):
            earned += points
        elif required:
            missing.append(label)
    score = round(earned / BLOCK4_MAX * 100)
    return score, missing


def _score_block5(data: dict) -> tuple[float, list[str]]:
    earned = 0
    missing = []
    for key, label, required, points in BLOCK5_FIELDS:
        if _is_filled(_find(data, key)):
            earned += points
        elif required:
            missing.append(label)
    score = round(earned / BLOCK5_MAX * 100)
    return score, missing


# ---------------------------------------------------------------------------
# Публичный API
# ---------------------------------------------------------------------------

def analyze(data: dict) -> dict:
    fmt = _get_format(data)
    status = _get_status(data)
    площадка_id = _get_id(data)
    signed = _find(data, 'Состояние подписи')

    b1, m1 = _score_block1(data)
    b2, m2 = _score_block2(data, fmt)
    b3, m3 = _score_block3(data)
    b4, m4 = _score_block4(data)
    b5, m5 = _score_block5(data)

    total_mer = round(
        b1 * BLOCK1_WEIGHT +
        b2 * BLOCK2_WEIGHT +
        b3 * BLOCK3_WEIGHT +
        b4 * BLOCK4_WEIGHT +
        b5 * BLOCK5_WEIGHT
    )

    total_portal, missing_portal = _score_portal(data, fmt)

    if total_portal >= PORTAL_GREEN:
        category = '🟢 Готова к публикации'
        ready = 'да'
    elif total_portal >= PORTAL_YELLOW:
        category = '🟡 Требует доработки'
        ready = 'после доработки'
    else:
        category = '🟠 Значительные пробелы'
        ready = 'нет'

    all_missing = m1 + m2 + m3 + m4 + m5

    sms = None
    if total_portal < SMS_THRESHOLD and all_missing:
        items = '\n'.join(f'- {f}' for f in all_missing)
        sms = (
            f'Площадка {площадка_id} заполнена на {total_portal}%.\n'
            f'Просим добавить:\n'
            f'{items}\n'
            f'для увеличения процента заполняемости.'
        )

    return {
        'id': площадка_id,
        'format': fmt,
        'status': status,
        'blocks': {
            1: {'score': b1, 'weight': BLOCK1_WEIGHT, 'contribution': round(b1 * BLOCK1_WEIGHT), 'missing': m1},
            2: {'score': b2, 'weight': BLOCK2_WEIGHT, 'contribution': round(b2 * BLOCK2_WEIGHT), 'missing': m2},
            3: {'score': b3, 'weight': BLOCK3_WEIGHT, 'contribution': round(b3 * BLOCK3_WEIGHT), 'missing': m3},
            4: {'score': b4, 'weight': BLOCK4_WEIGHT, 'contribution': round(b4 * BLOCK4_WEIGHT), 'missing': m4},
            5: {'score': b5, 'weight': BLOCK5_WEIGHT, 'contribution': round(b5 * BLOCK5_WEIGHT), 'missing': m5},
        },
        'total': total_portal,
        'total_portal': total_portal,
        'total_mer': total_mer,
        'category': category,
        'ready': ready,
        'all_missing': all_missing,
        'missing_portal': missing_portal,
        'sms': sms,
        'signed': signed,
    }
