# ╔═══════════════════════════════════════════════════════════════════════╗
# ║                          documents.py                                     ║
# ║  Файлы договоров (версии), генерация DOCX из шаблона.                   ║
# ║                                                                        ║
# ║  Шаблон: contracts/doc_templates/council_contract.docx — проект по     ║
# ║  образцу DD-obrazets.doc с метками {{...}}; юридическая вычитка        ║
# ║  обязательна до использования в работе.                                ║
# ╚══════════════════════════════════════════════════════════════════════╝

import hashlib
import os
import re
import uuid
from datetime import datetime

import docx

from paths import UPLOADS_DIR
from . import person_names, positions, pricing, service_texts

CONTRACTS_UPLOAD_DIR = os.path.join(UPLOADS_DIR, 'contracts')

ALLOWED_EXTENSIONS = {
    'pdf', 'doc', 'docx', 'xls', 'xlsx',
    'dwg', 'dxf', 'zip', 'rar', 'jpg', 'jpeg', 'png',
}

CATEGORY_LABELS = {
    'project':         'Проект договора',
    'remark':          'Замечания заказчика',
    'directum':        'Версия из Directum',
    'signed_contract': 'Подписанный договор',
    'guarantee':       'Гарантийное письмо',
    'invoice':         'Счёт',
    'payment':         'Платёжный документ',
    'incoming':        'Исходные документы заказчика',
    'memo':            'Служебная записка',
    'result':          'Результат услуг',
    'act':             'Акт / УПД',
    'decision':        'Решение Совета / КЗЗ',
    'other':           'Иное',
}

TEMPLATE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    'doc_templates', 'council_contract.docx'
)


def allowed_file(filename: str) -> bool:
    return ('.' in filename
            and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS)


def max_file_bytes(conn) -> int:
    row = conn.execute(
        "SELECT value FROM contract_settings WHERE key='max_file_mb'"
    ).fetchone()
    mb = int(row['value']) if row and row['value'] else 200
    return mb * 1024 * 1024


def save_version(conn, contract_id: int, category: str, file_obj, user_id: int,
                 comment: str = None):
    """Сохраняет новую версию файла; прежняя версия перестаёт быть текущей.

    Возвращает id записи или (None, причина).
    """
    if not file_obj or not file_obj.filename:
        return None, 'Файл не выбран'
    if not allowed_file(file_obj.filename):
        return None, ('Недопустимый формат. Разрешены: '
                      + ', '.join(sorted(ALLOWED_EXTENSIONS)))
    ext = file_obj.filename.rsplit('.', 1)[1].lower()
    stored_name = f"{uuid.uuid4().hex}.{ext}"
    folder = os.path.join(CONTRACTS_UPLOAD_DIR, str(contract_id))
    os.makedirs(folder, exist_ok=True)
    save_path = os.path.join(folder, stored_name)

    data = file_obj.read()
    if len(data) > max_file_bytes(conn):
        return None, 'Файл превышает допустимый размер'
    with open(save_path, 'wb') as fh:
        fh.write(data)

    version = 1 + conn.execute(
        """SELECT COALESCE(MAX(version), 0) FROM contract_files
           WHERE contract_id=? AND category=?""",
        (contract_id, category)
    ).fetchone()[0]
    conn.execute(
        "UPDATE contract_files SET is_current=0 "
        "WHERE contract_id=? AND category=?",
        (contract_id, category)
    )
    cur = conn.execute(
        """INSERT INTO contract_files
           (contract_id, category, version, is_current, orig_name,
            stored_name, user_id, sha256, size_bytes, comment)
           VALUES (?,?,?,1,?,?,?,?,?,?)""",
        (contract_id, category, version, file_obj.filename, stored_name,
         user_id, hashlib.sha256(data).hexdigest(), len(data), comment)
    )
    return cur.lastrowid, None


def file_abs_path(row) -> str:
    return os.path.join(
        CONTRACTS_UPLOAD_DIR, str(row['contract_id']), row['stored_name']
    )


# ─── ФОРМАТИРОВАНИЕ ДЕНЕГ И СРОКОВ ДЛЯ ДОКУМЕНТА ─────────────────────────────

