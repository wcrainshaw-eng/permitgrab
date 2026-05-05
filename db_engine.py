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
_pg_pool_enabled = False  # V70: Pool DISABLED by default — must be manually enabled
_pg_lock = threading.Lock()

# V65: Rate-limit background thread connection usage to prevent API starvation
# Background threads (collection, sync, discovery) share this semaphore
_bg_conn_semaphore = threading.Semaphore(10)  # Max 10 concurrent background connections
_bg_thread_names = {'scheduled_collection', 'email_scheduler', 'city_sync', 'discovery'}


def enable_pg_pool():
    """V70: Manually enable Postgres pool. Call from admin endpoint only.

    V514 sizing: minconn=2, maxconn=25 per gunicorn worker. With
    WEB_CONCURRENCY=2 → 50 web slots + ~5 daemon = 55 of Postgres
    Pro-4gb's 100-conn ceiling. Adds a 60s pool-state logger so we
    can spot leaks in production before they exhaust the pool.
    """
    global _pg_pool, _pg_pool_enabled
    if _pg_pool is not None:
        print("[DB_ENGINE] V70: Pool already exists")
        return True
    try:
        import psycopg2
        import psycopg2.pool

        # Fix Render's postgres:// → postgresql:// URL scheme
        db_url = DATABASE_URL
        if db_url.startswith('postgres://'):
            db_url = db_url.replace('postgres://', 'postgresql://', 1)

        _pg_pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=2,
            maxconn=25,
            dsn=db_url,
        )
        _pg_pool_enabled = True
        print(f"[DB_ENGINE] V514: Postgres pool created (min=2, max=25)")

        # V514: Start the pool-state logger thread (one per process).
        try:
            t = threading.Thread(
                target=_pool_monitor,
                name='pg_pool_monitor',
                daemon=True,
            )
            t.start()
            print("[DB_ENGINE] V514: pool monitor thread started")
        except Exception as me:
            print(f"[DB_ENGINE] V514: pool monitor failed to start: {me}")
        return True
    except Exception as e:
        print(f"[DB_ENGINE] V70: Failed to create pool: {e}")
        return False


def _pool_monitor():
    """V514: log pool state every 60s so leaks surface before they
    exhaust the pool. ThreadedConnectionPool exposes ._used (dict of
    in-use conns) and ._pool (list of idle conns). Safe-readonly."""
    while True:
        try:
            time.sleep(60)
            p = _pg_pool
            if p is None:
                continue
            in_use = len(getattr(p, '_used', {}) or {})
            idle = len(getattr(p, '_pool', []) or [])
            maxc = getattr(p, 'maxconn', '?')
            sem_avail = _bg_conn_semaphore._value if hasattr(_bg_conn_semaphore, '_value') else '?'
            print(f"[DB_ENGINE] V514 pool: in_use={in_use} idle={idle} max={maxc} bg_sem_avail={sem_avail}")
        except Exception as e:
            print(f"[DB_ENGINE] V514 pool monitor error: {e}")


def _get_pg_pool():
    """V70: Returns pool ONLY if explicitly enabled. Does NOT auto-create."""
    global _pg_pool
    if _pg_pool is None:
        raise RuntimeError("[DB_ENGINE] V70: Postgres pool not initialized. POST /api/admin/enable-postgres first.")
    return _pg_pool


def is_pg_pool_enabled():
    """V70: Check if Postgres pool is available."""
    return _pg_pool is not None and _pg_pool_enabled


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
        if self._conn is None:
            return
        pool = _get_pg_pool()
        pool.putconn(self._conn)
        self._conn = None
        # V65: Release background semaphore if this was a background connection
        if self._is_background:
            _bg_conn_semaphore.release()
            self._is_background = False

    def __del__(self):
        """V514 GC safety net: if a caller forgets to close(), at least
        return the connection to the pool when this object is garbage-
        collected. Not a substitute for try/finally pairing — slow
        leaks still degrade the pool until GC catches up — but it
        prevents a silent forever-leak from killing the pool."""
        try:
            if self._conn is not None:
                self.close()
        except Exception:
            pass

    @property
    def rowcount(self):
        return self._conn.cursor().rowcount

    def cursor(self, cursor_factory=None):
        """V522d: psycopg2-style API. Many callers (V518 Stripe admin
        endpoints, the daemon, etc.) use `conn.cursor()` followed by
        `cur.execute()` instead of the SQLite-style `conn.execute()`.
        Without this method the AttributeError torpedoes every request
        the moment USE_POSTGRES=true. Returns a PgCursor wrapping a
        RealDictCursor so fetchone/fetchall return dict-like rows
        compatible with both `r['col']` and `r[0]` access."""
        import psycopg2.extras
        cf = cursor_factory or psycopg2.extras.RealDictCursor
        cur = self._conn.cursor(cursor_factory=cf)
        return PgCursor(cur)


