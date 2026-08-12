"""Read-only summary and comparison queries for Investmap V2 history."""

from __future__ import annotations


MANUAL_REFERENCE = {
    "sites": 616,
    "average_score": 84.87,
    "as_of": "2026-08-06",
}

DEFAULT_LIMIT = 25
MAX_LIMIT = 100

CHANGE_KINDS = frozenset(
    {
        "all",
        "new",
        "removed",
        "changed_source",
        "improved",
        "worsened",
        "changed_inclusion",
        "error",
    }
)

_RUN_COLUMNS = """
    id,
    created_at,
    formula_version,
    source_label,
    total_sites,
    active_sites,
    excluded_sites,
    error_sites,
    average_score
"""

_ZERO_COUNTERS = {
    "new_sites": 0,
    "removed_sites": 0,
    "changed_source_sites": 0,
    "improved_sites": 0,
    "worsened_sites": 0,
    "changed_inclusion_sites": 0,
    "error_sites": 0,
    "changes_total": 0,
}


def _get_run_metadata(conn, run_id):
    row = conn.execute(
        f"""
        SELECT {_RUN_COLUMNS}
        FROM portal_analysis_runs
        WHERE id = ?
        """,
        (run_id,),
    ).fetchone()
    return dict(row) if row else None


def get_latest_run_metadata(conn):
    """Возвращает compact metadata последнего history run."""
    row = conn.execute(
        f"""
        SELECT {_RUN_COLUMNS}
        FROM portal_analysis_runs
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """
    ).fetchone()
    return dict(row) if row else None


