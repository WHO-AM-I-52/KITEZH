"""Read-only Excel export for the InvestMap RF monitor."""

from __future__ import annotations

from collections import defaultdict
from io import BytesIO
import sqlite3
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter


_TECHNICAL_HEADERS = [
    "Global ID",
    "Наименование",
    "Муниципалитет",
    "Территориальный управляющий",
    "Источник назначения",
    "Статус сопоставления",
    "Открытая проблема",
    "Дата последнего snapshot",
    "Средний процент заполнения",
    "V2-оценка и дата анализа",
]

_OVERVIEW_HEADERS = [
    "Global ID",
    "Наименование",
    "Муниципалитет",
    "Территориальный управляющий",
    "Средний процент заполнения",
]

_MANAGER_HEADERS = [
    "Территориальный управляющий",
    "Количество карточек",
    "Средний процент заполнения",
]

_RANKING_HEADERS = [
    "Global ID",
    "Средний процент заполнения",
    "Территориальный управляющий",
]

_STATUS_LABELS = {
    "matched": "Сопоставлено",
    "manual": "Назначено вручную",
    "unmatched": "Не найдено",
    "ambiguous": "Неоднозначно",
}

_SOURCE_LABELS = {
    "automatic": "Автоматическое",
    "manual": "Ручное",
}

_ISSUE_LABELS = {
    "unmatched": "Не найдено правило",
    "ambiguous": "Несколько правил",
}

_HEADER_FILL = PatternFill("solid", fgColor="1F4E78")
_SECTION_FILL = PatternFill("solid", fgColor="D9EAF7")
_TOTAL_FILL = PatternFill("solid", fgColor="E2F0D9")
_HEADER_FONT = Font(color="FFFFFF", bold=True)
_BOLD_FONT = Font(bold=True)


def _first_text(value: Any) -> str | None:
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return None


def _payload_value(payload: Any, *keys: str) -> str | None:
    if not isinstance(payload, dict):
        return None

    for key in keys:
        value = _first_text(payload.get(key))
        if value:
            return value

    return None


