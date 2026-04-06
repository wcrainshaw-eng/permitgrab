"""
PermitGrab V62 — Database layer (PostgreSQL + SQLite fallback)

V62: Uses db_engine for connection management. When DATABASE_URL is set,
uses PostgreSQL connection pool (eliminates "database is locked" errors).
Falls back to SQLite for local dev when DATABASE_URL is not set.

Original: V12.50 — SQLite database layer
"""

import sqlite3
import os
import json
import re
import threading
from datetime import datetime, timedelta

# V62: Import db_engine for Postgres-aware connections
try:
    from db_engine import get_connection as _engine_get_connection, USE_POSTGRES, init_schema
    _HAS_ENGINE = True
except ImportError:
    _HAS_ENGINE = False
    USE_POSTGRES = False


# V18: City name normalization to prevent duplicates like "Ft Lauderdale" vs "Fort Lauderdale"
CITY_NAME_REPLACEMENTS = {
    r'\bFt\.?\b': 'Fort',
    r'\bSt\.?\b': 'Saint',
    r'\bMt\.?\b': 'Mount',
    r'\bN\.?\b': 'North',
    r'\bS\.?\b': 'South',
    r'\bE\.?\b': 'East',
    r'\bW\.?\b': 'West',
}

# V18: Known garbage city names to filter out (database field names, test data, etc.)
# V19: Added decimal patterns and more bad values
GARBAGE_CITY_PATTERNS = [
    r'_',           # Underscores (e.g., "Archived_Buildings")
    r'^test',       # Test data
    r'^sample',     # Sample data
    r'^[\d.]+$',    # Numbers/decimals (e.g., '6', '5.0', '2.0')
    r'^null$',      # Null values
    r'^n/a$',       # N/A values
    r'^na$',        # NA values
    r'^none$',      # None values
    r'^unknown$',   # Unknown values
    r'^tbd$',       # TBD values
    r'^-$',         # Just a dash
    # V32: Additional garbage patterns found in data quality audit
    r'^dob now',        # NYC DOB system names (e.g., "DOB NOW: Build – Approved")
    r'^building$',      # Generic "Building" as city name
    r'^permits?$',      # "Permit" or "Permits" as city name
    r'permit application', # "Hamlin Building Permit Application"
    r'^archived',       # Archived dataset names
    r'featureserver',   # ArcGIS layer names leaking as city names
    r'mapserver',       # ArcGIS layer names
]

# V35: Strengthened garbage patterns — catches dataset/layer names that leaked through V32 filters
GARBAGE_CITY_PATTERNS_V35 = [
    r'county\s+sewer',        # "Kitsap County Sewer Data_WFL1"
    r'development\s+permit',  # "CFW Development Permits Table"
    r'building\s+and\s+safety', # "Building and Safety"
    r'case\s+history',        # "LA County Permitting (EPIC-LA Case History)"
    r'bureau\s+of',           # "Bureau of Engineering Permit Information"
    r'inspection',            # "Gas and Inspections"
    r'permit\s+info',         # "Permit Information"
    r'\bwfl\d',               # ArcGIS WFL layer names
    r'\bwgs\d',               # WGS coordinate system in name
    r'data_',                 # "Sewer Data_WFL1"
    r'^table$',               # Just "Table"
    r'epic-la',               # EPIC-LA system names
    r'^gas$',                 # "Gas" as city name
    r'^plumbing$',
    r'^electrical$',
    r'^mechanical$',
    r'^roofing$',
    r'^sign$',
    r'^fence$',
    r'^fire$',
    r'^demolition$',
    r'limited\s+alteration',  # "DOB NOW: Build – Limited Alteration Applications"
    r'approved\s+permit',     # System status descriptions
    r'\bfeature\s*layer\b',
    r'\brest\s*service\b',
    r'^city\s+of\s+',         # "City of Chicago" → should be just "Chicago"
]

# V19: Neighborhood/subdivision names that should map to their parent city
# Format: {(neighborhood, state): actual_city}
NEIGHBORHOOD_TO_CITY = {
    # Orlando, FL neighborhoods (from Orange County bulk source)
    ('Lake Nona South', 'FL'): 'Orlando', ('Lake Nona Central', 'FL'): 'Orlando',
    ('Lake Nona Estates', 'FL'): 'Orlando', ('Vista Park', 'FL'): 'Orlando',
    ('Vista East', 'FL'): 'Orlando', ('College Park', 'FL'): 'Orlando',
    ('Meridian Park', 'FL'): 'Orlando', ('Florida Center', 'FL'): 'Orlando',
    ('Florida Center North', 'FL'): 'Orlando', ('Central Business District', 'FL'): 'Orlando',
    ('Johnson Village', 'FL'): 'Orlando', ('33Rd St. Industrial', 'FL'): 'Orlando',
    ('Southeastern Oaks', 'FL'): 'Orlando', ('North Orange', 'FL'): 'Orlando',
    ('South Orange', 'FL'): 'Orlando', ('East Park', 'FL'): 'Orlando',
    ('West Colonial', 'FL'): 'Orlando', ('Airport North', 'FL'): 'Orlando',
    ('Rosemont', 'FL'): 'Orlando', ('Rosemont North', 'FL'): 'Orlando',
    ('Audubon Park', 'FL'): 'Orlando', ('Holden Heights', 'FL'): 'Orlando',
    ('Holden/Parramore', 'FL'): 'Orlando', ('Colonialtown South', 'FL'): 'Orlando',
    ('Colonial Town Center', 'FL'): 'Orlando', ('Lake Eola Heights', 'FL'): 'Orlando',
    ('Lake Fairview', 'FL'): 'Orlando', ('Lake Davis/Greenwood', 'FL'): 'Orlando',
    ('Lake Terrace', 'FL'): 'Orlando', ('Lake Underhill', 'FL'): 'Orlando',
    ('Lake Como', 'FL'): 'Orlando', ('Lake Cherokee', 'FL'): 'Orlando',
    ('Lake Formosa', 'FL'): 'Orlando', ('Lake Sunset', 'FL'): 'Orlando',
    ('Lake Copeland', 'FL'): 'Orlando', ('Lake Mann Estates', 'FL'): 'Orlando',
    ('Lake Weldona', 'FL'): 'Orlando', ('Park Lake/Highland', 'FL'): 'Orlando',
    ('Spring Lake', 'FL'): 'Orlando', ('Clear Lake', 'FL'): 'Orlando',
    ('Kirkman North', 'FL'): 'Orlando', ('Kirkman South', 'FL'): 'Orlando',
    ('Mercy Drive', 'FL'): 'Orlando', ('Boggy Creek', 'FL'): 'Orlando',
    ('Conway', 'FL'): 'Orlando', ('Storey Park', 'FL'): 'Orlando',
    ('Randal Park', 'FL'): 'Orlando', ('Northlake Park At Lake Nona', 'FL'): 'Orlando',
    ('Sunbridge/Icp', 'FL'): 'Orlando', ('Dover Shores West', 'FL'): 'Orlando',
    ('Dover Shores East', 'FL'): 'Orlando', ('Dover Estates', 'FL'): 'Orlando',
    ('Dover Manor', 'FL'): 'Orlando', ('Rose Isle', 'FL'): 'Orlando',
    ('Pineloch', 'FL'): 'Orlando', ('Princeton/Silver Star', 'FL'): 'Orlando',
    ('Milk District', 'FL'): 'Orlando', ('Thornton Park', 'FL'): 'Orlando',
    ('South Eola', 'FL'): 'Orlando', ('Delaney Park', 'FL'): 'Orlando',
    ('Lorna Doone', 'FL'): 'Orlando', ('Signal Hill', 'FL'): 'Orlando',
    ('Pershing', 'FL'): 'Orlando', ('Catalina', 'FL'): 'Orlando',
    ('Bryn Mawr', 'FL'): 'Orlando', ('Monterey', 'FL'): 'Orlando',
    ('Rock Lake', 'FL'): 'Orlando', ('Windhover', 'FL'): 'Orlando',
    ('Carver Shores', 'FL'): 'Orlando', ('North Quarter', 'FL'): 'Orlando',
    ('Southern Oaks', 'FL'): 'Orlando', ('South Semoran', 'FL'): 'Orlando',
    ('Lancaster Park', 'FL'): 'Orlando', ('Rowena Gardens', 'FL'): 'Orlando',
    ('Lawsona/Fern Creek', 'FL'): 'Orlando', ('Dixie Belle', 'FL'): 'Orlando',
    ('Malibu Groves', 'FL'): 'Orlando', ('Southport', 'FL'): 'Orlando',
    ('Bel Air', 'FL'): 'Orlando', ('Richmond Heights', 'FL'): 'Orlando',
    ('Richmond Estates', 'FL'): 'Orlando', ('Engelwood Park', 'FL'): 'Orlando',
    ('Wadeview Park', 'FL'): 'Orlando', ('Orwin Manor', 'FL'): 'Orlando',
    ('Ventura', 'FL'): 'Orlando', ('Bal Bay', 'FL'): 'Orlando',
    ('Crescent Park', 'FL'): 'Orlando', ('Timberleaf', 'FL'): 'Orlando',
    ('Countryside', 'FL'): 'Orlando', ('Mariners Village', 'FL'): 'Orlando',
    ('Orlando Executive Airport', 'FL'): 'Orlando',
    ('Orlando International Airport', 'FL'): 'Orlando',
    ('Seaboard Industrial', 'FL'): 'Orlando', ('Beltway Commerce Center', 'FL'): 'Orlando',
    ('Palomar', 'FL'): 'Orlando', ('Azalea Park', 'FL'): 'Orlando',
}