def get_immediately_previous_run_metadata(conn, current_run_id):
    """Возвращает run, непосредственно предшествующий current по created_at/id."""
    current = _get_run_metadata(conn, current_run_id)
    if current is None:
        raise ValueError("current_run_id does not exist")

    row = conn.execute(
        f"""
        SELECT {_RUN_COLUMNS}
        FROM portal_analysis_runs
        WHERE created_at < ?
           OR (created_at = ? AND id < ?)
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        (
            current["created_at"],
            current["created_at"],
            current["id"],
        ),
    ).fetchone()
    return dict(row) if row else None


def _get_verified_run_pair(conn, current_run_id, previous_run_id):
    current = _get_run_metadata(conn, current_run_id)
    if current is None:
        raise ValueError("current_run_id does not exist")

    previous = get_immediately_previous_run_metadata(conn, current_run_id)
    if previous is None or previous["id"] != previous_run_id:
        raise ValueError("previous_run_id is not immediately previous")

    return current, previous


def _get_paired_snapshot_rows(conn, current_run_id, previous_run_id):
    """Имитирует full outer join снимков двух run через UNION двух LEFT JOIN."""
    return conn.execute(
        """
        WITH current_snapshots AS (
            SELECT
                site_id,
                site_name,
                site_status,
                is_included,
                score_percent,
                field_values_hash,
                analysis_status
            FROM portal_analysis_site_snapshots
            WHERE run_id = ?
        ),
        previous_snapshots AS (
            SELECT
                site_id,
                site_name,
                site_status,
                is_included,
                score_percent,
                field_values_hash,
                analysis_status
            FROM portal_analysis_site_snapshots
            WHERE run_id = ?
        )
        SELECT
            c.site_id AS site_id,
            c.site_name AS current_site_name,
            p.site_name AS previous_site_name,
            c.site_status AS current_site_status,
            p.site_status AS previous_site_status,
            c.is_included AS current_is_included,
            p.is_included AS previous_is_included,
            c.score_percent AS current_score,
            p.score_percent AS previous_score,
            c.field_values_hash AS current_hash,
            p.field_values_hash AS previous_hash,
            c.analysis_status AS current_analysis_status,
            p.analysis_status AS previous_analysis_status
        FROM current_snapshots AS c
        LEFT JOIN previous_snapshots AS p
            ON p.site_id = c.site_id

        UNION ALL

        SELECT
            p.site_id AS site_id,
            NULL AS current_site_name,
            p.site_name AS previous_site_name,
            NULL AS current_site_status,
            p.site_status AS previous_site_status,
            NULL AS current_is_included,
            p.is_included AS previous_is_included,
            NULL AS current_score,
            p.score_percent AS previous_score,
            NULL AS current_hash,
            p.field_values_hash AS previous_hash,
            NULL AS current_analysis_status,
            p.analysis_status AS previous_analysis_status
        FROM previous_snapshots AS p
        LEFT JOIN current_snapshots AS c
            ON c.site_id = p.site_id
        WHERE c.site_id IS NULL
        """,
        (current_run_id, previous_run_id),
    ).fetchall()


def _make_change_item(row):
    current_exists = row["current_analysis_status"] is not None
    previous_exists = row["previous_analysis_status"] is not None
    both_exist = current_exists and previous_exists

    is_new = current_exists and not previous_exists
    is_removed = previous_exists and not current_exists

    changed_source = (
        both_exist
        and row["current_hash"] != row["previous_hash"]
    )

    changed_inclusion = (
        both_exist
        and row["current_is_included"] != row["previous_is_included"]
    )

    scores_are_comparable = (
        both_exist
        and row["current_is_included"] == 1
        and row["previous_is_included"] == 1
        and row["current_analysis_status"] == "ok"
        and row["previous_analysis_status"] == "ok"
        and row["current_score"] is not None
        and row["previous_score"] is not None
    )

    improved = (
        scores_are_comparable
        and row["current_score"] > row["previous_score"]
    )
    worsened = (
        scores_are_comparable
        and row["current_score"] < row["previous_score"]
    )

    has_error = (
        row["current_analysis_status"] == "error"
        or row["previous_analysis_status"] == "error"
    )

    if not any(
        (
            is_new,
            is_removed,
            changed_source,
            improved,
            worsened,
            changed_inclusion,
            has_error,
        )
    ):
        return None

    if has_error:
        primary_kind = "error"
    elif is_new:
        primary_kind = "new"
    elif is_removed:
        primary_kind = "removed"
    elif changed_inclusion:
        primary_kind = "changed_inclusion"
    elif improved:
        primary_kind = "improved"
    elif worsened:
        primary_kind = "worsened"
    else:
        primary_kind = "changed_source"

    current_score = row["current_score"]
    previous_score = row["previous_score"]

    return {
        "site_id": row["site_id"],
        "site_name": (
            row["current_site_name"]
            or row["previous_site_name"]
        ),
        "primary_kind": primary_kind,
        "is_new": is_new,
        "is_removed": is_removed,
        "changed_source": changed_source,
        "improved": improved,
        "worsened": worsened,
        "changed_inclusion": changed_inclusion,
        "has_error": has_error,
        "current_score": current_score,
        "previous_score": previous_score,
        "score_delta": (
            current_score - previous_score
            if current_score is not None
            and previous_score is not None
            else None
        ),
        "current_is_included": (
            bool(row["current_is_included"])
            if current_exists
            else None
        ),
        "previous_is_included": (
            bool(row["previous_is_included"])
            if previous_exists
            else None
        ),
        "current_site_status": row["current_site_status"],
        "previous_site_status": row["previous_site_status"],
        "current_analysis_status": row["current_analysis_status"],
        "previous_analysis_status": row["previous_analysis_status"],
    }


def _get_changes(conn, current_run_id, previous_run_id):
    items = []

    for row in _get_paired_snapshot_rows(
        conn,
        current_run_id,
        previous_run_id,
    ):
        item = _make_change_item(row)
        if item is not None:
            items.append(item)

    return sorted(
        items,
        key=lambda item: (
            item["primary_kind"],
            item["site_id"].casefold(),
        ),
    )


def get_run_comparison(conn, current_run_id, previous_run_id):
    """Возвращает formula-safe aggregate comparison соседних run."""
    current, previous = _get_verified_run_pair(
        conn,
        current_run_id,
        previous_run_id,
    )

    if current["formula_version"] != previous["formula_version"]:
        return {
            "current_run": current,
            "previous_run": previous,
            "comparison_available": False,
            "reason": "formula_version_mismatch",
            **_ZERO_COUNTERS,
        }

    items = _get_changes(conn, current_run_id, previous_run_id)

    return {
        "current_run": current,
        "previous_run": previous,
        "comparison_available": True,
        "reason": None,
        "new_sites": sum(item["is_new"] for item in items),
        "removed_sites": sum(item["is_removed"] for item in items),
        "changed_source_sites": sum(
            item["changed_source"]
            for item in items
        ),
        "improved_sites": sum(item["improved"] for item in items),
        "worsened_sites": sum(item["worsened"] for item in items),
        "changed_inclusion_sites": sum(
            item["changed_inclusion"]
            for item in items
        ),
        "error_sites": sum(item["has_error"] for item in items),
        "changes_total": len(items),
    }


def _validate_pagination(kind, limit, offset):
    if kind not in CHANGE_KINDS:
        raise ValueError("invalid change kind")

    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or limit < 1
    ):
        raise ValueError("limit must be a positive integer")

    if (
        isinstance(offset, bool)
        or not isinstance(offset, int)
        or offset < 0
    ):
        raise ValueError("offset must be a non-negative integer")

    return kind, min(limit, MAX_LIMIT), offset


def get_paginated_run_changes(
    conn,
    current_run_id,
    previous_run_id,
    *,
    kind="all",
    limit=DEFAULT_LIMIT,
    offset=0,
):
    """Возвращает compact one-item-per-site список изменений."""
    kind, limit, offset = _validate_pagination(
        kind,
        limit,
        offset,
    )

    comparison = get_run_comparison(
        conn,
        current_run_id,
        previous_run_id,
    )

    if not comparison["comparison_available"]:
        return {
            "current_run_id": current_run_id,
            "previous_run_id": previous_run_id,
            "comparison_available": False,
            "reason": comparison["reason"],
            "kind": kind,
            "limit": limit,
            "offset": offset,
            "total": 0,
            "items": [],
        }

    items = _get_changes(conn, current_run_id, previous_run_id)

    if kind != "all":
        flag_name = {
            "new": "is_new",
            "removed": "is_removed",
            "error": "has_error",
        }.get(kind, kind)

        items = [
            item
            for item in items
            if item[flag_name]
        ]

    return {
        "current_run_id": current_run_id,
        "previous_run_id": previous_run_id,
        "comparison_available": True,
        "reason": None,
        "kind": kind,
        "limit": limit,
        "offset": offset,
        "total": len(items),
        "items": items[offset:offset + limit],
    }


def get_history_summary(conn):
    """Совместимый wrapper существующего history summary API."""
    latest = get_latest_run_metadata(conn)

    if latest is None:
        return {
            "latest": None,
            "previous": None,
            "comparison": {},
            "manual_reference": MANUAL_REFERENCE,
        }

    previous = get_immediately_previous_run_metadata(
        conn,
        latest["id"],
    )

    comparison = (
        get_run_comparison(
            conn,
            latest["id"],
            previous["id"],
        )
        if previous is not None
        else {}
    )

    return {
        "latest": latest,
        "previous": previous,
        "comparison": comparison,
        "manual_reference": MANUAL_REFERENCE,
    }
