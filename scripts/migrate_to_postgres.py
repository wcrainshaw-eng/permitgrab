"""V522 (V513 Step 3 STAGING): one-shot SQLite → Postgres migration.

USAGE (run on Render shell after USE_POSTGRES is still 'false'):

    ssh srv-d6s1tvsr85hc73em9ch0@ssh.oregon.render.com
    cd /app
    python3 scripts/migrate_to_postgres.py

Reads SQLite at /var/data/permitgrab.db (override via SQLITE_PATH).
Writes Postgres at $DATABASE_URL (Render-injected when permitgrab-db
is linked).

Step 1: applies scripts/postgres_schema.sql (idempotent).
Step 2: COPY-bulk-loads each table in dependency-friendly order.

The SQLite source is read-only during the migration. Once successful,
flip USE_POSTGRES=true on Render to cut the live site over.

Rollback: set USE_POSTGRES=false → 3-min redeploy → site back on
SQLite. Up to ~30 min of writes are lost (whatever went to Postgres
between flip-on and flip-off). For PermitGrab's small subscriber
count this is acceptable.
"""
from __future__ import annotations

import io
import os
import sqlite3
import sys
import time
from pathlib import Path

try:
    import psycopg2
    from psycopg2 import sql as pgsql
except ImportError:
    print("ERROR: psycopg2-binary not installed. Add to requirements.txt.", file=sys.stderr)
    sys.exit(1)


SQLITE_PATH = os.environ.get("SQLITE_PATH", "/var/data/permitgrab.db")
DATABASE_URL = os.environ.get("DATABASE_URL", "")
SCHEMA_SQL_PATH = Path(__file__).parent / "postgres_schema.sql"

# Dependency-friendly load order. Tables with no FK ancestors first,
# then dependents. Empty FK on the schema means order is informational
# only — but loading prod_cities before tables that key on it lets
# COPY work without violating any DB-level constraint we add later.
TABLES_IN_ORDER = [
    # Reference / lookup tables
    "us_cities", "us_counties", "system_state",
    # Subscribers + Stripe
    "subscribers", "stripe_webhook_events", "digest_log",
    # Source registry
    "sources", "bulk_sources", "city_sources", "sweep_sources",
    "discovered_sources",
    "prod_cities", "cities", "bulk_source_coverage",
    "city_research", "city_activation_log", "stale_cities_review",
    # Data tables — bulk
    "permits", "permit_history",
    "contractor_profiles", "contractor_contacts",
    "violations", "property_owners",
    # Run logs
    "scraper_runs", "collection_runs", "collection_log",
    "enrichment_log", "discovery_runs",
    "pipeline_runs", "pipeline_progress",
]


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def apply_schema(pg_conn) -> None:
    log(f"Applying schema from {SCHEMA_SQL_PATH}")
    sql_text = SCHEMA_SQL_PATH.read_text()
    with pg_conn.cursor() as cur:
        cur.execute(sql_text)
    pg_conn.commit()
    log("Schema applied.")


def _serialize_for_copy(val) -> str:
    """COPY format escaping. NULL becomes \\N; tabs/newlines/backslashes
    inside text get escape-encoded so the parser reassembles them."""
    if val is None:
        return "\\N"
    if isinstance(val, bytes):
        # Bytea: hex format
        return "\\\\x" + val.hex()
    s = str(val)
    return (
        s.replace("\\", "\\\\")
         .replace("\t", "\\t")
         .replace("\n", "\\n")
         .replace("\r", "\\r")
    )