def _day_words(n: int, day_type: str) -> str:
    word = 'рабочих' if day_type == 'work' else 'календарных'
    n_word = {3: 'Три', 5: 'Пять', 7: 'Семь', 10: 'Десяти', 14: 'Четырнадцати',
              30: 'Тридцати'}.get(n, str(n))
    return f"{n} ({n_word}) {word} дней"


def money_in_doc(total_kop: int) -> str:
    """«160 000 (Сто шестьдесят тысяч) рублей 00 копеек»."""
    rub, kop = divmod(int(total_kop), 100)
    full = pricing.rub_in_words(total_kop)
    m = re.match(r'^(.*?) (руб\w+) (\d\d) (копе\w+)$', full)
    if not m:
        return f"{rub:,} руб. {kop:02d} коп.".replace(',', ' ')
    words = m.group(1)
    words = words[0].upper() + words[1:]
    rub_s = f"{rub:,}".replace(',', ' ')
    return f"{rub_s} ({words}) {m.group(2)} {m.group(3)} {m.group(4)}"


# ─── ГЕНЕРАЦИЯ ДОГОВОРА ──────────────────────────────────────────────────────

def _preamble(contract) -> str:
    """Преамбула в зависимости от типа заказчика."""
    full = contract['customer_full_name'] or ''
    short = contract['customer_short_name'] or ''
    sign_name = contract['signatory_name'] or ''
    sign_pos = contract['signatory_position'] or ''
    sign_basis = contract['signatory_basis'] or 'Устава'
    if contract['customer_type'] == 'individual':
        return (
            f"Гражданин Российской Федерации {sign_name} "
            f"({full}), именуемый в дальнейшем «Заказчик», "
            f"действующий на основании {sign_basis}, с одной стороны, "
        )
    if contract['customer_type'] == 'ip':
        return (
            f"Индивидуальный предприниматель {sign_name or full} "
            f"({short or full}), именуемый в дальнейшем «Заказчик», "
            f"действующий на основании {sign_basis}, с одной стороны, "
        )
    sign_name_gen = person_names.genitive_fio(sign_name)
    return (
        f"{full} ({short}), именуемое в дальнейшем «Заказчик», "
        f"в лице {sign_pos} {sign_name_gen}, действующего на основании "
        f"{sign_basis}, с одной стороны, "
    )


def _payment_clause(contract) -> str:
    n = contract['payment_days']
    days = _day_words(n, contract['payment_day_type'] or 'work')
    scheme = contract['payment_scheme']
    if scheme == 'postpay':
        return (
            "2.2. Заказчик обязуется оплатить Услуги в течение "
            f"{days} с даты подписания Акта путем перечисления денежных "
            "средств на счет Исполнителя на основании счета, "
            "выставленного Исполнителем."
        )
    if scheme == 'prepay_partial':
        return (
            "2.2. Заказчик обязуется произвести предварительную оплату Услуг "
            f"в размере, согласованном Сторонами, в течение {days} "
            "с даты подписания Договора; оставшуюся часть — в течение "
            f"{days} с даты подписания Акта."
        )
    if scheme == 'guarantee_letter':
        return (
            "2.2. Оплата Услуг производится в течение "
            f"{days} с даты подписания Договора на основании "
            "гарантийного письма Заказчика."
        )
    return (
        "2.2. Заказчик обязуется произвести предварительную оплату Услуг "
        "в размере 100% от общей цены Услуг в течение "
        f"{days} с даты подписания Договора путем перечисления денежных "
        "средств на счет Исполнителя на основании счета, выставленного "
        "Исполнителем."
    )


def _duration_clause(contract) -> str:
    n = contract['duration_days']
    days = _day_words(n, contract['duration_day_type'] or 'work')
    return (
        "3.1. Исполнитель обязуется оказать Услугу, предусмотренную "
        f"Договором, в течение {days} с даты получения необходимых "
        "документов и информации в соответствии с п. 1.4. Договора. "
        "Исполнение условий Договора осуществляется при условии исполнения "
        "обязательства Заказчика по оплате в соответствии с п. 2.2. "
        "Договора. В случае неисполнения Заказчиком обязательства по оплате "
        "в соответствии с п. 2.2. Договора Исполнитель не считается "
        "нарушившим обязательство по оказанию Услуг в срок, "
        "предусмотренный Договором."
    )