# V85: City name canonicalization — maps variant names to canonical names
# This fixes the issue where 180K+ permits aren't counted due to name mismatches
# Format: lowercase variant -> canonical name
CITY_NAME_CANONICALIZATION = {
    # Major city variants
    'new york city': 'New York',
    'nyc': 'New York',
    'manhattan': 'New York',
    'brooklyn': 'New York',
    'queens': 'New York',
    'bronx': 'New York',
    'staten island': 'New York',

    'washington dc': 'Washington',
    'washington d.c.': 'Washington',
    'washington, d.c.': 'Washington',
    'district of columbia': 'Washington',

    'orleans': 'New Orleans',  # Common truncation
    'nola': 'New Orleans',

    'la': 'Los Angeles',
    'l.a.': 'Los Angeles',

    'sf': 'San Francisco',
    's.f.': 'San Francisco',

    'philly': 'Philadelphia',
    'phila': 'Philadelphia',

    'vegas': 'Las Vegas',

    'ft worth': 'Fort Worth',
    'ft. worth': 'Fort Worth',

    'ft lauderdale': 'Fort Lauderdale',
    'ft. lauderdale': 'Fort Lauderdale',

    'st louis': 'Saint Louis',
    'st. louis': 'Saint Louis',

    'st paul': 'Saint Paul',
    'st. paul': 'Saint Paul',

    'st petersburg': 'Saint Petersburg',
    'st. petersburg': 'Saint Petersburg',

    # County variants - often reported without "County"
    'miami-dade': 'Miami-Dade County',
    'broward': 'Broward County',
    'palm beach': 'Palm Beach County',
    'orange county': 'Orange County',  # Keep as-is but normalize
    'los angeles county': 'Los Angeles County',
    'san diego county': 'San Diego County',
    'cook county': 'Cook County',

    # State abbreviation suffixes that sneak in
    'houston tx': 'Houston',
    'dallas tx': 'Dallas',
    'austin tx': 'Austin',
    'chicago il': 'Chicago',
    'phoenix az': 'Phoenix',
    'seattle wa': 'Seattle',
    'denver co': 'Denver',
    'atlanta ga': 'Atlanta',
    'miami fl': 'Miami',
    'tampa fl': 'Tampa',
    'orlando fl': 'Orlando',
}

# V85: Garbage city names that should be filtered out entirely
GARBAGE_CITY_NAMES = {
    'hickory creek',  # Bulk source garbage - 51K permits
    'island park',    # Suspicious bulk data
    'warr acres',     # Bulk source issue
    'unincorporated', # Generic placeholder
    'unknown',
    'n/a',
    'na',
    'none',
    'test',
    'sample',
}


def is_garbage_city_name(city_name):
    """V18/V35: Check if a city name is garbage (database field name, test data, etc.)."""
    if not city_name:
        return True
    name_lower = city_name.strip().lower()
    if len(name_lower) < 2:
        return True
    # V35: Also reject if longer than 40 chars (real city names are shorter)
    if len(name_lower) > 40:
        return True
    for pattern in GARBAGE_CITY_PATTERNS + GARBAGE_CITY_PATTERNS_V35:
        if re.search(pattern, name_lower, re.IGNORECASE):
            return True
    return False


def clean_city_name_for_prod(city_name, state=""):
    """V35: Clean city name before storing in prod_cities.
    Removes state abbreviations, fixes known corruptions.
    """
    if not city_name:
        return city_name
    import re
    cleaned = city_name.strip()

    # Remove state abbreviation from end (e.g., "Norfolk Va" → "Norfolk")
    if state and len(state) == 2:
        pattern = rf'\s+{re.escape(state)}$'
        cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE)

    # Remove " Metro", " Area", " Region" suffixes
    cleaned = re.sub(r'\s+(Metro|Area|Region)$', '', cleaned, flags=re.IGNORECASE)

    # Fix known corruptions
    cleaned = cleaned.replace("George'South", "George's")
    cleaned = cleaned.replace("Saint.", "St.")

    # Remove "City of " prefix
    cleaned = re.sub(r'^City\s+of\s+', '', cleaned, flags=re.IGNORECASE)

    return cleaned.strip()


def canonicalize_city_name(city_name, state=""):
    """V85: Canonicalize city name to match prod_cities entries.

    This handles:
    1. Known variant names (NYC -> New York, Orleans -> New Orleans)
    2. State suffix removal (Houston TX -> Houston)
    3. Garbage name filtering
    4. Neighborhood to city mapping

    Returns:
        tuple: (canonical_name, is_garbage)
        - canonical_name: The cleaned/canonical city name
        - is_garbage: True if this should be filtered out entirely
    """
    if not city_name:
        return city_name, True

    # Normalize whitespace and case for lookup
    cleaned = ' '.join(city_name.strip().split())
    lookup_key = cleaned.lower()

    # Check if it's a known garbage name
    if lookup_key in GARBAGE_CITY_NAMES:
        return city_name, True

    # Check garbage patterns
    if is_garbage_city_name(cleaned):
        return city_name, True

    # Check canonicalization map
    if lookup_key in CITY_NAME_CANONICALIZATION:
        return CITY_NAME_CANONICALIZATION[lookup_key], False

    # Check neighborhood mapping
    if state and (cleaned.title(), state.upper()) in NEIGHBORHOOD_TO_CITY:
        return NEIGHBORHOOD_TO_CITY[(cleaned.title(), state.upper())], False

    # Apply standard cleaning
    cleaned = clean_city_name_for_prod(cleaned, state)

    # Remove trailing state abbreviation if present (e.g., "Houston TX" -> "Houston")
    if state and len(state) == 2:
        import re
        pattern = rf'\s+{re.escape(state)}$'
        cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE)

    # Remove " City" suffix (e.g., "Oklahoma City" stays, but "Kansas City" stays too)
    # Only remove if it's redundant like "New York City" -> handled by canonicalization

    return cleaned.strip().title(), False


def normalize_city_name(city_name):
    """V18: Normalize city name to prevent duplicates from different sources.
    Converts abbreviations: Ft -> Fort, St -> Saint, Mt -> Mount, etc.
    """
    if not city_name:
        return city_name

    normalized = city_name.strip()
    for pattern, replacement in CITY_NAME_REPLACEMENTS.items():
        # Case-insensitive replacement, preserving original case style
        normalized = re.sub(pattern, replacement, normalized, flags=re.IGNORECASE)

    # Clean up multiple spaces
    normalized = re.sub(r'\s+', ' ', normalized).strip()

    # Title case for consistency
    normalized = normalized.title()

    return normalized


def normalize_city_slug(city_name, state=None):
    """V18: Generate normalized slug from city name.
    Example: 'Ft Lauderdale' -> 'fort-lauderdale'
    """
    normalized_name = normalize_city_name(city_name)
    slug = normalized_name.lower()
    slug = re.sub(r'[^a-z0-9]+', '-', slug)
    slug = slug.strip('-')
    return slug

# Use Render persistent disk if available
if os.path.isdir('/var/data'):
    DB_PATH = '/var/data/permitgrab.db'
else:
    DB_PATH = os.path.join(os.path.dirname(__file__), 'data', 'permitgrab.db')

# Thread-local connections (SQLite connections can't be shared across threads)
# V12.51: Also track PID to handle Gunicorn --preload fork correctly
_local = threading.local()


def get_connection():
    """Get a database connection.

    V62: Delegates to db_engine when DATABASE_URL is set (PostgreSQL).
    Falls back to thread-local SQLite for local dev.
    V70: Falls back to SQLite if Postgres pool not initialized.

    V12.51: Process-aware — resets connection after Gunicorn fork.
    V12.60: Validates connection is still open before returning.
    """
    # V62: Use Postgres pool when available
    # V70: TRY Postgres, but fall back to SQLite if pool not enabled
    if _HAS_ENGINE and USE_POSTGRES:
        try:
            return _engine_get_connection()
        except RuntimeError as e:
            # V70: Pool not initialized — fall back to SQLite
            if "not initialized" in str(e):
                pass  # Fall through to SQLite
            else:
                raise

    # SQLite fallback (local dev or V70: Postgres unavailable)
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
        _local.conn.execute("PRAGMA busy_timeout=60000")  # wait up to 60s for locks (V35: startup cleanup takes time)
    return _local.conn