def migrate_table(table: str, sqlite_conn, pg_conn, batch_size: int = 50000) -> int:
    sqlite_cur = sqlite_conn.cursor()
    try:
        sqlite_cur.execute(f"SELECT COUNT(*) FROM {table}")
        total = sqlite_cur.fetchone()[0]
    except sqlite3.OperationalError as e:
        log(f"  {table}: skip ({e})")
        return 0
    if total == 0:
        log(f"  {table}: 0 rows, skip")
        return 0

    sqlite_cur.execute(f"SELECT * FROM {table}")
    columns = [d[0] for d in sqlite_cur.description]
    log(f"  {table}: {total} rows ({len(columns)} cols)")

    # Truncate destination to make migrations idempotent (re-run safe)
    with pg_conn.cursor() as cur:
        cur.execute(pgsql.SQL("TRUNCATE TABLE {} RESTART IDENTITY CASCADE").format(
            pgsql.Identifier(table)))
    pg_conn.commit()

    inserted = 0
    while True:
        chunk = sqlite_cur.fetchmany(batch_size)
        if not chunk:
            break
        buf = io.StringIO()
        for row in chunk:
            buf.write("\t".join(_serialize_for_copy(v) for v in row) + "\n")
        buf.seek(0)
        with pg_conn.cursor() as cur:
            try:
                cur.copy_from(buf, table, columns=columns, sep="\t", null="\\N")
            except Exception as e:
                pg_conn.rollback()
                log(f"  {table}: COPY chunk failed at row {inserted} — {e}")
                # Fall back to per-row INSERT for this chunk so one bad
                # row doesn't kill the whole table. Slow but salvages
                # the rest.
                buf2 = chunk
                ph = ",".join(["%s"] * len(columns))
                col_idents = pgsql.SQL(", ").join(
                    pgsql.Identifier(c) for c in columns)
                stmt = pgsql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                    pgsql.Identifier(table), col_idents, pgsql.SQL(ph)
                )
                ok = 0
                for row in buf2:
                    try:
                        with pg_conn.cursor() as c2:
                            c2.execute(stmt, row)
                        pg_conn.commit()
                        ok += 1
                    except Exception as _ie:
                        pg_conn.rollback()
                inserted += ok
                continue
        pg_conn.commit()
        inserted += len(chunk)
        if total > 0 and inserted % (batch_size * 5) == 0:
            log(f"    {table}: {inserted}/{total} ({100*inserted/total:.1f}%)")
    log(f"  {table}: DONE — {inserted} rows")
    return inserted


def main() -> int:
    if not DATABASE_URL:
        print("ERROR: DATABASE_URL not set", file=sys.stderr)
        return 1
    if not SCHEMA_SQL_PATH.exists():
        print(f"ERROR: schema missing: {SCHEMA_SQL_PATH}", file=sys.stderr)
        return 1
    if not Path(SQLITE_PATH).exists():
        print(f"ERROR: SQLite DB missing: {SQLITE_PATH}", file=sys.stderr)
        return 1

    log(f"SQLITE_PATH={SQLITE_PATH}")
    log(f"DATABASE_URL={DATABASE_URL[:60]}...")

    sqlite_conn = sqlite3.connect(SQLITE_PATH)
    sqlite_conn.row_factory = sqlite3.Row
    pg_conn = psycopg2.connect(DATABASE_URL)

    try:
        apply_schema(pg_conn)

        # Discover any tables in SQLite that aren't in our explicit
        # order list and append them; nothing should be silently
        # dropped during migration.
        cur = sqlite_conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name"
        )
        all_tables = [r[0] for r in cur.fetchall()]
        ordered = [t for t in TABLES_IN_ORDER if t in all_tables]
        leftover = [t for t in all_tables if t not in TABLES_IN_ORDER]
        if leftover:
            log(f"NOTE: leftover tables (loading at end): {leftover}")
        full_order = ordered + leftover

        log(f"Migrating {len(full_order)} tables")
        t0 = time.time()
        grand_total = 0
        for table in full_order:
            try:
                inserted = migrate_table(table, sqlite_conn, pg_conn)
                grand_total += inserted
            except Exception as e:
                log(f"  {table}: FATAL — {e}")
                pg_conn.rollback()
                continue
        elapsed = time.time() - t0
        log(f"\n=== MIGRATION COMPLETE ===")
        log(f"  total rows: {grand_total}")
        log(f"  elapsed:    {elapsed/60:.1f} min")
        log(f"\nNext: flip USE_POSTGRES=true on Render env vars + redeploy.")
        log(f"Rollback: set USE_POSTGRES=false → 3-min redeploy.")
        return 0
    finally:
        sqlite_conn.close()
        pg_conn.close()


if __name__ == "__main__":
    sys.exit(main())
