"""
Сервисный слой модуля квартальной актуализации данных Инвесткарты.

Модуль работает только с БД:
- формирует единственный план на квартальный период;
- создаёт строки по матрице получателей;
- хранит статусы, реквизиты писем и документы;
- не зависит от Flask, session, request и файлового хранилища.
"""

from __future__ import annotations

from datetime import date, datetime
import sqlite3
from typing import Any


RECORD_STATUSES = (
    "planned",
    "request_sent",
    "response_received",
    "overdue",
)

RECORD_STATUS_LABELS = {
    "planned": "Не направлено",
    "request_sent": "Запрос направлен",
    "response_received": "Получен ответ",
    "overdue": "Требуется уточнение",
}

DOCUMENT_TYPES = (
    "request",
    "response",
    "other",
)

DOCUMENT_TYPE_LABELS = {
    "request": "Запрос",
    "response": "Ответ",
    "other": "Иной документ",
}


def _as_iso_date(value: str | date) -> str:
    """Преобразует дату ДД.ММ.ГГГГ или date в ISO-формат ГГГГ-ММ-ДД."""
    if isinstance(value, date):
        return value.isoformat()

    if not isinstance(value, str):
        raise ValueError("Дата периода должна быть строкой ДД.ММ.ГГГГ.")

    try:
        return datetime.strptime(value.strip(), "%d.%m.%Y").date().isoformat()
    except ValueError as error:
        raise ValueError(
            "Дата периода должна иметь формат ДД.ММ.ГГГГ."
        ) from error


def parse_period_label(period_label: str) -> tuple[str, str]:
    """
    Разбирает период вида 01.01.2026-31.03.2026.

    Возвращает две ISO-даты: period_start, period_end.
    """
    if not isinstance(period_label, str):
        raise ValueError(
            "Период должен иметь формат ДД.ММ.ГГГГ-ДД.ММ.ГГГГ."
        )

    parts = [part.strip() for part in period_label.split("-")]
    if len(parts) != 2 or not all(parts):
        raise ValueError(
            "Период должен иметь формат ДД.ММ.ГГГГ-ДД.ММ.ГГГГ."
        )

    period_start = _as_iso_date(parts[0])
    period_end = _as_iso_date(parts[1])

    if period_start > period_end:
        raise ValueError(
            "Дата начала периода не может быть позже даты окончания."
        )

    return period_start, period_end


def format_period_label(period_start: str | date, period_end: str | date) -> str:
    """Возвращает отображаемый период вида 01.01.2026-31.03.2026."""
    start = datetime.strptime(
        _as_iso_date(period_start), "%Y-%m-%d"
    ).date()
    end = datetime.strptime(
        _as_iso_date(period_end), "%Y-%m-%d"
    ).date()
    return f"{start:%d.%m.%Y}-{end:%d.%m.%Y}"