def init_db():
    """Create tables and indexes if they don't exist. Safe to call multiple times.

    V62: When DATABASE_URL is set, uses db_engine.init_schema() for Postgres DDL.
    V70: Skips Postgres init if pool not enabled — falls through to SQLite.
    """
    # V62: Postgres path — use db_engine schema
    # V70: Skip if pool not enabled
    if _HAS_ENGINE and USE_POSTGRES:
        try:
            from db_engine import is_pg_pool_enabled
            if not is_pg_pool_enabled():
                print(f"[DB] V70: Postgres pool not enabled, using SQLite only")
                # Fall through to SQLite path below
            else:
                init_schema()
                conn = get_connection()
                # Run each migration step with isolated error handling
                # so one failure doesn't cascade and kill the worker
                _migrations = [
                    ("V18 migrations", lambda: _run_v18_migrations_pg(conn)),
                    ("V33 source linking", lambda: _run_v33_source_linking(conn)),
                    ("V34 data cleanup", lambda: _run_v34_data_cleanup(conn)),
                    ("Bulk city deactivation", lambda: _deactivate_bulk_covered_cities(conn)),
                    ("ArcGIS date formats", lambda: _fix_arcgis_date_formats(conn)),
                    ("V85 city name canonicalization", lambda: _migrate_canonical_city_names(conn)),
                    ("V86 city linking", lambda: _run_v86_city_linking(conn)),
                    ("Prod city count sync", lambda: _sync_prod_city_counts(conn)),
                    ("V64 staleness columns", lambda: _run_v64_staleness_columns(conn)),
                ]
                for name, fn in _migrations:
                    try:
                        fn()
                        conn.commit()
                    except Exception as e:
                        print(f"[DB] {name} error (non-fatal): {e}")
                        try:
                            conn.rollback()
                        except Exception:
                            pass
                print(f"[DB] V62: PostgreSQL database initialized via db_engine")
                conn.close()
                return
        except Exception as e:
            print(f"[DB] V70: Postgres init failed (non-fatal): {e}")
            # Fall through to SQLite

    # SQLite path (local dev)
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
            -- V18: Staleness detection columns
            data_freshness TEXT DEFAULT 'fresh' CHECK (data_freshness IN ('fresh', 'aging', 'stale', 'very_stale', 'no_data', 'error', 'unknown')),
            newest_permit_date TEXT,
            stale_since TEXT,
            pause_reason TEXT,
            -- V64: Enhanced staleness tracking
            consecutive_no_new INTEGER DEFAULT 0,
            last_run_status TEXT,
            UNIQUE(city, state)
        );
        CREATE INDEX IF NOT EXISTS idx_prod_cities_status ON prod_cities(status);
        CREATE INDEX IF NOT EXISTS idx_prod_cities_state ON prod_cities(state);
        CREATE INDEX IF NOT EXISTS idx_prod_cities_slug ON prod_cities(city_slug);
        CREATE INDEX IF NOT EXISTS idx_prod_cities_last_collection ON prod_cities(last_collection);
        -- NOTE: freshness index created in _run_v18_migrations() after column is added

        -- V18: stale_cities_review: Manual review queue for cities without automated sources
        CREATE TABLE IF NOT EXISTS stale_cities_review (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            state TEXT NOT NULL,
            original_source TEXT,
            last_permit_date TEXT,
            stale_since TEXT,
            auto_search_attempted INTEGER DEFAULT 0,
            auto_search_result TEXT,
            manual_notes TEXT,
            alternate_source_url TEXT,
            status TEXT DEFAULT 'needs_review' CHECK (status IN ('needs_review', 'in_progress', 'resolved', 'no_source')),
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            UNIQUE(city, state)
        );

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

        -- V17: system_state for tracking daily tasks (discovery, etc.)
        CREATE TABLE IF NOT EXISTS system_state (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT DEFAULT (datetime('now'))
        );

        -- V17: city_activation_log for tracking newly activated cities
        CREATE TABLE IF NOT EXISTS city_activation_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city_slug TEXT NOT NULL,
            city_name TEXT NOT NULL,
            state TEXT NOT NULL,
            activated_at TEXT DEFAULT (datetime('now')),
            source TEXT,
            initial_permits INTEGER DEFAULT 0,
            seo_status TEXT DEFAULT 'needs_content',
            notes TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_city_activation_log_activated ON city_activation_log(activated_at);

        -- V17: discovered_sources for dynamically discovered data sources
        CREATE TABLE IF NOT EXISTS discovered_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
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
            discovered_at TEXT DEFAULT (datetime('now')),
            last_tested TEXT,
            status TEXT DEFAULT 'active',
            notes TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_discovered_sources_status ON discovered_sources(status);
    """)
    conn.commit()

    # V18: Migrations for staleness detection columns
    _run_v18_migrations(conn)

    # V33: Link prod_cities to CITY_REGISTRY source_ids
    _run_v33_source_linking(conn)

    # V34: Run data cleanup (fix wrong states, remove garbage records)
    _run_v34_data_cleanup(conn)

    # V35: Deactivate city sources redundant with bulk sources
    _deactivate_bulk_covered_cities(conn)

    # V35: Fix ArcGIS date_format for MapServer endpoints that can't handle epoch filters
    _fix_arcgis_date_formats(conn)

    # V85: Canonicalize existing permit city names
    _migrate_canonical_city_names(conn)

    # V34: Sync prod_cities.total_permits with actual permit counts in DB
    _sync_prod_city_counts(conn)

    # V64: Add enhanced staleness tracking columns
    _run_v64_staleness_columns(conn)

    # V86: Add prod_city_id foreign keys and link data
    _run_v86_city_linking(conn)

    print(f"[DB] V86: Database initialized at {DB_PATH}")


def _run_v18_migrations_pg(conn):
    """V62: Postgres-compatible V18 migrations (no PRAGMA table_info)."""
    try:
        # Check existing columns using information_schema
        cursor = conn.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'prod_cities'
        """)
        existing_cols = {row['column_name'] for row in cursor}

        v18_columns = [
            ("data_freshness", "TEXT DEFAULT 'fresh'"),
            ("newest_permit_date", "TEXT"),
            ("stale_since", "TIMESTAMP"),
            ("pause_reason", "TEXT"),
        ]

        for col_name, col_def in v18_columns:
            if col_name not in existing_cols:
                try:
                    conn.execute(f"ALTER TABLE prod_cities ADD COLUMN {col_name} {col_def}")
                    print(f"[V18-PG] Added column: prod_cities.{col_name}")
                except Exception:
                    pass

        conn.commit()
    except Exception as e:
        print(f"[V18-PG] Migration error (non-fatal): {e}")
        try:
            conn.rollback()
        except:
            pass


def _run_v18_migrations(conn):
    """V18: Add staleness detection columns to existing prod_cities tables."""
    # Check which columns exist
    cursor = conn.execute("PRAGMA table_info(prod_cities)")
    existing_cols = {row[1] for row in cursor.fetchall()}

    # Add missing V18 columns
    v18_columns = [
        ("data_freshness", "TEXT DEFAULT 'fresh'"),
        ("newest_permit_date", "TEXT"),
        ("stale_since", "TEXT"),
        ("pause_reason", "TEXT"),
    ]

    for col_name, col_def in v18_columns:
        if col_name not in existing_cols:
            try:
                conn.execute(f"ALTER TABLE prod_cities ADD COLUMN {col_name} {col_def}")
                print(f"[V18] Added column: prod_cities.{col_name}")
            except Exception as e:
                pass  # Column may already exist

    # Create freshness index if not exists
    try:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_prod_cities_freshness ON prod_cities(data_freshness)")
    except:
        pass

    # Create stale_cities_review table if not exists
    conn.execute("""
        CREATE TABLE IF NOT EXISTS stale_cities_review (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            state TEXT NOT NULL,
            original_source TEXT,
            last_permit_date TEXT,
            stale_since TEXT,
            auto_search_attempted INTEGER DEFAULT 0,
            auto_search_result TEXT,
            manual_notes TEXT,
            alternate_source_url TEXT,
            status TEXT DEFAULT 'needs_review',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            UNIQUE(city, state)
        )
    """)
    conn.commit()


