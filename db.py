"""
PermitGrab V12.50 — SQLite database layer
Replaces permits.json and permit_history.json with a single SQLite database.
"""

import sqlite3
import os
import json
import threading
from datetime import datetime, timedelta

# Use Render persistent disk if available
if os.path.isdir('/var/data'):
    DB_PATH = '/var/data/permitgrab.db'
else:
    DB_PATH = os.path.join(os.path.dirname(__file__), 'data', 'permitgrab.db')

# Thread-local connections (SQLite connections can't be shared across threads)
# V12.51: Also track PID to handle Gunicorn --preload fork correctly
_local = threading.local()


def get_connection():
    """Get a thread-local SQLite connection with WAL mode enabled.

    V12.51: Process-aware — resets connection after Gunicorn fork to avoid
    sharing connections across worker processes.
    V12.60: Validates connection is still open before returning. If a stale
    conn.close() poisoned the thread-local, we detect it and reconnect.
    """
    pid = os.getpid()

    # If we forked (Gunicorn worker), reset thread-local
    if not hasattr(_local, 'pid') or _local.pid != pid:
        _local.pid = pid
        _local.conn = None

    # V12.60: Check if existing connection was closed (e.g. by stale conn.close())
    if _local.conn is not None:
        try:
            _local.conn.execute("SELECT 1")
        except (sqlite3.ProgrammingError, sqlite3.OperationalError):
            _local.conn = None

    if _local.conn is None:
        # Ensure data directory exists
        db_dir = os.path.dirname(DB_PATH)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)

        _local.conn = sqlite3.connect(DB_PATH, timeout=30)
        _local.conn.row_factory = sqlite3.Row  # dict-like access
        _local.conn.execute("PRAGMA journal_mode=WAL")  # concurrent reads during writes
        _local.conn.execute("PRAGMA synchronous=NORMAL")  # good durability, better perf
        _local.conn.execute("PRAGMA cache_size=-8000")  # 8MB cache (conservative for 2GB box)
        _local.conn.execute("PRAGMA busy_timeout=10000")  # wait up to 10s for locks
    return _local.conn


