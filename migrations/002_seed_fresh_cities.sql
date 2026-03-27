-- ============================================================================
-- MIGRATION 002: Seed prod_cities with verified working sources
-- PermitGrab Collector Redesign - Phase 1
-- Date: March 27, 2026 (Updated after endpoint verification)
-- ============================================================================
--
-- Only cities with VERIFIED WORKING endpoints as of March 27, 2026.
-- Removed: Houston (no endpoint), Tyler, Utica, San Antonio, Lubbock,
--          Corpus Christi (all connection failures)
--
-- Run after: 001_prod_cities_and_scraper_runs.sql
-- ============================================================================

-- San Marcos, TX - ArcGIS endpoint verified working
INSERT INTO prod_cities (city, state, city_slug, source_type, source_id, source_scope, status, added_by, notes)
VALUES ('San Marcos', 'TX', 'san-marcos-tx', 'arcgis', 'san_marcos', 'city', 'active', 'migration', 'Endpoint verified Mar 27, 2026');

-- Nashville, TN - ArcGIS endpoint verified working
INSERT INTO prod_cities (city, state, city_slug, source_type, source_id, source_scope, status, added_by, notes)
VALUES ('Nashville', 'TN', 'nashville-tn', 'arcgis', 'nashville', 'city', 'active', 'migration', 'Endpoint verified Mar 27, 2026');

-- Orlando, FL - Socrata endpoint verified working
INSERT INTO prod_cities (city, state, city_slug, source_type, source_id, source_scope, status, added_by, notes)
VALUES ('Orlando', 'FL', 'orlando-fl', 'socrata', 'orlando', 'city', 'active', 'migration', 'Endpoint verified Mar 27, 2026');

-- Buffalo, NY - Socrata endpoint verified working
INSERT INTO prod_cities (city, state, city_slug, source_type, source_id, source_scope, status, added_by, notes)
VALUES ('Buffalo', 'NY', 'buffalo-ny', 'socrata', 'buffalo', 'city', 'active', 'migration', 'Endpoint verified Mar 27, 2026');

-- New York City, NY - Socrata endpoint verified working
INSERT INTO prod_cities (city, state, city_slug, source_type, source_id, source_scope, status, added_by, notes)
VALUES ('New York City', 'NY', 'new-york-ny', 'socrata', 'new_york', 'city', 'active', 'migration', 'Endpoint verified Mar 27, 2026');

-- San Francisco, CA - Socrata endpoint verified working
INSERT INTO prod_cities (city, state, city_slug, source_type, source_id, source_scope, status, added_by, notes)
VALUES ('San Francisco', 'CA', 'san-francisco-ca', 'socrata', 'san_francisco', 'city', 'active', 'migration', 'Endpoint verified Mar 27, 2026');

-- Seattle, WA - Socrata endpoint verified working
INSERT INTO prod_cities (city, state, city_slug, source_type, source_id, source_scope, status, added_by, notes)
VALUES ('Seattle', 'WA', 'seattle-wa', 'socrata', 'seattle', 'city', 'active', 'migration', 'Endpoint verified Mar 27, 2026');

-- Fairfax, CA - Socrata via Marin County verified working
INSERT INTO prod_cities (city, state, city_slug, source_type, source_id, source_scope, status, added_by, notes)
VALUES ('Fairfax', 'CA', 'fairfax-ca', 'socrata', 'fairfax', 'county', 'active', 'migration', 'Via Marin County. Verified Mar 27, 2026');

-- Rockwall, TX - Socrata endpoint verified working
INSERT INTO prod_cities (city, state, city_slug, source_type, source_id, source_scope, status, added_by, notes)
VALUES ('Rockwall', 'TX', 'rockwall-tx', 'socrata', 'rockwall', 'city', 'active', 'migration', 'Endpoint verified Mar 27, 2026');

