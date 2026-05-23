# ╔══════════════════════════════════════════════════════════════╗
# ║                      validators.py                           ║
# ║  Валидация и преобразование данных (ИНН, числа, файлы)      ║
# ╚══════════════════════════════════════════════════════════════╝

from db import ALLOWED_EXT


def _int(v):
    """
    Безопасное преобразование к int.
    Пустая строка или мусор → None.
    """
    try:
        return int(v) if v and str(v).strip() else None
    except Exception:
        return None


def _flt(v):
    """
    Безопасное преобразование к float.
    Разрешает запятую как десятичный разделитель.
    Пустая строка или мусор → None.
    """
    try:
        return float(str(v).replace(',', '.')) if v and str(v).strip() else None
    except Exception:
        return None


def allowed_file(fn: str) -> bool:
    """
    Проверяет, что у файла допустимое расширение из ALLOWED_EXT.
    Используется при загрузке файлов к обращению.
    """
    return '.' in fn and fn.rsplit('.', 1)[1].lower() in ALLOWED_EXT


def validate_inn(inn: str):
    """
    Проверяет корректность ИНН (юрлица 10 цифр, ИП/физлица 12 цифр).

    Возвращает кортеж:
      (True, None)       — если ИНН валиден,
      (False, 'empty')   — строка пустая,
      (False, 'format')  — есть нецифровые символы,
      (False, 'length')  — длина не 10 и не 12,
      (False, 'checksum')— не сходится контрольная цифра(ы).

    Мягкая логика (пустой ИНН не считаем фатальной ошибкой)
    реализуется на уровне контроллера.
    """
    inn = (inn or "").strip()
    if not inn:
        return False, "empty"

    if not inn.isdigit():
        return False, "format"

    n = len(inn)
    if n not in (10, 12):
        return False, "length"

    digits = [int(d) for d in inn]

    # Юрлицо: 10-значный ИНН
    if n == 10:
        weights = [2, 4, 10, 3, 5, 9, 4, 6, 8]
        s = sum(w * d for w, d in zip(weights, digits[:9]))
        r = (s % 11) % 10
        return (r == digits[9], None if r == digits[9] else "checksum")

    # Физлицо/ИП: 12-значный ИНН
    if n == 12:
        weights1 = [7, 2, 4, 10, 3, 5, 9, 4, 6, 8]
        s1 = sum(w * d for w, d in zip(weights1, digits[:10]))
        r1 = (s1 % 11) % 10
        if r1 != digits[10]:
            return False, "checksum"

        weights2 = [3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8]
        s2 = sum(w * d for w, d in zip(weights2, digits[:11]))
        r2 = (s2 % 11) % 10
        if r2 != digits[11]:
            return False, "checksum"

        return True, None