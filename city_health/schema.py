"""V540 PR1: city_health table schema. Cross-dialect SQL — works on
Postgres + SQLite via the V525b ON CONFLICT pattern.

The `ensure_table()` function is idempotent — safe to call from both
init_db (server.py boot) and the migration script (V522 staging).
"""
from __future__ import annotations

import db as permitdb


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS city_health (
    city_slug      TEXT PRIMARY KEY,
    status         TEXT NOT NULL,
    reason_code    TEXT,
    reason_detail  TEXT,
    evidence_json  TEXT,
    computed_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

CREATE_INDEX_STATUS = (
    "CREATE INDEX IF NOT EXISTS idx_city_health_status ON city_health(status)"
)
CREATE_INDEX_COMPUTED_AT = (
    "CREATE INDEX IF NOT EXISTS idx_city_health_computed_at ON city_health(computed_at)"
)


def ensure_table():
    """Create city_health table + indexes if absent. Idempotent.

    Returns True on success, False on any DB error (logged to stdout).
    Safe to call from init_db at boot AND from the cron just before
    the first compute, so a fresh DB doesn't crash the scheduler.
    """
    try:
        conn = permitdb.get_connection()
        conn.execute(CREATE_TABLE_SQL)
        conn.execute(CREATE_INDEX_STATUS)
        conn.execute(CREATE_INDEX_COMPUTED_AT)
        conn.commit()
        return True
    except Exception as e:
        print(f'[city_health.schema] ensure_table failed: {e}', flush=True)
        return False