def _run_v64_staleness_columns(conn):
    """V64: Add enhanced staleness tracking columns."""
    # Check which columns exist
    try:
        cursor = conn.execute("PRAGMA table_info(prod_cities)")
        existing_cols = {row[1] for row in cursor.fetchall()}
    except Exception:
        # Postgres path
        try:
            cursor = conn.execute("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name = 'prod_cities'
            """)
            existing_cols = {row[0] if isinstance(row, tuple) else row['column_name'] for row in cursor}
        except Exception:
            existing_cols = set()

    # Add missing V64 columns
    v64_columns = [
        ("consecutive_no_new", "INTEGER DEFAULT 0"),
        ("last_run_status", "TEXT"),
    ]

    for col_name, col_def in v64_columns:
        if col_name not in existing_cols:
            try:
                conn.execute(f"ALTER TABLE prod_cities ADD COLUMN {col_name} {col_def}")
                print(f"[V64] Added column: prod_cities.{col_name}")
            except Exception:
                pass  # Column may already exist

    conn.commit()


def _run_v86_city_linking(conn):
    """V86: Add prod_city_id foreign keys to city_sources and permits.

    This creates a clean architecture where:
    - Every source explicitly belongs to a city (via prod_city_id FK)
    - Every permit explicitly belongs to a city (via prod_city_id FK)
    - Counts are computed directly from FK relationships

    This replaces the flaky name-based matching that caused 700+ cities to show 0 permits.
    """
    import time
    start = time.time()

    # Step 1: Add prod_city_id column to city_sources if not exists
    try:
        cursor = conn.execute("PRAGMA table_info(city_sources)")
        existing_cols = {row[1] for row in cursor.fetchall()}
    except Exception:
        try:
            cursor = conn.execute("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name = 'city_sources'
            """)
            existing_cols = {row[0] if isinstance(row, tuple) else row['column_name'] for row in cursor}
        except Exception:
            existing_cols = set()

    if 'prod_city_id' not in existing_cols:
        try:
            conn.execute("ALTER TABLE city_sources ADD COLUMN prod_city_id INTEGER REFERENCES prod_cities(id)")
            print("[V86] Added city_sources.prod_city_id column")
        except Exception as e:
            print(f"[V86] city_sources.prod_city_id may already exist: {e}")

    # Step 2: Add prod_city_id column to permits if not exists
    try:
        cursor = conn.execute("PRAGMA table_info(permits)")
        permit_cols = {row[1] for row in cursor.fetchall()}
    except Exception:
        try:
            cursor = conn.execute("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name = 'permits'
            """)
            permit_cols = {row[0] if isinstance(row, tuple) else row['column_name'] for row in cursor}
        except Exception:
            permit_cols = set()

    if 'prod_city_id' not in permit_cols:
        try:
            conn.execute("ALTER TABLE permits ADD COLUMN prod_city_id INTEGER REFERENCES prod_cities(id)")
            print("[V86] Added permits.prod_city_id column")
        except Exception as e:
            print(f"[V86] permits.prod_city_id may already exist: {e}")

    conn.commit()

    # Step 3: Create indexes for the new FK columns
    try:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_city_sources_prod_city ON city_sources(prod_city_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_permits_prod_city ON permits(prod_city_id)")
        conn.commit()
        print("[V86] Created indexes for prod_city_id columns")
    except Exception as e:
        print(f"[V86] Index creation note: {e}")

    # Step 4: Build canonical name -> prod_city_id lookup
    prod_cities = conn.execute("SELECT id, city, state FROM prod_cities").fetchall()
    city_lookup = {}  # (canonical_lower, state) -> prod_city_id
    for row in prod_cities:
        canonical, _ = canonicalize_city_name(row[1], row[2] or '')
        key = (canonical.lower(), (row[2] or '').upper())
        city_lookup[key] = row[0]
    print(f"[V86] Built lookup for {len(city_lookup)} prod_cities")

    # Step 5: Link city_sources to prod_cities
    sources = conn.execute("""
        SELECT id, name, state FROM city_sources WHERE prod_city_id IS NULL
    """).fetchall()

    linked_sources = 0
    for row in sources:
        canonical, is_garbage = canonicalize_city_name(row[1], row[2] or '')
        if is_garbage:
            continue
        key = (canonical.lower(), (row[2] or '').upper())
        if key in city_lookup:
            conn.execute("UPDATE city_sources SET prod_city_id = ? WHERE id = ?",
                        (city_lookup[key], row[0]))
            linked_sources += 1

    conn.commit()
    print(f"[V86] Linked {linked_sources} city_sources to prod_cities")

    # Step 6: Link permits to prod_cities (in batches for performance)
    # First check how many need linking
    unlinked = conn.execute("SELECT COUNT(*) FROM permits WHERE prod_city_id IS NULL").fetchone()[0]
    if unlinked == 0:
        print("[V86] All permits already linked")
    else:
        print(f"[V86] Linking {unlinked:,} permits to prod_cities...")

        # Get distinct city/state combos from permits that need linking
        combos = conn.execute("""
            SELECT DISTINCT city, state FROM permits
            WHERE prod_city_id IS NULL AND city IS NOT NULL
        """).fetchall()

        linked_permits = 0
        for city, state in combos:
            canonical, is_garbage = canonicalize_city_name(city, state or '')
            if is_garbage:
                continue
            key = (canonical.lower(), (state or '').upper())
            if key in city_lookup:
                prod_city_id = city_lookup[key]
                result = conn.execute("""
                    UPDATE permits SET prod_city_id = ?
                    WHERE city = ? AND (state = ? OR (state IS NULL AND ? IS NULL))
                    AND prod_city_id IS NULL
                """, (prod_city_id, city, state, state))
                linked_permits += result.rowcount

        conn.commit()
        print(f"[V86] Linked {linked_permits:,} permits to prod_cities")

    elapsed = time.time() - start
    print(f"[V86] City linking migration completed in {elapsed:.1f}s")


def _run_v33_source_linking(conn):
    """V33: Link prod_cities to CITY_REGISTRY source_ids so the collector picks them up.

    Many prod_cities have source_id=NULL or 'N/A' even though they have valid
    CITY_REGISTRY entries. This migration matches them by slug and updates source_id
    so the V16 collection loop actually fetches their permits.
    """
    try:
        from city_configs import CITY_REGISTRY

        # Get all prod_cities that have no valid source_id
        cursor = conn.execute("""
            SELECT id, city, state, city_slug, source_id, source_type
            FROM prod_cities
            WHERE source_id IS NULL OR source_id = '' OR source_id = 'N/A'
        """)
        unlinked = cursor.fetchall()
        if not unlinked:
            return

        linked_count = 0
        for row in unlinked:
            city_slug = row['city_slug']
            city_name = row['city']
            state = row['state']

            # Try matching by slug directly, then underscore variant, then name
            matched_key = None
            slug_underscore = city_slug.replace('-', '_')
            slug_with_state = f"{city_slug}-{state.lower()}" if state else None
            slug_under_state = f"{slug_underscore}_{state.lower()}" if state else None

            # Priority order: exact slug, underscore variant, slug+state, underscore+state
            for candidate in [city_slug, slug_underscore, slug_with_state, slug_under_state]:
                if candidate and candidate in CITY_REGISTRY:
                    matched_key = candidate
                    break

            # Fallback: match by city name + state
            if not matched_key:
                for key, cfg in CITY_REGISTRY.items():
                    if (cfg.get('name', '').lower() == city_name.lower()
                            and cfg.get('state', '').upper() == state.upper()):
                        matched_key = key
                        break

            # Last resort: match by slug field in registry
            if not matched_key:
                for key, cfg in CITY_REGISTRY.items():
                    if cfg.get('slug') == city_slug:
                        matched_key = key
                        break

            if matched_key:
                cfg = CITY_REGISTRY[matched_key]
                if cfg.get('active', False):
                    platform = cfg.get('platform', 'unknown')
                    conn.execute("""
                        UPDATE prod_cities
                        SET source_id = ?, source_type = ?
                        WHERE id = ?
                    """, (matched_key, platform, row['id']))
                    linked_count += 1

        if linked_count > 0:
            conn.commit()
            print(f"[V33] Linked {linked_count} prod_cities to CITY_REGISTRY source_ids")
        else:
            print("[V33] No unlinked prod_cities needed source_id updates")

    except Exception as e:
        print(f"[V33] Source linking migration error: {e}")


def _deactivate_bulk_covered_cities(conn):
    """V35/V82: Deactivate city_sources that have NEVER produced permits AND are old.

    V82 FIX: Only deactivate sources that:
    1. Have been around for at least 7 days (created_at older than 7 days)
    2. Have been collected at least once (last_collected_at is not null)
    3. Still have 0 total_permits_collected

    This prevents newly added sources from being deactivated before they get a chance to run.
    """
    try:
        # V82: Only deactivate sources that are old AND have been tried AND still have no data
        result = conn.execute("""
            UPDATE city_sources
            SET status = 'inactive', last_failure_reason = 'v82_no_data_after_collection'
            WHERE status = 'active'
              AND (total_permits_collected IS NULL OR total_permits_collected = 0)
              AND last_collected_at IS NOT NULL
              AND created_at < datetime('now', '-7 days')
        """)
        deactivated = result.rowcount

        if deactivated > 0:
            conn.commit()
            print(f"[V82] Deactivated {deactivated} old city sources that never produced data after collection.")
        else:
            print(f"[V82] No old unproductive sources to deactivate.")

    except Exception as e:
        print(f"[V82] Source deactivation error: {e}")


def _fix_arcgis_date_formats(conn):
    """V35: Fix ArcGIS endpoints that can't handle date filters in WHERE clause.

    Many MapServer endpoints (and some FeatureServer) return 400 errors when we use
    epoch or DATE comparisons. Fix by storing date_format in field_map JSON (since
    city_sources table has no date_format column) and updating the collector to read it.
    """
    try:
        # These cities have endpoints that choke on date queries in WHERE clause
        FIX_TARGETS = {
            'phoenix', 'arlington', 'chattanooga', 'cleveland',
            'columbus', 'durham', 'knoxville', 'minneapolis',
            'baltimore', 'sacramento', 'virginia_beach', 'asheville_nc', 'deltona_fl',
            'washington_dc', 'tacoma',
        }

        import json
        fixed = 0
        for key in FIX_TARGETS:
            row = conn.execute(
                "SELECT source_key, field_map FROM city_sources WHERE source_key = ?",
                (key,)
            ).fetchone()
            if row:
                try:
                    fmap = json.loads(row['field_map']) if row['field_map'] else {}
                except:
                    fmap = {}
                if fmap.get('_date_format') != 'none':
                    fmap['_date_format'] = 'none'
                    conn.execute(
                        "UPDATE city_sources SET field_map = ? WHERE source_key = ?",
                        (json.dumps(fmap), key)
                    )
                    fixed += 1

        if fixed > 0:
            conn.commit()
            print(f"[V35] Fixed date_format for {fixed} ArcGIS endpoints (stored _date_format=none in field_map)")
    except Exception as e:
        print(f"[V35] ArcGIS date format fix error: {e}")


def _run_v34_data_cleanup(conn):
    return  # DISABLED - causes startup hang on large Postgres tables
    """V34: Comprehensive data cleanup for known data quality issues.

    Fixes:
    1. Wrong state assignments (Houston OK→TX, Austin OK→TX, etc.)
    2. State names used as city names (South Dakota, Michigan, etc.)
    3. Permit types used as city names (Gas, Plumbing, etc.)
    4. Missing states
    5. Casing inconsistencies for major cities
    6. Delete garbage records that can't be fixed
    """
    try:
        total_fixed = 0

        # 1. Fix wrong state assignments using prod_cities as truth
        # Build authoritative city→state map from prod_cities
        prod_rows = conn.execute(
            "SELECT LOWER(city) as city_lower, state FROM prod_cities WHERE state IS NOT NULL AND state != ''"
        ).fetchall()
        city_to_state = {r['city_lower']: r['state'] for r in prod_rows}

        # Fix permits where city exists in prod_cities but state doesn't match
        for city_lower, correct_state in city_to_state.items():
            result = conn.execute("""
                UPDATE permits SET state = ?
                WHERE LOWER(city) = ? AND (state != ? OR state IS NULL OR state = '')
            """, (correct_state, city_lower, correct_state))
            if result.rowcount > 0:
                print(f"[V34] Fixed {result.rowcount} permits: {city_lower} → state={correct_state}")
                total_fixed += result.rowcount

        # 2. V35: Bulk fix for OK→TX misassignment
        # A Texas bulk source had state="OK" in its config, poisoning ~58K permits.
        # Only a handful of cities tagged "OK" are actually in Oklahoma.
        ACTUALLY_OKLAHOMA = {
            'warr acres', 'oklahoma city', 'tulsa', 'norman', 'edmond',
            'broken arrow', 'moore', 'midwest city', 'enid', 'stillwater',
            'lawton', 'muskogee', 'bartlesville', 'bethany', 'del city',
            'yukon', 'mustang', 'shawnee', 'bixby', 'jenks', 'owasso',
            'sand springs', 'sapulpa', 'claremore', 'ponca city', 'duncan',
            'ardmore', 'ada', 'mcalester', 'durant', 'tahlequah', 'el reno',
            'guthrie', 'chickasha', 'okmulgee', 'altus', 'pryor creek',
        }
        ok_cities = conn.execute(
            "SELECT DISTINCT city FROM permits WHERE state = 'OK'"
        ).fetchall()
        for row in ok_cities:
            if row['city'].lower().strip() not in ACTUALLY_OKLAHOMA:
                result = conn.execute(
                    "UPDATE permits SET state = 'TX' WHERE city = ? AND state = 'OK'",
                    (row['city'],)
                )
                if result.rowcount > 0:
                    print(f"[V35] Fixed {result.rowcount} permits: {row['city']} OK → TX")
                    total_fixed += result.rowcount

        # V35: Fix LA→CA misassignment (LA County bulk source tagged CA cities as Louisiana)
        ACTUALLY_LOUISIANA = {
            'orleans', 'new orleans', 'baton rouge', 'cameron parish', 'shreveport',
            'lafayette', 'lake charles', 'kenner', 'bossier city', 'slidell',
            'houma', 'hammond', 'monroe', 'alexandria', 'natchitoches', 'opelousas',
            'ruston', 'sulphur', 'zachary', 'west monroe', 'denham springs',
            'pineville', 'bogalusa', 'crowley', 'minden', 'abbeville', 'thibodaux',
            'eunice', 'rayne', 'morgan city', 'covington', 'mandeville', 'gonzales',
            'metairie', 'marrero', 'harvey', 'chalmette', 'gretna', 'westwego',
            'terrytown', 'avondale', 'estelle', 'river ridge', 'destrehan', 'arabi',
            'luling', 'laplace', 'prairieville', 'central', 'youngsville',
            'breaux bridge', 'scott', 'carencro', 'broussard', 'new iberia',
            'jennings', 'leesville', 'marksville', 'ville platte', 'deridder',
            'tallulah', 'winnfield', 'jonesboro', 'grambling', 'bastrop', 'oakdale',
            'welsh', 'church point', 'st. martinville', 'kaplan', 'jeanerette',
            'patterson', 'franklin', 'berwick', 'lutcher', 'gramercy', 'reserve',
            'hahnville', 'belle chasse', 'jefferson',
        }
        la_cities = conn.execute(
            "SELECT DISTINCT city FROM permits WHERE state = 'LA'"
        ).fetchall()
        for row in la_cities:
            if row['city'].lower().strip() not in ACTUALLY_LOUISIANA:
                result = conn.execute(
                    "UPDATE permits SET state = 'CA' WHERE city = ? AND state = 'LA'",
                    (row['city'],)
                )
                if result.rowcount > 0:
                    print(f"[V35] Fixed {result.rowcount} permits: {row['city']} LA → CA")
                    total_fixed += result.rowcount

        # Other known misattributions
        KNOWN_FIXES = [
            ('Little Rock', 'WI', 'AR'),
            ('LITTLE ROCK', 'WI', 'AR'),
            ('North Little Rock', 'WI', 'AR'),
        ]
        for city, wrong_state, correct_state in KNOWN_FIXES:
            result = conn.execute(
                "UPDATE permits SET state = ? WHERE city = ? AND state = ?",
                (correct_state, city, wrong_state)
            )
            if result.rowcount > 0:
                print(f"[V34] Fixed {result.rowcount}: {city},{wrong_state} → {correct_state}")
                total_fixed += result.rowcount

        # 3. Delete records with state names as city names
        STATE_NAMES_AS_CITIES = [
            'Alabama', 'Alaska', 'Arizona', 'Arkansas', 'California', 'Colorado',
            'Connecticut', 'Delaware', 'Florida', 'Georgia', 'Hawaii', 'Idaho',
            'Illinois', 'Indiana', 'Iowa', 'Kansas', 'Kentucky', 'Louisiana',
            'Maine', 'Maryland', 'Massachusetts', 'Michigan', 'Minnesota',
            'Mississippi', 'Missouri', 'Montana', 'Nebraska', 'Nevada',
            'New Hampshire', 'New Jersey', 'New Mexico', 'New York',
            'North Carolina', 'North Dakota', 'Ohio', 'Oklahoma', 'Oregon',
            'Pennsylvania', 'Rhode Island', 'South Carolina', 'South Dakota',
            'Tennessee', 'Texas', 'Utah', 'Vermont', 'Virginia', 'Washington',
            'West Virginia', 'Wisconsin', 'Wyoming',
            # Also county-as-city patterns
            'Texas County',
        ]
        for state_name in STATE_NAMES_AS_CITIES:
            result = conn.execute(
                "DELETE FROM permits WHERE city = ? OR city = ?",
                (state_name, state_name.upper())
            )
            if result.rowcount > 0:
                print(f"[V34] Deleted {result.rowcount} permits with city='{state_name}'")
                total_fixed += result.rowcount

        # 4. Delete records with permit types as city names
        PERMIT_TYPES_AS_CITIES = [
            'Gas', 'Plumbing', 'Electrical', 'Mechanical', 'Building',
            'Fire', 'Demolition', 'Roofing', 'Sign', 'Fence',
            'GAS', 'PLUMBING', 'ELECTRICAL', 'MECHANICAL', 'BUILDING',
        ]
        for ptype in PERMIT_TYPES_AS_CITIES:
            result = conn.execute(
                "DELETE FROM permits WHERE city = ?", (ptype,)
            )
            if result.rowcount > 0:
                print(f"[V34] Deleted {result.rowcount} permits with city='{ptype}'")
                total_fixed += result.rowcount

        # 5. Delete records with empty/null city
        result = conn.execute(
            "DELETE FROM permits WHERE city IS NULL OR city = '' OR LENGTH(city) < 2"
        )
        if result.rowcount > 0:
            print(f"[V34] Deleted {result.rowcount} permits with empty/short city names")
            total_fixed += result.rowcount

        # 6. Normalize city casing for major cities (title case)
        major_cities = conn.execute("""
            SELECT city, COUNT(*) as cnt FROM permits
            WHERE city = UPPER(city) AND LENGTH(city) > 3
            GROUP BY city HAVING cnt > 10
        """).fetchall()
        for row in major_cities:
            # Convert "HOUSTON" to "Houston", "SAN ANTONIO" to "San Antonio"
            title_case = row['city'].title()
            result = conn.execute(
                "UPDATE permits SET city = ? WHERE city = ?",
                (title_case, row['city'])
            )
            if result.rowcount > 0:
                print(f"[V34] Normalized casing: {row['city']} → {title_case} ({result.rowcount} permits)")
                total_fixed += result.rowcount

        # V35: Fix "City of X" → "X" in permits
        city_of_rows = conn.execute("""
            SELECT DISTINCT city FROM permits
            WHERE city LIKE 'City of %' OR city LIKE 'CITY OF %'
        """).fetchall()
        for row in city_of_rows:
            old_name = row['city']
            new_name = re.sub(r'^City\s+of\s+', '', old_name, flags=re.IGNORECASE).strip()
            if new_name and len(new_name) > 2:
                result = conn.execute(
                    "UPDATE permits SET city = ? WHERE city = ?",
                    (new_name, old_name)
                )
                if result.rowcount > 0:
                    print(f"[V35] Renamed city: '{old_name}' → '{new_name}' ({result.rowcount} permits)")
                    total_fixed += result.rowcount

        # V35: Delete all garbage records that match V35 patterns
        garbage_rows = conn.execute("""
            SELECT DISTINCT city, COUNT(*) as cnt FROM permits
            WHERE LENGTH(city) > 40
               OR city LIKE '%Sewer%'
               OR city LIKE '%WFL%'
               OR city LIKE '%WGS%'
               OR city LIKE '%Table%'
               OR city LIKE '%Safety%'
               OR city LIKE '%Engineering%'
               OR city LIKE '%EPIC%'
               OR city LIKE '%Bureau%'
               OR city LIKE '%Inspection%'
               OR city LIKE '%Feature%Layer%'
               OR city LIKE '%Rest%Service%'
               OR city LIKE '%Case History%'
               OR city LIKE '%Development Permit%'
               OR city LIKE '%DOB NOW%'
               OR city LIKE '%Limited Alteration%'
               OR city LIKE '%Permit Application%'
               OR city LIKE '%Permit Review%'
               OR city LIKE '%Plan Review%'
               OR city LIKE '%Violation%'
               OR city LIKE '%Data Model%'
               OR city LIKE '%Relationship%'
               OR city LIKE '%MapServer%'
               OR city LIKE '%FeatureServer%'
               OR city LIKE '%Open Data%'
               OR city LIKE '%Dataset%'
               OR city IN ('Permits', 'Issued', 'Development', 'Trades', 'General',
                           'Plumbing', 'Electrical', 'Mechanical', 'Roofing', 'Sign',
                           'Fence', 'Fire', 'Demolition', 'Gas', 'Building')
            GROUP BY city
        """).fetchall()
        for row in garbage_rows:
            result = conn.execute("DELETE FROM permits WHERE city = ?", (row['city'],))
            if result.rowcount > 0:
                print(f"[V35] Deleted {result.rowcount} garbage permits with city='{row['city']}'")
                total_fixed += result.rowcount

        conn.commit()
        print(f"[V34] Data cleanup complete: {total_fixed} total records fixed/deleted")
        return total_fixed

    except Exception as e:
        print(f"[V34] Data cleanup error: {e}")
        import traceback
        traceback.print_exc()
        try:
            conn.rollback()
        except Exception:
            pass
        return 0


def _migrate_canonical_city_names(conn):
    """V85: One-time migration to canonicalize existing permit city names.

    This updates permits with known variant names to their canonical form,
    enabling proper matching to prod_cities.
    """
    try:
        # Check if we've already run this migration
        check = conn.execute("""
            SELECT COUNT(*) as cnt FROM permits WHERE city = 'New York City'
        """).fetchone()

        if check['cnt'] == 0:
            print("[V85] City name migration: no 'New York City' variants found, may already be migrated")
            return

        print(f"[V85] Migrating city names - found {check['cnt']} 'New York City' variants")

        # Get all distinct city/state combos that need canonicalization
        distinct_cities = conn.execute("""
            SELECT DISTINCT city, state FROM permits
            WHERE city IS NOT NULL AND city != ''
        """).fetchall()

        updates = []
        for row in distinct_cities:
            canonical, is_garbage = canonicalize_city_name(row['city'], row['state'] or '')
            if is_garbage:
                # Delete garbage permits
                result = conn.execute(
                    "DELETE FROM permits WHERE city = ? AND (state = ? OR (state IS NULL AND ? IS NULL))",
                    (row['city'], row['state'], row['state'])
                )
                if result.rowcount > 0:
                    print(f"[V85] Deleted {result.rowcount} garbage permits: '{row['city']}'")
            elif canonical != row['city']:
                updates.append((canonical, row['city'], row['state']))

        # Batch update each variant
        total_updated = 0
        for canonical, old_city, state in updates:
            if state:
                result = conn.execute(
                    "UPDATE permits SET city = ? WHERE city = ? AND state = ?",
                    (canonical, old_city, state)
                )
            else:
                result = conn.execute(
                    "UPDATE permits SET city = ? WHERE city = ? AND state IS NULL",
                    (canonical, old_city)
                )
            if result.rowcount > 0:
                print(f"[V85] Canonicalized {result.rowcount}: '{old_city}' → '{canonical}'")
                total_updated += result.rowcount

        conn.commit()
        print(f"[V85] City name migration complete: {total_updated} permits updated")

    except Exception as e:
        print(f"[V85] City name migration error: {e}")
        import traceback
        traceback.print_exc()
        try:
            conn.rollback()
        except Exception:
            pass


def _sync_prod_city_counts(conn):
    """V86: Sync prod_cities.total_permits using FK relationship.

    This uses the prod_city_id foreign key on permits for accurate counting.
    Much simpler and more reliable than name-based matching.
    """
    try:
        # V86: Check if prod_city_id column exists (indicates V86 migration ran)
        try:
            conn.execute("SELECT prod_city_id FROM permits LIMIT 1")
            has_fk = True
        except Exception:
            has_fk = False

        if has_fk:
            # V86: Use FK-based counting - simple and reliable
            result = conn.execute("""
                UPDATE prod_cities SET total_permits = (
                    SELECT COUNT(*) FROM permits WHERE permits.prod_city_id = prod_cities.id
                )
            """)
            conn.commit()

            # Get stats
            with_data = conn.execute("SELECT COUNT(*) FROM prod_cities WHERE total_permits > 0").fetchone()[0]
            total_counted = conn.execute("SELECT SUM(total_permits) FROM prod_cities").fetchone()[0] or 0
            total_permits = conn.execute("SELECT COUNT(*) FROM permits").fetchone()[0]
            linked_permits = conn.execute("SELECT COUNT(*) FROM permits WHERE prod_city_id IS NOT NULL").fetchone()[0]

            print(f"[V86] Synced prod_cities: {with_data} cities have data")
            print(f"[V86] Permits: {linked_permits:,}/{total_permits:,} linked ({total_counted:,} counted)")
        else:
            # Fallback to old name-based matching for backwards compatibility
            # V85: Get actual counts per city from permits table, then canonicalize
            actual = conn.execute("""
                SELECT city, state, COUNT(*) as cnt
                FROM permits
                WHERE city IS NOT NULL AND city != ''
                GROUP BY city, state
            """).fetchall()

            # V85: Build count map using canonical names (merge variants)
            count_map = {}  # canonical_name_lower -> count
            for r in actual:
                canonical, is_garbage = canonicalize_city_name(r['city'], r['state'] or '')
                if is_garbage:
                    continue
                key = canonical.lower()
                count_map[key] = count_map.get(key, 0) + r['cnt']

            # Update each prod_city using canonical name matching
            prod = conn.execute("SELECT id, city, state FROM prod_cities").fetchall()
            updated = 0
            for row in prod:
                # V85: Canonicalize prod_city name for matching
                canonical_prod, _ = canonicalize_city_name(row['city'], row['state'] or '')
                actual_count = count_map.get(canonical_prod.lower(), 0)
                conn.execute(
                    "UPDATE prod_cities SET total_permits = ? WHERE id = ?",
                    (actual_count, row['id'])
                )
                updated += 1

            conn.commit()
            nonzero = sum(1 for r in prod if count_map.get(canonicalize_city_name(r['city'], r['state'] or '')[0].lower(), 0) > 0)
            print(f"[V34] Synced {updated} prod_cities permit counts ({nonzero} have data)")

        # V35: Auto-reactivate paused cities that now have data
        reactivated = conn.execute("""
            UPDATE prod_cities SET status = 'active'
            WHERE status = 'paused' AND total_permits > 0
        """)
        if reactivated.rowcount > 0:
            print(f"[V35] Reactivated {reactivated.rowcount} paused cities that have data")
        conn.commit()
    except Exception as e:
        print(f"[V34] Sync error: {e}")


# ---------------------------------------------------------------------------
# Permit CRUD — replaces load_permits(), atomic_write_json(permits.json), etc.
# ---------------------------------------------------------------------------

def upsert_permits(permits, source_city_key=None):
    """
    Insert or update permits. This is the core write operation that replaces
    all the JSON file writes. Uses INSERT OR REPLACE so duplicates by
    permit_number are automatically handled (newer data wins).

    V19: Also deduplicates by address+city+state+filing_date to prevent
    duplicate rows for the same physical permit with different permit numbers.

    Args:
        permits: list of permit dicts (same format as the old JSON)
        source_city_key: which city config collected these (for tracking)

    Returns:
        (new_count, updated_count)
    """
    # V19: Apply neighborhood-to-city mapping (e.g., "Vista Park, FL" -> "Orlando, FL")
    for p in permits:
        city = p.get('city', '').strip() if p.get('city') else ''
        state = p.get('state', '').strip() if p.get('state') else ''
        if city and state:
            mapped_city = NEIGHBORHOOD_TO_CITY.get((city, state))
            if mapped_city:
                p['city'] = mapped_city

    # V30: Source-level state normalization — always use the state from prod_cities
    # as the authoritative source. This fixes issues like Atlanta showing as "TX"
    # or "OK" when ArcGIS sources don't return state data, or when bulk datasets
    # have wrong state mappings. prod_cities is the single source of truth.
    try:
        conn_tmp = get_connection()
        prod_rows = conn_tmp.execute(
            "SELECT LOWER(city) as city_lower, state FROM prod_cities WHERE state IS NOT NULL AND state != ''"
        ).fetchall()
        _city_to_state = {r['city_lower']: r['state'] for r in prod_rows}
        for p in permits:
            city = (p.get('city') or '').strip().lower()
            if city and city in _city_to_state:
                p['state'] = _city_to_state[city]
    except Exception as e:
        print(f"[V30] State normalization warning: {e}")

    # V85: Canonicalize city names to ensure consistent matching
    canonicalized = 0
    filtered_garbage = 0
    pre_canon_permits = permits
    permits = []
    for p in pre_canon_permits:
        city = p.get('city', '').strip() if p.get('city') else ''
        state = p.get('state', '').strip() if p.get('state') else ''
        if city:
            canonical, is_garbage = canonicalize_city_name(city, state)
            if is_garbage:
                filtered_garbage += 1
                continue
            if canonical != city:
                p['city'] = canonical
                canonicalized += 1
        permits.append(p)

    if canonicalized > 0:
        print(f"[V85] Canonicalized {canonicalized} city names")
    if filtered_garbage > 0:
        print(f"[V85] Filtered {filtered_garbage} permits with garbage city names")

    # V86: Build prod_city_id lookup for FK assignment
    try:
        conn_tmp = get_connection()
        prod_rows = conn_tmp.execute(
            "SELECT id, city, state FROM prod_cities"
        ).fetchall()
        _city_to_prod_id = {}  # (canonical_lower, state_upper) -> prod_city_id
        for r in prod_rows:
            canonical, _ = canonicalize_city_name(r['city'], r['state'] or '')
            key = (canonical.lower(), (r['state'] or '').upper())
            _city_to_prod_id[key] = r['id']
    except Exception as e:
        print(f"[V86] Prod city lookup warning: {e}")
        _city_to_prod_id = {}

    # V86: Assign prod_city_id to each permit
    for p in permits:
        city = p.get('city', '').strip() if p.get('city') else ''
        state = p.get('state', '').strip() if p.get('state') else ''
        if city:
            canonical, _ = canonicalize_city_name(city, state)
            key = (canonical.lower(), state.upper())
            p['_prod_city_id'] = _city_to_prod_id.get(key)

    # V19: Pre-filter to check for existing permits by address+city+state+filing_date
    # This prevents duplicates even when permit_number differs
    conn = get_connection()
    existing_addr_combos = set()

    # Build list of (address, city, state, filing_date) to check
    addr_combos = []
    for p in permits:
        addr = p.get('address', '').strip() if p.get('address') else ''
        city = p.get('city', '').strip() if p.get('city') else ''
        state = p.get('state', '').strip() if p.get('state') else ''
        filing_date = p.get('filing_date', '').strip() if p.get('filing_date') else ''
        if addr and city and state and filing_date and addr.lower() != 'address not provided':
            addr_combos.append((addr, city, state, filing_date))

    # Check existing in batches
    for i in range(0, len(addr_combos), 100):
        batch = addr_combos[i:i+100]
        for addr, city, state, filing_date in batch:
            row = conn.execute("""
                SELECT 1 FROM permits
                WHERE address = ? AND city = ? AND state = ? AND filing_date = ?
                LIMIT 1
            """, (addr, city, state, filing_date)).fetchone()
            if row:
                existing_addr_combos.add((addr.lower(), city.lower(), state.lower(), filing_date))

    # Filter out permits that already exist by address combo
    filtered_permits = []
    skipped_dupes = 0
    for p in permits:
        addr = (p.get('address', '') or '').strip().lower()
        city = (p.get('city', '') or '').strip().lower()
        state = (p.get('state', '') or '').strip().lower()
        filing_date = (p.get('filing_date', '') or '').strip()

        if addr and city and state and filing_date and addr != 'address not provided':
            if (addr, city, state, filing_date) in existing_addr_combos:
                skipped_dupes += 1
                continue

        filtered_permits.append(p)

    if skipped_dupes > 0:
        print(f"[DB] Skipped {skipped_dupes} duplicate permits (same address+city+state+date)")

    permits = filtered_permits

    # V32: Filter out permits with garbage city names before insertion
    pre_filter_count = len(permits)
    permits = [p for p in permits if not is_garbage_city_name(p.get('city', ''))]
    garbage_filtered = pre_filter_count - len(permits)
    if garbage_filtered > 0:
        print(f"[V32] Filtered {garbage_filtered} permits with garbage city names")

    # V32: Filter out permits with invalid/empty state codes
    VALID_US_STATES = {
        'AL', 'AK', 'AZ', 'AR', 'CA', 'CO', 'CT', 'DE', 'DC', 'FL', 'GA',
        'HI', 'ID', 'IL', 'IN', 'IA', 'KS', 'KY', 'LA', 'ME', 'MD', 'MA',
        'MI', 'MN', 'MS', 'MO', 'MT', 'NE', 'NV', 'NH', 'NJ', 'NM', 'NY',
        'NC', 'ND', 'OH', 'OK', 'OR', 'PA', 'RI', 'SC', 'SD', 'TN', 'TX',
        'UT', 'VT', 'VA', 'WA', 'WV', 'WI', 'WY'
    }
    pre_state_count = len(permits)
    permits = [p for p in permits if (p.get('state', '') or '').upper() in VALID_US_STATES]
    state_filtered = pre_state_count - len(permits)
    if state_filtered > 0:
        print(f"[V32] Filtered {state_filtered} permits with invalid state codes")

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
                source_city_key, collected_at, updated_at, prod_city_id
            ) VALUES (
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?, ?
            )
        """, (
            pn, p.get('city'), p.get('state'), p.get('address'), p.get('zip'),
            p.get('permit_type'), p.get('permit_sub_type'), p.get('work_type'), p.get('trade_category'),
            p.get('description'), p.get('display_description'), p.get('estimated_cost', 0), p.get('value_tier'),
            p.get('status'), p.get('filing_date'), p.get('issued_date'), p.get('date'),
            p.get('contact_name'), p.get('contact_phone'), p.get('contact_email'), p.get('owner_name'),
            p.get('contractor_name'), p.get('square_feet'), p.get('lifecycle_label'),
            source_city_key or p.get('source_bulk') or p.get('source_city') or p.get('source_city_key'), now, now,
            p.get('_prod_city_id')
        ))

    conn.commit()
    print(f"[DB] Upserted permits: {new_count} new, {updated_count} updated")
    return new_count, updated_count