def init_db():
    """Create tables and indexes if they don't exist. Safe to call multiple times."""
    conn = get_connection()
    conn.executescript("""
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
            estimated_cost REAL DEFAULT 0,
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
            square_feet REAL,
            lifecycle_label TEXT,
            source_city_key TEXT,
            collected_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_permits_city ON permits(city);
        CREATE INDEX IF NOT EXISTS idx_permits_trade ON permits(trade_category);
        CREATE INDEX IF NOT EXISTS idx_permits_filing_date ON permits(filing_date);
        CREATE INDEX IF NOT EXISTS idx_permits_status ON permits(status);
        CREATE INDEX IF NOT EXISTS idx_permits_cost ON permits(estimated_cost);
        CREATE INDEX IF NOT EXISTS idx_permits_date ON permits(date);

        CREATE TABLE IF NOT EXISTS permit_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            address_key TEXT NOT NULL,
            address TEXT,
            city TEXT,
            state TEXT,
            permit_number TEXT,
            permit_type TEXT,
            work_type TEXT,
            trade_category TEXT,
            filing_date TEXT,
            estimated_cost REAL,
            description TEXT,
            contractor TEXT,
            UNIQUE(address_key, permit_number)
        );
        CREATE INDEX IF NOT EXISTS idx_history_address ON permit_history(address_key);
        CREATE INDEX IF NOT EXISTS idx_history_date ON permit_history(filing_date);

        CREATE TABLE IF NOT EXISTS collection_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_type TEXT NOT NULL,
            started_at TEXT DEFAULT (datetime('now')),
            completed_at TEXT,
            cities_processed INTEGER DEFAULT 0,
            permits_collected INTEGER DEFAULT 0,
            permits_new INTEGER DEFAULT 0,
            permits_updated INTEGER DEFAULT 0,
            status TEXT DEFAULT 'running',
            error_message TEXT,
            details TEXT
        );

        -- V12.54: Autonomy Engine Tables --

        -- us_cities: Master list of every US incorporated place
        CREATE TABLE IF NOT EXISTS us_cities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city_name TEXT NOT NULL,
            state TEXT NOT NULL,
            county TEXT,
            county_fips TEXT,
            population INTEGER DEFAULT 0,
            latitude REAL,
            longitude REAL,
            slug TEXT UNIQUE,
            status TEXT DEFAULT 'not_started',
            status_reason TEXT,
            covered_by_source TEXT,
            priority INTEGER DEFAULT 99999,
            last_searched_at TEXT,
            search_attempts INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_us_cities_status ON us_cities(status);
        CREATE INDEX IF NOT EXISTS idx_us_cities_priority ON us_cities(priority);
        CREATE INDEX IF NOT EXISTS idx_us_cities_state ON us_cities(state);
        CREATE INDEX IF NOT EXISTS idx_us_cities_slug ON us_cities(slug);
        CREATE INDEX IF NOT EXISTS idx_us_cities_county_fips ON us_cities(county_fips);

        -- us_counties: County targets (processed first for max coverage)
        CREATE TABLE IF NOT EXISTS us_counties (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            county_name TEXT NOT NULL,
            state TEXT NOT NULL,
            fips TEXT UNIQUE,
            population INTEGER DEFAULT 0,
            cities_in_county INTEGER DEFAULT 0,
            portal_domain TEXT,
            status TEXT DEFAULT 'not_started',
            status_reason TEXT,
            source_key TEXT,
            last_searched_at TEXT,
            priority INTEGER DEFAULT 99999,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_us_counties_status ON us_counties(status);
        CREATE INDEX IF NOT EXISTS idx_us_counties_priority ON us_counties(priority);
        CREATE INDEX IF NOT EXISTS idx_us_counties_fips ON us_counties(fips);

        -- city_sources: Discovered data sources (replaces CITY_REGISTRY/BULK_SOURCES)
        CREATE TABLE IF NOT EXISTS city_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
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
            last_collected_at TEXT,
            total_permits_collected INTEGER DEFAULT 0,
            covers_cities TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_city_sources_status ON city_sources(status);
        CREATE INDEX IF NOT EXISTS idx_city_sources_platform ON city_sources(platform);
        CREATE INDEX IF NOT EXISTS idx_city_sources_mode ON city_sources(mode);

        -- discovery_runs: Audit log for autonomy engine runs
        CREATE TABLE IF NOT EXISTS discovery_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_type TEXT NOT NULL,
            started_at TEXT DEFAULT (datetime('now')),
            completed_at TEXT,
            targets_searched INTEGER DEFAULT 0,
            sources_found INTEGER DEFAULT 0,
            permits_loaded INTEGER DEFAULT 0,
            cities_activated INTEGER DEFAULT 0,
            errors TEXT
        );

        -- V15: Collector Redesign Tables --

        -- prod_cities: Verified cities with working data sources
        -- Replaces heuristic-based city listing (KNOWN_OK_CITIES, VALID_STATES)
        CREATE TABLE IF NOT EXISTS prod_cities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            state TEXT NOT NULL,
            city_slug TEXT UNIQUE NOT NULL,
            source_type TEXT,
            source_id TEXT,
            source_scope TEXT,
            source_endpoint TEXT,
            verified_date TEXT,
            last_collection TEXT,
            last_permit_date TEXT,
            total_permits INTEGER DEFAULT 0,
            permits_last_30d INTEGER DEFAULT 0,
            avg_daily_permits REAL DEFAULT 0,
            status TEXT DEFAULT 'active' CHECK (status IN ('active', 'paused', 'failed', 'pending')),
            consecutive_failures INTEGER DEFAULT 0,
            last_error TEXT,
            added_by TEXT,
            added_at TEXT DEFAULT (datetime('now')),
            notes TEXT,
            UNIQUE(city, state)
        );
        CREATE INDEX IF NOT EXISTS idx_prod_cities_status ON prod_cities(status);
        CREATE INDEX IF NOT EXISTS idx_prod_cities_state ON prod_cities(state);
        CREATE INDEX IF NOT EXISTS idx_prod_cities_slug ON prod_cities(city_slug);
        CREATE INDEX IF NOT EXISTS idx_prod_cities_last_collection ON prod_cities(last_collection);

        -- scraper_runs: Per-city collection logging
        CREATE TABLE IF NOT EXISTS scraper_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_name TEXT,
            city TEXT,
            state TEXT,
            city_slug TEXT,
            run_started_at TEXT DEFAULT (datetime('now')),
            run_completed_at TEXT,
            duration_ms INTEGER,
            permits_found INTEGER DEFAULT 0,
            permits_inserted INTEGER DEFAULT 0,
            status TEXT CHECK (status IN ('success', 'error', 'no_new', 'timeout', 'skipped')),
            error_message TEXT,
            error_type TEXT,
            http_status INTEGER,
            response_size_bytes INTEGER,
            collection_type TEXT DEFAULT 'scheduled',
            triggered_by TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_scraper_runs_city_slug ON scraper_runs(city_slug);
        CREATE INDEX IF NOT EXISTS idx_scraper_runs_started ON scraper_runs(run_started_at);
        CREATE INDEX IF NOT EXISTS idx_scraper_runs_status ON scraper_runs(status);
    """)
    conn.commit()
    print(f"[DB] V15: Database initialized at {DB_PATH}")


