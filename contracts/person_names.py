# -*- coding: utf-8 -*-
"""ФИО подписанта в договоре.

В карточке ФИО хранится в именительном падеже «Фамилия Имя Отчество».
- genitive_fio: для преамбулы («в лице генерального директора
  Миронова Дмитрия Васильевича»);
- signature_name: для блока подписи («Д.В.Миронов», без пробела).

Склонение делает библиотека Petrovich (requirements.txt). Если её нет
или ФИО нетипичное, функция возвращает текст без изменений,
формирование договора не падает.
"""
import re

_INITIALS = re.compile(r'(?:[^\W\d_]\.)+')

_inflector = None


def _parts(fio) -> list:
    return (fio or '').split()


def _gender_key(patronymic: str):
    """'male' / 'female' по окончанию отчества; None, если неясно."""
    low = patronymic.casefold()
    if low.endswith(('ич', 'ыч')):
        return 'male'
    if low.endswith('на'):
        return 'female'
    return None


def genitive_fio(fio) -> str:
    """ФИО в родительном падеже; при сбое — введённый текст."""
    global _inflector
    parts = _parts(fio)
    plain = ' '.join(parts)
    if len(parts) not in (2, 3):
        return plain
    if any(_INITIALS.fullmatch(p) for p in parts[1:]):
        return plain
    try:
        from petrovich import Case, Gender, Petrovich
        if _inflector is None:
            _inflector = Petrovich()
        gender = None
        if len(parts) == 3:
            key = _gender_key(parts[2])
            gender = {'male': Gender.MALE, 'female': Gender.FEMALE}.get(key)
        args = (Case.GENITIVE,) + ((gender,) if gender else ())
        result = [
            _inflector.lastname(parts[0], *args),
            _inflector.firstname(parts[1], *args),
        ]
        if len(parts) == 3:
            result.append(_inflector.middlename(parts[2], *args))
        return ' '.join(result)
    except (ImportError, ValueError, TypeError):
        return plain


def signature_name(fio) -> str:
    """«Миронов Дмитрий Васильевич» -> «Д.В.Миронов» (без пробела).

    Два слова дают одну инициалу, одно слово остаётся как есть.
    Инициалы, уже введённые в карточке («Миронов Д.В.»), сохраняются.
    Берутся не более двух слов после фамилии.
    """
    parts = _parts(fio)
    if len(parts) < 2:
        return ' '.join(parts)
    initials = ''
    for token in parts[1:3]:
        if _INITIALS.fullmatch(token):
            initials += token.upper()
        else:
            initials += token[0].upper() + '.'
    return f"{initials}{parts[0]}"