def query_permits(city=None, state=None, trade=None, value=None, status=None, quality=None,
                  search=None, page=1, per_page=50, order_by='filing_date DESC'):
    """
    Query permits with filters and pagination. Replaces the Python list
    comprehension filtering in /api/permits.

    V32: Added state parameter to prevent cross-state data pollution.

    Returns:
        (permits_list, total_count)
    """
    conn = get_connection()
    conditions = []
    params = []

    if city:
        conditions.append("city = ?")
        params.append(city)
    if state:
        conditions.append("state = ?")
        params.append(state)
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

    V31: city_count now reflects actively collected cities (prod_cities with
    status='active'), not every distinct city name in the permits table.
    The old COUNT(DISTINCT city) included historical bulk-source data.
    """
    conn = get_connection()
    row = conn.execute("""
        SELECT
            COUNT(*) as total_permits,
            COALESCE(SUM(estimated_cost), 0) as total_value,
            COUNT(CASE WHEN estimated_cost >= 100000 THEN 1 END) as high_value_count
        FROM permits
    """).fetchone()

    # V31: City count from actively pulled sources only
    active_city_count = get_prod_city_count() if prod_cities_table_exists() else 0
    # Fallback: if prod_cities not populated yet, use distinct cities from permits
    if active_city_count == 0:
        fallback = conn.execute("SELECT COUNT(DISTINCT city) as cnt FROM permits").fetchone()
        active_city_count = fallback['cnt'] if fallback else 0

    return {
        'total_permits': row['total_permits'],
        'total_value': row['total_value'],
        'high_value_count': row['high_value_count'],
        'city_count': active_city_count,
    }


def get_cities_with_permits():
    """Get list of cities that have permit data. Replaces get_cities_with_data().
    V70: Returns empty list if database unavailable."""
    try:
        conn = get_connection()
        cursor = conn.execute("""
            SELECT DISTINCT city, state, COUNT(*) as permit_count
            FROM permits
            GROUP BY city, state
            ORDER BY city
        """)
        return [dict(row) for row in cursor]
    except Exception:
        # V70: Any error → return empty list
        return []


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

    try:
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
    except Exception as e:
        print(f"[DB] V13.2: Date cleanup error (non-fatal): {e}")
        try:
            conn.rollback()
        except Exception:
            pass

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

def get_prod_cities(status='active', min_permits=1):
    """
    V15: Get all cities from prod_cities table.
    Returns list of dicts compatible with the old get_cities_with_data() format.

    Args:
        status: Filter by status ('active', 'paused', 'failed', 'pending', or None for all)
        min_permits: V18: Minimum permit count to include (default 1 to exclude empty cities)

    Returns:
        List of city dicts with keys: name, state, slug, permit_count, active
    """
    conn = get_connection()

    if status:
        cursor = conn.execute("""
            SELECT city, state, city_slug, total_permits, status, last_permit_date,
                   source_type, source_id, consecutive_failures, last_error
            FROM prod_cities
            WHERE status = ? AND total_permits >= ?
            ORDER BY total_permits DESC
        """, (status, min_permits))
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
    """V15/V34: Get count of active prod cities WITH actual permits.

    V34: Only counts active cities where total_permits > 0.
    The total_permits column is synced with actual DB counts
    during startup via _sync_prod_city_counts().
    V70: Returns 0 if Postgres unavailable.
    """
    try:
        conn = get_connection()
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM prod_cities WHERE status = 'active' AND total_permits > 0"
        ).fetchone()
        return row['cnt'] if row else 0
    except Exception:
        # V70: Pool not initialized → return 0
        return 0


def get_verified_city_count():
    """V34: Get count of all distinct cities with permits in DB,
    regardless of prod_cities status. Includes bulk-sourced cities."""
    conn = get_connection()
    row = conn.execute(
        "SELECT COUNT(DISTINCT LOWER(city)) as cnt FROM permits"
    ).fetchone()
    return row['cnt'] if row else 0


def audit_prod_cities():
    """V34: Comprehensive audit of prod_cities vs actual permit data.

    Returns a detailed report of every active prod_city with:
    - total_permits (synced on startup via _sync_prod_city_counts)
    - last permit date
    - status recommendation (keep active, pause, or investigate)

    V34b: Optimized — uses pre-synced total_permits from prod_cities
    instead of expensive GROUP BY on permits table. The counts are
    synced on every startup, so they're fresh enough for auditing.
    """
    conn = get_connection()

    # Get all active prod_cities — total_permits already synced on startup
    prod = conn.execute("""
        SELECT city, state, city_slug, total_permits, status,
               source_type, source_id, last_permit_date, newest_permit_date,
               consecutive_failures, last_error, last_collection
        FROM prod_cities WHERE status = 'active'
        ORDER BY city
    """).fetchall()

    results = {
        'verified_with_data': [],
        'active_no_data': [],
        'stale_data': [],
        'errored': [],
        'summary': {}
    }

    for row in prod:
        actual_count = row['total_permits'] or 0
        last_permit = row['newest_permit_date'] or row['last_permit_date'] or ''

        entry = {
            'city': row['city'],
            'state': row['state'],
            'slug': row['city_slug'],
            'total_permits': actual_count,
            'last_permit_date': last_permit,
            'last_collection': row['last_collection'] or '',
            'source_type': row['source_type'],
            'source_id': row['source_id'],
            'consecutive_failures': row['consecutive_failures'] or 0,
            'last_error': row['last_error'],
        }

        if row['consecutive_failures'] and row['consecutive_failures'] >= 3:
            entry['recommendation'] = 'pause_errors'
            results['errored'].append(entry)
        elif actual_count == 0:
            entry['recommendation'] = 'pause_no_data'
            results['active_no_data'].append(entry)
        elif last_permit and last_permit < '2025-01-01':
            entry['recommendation'] = 'investigate_stale'
            results['stale_data'].append(entry)
        else:
            entry['recommendation'] = 'keep_active'
            results['verified_with_data'].append(entry)

    # Quick count of distinct cities in permits (using indexed column)
    distinct = conn.execute("SELECT COUNT(DISTINCT city) as cnt FROM permits").fetchone()

    results['summary'] = {
        'total_active_prod_cities': len(prod),
        'verified_with_current_data': len(results['verified_with_data']),
        'active_but_no_permits': len(results['active_no_data']),
        'stale_data': len(results['stale_data']),
        'errored': len(results['errored']),
        'distinct_cities_in_permits_table': distinct['cnt'] if distinct else 0,
    }

    return results


def pause_cities_without_data():
    """V34: Pause all active prod_cities that have 0 actual permits in DB.
    Returns list of paused city slugs."""
    conn = get_connection()

    # Find active prod_cities with 0 permits
    cities_to_pause = conn.execute("""
        SELECT pc.city_slug, pc.city, pc.state
        FROM prod_cities pc
        LEFT JOIN (
            SELECT LOWER(city) as city_lower, state, COUNT(*) as cnt
            FROM permits GROUP BY LOWER(city), state
        ) p ON LOWER(pc.city) = p.city_lower AND (pc.state = p.state OR p.state IS NULL)
        WHERE pc.status = 'active' AND COALESCE(p.cnt, 0) = 0
    """).fetchall()

    paused = []
    for row in cities_to_pause:
        conn.execute("""
            UPDATE prod_cities SET
                status = 'paused',
                pause_reason = 'V34 audit: no permits in database',
                data_freshness = 'no_data'
            WHERE city_slug = ?
        """, (row['city_slug'],))
        paused.append({'slug': row['city_slug'], 'city': row['city'], 'state': row['state']})

    conn.commit()
    return paused


def is_prod_city(city_slug):
    """V15: Check if a city slug is in the prod_cities table."""
    conn = get_connection()
    row = conn.execute(
        "SELECT id, status FROM prod_cities WHERE city_slug = ?",
        (city_slug,)
    ).fetchone()
    return row is not None, row['status'] if row else None


def lookup_prod_city_by_slug(slug):
    """V32: Look up a city from prod_cities by slug, with fuzzy matching.
    Handles bulk source slug mismatch — e.g., URL slug 'lakewood' matching
    prod_cities slug 'lakewood-nj' from NJ bulk source.

    Returns (city_name, state, city_slug) or (None, None, None).
    """
    conn = get_connection()

    # Exact match first
    row = conn.execute(
        "SELECT city, state, city_slug FROM prod_cities WHERE city_slug = ? AND status = 'active'",
        (slug,)
    ).fetchone()
    if row:
        return row['city'], row['state'], row['city_slug']

    # Fuzzy match: slug is a prefix (e.g., 'lakewood' matches 'lakewood-nj')
    # Only match if exactly one state suffix follows
    row = conn.execute("""
        SELECT city, state, city_slug FROM prod_cities
        WHERE city_slug LIKE ? || '-__' AND status = 'active'
        ORDER BY total_permits DESC
        LIMIT 1
    """, (slug,)).fetchone()
    if row:
        return row['city'], row['state'], row['city_slug']

    return None, None, None


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
    """V15: Insert or update a prod city.
    V18: Normalizes city name and slug to prevent duplicates (Ft -> Fort, etc.)
    """
    # V18: Normalize city name and slug to prevent duplicates
    normalized_city = normalize_city_name(city)
    normalized_slug = normalize_city_slug(city)

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
    """, (normalized_city, state, normalized_slug, source_type, source_id, source_scope,
          status, added_by, notes))
    conn.commit()


