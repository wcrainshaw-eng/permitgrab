-- ============================================================================
-- MIGRATION 001: prod_cities and scraper_runs tables
-- PermitGrab Collector Redesign - Phase 1
-- Date: March 27, 2026
-- ============================================================================
--
-- This migration creates the two core tables for the new collector architecture:
--   1. prod_cities - Only verified cities with working data sources
--   2. scraper_runs - Per-city collection logging
--
-- Run with: sqlite3 data/permitgrab.db < migrations/001_prod_cities_and_scraper_runs.sql
-- ============================================================================

-- =============================================================================
-- TABLE: prod_cities
-- =============================================================================
-- Purpose: Replace heuristic-based city listing (KNOWN_OK_CITIES, VALID_STATES)
--          with a table of verified cities that have working data sources.
--
-- Rules:
--   - A city appears on the site ONLY if it's in prod_cities with status='active'
--   - The "X cities" count = SELECT COUNT(*) FROM prod_cities WHERE status='active'
--   - get_cities_with_data() queries this table, not heuristics
--   - /cities page pulls from this table
-- =============================================================================

CREATE TABLE IF NOT EXISTS prod_cities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    -- City identification
    city VARCHAR(100) NOT NULL,
    state VARCHAR(2) NOT NULL,
    city_slug VARCHAR(200) UNIQUE NOT NULL,

    -- Source information
    source_type VARCHAR(20),            -- socrata, arcgis, ckan, carto, custom
    source_id VARCHAR(200),             -- key from CITY_REGISTRY or BULK_SOURCES
    source_scope VARCHAR(20),           -- state, county, city (what level of source)
    source_endpoint TEXT,               -- The actual API endpoint URL

    -- Verification tracking
    verified_date TIMESTAMP,            -- When the source was last tested/verified
    last_collection TIMESTAMP,          -- When we last successfully collected permits
    last_permit_date DATE,              -- Date of most recent permit collected

    -- Data metrics
    total_permits INTEGER DEFAULT 0,    -- Total permits ever collected for this city
    permits_last_30d INTEGER DEFAULT 0, -- Permits collected in last 30 days
    avg_daily_permits REAL DEFAULT 0,   -- Average permits per day (rolling 30-day)

    -- Status
    status VARCHAR(20) DEFAULT 'active' CHECK (status IN ('active', 'paused', 'failed', 'pending')),
    -- active:  Working, collected within expected timeframe
    -- paused:  Failed 3+ times, being retried daily instead of 6-hourly
    -- failed:  Failed 30+ consecutive days, needs manual review
    -- pending: Discovered but not yet verified

    consecutive_failures INTEGER DEFAULT 0,  -- Count of consecutive failed collections
    last_error TEXT,                         -- Most recent error message

    -- Metadata
    added_by VARCHAR(50),               -- 'discovery_engine', 'manual', 'migration'
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    notes TEXT,

    -- Unique constraint on city+state
    UNIQUE(city, state)
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_prod_cities_status ON prod_cities(status);
CREATE INDEX IF NOT EXISTS idx_prod_cities_state ON prod_cities(state);
CREATE INDEX IF NOT EXISTS idx_prod_cities_slug ON prod_cities(city_slug);
CREATE INDEX IF NOT EXISTS idx_prod_cities_last_collection ON prod_cities(last_collection);
CREATE INDEX IF NOT EXISTS idx_prod_cities_source_id ON prod_cities(source_id);


-- =============================================================================
-- TABLE: scraper_runs
-- =============================================================================
-- Purpose: Log every collection run, per-city, with timing and results.
--          This enables the health dashboard and failure detection.
-- =============================================================================

