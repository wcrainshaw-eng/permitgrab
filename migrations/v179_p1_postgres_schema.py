"""V179 Phase 1: Create Postgres schemas for every SQLite-only table.
Also add missing columns to existing Postgres tables.

Run on Render Shell:
  python3 migrations/v179_p1_postgres_schema.py
"""
import os, sys, psycopg2

def run():
    conn = psycopg2.connect(os.environ['DATABASE_URL'])
    conn.autocommit = True
    cur = conn.cursor()

    # ---- NEW TABLES (SQLite-only → create in Postgres) ----

    cur.execute("""
        CREATE TABLE IF NOT EXISTS bulk_sources (
            id BIGSERIAL PRIMARY KEY,
            source_key TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            scope_type TEXT NOT NULL,
            scope_name TEXT NOT NULL,
            state TEXT,
            platform TEXT NOT NULL,
            endpoint TEXT NOT NULL,
            dataset_id TEXT,
            field_map TEXT,
            date_field TEXT,
            city_field TEXT,
            limit_per_page INTEGER DEFAULT 2000,
            status TEXT DEFAULT 'active',
            consecutive_failures INTEGER DEFAULT 0,
            last_failure_reason TEXT,
            last_collected_at TEXT,
            total_permits_collected INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (to_char(NOW(), 'YYYY-MM-DD HH24:MI:SS')),
            updated_at TEXT DEFAULT (to_char(NOW(), 'YYYY-MM-DD HH24:MI:SS'))
        )
    """)
    print("  bulk_sources: created")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS cities (
            city_slug TEXT PRIMARY KEY,
            city TEXT NOT NULL,
            state TEXT NOT NULL,
            population INTEGER DEFAULT 0,
            platform TEXT,
            endpoint TEXT,
            dataset_id TEXT,
            date_field TEXT,
            field_map TEXT,
            scraper_config TEXT,
            status TEXT DEFAULT 'pending',
            last_collected_at TEXT,
            last_success_at TEXT,
            last_error TEXT,
            permits_total INTEGER DEFAULT 0,
            permits_7d INTEGER DEFAULT 0,
            last_run_permits_found INTEGER DEFAULT 0,
            last_run_permits_inserted INTEGER DEFAULT 0,
            created_at TEXT,
            updated_at TEXT,
            notes TEXT
        )
    """)
    print("  cities: created")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS city_research (
            id BIGSERIAL PRIMARY KEY,
            city TEXT NOT NULL,
            state TEXT NOT NULL,
            population INTEGER DEFAULT 0,
            status TEXT DEFAULT 'untested',
            portal_url TEXT,
            dataset_id TEXT,
            platform TEXT,
            date_field TEXT,
            address_field TEXT,
            notes TEXT,
            tested_at TEXT,
            onboarded_at TEXT,
            created_at TEXT
        )
    """)
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_cr_city_state ON city_research(city, state)")
    print("  city_research: created")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS digest_log (
            id BIGSERIAL PRIMARY KEY,
            sent_at TEXT,
            recipient_email TEXT,
            permits_count INTEGER DEFAULT 0,
            cities_included TEXT,
            status TEXT,
            error_message TEXT
        )
    """)
    print("  digest_log: created")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS sources (
            source_key TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            scope_type TEXT NOT NULL DEFAULT 'city',
            state TEXT,
            population INTEGER DEFAULT 0,
            platform TEXT NOT NULL,
            endpoint TEXT NOT NULL DEFAULT '',
            dataset_id TEXT,
            field_map TEXT DEFAULT '{}',
            date_field TEXT DEFAULT 'date',
            city_field TEXT,
            limit_per_page INTEGER,
            status TEXT DEFAULT 'pending',
            pause_reason TEXT,
            last_attempt_at TEXT,
            last_attempt_status TEXT,
            last_attempt_error TEXT,
            last_attempt_duration_ms INTEGER,
            last_success_at TEXT,
            consecutive_failures INTEGER DEFAULT 0,
            last_permits_found INTEGER DEFAULT 0,
            last_permits_inserted INTEGER DEFAULT 0,
            total_permits INTEGER DEFAULT 0,
            newest_permit_date TEXT,
            covers_cities TEXT,
            created_at TEXT,
            updated_at TEXT,
            notes TEXT,
            data_type TEXT NOT NULL DEFAULT 'permits',
            verified_at TEXT,
            last_verified_at TEXT,
            verification_status TEXT DEFAULT 'pending',
            days_consecutive INTEGER DEFAULT 0
        )
    """)
    print("  sources: created")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS subscribers (
            id BIGSERIAL PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            name TEXT,
            plan TEXT DEFAULT 'free',
            digest_cities TEXT DEFAULT '[]',
            active INTEGER DEFAULT 1,
            created_at TEXT,
            last_digest_sent_at TEXT
        )
    """)
    print("  subscribers: created")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS sweep_sources (
            id BIGSERIAL PRIMARY KEY,
            city_slug TEXT NOT NULL,
            source_type TEXT NOT NULL,
            source_url TEXT NOT NULL,
            platform TEXT NOT NULL,
            dataset_id TEXT DEFAULT '',
            domain TEXT DEFAULT '',
            name TEXT DEFAULT '',
            city_column TEXT DEFAULT '',
            city_value TEXT DEFAULT '',
            status TEXT DEFAULT 'pending_test',
            permits_found INTEGER DEFAULT 0,
            discovered_at TEXT DEFAULT '',
            tested_at TEXT DEFAULT '',
            UNIQUE(city_slug, dataset_id)
        )
    """)
    print("  sweep_sources: created")

    # ---- ADD MISSING COLUMNS to existing Postgres tables ----

    # permits: add prod_city_id + normalized_address (if not exists)
    for col, typ in [('prod_city_id', 'INTEGER'), ('normalized_address', 'TEXT')]:
        try:
            cur.execute(f"ALTER TABLE permits ADD COLUMN IF NOT EXISTS {col} {typ}")
            print(f"  permits.{col}: added")
        except Exception as e:
            print(f"  permits.{col}: {e}")

    # prod_cities: add 12 missing columns from SQLite
    pc_cols = [
        ('consecutive_no_new', 'INTEGER DEFAULT 0'),
        ('last_run_status', 'TEXT'),
        ('population', 'INTEGER DEFAULT 0'),
        ('health_status', 'TEXT'),
        ('first_successful_collection', 'TEXT'),
        ('last_successful_collection', 'TEXT'),
        ('last_failure_reason', 'TEXT'),
        ('earliest_permit_date', 'TEXT'),
        ('latest_permit_date', 'TEXT'),
        ('days_since_new_data', 'INTEGER'),
        ('pipeline_checked_at', 'TEXT'),
        ('backfill_status', 'TEXT'),
    ]
    for col, typ in pc_cols:
        try:
            cur.execute(f"ALTER TABLE prod_cities ADD COLUMN IF NOT EXISTS {col} {typ}")
        except Exception as e:
            if 'already exists' not in str(e):
                print(f"  prod_cities.{col}: {e}")
    print("  prod_cities: 12 columns added/verified")

    # system_state: ensure it exists
    cur.execute("""
        CREATE TABLE IF NOT EXISTS system_state (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT
        )
    """)
    print("  system_state: created/verified")

    # Create indexes
    cur.execute("CREATE INDEX IF NOT EXISTS idx_permits_prod_city ON permits(prod_city_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_permits_norm_addr ON permits(normalized_address)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_permits_source_city ON permits(source_city_key)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_violations_norm_addr ON violations(normalized_address)")
    print("  indexes: created/verified")

    conn.close()
    print("\nPhase 1 complete. All schemas created.")

if __name__ == '__main__':
    run()