def update_prod_city_collection(city_slug, permits_found=0, last_permit_date=None, error=None):
    """V15/V64: Update prod city after a collection run.

    V64: Also tracks consecutive_no_new and last_run_status for staleness detection.
    """
    conn = get_connection()

    if error:
        # Increment failure count, track error status
        conn.execute("""
            UPDATE prod_cities SET
                consecutive_failures = consecutive_failures + 1,
                last_error = ?,
                last_collection = datetime('now'),
                last_run_status = 'error'
            WHERE city_slug = ?
        """, (error, city_slug))

        # Check if should pause (3+ failures)
        row = conn.execute(
            "SELECT consecutive_failures FROM prod_cities WHERE city_slug = ?",
            (city_slug,)
        ).fetchone()
        if row and row['consecutive_failures'] >= 3:
            conn.execute(
                "UPDATE prod_cities SET status = 'paused', data_freshness = 'error' WHERE city_slug = ?",
                (city_slug,)
            )
    elif permits_found > 0:
        # Success with new permits - reset both failure counters, update freshness
        conn.execute("""
            UPDATE prod_cities SET
                consecutive_failures = 0,
                consecutive_no_new = 0,
                last_error = NULL,
                last_collection = datetime('now'),
                last_permit_date = COALESCE(?, last_permit_date),
                newest_permit_date = COALESCE(?, newest_permit_date),
                total_permits = total_permits + ?,
                last_run_status = 'success',
                data_freshness = 'fresh'
            WHERE city_slug = ?
        """, (last_permit_date, last_permit_date, permits_found, city_slug))

        # Reactivate if was paused
        conn.execute("""
            UPDATE prod_cities SET status = 'active'
            WHERE city_slug = ? AND status = 'paused'
        """, (city_slug,))
    else:
        # V64: No new permits (permits_found == 0) - increment no_new counter
        conn.execute("""
            UPDATE prod_cities SET
                consecutive_failures = 0,
                consecutive_no_new = COALESCE(consecutive_no_new, 0) + 1,
                last_error = NULL,
                last_collection = datetime('now'),
                last_run_status = 'no_new'
            WHERE city_slug = ?
        """, (city_slug,))

    conn.commit()


