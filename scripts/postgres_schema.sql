-- V522 (V513 Step 3 STAGING): Postgres schema, hand-converted from SQLite via /tmp/build_pg_schema.py
-- Generated 2026-05-05. Each statement is idempotent (CREATE TABLE IF NOT EXISTS).
-- The cutover (USE_POSTGRES=true) is a SEPARATE operation — see scripts/migrate_to_postgres.py.

-- bulk_source_coverage
CREATE TABLE IF NOT EXISTS bulk_source_coverage (
            id BIGSERIAL PRIMARY KEY,
            bulk_source_id BIGINT NOT NULL,
            prod_city_id BIGINT NOT NULL,
            is_primary BIGINT DEFAULT 0,  -- 1 if this is the primary source for the city
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(bulk_source_id, prod_city_id)
        );

-- bulk_sources
CREATE TABLE IF NOT EXISTS bulk_sources (
            id BIGSERIAL PRIMARY KEY,
            source_key TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            scope_type TEXT NOT NULL CHECK (scope_type IN ('county', 'state', 'region', 'multi')),
            scope_name TEXT NOT NULL,  -- e.g., "Los Angeles County" or "California"
            state TEXT,
            platform TEXT NOT NULL,
            endpoint TEXT NOT NULL,
            dataset_id TEXT,
            field_map TEXT,
            date_field TEXT,
            city_field TEXT,  -- Field that identifies which city each permit belongs to
            limit_per_page BIGINT DEFAULT 2000,
            status TEXT DEFAULT 'active',
            consecutive_failures BIGINT DEFAULT 0,
            last_failure_reason TEXT,
            last_collected_at TEXT,
            total_permits_collected BIGINT DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

-- cities
CREATE TABLE IF NOT EXISTS cities (
            city_slug TEXT PRIMARY KEY,
            city TEXT NOT NULL,
            state TEXT NOT NULL,
            population BIGINT DEFAULT 0,
            platform TEXT,
            endpoint TEXT,
            dataset_id TEXT,
            date_field TEXT,
            field_map TEXT,
            scraper_config TEXT,
            status TEXT DEFAULT 'pending',
            last_collected_at TEXT,
            last_success_at TEXT,
            last_error TEXT,
            permits_total BIGINT DEFAULT 0,
            permits_7d BIGINT DEFAULT 0,
            last_run_permits_found BIGINT DEFAULT 0,
            last_run_permits_inserted BIGINT DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            notes TEXT
        );

-- city_activation_log
CREATE TABLE IF NOT EXISTS city_activation_log (
            id BIGSERIAL PRIMARY KEY,
            city_slug TEXT NOT NULL,
            city_name TEXT NOT NULL,
            state TEXT NOT NULL,
            activated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            source TEXT,
            initial_permits BIGINT DEFAULT 0,
            seo_status TEXT DEFAULT 'needs_content',
            notes TEXT
        );

-- city_research
CREATE TABLE IF NOT EXISTS city_research (
            id BIGSERIAL PRIMARY KEY,
            city TEXT NOT NULL,
            state TEXT NOT NULL,
            population BIGINT DEFAULT 0,
            status TEXT DEFAULT 'untested',
            portal_url TEXT,
            dataset_id TEXT,
            platform TEXT,
            date_field TEXT,
            address_field TEXT,
            notes TEXT,
            tested_at TEXT,
            onboarded_at TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

-- city_sources
CREATE TABLE IF NOT EXISTS city_sources (
            id BIGSERIAL PRIMARY KEY,
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
            limit_per_page BIGINT DEFAULT 2000,
            status TEXT DEFAULT 'active',
            discovery_score BIGINT DEFAULT 0,
            consecutive_failures BIGINT DEFAULT 0,
            last_failure_reason TEXT,
            last_collected_at TEXT,
            total_permits_collected BIGINT DEFAULT 0,
            covers_cities TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        , prod_city_id BIGINT);

-- collection_log
CREATE TABLE IF NOT EXISTS collection_log (
    id BIGSERIAL PRIMARY KEY,
    city_slug TEXT NOT NULL,
    collection_type TEXT DEFAULT 'permits',
    status TEXT NOT NULL,
    records_fetched BIGINT DEFAULT 0,
    records_inserted BIGINT DEFAULT 0,
    error_message TEXT,
    duration_seconds DOUBLE PRECISION,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
, api_url TEXT, query_params TEXT, api_rows_returned BIGINT, duplicate_rows_skipped BIGINT, newest_record_date TEXT, response_time_ms BIGINT, circuit_state TEXT DEFAULT 'closed');

-- collection_runs
CREATE TABLE IF NOT EXISTS collection_runs (
            id BIGSERIAL PRIMARY KEY,
            run_type TEXT NOT NULL,
            started_at TEXT DEFAULT CURRENT_TIMESTAMP,
            completed_at TEXT,
            cities_processed BIGINT DEFAULT 0,
            permits_collected BIGINT DEFAULT 0,
            permits_new BIGINT DEFAULT 0,
            permits_updated BIGINT DEFAULT 0,
            status TEXT DEFAULT 'running',
            error_message TEXT,
            details TEXT
        );

-- contractor_contacts
CREATE TABLE IF NOT EXISTS contractor_contacts (
            id BIGSERIAL PRIMARY KEY,
            contractor_name_normalized TEXT NOT NULL UNIQUE,
            display_name TEXT,
            phone TEXT,
            email TEXT,
            website TEXT,
            address TEXT,
            source TEXT,
            confidence TEXT,
            looked_up_at TEXT DEFAULT CURRENT_TIMESTAMP,
            last_error TEXT
        );

-- contractor_profiles
CREATE TABLE IF NOT EXISTS contractor_profiles (
            id BIGSERIAL PRIMARY KEY,
            contractor_name_raw TEXT NOT NULL,
            contractor_name_normalized TEXT NOT NULL,
            source_city_key TEXT,
            city TEXT,
            state TEXT,
            total_permits BIGINT DEFAULT 0,
            permits_90d BIGINT DEFAULT 0,
            permits_30d BIGINT DEFAULT 0,
            primary_trade TEXT,
            trade_breakdown TEXT,
            avg_project_value DOUBLE PRECISION,
            max_project_value DOUBLE PRECISION,
            total_project_value DOUBLE PRECISION,
            primary_area TEXT,
            first_permit_date TEXT,
            last_permit_date TEXT,
            is_active BIGINT DEFAULT 0,
            permit_frequency TEXT,
            phone TEXT,
            website TEXT,
            email TEXT,
            google_place_id TEXT,
            license_number TEXT,
            license_status TEXT,
            enrichment_status TEXT DEFAULT 'pending',
            enriched_at TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(contractor_name_normalized, source_city_key)
        );

-- digest_log
CREATE TABLE IF NOT EXISTS digest_log (
            id BIGSERIAL PRIMARY KEY,
            sent_at TEXT DEFAULT CURRENT_TIMESTAMP,
            recipient_email TEXT,
            permits_count BIGINT DEFAULT 0,
            cities_included TEXT,
            status TEXT,
            error_message TEXT
        );

-- discovered_sources
CREATE TABLE IF NOT EXISTS discovered_sources (
            id BIGSERIAL PRIMARY KEY,
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
            discovered_at TEXT DEFAULT CURRENT_TIMESTAMP,
            last_tested TEXT,
            status TEXT DEFAULT 'active',
            notes TEXT
        );

-- discovery_runs
CREATE TABLE IF NOT EXISTS discovery_runs (
            id BIGSERIAL PRIMARY KEY,
            run_type TEXT NOT NULL,
            started_at TEXT DEFAULT CURRENT_TIMESTAMP,
            completed_at TEXT,
            targets_searched BIGINT DEFAULT 0,
            sources_found BIGINT DEFAULT 0,
            permits_loaded BIGINT DEFAULT 0,
            cities_activated BIGINT DEFAULT 0,
            errors TEXT
        );

-- enrichment_log
CREATE TABLE IF NOT EXISTS enrichment_log (
            id BIGSERIAL PRIMARY KEY,
            contractor_profile_id BIGINT,
            source TEXT NOT NULL,
            status TEXT NOT NULL,
            details TEXT,
            cost DOUBLE PRECISION DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (contractor_profile_id)
        );

-- permit_history
CREATE TABLE IF NOT EXISTS permit_history (
            id BIGSERIAL PRIMARY KEY,
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
        );

-- permits
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
            collected_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        , prod_city_id BIGINT);

-- pipeline_progress
CREATE TABLE IF NOT EXISTS pipeline_progress (
            city_slug TEXT PRIMARY KEY,
            status TEXT,
            source_found TEXT,
            permits_inserted BIGINT DEFAULT 0,
            error_message TEXT,
            processed_at TEXT
        );

-- pipeline_runs
CREATE TABLE IF NOT EXISTS pipeline_runs (
            id BIGSERIAL PRIMARY KEY,
            started_at TEXT DEFAULT CURRENT_TIMESTAMP,
            completed_at TEXT,
            results_json TEXT,
            cities_processed BIGINT DEFAULT 0,
            cities_succeeded BIGINT DEFAULT 0
        );

-- prod_cities
CREATE TABLE IF NOT EXISTS prod_cities (
            id BIGSERIAL PRIMARY KEY,
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
            total_permits BIGINT DEFAULT 0,
            permits_last_30d BIGINT DEFAULT 0,
            avg_daily_permits DOUBLE PRECISION DEFAULT 0,
            status TEXT DEFAULT 'active' CHECK (status IN ('active', 'paused', 'failed', 'pending')),
            consecutive_failures BIGINT DEFAULT 0,
            last_error TEXT,
            added_by TEXT,
            added_at TEXT DEFAULT CURRENT_TIMESTAMP,
            notes TEXT, data_freshness TEXT DEFAULT 'fresh', newest_permit_date TEXT, stale_since TEXT, pause_reason TEXT, consecutive_no_new BIGINT DEFAULT 0, last_run_status TEXT, population BIGINT DEFAULT 0, health_status TEXT DEFAULT 'unknown', first_successful_collection TEXT, last_successful_collection TEXT, last_failure_reason TEXT, earliest_permit_date TEXT, latest_permit_date TEXT, days_since_new_data BIGINT, pipeline_checked_at TEXT, backfill_status TEXT DEFAULT 'pending', has_enrichment BIGINT DEFAULT 0, has_violations BIGINT DEFAULT 0, expected_freshness_days BIGINT DEFAULT 14, needs_attention BIGINT DEFAULT 0, attention_reason TEXT,
            UNIQUE(city, state)
        );

-- property_owners
CREATE TABLE IF NOT EXISTS property_owners (
            id BIGSERIAL PRIMARY KEY,
            address TEXT NOT NULL,
            city TEXT,
            state TEXT,
            zip TEXT,
            owner_name TEXT,
            owner_mailing_address TEXT,
            parcel_id TEXT,
            source TEXT,
            last_updated TEXT DEFAULT CURRENT_TIMESTAMP
        );

-- scraper_runs
CREATE TABLE IF NOT EXISTS scraper_runs (
            id BIGSERIAL PRIMARY KEY,
            source_name TEXT,
            city TEXT,
            state TEXT,
            city_slug TEXT,
            run_started_at TEXT DEFAULT CURRENT_TIMESTAMP,
            run_completed_at TEXT,
            duration_ms BIGINT,
            permits_found BIGINT DEFAULT 0,
            permits_inserted BIGINT DEFAULT 0,
            status TEXT CHECK (status IN ('success', 'error', 'no_new', 'timeout', 'skipped')),
            error_message TEXT,
            error_type TEXT,
            http_status BIGINT,
            response_size_bytes BIGINT,
            collection_type TEXT DEFAULT 'scheduled',
            triggered_by TEXT
        );

-- sources
CREATE TABLE IF NOT EXISTS sources (
            source_key TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            scope_type TEXT NOT NULL DEFAULT 'city',
            state TEXT,
            population BIGINT DEFAULT 0,
            platform TEXT NOT NULL,
            endpoint TEXT NOT NULL DEFAULT '',
            dataset_id TEXT,
            field_map TEXT DEFAULT '{}',
            date_field TEXT DEFAULT 'date',
            city_field TEXT,
            limit_per_page BIGINT,
            status TEXT DEFAULT 'pending',
            pause_reason TEXT,
            last_attempt_at TEXT,
            last_attempt_status TEXT,
            last_attempt_error TEXT,
            last_attempt_duration_ms BIGINT,
            last_success_at TEXT,
            consecutive_failures BIGINT DEFAULT 0,
            last_permits_found BIGINT DEFAULT 0,
            last_permits_inserted BIGINT DEFAULT 0,
            total_permits BIGINT DEFAULT 0,
            newest_permit_date TEXT,
            covers_cities TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            notes TEXT
        , data_type TEXT NOT NULL DEFAULT 'permits', verified_at TEXT, last_verified_at TEXT, verification_status TEXT DEFAULT 'pending', days_consecutive BIGINT DEFAULT 0);

-- stale_cities_review
CREATE TABLE IF NOT EXISTS stale_cities_review (
            id BIGSERIAL PRIMARY KEY,
            city TEXT NOT NULL,
            state TEXT NOT NULL,
            original_source TEXT,
            last_permit_date TEXT,
            stale_since TEXT,
            auto_search_attempted BIGINT DEFAULT 0,
            auto_search_result TEXT,
            manual_notes TEXT,
            alternate_source_url TEXT,
            status TEXT DEFAULT 'needs_review' CHECK (status IN ('needs_review', 'in_progress', 'resolved', 'no_source')),
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(city, state)
        );

-- stripe_webhook_events
CREATE TABLE IF NOT EXISTS stripe_webhook_events (
            event_id TEXT PRIMARY KEY,
            event_type TEXT,
            processed_at TEXT DEFAULT CURRENT_TIMESTAMP
        , payload_json TEXT, handler_status TEXT, handler_error TEXT, customer_id TEXT, subscription_id TEXT);

-- subscribers
CREATE TABLE IF NOT EXISTS subscribers (
            id BIGSERIAL PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            name TEXT,
            plan TEXT DEFAULT 'free',
            digest_cities TEXT DEFAULT '[]',
            active BIGINT DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            last_digest_sent_at TEXT
        , stripe_customer_id TEXT, stripe_subscription_id TEXT, current_period_end TIMESTAMP, trial_end TIMESTAMP, cancellation_requested_at TIMESTAMP, cancelled_at TIMESTAMP);

-- sweep_sources
CREATE TABLE IF NOT EXISTS sweep_sources (
            id BIGSERIAL PRIMARY KEY,
            city_slug TEXT NOT NULL,
            source_type TEXT NOT NULL,
            source_url TEXT NOT NULL,
            platform TEXT NOT NULL,
            dataset_id TEXT DEFAULT '',
            domain TEXT DEFAULT '',
            name TEXT DEFAULT '',
            city_column TEXT DEFAULT '',
            city_value TEXT DEFAULT '',
            status TEXT DEFAULT 'pending_test',
            permits_found BIGINT DEFAULT 0,
            discovered_at TEXT DEFAULT '',
            tested_at TEXT DEFAULT '',
            UNIQUE(city_slug, dataset_id)
        );

-- system_state
CREATE TABLE IF NOT EXISTS system_state (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

-- us_cities
CREATE TABLE IF NOT EXISTS us_cities (
            id BIGSERIAL PRIMARY KEY,
            city_name TEXT NOT NULL,
            state TEXT NOT NULL,
            county TEXT,
            county_fips TEXT,
            population BIGINT DEFAULT 0,
            latitude DOUBLE PRECISION,
            longitude DOUBLE PRECISION,
            slug TEXT UNIQUE,
            status TEXT DEFAULT 'not_started',
            status_reason TEXT,
            covered_by_source TEXT,
            priority BIGINT DEFAULT 99999,
            last_searched_at TEXT,
            search_attempts BIGINT DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

-- us_counties
CREATE TABLE IF NOT EXISTS us_counties (
            id BIGSERIAL PRIMARY KEY,
            county_name TEXT NOT NULL,
            state TEXT NOT NULL,
            fips TEXT UNIQUE,
            population BIGINT DEFAULT 0,
            cities_in_county BIGINT DEFAULT 0,
            portal_domain TEXT,
            status TEXT DEFAULT 'not_started',
            status_reason TEXT,
            source_key TEXT,
            last_searched_at TEXT,
            priority BIGINT DEFAULT 99999,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

-- violations
CREATE TABLE IF NOT EXISTS violations (
            id BIGSERIAL PRIMARY KEY,
            prod_city_id BIGINT NOT NULL,
            city TEXT NOT NULL,
            state TEXT NOT NULL,
            source_violation_id TEXT UNIQUE,
            violation_date TEXT,
            violation_type TEXT,
            violation_description TEXT,
            status TEXT,
            address TEXT,
            zip TEXT,
            latitude DOUBLE PRECISION,
            longitude DOUBLE PRECISION,
            raw_data TEXT,
            collected_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

-- helpful indexes (subset; expand as needed)
CREATE INDEX IF NOT EXISTS idx_permits_source_city_key ON permits(source_city_key);
CREATE INDEX IF NOT EXISTS idx_permits_date ON permits(date);
CREATE INDEX IF NOT EXISTS idx_permits_prod_city_id ON permits(prod_city_id);
CREATE INDEX IF NOT EXISTS idx_violations_prod_city_id ON violations(prod_city_id);
CREATE INDEX IF NOT EXISTS idx_property_owners_source_city_key ON property_owners(source_city_key);
CREATE INDEX IF NOT EXISTS idx_contractor_profiles_source_city_key ON contractor_profiles(source_city_key);
CREATE INDEX IF NOT EXISTS idx_scraper_runs_run_started_at ON scraper_runs(run_started_at);
CREATE INDEX IF NOT EXISTS idx_scraper_runs_city_slug ON scraper_runs(city_slug);
CREATE INDEX IF NOT EXISTS idx_prod_cities_status ON prod_cities(status);
CREATE INDEX IF NOT EXISTS idx_prod_cities_city_slug ON prod_cities(city_slug);
CREATE INDEX IF NOT EXISTS idx_subscribers_email ON subscribers(email);
CREATE INDEX IF NOT EXISTS idx_subscribers_stripe_customer ON subscribers(stripe_customer_id);
CREATE INDEX IF NOT EXISTS idx_webhook_events_customer ON stripe_webhook_events(customer_id);
