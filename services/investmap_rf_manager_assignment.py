"""Сопоставление муниципалитета карточки Инвесткарты РФ с управляющим."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any


ISSUE_TYPE_UNMATCHED = "unmatched"
ISSUE_TYPE_AMBIGUOUS = "ambiguous"

MATCH_STATUS_MATCHED = "matched"
MATCH_STATUS_UNMATCHED = "unmatched"
MATCH_STATUS_AMBIGUOUS = "ambiguous"
MATCH_STATUS_MANUAL = "manual"
KULIBIN_MANAGER_NAME = "Земсков Александр Николаевич"
KULIBIN_MARKER = "кулибин"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_municipality(value: Any) -> str:
    """
    Нормализует муниципальное образование для безопасного поиска.

    Все варианты наименования муниципального округа город Бор приводятся
    к каноническому ключу «бор», чтобы применялось единое правило назначения
    территориального управляющего.
    """
    text = "" if value is None else str(value)
    text = re.sub(r"\s+", " ", text).strip().casefold()

    compact = re.sub(r"[.,()«»\"'\\-]+", " ", text)
    compact = re.sub(r"\s+", " ", compact).strip()

    bor_aliases = {
        "бор",
        "город бор",
        "г о город бор",
        "го город бор",
        "городской округ город бор",
        "муниципальный округ город бор",
        "м о город бор",
        "мо город бор",
    }

    if compact in bor_aliases:
        return "бор"

    return text


def get_card_municipality(card) -> str:
    """Возвращает муниципальное образование из API-payload карточки."""
    payload = getattr(card, "payload", None)

    if not isinstance(payload, dict):
        return ""

    value = payload.get("municipality")
    return "" if value is None else str(value).strip()


def is_kulibin_special_economic_zone(card) -> bool:
    """Проверяет, относится ли API-карточка к ОЭЗ ППТ «Кулибин»."""
    payload = getattr(card, "payload", None)

    if not isinstance(payload, dict):
        return False

    for key in (
        "preferentialBusinessLink",
        "businessEnvironmentPreferentialLink",
        "businessEnvironmentPrivilegedLink",
    ):
        value = payload.get(key)

        if isinstance(value, dict):
            name = value.get("name")
        else:
            name = value

        if KULIBIN_MARKER in normalize_municipality(name):
            return True

    return False


def _get_existing_assignment(conn, global_id: int):
    return conn.execute(
        """
        SELECT *
        FROM investmap_rf_card_manager_assignments
        WHERE global_id = ?
        """,
        (global_id,),
    ).fetchone()


def _find_matching_rules(conn, municipality_normalized: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT
            id,
            municipality_name,
            municipality_normalized,
            manager_name,
            match_mode
        FROM investmap_rf_municipality_manager_rules
        WHERE is_active = 1
        ORDER BY LENGTH(municipality_normalized) DESC, id ASC
        """
    ).fetchall()

    matches: list[dict[str, Any]] = []

    for row in rows:
        rule = dict(row)
        rule_key = normalize_municipality(rule["municipality_normalized"])

        if not rule_key:
            continue

        if rule["match_mode"] == "exact":
            is_match = municipality_normalized == rule_key
        else:
            is_match = rule_key in municipality_normalized

        if is_match:
            matches.append(rule)

    return matches


def _resolve_open_issues(
    conn,
    *,
    global_id: int,
    resolved_by_user_id: int | None = None,
) -> None:
    conn.execute(
        """
        UPDATE investmap_rf_manager_match_issues
        SET
            is_resolved = 1,
            resolved_by_user_id = COALESCE(?, resolved_by_user_id),
            resolved_at_utc = ?,
            updated_at_utc = ?
        WHERE global_id = ?
          AND is_resolved = 0
        """,
        (
            resolved_by_user_id,
            _utc_now(),
            _utc_now(),
            global_id,
        ),
    )