def update_prod_city_status(city_slug, status, notes=None):
    """V17: Update prod city status (pending, active, failed, paused)."""
    conn = get_connection()
    conn.execute("""
        UPDATE prod_cities SET
            status = ?,
            notes = COALESCE(?, notes),
            verified_date = datetime('now')
        WHERE city_slug = ?
    """, (status, notes, city_slug))
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
    """V15: Check if prod_cities table exists and has data.
    V70: Returns False if Postgres unavailable."""
    try:
        conn = get_connection()
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM prod_cities"
        ).fetchone()
        return row['cnt'] > 0
    except Exception:
        # V70: Any error (including pool not initialized) → return False
        return False


# ---------------------------------------------------------------------------
# V17: System state helpers for tracking daily tasks
# ---------------------------------------------------------------------------

def get_system_state(key):
    """V17: Get a system state value by key."""
    conn = get_connection()
    row = conn.execute(
        "SELECT value, updated_at FROM system_state WHERE key = ?",
        (key,)
    ).fetchone()
    return dict(row) if row else None


def set_system_state(key, value):
    """V17: Set a system state value."""
    conn = get_connection()
    conn.execute("""
        INSERT OR REPLACE INTO system_state (key, value, updated_at)
        VALUES (?, ?, datetime('now'))
    """, (key, value))
    conn.commit()