# ---------------------------------------------------------------------------
# Permit CRUD — replaces load_permits(), atomic_write_json(permits.json), etc.
# ---------------------------------------------------------------------------

def upsert_permits(permits, source_city_key=None):
    """
    Insert or update permits. This is the core write operation that replaces
    all the JSON file writes. Uses INSERT OR REPLACE so duplicates by
    permit_number are automatically handled (newer data wins).

    Args:
        permits: list of permit dicts (same format as the old JSON)
        source_city_key: which city config collected these (for tracking)

    Returns:
        (new_count, updated_count)
    """
    # V12.56: Safety net - convert any dict/list values to strings to prevent SQLite binding errors
    for permit in permits:
        for key, val in list(permit.items()):
            if isinstance(val, (dict, list)):
                permit[key] = str(val)

        # V13.2: Validate date fields - clear if they don't look like dates
        # This fixes Mesa issue where reviewer names ("WROCCO") were stored as filing_date
        for date_field in ['filing_date', 'issued_date', 'date']:
            date_val = permit.get(date_field)
            if date_val and isinstance(date_val, str):
                # Valid dates start with digit (e.g., "2026-03-24" or "03/24/2026")
                if not date_val[0].isdigit():
                    permit[date_field] = None

        # V13.2: Validate estimated_cost - clear suspicious placeholder values
        # Common bad values: exactly $50M or $100M (likely parsing errors or defaults)
        cost = permit.get('estimated_cost')
        if cost:
            try:
                cost_float = float(cost)
                # Clear exact round millions that are likely placeholders
                if cost_float in (50000000, 100000000, 50000000.0, 100000000.0):
                    permit['estimated_cost'] = None
            except (ValueError, TypeError):
                pass

    conn = get_connection()
    now = datetime.now().isoformat()
    new_count = 0
    updated_count = 0

    # Check which ones already exist
    existing = set()
    permit_numbers = [p.get('permit_number') for p in permits if p.get('permit_number')]
    # Query in batches of 500 to avoid SQLite variable limits
    for i in range(0, len(permit_numbers), 500):
        batch = permit_numbers[i:i+500]
        placeholders = ','.join('?' * len(batch))
        cursor = conn.execute(
            f"SELECT permit_number FROM permits WHERE permit_number IN ({placeholders})",
            batch
        )
        existing.update(row[0] for row in cursor)

    # Batch insert/update
    for p in permits:
        pn = p.get('permit_number')
        if not pn:
            continue

        if pn in existing:
            updated_count += 1
        else:
            new_count += 1

        conn.execute("""
            INSERT OR REPLACE INTO permits (
                permit_number, city, state, address, zip,
                permit_type, permit_sub_type, work_type, trade_category,
                description, display_description, estimated_cost, value_tier,
                status, filing_date, issued_date, date,
                contact_name, contact_phone, contact_email, owner_name,
                contractor_name, square_feet, lifecycle_label,
                source_city_key, collected_at, updated_at
            ) VALUES (
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?
            )
        """, (
            pn, p.get('city'), p.get('state'), p.get('address'), p.get('zip'),
            p.get('permit_type'), p.get('permit_sub_type'), p.get('work_type'), p.get('trade_category'),
            p.get('description'), p.get('display_description'), p.get('estimated_cost', 0), p.get('value_tier'),
            p.get('status'), p.get('filing_date'), p.get('issued_date'), p.get('date'),
            p.get('contact_name'), p.get('contact_phone'), p.get('contact_email'), p.get('owner_name'),
            p.get('contractor_name'), p.get('square_feet'), p.get('lifecycle_label'),
            source_city_key, now, now
        ))

    conn.commit()
    print(f"[DB] Upserted permits: {new_count} new, {updated_count} updated")
    return new_count, updated_count


