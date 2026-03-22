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
    """
    pid = os.getpid()

    # If we forked (Gunicorn worker), reset thread-local
    if not hasattr(_local, 'pid') or _local.pid != pid:
        _local.pid = pid
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
    """)
    conn.commit()
    print(f"[DB] V12.50: Database initialized at {DB_PATH}")


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