class PgCursor:
    """Wrapper for psycopg2 cursor to match SQLite cursor patterns."""

    def __init__(self, cursor):
        self._cursor = cursor

    def execute(self, sql, params=None):
        """V522d: support `cur = conn.cursor(); cur.execute(sql, params)`
        pattern. SQLite uses ? placeholders, psycopg2 uses %s — translate
        before forwarding so existing SQLite-style call sites Just Work."""
        translated = _translate_sql(sql)
        self._cursor.execute(translated, params)
        return self

    def executemany(self, sql, params_list):
        translated = _translate_sql(sql)
        self._cursor.executemany(translated, params_list)
        return self

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

    def close(self):
        try:
            self._cursor.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


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
        # V485 (mirror db.py P0 root-cause #2 fix): aggressive WAL
        # auto-checkpoint to limit growth between active checkpoints
        # by the worker heartbeat. See db.py for the full diagnosis.
        _sqlite_local.conn.execute("PRAGMA wal_autocheckpoint=200")
        _sqlite_local.conn.execute("PRAGMA journal_size_limit=67108864")

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

def get_connection(timeout_s=5.0, background=None, **legacy_kwargs):
    """Get a database connection (Postgres pool or SQLite thread-local).

    V514: replaces the V67 10-retry-30s exponential-backoff loop with a
    5-second circuit breaker. A hung getconn() holds a gunicorn worker
    slot — the longer we wait the worse the cascade gets. Better to
    fail fast with a 503 and let the caller render a friendly error
    than to chain-block every request behind a pool that's never
    going to drain in time. Cuts cutover-failure blast radius from
    full-site outage to single-request 503.

    V70: Raises RuntimeError if Postgres pool not enabled.
    V65: Background threads share a 10-slot semaphore so daemon writes
    can't starve API workers.

    Args:
        timeout_s: Max wait in seconds for a free pool slot before
            raising PoolError. Default 5s. Pass timeout_s=0 to skip
            wait entirely (fail immediately).
        background: If True, use background semaphore. If None,
            auto-detect from thread name.
        **legacy_kwargs: ignored — accepted for back-compat with the
            old `max_retries=` / `retry_delay=` signature so we don't
            need to update every callsite in this PR.
    """
    if USE_POSTGRES:
        import psycopg2.pool

        # V70: Check if pool is enabled before proceeding
        if not is_pg_pool_enabled():
            raise RuntimeError("[DB_ENGINE] V70: Postgres pool not initialized. POST /api/admin/enable-postgres first.")

        # V65: Auto-detect if this is a background thread
        if background is None:
            thread_name = threading.current_thread().name.lower()
            background = any(bg in thread_name for bg in _bg_thread_names)

        # V65: Rate-limit background threads to prevent API starvation
        if background:
            _bg_conn_semaphore.acquire()

        try:
            pool = _get_pg_pool()

            # V514: 5s circuit breaker. Poll at 100ms while pool is
            # exhausted; after timeout_s seconds, raise so the caller
            # can render a 503 instead of holding the worker hostage.
            start = time.monotonic()
            while True:
                try:
                    conn = pool.getconn()
                    return PgConnection(conn, is_background=background)
                except psycopg2.pool.PoolError:
                    elapsed = time.monotonic() - start
                    if elapsed >= timeout_s:
                        print(f"[DB_ENGINE] V514 pool exhausted after {elapsed:.1f}s — failing fast")
                        raise
                    time.sleep(0.1)
        except:
            # Release semaphore if we failed to get a connection
            if background:
                _bg_conn_semaphore.release()
            raise
    else:
        return _get_sqlite_conn()


def put_connection(conn):
    """V514: explicit putconn() helper for callsites that don't use the
    `connection()` context manager. Pair every get_connection() with a
    put_connection() in a finally block so a raised exception inside
    the cursor work doesn't leak a pool slot.

    Safe to call with a sqlite Connection — the SQLite path uses a
    thread-local connection and does not need to be returned to a pool.
    """
    if conn is None:
        return
    if isinstance(conn, PgConnection):
        try:
            conn.close()
        except Exception as e:
            print(f"[DB_ENGINE] V514 put_connection error: {e}")
    # SQLite path: thread-local connection, nothing to return.


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

    # V156: Violations table for code enforcement data
    conn.execute("""
        CREATE TABLE IF NOT EXISTS violations (
            id SERIAL PRIMARY KEY,
            city TEXT NOT NULL,
            state TEXT NOT NULL,
            violation_id TEXT,
            address TEXT,
            violation_date TEXT,
            violation_type TEXT,
            description TEXT,
            status TEXT,
            source_dataset TEXT,
            source_url TEXT,
            collected_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(city, state, violation_id)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_violations_city_state ON violations(city, state)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_violations_date ON violations(violation_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_violations_status ON violations(status)")

    # V485 B8: page_views — close the SEO measurement loop. The
    # analytics_track_page_view after_request hook in server.py:4692
    # has been INSERTing into this table for years, but the table never
    # existed in production — every INSERT silently swallowed by the
    # surrounding try/except. With this CREATE, every page load
    # (excluding /static, /api/, /health, /favicon, /robots, /sitemap)
    # writes a row, so we can finally measure Googlebot crawl rate,
    # bot vs human ratio, and which pages get traffic.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS page_views (
            id            SERIAL PRIMARY KEY,
            path          TEXT NOT NULL,
            method        TEXT,
            status_code   INTEGER,
            user_agent    TEXT,
            ip_address    TEXT,
            referrer      TEXT,
            session_id    TEXT,
            user_id       INTEGER,
            created_at    TIMESTAMP DEFAULT NOW()
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_page_views_path_created ON page_views(path, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_page_views_created_at ON page_views(created_at)")

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