def _upsert_issue(
    conn,
    *,
    global_id: int,
    municipality_raw: str,
    municipality_normalized: str,
    issue_type: str,
    details: str,
) -> tuple[dict[str, Any], bool]:
    existing = conn.execute(
        """
        SELECT *
        FROM investmap_rf_manager_match_issues
        WHERE global_id = ?
          AND municipality_normalized = ?
          AND issue_type = ?
          AND is_resolved = 0
        """,
        (
            global_id,
            municipality_normalized,
            issue_type,
        ),
    ).fetchone()

    now = _utc_now()

    if existing is not None:
        conn.execute(
            """
            UPDATE investmap_rf_manager_match_issues
            SET
                municipality_raw = ?,
                details = ?,
                updated_at_utc = ?
            WHERE id = ?
            """,
            (
                municipality_raw,
                details,
                now,
                existing["id"],
            ),
        )
        row = conn.execute(
            """
            SELECT *
            FROM investmap_rf_manager_match_issues
            WHERE id = ?
            """,
            (existing["id"],),
        ).fetchone()
        return dict(row), False

    cursor = conn.execute(
        """
        INSERT INTO investmap_rf_manager_match_issues (
            global_id,
            municipality_raw,
            municipality_normalized,
            issue_type,
            details,
            is_resolved,
            created_at_utc,
            updated_at_utc
        )
        VALUES (?, ?, ?, ?, ?, 0, ?, ?)
        """,
        (
            global_id,
            municipality_raw,
            municipality_normalized,
            issue_type,
            details,
            now,
            now,
        ),
    )
    row = conn.execute(
        """
        SELECT *
        FROM investmap_rf_manager_match_issues
        WHERE id = ?
        """,
        (cursor.lastrowid,),
    ).fetchone()
    return dict(row), True


def _notify_admins(conn, *, message: str, link: str) -> None:
    admins = conn.execute(
        """
        SELECT id
        FROM users
        WHERE role = 'admin'
          AND is_active = 1
        """
    ).fetchall()

    now = _utc_now()

    for admin in admins:
        conn.execute(
            """
            INSERT INTO notifications (
                user_id,
                message,
                link,
                is_read,
                created_at
            )
            VALUES (?, ?, ?, 0, ?)
            """,
            (
                admin["id"],
                message,
                link,
                now,
            ),
        )


def _log_system_action(conn, *, action: str, detail: str) -> None:
    conn.execute(
        """
        INSERT INTO activity_log (
            user_id,
            action,
            detail,
            created_at
        )
        VALUES (NULL, ?, ?, ?)
        """,
        (
            action,
            detail,
            _utc_now(),
        ),
    )


def _upsert_assignment(
    conn,
    *,
    global_id: int,
    municipality_raw: str,
    municipality_normalized: str,
    manager_name: str | None,
    rule_id: int | None,
    assignment_source: str,
    match_status: str,
    assigned_by_user_id: int | None = None,
) -> dict[str, Any]:
    now = _utc_now()

    conn.execute(
        """
        INSERT INTO investmap_rf_card_manager_assignments (
            global_id,
            municipality_raw,
            municipality_normalized,
            manager_name,
            rule_id,
            assignment_source,
            match_status,
            assigned_by_user_id,
            updated_at_utc
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(global_id) DO UPDATE SET
            municipality_raw = excluded.municipality_raw,
            municipality_normalized = excluded.municipality_normalized,
            manager_name = excluded.manager_name,
            rule_id = excluded.rule_id,
            assignment_source = excluded.assignment_source,
            match_status = excluded.match_status,
            assigned_by_user_id = excluded.assigned_by_user_id,
            updated_at_utc = excluded.updated_at_utc
        """,
        (
            global_id,
            municipality_raw,
            municipality_normalized,
            manager_name,
            rule_id,
            assignment_source,
            match_status,
            assigned_by_user_id,
            now,
        ),
    )

    row = conn.execute(
        """
        SELECT *
        FROM investmap_rf_card_manager_assignments
        WHERE global_id = ?
        """,
        (global_id,),
    ).fetchone()
    return dict(row)


