"""
PermitGrab V62 — Database Engine Abstraction

Replaces the raw sqlite3 thread-local connection pattern with a proper
database engine that supports both PostgreSQL (production) and SQLite (local dev).

PostgreSQL eliminates the "database is locked" errors caused by 5 daemon threads
competing for SQLite write locks.

Usage:
    from db_engine import get_connection, execute, executemany, fetchone, fetchall, commit

    # Simple query
    rows = fetchall("SELECT * FROM permits WHERE city = %s", ("Austin",))

    # Insert with commit
    execute("INSERT INTO permits (permit_number, city) VALUES (%s, %s)", ("P123", "Austin"))
    commit()

Environment:
    DATABASE_URL  — Postgres connection string (Render provides this automatically)
                    If not set, falls back to SQLite at DB_PATH.
"""

import os
import time
import threading
import sqlite3
from contextlib import contextmanager

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

DATABASE_URL = os.environ.get('DATABASE_URL')

# SQLite fallback path (local dev)
if os.path.isdir('/var/data'):
    SQLITE_PATH = '/var/data/permitgrab.db'
else:
    SQLITE_PATH = os.path.join(os.path.dirname(__file__), 'data', 'permitgrab.db')

USE_POSTGRES = bool(DATABASE_URL)

# --------------------------------------------------------------------------
# PostgreSQL Engine (production)
# --------------------------------------------------------------------------

_pg_pool = None
_pg_lock = threading.Lock()

# V65: Rate-limit background thread connection usage to prevent API starvation
# Background threads (collection, sync, discovery) share this semaphore
_bg_conn_semaphore = threading.Semaphore(10)  # Max 10 concurrent background connections
_bg_thread_names = {'scheduled_collection', 'email_scheduler', 'city_sync', 'discovery'}


def _get_pg_pool():
    """Lazy-init a threaded connection pool."""
    global _pg_pool
    if _pg_pool is None:
        with _pg_lock:
            if _pg_pool is None:
                import psycopg2
                import psycopg2.pool
                import psycopg2.extras

                # Fix Render's postgres:// → postgresql:// URL scheme
                db_url = DATABASE_URL
                if db_url.startswith('postgres://'):
                    db_url = db_url.replace('postgres://', 'postgresql://', 1)

                _pg_pool = psycopg2.pool.ThreadedConnectionPool(
                    minconn=5,
                    maxconn=35,
                    dsn=db_url,
                )
                print(f"[DB_ENGINE] PostgreSQL pool initialized (5-35 connections)")
    return _pg_pool


class PgConnection:
    """Wrapper around psycopg2 connection to provide sqlite3.Row-like interface."""

    def __init__(self, conn, is_background=False):
        self._conn = conn
        self._conn.autocommit = False
        self._is_background = is_background  # V65: Track for semaphore release

    def execute(self, sql, params=None):
        import psycopg2.extras
        translated = _translate_sql(sql)
        cur = self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(translated, params)
        return PgCursor(cur)

    def executemany(self, sql, params_list):
        import psycopg2.extras
        translated = _translate_sql(sql)
        cur = self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.executemany(translated, params_list)
        return PgCursor(cur)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        pool = _get_pg_pool()
        pool.putconn(self._conn)
        # V65: Release background semaphore if this was a background connection
        if self._is_background:
            _bg_conn_semaphore.release()

    @property
    def rowcount(self):
        return self._conn.cursor().rowcount


class PgCursor:
    """Wrapper for psycopg2 cursor to match SQLite cursor patterns."""

    def __init__(self, cursor):
        self._cursor = cursor

    def fetchone(self):
        row = self._cursor.fetchone()
        if row is None:
            return None
        return DictRow(row)

    def fetchall(self):
        return [DictRow(row) for row in self._cursor.fetchall()]

    @property
    def rowcount(self):
        return self._cursor.rowcount

    @property
    def lastrowid(self):
        # Postgres doesn't have lastrowid — use RETURNING if needed
        return None

    def __iter__(self):
        return (DictRow(row) for row in self._cursor)


