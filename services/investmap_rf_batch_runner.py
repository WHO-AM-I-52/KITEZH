"""CLI для последовательного сбора снимков нескольких карточек Инвесткарты РФ."""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

from services.investmap_rf_client import InvestmapRfClientError
from services.investmap_rf_snapshot_runner import (
    SnapshotSaveResult,
    collect_card_snapshot,
)

DEFAULT_DELAY_SECONDS = 1.0
MIN_DELAY_SECONDS = 1.0


@dataclass(frozen=True)
class BatchItemResult:
    global_id: int
    status: str
    snapshot_id: int | None = None
    changes_count: int = 0
    error: str | None = None


@dataclass(frozen=True)
class BatchReport:
    started_at_utc: str
    completed_at_utc: str
    requested_count: int
    processed_count: int
    new_snapshots_count: int
    unchanged_count: int
    errors_count: int
    interrupted: bool
    items: list[BatchItemResult]


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def read_global_ids(path: Path) -> list[int]:
    """Читает уникальные положительные ID из текстового файла."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError(f"Не удалось прочитать файл ID: {exc}") from exc

    ids: list[int] = []
    seen: set[int] = set()

    for line_number, raw_line in enumerate(lines, start=1):
        value = raw_line.split("#", maxsplit=1)[0].strip()
        if not value:
            continue

        try:
            global_id = int(value)
        except ValueError as exc:
            raise ValueError(
                f"Строка {line_number}: ID должен быть целым числом."
            ) from exc

        if global_id <= 0:
            raise ValueError(
                f"Строка {line_number}: ID должен быть положительным числом."
            )

        if global_id not in seen:
            seen.add(global_id)
            ids.append(global_id)

    return ids


def run_batch(
    global_ids: Sequence[int],
    *,
    delay_seconds: float = DEFAULT_DELAY_SECONDS,
    collect_snapshot_fn: Callable[[int], SnapshotSaveResult] = collect_card_snapshot,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> BatchReport:
    """Последовательно сохраняет снимки, не прерываясь из-за ошибки одной карточки."""
    if delay_seconds < MIN_DELAY_SECONDS:
        raise ValueError(
            f"delay_seconds не может быть меньше {MIN_DELAY_SECONDS}."
        )

    started_at = _utc_now()
    items: list[BatchItemResult] = []
    interrupted = False

    for index, global_id in enumerate(global_ids):
        try:
            result = collect_snapshot_fn(global_id)
        except InvestmapRfClientError as exc:
            items.append(
                BatchItemResult(
                    global_id=global_id,
                    status="error",
                    error=str(exc),
                )
            )
        except Exception as exc:
            items.append(
                BatchItemResult(
                    global_id=global_id,
                    status="error",
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
        else:
            items.append(
                BatchItemResult(
                    global_id=global_id,
                    status="new" if result.is_new_snapshot else "unchanged",
                    snapshot_id=result.snapshot_id,
                    changes_count=result.changes_count,
                )
            )

        if index < len(global_ids) - 1:
            try:
                sleep_fn(delay_seconds)
            except KeyboardInterrupt:
                interrupted = True
                break

    processed_count = len(items)
    new_snapshots_count = sum(item.status == "new" for item in items)
    unchanged_count = sum(item.status == "unchanged" for item in items)
    errors_count = sum(item.status == "error" for item in items)

    return BatchReport(
        started_at_utc=started_at,
        completed_at_utc=_utc_now(),
        requested_count=len(global_ids),
        processed_count=processed_count,
        new_snapshots_count=new_snapshots_count,
        unchanged_count=unchanged_count,
        errors_count=errors_count,
        interrupted=interrupted,
        items=items,
    )


def write_report(path: Path, report: BatchReport) -> None:
    """Сохраняет итог обработки без полных payload карточек."""
    payload = asdict(report)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Последовательно получает карточки API Инвестиционной карты РФ "
            "и сохраняет новые SQLite-снимки."
        )
    )
    parser.add_argument(
        "ids_file",
        type=Path,
        help="UTF-8 текстовый файл: один global_id в строке, # — комментарий.",
    )
    parser.add_argument(
        "--delay-seconds",
        type=float,
        default=DEFAULT_DELAY_SECONDS,
        help=f"Пауза между запросами, не меньше {MIN_DELAY_SECONDS} сек.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Обработать не больше указанного количества уникальных ID.",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("reports/investmap_rf_batch_report.json"),
        help="Путь к итоговому JSON-отчёту.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.limit is not None and args.limit <= 0:
        print("--limit должен быть положительным числом.", file=sys.stderr)
        return 2

    try:
        global_ids = read_global_ids(args.ids_file)
        if args.limit is not None:
            global_ids = global_ids[:args.limit]

        if not global_ids:
            print("Файл не содержит ID для обработки.", file=sys.stderr)
            return 2

        report = run_batch(
            global_ids,
            delay_seconds=args.delay_seconds,
        )
        write_report(args.report, report)
    except ValueError as exc:
        print(f"Ошибка параметров: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"Не удалось сохранить отчёт: {exc}", file=sys.stderr)
        return 1

    print(
        "Обработка завершена: "
        f"всего={report.requested_count}; "
        f"обработано={report.processed_count}; "
        f"новых={report.new_snapshots_count}; "
        f"без изменений={report.unchanged_count}; "
        f"ошибок={report.errors_count}; "
        f"прервана={'да' if report.interrupted else 'нет'}."
    )
    print(f"Отчёт: {args.report}")
    return 1 if report.errors_count or report.interrupted else 0


if __name__ == "__main__":
    raise SystemExit(main())
