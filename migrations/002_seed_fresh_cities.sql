-- ============================================================================
-- MIGRATION 002: Seed prod_cities with 17 known-fresh cities
-- PermitGrab Collector Redesign - Phase 1
-- Date: March 27, 2026
-- ============================================================================
--
-- These 17 cities have fresh data (0-2 days old as of March 27, 2026)
-- and are confirmed working sources.
--
-- Run after: 001_prod_cities_and_scraper_runs.sql
-- ============================================================================

-- San Marcos, TX (0 days stale) - freshest city
INSERT INTO prod_cities (city, state, city_slug, source_type, source_id, source_scope, status, added_by, notes)
VALUES ('San Marcos', 'TX', 'san-marcos-tx', 'socrata', 'san_marcos', 'city', 'active', 'migration', 'Fresh as of Mar 27, 2026 (0 days)');

-- Houston, TX (1 day stale)
INSERT INTO prod_cities (city, state, city_slug, source_type, source_id, source_scope, status, added_by, notes)
VALUES ('Houston', 'TX', 'houston-tx', 'socrata', 'houston', 'city', 'active', 'migration', 'Fresh as of Mar 26, 2026 (1 day)');

-- Nashville, TN (1 day stale)
INSERT INTO prod_cities (city, state, city_slug, source_type, source_id, source_scope, status, added_by, notes)
VALUES ('Nashville', 'TN', 'nashville-tn', 'socrata', 'nashville', 'city', 'active', 'migration', 'Fresh as of Mar 26, 2026 (1 day)');

-- Orlando, FL (1 day stale)
INSERT INTO prod_cities (city, state, city_slug, source_type, source_id, source_scope, status, added_by, notes)
VALUES ('Orlando', 'FL', 'orlando-fl', 'socrata', 'orlando', 'city', 'active', 'migration', 'Fresh as of Mar 26, 2026 (1 day)');

-- Buffalo, NY (1 day stale)
INSERT INTO prod_cities (city, state, city_slug, source_type, source_id, source_scope, status, added_by, notes)
VALUES ('Buffalo', 'NY', 'buffalo-ny', 'socrata', 'buffalo', 'city', 'active', 'migration', 'Fresh as of Mar 26, 2026 (1 day)');

-- Tyler, TX (1 day stale)
INSERT INTO prod_cities (city, state, city_slug, source_type, source_id, source_scope, status, added_by, notes)
VALUES ('Tyler', 'TX', 'tyler-tx', 'socrata', 'tyler', 'city', 'active', 'migration', 'Fresh as of Mar 26, 2026 (1 day)');

-- Utica, NY (1 day stale)
INSERT INTO prod_cities (city, state, city_slug, source_type, source_id, source_scope, status, added_by, notes)
VALUES ('Utica', 'NY', 'utica-ny', 'socrata', 'utica', 'city', 'active', 'migration', 'Fresh as of Mar 26, 2026 (1 day)');

-- New York, NY (2 days stale)
INSERT INTO prod_cities (city, state, city_slug, source_type, source_id, source_scope, status, added_by, notes)
VALUES ('New York City', 'NY', 'new-york-ny', 'socrata', 'new_york', 'city', 'active', 'migration', 'Fresh as of Mar 25, 2026 (2 days)');

-- San Antonio, TX (2 days stale)
INSERT INTO prod_cities (city, state, city_slug, source_type, source_id, source_scope, status, added_by, notes)
VALUES ('San Antonio', 'TX', 'san-antonio-tx', 'socrata', 'san_antonio', 'city', 'active', 'migration', 'Fresh as of Mar 25, 2026 (2 days)');

-- San Francisco, CA (2 days stale)
INSERT INTO prod_cities (city, state, city_slug, source_type, source_id, source_scope, status, added_by, notes)
VALUES ('San Francisco', 'CA', 'san-francisco-ca', 'socrata', 'san_francisco', 'city', 'active', 'migration', 'Fresh as of Mar 25, 2026 (2 days)');

-- Seattle, WA (2 days stale)
INSERT INTO prod_cities (city, state, city_slug, source_type, source_id, source_scope, status, added_by, notes)
VALUES ('Seattle', 'WA', 'seattle-wa', 'socrata', 'seattle', 'city', 'active', 'migration', 'Fresh as of Mar 25, 2026 (2 days)');

-- Lubbock, TX (2 days stale)
INSERT INTO prod_cities (city, state, city_slug, source_type, source_id, source_scope, status, added_by, notes)
VALUES ('Lubbock', 'TX', 'lubbock-tx', 'socrata', 'lubbock', 'city', 'active', 'migration', 'Fresh as of Mar 25, 2026 (2 days)');

-- Corpus Christi, TX (2 days stale)
INSERT INTO prod_cities (city, state, city_slug, source_type, source_id, source_scope, status, added_by, notes)
VALUES ('Corpus Christi', 'TX', 'corpus-christi-tx', 'socrata', 'corpus_christi', 'city', 'active', 'migration', 'Fresh as of Mar 25, 2026 (2 days)');

-- Fairfax, CA (2 days stale) - Note: This is Fairfax CA, not VA
-- The audit mentioned "Fairfax VA" but config shows Fairfax CA (Marin County)
INSERT INTO prod_cities (city, state, city_slug, source_type, source_id, source_scope, status, added_by, notes)
VALUES ('Fairfax', 'CA', 'fairfax-ca', 'socrata', 'fairfax', 'county', 'active', 'migration', 'Fresh as of Mar 25, 2026 (2 days). Via Marin County bulk source.');

-- Rockwall, TX (2 days stale)
INSERT INTO prod_cities (city, state, city_slug, source_type, source_id, source_scope, status, added_by, notes)
VALUES ('Rockwall', 'TX', 'rockwall-tx', 'socrata', 'rockwall', 'city', 'active', 'migration', 'Fresh as of Mar 25, 2026 (2 days)');

-- Bellevue, WA (2 days stale)
INSERT INTO prod_cities (city, state, city_slug, source_type, source_id, source_scope, status, added_by, notes)
VALUES ('Bellevue', 'WA', 'bellevue-wa', 'socrata', 'bellevue', 'city', 'active', 'migration', 'Fresh as of Mar 25, 2026 (2 days)');

-- Rolling Meadows, IL (2 days stale)
INSERT INTO prod_cities (city, state, city_slug, source_type, source_id, source_scope, status, added_by, notes)
VALUES ('Rolling Meadows', 'IL', 'rolling-meadows-il', 'socrata', 'rolling_meadows_il', 'county', 'active', 'migration', 'Fresh as of Mar 25, 2026 (2 days). Via Cook County bulk source.');


-- ============================================================================
-- VERIFICATION QUERY
-- After running this migration, verify with:
--   SELECT city, state, city_slug, status FROM prod_cities ORDER BY city;
-- Should return 17 rows, all with status='active'
-- ============================================================================