class DictRow(dict):
    """Dict-like row that also supports index access like sqlite3.Row."""

    def __init__(self, data):
        super().__init__(data)
        self._keys = list(data.keys())

    def __getitem__(self, key):
        if isinstance(key, int):
            return super().__getitem__(self._keys[key])
        return super().__getitem__(key)


# --------------------------------------------------------------------------
# SQLite Engine (local dev fallback)
# --------------------------------------------------------------------------

_sqlite_local = threading.local()


def _get_sqlite_conn():
    """Thread-local SQLite connection with WAL mode."""
    pid = os.getpid()
    if not hasattr(_sqlite_local, 'pid') or _sqlite_local.pid != pid:
        _sqlite_local.pid = pid
        _sqlite_local.conn = None

    if _sqlite_local.conn is not None:
        try:
            _sqlite_local.conn.execute("SELECT 1")
        except (sqlite3.ProgrammingError, sqlite3.OperationalError):
            _sqlite_local.conn = None

    if _sqlite_local.conn is None:
        db_dir = os.path.dirname(SQLITE_PATH)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)

        _sqlite_local.conn = sqlite3.connect(SQLITE_PATH, timeout=30)
        _sqlite_local.conn.row_factory = sqlite3.Row
        _sqlite_local.conn.execute("PRAGMA journal_mode=WAL")
        _sqlite_local.conn.execute("PRAGMA synchronous=NORMAL")
        _sqlite_local.conn.execute("PRAGMA cache_size=-8000")
        _sqlite_local.conn.execute("PRAGMA busy_timeout=60000")

    return _sqlite_local.conn


# --------------------------------------------------------------------------
# SQL Translation Layer
# --------------------------------------------------------------------------

def _translate_sql(sql):
    """Convert SQLite-flavored SQL to Postgres-compatible SQL.

    This handles the most common differences so existing queries in db.py
    can work with minimal changes.
    """
    if not USE_POSTGRES:
        return sql

    # Parameter placeholders: ? → %s
    # (careful not to replace ? inside strings)
    translated = sql.replace('?', '%s')

    # datetime('now') → NOW()
    translated = translated.replace("datetime('now')", "NOW()")

    # julianday('now') - julianday(x) → EXTRACT(EPOCH FROM NOW() - x::timestamp) / 86400
    # This is complex — handle the most common pattern
    import re
    translated = re.sub(
        r"julianday\('now'\)\s*-\s*julianday\((\w+(?:\.\w+)?)\)",
        r"EXTRACT(EPOCH FROM NOW() - \1::timestamp) / 86400",
        translated
    )
    translated = re.sub(
        r"CAST\(EXTRACT\(EPOCH FROM NOW\(\) - (\w+(?:\.\w+)?)::timestamp\) / 86400 AS INTEGER\)",
        r"EXTRACT(EPOCH FROM NOW() - \1::timestamp)::int / 86400",
        translated
    )

    # julianday(x) → EXTRACT(EPOCH FROM x::timestamp) / 86400
    translated = re.sub(
        r"julianday\((\w+(?:\.\w+)?)\)",
        r"EXTRACT(EPOCH FROM \1::timestamp) / 86400",
        translated
    )

    # INSERT OR REPLACE → INSERT ... ON CONFLICT DO UPDATE
    # This one needs context-specific handling — leave for db.py to manage

    # INSERT OR IGNORE → INSERT ... ON CONFLICT DO NOTHING
    translated = translated.replace("INSERT OR IGNORE", "INSERT")
    # We'll add ON CONFLICT DO NOTHING in db.py where needed

    # AUTOINCREMENT → (just remove it, Postgres SERIAL handles this)
    translated = translated.replace("AUTOINCREMENT", "")

    # INTEGER PRIMARY KEY (implicit rowid in SQLite) → SERIAL PRIMARY KEY
    translated = re.sub(
        r"(\w+)\s+INTEGER\s+PRIMARY\s+KEY\b(?!\s+NOT)",
        r"\1 SERIAL PRIMARY KEY",
        translated
    )

    # NOT GLOB → !~ (Postgres negated regex match) — must come before GLOB
    translated = translated.replace(" NOT GLOB ", " !~ ")
    # GLOB → ~ (Postgres regex match)
    translated = translated.replace(" GLOB ", " ~ ")

    # SUBSTR → SUBSTRING (both actually work in Postgres, but be explicit)
    # Actually SUBSTR works in Postgres too, so skip this

    return translated