def should_run_daily(task_name):
    """V17: Check if a daily task should run (last run was >24 hours ago)."""
    state = get_system_state(f'last_{task_name}_run')
    if not state:
        return True

    last_run = state.get('updated_at')
    if not last_run:
        return True

    try:
        last_dt = datetime.fromisoformat(last_run.replace('Z', '+00:00'))
        hours_since = (datetime.now() - last_dt).total_seconds() / 3600
        return hours_since >= 24
    except (ValueError, TypeError):
        return True


def mark_daily_complete(task_name):
    """V17: Mark a daily task as complete (sets last run time to now)."""
    set_system_state(f'last_{task_name}_run', datetime.now().isoformat())


# ---------------------------------------------------------------------------
# V17: City activation log helpers
# ---------------------------------------------------------------------------

def log_city_activation(city_slug, city_name, state, source='discovery_engine',
                        initial_permits=0, notes=None):
    """V17: Log a city activation to the activation log."""
    conn = get_connection()
    conn.execute("""
        INSERT INTO city_activation_log
            (city_slug, city_name, state, source, initial_permits, notes)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (city_slug, city_name, state, source, initial_permits, notes))
    conn.commit()


def get_recent_activations(days=7):
    """V17: Get recently activated cities."""
    conn = get_connection()
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    cursor = conn.execute("""
        SELECT * FROM city_activation_log
        WHERE activated_at >= ?
        ORDER BY activated_at DESC
    """, (cutoff,))
    return [dict(row) for row in cursor]


def update_activation_seo_status(city_slug, seo_status):
    """V17: Update SEO status for an activated city."""
    conn = get_connection()
    conn.execute("""
        UPDATE city_activation_log
        SET seo_status = ?
        WHERE city_slug = ?
    """, (seo_status, city_slug))
    conn.commit()


# ---------------------------------------------------------------------------
# V17: Discovered sources helpers
# ---------------------------------------------------------------------------

def get_discovered_sources(status='active'):
    """V17: Get all discovered sources with given status."""
    conn = get_connection()
    cursor = conn.execute("""
        SELECT * FROM discovered_sources
        WHERE status = ?
        ORDER BY discovered_at DESC
    """, (status,))
    return [dict(row) for row in cursor]


def upsert_discovered_source(source_config):
    """V17: Insert or update a discovered source."""
    conn = get_connection()
    field_map_json = json.dumps(source_config.get('field_map', {})) if source_config.get('field_map') else None

    conn.execute("""
        INSERT OR REPLACE INTO discovered_sources
            (source_key, name, state, platform, mode, endpoint, dataset_id,
             city_field, date_field, field_map, scope, status, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        source_config.get('source_key'),
        source_config.get('name'),
        source_config.get('state', ''),
        source_config.get('platform', 'socrata'),
        source_config.get('mode', 'city'),
        source_config.get('endpoint'),
        source_config.get('dataset_id'),
        source_config.get('city_field'),
        source_config.get('date_field'),
        field_map_json,
        source_config.get('scope'),
        source_config.get('status', 'active'),
        source_config.get('notes'),
    ))
    conn.commit()


# ---------------------------------------------------------------------------
# V18: Staleness detection functions
# ---------------------------------------------------------------------------

# Configurable thresholds (days)
FRESHNESS_STALE_DAYS = 14
FRESHNESS_VERY_STALE_DAYS = 30


def get_stale_cities():
    """V18: Get all cities with stale data (>14 days since newest permit).

    Returns list of dicts with city info and staleness details.
    """
    conn = get_connection()
    cursor = conn.execute("""
        SELECT pc.city, pc.state, pc.city_slug, pc.total_permits,
               pc.source_type, pc.source_id, pc.data_freshness, pc.stale_since,
               MAX(p.filing_date) as newest_permit,
               CAST(julianday('now') - julianday(MAX(p.filing_date)) AS INTEGER) as days_stale
        FROM prod_cities pc
        LEFT JOIN permits p ON LOWER(p.city) = LOWER(pc.city)
                            AND LOWER(p.state) = LOWER(pc.state)
        WHERE pc.status = 'active'
        GROUP BY pc.city, pc.state
        HAVING days_stale > ? OR newest_permit IS NULL
        ORDER BY days_stale DESC
    """, (FRESHNESS_STALE_DAYS,))

    results = []
    for row in cursor:
        results.append({
            'city': row['city'],
            'state': row['state'],
            'city_slug': row['city_slug'],
            'total_permits': row['total_permits'] or 0,
            'source_type': row['source_type'],
            'source_id': row['source_id'],
            'current_freshness': row['data_freshness'],
            'stale_since': row['stale_since'],
            'newest_permit': row['newest_permit'],
            'days_stale': row['days_stale'] if row['newest_permit'] else None,
        })
    return results


def update_city_freshness(city_slug, freshness, newest_permit_date=None, stale_since=None):
    """V18: Update freshness status for a city."""
    conn = get_connection()
    if stale_since:
        conn.execute("""
            UPDATE prod_cities
            SET data_freshness = ?,
                newest_permit_date = ?,
                stale_since = ?
            WHERE city_slug = ?
        """, (freshness, newest_permit_date, stale_since, city_slug))
    else:
        conn.execute("""
            UPDATE prod_cities
            SET data_freshness = ?,
                newest_permit_date = ?
            WHERE city_slug = ?
        """, (freshness, newest_permit_date, city_slug))
    conn.commit()


def pause_city_stale(city_slug, reason='stale_data'):
    """V18: Pause a city due to stale data."""
    conn = get_connection()
    conn.execute("""
        UPDATE prod_cities
        SET status = 'paused',
            pause_reason = ?,
            data_freshness = 'very_stale'
        WHERE city_slug = ?
    """, (reason, city_slug))
    conn.commit()


def reactivate_city(city_slug):
    """V18: Reactivate a paused city when fresh data is found."""
    conn = get_connection()
    conn.execute("""
        UPDATE prod_cities
        SET status = 'active',
            pause_reason = NULL,
            data_freshness = 'fresh',
            stale_since = NULL
        WHERE city_slug = ?
    """, (city_slug,))
    conn.commit()


def add_to_review_queue(city, state, original_source, last_permit_date, stale_since, search_result=None):
    """V18: Add a stale city to the manual review queue."""
    conn = get_connection()
    conn.execute("""
        INSERT INTO stale_cities_review
            (city, state, original_source, last_permit_date, stale_since,
             auto_search_attempted, auto_search_result, status)
        VALUES (?, ?, ?, ?, ?, 1, ?, 'needs_review')
        ON CONFLICT(city, state) DO UPDATE SET
            last_permit_date = excluded.last_permit_date,
            auto_search_attempted = 1,
            auto_search_result = excluded.auto_search_result,
            updated_at = datetime('now')
    """, (city, state, original_source, last_permit_date, stale_since, search_result))
    conn.commit()


def get_review_queue(status=None):
    """V18: Get the manual review queue."""
    conn = get_connection()
    if status:
        cursor = conn.execute("""
            SELECT * FROM stale_cities_review
            WHERE status = ?
            ORDER BY stale_since DESC
        """, (status,))
    else:
        cursor = conn.execute("""
            SELECT * FROM stale_cities_review
            ORDER BY stale_since DESC
        """)
    return [dict(row) for row in cursor]


def get_freshness_summary():
    """V18: Get summary of data freshness across all cities."""
    conn = get_connection()
    summary = {}

    # Count by freshness status
    for row in conn.execute("""
        SELECT data_freshness, COUNT(*) as cnt
        FROM prod_cities
        WHERE status = 'active'
        GROUP BY data_freshness
    """):
        summary[row['data_freshness'] or 'unknown'] = row['cnt']

    # Count paused due to stale data
    row = conn.execute("""
        SELECT COUNT(*) as cnt FROM prod_cities
        WHERE status = 'paused' AND pause_reason = 'stale_data'
    """).fetchone()
    summary['paused_stale'] = row['cnt'] if row else 0

    # Get oldest active city's newest permit
    row = conn.execute("""
        SELECT city, state, newest_permit_date
        FROM prod_cities
        WHERE status = 'active' AND newest_permit_date IS NOT NULL
        ORDER BY newest_permit_date ASC
        LIMIT 1
    """).fetchone()
    if row:
        summary['oldest_city'] = f"{row['city']}, {row['state']}: {row['newest_permit_date']}"

    return summary
