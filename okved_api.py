# ╔══════════════════════════════════════════════════════════════╗
# ║                        okved_api.py                          ║
# ║  Публичный API для фронта по ОКВЭД:                          ║
# ║  - поиск кодов по коду или наименованию                      ║
# ╚══════════════════════════════════════════════════════════════╝

from flask import Blueprint, request, jsonify

from db import get_db
from auth_utils import login_required


# ─── НАСТРОЙКИ BLUEPRINT ─────────────────────────────────────────────────────

okved_api_bp = Blueprint('okved_api', __name__, url_prefix='/api/okved')


# ─── ПОИСК ОКВЭД ─────────────────────────────────────────────────────────────

@okved_api_bp.route('/search')
@login_required
def okved_search():
    """
    Поиск кодов ОКВЭД по коду и/или наименованию.

    Query-параметры:
      q      — строка поиска (обязательна, если пустая → пустой список),
      limit  — максимальное количество результатов (по умолчанию 10, максимум 50).

    Логика:
      - фильтр по is_active=1;
      - совпадения по началу кода (code LIKE 'q%') и по части имени (name LIKE '%q%');
      - сначала в выдаче те записи, где совпадает код (приоритет code LIKE).

    Возвращает JSON:
      [
        {"code": "...", "name": "...", "parent_code": "..."},
        ...
      ]
    """
    q = (request.args.get('q', '') or '').strip()
    if not q:
        return jsonify([])

    try:
        limit = int(request.args.get('limit', 10))
    except ValueError:
        limit = 10
    limit = max(1, min(limit, 50))

    conn = get_db()
    like_code = f"{q}%"
    like_name = f"%{q}%"

    rows = conn.execute(
        """
        SELECT code, name, parent_code
        FROM okved
        WHERE is_active=1
          AND (code LIKE ? OR name LIKE ?)
        ORDER BY 
          CASE WHEN code LIKE ? THEN 0 ELSE 1 END,
          code
        LIMIT ?
        """,
        (like_code, like_name, like_code, limit)
    ).fetchall()
    conn.close()

    result = [
        {
            "code": r["code"],
            "name": r["name"],
            "parent_code": r["parent_code"],
        }
        for r in rows
    ]
    return jsonify(result)