"""
Миграции для модуля «Актуализация данных Инвесткарты».

Модуль хранит:
- категории (слои) актуализации;
- внешние организации — поставщики данных;
- матрицу получателей по категориям;
- квартальные планы и их строки;
- документы запросов и ответов.

Фактическое выполнение миграции подключается отдельно из migrations.py.
"""

from __future__ import annotations

import sqlite3


UPDATE_CATEGORIES = [
    ("regional_analytics", "Аналитика региона", 10),
    ("business_environment", "Деловое окружение", 20),
    ("investment_sites", "Инвест-площадки", 30),
    ("investment_proposals", "Инвест-предложения", 40),
    ("cultural_heritage", "ОКН", 50),
    ("minerals", "Полезные ископаемые", 60),
    ("regional_support_measures", "Региональные меры поддержки", 70),
]


UPDATE_ORGANIZATIONS = [
    "АО «Корпорация развития Нижегородской области»",
    "АНО «АСИРИС»",
    "ДОМ.РФ",
    "Министерство сельского хозяйства и продовольственных ресурсов Нижегородской области",
    "Министерство спорта Нижегородской области",
    "Министерство туризма и промыслов Нижегородской области",
    "Министерство промышленности, торговли и предпринимательства Нижегородской области",
    "Министерство цифрового развития и связи Нижегородской области",
    "Министерство экономического развития и инвестиций Нижегородской области",
    "Министерство экологии и природных ресурсов Нижегородской области",
    "Министерство энергетики и жилищно-коммунального хозяйства Нижегородской области",
    "Управление государственной охраны объектов культурного наследия Нижегородской области",
]


CATEGORY_ORGANIZATION_RULES = {
    "regional_analytics": [
        "Министерство экономического развития и инвестиций Нижегородской области",
        "АО «Корпорация развития Нижегородской области»",
    ],
    "business_environment": [
        "Министерство промышленности, торговли и предпринимательства Нижегородской области",
    ],
    "investment_proposals": [
        "АО «Корпорация развития Нижегородской области»",
    ],
    "cultural_heritage": [
        "АНО «АСИРИС»",
        "Управление государственной охраны объектов культурного наследия Нижегородской области",
        "ДОМ.РФ",
    ],
    "minerals": [
        "Министерство экологии и природных ресурсов Нижегородской области",
    ],
    "regional_support_measures": [
        "Министерство сельского хозяйства и продовольственных ресурсов Нижегородской области",
        "Министерство экономического развития и инвестиций Нижегородской области",
        "Министерство спорта Нижегородской области",
        "Министерство цифрового развития и связи Нижегородской области",
        "Министерство промышленности, торговли и предпринимательства Нижегородской области",
        "Министерство туризма и промыслов Нижегородской области",
        "Министерство энергетики и жилищно-коммунального хозяйства Нижегородской области",
        "Управление государственной охраны объектов культурного наследия Нижегородской области",
    ],
}


