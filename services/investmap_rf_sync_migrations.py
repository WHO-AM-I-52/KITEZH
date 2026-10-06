"""Миграции планов, истории и повторов синхронизации Инвесткарты РФ."""

def migrate_sync_plan_tables(conn):
    """Создаёт таблицы планов и истории пакетной синхронизации Инвесткарты РФ."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS investmap_rf_sync_plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            is_enabled INTEGER NOT NULL DEFAULT 0,
            batch_size INTEGER NOT NULL DEFAULT 5,
            interval_minutes INTEGER NOT NULL DEFAULT 10,
            status TEXT NOT NULL DEFAULT 'idle',
            next_run_at_utc TEXT,
            cursor_global_id INTEGER,
            current_cycle_started_at_utc TEXT,
            last_run_started_at_utc TEXT,
            last_run_finished_at_utc TEXT,
            last_error TEXT,
            stop_requested INTEGER NOT NULL DEFAULT 0,
            created_at_utc TEXT NOT NULL,
            updated_at_utc TEXT NOT NULL,
            created_by_user_id INTEGER,
            updated_by_user_id INTEGER
        );

        CREATE INDEX IF NOT EXISTS idx_investmap_rf_sync_plans_due
        ON investmap_rf_sync_plans(is_enabled, status, next_run_at_utc);

        CREATE TABLE IF NOT EXISTS investmap_rf_sync_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'running',
            started_at_utc TEXT NOT NULL,
            finished_at_utc TEXT,
            requested_cards_count INTEGER NOT NULL DEFAULT 0,
            processed_cards_count INTEGER NOT NULL DEFAULT 0,
            successful_cards_count INTEGER NOT NULL DEFAULT 0,
            failed_cards_count INTEGER NOT NULL DEFAULT 0,
            changed_cards_count INTEGER NOT NULL DEFAULT 0,
            started_by_user_id INTEGER,
            error_message TEXT,
            FOREIGN KEY(plan_id) REFERENCES investmap_rf_sync_plans(id)
        );

        CREATE INDEX IF NOT EXISTS idx_investmap_rf_sync_runs_plan
        ON investmap_rf_sync_runs(plan_id, id DESC);

        CREATE TABLE IF NOT EXISTS investmap_rf_sync_batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_id INTEGER NOT NULL,
            run_id INTEGER NOT NULL,
            batch_number INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            global_ids_json TEXT NOT NULL,
            started_at_utc TEXT,
            finished_at_utc TEXT,
            processed_cards_count INTEGER NOT NULL DEFAULT 0,
            successful_cards_count INTEGER NOT NULL DEFAULT 0,
            failed_cards_count INTEGER NOT NULL DEFAULT 0,
            changed_cards_count INTEGER NOT NULL DEFAULT 0,
            error_message TEXT,
            FOREIGN KEY(plan_id) REFERENCES investmap_rf_sync_plans(id),
            FOREIGN KEY(run_id) REFERENCES investmap_rf_sync_runs(id)
        );

        CREATE INDEX IF NOT EXISTS idx_investmap_rf_sync_batches_plan
        ON investmap_rf_sync_batches(plan_id, id DESC);

        CREATE INDEX IF NOT EXISTS idx_investmap_rf_sync_batches_run
        ON investmap_rf_sync_batches(run_id, batch_number);
        """
    )

    run_columns = {
        row["name"]
        for row in conn.execute(
            "PRAGMA table_info(investmap_rf_sync_runs)"
        ).fetchall()
    }
    if "global_ids_json" not in run_columns:
        conn.execute(
            """
            ALTER TABLE investmap_rf_sync_runs
            ADD COLUMN global_ids_json TEXT NOT NULL DEFAULT '[]'
            """
        )

    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_investmap_rf_sync_runs_active
        ON investmap_rf_sync_runs(status, plan_id, id DESC)
        """
    )

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS investmap_rf_daily_sync_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scheduled_date_msk TEXT NOT NULL UNIQUE,
            scheduled_at_utc TEXT NOT NULL,
            status TEXT NOT NULL,
            plan_id INTEGER,
            run_id INTEGER,
            reason TEXT,
            created_at_utc TEXT NOT NULL,
            FOREIGN KEY(plan_id) REFERENCES investmap_rf_sync_plans(id),
            FOREIGN KEY(run_id) REFERENCES investmap_rf_sync_runs(id)
        );

        CREATE INDEX IF NOT EXISTS idx_investmap_rf_daily_sync_runs_status
        ON investmap_rf_daily_sync_runs(status, scheduled_date_msk DESC);
        """
    )


    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS investmap_rf_sync_schedule (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            plan_id INTEGER UNIQUE,
            is_enabled INTEGER NOT NULL DEFAULT 1
                CHECK (is_enabled IN (0, 1)),
            frequency TEXT NOT NULL DEFAULT 'weekdays'
                CHECK (frequency IN ('daily', 'weekly', 'weekdays', 'monthly')),
            start_time_msk TEXT NOT NULL DEFAULT '13:00'
                CHECK (
                    length(start_time_msk) = 5
                    AND start_time_msk GLOB '[0-2][0-9]:[0-5][0-9]'
                    AND substr(start_time_msk, 1, 2) <= '23'
                ),
            weekday INTEGER NOT NULL DEFAULT 0
                CHECK (weekday BETWEEN 0 AND 6),
            month_day INTEGER NOT NULL DEFAULT 1
                CHECK (month_day BETWEEN 1 AND 28),
            updated_at_utc TEXT NOT NULL
                DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
            updated_by_user_id INTEGER,
            FOREIGN KEY(plan_id) REFERENCES investmap_rf_sync_plans(id)
                ON DELETE SET NULL
        );

        INSERT OR IGNORE INTO investmap_rf_sync_schedule (id)
        VALUES (1);
        """
    )

def migrate_sync_retry_tables(conn):
    """Создаёт очередь повторной синхронизации ошибочных площадок."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS investmap_rf_sync_retry_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_id INTEGER NOT NULL,
            source_run_id INTEGER NOT NULL,
            global_ids_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            requested_cards_count INTEGER NOT NULL DEFAULT 0,
            processed_cards_count INTEGER NOT NULL DEFAULT 0,
            successful_cards_count INTEGER NOT NULL DEFAULT 0,
            failed_cards_count INTEGER NOT NULL DEFAULT 0,
            changed_cards_count INTEGER NOT NULL DEFAULT 0,
            error_message TEXT,
            created_at_utc TEXT NOT NULL,
            started_at_utc TEXT,
            finished_at_utc TEXT,
            created_by_user_id INTEGER,
            FOREIGN KEY(plan_id) REFERENCES investmap_rf_sync_plans(id),
            FOREIGN KEY(source_run_id) REFERENCES investmap_rf_sync_runs(id)
        );

        CREATE INDEX IF NOT EXISTS idx_investmap_rf_sync_retry_jobs_plan
            ON investmap_rf_sync_retry_jobs(plan_id, id DESC);

        CREATE INDEX IF NOT EXISTS idx_investmap_rf_sync_retry_jobs_status
            ON investmap_rf_sync_retry_jobs(status, id ASC);
        """
    )