def query_permits(city=None, trade=None, value=None, status=None, quality=None,
                  search=None, page=1, per_page=50, order_by='filing_date DESC'):
    """
    Query permits with filters and pagination. Replaces the Python list
    comprehension filtering in /api/permits.

    Returns:
        (permits_list, total_count)
    """
    conn = get_connection()
    conditions = []
    params = []

    if city:
        conditions.append("city = ?")
        params.append(city)
    if trade and trade != 'all-trades':
        conditions.append("LOWER(trade_category) = LOWER(?)")
        params.append(trade)
    if value:
        conditions.append("value_tier = ?")
        params.append(value)
    if status:
        conditions.append("status = ?")
        params.append(status)
    if search:
        conditions.append("""
            (LOWER(address) LIKE ? OR LOWER(description) LIKE ?
             OR LOWER(contact_name) LIKE ? OR LOWER(permit_number) LIKE ?
             OR LOWER(zip) LIKE ?)
        """)
        like_param = f"%{search.lower()}%"
        params.extend([like_param] * 5)

    where_clause = " AND ".join(conditions) if conditions else "1=1"

    # Get total count
    count_sql = f"SELECT COUNT(*) FROM permits WHERE {where_clause}"
    total = conn.execute(count_sql, params).fetchone()[0]

    # Get page of results
    offset = (page - 1) * per_page
    data_sql = f"""
        SELECT * FROM permits
        WHERE {where_clause}
        ORDER BY {order_by}
        LIMIT ? OFFSET ?
    """
    cursor = conn.execute(data_sql, params + [per_page, offset])
    permits = [dict(row) for row in cursor]

    return permits, total


def get_permit_stats():
    """
    Get aggregate stats. Replaces loading all permits just to count them.
    This is a single SQL query — uses ~0 memory regardless of dataset size.
    """
    conn = get_connection()
    row = conn.execute("""
        SELECT
            COUNT(*) as total_permits,
            COALESCE(SUM(estimated_cost), 0) as total_value,
            COUNT(CASE WHEN estimated_cost >= 100000 THEN 1 END) as high_value_count,
            COUNT(DISTINCT city) as city_count
        FROM permits
    """).fetchone()

    return {
        'total_permits': row['total_permits'],
        'total_value': row['total_value'],
        'high_value_count': row['high_value_count'],
        'city_count': row['city_count'],
    }


def get_cities_with_permits():
    """Get list of cities that have permit data. Replaces get_cities_with_data()."""
    conn = get_connection()
    cursor = conn.execute("""
        SELECT DISTINCT city, state, COUNT(*) as permit_count
        FROM permits
        GROUP BY city, state
        ORDER BY city
    """)
    return [dict(row) for row in cursor]


def delete_old_permits(days=90):
    """
    Prune permits older than N days. Keeps the database from growing forever.
    This replaces the time-window filtering that was baked into collection.
    """
    conn = get_connection()
    cutoff = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    cursor = conn.execute(
        "DELETE FROM permits WHERE date < ? AND date != '' AND date IS NOT NULL",
        (cutoff,)
    )
    conn.commit()
    deleted = cursor.rowcount
    if deleted > 0:
        print(f"[DB] Pruned {deleted} permits older than {days} days")
    return deleted


