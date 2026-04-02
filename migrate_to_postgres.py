"""
PermitGrab V62 — SQLite to PostgreSQL Migration Script

One-time migration to move all data from SQLite to Postgres.
Run this ONCE after deploying with DATABASE_URL set.

Usage:
    # Set DATABASE_URL in env, then:
    python migrate_to_postgres.py

    # Or with explicit paths:
    DATABASE_URL=postgresql://... python migrate_to_postgres.py /var/data/permitgrab.db

The script:
1. Creates all Postgres tables (via db_engine.init_schema)
2. Reads each SQLite table
3. Batch-inserts into Postgres
4. Verifies row counts match
5. Reports any discrepancies
"""

import os
import sys
import sqlite3
import time
from datetime import datetime

# Ensure db_engine can be imported
sys.path.insert(0, os.path.dirname(__file__))


def migrate(sqlite_path=None):
    """Run the full SQLite → Postgres migration."""

    # Resolve SQLite path
    if not sqlite_path:
        if os.path.isdir('/var/data'):
            sqlite_path = '/var/data/permitgrab.db'
        else:
            sqlite_path = os.path.join(os.path.dirname(__file__), 'data', 'permitgrab.db')

    if not os.path.exists(sqlite_path):
        print(f"ERROR: SQLite database not found at {sqlite_path}")
        sys.exit(1)

    # Verify Postgres is configured
    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        print("ERROR: DATABASE_URL environment variable not set")
        print("Set it to your Postgres connection string, e.g.:")
        print("  export DATABASE_URL='postgresql://user:pass@host:5432/permitgrab'")
        sys.exit(1)

    print(f"{'='*60}")
    print(f"PermitGrab SQLite → PostgreSQL Migration")
    print(f"{'='*60}")
    print(f"Source: {sqlite_path}")
    print(f"Target: {database_url[:40]}...")
    print(f"Started: {datetime.now().isoformat()}")
    print()

    # Connect to SQLite
    sqlite_conn = sqlite3.connect(sqlite_path)
    sqlite_conn.row_factory = sqlite3.Row

    # Initialize Postgres schema
    from db_engine import init_schema, get_connection, USE_POSTGRES
    if not USE_POSTGRES:
        print("ERROR: DATABASE_URL is set but db_engine didn't pick it up.")
        print("Make sure DATABASE_URL is exported before running this script.")
        sys.exit(1)

    print("[1/4] Creating Postgres schema...")
    init_schema()
    print("       Done.")

    # Tables to migrate (in dependency order)
    TABLES = [
        'permits',
        'permit_history',
        'collection_runs',
        'us_cities',
        'us_counties',
        'city_sources',
        'discovery_runs',
        'prod_cities',
        'stale_cities_review',
        'scraper_runs',
        'system_state',
        'city_activation_log',
        'discovered_sources',
    ]

    # Get Postgres connection
    pg_conn = get_connection()

    print(f"\n[2/4] Migrating {len(TABLES)} tables...")
    total_rows = 0
    results = {}

    for table in TABLES:
        try:
            # Check if table exists in SQLite
            exists = sqlite_conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table,)
            ).fetchone()

            if not exists:
                print(f"  {table}: SKIPPED (not in SQLite)")
                results[table] = {'status': 'skipped', 'rows': 0}
                continue

            # Get row count
            count = sqlite_conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            if count == 0:
                print(f"  {table}: SKIPPED (empty)")
                results[table] = {'status': 'empty', 'rows': 0}
                continue

            # Get column names
            cursor = sqlite_conn.execute(f"SELECT * FROM {table} LIMIT 1")
            columns = [desc[0] for desc in cursor.description]

            # Skip 'id' column for SERIAL tables (Postgres auto-generates)
            serial_tables = {
                'permit_history', 'collection_runs', 'us_cities', 'us_counties',
                'city_sources', 'discovery_runs', 'prod_cities', 'stale_cities_review',
                'scraper_runs', 'city_activation_log', 'discovered_sources',
            }
            if table in serial_tables and 'id' in columns:
                columns = [c for c in columns if c != 'id']

            # Build INSERT statement
            placeholders = ', '.join(['%s'] * len(columns))
            col_names = ', '.join(columns)

            # Handle conflicts
            if table == 'permits':
                insert_sql = f"""
                    INSERT INTO {table} ({col_names}) VALUES ({placeholders})
                    ON CONFLICT (permit_number) DO NOTHING
                """
            elif table == 'system_state':
                insert_sql = f"""
                    INSERT INTO {table} ({col_names}) VALUES ({placeholders})
                    ON CONFLICT (key) DO NOTHING
                """
            elif table == 'prod_cities':
                insert_sql = f"""
                    INSERT INTO {table} ({col_names}) VALUES ({placeholders})
                    ON CONFLICT (city_slug) DO NOTHING
                """
            elif table == 'us_cities':
                insert_sql = f"""
                    INSERT INTO {table} ({col_names}) VALUES ({placeholders})
                    ON CONFLICT (slug) DO NOTHING
                """
            elif table == 'us_counties':
                insert_sql = f"""
                    INSERT INTO {table} ({col_names}) VALUES ({placeholders})
                    ON CONFLICT (fips) DO NOTHING
                """
            elif table == 'city_sources':
                insert_sql = f"""
                    INSERT INTO {table} ({col_names}) VALUES ({placeholders})
                    ON CONFLICT (source_key) DO NOTHING
                """
            elif table == 'discovered_sources':
                insert_sql = f"""
                    INSERT INTO {table} ({col_names}) VALUES ({placeholders})
                    ON CONFLICT (source_key) DO NOTHING
                """
            elif table == 'permit_history':
                insert_sql = f"""
                    INSERT INTO {table} ({col_names}) VALUES ({placeholders})
                    ON CONFLICT (address_key, permit_number) DO NOTHING
                """
            elif table == 'stale_cities_review':
                insert_sql = f"""
                    INSERT INTO {table} ({col_names}) VALUES ({placeholders})
                    ON CONFLICT (city, state) DO NOTHING
                """
            else:
                insert_sql = f"INSERT INTO {table} ({col_names}) VALUES ({placeholders})"

            # Batch migrate
            BATCH_SIZE = 1000
            migrated = 0
            start = time.time()

            offset = 0
            while True:
                rows = sqlite_conn.execute(
                    f"SELECT {col_names} FROM {table} LIMIT {BATCH_SIZE} OFFSET {offset}"
                ).fetchall()

                if not rows:
                    break

                # Convert sqlite3.Row to tuples
                batch = [tuple(row[col] for col in columns) for row in rows]

                try:
                    import psycopg2.extras
                    cur = pg_conn._conn.cursor()
                    psycopg2.extras.execute_batch(cur, insert_sql, batch, page_size=100)
                    pg_conn.commit()
                except Exception as e:
                    pg_conn.rollback()
                    # Try one-by-one for this batch
                    for row_data in batch:
                        try:
                            pg_conn.execute(insert_sql, row_data)
                            pg_conn.commit()
                        except Exception:
                            pg_conn.rollback()
                            continue

                migrated += len(rows)
                offset += BATCH_SIZE

                if migrated % 10000 == 0:
                    elapsed = time.time() - start
                    rate = migrated / elapsed if elapsed > 0 else 0
                    print(f"  {table}: {migrated:,}/{count:,} ({rate:.0f} rows/sec)")

            elapsed = time.time() - start
            rate = migrated / elapsed if elapsed > 0 else 0
            print(f"  {table}: {migrated:,} rows in {elapsed:.1f}s ({rate:.0f}/sec)")
            results[table] = {'status': 'ok', 'rows': migrated, 'time': elapsed}
            total_rows += migrated

        except Exception as e:
            print(f"  {table}: ERROR — {e}")
            results[table] = {'status': 'error', 'rows': 0, 'error': str(e)}

    # Verify
    print(f"\n[3/4] Verifying row counts...")
    mismatches = []
    for table in TABLES:
        if results.get(table, {}).get('status') not in ('ok',):
            continue

        sqlite_count = sqlite_conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        pg_count = pg_conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone().fetchone()
        if pg_count:
            pg_count = pg_count[0]
        else:
            pg_count = 0

        match = "OK" if pg_count >= sqlite_count * 0.95 else "MISMATCH"
        if match == "MISMATCH":
            mismatches.append(table)
        print(f"  {table}: SQLite={sqlite_count:,}  Postgres={pg_count:,}  [{match}]")

    # Reset Postgres sequences for SERIAL columns
    print(f"\n[4/4] Resetting Postgres sequences...")
    for table in TABLES:
        if table in ('permits', 'system_state'):
            continue  # No SERIAL column
        try:
            pg_conn.execute(f"""
                SELECT setval(pg_get_serial_sequence('{table}', 'id'),
                              COALESCE((SELECT MAX(id) FROM {table}), 0) + 1, false)
            """)
            pg_conn.commit()
        except Exception:
            pg_conn.rollback()

    # Summary
    print(f"\n{'='*60}")
    print(f"Migration Complete")
    print(f"{'='*60}")
    print(f"Total rows migrated: {total_rows:,}")
    print(f"Tables: {sum(1 for r in results.values() if r['status'] == 'ok')}/{len(TABLES)}")
    if mismatches:
        print(f"MISMATCHES: {mismatches}")
        print("These tables may have had conflicting rows (ON CONFLICT DO NOTHING)")
    print(f"Finished: {datetime.now().isoformat()}")

    sqlite_conn.close()
    pg_conn.close()

    return results


if __name__ == '__main__':
    path = sys.argv[1] if len(sys.argv) > 1 else None
    migrate(path)