def update_card_manager_assignment(
    conn,
    *,
    card,
    notify_admins: bool = True,
) -> dict[str, Any]:
    """
    Сопоставляет municipality API-карточки с управляющим.

    Ручное назначение не перезаписывается автоматикой. При новой проблеме
    создаётся issue, уведомления для админов и запись системного журнала.
    """
    global_id = int(getattr(card, "global_id"))
    municipality_raw = get_card_municipality(card)
    municipality_normalized = normalize_municipality(municipality_raw)
    existing = _get_existing_assignment(conn, global_id)

    if existing is not None and existing["assignment_source"] == "manual":
        return {
            "status": MATCH_STATUS_MANUAL,
            "assignment": dict(existing),
            "issue": None,
            "notification_created": False,
        }

    if is_kulibin_special_economic_zone(card):
        _resolve_open_issues(conn, global_id=global_id)
        assignment = _upsert_assignment(
            conn,
            global_id=global_id,
            municipality_raw=municipality_raw,
            municipality_normalized=municipality_normalized,
            manager_name=KULIBIN_MANAGER_NAME,
            rule_id=None,
            assignment_source="auto",
            match_status=MATCH_STATUS_MATCHED,
        )
        return {
            "status": MATCH_STATUS_MATCHED,
            "assignment": assignment,
            "issue": None,
            "notification_created": False,
        }

    if not municipality_normalized:
        issue, is_new_issue = _upsert_issue(
            conn,
            global_id=global_id,
            municipality_raw=municipality_raw,
            municipality_normalized=municipality_normalized,
            issue_type=ISSUE_TYPE_UNMATCHED,
            details="В API-карточке отсутствует значение municipality.",
        )
        assignment = _upsert_assignment(
            conn,
            global_id=global_id,
            municipality_raw=municipality_raw,
            municipality_normalized=municipality_normalized,
            manager_name=None,
            rule_id=None,
            assignment_source="auto",
            match_status=MATCH_STATUS_UNMATCHED,
        )

        if is_new_issue and notify_admins:
            message = (
                f"Инвесткарта РФ: не найдено муниципальное образование "
                f"для карточки {global_id}."
            )
            _notify_admins(
                conn,
                message=message,
                link=f"/investmap/rf-monitor?global_id={global_id}",
            )
            _log_system_action(
                conn,
                action="investmap_rf_manager_match_unmatched",
                detail=message,
            )

        return {
            "status": MATCH_STATUS_UNMATCHED,
            "assignment": assignment,
            "issue": issue,
            "notification_created": is_new_issue,
        }

    matches = _find_matching_rules(conn, municipality_normalized)

    if len(matches) == 1:
        rule = matches[0]
        _resolve_open_issues(conn, global_id=global_id)
        assignment = _upsert_assignment(
            conn,
            global_id=global_id,
            municipality_raw=municipality_raw,
            municipality_normalized=municipality_normalized,
            manager_name=rule["manager_name"],
            rule_id=int(rule["id"]),
            assignment_source="auto",
            match_status=MATCH_STATUS_MATCHED,
        )
        return {
            "status": MATCH_STATUS_MATCHED,
            "assignment": assignment,
            "issue": None,
            "notification_created": False,
        }

    if len(matches) > 1:
        details = (
            "Найдено несколько правил: "
            + ", ".join(
                f"{match['municipality_name']} → {match['manager_name']}"
                for match in matches
            )
        )
        issue_type = ISSUE_TYPE_AMBIGUOUS
        match_status = MATCH_STATUS_AMBIGUOUS
    else:
        details = "Для municipality не найдено активное правило."
        issue_type = ISSUE_TYPE_UNMATCHED
        match_status = MATCH_STATUS_UNMATCHED

    issue, is_new_issue = _upsert_issue(
        conn,
        global_id=global_id,
        municipality_raw=municipality_raw,
        municipality_normalized=municipality_normalized,
        issue_type=issue_type,
        details=details,
    )
    assignment = _upsert_assignment(
        conn,
        global_id=global_id,
        municipality_raw=municipality_raw,
        municipality_normalized=municipality_normalized,
        manager_name=None,
        rule_id=None,
        assignment_source="auto",
        match_status=match_status,
    )

    if is_new_issue and notify_admins:
        message = (
            f"Инвесткарта РФ: {issue_type} сопоставление управляющего "
            f"для карточки {global_id}; муниципалитет: {municipality_raw or 'не указан'}."
        )
        _notify_admins(
            conn,
            message=message,
            link=f"/investmap/rf-monitor?global_id={global_id}",
        )
        _log_system_action(
            conn,
            action=f"investmap_rf_manager_match_{issue_type}",
            detail=message,
        )

    return {
        "status": match_status,
        "assignment": assignment,
        "issue": issue,
        "notification_created": is_new_issue,
    }