def cleanup_invalid_dates():
    """
    V13.2: Fix permits with invalid date fields (e.g., reviewer names like "WROCCO").
    Sets filing_date/issued_date/date to NULL if they don't start with a digit.
    This is a one-time cleanup for existing bad data (e.g., Mesa AZ permits).

    Returns:
        dict with counts of fixed records per field
    """
    conn = get_connection()
    fixed = {}

    for field in ['filing_date', 'issued_date', 'date']:
        # Find and fix records where date field doesn't start with digit
        cursor = conn.execute(f"""
            UPDATE permits
            SET {field} = NULL
            WHERE {field} IS NOT NULL
              AND {field} != ''
              AND SUBSTR({field}, 1, 1) NOT GLOB '[0-9]'
        """)
        conn.commit()
        fixed[field] = cursor.rowcount
        if cursor.rowcount > 0:
            print(f"[DB] V13.2: Cleaned {cursor.rowcount} invalid {field} values")

    total = sum(fixed.values())
    if total > 0:
        print(f"[DB] V13.2: Total date cleanup: {total} records fixed")

    # V13.2: Also clean up suspicious cost values (exact $50M or $100M = likely placeholders)
    cost_cursor = conn.execute("""
        UPDATE permits
        SET estimated_cost = NULL
        WHERE estimated_cost IN (50000000, 100000000)
    """)
    conn.commit()
    cost_fixed = cost_cursor.rowcount
    if cost_fixed > 0:
        print(f"[DB] V13.2: Cleaned {cost_fixed} suspicious cost values ($50M/$100M placeholders)")
        fixed['estimated_cost'] = cost_fixed

    return fixed


# ---------------------------------------------------------------------------
# History — replaces permit_history.json
# ---------------------------------------------------------------------------