def migrate_investmap_data_updates(conn: sqlite3.Connection) -> None:
    """Создаёт схему и начальные данные модуля актуализации Инвесткарты."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS investmap_update_categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL UNIQUE,
            sort_order INTEGER NOT NULL DEFAULT 0,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS investmap_update_organizations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS investmap_update_recipient_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category_id INTEGER NOT NULL,
            recipient_mode TEXT NOT NULL
                CHECK (recipient_mode IN ('all_districts', 'organization')),
            organization_id INTEGER,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(category_id) REFERENCES investmap_update_categories(id),
            FOREIGN KEY(organization_id) REFERENCES investmap_update_organizations(id),
            CHECK (
                (recipient_mode = 'all_districts' AND organization_id IS NULL)
                OR
                (recipient_mode = 'organization' AND organization_id IS NOT NULL)
            ),
            UNIQUE(category_id, recipient_mode, organization_id)
        );

        CREATE TABLE IF NOT EXISTS investmap_update_plan_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            period_start TEXT NOT NULL,
            period_end TEXT NOT NULL,
            created_by_user_id INTEGER,
            note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CHECK (date(period_start) <= date(period_end)),
            FOREIGN KEY(created_by_user_id) REFERENCES users(id),
            UNIQUE(period_start, period_end)
        );

        CREATE TABLE IF NOT EXISTS investmap_update_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_run_id INTEGER NOT NULL,
            category_id INTEGER NOT NULL,
            source_type TEXT NOT NULL
                CHECK (source_type IN ('district', 'organization')),
            district_id INTEGER,
            organization_id INTEGER,
            source_name_snapshot TEXT NOT NULL,
            request_letter_number TEXT NOT NULL DEFAULT '',
            response_letter_number TEXT NOT NULL DEFAULT '',
            changes_count INTEGER NOT NULL DEFAULT 0
                CHECK (changes_count >= 0),
            status TEXT NOT NULL DEFAULT 'planned'
                CHECK (
                    status IN (
                        'planned',
                        'request_sent',
                        'response_received',
                        'no_changes',
                        'overdue'
                    )
                ),
            note TEXT NOT NULL DEFAULT '',
            created_by_user_id INTEGER,
            updated_by_user_id INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(plan_run_id) REFERENCES investmap_update_plan_runs(id),
            FOREIGN KEY(category_id) REFERENCES investmap_update_categories(id),
            FOREIGN KEY(district_id) REFERENCES districts(id),
            FOREIGN KEY(organization_id) REFERENCES investmap_update_organizations(id),
            FOREIGN KEY(created_by_user_id) REFERENCES users(id),
            FOREIGN KEY(updated_by_user_id) REFERENCES users(id),
            CHECK (
                (source_type = 'district'
                    AND district_id IS NOT NULL
                    AND organization_id IS NULL)
                OR
                (source_type = 'organization'
                    AND organization_id IS NOT NULL
                    AND district_id IS NULL)
            ),
            UNIQUE(plan_run_id, category_id, source_type, source_name_snapshot)
        );

        CREATE TABLE IF NOT EXISTS investmap_update_documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            record_id INTEGER NOT NULL,
            document_type TEXT NOT NULL
                CHECK (document_type IN ('request', 'response')),
            original_name TEXT NOT NULL,
            stored_name TEXT NOT NULL UNIQUE,
            uploaded_by_user_id INTEGER,
            uploaded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(record_id) REFERENCES investmap_update_records(id)
                ON DELETE CASCADE,
            FOREIGN KEY(uploaded_by_user_id) REFERENCES users(id)
        );

        CREATE INDEX IF NOT EXISTS idx_investmap_update_categories_active
        ON investmap_update_categories(is_active, sort_order, name);

        CREATE INDEX IF NOT EXISTS idx_investmap_update_organizations_active
        ON investmap_update_organizations(is_active, name);

        CREATE INDEX IF NOT EXISTS idx_investmap_update_recipient_rules_category
        ON investmap_update_recipient_rules(category_id, is_active);

        CREATE INDEX IF NOT EXISTS idx_investmap_update_plan_runs_period
        ON investmap_update_plan_runs(period_start, period_end);

        CREATE INDEX IF NOT EXISTS idx_investmap_update_records_plan_category
        ON investmap_update_records(plan_run_id, category_id);

        CREATE INDEX IF NOT EXISTS idx_investmap_update_records_source
        ON investmap_update_records(source_type, district_id, organization_id);

        CREATE INDEX IF NOT EXISTS idx_investmap_update_documents_record
        ON investmap_update_documents(record_id, document_type);
        """
    )

    for code, name, sort_order in UPDATE_CATEGORIES:
        conn.execute(
            """
            INSERT INTO investmap_update_categories (
                code, name, sort_order, is_active
            )
            VALUES (?, ?, ?, 1)
            ON CONFLICT(code) DO UPDATE SET
                name = excluded.name,
                sort_order = excluded.sort_order,
                updated_at = CURRENT_TIMESTAMP
            """,
            (code, name, sort_order),
        )

    for name in UPDATE_ORGANIZATIONS:
        conn.execute(
            """
            INSERT INTO investmap_update_organizations (name, is_active)
            VALUES (?, 1)
            ON CONFLICT(name) DO NOTHING
            """,
            (name,),
        )

    category_ids = {
        row["code"]: row["id"]
        for row in conn.execute(
            """
            SELECT id, code
            FROM investmap_update_categories
            """
        ).fetchall()
    }

    organization_ids = {
        row["name"]: row["id"]
        for row in conn.execute(
            """
            SELECT id, name
            FROM investmap_update_organizations
            """
        ).fetchall()
    }

    investment_sites_category_id = category_ids["investment_sites"]
    conn.execute(
        """
        INSERT INTO investmap_update_recipient_rules (
            category_id, recipient_mode, organization_id, is_active
        )
        VALUES (?, 'all_districts', NULL, 1)
        ON CONFLICT(category_id, recipient_mode, organization_id) DO NOTHING
        """,
        (investment_sites_category_id,),
    )

    for category_code, organization_names in CATEGORY_ORGANIZATION_RULES.items():
        category_id = category_ids[category_code]
        for organization_name in organization_names:
            conn.execute(
                """
                INSERT INTO investmap_update_recipient_rules (
                    category_id, recipient_mode, organization_id, is_active
                )
                VALUES (?, 'organization', ?, 1)
                ON CONFLICT(category_id, recipient_mode, organization_id)
                DO NOTHING
                """,
                (category_id, organization_ids[organization_name]),
            )