_CUSTOMER_KIND = {
    'legal': 'Юридическое лицо',
    'ip': 'Индивидуальный предприниматель',
    'individual': 'Физическое лицо',
}


def _customer_lines(contract, full=False) -> list:
    """Строки с данными заказчика; пустые поля пропускаются.

    full=False — реквизиты для подписной части договора;
    full=True  — все данные заказчика (для приложений).
    """
    lines = [contract['customer_full_name'] or '']
    if full:
        kind = _CUSTOMER_KIND.get(contract['customer_type'])
        if kind:
            lines.append(f"Тип заказчика: {kind}")
        short = contract['customer_short_name']
        if short and short != contract['customer_full_name']:
            lines.append(f"Сокращённое наименование: {short}")
    pairs = [
        ('ИНН', contract['customer_inn']),
        ('КПП', contract['customer_kpp']),
        ('ОГРН/ОГРНИП', contract['customer_ogrn']),
        ('Юридический адрес', contract['customer_address']),
    ]
    if full:
        signatory = ' '.join(
            x for x in (contract['signatory_position'],
                        contract['signatory_name']) if x
        )
        pairs.append(('Представитель', signatory))
        pairs.append(('Действует на основании', contract['signatory_basis']))
    pairs += [
        ('Контактное лицо', contract['contact_name']),
        ('Телефон', contract['contact_phone']),
        ('E-mail', contract['contact_email']),
    ]
    lines += [f"{label}: {value}" for label, value in pairs if value]
    return [l for l in lines if l]


def _requisites_block(contract) -> str:
    return '\n'.join(_customer_lines(contract))


def _customer_info_block(contract) -> str:
    return '\n'.join(_customer_lines(contract, full=True))


def _field(contract, name):
    """Значение поля карточки; None, если колонки ещё нет в базе."""
    return contract[name] if name in contract.keys() else None


def _customer_details_block(contract) -> str:
    """Реквизиты заказчика без названия (метка {{customer_details}}).

    Адреса, ОГРН, ИНН/КПП, банковские реквизиты, e-mail организации.
    Пустые поля пропускаются; юридический и фактический адреса
    выводятся отдельными строками даже если совпадают.
    """
    lines = []
    legal = (_field(contract, 'customer_address') or '').strip()
    fact = (_field(contract, 'customer_fact_address') or '').strip()
    if legal:
        lines.append(f"Юридический адрес: {legal}")
    if fact:
        lines.append(f"Фактический адрес: {fact}")
    ogrn = _field(contract, 'customer_ogrn')
    if ogrn:
        label = 'ОГРНИП' if _field(contract, 'customer_type') == 'ip' else 'ОГРН'
        lines.append(f"{label}: {ogrn}")
    tax = [f"{label} {value}" for label, value in (
        ('ИНН', _field(contract, 'customer_inn')),
        ('КПП', _field(contract, 'customer_kpp')),
    ) if value]
    if tax:
        lines.append(', '.join(tax))
    account = _field(contract, 'customer_bank_account')
    bank_name = _field(contract, 'customer_bank_name')
    bank = []
    if account:
        bank.append(f"р/с {account}" + (f" в {bank_name}" if bank_name else ''))
    elif bank_name:
        bank.append(f"Банк: {bank_name}")
    if _field(contract, 'customer_bank_bik'):
        bank.append(f"БИК {_field(contract, 'customer_bank_bik')}")
    if _field(contract, 'customer_bank_corr_account'):
        bank.append(f"к/с {_field(contract, 'customer_bank_corr_account')}")
    if bank:
        lines.append(',\n'.join(bank))
    if _field(contract, 'customer_email'):
        lines.append(f"e-mail: {_field(contract, 'customer_email')}")
    return '\n'.join(lines)


def _notice_email(contract) -> str:
    """E-mail для п. 1.4: контактного лица, иначе организации."""
    return (
        _field(contract, 'contact_email')
        or _field(contract, 'customer_email')
        or '_______'
    )


def _replace_paragraph_text(p, new_text):
    for run in list(p.runs[1:]):
        run.text = ''
    if p.runs:
        p.runs[0].text = new_text
    else:
        p.add_run(new_text)


