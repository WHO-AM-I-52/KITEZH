"""SQLite-хранилище снимков API Инвестиционной карты РФ."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from services.investmap_rf_client import InvestmapRfCard


@dataclass(frozen=True)
class SnapshotSaveResult:
    """Результат сохранения или сопоставления API-снимка."""

    snapshot_id: int
    is_new_snapshot: bool
    changes_count: int


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _payload_sha256(payload_json: str) -> str:
    return hashlib.sha256(payload_json.encode("utf-8")).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _json_value(value: Any) -> str:
    return _canonical_json(value)


def _diff_values(
    previous: Any,
    current: Any,
    *,
    path: str = "",
) -> list[tuple[str, Any, Any]]:
    """Возвращает изменения двух JSON-значений с JSON Pointer-подобными путями."""
    if isinstance(previous, dict) and isinstance(current, dict):
        changes: list[tuple[str, Any, Any]] = []
        for key in sorted(set(previous) | set(current)):
            escaped_key = str(key).replace("~", "~0").replace("/", "~1")
            changes.extend(
                _diff_values(
                    previous.get(key),
                    current.get(key),
                    path=f"{path}/{escaped_key}",
                )
            )
        return changes

    if isinstance(previous, list) and isinstance(current, list):
        changes = []
        max_length = max(len(previous), len(current))
        for index in range(max_length):
            previous_value = previous[index] if index < len(previous) else None
            current_value = current[index] if index < len(current) else None
            changes.extend(
                _diff_values(
                    previous_value,
                    current_value,
                    path=f"{path}/{index}",
                )
            )
        return changes

    if previous != current:
        return [(path or "/", previous, current)]

    return []


def _last_snapshot_row(
    conn: sqlite3.Connection,
    global_id: int,
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT id, payload_json, payload_sha256
        FROM investmap_rf_card_snapshots
        WHERE global_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (global_id,),
    ).fetchone()


def save_card_snapshot(
    conn: sqlite3.Connection,
    card: InvestmapRfCard,
    *,
    fetched_at_utc: str | None = None,
) -> SnapshotSaveResult:
    """
    Сохраняет изменённый снимок карточки и построчно фиксирует изменения.

    Соединение принадлежит вызывающему коду: функция не делает commit/rollback
    и не закрывает conn.
    """
    payload_json = _canonical_json(card.payload)
    payload_sha256 = _payload_sha256(payload_json)
    previous_row = _last_snapshot_row(conn, card.global_id)

    if previous_row is not None and previous_row["payload_sha256"] == payload_sha256:
        return SnapshotSaveResult(
            snapshot_id=previous_row["id"],
            is_new_snapshot=False,
            changes_count=0,
        )

    captured_at = fetched_at_utc or _utc_now()
    cursor = conn.execute(
        """
        INSERT INTO investmap_rf_card_snapshots (
            global_id,
            payload_json,
            payload_sha256,
            fetched_at_utc,
            filling_level,
            region_code
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            card.global_id,
            payload_json,
            payload_sha256,
            captured_at,
            card.filling_level,
            card.region_code,
        ),
    )
    snapshot_id = cursor.lastrowid

    if previous_row is None:
        return SnapshotSaveResult(
            snapshot_id=snapshot_id,
            is_new_snapshot=True,
            changes_count=0,
        )

    try:
        previous_payload = json.loads(previous_row["payload_json"])
    except (TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            "Сохранённый снимок Инвестиционной карты РФ содержит некорректный JSON."
        ) from exc

    changes = _diff_values(previous_payload, card.payload)
    for field_path, old_value, new_value in changes:
        conn.execute(
            """
            INSERT INTO investmap_rf_card_changes (
                global_id,
                previous_snapshot_id,
                current_snapshot_id,
                field_path,
                old_value_json,
                new_value_json,
                detected_at_utc
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                card.global_id,
                previous_row["id"],
                snapshot_id,
                field_path,
                _json_value(old_value),
                _json_value(new_value),
                captured_at,
            ),
        )

    return SnapshotSaveResult(
        snapshot_id=snapshot_id,
        is_new_snapshot=True,
        changes_count=len(changes),
    )
