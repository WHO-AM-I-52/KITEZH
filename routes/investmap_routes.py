import json
from flask import (
    Blueprint,
    current_app,
    render_template,
    request,
    jsonify,
    flash,
    g,
    redirect,
    session,
    url_for,
    send_file,
)
from core.activity_log import log_action
from core.auth_utils import (
    admin_required,
    get_user_perm,
    login_required,
    permission_required,
)
from db import get_db
from services.investmap_rf_monitor_queries import (
    get_monitor_card_detail,
    get_monitor_cards,
    get_monitor_registry_cards,
    get_monitor_registry_events,
    get_monitor_summary,
)
from services.investmap_rf_monitor_export import (
    build_monitor_export_xlsx,
)
from core.kitezh_logger import err_logger
from services.investmap_rf_registry import (
    deactivate_card_not_found_by_operator,
    deactivate_card_not_found_in_api,
    import_monitored_cards_xlsx,
    keep_card_not_found_by_operator,
    record_card_api_check_error,
    record_card_api_check_success,
    record_card_api_not_found,
)
from services.investmap_rf_registry_runner import run_registry_card_refresh
from services.investmap_rf_client import (
    InvestmapRfClientError,
    InvestmapRfNotFoundError,
)
from portal_analysis.batch_analysis import run_batch_history
from portal_analysis.history_summary import (
    get_immediately_previous_run_metadata,
    get_latest_run_metadata,
    get_paginated_run_changes,
    get_run_comparison,
)
from portal_analysis.portal_checker import (
    V2_FORMULA_VERSION,
    calc_portal_score_v2,
)
from tools.investmap_export import convert_excel_to_text
from portal_analysis.export_normalizer import normalize_export_row
from portal_analysis.api_snapshot_normalizer import (
    api_snapshot_to_portal_row,
    merge_missing_portal_values,
)
from tools.investmap_analyzer import (
    analyze,
    build_summary_sms,
    build_v2_summary_sms,
)

investmap_bp = Blueprint('investmap', __name__)
_HISTORY_ERROR_STATUSES = frozenset({"invalid_id", "error"})


def _history_item_to_api_result(item: dict) -> dict:
    """Преобразует history item в совместимый API result V2.

    Успешный scorer result сохраняется в исходной форме и дополняется
    техническими полями истории. Ошибочный item остаётся позиционно
    совместимым с export.texts, но не маскируется как реальная оценка 0%.
    """
    canonical_result = item.get("result")
    analysis_status = item.get("analysis_status")

    if canonical_result is not None:
        api_result = dict(canonical_result)
        api_result["is_included"] = item.get("included") is True
        api_result["analysis_status"] = analysis_status

        source_row = item.get("source_row")
        if isinstance(source_row, dict):
            api_snapshot_id = source_row.get("_v2_api_snapshot_id")
            api_fetched_at_utc = source_row.get("_v2_api_fetched_at_utc")
            api_filling_level = source_row.get("_v2_api_filling_level")

            if api_snapshot_id is not None:
                api_result["api_snapshot_id"] = api_snapshot_id
            if api_fetched_at_utc is not None:
                api_result["api_fetched_at_utc"] = api_fetched_at_utc
            if api_filling_level is not None:
                api_result["api_filling_level"] = api_filling_level
                api_result["api_score_delta"] = (
                    api_result.get("score") - api_filling_level
                    if api_result.get("score") is not None
                    else None
                )

        return api_result

    return {
        "score": None,
        "filled": 0,
        "total": 0,
        "missing": [],
        "skipped": [],
        "is_included": False,
        "analysis_status": analysis_status,
        "error": item.get("error"),
        "global_id": item.get("site_id"),
    }


def _active_sms_pairs(results: list[dict], source_rows: list[dict]):
    """Возвращает только активные успешно рассчитанные пары result/source row."""
    return [
        (result, source_row)
        for result, source_row in zip(results, source_rows)
        if result.get("analysis_status") == "ok"
        and result.get("is_included") is True
    ]

