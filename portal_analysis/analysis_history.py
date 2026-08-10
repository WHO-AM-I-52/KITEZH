"""Хранение запусков и снимков расчёта заполненности инвестиционных площадок."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

EXCLUDED_SITE_STATUSES = frozenset({
    "продана",
    "предоставлена в аренду",
    "использована для других целей",
    "снята с реализации",
})


def normalize_site_status(value: Any) -> str:
    """Приводит статус площадки к форме для надёжного сравнения."""
    text = "" if value is None else str(value)
    return re.sub(r"\s+", " ", text).strip().casefold()


def get_exclusion_reason(site_status: Any) -> str | None:
    """Возвращает нормализованный исключающий статус или None."""
    normalized = normalize_site_status(site_status)
    return normalized if normalized in EXCLUDED_SITE_STATUSES else None


def is_site_included(site_status: Any) -> bool:
    """Определяет, должна ли площадка участвовать в агрегатах V2."""
    return get_exclusion_reason(site_status) is None


def make_fields_hash(row: dict[str, Any], field_names: list[str]) -> str:
    """Строит стабильный хеш значимых полей карточки для поиска изменений."""
    normalized = {str(key).strip().casefold(): value for key, value in row.items()}
    payload = {
        field: normalized.get(field.strip().casefold())
        for field in sorted(field_names, key=str.casefold)
    }
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def create_analysis_history_tables(conn) -> None:
    """Создаёт таблицы истории запусков V2 и индексы."""
    conn.executescript("""
CREATE TABLE IF NOT EXISTS portal_analysis_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    formula_version TEXT NOT NULL,
    initiated_by INTEGER,
    source_label TEXT,
    total_sites INTEGER NOT NULL DEFAULT 0,
    active_sites INTEGER NOT NULL DEFAULT 0,
    excluded_sites INTEGER NOT NULL DEFAULT 0,
    error_sites INTEGER NOT NULL DEFAULT 0,
    average_score REAL,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS portal_analysis_site_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES portal_analysis_runs(id) ON DELETE CASCADE,
    site_id TEXT NOT NULL,
    site_name TEXT,
    site_status TEXT,
    is_included INTEGER NOT NULL,
    exclusion_reason TEXT,
    score_percent INTEGER,
    required_fields_count INTEGER,
    filled_fields_count INTEGER,
    missing_fields_json TEXT NOT NULL DEFAULT '[]',
    skipped_fields_json TEXT NOT NULL DEFAULT '[]',
    field_values_hash TEXT,
    error_message TEXT,
    previous_snapshot_id INTEGER REFERENCES portal_analysis_site_snapshots(id),
    UNIQUE(run_id, site_id)
);

CREATE INDEX IF NOT EXISTS idx_portal_analysis_snapshots_site
    ON portal_analysis_site_snapshots(site_id, run_id DESC);
CREATE INDEX IF NOT EXISTS idx_portal_analysis_snapshots_run
    ON portal_analysis_site_snapshots(run_id);
CREATE INDEX IF NOT EXISTS idx_portal_analysis_snapshots_included
    ON portal_analysis_site_snapshots(run_id, is_included);
""")


def create_run(conn, formula_version: str, initiated_by: int | None = None, source_label: str | None = None) -> int:
    """Создаёт запуск анализа и возвращает его ID."""
    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    cursor = conn.execute(
        """
        INSERT INTO portal_analysis_runs (created_at, formula_version, initiated_by, source_label)
        VALUES (?, ?, ?, ?)
        """,
        (created_at, formula_version, initiated_by, source_label),
    )
    return int(cursor.lastrowid)


def get_previous_snapshot(conn, site_id: Any, run_id: int):
    """Возвращает последний снимок площадки до текущего запуска."""
    return conn.execute(
        """
        SELECT * FROM portal_analysis_site_snapshots
        WHERE site_id = ? AND run_id < ?
        ORDER BY run_id DESC, id DESC
        LIMIT 1
        """,
        (str(site_id), run_id),
    ).fetchone()


def save_snapshot(
    conn,
    run_id: int,
    site_id: Any,
    site_name: Any,
    site_status: Any,
    result: dict[str, Any] | None = None,
    field_values_hash: str | None = None,
    error_message: str | None = None,
) -> int:
    """Сохраняет результат одной площадки и связывает его с предыдущим снимком."""
    if site_id is None or str(site_id).strip() == "":
        raise ValueError("site_id обязателен для сохранения снимка")

    status_text = "" if site_status is None else str(site_status).strip()
    exclusion_reason = get_exclusion_reason(status_text)
    is_included = int(exclusion_reason is None)
    previous = get_previous_snapshot(conn, site_id, run_id)
    previous_id = previous["id"] if previous else None
    result = result or {}

    cursor = conn.execute(
        """
        INSERT INTO portal_analysis_site_snapshots (
            run_id, site_id, site_name, site_status, is_included, exclusion_reason,
            score_percent, required_fields_count, filled_fields_count,
            missing_fields_json, skipped_fields_json, field_values_hash,
            error_message, previous_snapshot_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id, str(site_id), None if site_name is None else str(site_name), status_text,
            is_included, exclusion_reason, result.get("score"), result.get("total"),
            result.get("filled"),
            json.dumps(result.get("missing", []), ensure_ascii=False),
            json.dumps(result.get("skipped", []), ensure_ascii=False),
            field_values_hash, error_message, previous_id,
        ),
    )
    return int(cursor.lastrowid)