def _require_user_exists(
    conn: sqlite3.Connection,
    user_id: int | None,
) -> None:
    """Проверяет существование автора действия, если ID передан."""
    if user_id is None:
        return

    row = conn.execute(
        "SELECT id FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()

    if row is None:
        raise ValueError("Пользователь-автор не найден.")


def _require_record_exists(
    conn: sqlite3.Connection,
    record_id: int,
) -> sqlite3.Row:
    """Возвращает строку актуализации или сообщает об ошибке."""
    row = conn.execute(
        """
        SELECT id, plan_run_id, status
        FROM investmap_update_records
        WHERE id = ?
        """,
        (record_id,),
    ).fetchone()

    if row is None:
        raise ValueError("Строка актуализации не найдена.")

    return row


def _get_plan_by_dates(
    conn: sqlite3.Connection,
    period_start: str,
    period_end: str,
) -> sqlite3.Row | None:
    """Находит существующий план по границам периода."""
    return conn.execute(
        """
        SELECT
            plans.id,
            plans.period_start,
            plans.period_end,
            plans.created_by_user_id,
            plans.note,
            plans.created_at,
            COALESCE(
                NULLIF(users.full_name, ''),
                users.username,
                ''
            ) AS created_by_name
        FROM investmap_update_plan_runs AS plans
        LEFT JOIN users
            ON users.id = plans.created_by_user_id
        WHERE plans.period_start = ?
          AND plans.period_end = ?
        """,
        (period_start, period_end),
    ).fetchone()


def list_update_categories(
    conn: sqlite3.Connection,
    active_only: bool = True,
) -> list[sqlite3.Row]:
    """Возвращает категории актуализации."""
    if active_only:
        where_clause = "WHERE is_active = 1"
    else:
        where_clause = ""

    return conn.execute(
        f"""
        SELECT
            id,
            code,
            name,
            sort_order,
            is_active,
            created_at,
            updated_at
        FROM investmap_update_categories
        {where_clause}
        ORDER BY sort_order, name COLLATE NOCASE, id
        """
    ).fetchall()


def list_update_organizations(
    conn: sqlite3.Connection,
    active_only: bool = True,
) -> list[sqlite3.Row]:
    """Возвращает организации-поставщики данных."""
    if active_only:
        where_clause = "WHERE is_active = 1"
    else:
        where_clause = ""

    return conn.execute(
        f"""
        SELECT
            id,
            name,
            is_active,
            created_at,
            updated_at
        FROM investmap_update_organizations
        {where_clause}
        ORDER BY name COLLATE NOCASE, id
        """
    ).fetchall()


def list_update_plans(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Возвращает планы с количеством строк и полученных ответов."""
    return conn.execute(
        """
        SELECT
            plans.id,
            plans.period_start,
            plans.period_end,
            plans.created_by_user_id,
            plans.note,
            plans.created_at,
            COALESCE(
                NULLIF(users.full_name, ''),
                users.username,
                ''
            ) AS created_by_name,
            COUNT(records.id) AS records_count,
            SUM(
                CASE
                    WHEN records.status = 'response_received' THEN 1
                    ELSE 0
                END
            ) AS received_count
        FROM investmap_update_plan_runs AS plans
        LEFT JOIN users
            ON users.id = plans.created_by_user_id
        LEFT JOIN investmap_update_records AS records
            ON records.plan_run_id = plans.id
        GROUP BY plans.id
        ORDER BY plans.period_start DESC, plans.period_end DESC, plans.id DESC
        """
    ).fetchall()


def get_update_plan(
    conn: sqlite3.Connection,
    period_label: str,
) -> sqlite3.Row | None:
    """Возвращает план по отображаемому периоду."""
    period_start, period_end = parse_period_label(period_label)
    return _get_plan_by_dates(conn, period_start, period_end)


def get_update_plan_by_id(
    conn: sqlite3.Connection,
    plan_run_id: int,
) -> sqlite3.Row | None:
    """Возвращает план по его ID."""
    return conn.execute(
        """
        SELECT
            plans.id,
            plans.period_start,
            plans.period_end,
            plans.created_by_user_id,
            plans.note,
            plans.created_at,
            COALESCE(
                NULLIF(users.full_name, ''),
                users.username,
                ''
            ) AS created_by_name
        FROM investmap_update_plan_runs AS plans
        LEFT JOIN users
            ON users.id = plans.created_by_user_id
        WHERE plans.id = ?
        """,
        (plan_run_id,),
    ).fetchone()


def _create_plan_records(
    conn: sqlite3.Connection,
    plan_run_id: int,
    user_id: int | None,
) -> int:
    """
    Наполняет новый план строками по активным правилам получателей.

    all_districts создаёт отдельную строку для каждого активного района.
    organization создаёт одну строку для закреплённой организации.
    """
    rules = conn.execute(
        """
        SELECT
            rules.id AS rule_id,
            rules.recipient_mode,
            categories.id AS category_id,
            organizations.id AS organization_id,
            organizations.name AS organization_name
        FROM investmap_update_recipient_rules AS rules
        JOIN investmap_update_categories AS categories
            ON categories.id = rules.category_id
        LEFT JOIN investmap_update_organizations AS organizations
            ON organizations.id = rules.organization_id
        WHERE rules.is_active = 1
          AND categories.is_active = 1
          AND (
              rules.recipient_mode = 'all_districts'
              OR (
                  rules.recipient_mode = 'organization'
                  AND organizations.is_active = 1
              )
          )
        ORDER BY categories.sort_order, categories.name, rules.id
        """
    ).fetchall()

    districts = conn.execute(
        """
        SELECT id, name
        FROM districts
        WHERE is_active = 1
        ORDER BY name COLLATE NOCASE, id
        """
    ).fetchall()

    created_count = 0

    for rule in rules:
        if rule["recipient_mode"] == "all_districts":
            for district in districts:
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO investmap_update_records (
                        plan_run_id,
                        category_id,
                        source_type,
                        district_id,
                        organization_id,
                        source_name_snapshot,
                        created_by_user_id,
                        updated_by_user_id
                    )
                    VALUES (?, ?, 'district', ?, NULL, ?, ?, ?)
                    """,
                    (
                        plan_run_id,
                        rule["category_id"],
                        district["id"],
                        district["name"],
                        user_id,
                        user_id,
                    ),
                )
                created_count += cursor.rowcount

        elif rule["recipient_mode"] == "organization":
            if rule["organization_id"] is None or not rule["organization_name"]:
                raise ValueError(
                    "Для правила организации не задана активная организация."
                )

            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO investmap_update_records (
                    plan_run_id,
                    category_id,
                    source_type,
                    district_id,
                    organization_id,
                    source_name_snapshot,
                    created_by_user_id,
                    updated_by_user_id
                )
                VALUES (?, ?, 'organization', NULL, ?, ?, ?, ?)
                """,
                (
                    plan_run_id,
                    rule["category_id"],
                    rule["organization_id"],
                    rule["organization_name"],
                    user_id,
                    user_id,
                ),
            )
            created_count += cursor.rowcount

        else:
            raise ValueError(
                f"Неподдерживаемый тип правила: {rule['recipient_mode']}."
            )

    return created_count


def create_or_get_update_plan(
    conn: sqlite3.Connection,
    period_label: str,
    created_by_user_id: int | None,
    note: str = "",
) -> tuple[sqlite3.Row, bool]:
    """
    Создаёт единственный план на период или возвращает существующий.

    Второе возвращаемое значение равно True, если план создан сейчас,
    и False, если для периода уже существовал план.
    """
    period_start, period_end = parse_period_label(period_label)
    _require_user_exists(conn, created_by_user_id)

    existing_plan = _get_plan_by_dates(conn, period_start, period_end)
    if existing_plan is not None:
        return existing_plan, False

    cursor = conn.execute(
        """
        INSERT INTO investmap_update_plan_runs (
            period_start,
            period_end,
            created_by_user_id,
            note
        )
        VALUES (?, ?, ?, ?)
        """,
        (
            period_start,
            period_end,
            created_by_user_id,
            (note or "").strip(),
        ),
    )

    plan_run_id = cursor.lastrowid

    try:
        _create_plan_records(conn, plan_run_id, created_by_user_id)
    except Exception:
        conn.execute(
            "DELETE FROM investmap_update_plan_runs WHERE id = ?",
            (plan_run_id,),
        )
        raise

    plan = get_update_plan_by_id(conn, plan_run_id)
    if plan is None:
        raise RuntimeError("Не удалось прочитать созданный план.")

    return plan, True


def list_update_records(
    conn: sqlite3.Connection,
    plan_run_id: int,
) -> list[sqlite3.Row]:
    """Возвращает строки плана с категорией, авторами и количеством файлов."""
    return conn.execute(
        """
        SELECT
            records.id,
            records.plan_run_id,
            records.category_id,
            categories.code AS category_code,
            categories.name AS category_name,
            categories.sort_order AS category_sort_order,
            records.source_type,
            records.district_id,
            records.organization_id,
            records.source_name_snapshot,
            records.request_letter_number,
            records.response_letter_number,
            records.changes_count,
            records.status,
            records.note,
            records.created_by_user_id,
            records.updated_by_user_id,
            records.created_at,
            records.updated_at,
            COALESCE(
                NULLIF(created_by.full_name, ''),
                created_by.username,
                ''
            ) AS created_by_name,
            COALESCE(
                NULLIF(updated_by.full_name, ''),
                updated_by.username,
                ''
            ) AS updated_by_name,
            SUM(
                CASE
                    WHEN documents.document_type = 'request' THEN 1
                    ELSE 0
                END
            ) AS request_documents_count,
            SUM(
                CASE
                    WHEN documents.document_type = 'response' THEN 1
                    ELSE 0
                END
            ) AS response_documents_count,
            SUM(
                CASE
                    WHEN documents.document_type = 'other' THEN 1
                    ELSE 0
                END
            ) AS other_documents_count
        FROM investmap_update_records AS records
        JOIN investmap_update_categories AS categories
            ON categories.id = records.category_id
        LEFT JOIN users AS created_by
            ON created_by.id = records.created_by_user_id
        LEFT JOIN users AS updated_by
            ON updated_by.id = records.updated_by_user_id
        LEFT JOIN investmap_update_documents AS documents
            ON documents.record_id = records.id
        WHERE records.plan_run_id = ?
        GROUP BY records.id
        ORDER BY
            categories.sort_order,
            categories.name COLLATE NOCASE,
            CASE records.source_type
                WHEN 'district' THEN 0
                ELSE 1
            END,
            records.source_name_snapshot COLLATE NOCASE,
            records.id
        """,
        (plan_run_id,),
    ).fetchall()


def update_record_status(
    conn: sqlite3.Connection,
    record_id: int,
    status: str,
    updated_by_user_id: int | None,
    note: str | None = None,
) -> sqlite3.Row:
    """Изменяет статус строки и при необходимости её комментарий."""
    if status not in RECORD_STATUSES:
        allowed = ", ".join(RECORD_STATUSES)
        raise ValueError(f"Недопустимый статус. Разрешены: {allowed}.")

    _require_user_exists(conn, updated_by_user_id)
    _require_record_exists(conn, record_id)

    if note is None:
        cursor = conn.execute(
            """
            UPDATE investmap_update_records
            SET
                status = ?,
                updated_by_user_id = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (status, updated_by_user_id, record_id),
        )
    else:
        cursor = conn.execute(
            """
            UPDATE investmap_update_records
            SET
                status = ?,
                note = ?,
                updated_by_user_id = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                status,
                note.strip(),
                updated_by_user_id,
                record_id,
            ),
        )

    if cursor.rowcount != 1:
        raise RuntimeError("Не удалось обновить статус строки.")

    row = conn.execute(
        """
        SELECT
            records.id,
            records.plan_run_id,
            records.status,
            records.note,
            records.updated_by_user_id,
            records.updated_at,
            COALESCE(
                NULLIF(users.full_name, ''),
                users.username,
                ''
            ) AS updated_by_name
        FROM investmap_update_records AS records
        LEFT JOIN users
            ON users.id = records.updated_by_user_id
        WHERE records.id = ?
        """,
        (record_id,),
    ).fetchone()

    if row is None:
        raise RuntimeError("Не удалось прочитать обновлённую строку.")

    return row


def update_record_details(
    conn: sqlite3.Connection,
    record_id: int,
    updated_by_user_id: int | None,
    request_letter_number: str | None = None,
    response_letter_number: str | None = None,
    changes_count: int | None = None,
    note: str | None = None,
) -> sqlite3.Row:
    """Изменяет реквизиты писем, число изменений и комментарий строки."""
    _require_user_exists(conn, updated_by_user_id)
    _require_record_exists(conn, record_id)

    assignments: list[str] = []
    values: list[Any] = []

    if request_letter_number is not None:
        assignments.append("request_letter_number = ?")
        values.append(request_letter_number.strip())

    if response_letter_number is not None:
        assignments.append("response_letter_number = ?")
        values.append(response_letter_number.strip())

    if changes_count is not None:
        if not isinstance(changes_count, int) or changes_count < 0:
            raise ValueError(
                "Количество изменений должно быть целым неотрицательным числом."
            )
        assignments.append("changes_count = ?")
        values.append(changes_count)

    if note is not None:
        assignments.append("note = ?")
        values.append(note.strip())

    if not assignments:
        raise ValueError("Не переданы данные для обновления строки.")

    assignments.extend(
        (
            "updated_by_user_id = ?",
            "updated_at = CURRENT_TIMESTAMP",
        )
    )
    values.extend((updated_by_user_id, record_id))

    cursor = conn.execute(
        f"""
        UPDATE investmap_update_records
        SET {", ".join(assignments)}
        WHERE id = ?
        """,
        values,
    )

    if cursor.rowcount != 1:
        raise RuntimeError("Не удалось обновить данные строки.")

    row = conn.execute(
        """
        SELECT
            records.id,
            records.plan_run_id,
            records.request_letter_number,
            records.response_letter_number,
            records.changes_count,
            records.note,
            records.updated_by_user_id,
            records.updated_at,
            COALESCE(
                NULLIF(users.full_name, ''),
                users.username,
                ''
            ) AS updated_by_name
        FROM investmap_update_records AS records
        LEFT JOIN users
            ON users.id = records.updated_by_user_id
        WHERE records.id = ?
        """,
        (record_id,),
    ).fetchone()

    if row is None:
        raise RuntimeError("Не удалось прочитать обновлённую строку.")

    return row


def list_record_documents(
    conn: sqlite3.Connection,
    record_id: int,
) -> list[sqlite3.Row]:
    """Возвращает все документы строки актуализации."""
    _require_record_exists(conn, record_id)

    return conn.execute(
        """
        SELECT
            documents.id,
            documents.record_id,
            documents.document_type,
            documents.original_name,
            documents.stored_name,
            documents.uploaded_by_user_id,
            documents.uploaded_at,
            COALESCE(
                NULLIF(users.full_name, ''),
                users.username,
                ''
            ) AS uploaded_by_name
        FROM investmap_update_documents AS documents
        LEFT JOIN users
            ON users.id = documents.uploaded_by_user_id
        WHERE documents.record_id = ?
        ORDER BY documents.uploaded_at, documents.id
        """,
        (record_id,),
    ).fetchall()


def add_record_document(
    conn: sqlite3.Connection,
    record_id: int,
    document_type: str,
    original_name: str,
    stored_name: str,
    uploaded_by_user_id: int | None,
) -> sqlite3.Row:
    """
    Регистрирует метаданные уже сохраненного файла.

    Физическое сохранение файла выполняет будущий маршрут.
    """
    if document_type not in DOCUMENT_TYPES:
        allowed = ", ".join(DOCUMENT_TYPES)
        raise ValueError(
            f"Недопустимый тип документа. Разрешены: {allowed}."
        )

    original_name = (original_name or "").strip()
    stored_name = (stored_name or "").strip()

    if not original_name:
        raise ValueError("Не указано исходное имя файла.")

    if not stored_name:
        raise ValueError("Не указано имя файла в хранилище.")

    _require_user_exists(conn, uploaded_by_user_id)
    _require_record_exists(conn, record_id)

    try:
        cursor = conn.execute(
            """
            INSERT INTO investmap_update_documents (
                record_id,
                document_type,
                original_name,
                stored_name,
                uploaded_by_user_id
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                record_id,
                document_type,
                original_name,
                stored_name,
                uploaded_by_user_id,
            ),
        )
    except sqlite3.IntegrityError as error:
        if "stored_name" in str(error).lower():
            raise ValueError(
                "Документ с таким именем в хранилище уже зарегистрирован."
            ) from error
        raise

    row = conn.execute(
        """
        SELECT
            documents.id,
            documents.record_id,
            documents.document_type,
            documents.original_name,
            documents.stored_name,
            documents.uploaded_by_user_id,
            documents.uploaded_at,
            COALESCE(
                NULLIF(users.full_name, ''),
                users.username,
                ''
            ) AS uploaded_by_name
        FROM investmap_update_documents AS documents
        LEFT JOIN users
            ON users.id = documents.uploaded_by_user_id
        WHERE documents.id = ?
        """,
        (cursor.lastrowid,),
    ).fetchone()

    if row is None:
        raise RuntimeError("Не удалось прочитать добавленный документ.")

    return row


def delete_record_document(
    conn: sqlite3.Connection,
    document_id: int,
) -> sqlite3.Row:
    """
    Удаляет метаданные документа и возвращает их.

    Физическое удаление файла из хранилища выполняет будущий маршрут
    после успешного удаления записи из БД.
    """
    row = conn.execute(
        """
        SELECT
            id,
            record_id,
            document_type,
            original_name,
            stored_name,
            uploaded_by_user_id,
            uploaded_at
        FROM investmap_update_documents
        WHERE id = ?
        """,
        (document_id,),
    ).fetchone()

    if row is None:
        raise ValueError("Документ не найден.")

    cursor = conn.execute(
        "DELETE FROM investmap_update_documents WHERE id = ?",
        (document_id,),
    )

    if cursor.rowcount != 1:
        raise RuntimeError("Не удалось удалить документ из базы данных.")

    return row