def _get_latest_api_snapshot_row(db, global_id):
    """Возвращает последний API-снимок Инвесткарты по global_id."""
    try:
        site_id = int(str(global_id).strip())
    except (TypeError, ValueError):
        return None

    if site_id <= 0:
        return None

    return db.execute(
        """
        SELECT
            id,
            global_id,
            payload_json,
            fetched_at_utc,
            filling_level
        FROM investmap_rf_card_snapshots
        WHERE global_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (site_id,),
    ).fetchone()


def _build_v2_source_row(row, db):
    """Дополняет строку XLSX актуальными API-данными перед V2-анализом."""
    base_row = normalize_export_row(row)
    snapshot = _get_latest_api_snapshot_row(db, base_row.get("global_id"))

    if snapshot is None:
        return base_row

    api_row = api_snapshot_to_portal_row(snapshot["payload_json"])
    merged = merge_missing_portal_values(base_row, api_row)

    merged["_v2_api_snapshot_id"] = snapshot["id"]
    merged["_v2_api_fetched_at_utc"] = snapshot["fetched_at_utc"]
    merged["_v2_api_filling_level"] = snapshot["filling_level"]
    return merged

@investmap_bp.route('/investmap')
@login_required
@permission_required('can_view_investmap')
def investmap():
    """Главная страница — плитки навигации."""
    return render_template('investmap.html')

_MONITOR_CARDS_PER_PAGE = 50


def _monitor_query_positive_int(name: str) -> int | None:
    value = request.args.get(name)

    if value is None or not value.strip():
        return None

    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} должен быть положительным целым числом.") from None

    if parsed <= 0:
        raise ValueError(f"{name} должен быть положительным целым числом.")

    return parsed

def _registry_monitor_url(global_id: int | None = None) -> str:
    """Возвращает URL мониторинга с раскрытым реестром."""
    query: dict[str, int] = {"registry": 1}

    if global_id is not None:
        query["registry_global_id"] = global_id

    return url_for("investmap.investmap_rf_monitor", **query)

@investmap_bp.route('/investmap/rf-monitor')
@login_required
@permission_required('can_view_investmap')
def investmap_rf_monitor():
    """Read-only мониторинг сохранённых API-снимков Инвесткарты РФ."""
    user = getattr(g, 'user', {}).get('login', 'unknown')
    db = None

    try:
        page = _monitor_query_positive_int("page") or 1
        search_global_id = _monitor_query_positive_int("global_id")
        registry_search_global_id = _monitor_query_positive_int(
            "registry_global_id"
        )

        db = get_db()
        cards_page = get_monitor_cards(
            db,
            page=page,
            per_page=_MONITOR_CARDS_PER_PAGE,
            global_id=search_global_id,
        )

        return render_template(
            'investmap_rf_monitor.html',
            summary=get_monitor_summary(db),
            cards_page=cards_page,
            registry_cards=get_monitor_registry_cards(
                db,
                global_id=registry_search_global_id,
            ),
            registry_search_global_id=registry_search_global_id,
            registry_events=get_monitor_registry_events(db),
            is_admin=session.get('role') == 'admin',
            can_refresh_investmap_rf_cards=get_user_perm(
                'can_refresh_investmap_rf_cards'
            ),
        )
    except Exception as exc:
        err_logger.exception(
            'investmap_rf_monitor error | user=%s | %s',
            user,
            exc,
        )
        flash('Ошибка при загрузке мониторинга Инвесткарты РФ.', 'error')
        return render_template(
            'investmap_rf_monitor.html',
            summary={
                'cards_count': 0,
                'snapshots_count': 0,
                'changed_cards_count': 0,
            },
            cards_page={
                'items': [],
                'total': 0,
                'page': 1,
                'per_page': _MONITOR_CARDS_PER_PAGE,
                'pages': 1,
                'search_global_id': None,
                'first_item_number': 0,
                'last_item_number': 0,
            },
            registry_cards=[],
            registry_events=[],
            registry_search_global_id=None,
            is_admin=session.get('role') == 'admin',
            can_refresh_investmap_rf_cards=get_user_perm(
                'can_refresh_investmap_rf_cards'
            ),
        ), 500
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                err_logger.exception(
                    'investmap_rf_monitor close error | user=%s',
                    user,
                )

@investmap_bp.route("/investmap/rf-monitor/export.xlsx")
@login_required
@permission_required("can_view_investmap")
def export_investmap_rf_monitor():
    """Скачивает read-only XLSX-выгрузку мониторинга Инвесткарты РФ."""
    user = getattr(g, "user", {}).get("login", "unknown")
    db = None

    try:
        db = get_db()
        output = build_monitor_export_xlsx(db)

        return send_file(
            output,
            as_attachment=True,
            download_name="investmap_rf_monitor.xlsx",
            mimetype=(
                "application/vnd.openxmlformats-officedocument."
                "spreadsheetml.sheet"
            ),
        )
    except Exception as exc:
        err_logger.exception(
            "export_investmap_rf_monitor error | user=%s | %s",
            user,
            exc,
        )
        flash(
            "Не удалось сформировать Excel-выгрузку мониторинга "
            "Инвесткарты РФ.",
            "error",
        )
        return redirect(url_for("investmap.investmap_rf_monitor"))
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                err_logger.exception(
                    "export_investmap_rf_monitor close error | user=%s",
                    user,
                )

@investmap_bp.route("/investmap-rf/monitor/import", methods=["POST"])
@login_required
@permission_required("can_view_investmap")
def import_investmap_rf_monitor_registry():
    uploaded_file = request.files.get("xlsx_file")

    if uploaded_file is None or not uploaded_file.filename:
        flash("Выберите XLSX-файл для импорта.", "danger")
        return redirect(url_for("investmap.investmap_rf_monitor"))

    source_filename = uploaded_file.filename.strip()

    if not source_filename.lower().endswith(".xlsx"):
        flash("Поддерживаются только файлы формата .xlsx.", "danger")
        return redirect(url_for("investmap.investmap_rf_monitor"))

    xlsx_bytes = uploaded_file.read()

    if not xlsx_bytes:
        flash("Выбранный XLSX-файл пустой.", "danger")
        return redirect(url_for("investmap.investmap_rf_monitor"))

    conn = get_db()

    try:
        report = import_monitored_cards_xlsx(
            conn=conn,
            xlsx_bytes=xlsx_bytes,
            source_filename=source_filename,
        )
        conn.commit()
    except (RuntimeError, ValueError) as exc:
        conn.rollback()
        flash(f"Импорт не выполнен: {exc}", "danger")
        return redirect(url_for("investmap.investmap_rf_monitor"))
    except Exception:
        conn.rollback()
        current_app.logger.exception(
            "Ошибка импорта реестра мониторинга Инвесткарты РФ."
        )
        flash("Импорт не выполнен из-за внутренней ошибки.", "danger")
        return redirect(url_for("investmap.investmap_rf_monitor"))
    finally:
        conn.close()

    flash(
        "Импорт завершён: "
        f"свободных строк — {report['free_rows']}, "
        f"добавлено — {report['added']}, "
        f"реактивировано — {report['reactivated']}, "
        f"снято с мониторинга — {report['deactivated']}, "
        f"некорректных строк — {report['invalid_rows']}.",
        "success",
    )

    return redirect(url_for("investmap.investmap_rf_monitor"))

@investmap_bp.route(
    "/investmap-rf/monitor/registry/<int:global_id>/deactivate-api-not-found",
    methods=["POST"],
)
@login_required
@admin_required
def deactivate_investmap_rf_monitor_registry_card(global_id):
    """Снимает карточку с мониторинга после подтверждённого HTTP 404."""
    user_id = getattr(g, "user", {}).get("id")
    conn = get_db()

    try:
        result = deactivate_card_not_found_in_api(
            conn,
            global_id=global_id,
            changed_by_user_id=user_id,
        )

        if not log_action(
            conn,
            user_id,
            "investmap_rf_registry_deactivate_api_not_found",
            detail=f"global_id={global_id}",
        ):
            raise RuntimeError(
                "Не удалось записать действие снятия с мониторинга."
            )

        conn.commit()

    except ValueError as exc:
        conn.rollback()
        flash(str(exc), "danger")

    except Exception:
        conn.rollback()
        current_app.logger.exception(
            "Ошибка ручного снятия с мониторинга Инвесткарты РФ: "
            "global_id=%s",
            global_id,
        )
        flash(
            "Не удалось снять площадку с мониторинга из-за внутренней ошибки.",
            "danger",
        )

    else:
        flash(
            f"Площадка {result['global_id']} снята с мониторинга: "
            "внешний API подтвердил отсутствие карточки (HTTP 404).",
            "success",
        )

    finally:
        conn.close()

    return redirect(_registry_monitor_url(global_id))

@investmap_bp.route(
    "/investmap-rf/monitor/registry/<int:global_id>/refresh",
    methods=["POST"],
)
@login_required
@permission_required("can_refresh_investmap_rf_cards")
def refresh_investmap_rf_monitor_registry_card(global_id):
    """Точечно обновляет одну активную площадку реестра."""
    user_id = getattr(g, "user", {}).get("id")
    conn = get_db()

    try:
        refresh_result = run_registry_card_refresh(
            conn,
            global_id=global_id,
        )
        item = refresh_result["item"]

        if item is None:
            raise RuntimeError("Точечное обновление не вернуло результат.")

        if item.status in {"new", "unchanged"}:
            result = record_card_api_check_success(
                conn,
                global_id=global_id,
            )
            if not log_action(
                conn,
                user_id,
                "investmap_rf_registry_refresh",
                detail=(
                    f"global_id={global_id}; "
                    f"status={item.status}; "
                    f"snapshot_id={item.snapshot_id}"
                ),
            ):
                raise RuntimeError(
                    "Не удалось записать действие точечного обновления."
                )
            conn.commit()

            message = (
                f"Площадка {result['global_id']} обновлена: "
                f"{'создан новый снимок' if item.status == 'new' else 'изменений нет'}."
            )
            flash(message, "success")
            return redirect(_registry_monitor_url(global_id))

        error = item.error or "Неизвестная ошибка обновления."
        if "Внешний API не нашёл карточку." in error:
            result = record_card_api_not_found(
                conn,
                global_id=global_id,
                changed_by_user_id=user_id,
            )
            if not log_action(
                conn,
                user_id,
                "investmap_rf_registry_api_not_found_detected",
                detail=f"global_id={global_id}",
            ):
                raise RuntimeError(
                    "Не удалось записать результат проверки карточки."
                )
            conn.commit()
            flash(
                f"Площадка {result['global_id']} не найдена на Инвесткарте РФ. "
                "Проверьте статус подписания на ГИС «Экономика» и примите "
                "решение в реестре активных площадок.",
                "warning",
            )
            return redirect(_registry_monitor_url(global_id))

        result = record_card_api_check_error(
            conn,
            global_id=global_id,
            error=error,
        )
        if not log_action(
            conn,
            user_id,
            "investmap_rf_registry_refresh_error",
            detail=f"global_id={global_id}; error={result['error']}",
        ):
            raise RuntimeError(
                "Не удалось записать ошибку точечного обновления."
            )
        conn.commit()
        flash(
            f"Площадка {result['global_id']}: обновление не выполнено. "
            "Ошибка сохранена в реестре.",
            "danger",
        )

    except ValueError as exc:
        conn.rollback()
        flash(str(exc), "danger")

    except Exception:
        conn.rollback()
        current_app.logger.exception(
            "Ошибка точечного обновления Инвесткарты РФ: global_id=%s",
            global_id,
        )
        flash(
            "Не удалось обновить площадку из-за внутренней ошибки.",
            "danger",
        )

    finally:
        conn.close()

    return redirect(_registry_monitor_url(global_id))


@investmap_bp.route(
    "/investmap-rf/monitor/registry/<int:global_id>/keep-not-found",
    methods=["POST"],
)
@login_required
@admin_required
def keep_investmap_rf_monitor_registry_card(global_id):
    """Оставляет площадку в мониторинге после решения оператора."""
    user_id = getattr(g, "user", {}).get("id")
    conn = get_db()

    try:
        result = keep_card_not_found_by_operator(
            conn,
            global_id=global_id,
            changed_by_user_id=user_id,
        )
        if not log_action(
            conn,
            user_id,
            "investmap_rf_registry_keep_not_found",
            detail=f"global_id={global_id}",
        ):
            raise RuntimeError("Не удалось записать решение оператора.")
        conn.commit()

    except ValueError as exc:
        conn.rollback()
        flash(str(exc), "danger")

    except Exception:
        conn.rollback()
        current_app.logger.exception(
            "Ошибка решения оставить площадку в мониторинге: global_id=%s",
            global_id,
        )
        flash("Не удалось сохранить решение оператора.", "danger")

    else:
        flash(
            f"Площадка {result['global_id']} оставлена в отслеживании.",
            "success",
        )

    finally:
        conn.close()

    return redirect(_registry_monitor_url(global_id))


@investmap_bp.route(
    "/investmap-rf/monitor/registry/<int:global_id>/"
    "deactivate-operator-not-found",
    methods=["POST"],
)
@login_required
@admin_required
def deactivate_investmap_rf_monitor_registry_card_by_operator(global_id):
    """Снимает площадку после решения оператора о её отсутствии на карте."""
    user_id = getattr(g, "user", {}).get("id")
    conn = get_db()

    try:
        result = deactivate_card_not_found_by_operator(
            conn,
            global_id=global_id,
            changed_by_user_id=user_id,
        )
        if not log_action(
            conn,
            user_id,
            "investmap_rf_registry_deactivate_operator_not_found",
            detail=f"global_id={global_id}",
        ):
            raise RuntimeError("Не удалось записать решение оператора.")
        conn.commit()

    except ValueError as exc:
        conn.rollback()
        flash(str(exc), "danger")

    except Exception:
        conn.rollback()
        current_app.logger.exception(
            "Ошибка ручного снятия площадки после проверки карты: "
            "global_id=%s",
            global_id,
        )
        flash("Не удалось снять площадку с мониторинга.", "danger")

    else:
        flash(
            f"Площадка {result['global_id']} снята с отслеживания "
            "по решению оператора.",
            "success",
        )

    finally:
        conn.close()

    return redirect(_registry_monitor_url(global_id))

@investmap_bp.route('/investmap/rf-monitor/<int:global_id>')
@login_required
@permission_required('can_view_investmap')
def investmap_rf_monitor_detail(global_id):
    """Read-only просмотр снимков и изменений одной карточки."""
    user = getattr(g, 'user', {}).get('login', 'unknown')
    db = None

    try:
        db = get_db()

        try:
            from_snapshot_id = _monitor_query_positive_int(
                "from_snapshot_id"
            )
            to_snapshot_id = _monitor_query_positive_int(
                "to_snapshot_id"
            )
        except ValueError as exc:
            flash(str(exc), "warning")
            from_snapshot_id = None
            to_snapshot_id = None

        monitor_card = get_monitor_card_detail(
            db,
            global_id,
            from_snapshot_id=from_snapshot_id,
            to_snapshot_id=to_snapshot_id,
        ))

        if monitor_card is None:
            flash(
                f'Снимки для карточки {global_id} пока не найдены.',
                'error',
            )
            return redirect(url_for('investmap.investmap_rf_monitor'))

        return render_template(
            'investmap_rf_monitor_detail.html',
            monitor_card=monitor_card,
        )
    except Exception as exc:
        err_logger.exception(
            'investmap_rf_monitor_detail error | '
            'user=%s | global_id=%s | %s',
            user,
            global_id,
            exc,
        )
        flash(
            'Ошибка при загрузке карточки мониторинга Инвесткарты РФ.',
            'error',
        )
        return redirect(url_for('investmap.investmap_rf_monitor'))
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                err_logger.exception(
                    'investmap_rf_monitor_detail close error | '
                    'user=%s | global_id=%s',
                    user,
                    global_id,
                )

@investmap_bp.route('/investmap/v1')
@login_required
@permission_required('can_view_investmap')
def investmap_v1():
    """Анализ заполняемости (ГИС ЭКОНОМИКА) — перенесено с /investmap."""
    return render_template('investmap_v1.html')


@investmap_bp.route('/investmap/v2')
@login_required
@permission_required('can_view_investmap')
def investmap_v2():
    """Анализ заполняемости v2 — страница с кнопкой «Правила» и счётчиком правил."""
    user = getattr(g, 'user', {}).get('login', 'unknown')
    db = None
    try:
        db = get_db()
        rules_count = db.execute(
            'SELECT COUNT(*) FROM investmap_rules'
        ).fetchone()[0]
        return render_template('investmap_v2.html', rules_count=rules_count)
    except Exception as exc:
        err_logger.exception(
            'investmap_v2 error | user=%s | %s', user, exc
        )
        flash('Ошибка при загрузке страницы анализа v2.', 'error')
        return render_template('investmap_v2.html', rules_count=0), 500
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                err_logger.exception('investmap_v2 close error | user=%s', user)

def _history_comparison_payload(comparison):
    """Преобразует service comparison в JSON contract history endpoint."""
    return {
        "available": comparison["comparison_available"],
        "reason": comparison["reason"],
        "new_sites": comparison["new_sites"],
        "removed_sites": comparison["removed_sites"],
        "changed_source_sites": comparison["changed_source_sites"],
        "improved_sites": comparison["improved_sites"],
        "worsened_sites": comparison["worsened_sites"],
        "changed_inclusion_sites": comparison["changed_inclusion_sites"],
        "error_sites": comparison["error_sites"],
        "changes_total": comparison["changes_total"],
    }


def _history_query_int(name, default):
    """Читает неотрицательный integer query parameter."""
    value = request.args.get(name)

    if value is None:
        return default

    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValueError(f"invalid {name}") from None


@investmap_bp.route("/investmap/v2/history")
@login_required
@permission_required("can_view_investmap")
def investmap_v2_history():
    """Возвращает metadata последних history runs и их comparison."""
    db = None

    try:
        db = get_db()
        latest = get_latest_run_metadata(db)

        if latest is None:
            return jsonify({
                "latest": None,
                "previous": None,
                "comparison": {
                    "available": False,
                    "reason": "no_runs",
                },
            })

        previous = get_immediately_previous_run_metadata(
            db,
            latest["id"],
        )

        if previous is None:
            return jsonify({
                "latest": latest,
                "previous": None,
                "comparison": {
                    "available": False,
                    "reason": "no_previous_run",
                },
            })

        comparison = get_run_comparison(
            db,
            latest["id"],
            previous["id"],
        )

        return jsonify({
            "latest": latest,
            "previous": previous,
            "comparison": _history_comparison_payload(comparison),
        })

    except Exception as exc:
        err_logger.exception("investmap_v2_history error | %s", exc)
        return jsonify({"error": "Внутренняя ошибка сервера"}), 500
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                err_logger.exception("investmap_v2_history close error")


@investmap_bp.route("/investmap/v2/history/runs/<int:run_id>/changes")
@login_required
@permission_required("can_view_investmap")
def investmap_v2_history_changes(run_id):
    """Возвращает постраничный compact список изменений history run."""
    db = None

    try:
        try:
            limit = _history_query_int("limit", 25)
            offset = _history_query_int("offset", 0)
        except ValueError:
            return jsonify({"error": "Некорректные параметры пагинации"}), 400

        kind = request.args.get("kind", "all")

        db = get_db()

        try:
            previous = get_immediately_previous_run_metadata(db, run_id)
        except ValueError:
            return jsonify({"error": "History run не найден"}), 404

        if previous is None:
            return jsonify({
                "reason": "no_previous_run",
            }), 409

        try:
            changes = get_paginated_run_changes(
                db,
                run_id,
                previous["id"],
                kind=kind,
                limit=limit,
                offset=offset,
            )
        except ValueError as exc:
            message = str(exc)

            if message == "previous_run_id is not immediately previous":
                return jsonify({"error": "History run не найден"}), 404

            return jsonify({
                "error": "Некорректные параметры history changes",
            }), 400

        if not changes["comparison_available"]:
            return jsonify({
                "reason": changes["reason"],
            }), 409

        return jsonify(changes)

    except Exception as exc:
        err_logger.exception(
            "investmap_v2_history_changes error | run_id=%s | %s",
            run_id,
            exc,
        )
        return jsonify({"error": "Внутренняя ошибка сервера"}), 500
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                err_logger.exception(
                    "investmap_v2_history_changes close error"
                )

@investmap_bp.route('/investmap/v2', methods=['POST'])
@login_required
@permission_required('can_investmap_rules')
def investmap_v2_post():
    """
    Batch-оценка площадок через calc_portal_score_v2.

    Для format 2 создаёт history run в той же транзакции, что и activity log.
    Format 1/3 сохраняют текущий одиночный сценарий без history run.
    """
    f = request.files.get('file')
    if not f:
        return jsonify({'error': 'Файл не передан', 'results': [], 'count': 0}), 400
    if not f.filename.lower().endswith('.xlsx'):
        return jsonify({
            'error': 'Поддерживается только формат .xlsx',
            'results': [],
            'count': 0,
        }), 400

    user = getattr(g, 'user', {}).get('login', 'unknown')
    user_id = getattr(g, 'user', {}).get('id')
    db = None

    try:
        file_bytes = f.read()
        export = convert_excel_to_text(file_bytes)

        if export.get('error'):
            return jsonify({
                'results': [],
                'count': 0,
                'error': export['error'],
            }), 400

        data = export.get('data', {})
        fmt = export.get('format')
        db = get_db()

        history_summary = None

        if fmt == 2 and isinstance(data, list):
            batch_result = run_batch_history(
                conn=db,
                source_rows=data,
                score_fn=lambda row: calc_portal_score_v2(_build_v2_source_row(row, db), db),
                formula_version=V2_FORMULA_VERSION,
                initiated_by=user_id,
                source_label=f.filename,
            )

            results = [
                _history_item_to_api_result(item)
                for item in batch_result['results']
            ]

            history_summary = {
                'run_id': batch_result['run_id'],
                'total_sites': batch_result['total_sites'],
                'active_sites': batch_result['active_sites'],
                'excluded_sites': batch_result['excluded_sites'],
                'error_sites': batch_result['error_sites'],
            }

            sms_pairs = _active_sms_pairs(results, data)
            sms_results = [result for result, _ in sms_pairs]
            sms_rows = [row for _, row in sms_pairs]
            summary_sms = (
                build_v2_summary_sms(sms_results, sms_rows)
                if len(sms_results) > 1
                else None
            )
        else:
            results = [calc_portal_score_v2(_build_v2_source_row(data, db), db)]
            summary_sms = None

        export_payload = {
            'format': fmt,
            'count': export.get('count', len(results)),
        }

        if fmt == 2:
            export_payload['texts'] = export.get('texts', [])
        else:
            text = export.get('text', '')
            export_payload['text'] = text
            export_payload['texts'] = [text]

        if not log_action(
            db,
            user_id,
            'investmap_v2_score',
            detail=f'count={len(results)}',
        ):
            raise RuntimeError('Не удалось записать действие investmap_v2_score')

        db.commit()

        return jsonify({
            'results': results,
            'count': len(results),
            'summary_sms': summary_sms,
            'export': export_payload,
            'error': None,
            'history': history_summary,
        })

    except Exception as exc:
        if db is not None:
            try:
                db.rollback()
            except Exception:
                err_logger.exception(
                    'investmap_v2 POST rollback error | user=%s',
                    user,
                )

        err_logger.exception('investmap_v2 POST error | user=%s | %s', user, exc)
        return jsonify({
            'results': [],
            'count': 0,
            'error': 'Внутренняя ошибка сервера',
        }), 500
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                err_logger.exception(
                    'investmap_v2 POST close error | user=%s',
                    user,
                )


@investmap_bp.route('/investmap/v2/rules')
@login_required
@permission_required('can_investmap_rules')
def investmap_v2_rules():
    """Список правил рекомендаций."""
    db = None
    try:
        db = get_db()
        rules = db.execute("""
            SELECT r.id, r.source_field, r.source_value,
                   r.target_field, r.recommended_text,
                   sf.display_name AS source_display,
                   tf.display_name AS target_display
            FROM investmap_rules r
            LEFT JOIN investmap_fields sf ON sf.tech_name = r.source_field
            LEFT JOIN investmap_fields tf ON tf.tech_name = r.target_field
            ORDER BY r.id
        """).fetchall()
        fields = db.execute(
            "SELECT tech_name, display_name FROM investmap_fields ORDER BY display_name"
        ).fetchall()
        return render_template('investmap_v2_rules.html', rules=rules, fields=fields)
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                err_logger.exception('investmap_v2_rules close error')


@investmap_bp.route('/investmap/v2/rules/add', methods=['POST'])
@login_required
@permission_required('can_investmap_rules')
def investmap_v2_rules_add():
    """Добавить новое правило рекомендации."""
    source_field = request.form.get('source_field', '').strip()
    source_value = request.form.get('source_value', '').strip()
    target_field = request.form.get('target_field', '').strip()
    recommended_text = request.form.get('recommended_text', '').strip()

    if not all([source_field, source_value, target_field, recommended_text]):
        flash('Все поля обязательны.', 'error')
        return redirect(url_for('investmap.investmap_v2_rules'))

    user = getattr(g, 'user', {}).get('login', 'unknown')
    db = None
    try:
        db = get_db()
        db.execute(
            """INSERT INTO investmap_rules
               (source_field, source_value, target_field, recommended_text)
               VALUES (?, ?, ?, ?)""",
            (source_field, source_value, target_field, recommended_text)
        )
        db.commit()
        log_action(db, getattr(g, 'user', {}).get('id'), 'investmap_rules_add',
                   detail=f'source={source_field}:{source_value} → target={target_field}')
        flash('Правило добавлено.', 'success')
        return redirect(url_for('investmap.investmap_v2_rules'))
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                err_logger.exception('investmap_v2_rules_add close error | user=%s', user)


@investmap_bp.route('/investmap/v2/rules/delete/<int:rule_id>', methods=['POST'])
@login_required
@permission_required('can_investmap_rules')
def investmap_v2_rules_delete(rule_id):
    """Удалить правило рекомендации."""
    user = getattr(g, 'user', {}).get('login', 'unknown')
    db = None
    try:
        db = get_db()
        db.execute("DELETE FROM investmap_rules WHERE id = ?", (rule_id,))
        db.commit()
        log_action(db, getattr(g, 'user', {}).get('id'), 'investmap_rules_delete',
                   detail=f'rule_id={rule_id}')
        flash('Правило удалено.', 'success')
        return redirect(url_for('investmap.investmap_v2_rules'))
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                err_logger.exception('investmap_v2_rules_delete close error | user=%s', user)


@investmap_bp.route('/investmap/v2/rules/values')
@login_required
@permission_required('can_investmap_rules')
def investmap_v2_rules_values():
    """
    AJAX: вернуть список значений классификатора для выбранного source_field.

    GET /investmap/v2/rules/values?field=<tech_name>
    Возвращает JSON: list[str]
    """
    tech_name = request.args.get('field', '').strip()
    if not tech_name:
        return jsonify([])
    db = None
    try:
        db = get_db()
        row = db.execute(
            "SELECT classifier_num FROM investmap_fields WHERE tech_name = ?",
            (tech_name,)
        ).fetchone()
        if not row or not row['classifier_num']:
            return jsonify([])
        values = db.execute(
            "SELECT value FROM investmap_classifiers WHERE classifier_num = ? ORDER BY value",
            (row['classifier_num'],)
        ).fetchall()
        return jsonify([v['value'] for v in values])
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                err_logger.exception('investmap_v2_rules_values close error')


@investmap_bp.route('/investmap/convert', methods=['POST'])
@login_required
@permission_required('can_view_investmap')
def investmap_convert():
    """Только конвертация в текст — без анализа. Используется для отправки в AI-чат."""
    f = request.files.get('file')
    if not f:
        return jsonify({'error': 'Файл не передан'}), 400
    if not f.filename.lower().endswith('.xlsx'):
        return jsonify({'error': 'Поддерживается только формат .xlsx'}), 400

    result = convert_excel_to_text(f.read())
    if not result.get('error'):
        db = None
        try:
            db = get_db()
            log_action(db, getattr(g, 'user', {}).get('id'), 'investmap_convert',
                       detail=f'file={f.filename}')
        finally:
            if db is not None:
                try:
                    db.close()
                except Exception:
                    err_logger.exception('investmap_convert close error')
    return jsonify(result)


@investmap_bp.route('/investmap/analyze', methods=['POST'])
@login_required
@permission_required('can_investmap_rules')
@permission_required('can_view_investmap')
def investmap_analyze():
    """
    Полный анализ карточки инвестплощадки.

    POST /investmap/analyze
    Content-Type: multipart/form-data
    file: .xlsx
    """
    f = request.files.get('file')
    if not f:
        return jsonify({'error': 'Файл не передан'}), 400
    if not f.filename.lower().endswith('.xlsx'):
        return jsonify({'error': 'Поддерживается только формат .xlsx'}), 400

    file_bytes = f.read()
    export = convert_excel_to_text(file_bytes)

    if export.get('error'):
        return jsonify({
            'export': export,
            'analysis': None,
            'summary_sms': None,
            'error': export['error']
        }), 400

    data = export.get('data', {})
    fmt = export.get('format')

    if fmt == 2 and isinstance(data, list):
        analysis = [analyze(d) for d in data]
        summary_sms = build_summary_sms(analysis)
    else:
        analysis = analyze(data)
        summary_sms = None

    return jsonify({
        'export': {
            'format': fmt,
            'count': export.get('count', 1),
            'text': export.get('text', '')
        },
        'analysis': analysis,
        'summary_sms': summary_sms,
        'error': None
    })