# --------------------------------------------------------------------------
# Public API — drop-in replacement for get_connection()
# --------------------------------------------------------------------------

def get_connection(max_retries=10, retry_delay=1.0, background=None):
    """Get a database connection (Postgres pool or SQLite thread-local).

    Returns a connection object that supports:
        .execute(sql, params)
        .commit()
        .fetchone() / .fetchall() on cursor results

    For Postgres, SQL is auto-translated from SQLite syntax.

    V65: Added retry logic for pool exhaustion and rate-limiting for background threads.
    V67: Increased retries to 10 with exponential backoff up to 30s max.

    Args:
        max_retries: Number of times to retry on pool exhaustion (default 10, was 3)
        retry_delay: Base seconds to wait between retries (default 1.0, was 0.5)
        background: If True, use background semaphore. If None, auto-detect from thread name.
    """
    if USE_POSTGRES:
        import psycopg2.pool

        # V65: Auto-detect if this is a background thread
        if background is None:
            thread_name = threading.current_thread().name.lower()
            background = any(bg in thread_name for bg in _bg_thread_names)

        # V65: Rate-limit background threads to prevent API starvation
        if background:
            _bg_conn_semaphore.acquire()

        try:
            pool = _get_pg_pool()

            # V67: Retry logic with exponential backoff (up to 30s max wait per retry)
            last_error = None
            for attempt in range(max_retries):
                try:
                    conn = pool.getconn()
                    return PgConnection(conn, is_background=background)
                except psycopg2.pool.PoolError as e:
                    last_error = e
                    if attempt < max_retries - 1:
                        # V67: Exponential backoff capped at 30 seconds
                        wait_time = min(retry_delay * (2 ** attempt), 30)
                        print(f"[DB_ENGINE] Pool exhausted, retry {attempt + 1}/{max_retries} in {wait_time:.1f}s...")
                        time.sleep(wait_time)
                    else:
                        print(f"[DB_ENGINE] Pool exhausted after {max_retries} retries (~{sum(min(retry_delay * (2**i), 30) for i in range(max_retries-1)):.0f}s total wait)")
                        raise

            # Should not reach here, but just in case
            raise last_error
        except:
            # Release semaphore if we failed to get a connection
            if background:
                _bg_conn_semaphore.release()
            raise
    else:
        return _get_sqlite_conn()


@contextmanager
def connection(background=None):
    """V66: Context manager that guarantees connection return to pool.

    Usage:
        with connection() as conn:
            conn.execute("SELECT ...")
            conn.commit()
        # connection automatically returned to pool

    Args:
        background: If True, use background semaphore. If None, auto-detect.
    """
    conn = get_connection(background=background)
    try:
        yield conn
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        try:
            conn.close()
        except Exception:
            pass


def execute(sql, params=None):
    """Execute a query with auto-translation."""
    conn = get_connection()
    translated = _translate_sql(sql)
    if params:
        return conn.execute(translated, params)
    return conn.execute(translated)


def fetchone(sql, params=None):
    """Execute and return one row."""
    cursor = execute(sql, params)
    return cursor.fetchone()


def fetchall(sql, params=None):
    """Execute and return all rows."""
    cursor = execute(sql, params)
    return cursor.fetchall()


def commit():
    """Commit the current connection's transaction."""
    conn = get_connection()
    conn.commit()


@contextmanager
def transaction():
    """Context manager for explicit transactions.

    Usage:
        with transaction() as conn:
            conn.execute("INSERT ...")
            conn.execute("UPDATE ...")
        # auto-commits on exit, rolls back on exception
    """
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        if USE_POSTGRES:
            conn.close()


# --------------------------------------------------------------------------
# Schema initialization — Postgres-compatible DDL
# --------------------------------------------------------------------------