CREATE TABLE IF NOT EXISTS scraper_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Run identification
    source_name VARCHAR(200),           -- Key from CITY_REGISTRY or BULK_SOURCES
    city VARCHAR(100),
    state VARCHAR(2),
    city_slug VARCHAR(200),

    -- Timing
    run_started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    run_completed_at TIMESTAMP,
    duration_ms INTEGER,                -- How long the collection took

    -- Results
    permits_found INTEGER DEFAULT 0,    -- New permits discovered
    permits_inserted INTEGER DEFAULT 0, -- Permits actually inserted (after dedup)

    -- Status
    status VARCHAR(20) CHECK (status IN ('success', 'error', 'no_new', 'timeout', 'skipped')),
    -- success:  Collection completed, permits found
    -- error:    Collection failed with an error
    -- no_new:   Collection succeeded but no new permits
    -- timeout:  Collection timed out
    -- skipped:  City was skipped (e.g., paused status)

    error_message TEXT,                 -- Full error message if status='error'
    error_type VARCHAR(50),             -- Categorized error: 'connection', 'auth', 'parse', 'timeout', etc.

    -- Response details (for debugging)
    http_status INTEGER,                -- HTTP response code
    response_size_bytes INTEGER,        -- Size of API response

    -- Context
    collection_type VARCHAR(20) DEFAULT 'scheduled',  -- 'scheduled', 'manual', 'backfill', 'retry'
    triggered_by VARCHAR(50)            -- 'cron', 'admin', 'discovery_engine'
);

-- Indexes for health dashboard queries
CREATE INDEX IF NOT EXISTS idx_scraper_runs_city_slug ON scraper_runs(city_slug);
CREATE INDEX IF NOT EXISTS idx_scraper_runs_source ON scraper_runs(source_name);
CREATE INDEX IF NOT EXISTS idx_scraper_runs_started ON scraper_runs(run_started_at);
CREATE INDEX IF NOT EXISTS idx_scraper_runs_status ON scraper_runs(status);
CREATE INDEX IF NOT EXISTS idx_scraper_runs_city_time ON scraper_runs(city_slug, run_started_at);


-- =============================================================================
-- VIEW: city_health_status
-- =============================================================================
-- Purpose: Quick health check for each city showing days since data
-- =============================================================================

CREATE VIEW IF NOT EXISTS city_health_status AS
SELECT
    pc.city,
    pc.state,
    pc.city_slug,
    pc.status,
    pc.last_collection,
    pc.last_permit_date,
    CAST((julianday('now') - julianday(pc.last_permit_date)) AS INTEGER) AS days_since_data,
    pc.avg_daily_permits,
    pc.consecutive_failures,
    pc.last_error,
    CASE
        WHEN pc.status = 'failed' THEN 'RED'
        WHEN pc.status = 'paused' THEN 'YELLOW'
        WHEN pc.last_permit_date IS NULL THEN 'RED'
        WHEN julianday('now') - julianday(pc.last_permit_date) <= 2 THEN 'GREEN'
        WHEN julianday('now') - julianday(pc.last_permit_date) <= 7 THEN 'YELLOW'
        ELSE 'RED'
    END AS health_color
FROM prod_cities pc
WHERE pc.status IN ('active', 'paused', 'failed');


-- =============================================================================
-- VIEW: daily_collection_summary
-- =============================================================================
-- Purpose: Summary of today's collection runs for monitoring
-- =============================================================================

CREATE VIEW IF NOT EXISTS daily_collection_summary AS
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
GROUP BY DATE(run_started_at)
ORDER BY run_date DESC;


-- =============================================================================
-- MIGRATION NOTES
-- =============================================================================
--
-- After running this migration:
--
-- 1. Seed prod_cities with the 17 known-fresh cities:
--    INSERT INTO prod_cities (city, state, city_slug, source_type, source_id, status, added_by)
--    VALUES ('San Marcos', 'TX', 'san-marcos-tx', 'socrata', 'san_marcos_tx', 'active', 'migration');
--    ... (repeat for all 17 fresh cities)
--
-- 2. Update collector.py to:
--    - Query prod_cities instead of CITY_REGISTRY for collection targets
--    - Log each run to scraper_runs
--    - Update prod_cities.last_collection after each successful run
--    - Update prod_cities.consecutive_failures and status on errors
--
-- 3. Update server.py to:
--    - Replace get_cities_with_data() with prod_cities query
--    - Replace city count heuristics with: SELECT COUNT(*) FROM prod_cities WHERE status='active'
--
-- 4. Add /admin/collector-health route using city_health_status view
--