def _iter_all_paragraphs(document):
    for p in document.paragraphs:
        yield p
    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                for p in cell.paragraphs:
                    yield p


def generate_contract_docx(conn, contract, services) -> str:
    """Заполняет шаблон договора данными карточки.

    Возвращает путь к созданному файлу во временной папке uploads/tmp.
    Файл — проект: исполнитель вправе править его в Word и загрузить
    новой версией категории «Проект договора».
    """
    if not os.path.exists(TEMPLATE_PATH):
        raise FileNotFoundError(
            'Шаблон договора не найден: ' + TEMPLATE_PATH
        )
    document = docx.Document(TEMPLATE_PATH)

    vat_percent = contract['vat_percent'] or '22'
    total_kop = contract['total_kop'] or 0
    vat_kop = pricing.vat_from_total(total_kop, vat_percent)

    service_lines = [
        f"- {text}" + (f" ({qty})" if qty else '')
        for text, qty in service_texts.contract_service_items(services)
    ]
    if not service_lines:
        service_lines = ['- Услуги не выбраны']

    # Метки для Word. Новые: {{customer_details}} — реквизиты заказчика
    # без названия, {{customer_signatory_position}} и {{customer_signatory_name}}
    # — должность и ФИО подписанта; ранее: {{customer_info}},
    # {{customer_full_name}}, {{customer_short_name}}, {{object_address}}.
    # Должность в подписи — в именительном падеже из positions,
    # ФИО в подписи — инициалы перед фамилией (person_names).
    labels = {
        '{{customer_preamble}}': _preamble(contract),
        '{{price_clause}}': (
            f"2.1. Общая цена Услуг составляет "
            f"{money_in_doc(total_kop)} и включает в себя "
            f"НДС {vat_percent}% в размере {money_in_doc(vat_kop)}."
        ),
        '{{payment_clause}}': _payment_clause(contract),
        '{{duration_clause}}': _duration_clause(contract),
        '{{customer_requisites}}': _requisites_block(contract),
        '{{customer_info}}': _customer_info_block(contract),
        '{{customer_details}}': _customer_details_block(contract),
        '{{customer_full_name}}': contract['customer_full_name'] or '',
        '{{customer_short_name}}': (
            contract['customer_short_name']
            or contract['customer_full_name'] or ''
        ),
        '{{object_address}}': contract['address'] or '____________',
        '{{customer_signatory}}': (
            f"{contract['signatory_position'] or ''} "
            f"{contract['signatory_name'] or ''}"
        ),
        '{{customer_signatory_position}}': positions.signature_position(
            contract['signatory_position']
        ),
        '{{customer_signatory_name}}': person_names.signature_name(
            contract['signatory_name']
        ),
        '{{reg_number}}': contract['reg_number'] or '___',
        '{{doc_date}}': (
            '«___» __________ ' +
            str(datetime.utcnow().year) + ' г.'
        ),
        '{{customer_email}}': _notice_email(contract),
    }

    marker_paragraph = None
    for p in _iter_all_paragraphs(document):
        text = p.text
        if not text:
            continue
        if '{{services_list}}' in text:
            marker_paragraph = p
            continue
        replaced = text
        for label, value in labels.items():
            if label in replaced:
                replaced = replaced.replace(label, value)
        # e-mail заказчика: поле с прочерками в п. 1.4
        replaced = re.sub(
            r'Заказчика: _+ на',
            f"Заказчика: {_notice_email(contract)} на",
            replaced
        )
        if replaced != text:
            _replace_paragraph_text(p, replaced)

    if marker_paragraph is not None:
        style = marker_paragraph.style
        anchor = marker_paragraph
        for line in service_lines:
            anchor = anchor.insert_paragraph_before(line, style=style)
        remove_el = marker_paragraph._element
        remove_el.getparent().remove(remove_el)

    out_dir = os.path.join(UPLOADS_DIR, 'tmp')
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(
        out_dir,
        f"dogovor_{contract['inner_number'] or contract['id']}_"
        f"{datetime.utcnow().strftime('%Y%m%d%H%M%S')}.docx"
    )
    document.save(out_path)
    return out_path