def init_schema():
    """Create all tables using Postgres-compatible DDL.

    Called once on startup. Safe to call multiple times (CREATE IF NOT EXISTS).
    """
    conn = get_connection()

    if USE_POSTGRES:
        _init_postgres_schema(conn)
    else:
        # SQLite uses the original executescript from db.py
        pass

    if USE_POSTGRES:
        conn.commit()
        conn.close()


def _init_postgres_schema(conn):
    """Create Postgres tables with proper types and constraints."""

    # Permits — the main table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS permits (
            permit_number TEXT PRIMARY KEY,
            city TEXT NOT NULL,
            state TEXT,
            address TEXT,
            zip TEXT,
            permit_type TEXT,
            permit_sub_type TEXT,
            work_type TEXT,
            trade_category TEXT,
            description TEXT,
            display_description TEXT,
            estimated_cost DOUBLE PRECISION DEFAULT 0,
            value_tier TEXT,
            status TEXT,
            filing_date TEXT,
            issued_date TEXT,
            date TEXT,
            contact_name TEXT,
            contact_phone TEXT,
            contact_email TEXT,
            owner_name TEXT,
            contractor_name TEXT,
            square_feet DOUBLE PRECISION,
            lifecycle_label TEXT,
            source_city_key TEXT,
            collected_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        )
    """)

    # Indexes for permits
    for idx_sql in [
        "CREATE INDEX IF NOT EXISTS idx_permits_city ON permits(city)",
        "CREATE INDEX IF NOT EXISTS idx_permits_trade ON permits(trade_category)",
        "CREATE INDEX IF NOT EXISTS idx_permits_filing_date ON permits(filing_date)",
        "CREATE INDEX IF NOT EXISTS idx_permits_status ON permits(status)",
        "CREATE INDEX IF NOT EXISTS idx_permits_cost ON permits(estimated_cost)",
        "CREATE INDEX IF NOT EXISTS idx_permits_date ON permits(date)",
        "CREATE INDEX IF NOT EXISTS idx_permits_source_city_key ON permits(source_city_key)",
    ]:
        conn.execute(idx_sql)

    # Permit history
    conn.execute("""
        CREATE TABLE IF NOT EXISTS permit_history (
            id SERIAL PRIMARY KEY,
            address_key TEXT NOT NULL,
            address TEXT,
            city TEXT,
            state TEXT,
            permit_number TEXT,
            permit_type TEXT,
            work_type TEXT,
            trade_category TEXT,
            filing_date TEXT,
            estimated_cost DOUBLE PRECISION,
            description TEXT,
            contractor TEXT,
            UNIQUE(address_key, permit_number)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_history_address ON permit_history(address_key)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_history_date ON permit_history(filing_date)")

    # Collection runs
    conn.execute("""
        CREATE TABLE IF NOT EXISTS collection_runs (
            id SERIAL PRIMARY KEY,
            run_type TEXT NOT NULL,
            started_at TIMESTAMP DEFAULT NOW(),
            completed_at TIMESTAMP,
            cities_processed INTEGER DEFAULT 0,
            permits_collected INTEGER DEFAULT 0,
            permits_new INTEGER DEFAULT 0,
            permits_updated INTEGER DEFAULT 0,
            status TEXT DEFAULT 'running',
            error_message TEXT,
            details TEXT
        )
    """)

    # US Cities master list
    conn.execute("""
        CREATE TABLE IF NOT EXISTS us_cities (
            id SERIAL PRIMARY KEY,
            city_name TEXT NOT NULL,
            state TEXT NOT NULL,
            county TEXT,
            county_fips TEXT,
            population INTEGER DEFAULT 0,
            latitude DOUBLE PRECISION,
            longitude DOUBLE PRECISION,
            slug TEXT UNIQUE,
            status TEXT DEFAULT 'not_started',
            status_reason TEXT,
            covered_by_source TEXT,
            priority INTEGER DEFAULT 99999,
            last_searched_at TIMESTAMP,
            search_attempts INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    for idx in ['status', 'priority', 'state', 'slug', 'county_fips']:
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_us_cities_{idx} ON us_cities({idx})")

    # US Counties
    conn.execute("""
        CREATE TABLE IF NOT EXISTS us_counties (
            id SERIAL PRIMARY KEY,
            county_name TEXT NOT NULL,
            state TEXT NOT NULL,
            fips TEXT UNIQUE,
            population INTEGER DEFAULT 0,
            cities_in_county INTEGER DEFAULT 0,
            portal_domain TEXT,
            status TEXT DEFAULT 'not_started',
            status_reason TEXT,
            source_key TEXT,
            last_searched_at TIMESTAMP,
            priority INTEGER DEFAULT 99999,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    for idx in ['status', 'priority', 'fips']:
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_us_counties_{idx} ON us_counties({idx})")

    # City sources
    conn.execute("""
        CREATE TABLE IF NOT EXISTS city_sources (
            id SERIAL PRIMARY KEY,
            source_key TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            state TEXT,
            platform TEXT NOT NULL,
            mode TEXT DEFAULT 'city',
            endpoint TEXT NOT NULL,
            dataset_id TEXT,
            field_map TEXT,
            date_field TEXT,
            city_field TEXT,
            limit_per_page INTEGER DEFAULT 2000,
            status TEXT DEFAULT 'active',
            discovery_score INTEGER DEFAULT 0,
            consecutive_failures INTEGER DEFAULT 0,
            last_failure_reason TEXT,
            last_collected_at TIMESTAMP,
            total_permits_collected INTEGER DEFAULT 0,
            covers_cities TEXT,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        )
    """)
    for idx in ['status', 'platform', 'mode']:
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_city_sources_{idx} ON city_sources({idx})")

    # Discovery runs
    conn.execute("""
        CREATE TABLE IF NOT EXISTS discovery_runs (
            id SERIAL PRIMARY KEY,
            run_type TEXT NOT NULL,
            started_at TIMESTAMP DEFAULT NOW(),
            completed_at TIMESTAMP,
            targets_searched INTEGER DEFAULT 0,
            sources_found INTEGER DEFAULT 0,
            permits_loaded INTEGER DEFAULT 0,
            cities_activated INTEGER DEFAULT 0,
            errors TEXT
        )
    """)

    # Prod cities
    conn.execute("""
        CREATE TABLE IF NOT EXISTS prod_cities (
            id SERIAL PRIMARY KEY,
            city TEXT NOT NULL,
            state TEXT NOT NULL,
            city_slug TEXT UNIQUE NOT NULL,
            source_type TEXT,
            source_id TEXT,
            source_scope TEXT,
            source_endpoint TEXT,
            verified_date TIMESTAMP,
            last_collection TIMESTAMP,
            last_permit_date TEXT,
            total_permits INTEGER DEFAULT 0,
            permits_last_30d INTEGER DEFAULT 0,
            avg_daily_permits DOUBLE PRECISION DEFAULT 0,
            status TEXT DEFAULT 'active',
            consecutive_failures INTEGER DEFAULT 0,
            last_error TEXT,
            added_by TEXT,
            added_at TIMESTAMP DEFAULT NOW(),
            notes TEXT,
            data_freshness TEXT DEFAULT 'fresh',
            newest_permit_date TEXT,
            stale_since TIMESTAMP,
            pause_reason TEXT,
            UNIQUE(city, state)
        )
    """)
    for idx in ['status', 'state', 'slug', 'last_collection', 'freshness']:
        col = {'slug': 'city_slug', 'freshness': 'data_freshness'}.get(idx, idx)
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_prod_cities_{idx} ON prod_cities({col})")

    # Stale cities review queue
    conn.execute("""
        CREATE TABLE IF NOT EXISTS stale_cities_review (
            id SERIAL PRIMARY KEY,
            city TEXT NOT NULL,
            state TEXT NOT NULL,
            original_source TEXT,
            last_permit_date TEXT,
            stale_since TIMESTAMP,
            auto_search_attempted INTEGER DEFAULT 0,
            auto_search_result TEXT,
            manual_notes TEXT,
            alternate_source_url TEXT,
            status TEXT DEFAULT 'needs_review',
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(city, state)
        )
    """)

    # Scraper runs
    conn.execute("""
        CREATE TABLE IF NOT EXISTS scraper_runs (
            id SERIAL PRIMARY KEY,
            source_name TEXT,
            city TEXT,
            state TEXT,
            city_slug TEXT,
            run_started_at TIMESTAMP DEFAULT NOW(),
            run_completed_at TIMESTAMP,
            duration_ms INTEGER,
            permits_found INTEGER DEFAULT 0,
            permits_inserted INTEGER DEFAULT 0,
            status TEXT,
            error_message TEXT,
            error_type TEXT,
            http_status INTEGER,
            response_size_bytes INTEGER,
            collection_type TEXT DEFAULT 'scheduled',
            triggered_by TEXT
        )
    """)
    for idx in ['city_slug', 'started', 'status']:
        col = {'started': 'run_started_at'}.get(idx, idx)
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_scraper_runs_{idx} ON scraper_runs({col})")

    # System state
    conn.execute("""
        CREATE TABLE IF NOT EXISTS system_state (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TIMESTAMP DEFAULT NOW()
        )
    """)

    # City activation log
    conn.execute("""
        CREATE TABLE IF NOT EXISTS city_activation_log (
            id SERIAL PRIMARY KEY,
            city_slug TEXT NOT NULL,
            city_name TEXT NOT NULL,
            state TEXT NOT NULL,
            activated_at TIMESTAMP DEFAULT NOW(),
            source TEXT,
            initial_permits INTEGER DEFAULT 0,
            seo_status TEXT DEFAULT 'needs_content',
            notes TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_city_activation_log_activated ON city_activation_log(activated_at)")

    # Discovered sources
    conn.execute("""
        CREATE TABLE IF NOT EXISTS discovered_sources (
            id SERIAL PRIMARY KEY,
            source_key TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            state TEXT NOT NULL,
            platform TEXT NOT NULL,
            mode TEXT DEFAULT 'bulk',
            endpoint TEXT NOT NULL,
            dataset_id TEXT,
            city_field TEXT,
            date_field TEXT,
            field_map TEXT,
            scope TEXT,
            discovered_at TIMESTAMP DEFAULT NOW(),
            last_tested TIMESTAMP,
            status TEXT DEFAULT 'active',
            notes TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_discovered_sources_status ON discovered_sources(status)")

    # V62: City pipeline validation table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS city_validations (
            id SERIAL PRIMARY KEY,
            city_slug TEXT NOT NULL,
            city_name TEXT NOT NULL,
            state TEXT NOT NULL,
            population INTEGER DEFAULT 0,
            platform TEXT,
            endpoint TEXT,
            dataset_id TEXT,
            phase TEXT DEFAULT 'discovery',
            phase_status TEXT DEFAULT 'pending',
            endpoint_tested BOOLEAN DEFAULT FALSE,
            endpoint_test_date TIMESTAMP,
            endpoint_test_result TEXT,
            schema_valid BOOLEAN DEFAULT FALSE,
            date_parsing_valid BOOLEAN DEFAULT FALSE,
            pagination_valid BOOLEAN DEFAULT FALSE,
            backfill_started TIMESTAMP,
            backfill_completed TIMESTAMP,
            backfill_permit_count INTEGER DEFAULT 0,
            backfill_min_date TEXT,
            backfill_max_date TEXT,
            has_future_dates BOOLEAN DEFAULT FALSE,
            activated BOOLEAN DEFAULT FALSE,
            activated_at TIMESTAMP,
            rejection_reason TEXT,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(city_slug)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_city_validations_phase ON city_validations(phase)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_city_validations_status ON city_validations(phase_status)")

    print("[DB_ENGINE] PostgreSQL schema initialized")


# --------------------------------------------------------------------------
# Utility: detect which engine is active
# --------------------------------------------------------------------------

def engine_info():
    """Return info about the active database engine."""
    return {
        'engine': 'postgresql' if USE_POSTGRES else 'sqlite',
        'url': DATABASE_URL[:30] + '...' if DATABASE_URL else SQLITE_PATH,
        'pool_size': '2-10' if USE_POSTGRES else 'thread-local',
    }