def upsert_history_permits(address_key, address, city, state, permits):
    """Insert history permits for one address. Deduplicates by (address_key, permit_number)."""
    conn = get_connection()
    for p in permits:
        conn.execute("""
            INSERT OR IGNORE INTO permit_history (
                address_key, address, city, state,
                permit_number, permit_type, work_type, trade_category,
                filing_date, estimated_cost, description, contractor
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            address_key, address, city, state,
            p.get('permit_number'), p.get('permit_type'), p.get('work_type'),
            p.get('trade_category'), p.get('filing_date'),
            p.get('estimated_cost'), p.get('description', '')[:200],
            p.get('contractor')
        ))
    conn.commit()


def get_address_history(address_key):
    """Get all permits for a normalized address."""
    conn = get_connection()
    cursor = conn.execute(
        "SELECT * FROM permit_history WHERE address_key = ? ORDER BY filing_date DESC",
        (address_key,)
    )
    return [dict(row) for row in cursor]


def get_repeat_renovators(min_permits=3):
    """Find addresses with N+ permits (repeat renovators)."""
    conn = get_connection()
    cursor = conn.execute("""
        SELECT address_key, address, city, state, COUNT(*) as permit_count
        FROM permit_history
        GROUP BY address_key
        HAVING COUNT(*) >= ?
        ORDER BY permit_count DESC
    """, (min_permits,))
    return [dict(row) for row in cursor]


# ---------------------------------------------------------------------------
# Collection Stats — V12.51: Replaces collection_stats.json
# ---------------------------------------------------------------------------

def get_collection_stats():
    """
    V12.51: Get the latest collection run stats.
    Returns a dict compatible with the old collection_stats.json format.
    """
    conn = get_connection()

    # Get latest completed collection run
    row = conn.execute("""
        SELECT * FROM collection_runs
        WHERE status = 'completed'
        ORDER BY completed_at DESC
        LIMIT 1
    """).fetchone()

    if row:
        return {
            'collected_at': row['completed_at'],
            'run_type': row['run_type'],
            'cities_processed': row['cities_processed'],
            'permits_collected': row['permits_collected'],
            'permits_new': row['permits_new'],
            'permits_updated': row['permits_updated'],
            'details': json.loads(row['details']) if row['details'] else {},
        }

    return {}


def record_collection_run(run_type, cities_processed, permits_collected,
                          permits_new, permits_updated, details=None, error=None):
    """
    V12.51: Record a collection run in the database.
    """
    conn = get_connection()
    status = 'completed' if not error else 'failed'
    conn.execute("""
        INSERT INTO collection_runs (
            run_type, completed_at, cities_processed, permits_collected,
            permits_new, permits_updated, status, error_message, details
        ) VALUES (?, datetime('now'), ?, ?, ?, ?, ?, ?, ?)
    """, (
        run_type, cities_processed, permits_collected,
        permits_new, permits_updated, status, error,
        json.dumps(details) if details else None
    ))
    conn.commit()


# ---------------------------------------------------------------------------
# V15: Prod Cities — Verified cities with working data sources
# ---------------------------------------------------------------------------

def get_prod_cities(status='active'):
    """
    V15: Get all cities from prod_cities table.
    Returns list of dicts compatible with the old get_cities_with_data() format.

    Args:
        status: Filter by status ('active', 'paused', 'failed', 'pending', or None for all)

    Returns:
        List of city dicts with keys: name, state, slug, permit_count, active
    """
    conn = get_connection()

    if status:
        cursor = conn.execute("""
            SELECT city, state, city_slug, total_permits, status, last_permit_date,
                   source_type, source_id, consecutive_failures, last_error
            FROM prod_cities
            WHERE status = ?
            ORDER BY total_permits DESC
        """, (status,))
    else:
        cursor = conn.execute("""
            SELECT city, state, city_slug, total_permits, status, last_permit_date,
                   source_type, source_id, consecutive_failures, last_error
            FROM prod_cities
            ORDER BY total_permits DESC
        """)

    cities = []
    for row in cursor:
        cities.append({
            'name': row['city'],
            'state': row['state'],
            'slug': row['city_slug'],
            'permit_count': row['total_permits'] or 0,
            'active': row['status'] == 'active',
            'last_permit_date': row['last_permit_date'],
            'source_type': row['source_type'],
            'source_id': row['source_id'],
            'consecutive_failures': row['consecutive_failures'],
            'last_error': row['last_error'],
        })

    return cities


def get_prod_city_count():
    """V15: Get count of active prod cities."""
    conn = get_connection()
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM prod_cities WHERE status = 'active'"
    ).fetchone()
    return row['cnt'] if row else 0


def is_prod_city(city_slug):
    """V15: Check if a city slug is in the prod_cities table."""
    conn = get_connection()
    row = conn.execute(
        "SELECT id, status FROM prod_cities WHERE city_slug = ?",
        (city_slug,)
    ).fetchone()
    return row is not None, row['status'] if row else None


def get_city_health_status():
    """
    V15: Get health status for all prod cities.
    Returns list sorted by health (worst first).
    """
    conn = get_connection()
    cursor = conn.execute("""
        SELECT
            city, state, city_slug, status, last_collection, last_permit_date,
            total_permits, avg_daily_permits, consecutive_failures, last_error,
            CAST(julianday('now') - julianday(last_permit_date) AS INTEGER) AS days_since_data,
            CASE
                WHEN status = 'failed' THEN 'RED'
                WHEN status = 'paused' THEN 'YELLOW'
                WHEN last_permit_date IS NULL THEN 'RED'
                WHEN julianday('now') - julianday(last_permit_date) <= 2 THEN 'GREEN'
                WHEN julianday('now') - julianday(last_permit_date) <= 7 THEN 'YELLOW'
                ELSE 'RED'
            END AS health_color
        FROM prod_cities
        WHERE status IN ('active', 'paused', 'failed')
        ORDER BY
            CASE
                WHEN status = 'failed' THEN 1
                WHEN status = 'paused' THEN 2
                ELSE 3
            END,
            CASE
                WHEN last_permit_date IS NULL THEN 9999
                ELSE julianday('now') - julianday(last_permit_date)
            END DESC
    """)
    return [dict(row) for row in cursor]


def upsert_prod_city(city, state, city_slug, source_type=None, source_id=None,
                     source_scope=None, status='active', added_by='manual', notes=None):
    """V15: Insert or update a prod city."""
    conn = get_connection()
    conn.execute("""
        INSERT INTO prod_cities (
            city, state, city_slug, source_type, source_id, source_scope,
            status, added_by, notes, verified_date
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(city_slug) DO UPDATE SET
            source_type = excluded.source_type,
            source_id = excluded.source_id,
            source_scope = excluded.source_scope,
            status = excluded.status,
            notes = excluded.notes,
            verified_date = datetime('now')
    """, (city, state, city_slug, source_type, source_id, source_scope,
          status, added_by, notes))
    conn.commit()


def update_prod_city_collection(city_slug, permits_found=0, last_permit_date=None, error=None):
    """V15: Update prod city after a collection run."""
    conn = get_connection()

    if error:
        # Increment failure count
        conn.execute("""
            UPDATE prod_cities SET
                consecutive_failures = consecutive_failures + 1,
                last_error = ?,
                last_collection = datetime('now')
            WHERE city_slug = ?
        """, (error, city_slug))

        # Check if should pause (3+ failures)
        row = conn.execute(
            "SELECT consecutive_failures FROM prod_cities WHERE city_slug = ?",
            (city_slug,)
        ).fetchone()
        if row and row['consecutive_failures'] >= 3:
            conn.execute(
                "UPDATE prod_cities SET status = 'paused' WHERE city_slug = ?",
                (city_slug,)
            )
    else:
        # Success - reset failures, update stats
        conn.execute("""
            UPDATE prod_cities SET
                consecutive_failures = 0,
                last_error = NULL,
                last_collection = datetime('now'),
                last_permit_date = COALESCE(?, last_permit_date),
                total_permits = total_permits + ?
            WHERE city_slug = ?
        """, (last_permit_date, permits_found, city_slug))

        # Reactivate if was paused
        conn.execute("""
            UPDATE prod_cities SET status = 'active'
            WHERE city_slug = ? AND status = 'paused'
        """, (city_slug,))

    conn.commit()


# ---------------------------------------------------------------------------
# V15: Scraper Runs — Per-city collection logging
# ---------------------------------------------------------------------------

def log_scraper_run(source_name=None, city=None, state=None, city_slug=None,
                    permits_found=0, permits_inserted=0, status='success',
                    error_message=None, error_type=None, duration_ms=None,
                    http_status=None, collection_type='scheduled', triggered_by=None):
    """V15: Log a scraper run."""
    conn = get_connection()
    conn.execute("""
        INSERT INTO scraper_runs (
            source_name, city, state, city_slug, run_completed_at,
            permits_found, permits_inserted, status, error_message, error_type,
            duration_ms, http_status, collection_type, triggered_by
        ) VALUES (?, ?, ?, ?, datetime('now'), ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        source_name, city, state, city_slug,
        permits_found, permits_inserted, status, error_message, error_type,
        duration_ms, http_status, collection_type, triggered_by
    ))
    conn.commit()


def get_daily_collection_summary(date=None):
    """V15: Get collection summary for a day."""
    conn = get_connection()
    if date is None:
        date = datetime.now().strftime('%Y-%m-%d')

    row = conn.execute("""
        SELECT
            DATE(run_started_at) AS run_date,
            COUNT(*) AS total_runs,
            SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS successful,
            SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) AS errors,
            SUM(CASE WHEN status = 'no_new' THEN 1 ELSE 0 END) AS no_new_data,
            SUM(CASE WHEN status = 'timeout' THEN 1 ELSE 0 END) AS timeouts,
            SUM(permits_inserted) AS total_permits_inserted,
            AVG(duration_ms) AS avg_duration_ms
        FROM scraper_runs
        WHERE DATE(run_started_at) = ?
    """, (date,)).fetchone()

    return dict(row) if row else None


def get_recent_scraper_runs(city_slug=None, limit=50):
    """V15: Get recent scraper runs, optionally filtered by city."""
    conn = get_connection()

    if city_slug:
        cursor = conn.execute("""
            SELECT * FROM scraper_runs
            WHERE city_slug = ?
            ORDER BY run_started_at DESC
            LIMIT ?
        """, (city_slug, limit))
    else:
        cursor = conn.execute("""
            SELECT * FROM scraper_runs
            ORDER BY run_started_at DESC
            LIMIT ?
        """, (limit,))

    return [dict(row) for row in cursor]


def prod_cities_table_exists():
    """V15: Check if prod_cities table exists and has data."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM prod_cities"
        ).fetchone()
        return row['cnt'] > 0
    except sqlite3.OperationalError:
        return False