def _read_export_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        WITH latest_snapshot_ids AS (
            SELECT global_id, MAX(id) AS snapshot_id
            FROM investmap_rf_card_snapshots
            GROUP BY global_id
        ),
        latest_snapshots AS (
            SELECT
                snapshots.id AS snapshot_id,
                snapshots.global_id,
                snapshots.payload_json,
                snapshots.fetched_at_utc,
                snapshots.filling_level
            FROM investmap_rf_card_snapshots AS snapshots
            INNER JOIN latest_snapshot_ids
                ON latest_snapshot_ids.snapshot_id = snapshots.id
        ),
        latest_v2_snapshot_ids AS (
            SELECT site_id, MAX(run_id) AS run_id
            FROM portal_analysis_site_snapshots
            GROUP BY site_id
        ),
        latest_v2 AS (
            SELECT
                snapshots.site_id,
                snapshots.score_percent,
                snapshots.analysis_status,
                runs.created_at AS analyzed_at_utc
            FROM portal_analysis_site_snapshots AS snapshots
            INNER JOIN latest_v2_snapshot_ids
                ON latest_v2_snapshot_ids.site_id = snapshots.site_id
               AND latest_v2_snapshot_ids.run_id = snapshots.run_id
            INNER JOIN portal_analysis_runs AS runs
                ON runs.id = snapshots.run_id
        ),
        assignments AS (
            SELECT
                global_id,
                municipality_raw,
                manager_name,
                assignment_source,
                match_status
            FROM investmap_rf_card_manager_assignments
        ),
        latest_open_issue_ids AS (
            SELECT global_id, MAX(id) AS issue_id
            FROM investmap_rf_manager_match_issues
            WHERE is_resolved = 0
            GROUP BY global_id
        ),
        open_issues AS (
            SELECT
                issues.global_id,
                issues.issue_type,
                issues.details
            FROM investmap_rf_manager_match_issues AS issues
            INNER JOIN latest_open_issue_ids
                ON latest_open_issue_ids.issue_id = issues.id
        )
        SELECT
            latest.global_id,
            latest.payload_json,
            latest.fetched_at_utc,
            latest.filling_level,
            latest_v2.score_percent AS v2_score,
            latest_v2.analysis_status AS v2_analysis_status,
            latest_v2.analyzed_at_utc AS v2_analyzed_at_utc,
            assignments.municipality_raw,
            assignments.manager_name,
            assignments.assignment_source,
            assignments.match_status,
            open_issues.issue_type AS open_issue_type,
            open_issues.details AS open_issue_details
        FROM latest_snapshots AS latest
        LEFT JOIN latest_v2
            ON latest_v2.site_id = CAST(latest.global_id AS TEXT)
        LEFT JOIN assignments
            ON assignments.global_id = latest.global_id
        LEFT JOIN open_issues
            ON open_issues.global_id = latest.global_id
        ORDER BY latest.global_id ASC
        """
    ).fetchall()

    export_rows: list[dict[str, Any]] = []

    for row in rows:
        payload: Any = None

        try:
            import json
            payload = json.loads(row["payload_json"])
        except (TypeError, ValueError):
            payload = None

        municipality = (
            _first_text(row["municipality_raw"])
            or _payload_value(payload, "municipality", "municipality_name")
        )

        name = _payload_value(
            payload,
            "name",
            "title",
            "object_name",
            "site_name",
            "investment_site_name",
        )

        v2_value = None
        if row["v2_analysis_status"] == "ok" and row["v2_score"] is not None:
            v2_value = f"{row['v2_score']}%"
            if row["v2_analyzed_at_utc"]:
                v2_value += f" ({row['v2_analyzed_at_utc']})"

        issue = _ISSUE_LABELS.get(row["open_issue_type"])
        details = _first_text(row["open_issue_details"])
        if issue and details:
            issue = f"{issue}: {details}"
        elif details:
            issue = details

        export_rows.append(
            {
                "global_id": row["global_id"],
                "name": name,
                "municipality": municipality,
                "manager_name": _first_text(row["manager_name"]),
                "assignment_source": _SOURCE_LABELS.get(
                    row["assignment_source"],
                    _first_text(row["assignment_source"]),
                ),
                "match_status": _STATUS_LABELS.get(
                    row["match_status"],
                    _first_text(row["match_status"]),
                ),
                "open_issue": issue,
                "fetched_at_utc": row["fetched_at_utc"],
                "filling_level": row["filling_level"],
                "v2_value": v2_value,
            }
        )

    return export_rows


def _style_header(sheet, row_number: int, headers: list[str]) -> None:
    for column_number, header in enumerate(headers, start=1):
        cell = sheet.cell(row=row_number, column=column_number, value=header)
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    sheet.freeze_panes = f"A{row_number + 1}"
    sheet.auto_filter.ref = (
        f"A{row_number}:{get_column_letter(len(headers))}{row_number}"
    )


def _apply_widths(sheet, widths: list[int]) -> None:
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width


def _append_technical_sheet(workbook: Workbook, rows: list[dict[str, Any]]) -> None:
    sheet = workbook.active
    sheet.title = "Техническая информация"
    _style_header(sheet, 1, _TECHNICAL_HEADERS)

    for row in rows:
        sheet.append(
            [
                row["global_id"],
                row["name"] or "—",
                row["municipality"] or "—",
                row["manager_name"] or "—",
                row["assignment_source"] or "—",
                row["match_status"] or "Нет данных",
                row["open_issue"] or "—",
                row["fetched_at_utc"] or "—",
                row["filling_level"],
                row["v2_value"] or "—",
            ]
        )

    for cell in sheet["I"][1:]:
        cell.number_format = '0.0"%"'

    _apply_widths(sheet, [14, 36, 34, 32, 22, 24, 52, 25, 27, 30])


def _append_overview_sheet(workbook: Workbook, rows: list[dict[str, Any]]) -> None:
    sheet = workbook.create_sheet("Общая информация")
    _style_header(sheet, 1, _OVERVIEW_HEADERS)

    for row in rows:
        sheet.append(
            [
                row["global_id"],
                row["name"] or "—",
                row["municipality"] or "—",
                row["manager_name"] or "—",
                row["filling_level"],
            ]
        )

    for cell in sheet["E"][1:]:
        cell.number_format = '0.0"%"'

    _apply_widths(sheet, [14, 40, 36, 32, 27])


def _append_manager_summary_sheet(
    workbook: Workbook,
    rows: list[dict[str, Any]],
) -> None:
    sheet = workbook.create_sheet("Итог по управляющим")
    _style_header(sheet, 1, _MANAGER_HEADERS)

    grouped: dict[str, list[float]] = defaultdict(list)
    counts: dict[str, int] = defaultdict(int)

    for row in rows:
        manager = row["manager_name"] or "Не назначен"
        counts[manager] += 1

        if row["filling_level"] is not None:
            grouped[manager].append(float(row["filling_level"]))

    for manager in sorted(counts, key=lambda item: item.casefold()):
        values = grouped[manager]
        average = sum(values) / len(values) if values else None
        sheet.append([manager, counts[manager], average])

    all_values = [
        float(row["filling_level"])
        for row in rows
        if row["filling_level"] is not None
    ]
    total_average = sum(all_values) / len(all_values) if all_values else None

    total_row = sheet.max_row + 1
    sheet.append(["Итого", len(rows), total_average])

    for cell in sheet[total_row]:
        cell.fill = _TOTAL_FILL
        cell.font = _BOLD_FONT

    for cell in sheet["C"][1:]:
        cell.number_format = '0.0"%"'

    _apply_widths(sheet, [36, 22, 30])


def _append_rankings_sheet(workbook: Workbook, rows: list[dict[str, Any]]) -> None:
    sheet = workbook.create_sheet("Лучшие и худшие площадки")

    calculated = [
        row for row in rows if row["filling_level"] is not None
    ]
    calculated.sort(key=lambda row: (-float(row["filling_level"]), row["global_id"]))
    top_ten = calculated[:10]

    below_eighty = [
        row for row in rows if row["filling_level"] is not None and row["filling_level"] < 80
    ]
    below_eighty.sort(key=lambda row: (float(row["filling_level"]), row["global_id"]))

    without_filling = [
        row for row in rows if row["filling_level"] is None
    ]
    without_filling.sort(key=lambda row: row["global_id"])

    def append_section(title: str, items: list[dict[str, Any]], start_row: int) -> int:
        title_cell = sheet.cell(row=start_row, column=1, value=title)
        title_cell.fill = _SECTION_FILL
        title_cell.font = _BOLD_FONT

        _style_header(sheet, start_row + 1, _RANKING_HEADERS)

        row_number = start_row + 2
        for item in items:
            sheet.cell(row=row_number, column=1, value=item["global_id"])
            sheet.cell(row=row_number, column=2, value=item["filling_level"])
            sheet.cell(
                row=row_number,
                column=3,
                value=item["manager_name"] or "Не назначен",
            )
            sheet.cell(row=row_number, column=2).number_format = '0.0"%"'
            row_number += 1

        return row_number + 1

    next_row = append_section("Лучшие 10 площадок", top_ten, 1)
    next_row = append_section(
        "Площадки с заполнением ниже 80%",
        below_eighty,
        next_row,
    )
    append_section(
        "Карточки без рассчитанного процента",
        without_filling,
        next_row,
    )

    sheet.freeze_panes = "A3"
    _apply_widths(sheet, [16, 30, 36])


def build_monitor_export_xlsx(conn: sqlite3.Connection) -> BytesIO:
    """
    Формирует read-only XLSX-выгрузку мониторинга Инвесткарты РФ.

    Функция только читает сохранённые snapshot и V2-результаты. Она не
    обращается к внешнему API, не пересчитывает оценку и не меняет БД.
    """
    rows = _read_export_rows(conn)
    workbook = Workbook()

    _append_technical_sheet(workbook, rows)
    _append_overview_sheet(workbook, rows)
    _append_manager_summary_sheet(workbook, rows)
    _append_rankings_sheet(workbook, rows)

    output = BytesIO()
    workbook.save(output)
    output.seek(0)
    return output