-- Bellevue, WA - ArcGIS endpoint verified working
INSERT INTO prod_cities (city, state, city_slug, source_type, source_id, source_scope, status, added_by, notes)
VALUES ('Bellevue', 'WA', 'bellevue-wa', 'arcgis', 'bellevue', 'city', 'active', 'migration', 'Endpoint verified Mar 27, 2026');

-- Rolling Meadows, IL - Socrata via Cook County verified working
INSERT INTO prod_cities (city, state, city_slug, source_type, source_id, source_scope, status, added_by, notes)
VALUES ('Rolling Meadows', 'IL', 'rolling-meadows-il', 'socrata', 'rolling_meadows_il', 'county', 'active', 'migration', 'Via Cook County. Verified Mar 27, 2026');

-- ============================================================================
-- BULK SOURCES - Cover multiple cities each
-- ============================================================================

-- Miami-Dade County, FL - ArcGIS endpoint (county-wide, no city breakdown)
INSERT INTO prod_cities (city, state, city_slug, source_type, source_id, source_scope, status, added_by, notes)
VALUES ('Miami-Dade County', 'FL', 'miami-dade-fl', 'arcgis', 'miami_dade_county', 'county', 'active', 'migration', 'County-wide permits. ArcGIS endpoint. ID field is numeric.');

-- Fort Worth, TX - ArcGIS endpoint (city-wide)
INSERT INTO prod_cities (city, state, city_slug, source_type, source_id, source_scope, status, added_by, notes)
VALUES ('Fort Worth', 'TX', 'fort-worth-tx', 'arcgis', 'fort_worth_tx_bulk', 'city', 'active', 'migration', 'City-wide permits. File_Date is epoch_ms. Pop ~978K');

-- Dallas, TX - Socrata bulk endpoint verified working
INSERT INTO prod_cities (city, state, city_slug, source_type, source_id, source_scope, status, added_by, notes)
VALUES ('Dallas', 'TX', 'dallas-tx', 'socrata', 'dallas_tx_bulk', 'city', 'active', 'migration', 'Bulk source verified Mar 27, 2026. Pop ~1.3M');

-- Mesa, AZ - Socrata bulk endpoint verified working
INSERT INTO prod_cities (city, state, city_slug, source_type, source_id, source_scope, status, added_by, notes)
VALUES ('Mesa', 'AZ', 'mesa-az', 'socrata', 'mesa_az_bulk', 'city', 'active', 'migration', 'Bulk source verified Mar 27, 2026. Pop ~500K');

-- Norfolk, VA - Socrata bulk endpoint verified working
INSERT INTO prod_cities (city, state, city_slug, source_type, source_id, source_scope, status, added_by, notes)
VALUES ('Norfolk', 'VA', 'norfolk-va', 'socrata', 'norfolk_va_bulk', 'city', 'active', 'migration', 'Bulk source verified Mar 27, 2026. Pop ~245K');

-- Corona, CA - Socrata bulk endpoint verified working
INSERT INTO prod_cities (city, state, city_slug, source_type, source_id, source_scope, status, added_by, notes)
VALUES ('Corona', 'CA', 'corona-ca', 'socrata', 'corona_ca_bulk', 'city', 'active', 'migration', 'Bulk source verified Mar 27, 2026. Pop ~157K');

-- Orlando, FL - Socrata bulk endpoint verified working (separate from individual orlando config)
INSERT INTO prod_cities (city, state, city_slug, source_type, source_id, source_scope, status, added_by, notes)
VALUES ('Orlando Metro', 'FL', 'orlando-metro-fl', 'socrata', 'orlando_fl_bulk', 'city', 'active', 'migration', 'Bulk source verified Mar 27, 2026. Metro area coverage');

-- ============================================================================
-- VERIFICATION QUERY
-- After running this migration, verify with:
--   SELECT city, state, city_slug, status FROM prod_cities ORDER BY city;
-- Should return 18 rows, all with status='active'
-- ============================================================================
