"""
PermitGrab — Production Server
Flask app that serves the dashboard and API endpoints
Deploy to any VPS (DigitalOcean, Railway, Render, etc.)
"""

from flask import Flask, jsonify, request, send_from_directory, render_template_string, session, render_template, Response, redirect, abort, g
from difflib import SequenceMatcher
import json
import math
import os
import sqlite3
import sys
import threading
import time

# V167: App-level constants
APP_VERSION = 'V171'
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', '')
START_TIME = time.time()
import secrets
import uuid
from datetime import datetime, timedelta
import stripe
from werkzeug.security import generate_password_hash, check_password_hash
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from city_configs import get_all_cities_info, get_city_count, get_city_by_slug, CITY_REGISTRY, TRADE_CATEGORIES, format_city_name
from lifecycle import get_lifecycle_label
from trade_configs import TRADE_REGISTRY, get_trade, get_all_trades, get_trade_slugs
import analytics
import db as permitdb  # V12.50: SQLite database layer (renamed to avoid Flask-SQLAlchemy collision)

# V78: Global tracking for email digest daemon thread
DIGEST_STATUS = {
    'thread_started': None,
    'last_heartbeat': None,
    'last_digest_attempt': None,
    'last_digest_result': None,
    'last_digest_sent': 0,
    'last_digest_failed': 0,
    'thread_alive': False
}

# V14.1: TRADE_MAPPING - SQL LIKE patterns for matching permits to trades
# Used by city_trade_landing() to filter permits at database level
TRADE_MAPPING = {
    # V30: Broadened patterns — added hyphenated, compound, and ArcGIS-format variants
    'plumbing': ['%plumbing%', '%plumb%', '%pipe%', '%sewer%', '%water heater%', '%drain%', '%backflow%', '%water line%', '%gas line%', '%water service%', '%sewer line%', '%plbg%'],
    'electrical': ['%electrical%', '%electric%', '%wiring%', '%panel%', '%circuit%', '%generator%', '%outlet%', '%elec %', '% elec%', '%service upgrade%', '%meter%', '%transformer%', '%sub-panel%', '%subpanel%'],
    'hvac': ['%hvac%', '%heating%', '%air conditioning%', '%a/c%', '%furnace%', '%ductwork%', '%ventilation%', '%heat pump%', '%boiler%', '%mechanical%', '%mech %', '% mech%', '%mini split%', '%minisplit%', '%ac unit%', '%condenser%', '%air handler%', '%cooling%'],
    'roofing': ['%roofing%', '%roof%', '%reroof%', '%re-roof%', '%shingle%', '%gutter%', '%reroofing%', '%roof replacement%', '%roof repair%', '%residential-reroof%', '%commercial-reroof%', '%rooftop%', '%membrane%', '%flashing%', '%tpo%', '%epdm%'],
    'general-construction': ['%general%', '%alteration%', '%remodel%', '%renovation%', '%tenant improvement%', '%ti %', '% ti%', '%build out%', '%buildout%', '%commercial remodel%', '%residential remodel%', '%repair%', '%maintenance%'],
    'demolition': ['%demolition%', '%demo%', '%tear down%', '%abatement%', '%removal%', '%strip out%', '%gut%'],
    'fire-protection': ['%fire%', '%sprinkler%', '%fire alarm%', '%fire suppression%', '%fire protection%', '%hood suppression%', '%standpipe%'],
    'painting': ['%painting%', '%paint%', '%coating%', '%stucco%', '%exterior finish%'],
    'concrete': ['%concrete%', '%foundation%', '%slab%', '%footing%', '%masonry%', '%brick%', '%paving%', '%flatwork%', '%retaining wall%', '%block%', '%sidewalk%', '%driveway%', '%curb%'],
    'landscaping': ['%landscape%', '%landscaping%', '%irrigation%', '%fence%', '%deck%', '%patio%', '%pool%', '%pergola%', '%grading%', '%retaining%', '%hardscape%', '%sprinkler system%', '%gazebo%'],
    'solar': ['%solar%', '%photovoltaic%', '%pv %', '% pv%', '%pv system%', '%solar panel%', '%net meter%', '%battery storage%', '%solar electric%'],
    'new-construction': ['%new build%', '%new construction%', '%ground up%', '%new building%', '%new dwelling%', '%new home%', '%new single%', '%new multi%', '%new commercial%', '%new residential%', '%addition%', '%sfr%', '%single family%', '%new house%'],
    'interior-renovation': ['%interior%', '%interior renovation%', '%interior remodel%', '%fit out%', '%fitout%', '%tenant finish%', '%finish out%', '%interior alteration%', '%kitchen%', '%bathroom%', '%bath remodel%'],
    'windows-doors': ['%window%', '%door%', '%storefront%', '%glazing%', '%fenestration%', '%skylight%', '%sliding%', '%entry door%', '%garage door%'],
    'structural': ['%structural%', '%steel%', '%framing%', '%load bearing%', '%beam%', '%column%', '%truss%', '%joist%', '%header%', '%shear wall%', '%seismic%'],
    'addition': ['%addition%', '%add on%', '%extension%', '%expand%', '%enlarge%', '%bump out%', '%second story%', '%2nd story%', '%adu%', '%accessory dwelling%', '%guest house%'],
}

# V79: Blog posts data structure with pre-rendered HTML content
# V79: Blog posts data structure with pre-rendered HTML content.
# V229 B4: moved from inline BLOG_POSTS = [...] block (3,082 lines) to
# its own module. server.py is still too big, but shedding 18%% of it
# makes grep and diff readable.
from blog_content import BLOG_POSTS

# V79: Helper function to get blog posts by category
def get_blog_posts_by_category(category):
    return [p for p in BLOG_POSTS if p['category'] == category]

# V79: Helper function to get blog posts for a specific city
def get_blog_posts_for_city(city_link):
    return [p for p in BLOG_POSTS if p.get('city_link') == city_link]

# V79: Helper function to get related posts (same category, excluding current)
def get_related_posts(current_slug, limit=3):
    current = next((p for p in BLOG_POSTS if p['slug'] == current_slug), None)
    if not current:
        return []
    same_category = [p for p in BLOG_POSTS if p['category'] == current['category'] and p['slug'] != current_slug]
    if len(same_category) < limit:
        # Add posts from other categories
        other = [p for p in BLOG_POSTS if p['category'] != current['category'] and p['slug'] != current_slug]
        same_category.extend(other[:limit - len(same_category)])
    return same_category[:limit]

# V12.17: static_url_path='' serves static files from root (needed for GSC verification)
app = Flask(__name__, static_folder='static', static_url_path='', template_folder='templates')
# V229 E2: fall back to a process-random key when SECRET_KEY env var isn't
# set, but loudly warn about it on startup. Without a stable SECRET_KEY,
# every gunicorn restart rotates the key and invalidates all logged-in
# user sessions. Prod has this env var set; the warning is for dev + for
# anyone who forks the repo without carrying over env config.
_v229_fallback_key = os.environ.get('SECRET_KEY')
if not _v229_fallback_key:
    _v229_fallback_key = secrets.token_hex(32)
    print("[V229 E2] WARNING: SECRET_KEY env var is not set — using an "
          "ephemeral random key. User sessions will be invalidated on every "
          "restart. Set SECRET_KEY on Render to persist sessions.", flush=True)
app.secret_key = _v229_fallback_key

# V68: WSGI middleware to bypass ALL Flask processing for /api/health
# This ensures health checks ALWAYS return 200, even during pool exhaustion
class HealthCheckMiddleware:
    def __init__(self, wsgi_app):
        self.app = wsgi_app

    def __call__(self, environ, start_response):
        if environ.get('PATH_INFO') in ('/healthz',):  # V171: Only /healthz in WSGI. /api/health goes through Flask.
            import json
            status = '200 OK'
            response_headers = [('Content-Type', 'application/json')]
            start_response(status, response_headers)
            body = json.dumps({
                'status': 'ok',
                'version': APP_VERSION,
                'message': 'Health check bypasses Flask entirely'
            })
            return [body.encode('utf-8')]
        return self.app(environ, start_response)

# Apply the middleware
app.wsgi_app = HealthCheckMiddleware(app.wsgi_app)

# V69: SCORCHED EARTH — ALL background work DISABLED until server is stable
# The server can serve ALL web requests from SQLite alone.
_startup_done = False
_collectors_manually_started = False

def _try_discover_source(city_name, state, slug):
    """V120: Try to auto-discover a working permit data source for a city."""
    import requests as req

    # --- Socrata Discovery API ---
    try:
        r = req.get("https://api.us.socrata.com/api/catalog/v1",
                     params={'q': f'{city_name} building permits', 'limit': 10, 'only': 'datasets'},
                     timeout=15)
        if r.ok:
            for ds in r.json().get('results', []):
                resource = ds.get('resource', {})
                domain = ds.get('metadata', {}).get('domain', '')
                name = resource.get('name', '').lower()
                dataset_id = resource.get('id', '')
                if ('permit' in name or 'building' in name) and '.gov' in domain and dataset_id:
                    # REARCH-FIX: Domain must match the target city or state
                    domain_lower = domain.lower()
                    city_clean = city_name.lower().replace(' ', '').replace('.', '').replace("'", "")
                    state_lower = state.lower()
                    domain_ok = (city_clean in domain_lower or
                                 f".{state_lower}." in domain_lower or
                                 domain_lower.endswith(f".{state_lower}.gov"))
                    if not domain_ok:
                        print(f"REARCH-FIX: SKIP {domain} — doesn't match {city_name}, {state}", flush=True)
                        continue

                    endpoint = f"https://{domain}/resource/{dataset_id}.json"
                    test = req.get(f"{endpoint}?$limit=3", timeout=10)
                    if test.ok and len(test.json()) > 0:
                        columns = resource.get('columns_field_name', [])
                        date_field = next((c for c in columns if any(d in c.lower() for d in ['issued', 'date', 'applied'])), '')
                        print(f"REARCH-FIX: Discovered Socrata for {slug}: {domain}/{dataset_id}", flush=True)
                        return {'source_key': f"{slug}-socrata", 'platform': 'socrata',
                                'endpoint': endpoint, 'dataset_id': dataset_id, 'date_field': date_field,
                                'domain': domain}
    except Exception:
        pass

    # --- ArcGIS Hub ---
    try:
        r = req.get("https://hub.arcgis.com/api/v3/search",
                     params={'q': f'{city_name} {state} building permits', 'filter[type]': 'Feature Service'},
                     timeout=15)
        if r.ok:
            for item in r.json().get('data', [])[:5]:
                attrs = item.get('attributes', {})
                url = attrs.get('url', '')
                name = attrs.get('name', '').lower()
                if 'permit' in name and url:
                    test_url = f"{url}/0/query?where=1%3D1&outFields=*&resultRecordCount=3&f=json"
                    try:
                        test = req.get(test_url, timeout=15)
                        if test.ok and test.json().get('features'):
                            print(f"V120: Discovered ArcGIS for {slug}: {url}", flush=True)
                            return {'source_key': f"{slug}-arcgis", 'platform': 'arcgis', 'endpoint': url}
                    except Exception:
                        continue
    except Exception:
        pass

    # --- Common .gov domain patterns ---
    city_clean = city_name.lower().replace(' ', '').replace('.', '').replace("'", "")
    for domain in [f"data.{city_clean}.gov", f"data.cityof{city_clean}.org",
                   f"opendata.{city_clean}.gov", f"data.{city_clean}{state.lower()}.gov"]:
        try:
            r = req.get(f"https://{domain}/api/catalog/v1", params={'q': 'permits', 'limit': 5}, timeout=5)
            if r.ok:
                for ds in r.json().get('results', []):
                    resource = ds.get('resource', {})
                    ds_name = resource.get('name', '').lower()
                    ds_id = resource.get('id', '')
                    if ds_id and 'permit' in ds_name:
                        endpoint = f"https://{domain}/resource/{ds_id}.json"
                        test = req.get(f"{endpoint}?$limit=3", timeout=8)
                        if test.ok and len(test.json()) > 0:
                            columns = resource.get('columns_field_name', [])
                            date_field = next((c for c in columns if 'date' in c.lower()), '')
                            print(f"V120: Discovered portal {domain} for {slug}", flush=True)
                            return {'source_key': f"{slug}-socrata", 'platform': 'socrata',
                                    'endpoint': endpoint, 'dataset_id': ds_id, 'date_field': date_field,
                                    'domain': domain}
        except Exception:
            continue

    return None


def _cleanup_v108_pipeline_damage():
    """V110: One-time cleanup — uses system_state flag to run only ONCE."""
    try:
        conn = permitdb.get_connection()

        # Check if already done
        row = conn.execute("SELECT value FROM system_state WHERE key = 'v110_cleanup_done'").fetchone()
        if row:
            return  # Already cleaned up

        # Delete pipeline garbage permits
        deleted = conn.execute("""
            DELETE FROM permits WHERE permit_number LIKE 'PL-%' OR permit_number LIKE 'ARC-%'
        """).rowcount

        # Reset all bad cities (V108 originals + V109b NJ garbage)
        bad_slugs = [
            'oklahoma-city', 'milwaukee', 'kansas-city',
            'fullerton-ca', 'torrance-ca', 'warren',
            'jersey-city', 'paterson', 'elizabeth',
        ]
        for slug in bad_slugs:
            conn.execute("""
                UPDATE prod_cities SET total_permits = 0, source_id = NULL,
                source_type = NULL, health_status = 'never_worked', backfill_status = 'pending'
                WHERE city_slug = ? AND (total_permits = 0 OR source_id LIKE '%:%')
            """, (slug,))

        # Clear ALL pipeline_progress for a fresh V110 rerun
        conn.execute("DELETE FROM pipeline_progress")

        # Mark as done so this never runs again
        conn.execute("""
            INSERT OR REPLACE INTO system_state (key, value, updated_at)
            VALUES ('v110_cleanup_done', 'true', datetime('now'))
        """)
        conn.commit()

        if deleted:
            print(f"[V110] One-time cleanup: deleted {deleted} pipeline permits, reset {len(bad_slugs)} cities", flush=True)
        else:
            print(f"[V110] One-time cleanup: no pipeline permits found, reset {len(bad_slugs)} cities", flush=True)
    except Exception as e:
        print(f"[V110] Cleanup error (non-fatal): {e}", flush=True)

    # V111b: Clear stale pipeline_progress so search pipeline retries all cities
    # V113: Clear pipeline_progress for fresh run with state portal search
    try:
        conn = permitdb.get_connection()
        row = conn.execute("SELECT value FROM system_state WHERE key = 'v113_progress_cleared'").fetchone()
        if not row:
            conn.execute("DELETE FROM pipeline_progress")
            conn.execute("""
                INSERT OR REPLACE INTO system_state (key, value, updated_at)
                VALUES ('v113_progress_cleared', 'true', datetime('now'))
            """)
            conn.commit()
            print("[V113] Cleared pipeline_progress for state portal search run", flush=True)
    except Exception as e:
        print(f"[V111b] Progress clear error (non-fatal): {e}", flush=True)


_schema_initialized = False

@app.before_request
def _migrate_create_sources_table():
    """V145: Create sources + violations + enrichment tables. V164: Runs ONCE."""
    global _schema_initialized
    if _schema_initialized:
        return
    _schema_initialized = True
    conn = permitdb.get_connection()

    # Sources table (already exists from earlier V145 — just ensure indexes)
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS sources (
            source_key TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            scope_type TEXT NOT NULL DEFAULT 'city',
            state TEXT,
            population INTEGER DEFAULT 0,
            platform TEXT NOT NULL,
            endpoint TEXT NOT NULL DEFAULT '',
            dataset_id TEXT,
            field_map TEXT DEFAULT '{}',
            date_field TEXT DEFAULT 'date',
            city_field TEXT,
            limit_per_page INTEGER,
            status TEXT DEFAULT 'pending',
            pause_reason TEXT,
            last_attempt_at TEXT,
            last_attempt_status TEXT,
            last_attempt_error TEXT,
            last_attempt_duration_ms INTEGER,
            last_success_at TEXT,
            consecutive_failures INTEGER DEFAULT 0,
            last_permits_found INTEGER DEFAULT 0,
            last_permits_inserted INTEGER DEFAULT 0,
            total_permits INTEGER DEFAULT 0,
            newest_permit_date TEXT,
            covers_cities TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            notes TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_sources_status ON sources(status);
        CREATE INDEX IF NOT EXISTS idx_sources_platform ON sources(platform);
        CREATE INDEX IF NOT EXISTS idx_sources_last_attempt_status ON sources(last_attempt_status);
    ''')

    # Add data_type column if it doesn't exist
    try:
        conn.execute("ALTER TABLE sources ADD COLUMN data_type TEXT NOT NULL DEFAULT 'permits'")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sources_data_type ON sources(data_type)")
    except Exception:
        pass

    # Add verification columns
    for col_sql in [
        "ALTER TABLE sources ADD COLUMN verified_at TEXT",
        "ALTER TABLE sources ADD COLUMN last_verified_at TEXT",
        "ALTER TABLE sources ADD COLUMN verification_status TEXT DEFAULT 'pending'",
        "ALTER TABLE sources ADD COLUMN days_consecutive INTEGER DEFAULT 0",
        # V182 PR2: emblem flags for cities index + city-page badges
        "ALTER TABLE prod_cities ADD COLUMN has_enrichment INTEGER DEFAULT 0",
        "ALTER TABLE prod_cities ADD COLUMN has_violations INTEGER DEFAULT 0",
    ]:
        try:
            conn.execute(col_sql)
        except Exception:
            pass

    # Violations table (V162: Schema with prod_city_id FK)
    # Drop old schema if it lacks prod_city_id
    try:
        _vs = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='violations'").fetchone()
        if _vs and 'prod_city_id' not in ((_vs[0] if isinstance(_vs, tuple) else _vs['sql']) or ''):
            conn.execute("DROP TABLE IF EXISTS violations")
            conn.commit()
    except Exception:
        pass
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS violations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prod_city_id INTEGER NOT NULL,
            city TEXT NOT NULL,
            state TEXT NOT NULL,
            source_violation_id TEXT UNIQUE,
            violation_date TEXT,
            violation_type TEXT,
            violation_description TEXT,
            status TEXT,
            address TEXT,
            zip TEXT,
            latitude REAL,
            longitude REAL,
            raw_data TEXT,
            collected_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_violations_prod_city_id ON violations(prod_city_id);
        CREATE INDEX IF NOT EXISTS idx_violations_city_state ON violations(city, state);
        CREATE INDEX IF NOT EXISTS idx_violations_date ON violations(violation_date);
    ''')

    # V163: Drop dead tables
    # V182: Removed 'contractor_contacts' and 'enrichment_log' — now live (contractor intelligence).
    for dead_table in ['bulk_source_coverage',
                       'city_validations', 'pipeline_runs', 'pipeline_progress']:
        try:
            conn.execute(f"DROP TABLE IF EXISTS {dead_table}")
        except Exception:
            pass
    conn.commit()

    # V182: Contractor intelligence tables (profiles + enrichment log + contacts cache).
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS contractor_profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            contractor_name_raw TEXT NOT NULL,
            contractor_name_normalized TEXT NOT NULL,
            source_city_key TEXT,
            city TEXT,
            state TEXT,
            total_permits INTEGER DEFAULT 0,
            permits_90d INTEGER DEFAULT 0,
            permits_30d INTEGER DEFAULT 0,
            primary_trade TEXT,
            trade_breakdown TEXT,
            avg_project_value REAL,
            max_project_value REAL,
            total_project_value REAL,
            primary_area TEXT,
            first_permit_date TEXT,
            last_permit_date TEXT,
            is_active INTEGER DEFAULT 0,
            permit_frequency TEXT,
            phone TEXT,
            website TEXT,
            email TEXT,
            google_place_id TEXT,
            license_number TEXT,
            license_status TEXT,
            enrichment_status TEXT DEFAULT 'pending',
            enriched_at TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            UNIQUE(contractor_name_normalized, source_city_key)
        );
        CREATE INDEX IF NOT EXISTS idx_cp_normalized ON contractor_profiles(contractor_name_normalized);
        CREATE INDEX IF NOT EXISTS idx_cp_city_key ON contractor_profiles(source_city_key);
        CREATE INDEX IF NOT EXISTS idx_cp_active ON contractor_profiles(is_active);
        CREATE INDEX IF NOT EXISTS idx_cp_trade ON contractor_profiles(primary_trade);
        CREATE INDEX IF NOT EXISTS idx_cp_enrichment ON contractor_profiles(enrichment_status);

        CREATE TABLE IF NOT EXISTS enrichment_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            contractor_profile_id INTEGER,
            source TEXT NOT NULL,
            status TEXT NOT NULL,
            details TEXT,
            cost REAL DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (contractor_profile_id) REFERENCES contractor_profiles(id)
        );
        CREATE INDEX IF NOT EXISTS idx_el_profile ON enrichment_log(contractor_profile_id);
        CREATE INDEX IF NOT EXISTS idx_el_source ON enrichment_log(source);

        CREATE TABLE IF NOT EXISTS contractor_contacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            contractor_name_normalized TEXT NOT NULL UNIQUE,
            display_name TEXT,
            phone TEXT,
            email TEXT,
            website TEXT,
            address TEXT,
            source TEXT,
            confidence TEXT,
            looked_up_at TEXT DEFAULT (datetime('now')),
            last_error TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_contractor_contacts_name ON contractor_contacts(contractor_name_normalized);
    ''')
    conn.commit()

    # V148: City research pipeline table
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS city_research (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL,
            state TEXT NOT NULL,
            population INTEGER DEFAULT 0,
            status TEXT DEFAULT 'untested',
            portal_url TEXT,
            dataset_id TEXT,
            platform TEXT,
            date_field TEXT,
            address_field TEXT,
            notes TEXT,
            tested_at TEXT,
            onboarded_at TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
    ''')

    # V149: Unique index for city_research upsert
    try:
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_cr_city_state ON city_research(city, state)")
    except Exception:
        pass

    # V158: Digest logging and subscriber tables
    conn2 = permitdb.get_connection()
    conn2.executescript('''
        CREATE TABLE IF NOT EXISTS digest_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sent_at TEXT DEFAULT (datetime('now')),
            recipient_email TEXT,
            permits_count INTEGER DEFAULT 0,
            cities_included TEXT,
            status TEXT,
            error_message TEXT
        );
        CREATE TABLE IF NOT EXISTS subscribers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            name TEXT,
            plan TEXT DEFAULT 'free',
            digest_cities TEXT DEFAULT '[]',
            active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now')),
            last_digest_sent_at TEXT
        );
    ''')
    conn2.commit()

    # V158: Ensure default subscriber exists in DB
    try:
        existing = conn2.execute("SELECT id FROM subscribers WHERE email='wcrainshaw@gmail.com'").fetchone()
        if not existing:
            conn2.execute("""
                INSERT INTO subscribers (email, name, plan, digest_cities, active)
                VALUES ('wcrainshaw@gmail.com', 'Wes', 'pro', '["Atlanta"]', 1)
            """)
            conn2.commit()
            print(f"[{datetime.now()}] V158: Created default subscriber for wcrainshaw@gmail.com")
    except Exception as e:
        print(f"[{datetime.now()}] V158: Subscriber setup note: {e}")

    # V159: One-time fix — correct subscriber cities to Atlanta
    try:
        migrated = conn2.execute("SELECT value FROM system_state WHERE key='migration_v159_subscriber_fix'").fetchone()
        if not migrated:
            conn2.execute("""
                UPDATE subscribers SET digest_cities = '["Atlanta"]'
                WHERE email = 'wcrainshaw@gmail.com' AND digest_cities != '["Atlanta"]'
            """)
            conn2.execute("INSERT OR IGNORE INTO system_state (key, value) VALUES ('migration_v159_subscriber_fix', '1')")
            conn2.commit()
            print(f"[{datetime.now()}] V159: Fixed subscriber cities to Atlanta")
    except Exception as e:
        print(f"[{datetime.now()}] V159: Subscriber fix note: {e}")

    # V170 A1: Mark NO_SOURCE top-100 cities (one-time migration)
    try:
        m = conn2.execute("SELECT value FROM system_state WHERE key='migration_v170_a1'").fetchone()
        if not m:
            conn2.execute("""
                UPDATE prod_cities SET status='paused',
                    last_error='V170 A1: No viable public building permit API'
                WHERE source_type IS NULL AND status='active'
                AND city IN ('Jacksonville','Fresno','Long Beach','Tulsa',
                    'Corpus Christi','Laredo','Lubbock','Glendale',
                    'Garland','Irving','North Las Vegas')
            """)
            conn2.execute("INSERT OR IGNORE INTO system_state (key, value) VALUES ('migration_v170_a1', '1')")
            conn2.commit()
            print(f"[{datetime.now()}] V170 A1: Marked NO_SOURCE cities as paused")
    except Exception as e:
        print(f"[{datetime.now()}] V170 A1 migration note: {e}")

    # V170 A2: Freshness recalc REMOVED from startup — was blocking gunicorn for 10+ min.
    # Run freshness updates via /api/admin/execute or Render Shell instead.

    # V187: Onboard active CITY_REGISTRY cities missing from prod_cities.
    # The daemon only collects for cities with a prod_cities row. ~898 active
    # CITY_REGISTRY entries had no row — the daemon never tried to collect them.
    # Idempotent: INSERT OR IGNORE on the UNIQUE(city, state) constraint.
    try:
        m = conn2.execute("SELECT value FROM system_state WHERE key='migration_v187_onboard'").fetchone()
        if not m:
            from city_configs import CITY_REGISTRY
            existing = set(r[0] for r in conn2.execute("SELECT city_slug FROM prod_cities").fetchall())
            onboarded = 0
            for key, cfg in CITY_REGISTRY.items():
                if not cfg.get('active', False):
                    continue
                hyphen_slug = cfg.get('slug', key.replace('_', '-'))
                if hyphen_slug in existing:
                    continue
                city_name = cfg.get('name', key.replace('_', ' ').title())
                state = cfg.get('state', '')
                platform = cfg.get('platform', '')
                conn2.execute("""
                    INSERT OR IGNORE INTO prod_cities
                    (city, state, city_slug, source_id, source_type, status, added_by)
                    VALUES (?, ?, ?, ?, ?, 'active', 'V187')
                """, (city_name, state, hyphen_slug, key, platform))
                onboarded += 1
            conn2.execute("INSERT OR IGNORE INTO system_state (key, value) VALUES ('migration_v187_onboard', ?)", (str(onboarded),))
            conn2.commit()
            print(f"[{datetime.now()}] V187: Onboarded {onboarded} CITY_REGISTRY cities into prod_cities")
    except Exception as e:
        print(f"[{datetime.now()}] V187: Onboard migration error (non-fatal): {e}")

    # V190: Pause cities with no viable collection path (viewpoint/energov
    # platforms have no HTTP handler; zero-permit cities wasting daemon cycles).
    try:
        m = conn2.execute("SELECT value FROM system_state WHERE key='migration_v190_pause'").fetchone()
        if not m:
            paused = conn2.execute("""
                UPDATE prod_cities SET status = 'paused',
                    last_error = 'V190: no platform handler (viewpoint/energov)'
                WHERE source_type IN ('viewpoint', 'energov')
                  AND status = 'active'
                  AND id NOT IN (SELECT DISTINCT prod_city_id FROM permits WHERE prod_city_id IS NOT NULL)
            """).rowcount
            conn2.execute("INSERT OR IGNORE INTO system_state (key, value) VALUES ('migration_v190_pause', ?)", (str(paused),))
            conn2.commit()
            print(f"[{datetime.now()}] V190: Paused {paused} viewpoint/energov cities with no permits")
    except Exception as e:
        print(f"[{datetime.now()}] V190: Pause migration error (non-fatal): {e}")

    # V191b: Fix top-50 city gaps — one-time data corrections
    try:
        m = conn2.execute("SELECT value FROM system_state WHERE key='migration_v191b'").fetchone()
        if not m:
            fixes = 0
            # 1. Bakersfield: prod_cities says socrata but CITY_REGISTRY says accela (Kern County portal)
            fixes += conn2.execute("""
                UPDATE prod_cities SET source_type = 'accela',
                    source_id = 'bakersfield_ca'
                WHERE city_slug = 'bakersfield' AND source_type = 'socrata'
            """).rowcount

            # 2. Boston: unpause (has 8K+ permits from past collections)
            fixes += conn2.execute("""
                UPDATE prod_cities SET status = 'active'
                WHERE city_slug = 'boston' AND status = 'paused'
            """).rowcount

            # 3. Tulsa: activate (was pending, Socrata endpoint exists)
            fixes += conn2.execute("""
                UPDATE prod_cities SET status = 'active'
                WHERE city_slug IN ('tulsa-ok', 'tulsa') AND status IN ('pending', 'paused')
            """).rowcount

            # 4. Wichita: unpause (Accela, may work via V188 custom URL support)
            fixes += conn2.execute("""
                UPDATE prod_cities SET status = 'active'
                WHERE city_slug = 'wichita' AND status = 'paused'
            """).rowcount

            # 5. Albuquerque: mark stale endpoint (not pause — keeps the row findable)
            conn2.execute("""
                UPDATE prod_cities SET data_freshness = 'very_stale',
                    last_error = 'V191b: ArcGIS endpoint stale since April 2024'
                WHERE city_slug = 'albuquerque' AND status = 'active'
            """)

            conn2.execute("INSERT OR IGNORE INTO system_state (key, value) VALUES ('migration_v191b', ?)", (str(fixes),))
            conn2.commit()
            print(f"[{datetime.now()}] V191b: Fixed {fixes} top-50 city gaps (Bakersfield type, Boston/Tulsa/Wichita activate)")
    except Exception as e:
        print(f"[{datetime.now()}] V191b: Migration error (non-fatal): {e}")

    # V191c: Slug normalization for Baltimore + Mesa (data exists under
    # '-accela' suffix slugs, needs to be under canonical slugs).
    # Also fix San Jose CA/IL attribution.
    try:
        m = conn2.execute("SELECT value FROM system_state WHERE key='migration_v191c'").fetchone()
        if not m:
            fixes = 0
            # Baltimore: merge baltimore-md-accela → baltimore (canonical for us_cities lookup)
            fixes += conn2.execute("""
                UPDATE permits SET source_city_key = 'baltimore'
                WHERE source_city_key = 'baltimore-md-accela'
            """).rowcount
            # Mesa: merge mesa-az-accela → mesa (keep the Accela prod_cities row active)
            fixes += conn2.execute("""
                UPDATE permits SET source_city_key = 'mesa'
                WHERE source_city_key = 'mesa-az-accela'
            """).rowcount
            # Update prod_cities city_slug to match
            conn2.execute("UPDATE prod_cities SET city_slug = 'baltimore' WHERE city_slug = 'baltimore-md-accela'")
            conn2.execute("UPDATE prod_cities SET city_slug = 'mesa' WHERE city_slug = 'mesa-az-accela'")
            # San Jose: fix the IL attribution. prod_cities 'san-jose-il' has
            # San Jose CA data (5.4K permits). Fix the state.
            conn2.execute("""
                UPDATE prod_cities SET state = 'CA', city = 'San Jose'
                WHERE city_slug = 'san-jose-il' AND state = 'IL'
            """)
            conn2.execute("INSERT OR IGNORE INTO system_state (key, value) VALUES ('migration_v191c', ?)", (str(fixes),))
            conn2.commit()
            print(f"[{datetime.now()}] V191c: Slug normalization — {fixes} permits remapped (Baltimore+Mesa), San Jose state fixed")
    except Exception as e:
        print(f"[{datetime.now()}] V191c: Migration error (non-fatal): {e}")

    # V193: Fix top-50 city gaps — Boston reactivation + Tulsa config
    try:
        m = conn2.execute("SELECT value FROM system_state WHERE key='migration_v193'").fetchone()
        if not m:
            fixes = 0
            # Boston MA: reactivate (CKAN endpoint has 721K records, was paused)
            fixes += conn2.execute("""
                UPDATE prod_cities SET status = 'active',
                    last_error = NULL, data_freshness = 'fresh'
                WHERE city_slug = 'boston' AND status != 'active'
            """).rowcount

            # Tulsa: domain data.tulsaok.gov is dead (DNS failure).
            # Mark as documented dead end.
            conn2.execute("""
                UPDATE prod_cities SET status = 'paused',
                    last_error = 'V193: data.tulsaok.gov DNS dead — no alternative found'
                WHERE city_slug = 'tulsa-ok' AND status = 'active'
            """)

            # Kansas City: endpoint alive (data.kcmo.org/ntw8-aacc) but data
            # stale since May 2025. Keep active — source may resume.
            conn2.execute("""
                UPDATE prod_cities SET data_freshness = 'very_stale',
                    last_error = 'V193: data current through May 2025 only'
                WHERE city_slug = 'kansas-city' AND status = 'active'
            """)

            # Wichita: Accela empty, no alternative API found. Pause.
            conn2.execute("""
                UPDATE prod_cities SET status = 'paused',
                    last_error = 'V193: Accela portal empty, no alternative found'
                WHERE city_slug = 'wichita' AND status = 'active'
            """)

            # Bakersfield: Kern County Accela empty, no alternative. Pause.
            conn2.execute("""
                UPDATE prod_cities SET status = 'paused',
                    last_error = 'V193: KERNCO Accela portal empty, no alternative'
                WHERE city_slug = 'bakersfield' AND status = 'active'
            """)

            conn2.execute("INSERT OR IGNORE INTO system_state (key, value) VALUES ('migration_v193', ?)", (str(fixes),))
            conn2.commit()
            print(f"[{datetime.now()}] V193: Fixed {fixes} top-50 gaps (Boston activated, Tulsa/Wichita/Bakersfield paused)")
    except Exception as e:
        print(f"[{datetime.now()}] V193: Migration error (non-fatal): {e}")

    # V193b: Detroit slug normalization (same pattern as Baltimore/Mesa)
    # 1,163 permits under 'detroit-mi-accela' need remapping to 'detroit'
    try:
        m = conn2.execute("SELECT value FROM system_state WHERE key='migration_v193b'").fetchone()
        if not m:
            det = conn2.execute("""
                UPDATE permits SET source_city_key = 'detroit'
                WHERE source_city_key = 'detroit-mi-accela'
            """).rowcount
            conn2.execute("""
                UPDATE prod_cities SET city_slug = 'detroit'
                WHERE city_slug = 'detroit-mi-accela'
                AND NOT EXISTS (SELECT 1 FROM prod_cities WHERE city_slug = 'detroit')
            """)
            conn2.execute("INSERT OR IGNORE INTO system_state (key, value) VALUES ('migration_v193b', ?)", (str(det),))
            conn2.commit()
            print(f"[{datetime.now()}] V193b: Detroit slug normalization — {det} permits remapped")
    except Exception as e:
        print(f"[{datetime.now()}] V193b: Detroit migration error (non-fatal): {e}")

    # V194: Bulk slug normalization — remap underscore→hyphen and strip
    # -accela suffixes for ALL permits where a canonical prod_cities slug exists.
    # This is the same fix as Baltimore/Mesa/Detroit but automated for 400+ slugs.
    try:
        m = conn2.execute("SELECT value FROM system_state WHERE key='migration_v194_slugs'").fetchone()
        if not m:
            # Build the mapping: for each permit source_city_key, check if
            # a canonical prod_cities.city_slug exists via normalization
            import re as _re
            permit_slugs = conn2.execute("""
                SELECT source_city_key, COUNT(*) as cnt
                FROM permits
                WHERE source_city_key IS NOT NULL
                GROUP BY source_city_key HAVING cnt >= 10
            """).fetchall()
            prod_slugs = set(r[0] for r in conn2.execute("SELECT city_slug FROM prod_cities").fetchall())

            total_remapped = 0
            for row in permit_slugs:
                old = row[0]
                if old in prod_slugs:
                    continue  # already canonical
                # Try: strip -accela, then underscore→hyphen
                canonical = old
                if canonical.endswith('-accela'):
                    canonical = canonical[:-7]
                canonical = canonical.replace('_', '-')
                if canonical in prod_slugs and canonical != old:
                    n = conn2.execute(
                        "UPDATE permits SET source_city_key = ? WHERE source_city_key = ?",
                        (canonical, old)
                    ).rowcount
                    total_remapped += n

            conn2.execute("INSERT OR IGNORE INTO system_state (key, value) VALUES ('migration_v194_slugs', ?)", (str(total_remapped),))
            conn2.commit()
            print(f"[{datetime.now()}] V194: Slug normalization — {total_remapped:,} permits remapped across 400+ slugs")
    except Exception as e:
        print(f"[{datetime.now()}] V194: Slug migration error (non-fatal): {e}")

    # V195c: Sync source_type from CITY_REGISTRY for prod_cities with NULL type.
    # Many cities have active CITY_REGISTRY entries (with platform + endpoint)
    # but their prod_cities row has source_type=NULL — so the daemon skips them.
    # E.g., Lexington KY has ACCELA_CONFIGS + CITY_REGISTRY active entry but
    # prod_cities.source_type=NULL from the V88 population expansion.
    try:
        m = conn2.execute("SELECT value FROM system_state WHERE key='migration_v195c_sync'").fetchone()
        if not m:
            from city_configs import CITY_REGISTRY as _CR
            synced = 0
            null_rows = conn2.execute("""
                SELECT id, city_slug FROM prod_cities
                WHERE source_type IS NULL AND status IN ('active', 'pending')
            """).fetchall()
            for row in null_rows:
                slug = row[1]
                for v in [slug.replace('-', '_'), slug, slug.rsplit('-', 1)[0].replace('-', '_')]:
                    cfg = _CR.get(v)
                    if cfg and cfg.get('active') and cfg.get('platform'):
                        conn2.execute("""
                            UPDATE prod_cities SET source_type = ?, source_id = ?, status = 'active'
                            WHERE id = ?
                        """, (cfg['platform'], v, row[0]))
                        synced += 1
                        break
            conn2.execute("INSERT OR IGNORE INTO system_state (key, value) VALUES ('migration_v195c_sync', ?)", (str(synced),))
            conn2.commit()
            print(f"[{datetime.now()}] V195c: Synced source_type for {synced} prod_cities from CITY_REGISTRY")
    except Exception as e:
        print(f"[{datetime.now()}] V195c: Sync error (non-fatal): {e}")

    # V231 DC-1: merge Phoenix/PHOENIX duplicate prod_cities rows. Prod
    # has two entries for Phoenix, AZ — one with 12K permits, one with
    # 313 — and collectors keep writing to both. Pick the higher-permit
    # row as canonical, reassign the other's permits, delete the dupe.
    try:
        m = conn2.execute("SELECT value FROM system_state WHERE key='migration_v231_phoenix'").fetchone()
        if not m:
            rows = conn2.execute("""
                SELECT id, city, total_permits FROM prod_cities
                WHERE LOWER(city)='phoenix' AND state='AZ'
                ORDER BY COALESCE(total_permits, 0) DESC
            """).fetchall()
            if len(rows) > 1:
                keep_id = rows[0][0] if isinstance(rows[0], tuple) else rows[0]['id']
                keep_name = rows[0][1] if isinstance(rows[0], tuple) else rows[0]['city']
                for r in rows[1:]:
                    dupe_id = r[0] if isinstance(r, tuple) else r['id']
                    conn2.execute(
                        "UPDATE permits SET prod_city_id=?, city=? WHERE prod_city_id=?",
                        (keep_id, keep_name, dupe_id)
                    )
                    conn2.execute("DELETE FROM prod_cities WHERE id=?", (dupe_id,))
            # Also fix any permits with uppercase city = 'PHOENIX'
            conn2.execute(
                "UPDATE permits SET city='Phoenix' WHERE city='PHOENIX' AND state='AZ'"
            )
            conn2.execute("INSERT OR IGNORE INTO system_state (key, value) VALUES ('migration_v231_phoenix', ?)", (str(len(rows)),))
            conn2.commit()
            print(f"[{datetime.now()}] V231 DC-1: Phoenix merge — collapsed {max(0, len(rows)-1)} dupes")
    except Exception as e:
        print(f"[{datetime.now()}] V231 DC-1: Phoenix merge error (non-fatal): {e}")

    # V231 DC-2: dedupe prod_cities by (LOWER(city), state). Hundreds of
    # NJ towns have duplicates from old state-level ingestion. Keep the
    # row with the most permits in each group, reassign permits from the
    # losers, delete the loser rows. Legitimate same-name-different-state
    # cities (Portland OR vs Portland WI) are NOT touched since the GROUP
    # BY includes state.
    try:
        m = conn2.execute("SELECT value FROM system_state WHERE key='migration_v231_dedup'").fetchone()
        if not m:
            dup_groups = conn2.execute("""
                SELECT LOWER(city) as lc, state, COUNT(*) as cnt
                FROM prod_cities
                GROUP BY LOWER(city), state HAVING COUNT(*) > 1
            """).fetchall()
            merged = 0
            for g in dup_groups:
                lc = g[0] if isinstance(g, tuple) else g['lc']
                st = g[1] if isinstance(g, tuple) else g['state']
                rows = conn2.execute("""
                    SELECT id, total_permits FROM prod_cities
                    WHERE LOWER(city)=? AND state=?
                    ORDER BY COALESCE(total_permits, 0) DESC
                """, (lc, st)).fetchall()
                if len(rows) < 2:
                    continue
                keep_id = rows[0][0] if isinstance(rows[0], tuple) else rows[0]['id']
                for r in rows[1:]:
                    dupe_id = r[0] if isinstance(r, tuple) else r['id']
                    conn2.execute(
                        "UPDATE permits SET prod_city_id=? WHERE prod_city_id=?",
                        (keep_id, dupe_id)
                    )
                    conn2.execute("DELETE FROM prod_cities WHERE id=?", (dupe_id,))
                    merged += 1
            conn2.execute("INSERT OR IGNORE INTO system_state (key, value) VALUES ('migration_v231_dedup', ?)", (str(merged),))
            conn2.commit()
            print(f"[{datetime.now()}] V231 DC-2: Deduped prod_cities — collapsed {merged} duplicate rows")
    except Exception as e:
        print(f"[{datetime.now()}] V231 DC-2: Dedup error (non-fatal): {e}")

    # V231 DC-3: pause junk-data cities. Socrata/statewide ingestions
    # have polluted prod_cities with entries like Lindley NY (pop 1,760
    # holding 41K permits) where a tiny city attracted state-level
    # records. Pause these so they stop showing up in footers/landings
    # until the underlying ingestion is fixed.
    #
    # _v2 gate: the first pass matched `population < 5000` without
    # requiring population > 0, which paused counties (Miami-Dade,
    # Shelby, etc.) whose prod_cities row has population=0 because
    # population is only hydrated for incorporated cities. This pass
    # un-pauses any V231 victim with population <= 0 or >= 5000, then
    # re-runs the filter with the tightened condition.
    try:
        m = conn2.execute("SELECT value FROM system_state WHERE key='migration_v231_junk_pause_v2'").fetchone()
        if not m:
            # Step 1: undo the too-broad first pass
            conn2.execute("""
                UPDATE prod_cities
                SET status='active',
                    pause_reason=REPLACE(pause_reason,
                        ' | V231: paused, junk data — statewide permits in tiny city',
                        '')
                WHERE (population IS NULL OR population <= 0 OR population >= 5000)
                  AND pause_reason LIKE '%V231: paused, junk data%'
            """)
            # Step 2: apply corrected filter (population strictly > 0)
            junk = conn2.execute("""
                UPDATE prod_cities
                SET status='paused',
                    pause_reason=COALESCE(pause_reason, '') ||
                        ' | V231: paused, junk data — statewide permits in tiny city'
                WHERE population > 0
                  AND population < 5000
                  AND total_permits > 1000
                  AND status='active'
            """).rowcount
            conn2.execute("INSERT OR IGNORE INTO system_state (key, value) VALUES ('migration_v231_junk_pause_v2', ?)", (str(junk),))
            conn2.commit()
            print(f"[{datetime.now()}] V231 DC-3: Paused {junk} junk-data cities (0 < pop < 5K, permits > 1K)")
    except Exception as e:
        print(f"[{datetime.now()}] V231 DC-3: Junk pause error (non-fatal): {e}")

    # V231 DC-4: clip Portland future-dated permits. Portland OR's
    # Socrata source returns filing_date values up to 7 days in the
    # future (likely a scheduled-inspection column mis-mapped to
    # filing_date). Rather than trust the upstream, cap any future
    # filing_date to today during cleanup. A more permanent fix is to
    # filter out future dates during collection.
    try:
        m = conn2.execute("SELECT value FROM system_state WHERE key='migration_v231_portland_dates'").fetchone()
        if not m:
            clipped = conn2.execute("""
                UPDATE permits
                SET filing_date = date('now')
                WHERE city='Portland' AND state='OR'
                  AND filing_date > date('now')
            """).rowcount
            conn2.execute("INSERT OR IGNORE INTO system_state (key, value) VALUES ('migration_v231_portland_dates', ?)", (str(clipped),))
            conn2.commit()
            print(f"[{datetime.now()}] V231 DC-4: Clipped {clipped} Portland future-dated permits")
    except Exception as e:
        print(f"[{datetime.now()}] V231 DC-4: Portland date clip error (non-fatal): {e}")

    # V232: force Seattle re-collection. The field_map already maps
    # contractorcompanyname → contractor_name (added V218), but Seattle's
    # 4K prod permits were collected BEFORE V218, so contractor_name is
    # NULL everywhere and downstream profile creation + enrichment have
    # nothing to work with. Deleting just the Seattle rows triggers the
    # scheduled_collection daemon to re-pull them with the current
    # field_map populated.
    try:
        m = conn2.execute("SELECT value FROM system_state WHERE key='migration_v232_seattle_recollect'").fetchone()
        if not m:
            deleted = conn2.execute("""
                DELETE FROM permits
                WHERE (source_city_key='seattle' OR (city='Seattle' AND state='WA'))
            """).rowcount
            # Also clear prod_cities counters so the next collection refill
            # reports the correct numbers rather than stale-minus-delta.
            conn2.execute("""
                UPDATE prod_cities
                SET total_permits=0, newest_permit_date=NULL,
                    last_collection=NULL, data_freshness='no_data'
                WHERE city_slug='seattle' AND state='WA'
            """)
            conn2.execute("INSERT OR IGNORE INTO system_state (key, value) VALUES ('migration_v232_seattle_recollect', ?)", (str(deleted),))
            conn2.commit()
            print(f"[{datetime.now()}] V232: Cleared {deleted} Seattle permits to force re-collect with contractorcompanyname mapping")
    except Exception as e:
        print(f"[{datetime.now()}] V232: Seattle recollect migration error (non-fatal): {e}")

    # V232b: Seattle cursor reset — the V232 Seattle recollect migration
    # cleared permits + newest_permit_date but missed `last_permit_date`,
    # which is the actual incremental-collection cursor. With the cursor
    # still pointing at 2026-04-17, force-collect returned
    # permits_inserted=0 ("already caught up"). Null out every Seattle
    # checkpoint field — cursor, counts, status flags — so the next
    # force-collect does a full backfill populating contractor_name.
    try:
        m = conn2.execute("SELECT value FROM system_state WHERE key='migration_v232b_seattle_cursor'").fetchone()
        if not m:
            conn2.execute("""
                UPDATE prod_cities
                SET last_permit_date = NULL,
                    newest_permit_date = NULL,
                    latest_permit_date = NULL,
                    earliest_permit_date = NULL,
                    total_permits = 0,
                    permits_last_30d = 0,
                    avg_daily_permits = 0,
                    last_collection = NULL,
                    last_successful_collection = NULL,
                    first_successful_collection = NULL,
                    consecutive_no_new = 0,
                    consecutive_failures = 0,
                    last_error = NULL,
                    last_failure_reason = NULL,
                    last_run_status = NULL,
                    days_since_new_data = NULL,
                    data_freshness = 'no_data'
                WHERE city_slug = 'seattle' AND state = 'WA'
            """)
            conn2.execute("INSERT OR IGNORE INTO system_state (key, value) VALUES ('migration_v232b_seattle_cursor', datetime('now'))")
            conn2.commit()
            print(f"[{datetime.now()}] V232b: Reset Seattle cursor + checkpoint fields for full backfill")
    except Exception as e:
        print(f"[{datetime.now()}] V232b: Seattle cursor reset error (non-fatal): {e}")

    # V232 Fix 3: deactivate 0-permit Houston rows in non-TX states. Six
    # legitimate-but-empty Houston entries (AK, DE, MN, MO, MS, PA) clutter
    # admin listings and footer logic. Leaving them active gates nothing —
    # they have zero data — so pause them until a city actually fills in.
    try:
        m = conn2.execute("SELECT value FROM system_state WHERE key='migration_v232_houston_dupes'").fetchone()
        if not m:
            paused = conn2.execute("""
                UPDATE prod_cities
                SET status='paused',
                    pause_reason=COALESCE(pause_reason, '') ||
                        ' | V232: paused, 0-permit Houston in non-TX state'
                WHERE LOWER(city)='houston'
                  AND state != 'TX'
                  AND (total_permits IS NULL OR total_permits = 0)
                  AND status='active'
            """).rowcount
            conn2.execute("INSERT OR IGNORE INTO system_state (key, value) VALUES ('migration_v232_houston_dupes', ?)", (str(paused),))
            conn2.commit()
            print(f"[{datetime.now()}] V232: Paused {paused} zero-permit Houston rows in non-TX states")
    except Exception as e:
        print(f"[{datetime.now()}] V232: Houston dupe pause error (non-fatal): {e}")

    # V233 P0-1: extend the V232b Seattle cursor reset — task doc calls
    # out `backfill_status='pending'` and `health_status='pending'`
    # which V232b didn't touch. Without these, collection schedulers
    # that gate on health_status see Seattle as 'never_worked' and keep
    # skipping it. Re-set all cursor fields (in case V232b was partial)
    # and also set the two status fields the task doc specified.
    try:
        m = conn2.execute("SELECT value FROM system_state WHERE key='migration_v233_seattle_status'").fetchone()
        if not m:
            conn2.execute("""
                UPDATE prod_cities
                SET last_permit_date = NULL,
                    newest_permit_date = NULL,
                    latest_permit_date = NULL,
                    earliest_permit_date = NULL,
                    total_permits = 0,
                    permits_last_30d = 0,
                    avg_daily_permits = 0,
                    last_collection = NULL,
                    last_successful_collection = NULL,
                    first_successful_collection = NULL,
                    consecutive_no_new = 0,
                    consecutive_failures = 0,
                    last_error = NULL,
                    last_failure_reason = NULL,
                    last_run_status = NULL,
                    days_since_new_data = NULL,
                    data_freshness = 'no_data',
                    backfill_status = 'pending',
                    health_status = 'pending'
                WHERE city_slug = 'seattle' AND state = 'WA'
            """)
            conn2.execute("INSERT OR IGNORE INTO system_state (key, value) VALUES ('migration_v233_seattle_status', datetime('now'))")
            conn2.commit()
            print(f"[{datetime.now()}] V233 P0-1: Seattle backfill_status + health_status set to 'pending'")
    except Exception as e:
        print(f"[{datetime.now()}] V233 P0-1: Seattle status reset error (non-fatal): {e}")

    # V233 P0-2: rename existing NYC permits from "New York" to "New York
    # City". db.py's canonicalization used to rewrite every NYC variant
    # to "New York" (the state's name), so the 35K permits ended up under
    # `city='New York', state='NY'` while prod_cities.city='New York City'.
    # Any query that joined on (city, state) returned 0 rows. The table
    # flip in db.py fixes future ingests; this migration back-fills
    # existing rows so downstream pipelines (enrichment, profiles) see
    # NYC permits again.
    try:
        m = conn2.execute("SELECT value FROM system_state WHERE key='migration_v233_nyc_rename'").fetchone()
        if not m:
            renamed = conn2.execute("""
                UPDATE permits
                SET city='New York City'
                WHERE state='NY' AND city='New York'
            """).rowcount
            conn2.execute("INSERT OR IGNORE INTO system_state (key, value) VALUES ('migration_v233_nyc_rename', ?)", (str(renamed),))
            conn2.commit()
            print(f"[{datetime.now()}] V233 P0-2: Renamed {renamed} NYC permits 'New York' → 'New York City'")
    except Exception as e:
        print(f"[{datetime.now()}] V233 P0-2: NYC rename error (non-fatal): {e}")

    # V242b P2: pause the mesa-az-accela duplicate city key. V242's
    # audit showed 99.8% overlap (8,427 / 8,441) with the canonical
    # 'mesa' slug — same contractors collected under two keys, doubling
    # the enrichment workload for no benefit. Pause the dup and delete
    # its profiles so the daemon's per-city fairness quota stops
    # spending cycles on a redundant slot.
    try:
        m = conn2.execute("SELECT value FROM system_state WHERE key='migration_v242b_mesa_dup'").fetchone()
        if not m:
            conn2.execute("""
                UPDATE prod_cities
                SET status='paused', has_enrichment=0,
                    pause_reason = COALESCE(pause_reason, '') ||
                        ' | V242b: duplicate of mesa — collected under two keys'
                WHERE city_slug = 'mesa-az-accela' AND status != 'paused'
            """)
            deleted = conn2.execute("""
                DELETE FROM contractor_profiles
                WHERE source_city_key = 'mesa-az-accela'
            """).rowcount
            conn2.execute("INSERT OR IGNORE INTO system_state (key, value) VALUES ('migration_v242b_mesa_dup', ?)", (str(deleted),))
            conn2.commit()
            print(f"[{datetime.now()}] V242b: Paused mesa-az-accela duplicate and deleted {deleted} duplicated profiles")
    except Exception as e:
        print(f"[{datetime.now()}] V242b: mesa-az-accela dedup error (non-fatal): {e}")

    # V242 P0.5 B+C: garbage profile cleanup. 2026-04-22 audit found
    # 16K profiles where contractor_name_raw is purely numeric (permit
    # IDs, AMANDA Customer RSNs, or DOB applicant license numbers —
    # none of which are resolvable to a real business). Enrichment
    # kept fabricating phones against these IDs: 43% of all phone
    # rows in the system were garbage. Delete them, and hard-pause
    # the three cities whose entire profile sets are numeric.
    try:
        m = conn2.execute("SELECT value FROM system_state WHERE key='migration_v242_garbage_profiles'").fetchone()
        if not m:
            # Delete all profiles from the three broken cities.
            del1 = conn2.execute("""
                DELETE FROM contractor_profiles
                WHERE source_city_key IN ('fenner-ny', 'portland', 'portland-wi')
            """).rowcount
            # Delete numeric-only profiles in NYC (DOB applicant license
            # IDs mis-mapped as contractor names). Keep anything with a
            # letter in it.
            del2 = conn2.execute("""
                DELETE FROM contractor_profiles
                WHERE source_city_key = 'new-york-city'
                  AND contractor_name_raw GLOB '[0-9]*'
                  AND contractor_name_raw NOT GLOB '*[A-Za-z]*'
            """).rowcount
            # Hard-pause the three broken cities so the daemon stops
            # re-collecting them and auto-recreating garbage profiles.
            conn2.execute("""
                UPDATE prod_cities
                SET status='paused', has_enrichment=0,
                    pause_reason = COALESCE(pause_reason, '') ||
                        ' | V242: garbage-only profiles (numeric IDs, not business names)'
                WHERE city_slug IN ('fenner-ny', 'portland', 'portland-wi')
                  AND status != 'paused'
            """)
            conn2.execute("INSERT OR IGNORE INTO system_state (key, value) VALUES ('migration_v242_garbage_profiles', ?)", (str(del1 + del2),))
            conn2.commit()
            print(f"[{datetime.now()}] V242 P0.5: Deleted {del1} profiles from fenner-ny/portland/portland-wi, "
                  f"{del2} numeric-only profiles from new-york-city")
    except Exception as e:
        print(f"[{datetime.now()}] V242 P0.5: Garbage cleanup error (non-fatal): {e}")

    # V238 (launch readiness): pause Fenner NY and Portland WI — both
    # were flagged by V231 DC-3 as junk-data cities (statewide permits
    # funneled into a tiny-population row) but retained status='active'
    # because their population data was 0/missing and the DC-3 filter
    # required population > 0. That sent the enrichment daemon off to
    # fabricate 3,174 phone numbers for the Fenner NY "contractors"
    # (which are actually source-keyed numeric IDs). Hard-pause both.
    try:
        m = conn2.execute("SELECT value FROM system_state WHERE key='migration_v238_junk_pause'").fetchone()
        if not m:
            paused = conn2.execute("""
                UPDATE prod_cities
                SET status='paused', has_enrichment=0,
                    pause_reason = COALESCE(pause_reason, '') ||
                        ' | V238: junk data (statewide permits in tiny city) hard-paused'
                WHERE city_slug IN ('fenner-ny', 'portland-wi')
                  AND status != 'paused'
            """).rowcount
            conn2.execute("""
                UPDATE contractor_profiles
                SET enrichment_status='no_source', updated_at=datetime('now')
                WHERE source_city_key IN ('fenner-ny', 'portland-wi')
                  AND (enrichment_status IS NULL
                       OR enrichment_status IN ('pending','not_found'))
            """)
            conn2.execute("INSERT OR IGNORE INTO system_state (key, value) VALUES ('migration_v238_junk_pause', ?)", (str(paused),))
            conn2.commit()
            print(f"[{datetime.now()}] V238: Paused {paused} junk-data cities (fenner-ny, portland-wi)")
    except Exception as e:
        print(f"[{datetime.now()}] V238: Junk pause error (non-fatal): {e}")

    # V238: one-shot reset of any lingering future-dated prod_cities
    # newest_permit_date. V233 P1-5 already did Portland OR once but the
    # collector re-wrote it after the next cycle (the MAX(date) read
    # didn't filter future). With today's collector patch the regression
    # can't recur; this migration just forces the current state clean.
    try:
        m = conn2.execute("SELECT value FROM system_state WHERE key='migration_v238_future_date_reset'").fetchone()
        if not m:
            fixed = conn2.execute("""
                UPDATE prod_cities
                SET newest_permit_date = NULL
                WHERE newest_permit_date > date('now')
            """).rowcount
            conn2.execute("INSERT OR IGNORE INTO system_state (key, value) VALUES ('migration_v238_future_date_reset', ?)", (str(fixed),))
            conn2.commit()
            print(f"[{datetime.now()}] V238: Nulled {fixed} prod_cities future-dated newest_permit_date values")
    except Exception as e:
        print(f"[{datetime.now()}] V238: Future date reset error (non-fatal): {e}")

    # V238: Portland status cleanup. V237's OR CCB import assumed
    # Portland's numeric contractor_name_raw values were CCB license
    # numbers and flipped 3,521 profiles to 'not_found' when the lookup
    # failed. Turns out those values are Portland BDS AMANDA Customer
    # RSNs (internal FK, not published), so there's no public source to
    # ever resolve them. Move them out of the DDG retry pool by marking
    # as 'no_source', and flip prod_cities.has_enrichment=0 so the
    # enrichment daemon stops wasting cycles on Portland.
    try:
        m = conn2.execute("SELECT value FROM system_state WHERE key='migration_v238_portland_no_source'").fetchone()
        if not m:
            reset = conn2.execute("""
                UPDATE contractor_profiles
                SET enrichment_status = 'no_source',
                    updated_at = datetime('now')
                WHERE source_city_key IN ('portland', 'portland-or')
                  AND enrichment_status IN ('not_found', 'pending')
                  AND contractor_name_raw GLOB '[0-9]*'
            """).rowcount
            conn2.execute("""
                UPDATE prod_cities
                SET has_enrichment = 0
                WHERE city_slug IN ('portland', 'portland-or')
                  AND state = 'OR'
            """)
            conn2.execute("INSERT OR IGNORE INTO system_state (key, value) VALUES ('migration_v238_portland_no_source', ?)", (str(reset),))
            conn2.commit()
            print(f"[{datetime.now()}] V238: Marked {reset} Portland numeric-name profiles as 'no_source'; disabled has_enrichment")
    except Exception as e:
        print(f"[{datetime.now()}] V238: Portland cleanup error (non-fatal): {e}")

    # V233 P1-5: refresh Portland's newest_permit_date. V231 DC-4 clipped
    # the future-dated rows in the permits table AND added a forward-
    # guard in upsert_permits(), but prod_cities.newest_permit_date is a
    # denormalized summary that nothing has re-derived since then — so
    # the city page still reads "newest: 2026-04-28" (future date) on
    # the freshness badge. Refresh it from MAX(filing_date) directly.
    try:
        m = conn2.execute("SELECT value FROM system_state WHERE key='migration_v233_portland_freshness'").fetchone()
        if not m:
            row = conn2.execute("""
                SELECT MAX(filing_date) as max_fd
                FROM permits
                WHERE city='Portland' AND state='OR'
                  AND filing_date IS NOT NULL AND filing_date != ''
                  AND filing_date <= date('now')
            """).fetchone()
            max_fd = row[0] if row and row[0] else None
            if max_fd:
                conn2.execute("""
                    UPDATE prod_cities
                    SET newest_permit_date = ?
                    WHERE city_slug = 'portland' AND state = 'OR'
                """, (max_fd,))
            conn2.execute("INSERT OR IGNORE INTO system_state (key, value) VALUES ('migration_v233_portland_freshness', ?)", (str(max_fd or 'none'),))
            conn2.commit()
            print(f"[{datetime.now()}] V233 P1-5: Refreshed Portland newest_permit_date to {max_fd}")
    except Exception as e:
        print(f"[{datetime.now()}] V233 P1-5: Portland freshness refresh error (non-fatal): {e}")

    # V233 P0-3: pause 7 collectors with consecutive_failures > 10. Each
    # has been retrying the same dead/broken upstream every cycle, eating
    # log space and wasting threads. Pause them — they can be un-paused
    # if/when the source is fixed. (Houston is extra-paused: also drop
    # source_type so the collector doesn't even try.)
    try:
        m = conn2.execute("SELECT value FROM system_state WHERE key='migration_v233_pause_failing'").fetchone()
        if not m:
            _failing_slugs = [
                # (slug, reason)
                ('las-vegas',       'V233: ArcGIS 400 Invalid or missing input, 84+ consecutive failures'),
                ('houston',         'V233: data.houstontx.gov CKAN Invalid URL, 72+ failures, source confirmed dead'),
                ('sioux-falls',     'V233: ArcGIS 400, 70+ consecutive failures'),
                ('st-petersburg',   'V233: ArcGIS 404 Service not found, 66+ failures'),
                ('fairfax-county',  'V233: ArcGIS 400, 52+ consecutive failures'),
                ('greenville',      'V233: ArcGIS 400 Failed to execute query, 44+ failures'),
                ('anchorage',       'V233: Accela "Could not find Search button", 16+ failures'),
            ]
            paused = 0
            for slug, reason in _failing_slugs:
                rc = conn2.execute("""
                    UPDATE prod_cities
                    SET status='paused',
                        pause_reason = COALESCE(pause_reason, '') || ' | ' || ?
                    WHERE city_slug = ? AND status != 'paused'
                """, (reason, slug)).rowcount
                paused += rc
            # Houston also drops source_type so the daemon stops trying
            # the dead CKAN endpoint entirely.
            conn2.execute("""
                UPDATE prod_cities SET source_type = NULL
                WHERE city_slug = 'houston' AND source_type IS NOT NULL
            """)
            conn2.execute("INSERT OR IGNORE INTO system_state (key, value) VALUES ('migration_v233_pause_failing', ?)", (str(paused),))
            conn2.commit()
            print(f"[{datetime.now()}] V233 P0-3: Paused {paused} consistently failing collectors")
    except Exception as e:
        print(f"[{datetime.now()}] V233 P0-3: Pause failing collectors error (non-fatal): {e}")

    conn2.close()

    conn.close()
    print(f"[{datetime.now()}] V170: All tables created/verified")

    # V229 addendum K1: daemon startup moved out of this before_request
    # hook into a module-level spawn (see bottom of file). This hook now
    # only runs schema migrations. Previously daemons wouldn't start at
    # all if the first HTTP request missed the before_request chain
    # (e.g. a healthcheck on a path that skipped it).
    _ensure_deferred_startup_spawned()


def _bulk_load_city_research():
    """V149: One-time bulk load of US cities into city_research from us_cities."""
    conn = permitdb.get_connection()
    count = conn.execute("SELECT COUNT(*) FROM city_research").fetchone()[0]
    if count >= 400:
        print(f"[{datetime.now()}] V149: city_research already has {count} rows, skipping bulk load")
        conn.close()
        return
    r = conn.execute("""
        INSERT OR IGNORE INTO city_research (city, state, population)
        SELECT city_name, state, population FROM us_cities
        WHERE population >= 50000 ORDER BY population DESC
    """).rowcount
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM city_research").fetchone()[0]
    conn.close()
    print(f"[{datetime.now()}] V149: Bulk loaded {r} cities into city_research (total: {total})")


def _backfill_sources_table():
    """V145: Populate sources ONLY from proven data (successful scraper_runs + permits)."""
    conn = permitdb.get_connection()
    count = conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
    if count > 50:
        print(f"[{datetime.now()}] V145: sources already has {count} rows, skipping backfill")
        conn.close()
        return

    # STEP 1: Get prod_cities with successful scraper_runs in last 90 days
    try:
        proven = conn.execute("""
            SELECT DISTINCT p.city_slug, p.city, p.state, p.source_id, p.source_type, p.population
            FROM prod_cities p
            INNER JOIN scraper_runs sr ON sr.source_name = p.source_id
            WHERE p.source_id IS NOT NULL AND p.source_id != ''
              AND p.source_type IS NOT NULL
              AND sr.status = 'success'
              AND sr.run_started_at > datetime('now', '-90 days')
        """).fetchall()
    except Exception:
        proven = []
    print(f"[{datetime.now()}] V145: Found {len(proven)} proven sources from scraper_runs")

    inserted = 0
    for row in proven:
        source_key = row[0]  # city_slug (hyphen format)
        # Get permit stats
        stats = conn.execute("SELECT COUNT(*) as cnt, MAX(date) as newest FROM permits WHERE source_city_key=?", (source_key,)).fetchone()
        # Try config lookup
        cs = None
        for k in [source_key.replace('-', '_'), source_key]:
            cs = conn.execute("SELECT endpoint, field_map, date_field, platform FROM city_sources WHERE source_key=?", (k,)).fetchone()
            if cs: break
        try:
            conn.execute("""
                INSERT OR IGNORE INTO sources
                (source_key, name, state, population, platform, endpoint, field_map, date_field,
                 data_type, status, total_permits, newest_permit_date)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'permits', 'active', ?, ?)
            """, (source_key, row[1] or source_key, row[2] or '', row[5] or 0,
                  (cs[3] if cs else row[4]) or 'unknown', cs[0] if cs else '', cs[1] if cs else '{}', cs[2] if cs else 'date',
                  stats[0] if stats else 0, stats[1] if stats else None))
            inserted += 1
        except Exception:
            pass

    # STEP 2: Orphan permit sources with recent data but no prod_cities
    try:
        orphans = conn.execute("""
            SELECT DISTINCT source_city_key, COUNT(*) as cnt, MAX(date) as newest
            FROM permits
            WHERE source_city_key NOT IN (SELECT source_key FROM sources)
              AND date > date('now', '-90 days')
            GROUP BY source_city_key HAVING cnt > 10
        """).fetchall()
    except Exception:
        orphans = []

    for row in orphans:
        try:
            conn.execute("""
                INSERT OR IGNORE INTO sources
                (source_key, name, state, platform, endpoint, data_type, status,
                 total_permits, newest_permit_date, notes)
                VALUES (?, ?, '', 'unknown', '', 'permits', 'unverified', ?, ?, 'backfilled from permits')
            """, (row[0], row[0], row[1], row[2]))
        except Exception:
            pass

    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
    active = conn.execute("SELECT COUNT(*) FROM sources WHERE status='active'").fetchone()[0]
    unverified = conn.execute("SELECT COUNT(*) FROM sources WHERE status='unverified'").fetchone()[0]
    conn.close()
    print(f"[{datetime.now()}] V145: Backfill — {total} total, {active} active, {unverified} unverified")


def _deferred_startup():
    """V69: Mark startup done but DO NOT start any background threads.
    V93: Email scheduler is now auto-started (doesn't need Postgres)."""
    global _startup_done
    if _startup_done:
        return
    _startup_done = True
    print(f"[{datetime.now()}] V145: Server starting — SQLite only")

    # V145: Create sources table (single source of truth)
    try:
        _migrate_create_sources_table()
    except Exception as e:
        print(f"[{datetime.now()}] V145: Sources migration error (non-fatal): {e}")

    # V145: Backfill sources from prod_cities + permits (one-time)
    try:
        _backfill_sources_table()
    except Exception as e:
        print(f"[{datetime.now()}] V145: Sources backfill error (non-fatal): {e}")

    # V149: Bulk load cities into city_research pipeline
    try:
        _bulk_load_city_research()
    except Exception as e:
        print(f"[{datetime.now()}] V149: City research bulk load error (non-fatal): {e}")

    # V145: Cleanup old scraper_runs and log disk usage
    try:
        conn = permitdb.get_connection()
        deleted = conn.execute("DELETE FROM scraper_runs WHERE run_started_at < datetime('now', '-30 days')").rowcount
        conn.commit()
        conn.close()
        if deleted > 0:
            print(f"[{datetime.now()}] V145: Cleaned {deleted} old scraper_runs")
    except Exception as e:
        print(f"[{datetime.now()}] V145: Cleanup error (non-fatal): {e}")

    # V145: Log disk usage
    try:
        import shutil
        usage = shutil.disk_usage('/var/data')
        pct = usage.used / usage.total * 100
        print(f"[{datetime.now()}] V145: Disk {pct:.1f}% ({usage.used // 1024 // 1024}MB / {usage.total // 1024 // 1024}MB)")
        if pct > 85:
            print(f"[{datetime.now()}] V145: WARNING — disk usage above 85%!")
    except Exception:
        pass  # /var/data may not exist locally

    # V183: Refresh contractor profiles + emblems BEFORE spawning background
    # threads. This is the only reliable write window — _deferred_startup runs
    # sequentially, and once maintenance/_v146_safe_autostart spawn, the SQLite
    # write lock is held near-continuously by collection + maintenance threads.
    try:
        import time as _t183
        _t183_start = _t183.time()
        from contractor_profiles import refresh_contractor_profiles, update_city_emblems
        prof = refresh_contractor_profiles()
        emb = update_city_emblems()
        print(f"[{datetime.now()}] [V183] Startup profile refresh: "
              f"{prof['profiles_upserted']} profiles, "
              f"{emb.get('cities_with_enrichment', 0)} enriched / "
              f"{emb.get('cities_with_violations', 0)} violations, "
              f"{_t183.time() - _t183_start:.1f}s", flush=True)
    except Exception as e:
        print(f"[{datetime.now()}] [V183] Startup refresh error (non-fatal): {e}", flush=True)

    # V146: Safe auto-start daemon — runs start_collectors in background thread
    # so it doesn't block gunicorn worker (start_collectors sleeps 120s+)
    import threading as _th
    def _v146_safe_autostart():
        import time as _t
        _t.sleep(10)  # V183: Reverted to 10s — profile refresh now runs in _deferred_startup before threads spawn
        try:
            print(f"[{datetime.now()}] V146: Auto-starting collectors (background)...", flush=True)
            start_collectors()  # This takes 120s+ but runs in THIS thread, not blocking gunicorn
            print(f"[{datetime.now()}] V146: Collectors auto-started OK", flush=True)
        except Exception as _e:
            print(f"[{datetime.now()}] V146: Auto-start failed: {_e}", flush=True)
    _th.Thread(target=_v146_safe_autostart, daemon=True, name='v146_autostart').start()
    print(f"[{datetime.now()}] V146: Daemon auto-start scheduled (background thread)")

    # V106: Phase B — Heavy maintenance in background thread
    # Server is ready to serve requests while this runs
    def _run_background_maintenance():
        try:
            print(f"[{datetime.now()}] [V106] Background maintenance starting...")
            from db import relink_orphaned_permits
            from collector import (update_total_permits_from_actual, update_all_city_health,
                                   activate_bulk_covered_cities, cleanup_balance_of_entries,
                                   cleanup_source_id_mismatches, pause_tiny_no_endpoint_cities)

            # V109b: One-time cleanup of V108 pipeline damage
            _cleanup_v108_pipeline_damage()

            relink_orphaned_permits()
            update_total_permits_from_actual()
            activate_bulk_covered_cities()
            cleanup_balance_of_entries()
            cleanup_source_id_mismatches()
            pause_tiny_no_endpoint_cities()
            update_all_city_health()

            print(f"[{datetime.now()}] [V109b] Background maintenance complete")
        except Exception as e:
            print(f"[{datetime.now()}] [V106] Background maintenance error: {e}")
            import traceback
            traceback.print_exc()

    maintenance_thread = threading.Thread(target=_run_background_maintenance, name='v106_maintenance', daemon=True)
    maintenance_thread.start()
    print(f"[{datetime.now()}] [V106] Background maintenance thread started — server ready to serve")

    # V93: Start email scheduler thread automatically (uses JSON file + SMTP, no Postgres needed)
    try:
        email_thread = threading.Thread(target=schedule_email_tasks, name='email_scheduler', daemon=True)
        email_thread.start()
        print(f"[{datetime.now()}] V93: Email scheduler thread started automatically")
    except Exception as e:
        print(f"[{datetime.now()}] [ERROR] Email scheduler failed to start: {e}")


# V13.1: Jinja filter for human-readable date formatting
@app.template_filter('format_date')
def format_date_filter(date_str):
    """Format date string to human-readable format: Mar 24, 2026"""
    if not date_str:
        return 'Date not available'
    try:
        # Handle ISO format dates
        if isinstance(date_str, str):
            # Check if it starts with a digit (valid date format)
            if not date_str[0].isdigit():
                return 'Date not available'
            date_obj = datetime.strptime(date_str[:10], '%Y-%m-%d')
        else:
            date_obj = date_str
        return date_obj.strftime('%b %d, %Y')  # Mar 24, 2026
    except (ValueError, TypeError):
        return 'Date not available'


@app.template_filter('clean_address')
def clean_address_filter(val):
    """V12.60/V21: Clean raw GeoJSON/Socrata JSON from address fields at display time.
    V21 FIX #13: Return 'Address pending' instead of empty/N/A for missing addresses."""
    if not val:
        return 'Address pending'
    s = str(val).strip()
    # V21: Check for placeholder values
    if s.lower() in ('', 'n/a', 'address not provided', 'none', 'null'):
        return 'Address pending'
    # Quick check — if no curly brace, it's already clean
    if '{' not in s:
        return s
    # Contains JSON — run through parse_address_value
    from collector import parse_address_value
    cleaned = parse_address_value(s)
    return cleaned if cleaned else 'Address pending'


# V12.17: Google Search Console verification - MUST be registered first before any catch-alls
@app.route('/google3ef154d70f8049a0.html')
def google_verification():
    return Response('google-site-verification: google3ef154d70f8049a0.html', mimetype='text/html')


# ===========================
# V12.19: ADMIN ENDPOINTS FOR DATA RECOVERY
# ===========================

def check_admin_key():
    """V12.58: Validate admin key. Returns (is_valid, error_response).

    V229 A4: removed the hardcoded default value. If ADMIN_KEY env var isn't
    set in Render, every admin endpoint denies access — prior behavior was
    to fall back to a baked-in hex string, which meant anyone who could
    read this file had full admin access. Side effect: the admin HTML
    dashboard at /admin?key=... now requires ADMIN_KEY to be set in the
    Render env before it works at all.
    """
    secret = request.headers.get('X-Admin-Key')
    expected = os.environ.get('ADMIN_KEY')
    if not expected:
        return False, (jsonify({'error': 'ADMIN_KEY not configured'}), 503)
    if not secret or secret != expected:
        return False, (jsonify({'error': 'Unauthorized'}), 401)
    return True, None




@app.route('/api/admin/collect-v122', methods=['POST'])
def admin_collect_v122():
    """V122: Run collection from the cities table with per-city insert."""
    valid, error = check_admin_key()
    if not valid:
        return error
    data = request.json or {}
    days_back = data.get('days_back', 7)
    include_scrapers = data.get('include_scrapers', True)

    def run():
        try:
            from collector import collect_v122
            collect_v122(days_back=days_back, include_scrapers=include_scrapers)
        except Exception as e:
            print(f"V122 collection error: {e}", flush=True)
            import traceback
            traceback.print_exc()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return jsonify({'status': 'started', 'days_back': days_back, 'include_scrapers': include_scrapers}), 200


@app.route('/api/admin/force-collection', methods=['POST'])
def admin_force_collection():
    """V64: Force collection — runs ALL platforms, supports filtering.

    JSON body:
      days_back: int (default 7, max 90)
      platform: str (optional — filter to one platform: socrata, arcgis, ckan, carto, accela)
      city_slug: str (optional — run a single city only)
      include_scrapers: bool (default false — run Accela/Playwright scrapers too)
    """
    valid, error = check_admin_key()
    if not valid:
        return error

    data = request.json or {}
    days_back = min(int(data.get('days_back', 7)), 90)
    platform_filter = data.get('platform')
    city_slug = data.get('city_slug')
    include_scrapers = data.get('include_scrapers', True)  # V74: Default to True so Accela/CKAN get collected

    if city_slug:
        # Synchronous single-city mode (fast enough)
        try:
            from collector import collect_single_city
            result = collect_single_city(city_slug, days_back=days_back)
            return jsonify({
                'mode': 'single_city',
                'city_slug': city_slug,
                'days_back': days_back,
                'result': result
            })
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    else:
        # Background thread for full/filtered collection.
        # V229 A3: renamed from run_collection for clarity (3 inner functions
        # with the same name across this module — shadowing-safe because each
        # is scoped to its enclosing handler, but the name reuse made grep
        # noisy and obscured intent).
        def _run_refresh_collection():
            try:
                from collector import collect_refresh
                print(f"[Admin] Starting REFRESH collection (platform={platform_filter}, scrapers={include_scrapers})...")
                collect_refresh(
                    days_back=days_back,
                    platform_filter=platform_filter,
                    include_scrapers=include_scrapers
                )
                print("[Admin] Refresh collection complete.")
            except Exception as e:
                print(f"[Admin] Collection error: {e}")
                import traceback
                traceback.print_exc()

        thread = threading.Thread(target=_run_refresh_collection, daemon=True)
        thread.start()

        return jsonify({
            'message': 'REFRESH collection started',
            'mode': 'background',
            'days_back': days_back,
            'platform_filter': platform_filter,
            'include_scrapers': include_scrapers,
            'note': 'V64: Supports all platforms, check logs for progress'
        })


@app.route('/api/admin/full-collection', methods=['POST'])
def admin_full_collection():
    """V12.50: Trigger FULL collection (rebuild SQLite)."""
    valid, error = check_admin_key()
    if not valid:
        return error

    # V229 A3: renamed from run_collection (see _run_refresh_collection above)
    def _run_full_collection():
        try:
            from collector import collect_full
            print("[Admin] Starting FULL collection (rebuild mode)...")
            collect_full(days_back=365)
            print("[Admin] Full collection complete.")
        except Exception as e:
            print(f"[Admin] Full collection error: {e}")

    thread = threading.Thread(target=_run_full_collection, daemon=True)
    thread.start()

    return jsonify({
        'message': 'FULL collection started (rebuild mode)',
        'note': 'V12.50: Rebuilds SQLite database. Takes 30-60 minutes.'
    })


# ===========================
# V226: Admin tools — force-collect, city-health, dashboard, enrich
# ===========================

def _collect_city_sync(city_slug, collect_type='both', days_back=None):
    """V226 T2 / V228 hardened: synchronous per-city collection with
    structured before/after diff. Re-fetches the DB handle after each
    collector call because collector.collect_single_city and
    violation_collector.collect_violations_from_endpoint both call
    permitdb.get_connection() internally and may close shared handles
    in their cleanup paths — V226's version held one conn across all
    three phases and blew up with 'Cannot operate on a closed database'
    on the post-call queries."""
    import time as _t
    from datetime import datetime as _dt
    t0 = _t.time()

    def _snapshot():
        c = permitdb.get_connection()
        p = c.execute(
            "SELECT COUNT(*), MAX(COALESCE(NULLIF(filing_date,''),NULLIF(issued_date,''),NULLIF(date,''))) "
            "FROM permits WHERE source_city_key = ?",
            (city_slug,),
        ).fetchone()
        v = c.execute(
            "SELECT COUNT(*), MAX(violation_date) FROM violations WHERE prod_city_id IN "
            "(SELECT id FROM prod_cities WHERE city_slug = ?)",
            (city_slug,),
        ).fetchone()
        return (p[0] or 0, p[1], v[0] or 0, v[1])

    pre_permit_count, _, pre_viol_count, _ = _snapshot()
    permits_inserted = 0
    violations_inserted = 0
    error_messages = []

    # V233b: when the city is in pending-backfill state (cursor nulled,
    # backfill_status='pending') the default 7-day window only pulls the
    # last handful of permits. That's what left Seattle with 21 permits
    # post-V233 even though 4K had been wiped. Auto-upgrade to a 180-day
    # window for those cities. Caller can still override via days_back.
    _effective_days_back = days_back
    _is_backfill = False
    if _effective_days_back is None:
        try:
            _bf_conn = permitdb.get_connection()
            _bf_row = _bf_conn.execute(
                "SELECT backfill_status, last_permit_date, total_permits "
                "FROM prod_cities WHERE city_slug = ?",
                (city_slug,)
            ).fetchone()
            if _bf_row:
                _bfs = (_bf_row['backfill_status']
                        if isinstance(_bf_row, dict) else _bf_row[0])
                _lpd = (_bf_row['last_permit_date']
                        if isinstance(_bf_row, dict) else _bf_row[1])
                _tp = (_bf_row['total_permits']
                       if isinstance(_bf_row, dict) else _bf_row[2]) or 0
                if _bfs == 'pending' or _lpd is None or _tp < 100:
                    _is_backfill = True
                    _effective_days_back = 180
        except Exception:
            pass
    if _effective_days_back is None:
        _effective_days_back = 7

    if collect_type in ('permits', 'both'):
        try:
            from collector import collect_single_city
            collect_single_city(city_slug, days_back=_effective_days_back)
        except Exception as e:
            error_messages.append(f"permits: {e}")

    if collect_type in ('violations', 'both'):
        try:
            from violation_collector import VIOLATION_SOURCES, collect_violations_from_endpoint
            if city_slug in VIOLATION_SOURCES:
                cfg = VIOLATION_SOURCES[city_slug]
                for endpoint in cfg.get('endpoints', []):
                    try:
                        out = collect_violations_from_endpoint(cfg, endpoint)
                        if isinstance(out, dict):
                            violations_inserted += out.get('inserted', 0) or 0
                        else:
                            violations_inserted += int(out or 0)
                    except Exception as e:
                        error_messages.append(f"violations({endpoint.get('name','?')}): {e}")
        except Exception as e:
            error_messages.append(f"violations: {e}")

    # Re-snapshot with a fresh connection — one of the collectors above may
    # have closed the handle we started with.
    post_permit_count, post_permit_newest, post_viol_count, post_viol_newest = _snapshot()
    permits_inserted = post_permit_count - pre_permit_count
    # post_permit / post_viol tuples from the old layout
    post_permit = (post_permit_count, post_permit_newest)
    post_viol = (post_viol_count, post_viol_newest)

    status = 'success' if (permits_inserted > 0 or violations_inserted > 0) else (
        'error' if error_messages else 'caught_up'
    )

    # V233b: after a successful backfill, clear the pending flag so the
    # daemon falls back to its normal 7-day incremental cadence instead
    # of re-fetching 180 days every cycle.
    if _is_backfill and permits_inserted > 0 and not error_messages:
        try:
            _bf_conn = permitdb.get_connection()
            _bf_conn.execute("""
                UPDATE prod_cities
                SET backfill_status='complete', health_status='healthy'
                WHERE city_slug = ?
            """, (city_slug,))
            _bf_conn.commit()
        except Exception:
            pass

    return {
        'city_slug': city_slug,
        'type': collect_type,
        'status': status,
        'days_back': _effective_days_back,
        'backfill': _is_backfill,
        'permits_total': post_permit[0] or 0,
        'permits_inserted': permits_inserted,
        'permits_newest': post_permit[1],
        'violations_total': post_viol[0] or 0,
        'violations_inserted': violations_inserted,
        'violations_newest': post_viol[1],
        'elapsed_seconds': round(_t.time() - t0, 2),
        'errors': error_messages or None,
        'ran_at': _dt.utcnow().strftime('%Y-%m-%d %H:%M:%S'),
    }


@app.route('/api/admin/force-collect', methods=['GET', 'POST'])
def admin_force_collect():
    """V226 T2: Structured single-city collection with before/after diff.

    POST body: {"city_slug": "chicago", "type": "permits"|"violations"|"both"}
    GET: /api/admin/force-collect?city=chicago&type=both
    Returns inserted/total/newest deltas + elapsed + errors.
    """
    valid, error = check_admin_key()
    if not valid:
        return error

    if request.method == 'POST':
        data = request.get_json() or {}
        city_slug = data.get('city_slug') or data.get('city')
        collect_type = data.get('type', 'both')
        days_back = data.get('days_back')
    else:
        city_slug = request.args.get('city') or request.args.get('city_slug')
        collect_type = request.args.get('type', 'both')
        days_back = request.args.get('days_back')

    if not city_slug:
        return jsonify({'error': 'city_slug required'}), 400
    if collect_type not in ('permits', 'violations', 'both'):
        return jsonify({'error': 'type must be permits/violations/both'}), 400
    # V233b: accept optional days_back override. Default None lets
    # _collect_city_sync auto-select 180 for pending-backfill cities
    # and 7 for everything else.
    if days_back is not None:
        try:
            days_back = min(max(int(days_back), 1), 730)
        except (TypeError, ValueError):
            return jsonify({'error': 'days_back must be an integer'}), 400

    # V234 P2: async mode for long-running backfills. Render kills the
    # HTTP connection at 30s; a 180-day force-collect can take several
    # minutes. If the caller explicitly asks (?async=true) or passes a
    # days_back >= 30 (implying backfill), spawn a background thread
    # and return immediately. Short incremental calls stay synchronous
    # so Cowork's tooling gets the before/after diff in one response.
    if request.method == 'POST':
        _async_flag = data.get('async')
    else:
        _async_flag = request.args.get('async')
    _want_async = (
        str(_async_flag).lower() in ('1', 'true', 'yes')
        or (days_back is not None and days_back >= 30)
    )
    if _want_async:
        import uuid as _uuid
        job_id = _uuid.uuid4().hex[:12]
        def _bg_collect():
            try:
                result = _collect_city_sync(city_slug, collect_type, days_back=days_back)
                print(f"[V234] force-collect job {job_id} done: {result}", flush=True)
            except Exception as _e:
                import traceback as _tb
                print(f"[V234] force-collect job {job_id} error: {_e}", flush=True)
                _tb.print_exc()
        threading.Thread(target=_bg_collect, daemon=True,
                         name=f'force_collect_{city_slug}').start()
        return jsonify({
            'status': 'started',
            'async': True,
            'job_id': job_id,
            'city_slug': city_slug,
            'type': collect_type,
            'days_back': days_back or 'auto',
            'message': 'running in background — check collection_log / scraper_runs for status',
        }), 202

    try:
        result = _collect_city_sync(city_slug, collect_type, days_back=days_back)
        return jsonify(result)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e), 'city_slug': city_slug}), 500


@app.route('/api/admin/city-health', methods=['GET'])
def admin_city_health():
    """V226 T3: One-glance health status for every active top-cities entry.

    GREEN  = newest permit < 7d AND enrichment >= 40% (or no profiles)
    YELLOW = newest permit 7-21d OR enrichment 20-40%
    RED    = newest permit > 21d OR 0 permits OR enrichment < 20% (when profiles > 50)
    """
    valid, error = check_admin_key()
    if not valid:
        return error

    conn = permitdb.get_connection()
    top_slugs = [
        'new-york-city','los-angeles','chicago','houston','phoenix',
        'philadelphia','dallas','austin','san-antonio','san-diego',
        'nashville','seattle','san-francisco','fort-worth','columbus',
        'denver','memphis','new-orleans','milwaukee','san-jose',
    ]
    placeholders = ','.join('?' * len(top_slugs))
    rows = conn.execute(f"""
        SELECT pc.city_slug, pc.state, pc.status,
               COALESCE(p.cnt, 0) as permits,
               p.newest as newest_permit,
               COALESCE(v.cnt, 0) as violations,
               v.newest as newest_violation,
               COALESCE(cp.cnt, 0) as profiles,
               COALESCE(cp.enriched, 0) as enriched,
               pc.last_collection
        FROM prod_cities pc
        LEFT JOIN (
            SELECT source_city_key, COUNT(*) cnt,
                   MAX(COALESCE(NULLIF(filing_date,''),NULLIF(issued_date,''),NULLIF(date,''))) newest
            FROM permits GROUP BY source_city_key
        ) p ON p.source_city_key = pc.city_slug
        LEFT JOIN (
            SELECT prod_city_id, COUNT(*) cnt, MAX(violation_date) newest
            FROM violations GROUP BY prod_city_id
        ) v ON v.prod_city_id = pc.id
        LEFT JOIN (
            SELECT source_city_key, COUNT(*) cnt,
                   SUM(CASE WHEN (phone IS NOT NULL AND phone != '') OR (website IS NOT NULL AND website != '')
                            THEN 1 ELSE 0 END) enriched
            FROM contractor_profiles GROUP BY source_city_key
        ) cp ON cp.source_city_key = pc.city_slug
        WHERE pc.city_slug IN ({placeholders})
        ORDER BY permits DESC
    """, top_slugs).fetchall()

    from datetime import datetime as _dt, timedelta as _td
    today = _dt.utcnow().date()
    summary = {'green': 0, 'yellow': 0, 'red': 0}
    cities = []
    for r in rows:
        row = {k: r[k] for k in r.keys()}
        newest = (row.get('newest_permit') or '')[:10]
        age = None
        if newest and len(newest) == 10:
            try:
                age = (today - _dt.strptime(newest, '%Y-%m-%d').date()).days
            except Exception:
                age = None
        row['permit_age_days'] = age
        pct = 0
        if row['profiles']:
            pct = round(100.0 * row['enriched'] / row['profiles'], 1)
        row['enrichment_pct'] = pct

        # Health scoring
        if row['status'] != 'active':
            health = 'PAUSED'
        elif age is None or row['permits'] == 0 or age > 21:
            health = 'RED'
        elif row['profiles'] > 50 and pct < 20:
            health = 'RED'
        elif age > 7 or (row['profiles'] > 50 and pct < 40):
            health = 'YELLOW'
        else:
            health = 'GREEN'
        row['health'] = health
        if health in summary:
            summary[health.lower()] = summary.get(health.lower(), 0) + 1
        cities.append(row)

    from datetime import datetime as _dt2
    return jsonify({
        'generated_at': _dt2.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
        'total_cities_checked': len(cities),
        'cities': cities,
        'summary': summary,
    })


@app.route('/api/admin/enrich', methods=['POST'])
def admin_enrich():
    """V226 T5: Force-enrich N profiles for a given city using
    FreeEnrichmentEngine (DuckDuckGo + domain guess + YellowPages fallback).

    Body: {"city_slug": "chicago", "batch_size": 30}
    """
    valid, error = check_admin_key()
    if not valid:
        return error
    data = request.get_json() or {}
    city_slug = data.get('city_slug') or data.get('city')
    batch_size = min(int(data.get('batch_size', 20)), 100)
    if not city_slug:
        return jsonify({'error': 'city_slug required'}), 400

    import time as _t
    from datetime import datetime as _dt
    from web_enrichment import FreeEnrichmentEngine, normalize_name

    t0 = _t.time()
    conn = permitdb.get_connection()
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT cp.id, cp.contractor_name_raw, cp.contractor_name_normalized,
               cp.city, cp.state
        FROM contractor_profiles cp
        WHERE cp.source_city_key = ?
          AND (cp.enrichment_status IS NULL OR cp.enrichment_status = 'pending')
          AND cp.contractor_name_raw IS NOT NULL AND cp.contractor_name_raw != ''
          AND LENGTH(cp.contractor_name_raw) >= 5
          AND cp.contractor_name_raw NOT LIKE 'NOT GIVEN%'
          AND cp.contractor_name_raw NOT LIKE 'HOMEOWNER%'
          AND cp.contractor_name_raw NOT LIKE 'OWNER %'
          AND cp.contractor_name_raw NOT GLOB '[0-9]*'
          AND (cp.phone IS NULL OR cp.phone = '')
          AND (cp.website IS NULL OR cp.website = '')
        ORDER BY cp.total_permits DESC
        LIMIT ?
    """, (city_slug, batch_size)).fetchall()

    engine = FreeEnrichmentEngine()
    attempted = 0
    enriched = 0
    failed = 0
    for r in rows:
        attempted += 1
        raw = r['contractor_name_raw']
        norm = r['contractor_name_normalized'] or normalize_name(raw)
        try:
            phone, website = engine.enrich_one(raw, r['city'] or '', r['state'] or '')
        except Exception:
            phone, website = None, None
        now = _dt.utcnow().strftime('%Y-%m-%d %H:%M:%S')
        try:
            if phone or website:
                enriched += 1
                conn.execute("""INSERT OR REPLACE INTO contractor_contacts
                        (contractor_name_normalized, display_name, phone, website,
                         source, confidence, looked_up_at)
                        VALUES (?, ?, ?, ?, 'web_enrichment', 'low', ?)""",
                        (norm, raw, phone, website, now))
                conn.execute("""UPDATE contractor_profiles
                        SET phone = COALESCE(NULLIF(phone, ''), ?),
                            website = COALESCE(NULLIF(website, ''), ?),
                            enrichment_status = 'enriched',
                            enriched_at = ?, updated_at = ?
                        WHERE id = ?""", (phone, website, now, now, r['id']))
                conn.execute("""INSERT INTO enrichment_log
                        (contractor_profile_id, source, status, cost, created_at)
                        VALUES (?, 'web_enrichment', 'enriched', 0.0, ?)""",
                        (r['id'], now))
            else:
                failed += 1
                conn.execute("""UPDATE contractor_profiles
                        SET enrichment_status = 'not_found',
                            enriched_at = ?, updated_at = ?
                        WHERE id = ?""", (now, now, r['id']))
                conn.execute("""INSERT INTO enrichment_log
                        (contractor_profile_id, source, status, cost, created_at)
                        VALUES (?, 'web_enrichment', 'not_found', 0.0, ?)""",
                        (r['id'], now))
            conn.commit()
        except Exception as e:
            failed += 1
            print(f"[V226 enrich] write err {raw}: {e}", flush=True)

    return jsonify({
        'city_slug': city_slug,
        'attempted': attempted,
        'enriched': enriched,
        'failed': failed,
        'elapsed_seconds': round(_t.time() - t0, 2),
    })


@app.route('/api/admin/enrich-cities', methods=['POST'])
def admin_enrich_cities():
    """V231 Phase 4: run admin_enrich in series for a list of city_slugs.

    Body: {"city_slugs": ["fort-worth", "san-francisco", "seattle", "houston"],
           "batch_size": 50}

    Phase 4 of the master launch plan wants enrichment fast-tracked on
    specific cities without waiting for the 30-min daemon cycle to crawl
    them in FIFO order. This is a convenience wrapper that calls the
    same single-city enrichment logic once per slug and returns a
    per-city summary.
    """
    valid, error = check_admin_key()
    if not valid:
        return error
    data = request.get_json() or {}
    slugs = data.get('city_slugs') or []
    batch_size = min(int(data.get('batch_size', 30)), 100)
    # V234 P0: default to async. Enrichment for 3-4 cities at batch_size=50
    # regularly exceeds Render's 30s HTTP timeout (each DDG lookup is
    # 1-3s, so a 200-profile batch can easily hit 10+ minutes). Spawn a
    # background thread and return a job_id; Cowork can poll
    # enrichment_log to see results as they stream in.
    async_param = data.get('async')
    run_async = True if async_param is None else (
        str(async_param).lower() in ('1', 'true', 'yes')
    )
    if not slugs:
        return jsonify({'error': 'city_slugs required'}), 400
    if not isinstance(slugs, list):
        return jsonify({'error': 'city_slugs must be a list'}), 400

    import time as _t
    from datetime import datetime as _dt
    from web_enrichment import FreeEnrichmentEngine, normalize_name

    def _do_enrich(slug_list, _batch_size, _job_id=None):
        """Inline worker — shared by the sync + async paths."""
        _t0 = _t.time()
        _engine = FreeEnrichmentEngine()
        _conn = permitdb.get_connection()
        _conn.row_factory = sqlite3.Row
        _results = []
        for city_slug in slug_list:
            city_t0 = _t.time()
            rows = _conn.execute("""
                SELECT cp.id, cp.contractor_name_raw, cp.contractor_name_normalized,
                       cp.city, cp.state
                FROM contractor_profiles cp
                WHERE cp.source_city_key = ?
                  AND (cp.enrichment_status IS NULL OR cp.enrichment_status = 'pending')
                  AND cp.contractor_name_raw IS NOT NULL AND cp.contractor_name_raw != ''
                  AND LENGTH(cp.contractor_name_raw) >= 5
                  AND cp.contractor_name_raw NOT LIKE 'NOT GIVEN%'
                  AND cp.contractor_name_raw NOT LIKE 'HOMEOWNER%'
                  AND cp.contractor_name_raw NOT LIKE 'OWNER %'
                  AND cp.contractor_name_raw NOT GLOB '[0-9]*'
                  AND (cp.phone IS NULL OR cp.phone = '')
                  AND (cp.website IS NULL OR cp.website = '')
                ORDER BY cp.total_permits DESC
                LIMIT ?
            """, (city_slug, _batch_size)).fetchall()
            attempted = enriched = failed = 0
            for r in rows:
                attempted += 1
                raw = r['contractor_name_raw']
                norm = r['contractor_name_normalized'] or normalize_name(raw)
                try:
                    phone, website = _engine.enrich_one(raw, r['city'] or '', r['state'] or '')
                except Exception:
                    phone, website = None, None
                now = _dt.utcnow().strftime('%Y-%m-%d %H:%M:%S')
                try:
                    if phone or website:
                        enriched += 1
                        _conn.execute("""INSERT OR REPLACE INTO contractor_contacts
                                (contractor_name_normalized, display_name, phone, website,
                                 source, confidence, looked_up_at)
                                VALUES (?, ?, ?, ?, 'web_enrichment', 'low', ?)""",
                                (norm, raw, phone, website, now))
                        _conn.execute("""UPDATE contractor_profiles
                                SET phone=?, website=?, enrichment_status='enriched',
                                    enriched_at=?, updated_at=?
                                WHERE id=?""",
                                (phone, website, now, now, r['id']))
                        _conn.execute("""INSERT INTO enrichment_log
                                (contractor_profile_id, source, status, cost, created_at)
                                VALUES (?, 'web_enrichment', 'enriched', 0.0, ?)""",
                                (r['id'], now))
                    else:
                        _conn.execute("""UPDATE contractor_profiles
                                SET enrichment_status='not_found',
                                    enriched_at=?, updated_at=?
                                WHERE id=?""", (now, now, r['id']))
                        _conn.execute("""INSERT INTO enrichment_log
                                (contractor_profile_id, source, status, cost, created_at)
                                VALUES (?, 'web_enrichment', 'not_found', 0.0, ?)""",
                                (r['id'], now))
                    _conn.commit()
                except Exception as e:
                    failed += 1
                    print(f"[V234 enrich-cities] {city_slug} {raw}: {e}", flush=True)
            _summary = {
                'city_slug': city_slug,
                'attempted': attempted,
                'enriched': enriched,
                'failed': failed,
                'elapsed_seconds': round(_t.time() - city_t0, 2),
            }
            _results.append(_summary)
            if _job_id:
                print(f"[V234] enrich-cities job {_job_id}: {_summary}", flush=True)
        return {
            'cities': _results,
            'total_elapsed_seconds': round(_t.time() - _t0, 2),
        }

    if run_async:
        import uuid as _uuid
        job_id = _uuid.uuid4().hex[:12]
        def _bg():
            try:
                final = _do_enrich(slugs, batch_size, _job_id=job_id)
                print(f"[V234] enrich-cities job {job_id} COMPLETE: {final}", flush=True)
            except Exception as _e:
                import traceback as _tb
                print(f"[V234] enrich-cities job {job_id} ERROR: {_e}", flush=True)
                _tb.print_exc()
        threading.Thread(target=_bg, daemon=True,
                         name=f'enrich_cities_{job_id}').start()
        return jsonify({
            'status': 'started',
            'async': True,
            'job_id': job_id,
            'city_slugs': slugs,
            'batch_size': batch_size,
            'message': 'running in background — poll enrichment_log / contractor_profiles for progress',
        }), 202

    return jsonify(_do_enrich(slugs, batch_size))


# V241 P4: global serialization lock for license-import. Each state's
# CSV is 13K-500K rows; loading several concurrently OOM'd the Render
# worker (auto-restart 2026-04-22 after 5-state parallel trigger).
# Only one import may run at a time; a second request returns 409 with
# the state of the in-flight job.
_LICENSE_IMPORT_LOCK = threading.Lock()
_LICENSE_IMPORT_IN_FLIGHT = {'state': None, 'job_id': None, 'started_at': None}


@app.route('/api/admin/license-import', methods=['POST'])
def admin_license_import():
    """V237 PR#1: download a state's contractor-license open-data CSV and
    enrich contractor_profiles.

    Body: {"state": "OR", "async": true|false (default true)}
    Response (async): 202 {"status":"started", "job_id":..., "state":...}
    Response (sync):  200 {"state":..., "source_rows":..., "by_license":{...}, ...}
    Response (busy):  409 {"error":"import in progress", "state":..., "job_id":...}

    V241 P4: a threading.Lock gates every invocation (sync and async).
    Concurrent imports OOM'd Render's worker — the serialization keeps
    at most one state's CSV in memory at a time.
    """
    valid, error = check_admin_key()
    if not valid:
        return error
    data = request.get_json() or {}
    state = (data.get('state') or '').upper().strip()
    if not state:
        return jsonify({'error': 'state required'}), 400

    async_param = data.get('async')
    run_async = True if async_param is None else (
        str(async_param).lower() in ('1', 'true', 'yes')
    )

    from license_enrichment import import_state, STATE_CONFIGS
    if state not in STATE_CONFIGS:
        return jsonify({
            'error': f'unknown state {state!r}',
            'available': sorted(STATE_CONFIGS.keys()),
        }), 400

    # V241 P4: non-blocking try-acquire so a busy request rejects
    # immediately rather than hanging the caller for the entire
    # download.
    if not _LICENSE_IMPORT_LOCK.acquire(blocking=False):
        return jsonify({
            'error': 'license-import already in progress',
            'in_flight': dict(_LICENSE_IMPORT_IN_FLIGHT),
            'requested_state': state,
            'hint': 'wait for the current job to finish; check logs for '
                    'COMPLETE: {state: ...} before retrying',
        }), 409

    if run_async:
        import uuid as _uuid
        from datetime import datetime as _dt
        job_id = _uuid.uuid4().hex[:12]
        _LICENSE_IMPORT_IN_FLIGHT['state'] = state
        _LICENSE_IMPORT_IN_FLIGHT['job_id'] = job_id
        _LICENSE_IMPORT_IN_FLIGHT['started_at'] = _dt.utcnow().isoformat()

        def _bg():
            try:
                result = import_state(state)
                print(f"[V237] license-import job {job_id} COMPLETE: {result}",
                      flush=True)
            except Exception as e:
                import traceback as _tb
                print(f"[V237] license-import job {job_id} ERROR: {e}",
                      flush=True)
                _tb.print_exc()
            finally:
                _LICENSE_IMPORT_IN_FLIGHT['state'] = None
                _LICENSE_IMPORT_IN_FLIGHT['job_id'] = None
                _LICENSE_IMPORT_IN_FLIGHT['started_at'] = None
                _LICENSE_IMPORT_LOCK.release()

        threading.Thread(target=_bg, daemon=True,
                         name=f'license_import_{state}').start()
        return jsonify({
            'status': 'started',
            'async': True,
            'job_id': job_id,
            'state': state,
            'message': 'running in background — poll enrichment_log or '
                       'contractor_profiles for progress',
        }), 202

    # Sync path — lock already held; release after the import completes.
    try:
        result = import_state(state)
        return jsonify(result)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e), 'state': state}), 500
    finally:
        _LICENSE_IMPORT_LOCK.release()


@app.route('/api/admin/dashboard', methods=['GET'])
def admin_dashboard():
    """V226 T9: Combined one-stop status page — aggregates everything.

    Returns overall totals, ad-ready buckets, recent collection_log,
    recent enrichment_log, city-health matrix.
    """
    valid, error = check_admin_key()
    if not valid:
        return error

    conn = permitdb.get_connection()
    totals = conn.execute("""
        SELECT
          (SELECT COUNT(*) FROM permits) as permits,
          (SELECT COUNT(*) FROM violations) as violations,
          (SELECT COUNT(*) FROM contractor_profiles) as profiles,
          (SELECT COUNT(*) FROM contractor_profiles
             WHERE (phone IS NOT NULL AND phone != '')
                OR (website IS NOT NULL AND website != '')) as enriched,
          (SELECT COUNT(*) FROM prod_cities WHERE status = 'active') as active_cities,
          (SELECT COUNT(*) FROM prod_cities WHERE status = 'paused') as paused_cities
    """).fetchone()
    totals_dict = {k: totals[k] for k in totals.keys()}
    if totals_dict.get('profiles', 0):
        totals_dict['enrichment_pct'] = round(
            100.0 * (totals_dict.get('enriched') or 0) / totals_dict['profiles'], 1
        )
    else:
        totals_dict['enrichment_pct'] = 0

    # recent collections (last 15)
    recent_collections = []
    try:
        for r in conn.execute("""
            SELECT city_slug, status, records_inserted, api_rows_returned,
                   duplicate_rows_skipped, created_at
            FROM collection_log
            ORDER BY created_at DESC LIMIT 15
        """).fetchall():
            recent_collections.append({k: r[k] for k in r.keys()})
    except Exception:
        pass

    # recent enrichments (last 15)
    recent_enrichments = []
    try:
        for r in conn.execute("""
            SELECT contractor_profile_id, source, status, created_at
            FROM enrichment_log
            ORDER BY created_at DESC LIMIT 15
        """).fetchall():
            recent_enrichments.append({k: r[k] for k in r.keys()})
    except Exception:
        pass

    # ad-ready buckets — reuse city-health internal logic
    top_slugs = [
        'new-york-city','los-angeles','chicago','houston','phoenix',
        'philadelphia','dallas','austin','san-antonio','san-diego',
        'nashville','seattle','san-francisco','fort-worth','columbus',
        'denver','memphis','new-orleans','milwaukee','san-jose',
    ]
    placeholders = ','.join('?' * len(top_slugs))
    health_rows = conn.execute(f"""
        SELECT pc.city_slug, pc.state, pc.status,
               COALESCE(p.cnt, 0) as permits, p.newest as newest_permit,
               COALESCE(v.cnt, 0) as violations,
               COALESCE(cp.cnt, 0) as profiles,
               COALESCE(cp.enriched, 0) as enriched
        FROM prod_cities pc
        LEFT JOIN (
            SELECT source_city_key, COUNT(*) cnt,
                   MAX(COALESCE(NULLIF(filing_date,''),NULLIF(issued_date,''),NULLIF(date,''))) newest
            FROM permits GROUP BY source_city_key
        ) p ON p.source_city_key = pc.city_slug
        LEFT JOIN (
            SELECT prod_city_id, COUNT(*) cnt FROM violations GROUP BY prod_city_id
        ) v ON v.prod_city_id = pc.id
        LEFT JOIN (
            SELECT source_city_key, COUNT(*) cnt,
                   SUM(CASE WHEN (phone IS NOT NULL AND phone != '')
                             OR (website IS NOT NULL AND website != '')
                            THEN 1 ELSE 0 END) enriched
            FROM contractor_profiles GROUP BY source_city_key
        ) cp ON cp.source_city_key = pc.city_slug
        WHERE pc.city_slug IN ({placeholders})
    """, top_slugs).fetchall()

    from datetime import datetime as _dt
    today = _dt.utcnow().date()
    yes, close, no_list = [], [], []
    for r in health_rows:
        slug = r['city_slug']
        if r['status'] != 'active':
            no_list.append(slug)
            continue
        newest = (r['newest_permit'] or '')[:10]
        age = None
        if newest and len(newest) == 10:
            try:
                age = (today - _dt.strptime(newest, '%Y-%m-%d').date()).days
            except Exception:
                age = None
        pct = 0
        if r['profiles']:
            pct = 100.0 * r['enriched'] / r['profiles']
        if r['permits'] > 100 and age is not None and age < 7 and r['violations'] > 0 and pct > 40:
            yes.append(slug)
        elif r['permits'] > 100 and age is not None and age < 14:
            close.append(slug)
        else:
            no_list.append(slug)

    return jsonify({
        'generated_at': _dt.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
        'overall': totals_dict,
        'ad_ready': {
            'yes': yes,
            'close': close,
            'no': no_list,
        },
        'recent_collections': recent_collections,
        'recent_enrichments': recent_enrichments,
    })


@app.route('/admin', methods=['GET'])
@app.route('/admin/dashboard', methods=['GET'])
def admin_dashboard_html():
    """V227 T6: Interactive HTML command center — one page, force-collect
    and force-enrich buttons per city, inline JS that calls the existing
    JSON admin endpoints. Auth via ?key=... query param (same value as
    the X-Admin-Key header; this is admin-only so it isn't sensitive in a
    logged URL relative to the key itself)."""
    key = request.args.get('key') or request.headers.get('X-Admin-Key')
    expected = os.environ.get('ADMIN_KEY')
    if not expected:
        return Response(
            '<h1>503</h1><p>ADMIN_KEY env var not configured on this instance.</p>',
            status=503, mimetype='text/html')
    if key != expected:
        return Response(
            '<h1>401</h1><p>Append ?key=... or send X-Admin-Key header.</p>',
            status=401, mimetype='text/html')

    conn = permitdb.get_connection()
    top_slugs = [
        'new-york-city','los-angeles','chicago','houston','phoenix',
        'philadelphia','dallas','austin','san-antonio','san-diego',
        'nashville','seattle','san-francisco','fort-worth','columbus',
        'denver','memphis','new-orleans','milwaukee','san-jose',
    ]
    placeholders = ','.join('?' * len(top_slugs))
    rows = conn.execute(f"""
        SELECT pc.city_slug, pc.state, pc.status, pc.source_type,
               COALESCE(pc.expected_freshness_days, 14) expected_days,
               COALESCE(pc.needs_attention, 0) needs_attention,
               COALESCE(p.cnt, 0) permits,
               p.newest as newest_permit,
               COALESCE(v.cnt, 0) violations,
               COALESCE(cp.cnt, 0) profiles,
               COALESCE(cp.enriched, 0) enriched
        FROM prod_cities pc
        LEFT JOIN (
            SELECT source_city_key, COUNT(*) cnt,
                   MAX(COALESCE(NULLIF(filing_date,''),NULLIF(issued_date,''),NULLIF(date,''))) newest
            FROM permits GROUP BY source_city_key
        ) p ON p.source_city_key = pc.city_slug
        LEFT JOIN (
            SELECT prod_city_id, COUNT(*) cnt FROM violations GROUP BY prod_city_id
        ) v ON v.prod_city_id = pc.id
        LEFT JOIN (
            SELECT source_city_key, COUNT(*) cnt,
                   SUM(CASE WHEN (phone IS NOT NULL AND phone != '')
                             OR (website IS NOT NULL AND website != '')
                            THEN 1 ELSE 0 END) enriched
            FROM contractor_profiles GROUP BY source_city_key
        ) cp ON cp.source_city_key = pc.city_slug
        WHERE pc.city_slug IN ({placeholders})
        ORDER BY permits DESC
    """, top_slugs).fetchall()

    from datetime import datetime as _dt
    today = _dt.utcnow().date()
    table_rows = []
    for r in rows:
        row = {k: r[k] for k in r.keys()}
        newest = (row.get('newest_permit') or '')[:10]
        age = None
        if newest and len(newest) == 10:
            try:
                age = (today - _dt.strptime(newest, '%Y-%m-%d').date()).days
            except Exception:
                pass
        pct = round(100.0 * (row['enriched'] or 0) / row['profiles'], 1) if row['profiles'] else 0
        expected = row.get('expected_days') or 14
        if row['status'] != 'active':
            status = 'paused'
        elif age is None or row['permits'] == 0:
            status = 'no_data'
        elif age > expected * 3:
            status = 'critical'
        elif age > expected:
            status = 'stale'
        else:
            status = 'healthy'
        table_rows.append({
            'slug': row['city_slug'], 'state': row['state'],
            'status': status, 'days_stale': age,
            'expected': expected, 'permits': row['permits'],
            'violations': row['violations'], 'profiles': row['profiles'],
            'enriched': row['enriched'], 'enrichment_pct': pct,
            'source_type': row.get('source_type'),
            'needs_attention': bool(row.get('needs_attention')),
        })

    # Summary counters
    summary = {s: 0 for s in ('healthy','stale','critical','no_data','paused')}
    for r in table_rows:
        summary[r['status']] = summary.get(r['status'], 0) + 1

    # Render inline (keep it self-contained, no new template file needed)
    from markupsafe import escape
    html_rows = []
    for r in table_rows:
        slug = escape(r['slug'])
        status = r['status']
        attn_badge = ' <span class="attn">!</span>' if r['needs_attention'] else ''
        html_rows.append(
            f"<tr><td>{slug}</td>"
            f"<td class='{status}'>{status.upper()}{attn_badge}</td>"
            f"<td>{r['days_stale'] if r['days_stale'] is not None else '?'}d</td>"
            f"<td>{r['expected']}d</td>"
            f"<td>{escape(r['source_type'] or '?')}</td>"
            f"<td>{r['permits']:,}</td>"
            f"<td>{r['violations']:,}</td>"
            f"<td>{r['profiles']:,}</td>"
            f"<td>{r['enrichment_pct']}%</td>"
            f"<td><button class='btn-collect' onclick=\"forceCollect('{slug}')\">Collect</button>"
            f"<button class='btn-enrich' onclick=\"enrich('{slug}')\">Enrich</button></td></tr>"
        )

    html = f"""<!DOCTYPE html>
<html><head><title>PermitGrab Admin · Command Center</title>
<meta name="robots" content="noindex, nofollow">
<style>
*{{box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,sans-serif;margin:0;padding:20px;background:#0f172a;color:#e2e8f0}}
h1{{margin:0 0 4px;color:#f1f5f9}}
.sub{{color:#94a3b8;font-size:14px;margin-bottom:18px}}
.summary{{display:flex;gap:12px;margin:16px 0}}
.stat{{background:#1e293b;padding:14px 20px;border-radius:10px;min-width:120px;border:1px solid #334155}}
.stat .num{{font-size:28px;font-weight:700;display:block}}
.stat .lbl{{font-size:12px;text-transform:uppercase;letter-spacing:1px;color:#94a3b8}}
.healthy{{color:#4ade80}}.stale{{color:#facc15}}.critical{{color:#f87171}}
.no_data{{color:#94a3b8}}.paused{{color:#64748b}}
.attn{{display:inline-block;background:#dc2626;color:white;border-radius:50%;
      width:16px;height:16px;line-height:16px;text-align:center;font-size:11px;margin-left:6px}}
table{{border-collapse:collapse;width:100%;background:#1e293b;border-radius:10px;overflow:hidden}}
th,td{{padding:10px 14px;border-bottom:1px solid #334155;text-align:left}}
th{{background:#0f172a;color:#cbd5e1;font-size:12px;text-transform:uppercase;letter-spacing:1px}}
tr:last-child td{{border-bottom:none}}
button{{padding:5px 12px;border:none;border-radius:5px;cursor:pointer;font-size:12px;font-weight:600;margin-right:4px}}
.btn-collect{{background:#2563eb;color:white}}
.btn-enrich{{background:#8b5cf6;color:white}}
.btn-collect:hover{{background:#1d4ed8}}.btn-enrich:hover{{background:#7c3aed}}
#log{{background:#020617;padding:12px;margin-top:18px;border-radius:8px;
     max-height:320px;overflow-y:auto;font-family:Menlo,monospace;font-size:12px;
     border:1px solid #334155}}
.log-line{{padding:2px 0;color:#94a3b8}}
.log-line.ok{{color:#4ade80}}
.log-line.err{{color:#f87171}}
</style></head><body>
<h1>PermitGrab · Command Center</h1>
<p class="sub">V227 · {_dt.utcnow().strftime('%Y-%m-%d %H:%M UTC')}</p>
<div class="summary">
  <div class="stat"><span class="num healthy">{summary['healthy']}</span><span class="lbl">Healthy</span></div>
  <div class="stat"><span class="num stale">{summary['stale']}</span><span class="lbl">Stale</span></div>
  <div class="stat"><span class="num critical">{summary['critical']}</span><span class="lbl">Critical</span></div>
  <div class="stat"><span class="num no_data">{summary['no_data']}</span><span class="lbl">No Data</span></div>
  <div class="stat"><span class="num paused">{summary['paused']}</span><span class="lbl">Paused</span></div>
</div>
<table>
<tr><th>City</th><th>Status</th><th>Stale</th><th>Expected</th><th>Source</th>
  <th>Permits</th><th>Violations</th><th>Profiles</th><th>Enrich%</th><th>Actions</th></tr>
{''.join(html_rows)}
</table>
<div id="log"><div class="log-line">Ready.</div></div>
<script>
const KEY = new URLSearchParams(location.search).get('key') || '';
function log(msg, cls){{
  const l = document.getElementById('log');
  const d = document.createElement('div');
  d.className = 'log-line' + (cls ? ' '+cls : '');
  d.textContent = new Date().toLocaleTimeString() + ' ' + msg;
  l.prepend(d);
}}
async function forceCollect(city){{
  log('→ collecting '+city);
  try{{
    const r = await fetch('/api/admin/force-collect?city_slug='+encodeURIComponent(city)+'&type=both', {{
      method: 'GET', headers: {{'X-Admin-Key': KEY}}
    }});
    const d = await r.json();
    log(city+': '+d.status+' permits+'+d.permits_inserted+' viols+'+(d.violations_inserted||0)+' ('+d.elapsed_seconds+'s)',
        r.ok ? 'ok' : 'err');
  }}catch(e){{ log(city+': '+e, 'err') }}
}}
async function enrich(city){{
  log('→ enriching '+city);
  try{{
    const r = await fetch('/api/admin/enrich', {{
      method: 'POST',
      headers: {{'X-Admin-Key': KEY, 'Content-Type': 'application/json'}},
      body: JSON.stringify({{city_slug: city, batch_size: 20}})
    }});
    const d = await r.json();
    log(city+': '+d.enriched+'/'+d.attempted+' enriched ('+d.elapsed_seconds+'s)',
        r.ok ? 'ok' : 'err');
  }}catch(e){{ log(city+': '+e, 'err') }}
}}
</script>
</body></html>"""
    return Response(html, mimetype='text/html')


@app.route('/api/admin/add-source', methods=['POST'])
def admin_add_source():
    """V12.50: Add a single source and upsert to SQLite."""
    valid, error = check_admin_key()
    if not valid:
        return error

    source_key = request.args.get('source')
    source_type = request.args.get('type', 'bulk')  # 'bulk' or 'city'

    if not source_key:
        return jsonify({'error': 'Missing source parameter. Usage: ?source=nj_statewide&type=bulk'}), 400

    # V229 A3: renamed from run_collection
    def _run_single_source_collection():
        try:
            from collector import collect_single_source
            print(f"[Admin] Adding single source: {source_key} ({source_type})...")
            collect_single_source(source_key, source_type)
            print(f"[Admin] Source {source_key} added successfully.")
        except Exception as e:
            print(f"[Admin] Add source error: {e}")

    thread = threading.Thread(target=_run_single_source_collection, daemon=True)
    thread.start()

    return jsonify({
        'message': f'Adding source: {source_key} ({source_type})',
        'note': 'V12.50: Data written directly to SQLite'
    })


@app.route('/api/admin/collection-status')
def admin_collection_status():
    """V12.29: Get last collection run status for debugging."""
    valid, error = check_admin_key()
    if not valid:
        return error

    stats_file = os.path.join(DATA_DIR, "collection_stats.json")
    if not os.path.exists(stats_file):
        return jsonify({'error': 'No collection stats found', 'path': stats_file}), 404

    try:
        with open(stats_file) as f:
            stats = json.load(f)

        # Calculate summary
        city_stats = stats.get('city_stats', {})
        total_cities = len(city_stats)
        cities_with_permits = sum(1 for s in city_stats.values() if s.get('normalized', 0) > 0)
        cities_empty = sum(1 for s in city_stats.values() if s.get('status') == 'success_empty')
        cities_errored = sum(1 for s in city_stats.values() if 'error' in str(s.get('status', '')))
        cities_timeout = sum(1 for s in city_stats.values() if 'timeout' in str(s.get('status', '').lower()))

        # Get list of failed cities
        failed_cities = [
            {'city': k, 'name': v.get('city_name', k), 'status': v.get('status', 'unknown')}
            for k, v in city_stats.items()
            if 'error' in str(v.get('status', '')) or 'timeout' in str(v.get('status', '').lower())
        ]

        return jsonify({
            'collected_at': stats.get('collected_at'),
            'total_permits': stats.get('total_permits', 0),
            'summary': {
                'total_cities_attempted': total_cities,
                'cities_with_permits': cities_with_permits,
                'cities_empty': cities_empty,
                'cities_errored': cities_errored,
                'cities_timeout': cities_timeout,
            },
            'failed_cities': failed_cities[:50],  # Limit to 50 to avoid huge response
            'trade_breakdown': stats.get('trade_breakdown', {}),
        })
    except Exception as e:
        return jsonify({'error': f'Failed to read stats: {str(e)}'}), 500


@app.route('/api/admin/start-collectors', methods=['POST'])
def admin_start_collectors():
    """V69: Manually start background threads after server is stable.

    Since V69 disables all automatic background threads on startup,
    use this endpoint to manually trigger them when ready.
    """
    valid, error = check_admin_key()
    if not valid:
        return error

    global _collectors_manually_started

    try:
        if _collectors_manually_started:
            return jsonify({'status': 'already_running', 'message': 'Collectors already started'}), 200

        import threading

        def _run_collectors():
            print(f"[{datetime.now()}] V69: Manual start_collectors triggered via API")
            start_collectors()

        t = threading.Thread(target=_run_collectors, daemon=True)
        t.start()
        _collectors_manually_started = True

        return jsonify({
            'status': 'started',
            'message': 'Background collectors started in separate thread'
        }), 200

    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/admin/refresh-profiles', methods=['POST'])
def admin_refresh_profiles():
    """V182: Rebuild contractor_profiles aggregates + emblem flags.

    Query params:
      city: specific prod_cities.city_slug to refresh (optional; default = all active)

    Also triggers update_city_emblems() after refresh so has_enrichment
    flag stays in sync on prod_cities.
    """
    valid, error = check_admin_key()
    if not valid:
        return error
    try:
        from contractor_profiles import refresh_contractor_profiles, update_city_emblems
        city_slug = request.args.get('city')
        result = refresh_contractor_profiles(city_slug=city_slug)
        emblems = update_city_emblems()
        return jsonify({'status': 'ok', 'emblems': emblems, **result}), 200
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/admin/refresh-emblems', methods=['POST'])
def admin_refresh_emblems():
    """V182: Recompute has_enrichment/has_violations flags on prod_cities.

    Call after violation collection. Fast (<1s for 2K cities).
    """
    valid, error = check_admin_key()
    if not valid:
        return error
    try:
        from contractor_profiles import update_city_emblems
        return jsonify({'status': 'ok', **update_city_emblems()}), 200
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/admin/enrich-contractors', methods=['POST'])
def admin_enrich_contractors():
    """V182: Enrich contractor_profiles for one city via Google Places.

    Gated on GOOGLE_PLACES_API_KEY env var — returns 'skipped_no_api_key' if
    unset (no PR-merge-time failure mode). Call per-city with a cost cap.

    Query params:
      city: required — prod_cities.city_slug
      max_cost: optional $ cap (default $25)
    """
    valid, error = check_admin_key()
    if not valid:
        return error
    city_slug = request.args.get('city')
    if not city_slug:
        return jsonify({'error': 'Missing city parameter'}), 400
    try:
        max_cost = float(request.args.get('max_cost', '25'))
    except ValueError:
        return jsonify({'error': 'max_cost must be a number'}), 400
    try:
        from contractor_profiles import enrich_city_profiles
        result = enrich_city_profiles(city_slug=city_slug, max_cost=max_cost)
        return jsonify({'status': 'ok', **result}), 200
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/admin/reclassify-general', methods=['POST'])
def admin_reclassify_general():
    """V182 T3: Re-run classify_trade on permits currently tagged 'General Construction'.

    Only touches rows where the current trade_category is 'General Construction'.
    Expands TRADE_CATEGORIES hits (insulation, drywall, tile, etc.) to more
    specific trades. Batched updates, 1000 permits per transaction.

    Never downgrades already-specific trades; only upgrades General Construction.
    """
    valid, error = check_admin_key()
    if not valid:
        return error
    try:
        from collector import classify_trade
        conn = permitdb.get_connection()
        total_scanned = 0
        total_updated = 0
        batch_size = 1000
        updated_by_trade = {}

        while True:
            rows = conn.execute("""
                SELECT permit_number, description, work_type, permit_type
                FROM permits
                WHERE trade_category = 'General Construction'
                  AND (description IS NOT NULL AND description != '')
                LIMIT ?
                OFFSET ?
            """, (batch_size, total_scanned)).fetchall()
            if not rows:
                break
            for r in rows:
                text = ' '.join(filter(None, [r['description'], r['work_type'], r['permit_type']]))
                new_trade = classify_trade(text)
                if new_trade and new_trade != 'General Construction':
                    conn.execute(
                        "UPDATE permits SET trade_category = ? WHERE permit_number = ?",
                        (new_trade, r['permit_number']),
                    )
                    total_updated += 1
                    updated_by_trade[new_trade] = updated_by_trade.get(new_trade, 0) + 1
            conn.commit()
            total_scanned += len(rows)
            # If this batch was smaller than batch_size, we're done.
            if len(rows) < batch_size:
                break
        conn.close()
        return jsonify({
            'status': 'ok',
            'scanned': total_scanned,
            'updated': total_updated,
            'breakdown': updated_by_trade,
        }), 200
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/admin/recount-permits', methods=['POST'])
def admin_recount_permits():
    """V100: Recount total_permits from actual permits table."""
    valid, error = check_admin_key()
    if not valid:
        return error
    try:
        from collector import update_total_permits_from_actual
        updated = update_total_permits_from_actual()
        return jsonify({'status': 'ok', 'updated': updated}), 200
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500




@app.route('/api/admin/test-search', methods=['POST'])
def admin_test_search():
    """V111b: Test the search pipeline for a single city without saving."""
    valid, error = check_admin_key()
    if not valid:
        return error

    data = request.json or {}
    city = data.get('city', 'Oakland')
    state = data.get('state', 'CA')

    results = {'city': city, 'state': state, 'url_pattern_hits': [],
               'state_portal_hits': [], 'errors': []}

    # Phase 1: URL patterns
    try:
        from city_onboarding import _try_url_patterns
        url_hits = _try_url_patterns(city, state)
        results['url_pattern_hits'] = [{'url': h['url'], 'platform': h.get('platform', ''),
                                         'title': h.get('title', ''),
                                         'dataset_id': h.get('dataset_id', '')} for h in url_hits]
    except Exception as e:
        results['errors'].append(f"URL pattern error: {str(e)}")

    # Phase 2: State portal search (V113 — replaces DDG)
    try:
        from city_onboarding import _search_state_portal
        state_hits = _search_state_portal(state, city)
        results['state_portal_hits'] = [{'url': h['url'], 'domain': h.get('domain', ''),
                                          'dataset_id': h.get('dataset_id', ''),
                                          'city_column': h.get('city_column', ''),
                                          'title': h.get('title', '')} for h in state_hits]
    except Exception as e:
        results['errors'].append(f"State portal error: {str(e)}")

    return jsonify(results), 200




@app.route('/api/admin/pause-never-worked', methods=['POST'])
def admin_pause_never_worked():
    """V118: Pause cities with source assignments that never produced data."""
    valid, error = check_admin_key()
    if not valid:
        return error
    try:
        conn = permitdb.get_connection()
        paused = conn.execute("""
            UPDATE prod_cities SET status = 'paused',
                pause_reason = 'V118: source never produced data'
            WHERE status = 'active' AND health_status = 'never_worked'
            AND total_permits = 0 AND last_successful_collection IS NULL
            AND source_type IS NOT NULL
        """).rowcount
        conn.commit()

        active = conn.execute("SELECT count(*) FROM prod_cities WHERE status = 'active'").fetchone()[0]
        print(f"V118: Paused {paused} never-worked cities, {active} remaining active", flush=True)
        return jsonify({'status': 'complete', 'paused': paused, 'remaining_active': active}), 200
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/admin/create-permits-index', methods=['POST'])
def admin_create_permits_index():
    """V118: Create index for fast city/state/collected_at lookups."""
    valid, error = check_admin_key()
    if not valid:
        return error
    try:
        conn = permitdb.get_connection()
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_permits_city_state_collected
            ON permits(city, state, collected_at)
        """)
        conn.commit()
        print("V118: Created permits city/state/collected_at index", flush=True)
        return jsonify({'status': 'complete'}), 200
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/admin/add-verified-cities', methods=['POST'])
def admin_add_verified_cities():
    """V121: Add cities with manually verified and tested endpoints."""
    valid, error = check_admin_key()
    if not valid:
        return error
    try:
        conn = permitdb.get_connection()

        # V121: Verified and tested endpoints (manually confirmed data is correct)
        cities = [
            # V119 originals (DC confirmed working with 2K+ permits)
            {
                'source_key': 'dc-arcgis', 'slug': 'washington',
                'name': 'Washington', 'state': 'DC', 'platform': 'arcgis',
                'endpoint': 'https://maps2.dcgis.dc.gov/dcgis/rest/services/FEEDS/DCRA/FeatureServer/4',
                'dataset_id': '',
            },
            # V121: Milwaukee — CKAN, 16K+ records, addresses confirmed Milwaukee WI
            {
                'source_key': 'milwaukee-ckan', 'slug': 'milwaukee',
                'name': 'Milwaukee', 'state': 'WI', 'platform': 'ckan',
                'endpoint': 'https://data.milwaukee.gov/api/3/action/datastore_search',
                'dataset_id': '828e9630-d7cb-42e4-960e-964eae916397',
            },
            # V121: Virginia Beach — ArcGIS FeatureServer, PermitNumber+StreetAddress confirmed
            {
                'source_key': 'virginia-beach-arcgis', 'slug': 'virginia-beach',
                'name': 'Virginia Beach', 'state': 'VA', 'platform': 'arcgis',
                'endpoint': 'https://services2.arcgis.com/CyVvlIiUfRBmMQuu/arcgis/rest/services/Building_Permits_Applications_view/FeatureServer/0',
                'dataset_id': '',
            },
            # V121: Gilbert AZ — ArcGIS MapServer, AddressCity=GILBERT confirmed
            {
                'source_key': 'gilbert-arcgis', 'slug': 'gilbert',
                'name': 'Gilbert', 'state': 'AZ', 'platform': 'arcgis',
                'endpoint': 'https://maps.gilbertaz.gov/arcgis/rest/services/OD/Growth_Development_Tables_1/MapServer/3',
                'dataset_id': '',
            },
            # V119: Albuquerque — ArcGIS FeatureServer, verified endpoint
            {
                'source_key': 'albuquerque-nm-arcgis', 'slug': 'albuquerque',
                'name': 'Albuquerque', 'state': 'NM', 'platform': 'arcgis',
                'endpoint': 'https://coageo.cabq.gov/cabqgeo/rest/services/agis/City_Building_Permits/FeatureServer/0',
                'dataset_id': '',
            },
            # V119: St. Paul — Socrata
            {
                'source_key': 'saint-paul-mn-socrata', 'slug': 'saint-paul',
                'name': 'St. Paul', 'state': 'MN', 'platform': 'socrata',
                'endpoint': 'https://information.stpaul.gov/resource/j8ip-eytd.json',
                'dataset_id': 'j8ip-eytd',
            },
        ]

        added = 0
        for c in cities:
            try:
                # Upsert city_sources
                conn.execute("""
                    INSERT OR REPLACE INTO city_sources
                    (source_key, name, state, platform, endpoint, dataset_id, status)
                    VALUES (?, ?, ?, ?, ?, ?, 'active')
                """, (c['source_key'], c['name'], c['state'], c['platform'],
                      c['endpoint'], c['dataset_id']))

                # Find and update prod_cities — try slug first, then city name + state
                updated = conn.execute("""
                    UPDATE prod_cities SET source_type = ?, source_id = ?,
                    source_endpoint = ?, status = 'active', health_status = 'collecting',
                    consecutive_failures = 0
                    WHERE city_slug = ? OR (LOWER(city) = LOWER(?) AND state = ?)
                """, (c['platform'], c['source_key'], c['endpoint'],
                      c['slug'], c['name'], c['state'])).rowcount

                if updated:
                    added += 1
                    print(f"V119: Added {c['name']}, {c['state']} ({c['platform']})", flush=True)
                else:
                    print(f"V119: No prod_cities match for {c['slug']}", flush=True)
            except Exception as e:
                print(f"V119: Error adding {c['slug']}: {e}", flush=True)

        conn.commit()
        return jsonify({'status': 'complete', 'added': added, 'total': len(cities)}), 200
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/admin/test-city-collection', methods=['POST'])
def admin_test_city_collection():
    """V119: Test collection for a single city."""
    valid, error = check_admin_key()
    if not valid:
        return error
    try:
        data = request.json or {}
        city_slug = data.get('city_slug')
        days_back = data.get('days_back', 180)
        if not city_slug:
            return jsonify({'error': 'city_slug required'}), 400

        from collector import collect_single_city
        result = collect_single_city(city_slug, days_back=days_back)
        return jsonify({'city_slug': city_slug, 'result': result}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500




@app.route('/api/admin/city-research', methods=['GET'])
def admin_city_research_get():
    """V148: List city research entries."""
    valid, error = check_admin_key()
    if not valid:
        return error
    try:
        conn = permitdb.get_connection()
        status_filter = request.args.get('status')
        limit = int(request.args.get('limit', 50))
        if status_filter:
            rows = conn.execute("SELECT * FROM city_research WHERE status=? ORDER BY population DESC LIMIT ?", (status_filter, limit)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM city_research ORDER BY population DESC LIMIT ?", (limit,)).fetchall()
        conn.close()
        return jsonify({'count': len(rows), 'rows': [dict(r) for r in rows]}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/city-research', methods=['POST'])
def admin_city_research_post():
    """V148: Upsert a city research entry."""
    valid, error = check_admin_key()
    if not valid:
        return error
    try:
        data = request.get_json() or {}
        city = data.get('city')
        state = data.get('state')
        if not city or not state:
            return jsonify({'error': 'city and state are required'}), 400
        conn = permitdb.get_connection()
        existing = conn.execute("SELECT id FROM city_research WHERE city=? AND state=?", (city, state)).fetchone()
        if existing:
            sets = []
            vals = []
            for col in ['population','status','portal_url','dataset_id','platform','date_field','address_field','notes','tested_at','onboarded_at']:
                if col in data:
                    sets.append(f"{col}=?")
                    vals.append(data[col])
            if sets:
                vals.extend([city, state])
                conn.execute(f"UPDATE city_research SET {','.join(sets)} WHERE city=? AND state=?", vals)
        else:
            conn.execute("""INSERT INTO city_research (city, state, population, status, portal_url, dataset_id, platform, date_field, address_field, notes)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (city, state, data.get('population',0), data.get('status','untested'), data.get('portal_url'),
                 data.get('dataset_id'), data.get('platform'), data.get('date_field'), data.get('address_field'), data.get('notes')))
        conn.commit()
        row = conn.execute("SELECT * FROM city_research WHERE city=? AND state=?", (city, state)).fetchone()
        conn.close()
        return jsonify(dict(row)), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/city-research', methods=['DELETE'])
def admin_city_research_delete():
    """V148: Delete a city research entry."""
    valid, error = check_admin_key()
    if not valid:
        return error
    try:
        city = request.args.get('city')
        state = request.args.get('state')
        if not city or not state:
            return jsonify({'error': 'city and state query params required'}), 400
        conn = permitdb.get_connection()
        r = conn.execute("DELETE FROM city_research WHERE city=? AND state=?", (city, state)).rowcount
        conn.commit()
        conn.close()
        return jsonify({'deleted': r}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500




@app.route('/api/admin/config-audit')
def admin_config_audit():
    """REARCH: Audit config sources — shows gap between dicts and city_sources table."""
    valid, error = check_admin_key()
    if not valid:
        return error
    try:
        from city_configs import CITY_REGISTRY, BULK_SOURCES
        conn = permitdb.get_connection()

        reg_active = sum(1 for v in CITY_REGISTRY.values() if v.get('active', False))
        bulk_active = sum(1 for v in BULK_SOURCES.values() if v.get('active', True))
        cs_city = conn.execute("SELECT count(*) FROM city_sources WHERE mode = 'city' AND status = 'active'").fetchone()[0]
        cs_bulk = conn.execute("SELECT count(*) FROM city_sources WHERE mode = 'bulk' AND status = 'active'").fetchone()[0]

        # Find CITY_REGISTRY keys not in city_sources
        cs_keys = {r[0] for r in conn.execute("SELECT source_key FROM city_sources").fetchall()}
        reg_missing = [k for k in CITY_REGISTRY if CITY_REGISTRY[k].get('active', False) and k not in cs_keys]
        bulk_missing = [k for k in BULK_SOURCES if BULK_SOURCES[k].get('active', True) and k not in cs_keys]

        return jsonify({
            'city_registry_active': reg_active,
            'bulk_sources_active': bulk_active,
            'city_sources_city': cs_city,
            'city_sources_bulk': cs_bulk,
            'registry_not_in_db': len(reg_missing),
            'bulk_not_in_db': len(bulk_missing),
            'sample_missing_registry': reg_missing[:20],
            'sample_missing_bulk': bulk_missing[:10],
        }), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500




@app.route('/api/admin/cleanup-contamination', methods=['POST'])
def admin_cleanup_contamination():
    """REARCH-FIX: Remove permits from wrong-city onboard runs and reset cities."""
    valid, error = check_admin_key()
    if not valid:
        return error
    try:
        conn = permitdb.get_connection()
        bad_slugs = ['las-vegas', 'saint-paul', 'san-jose', 'oklahoma-city', 'district-of-columbia-dc']
        total_deleted = 0

        for slug in bad_slugs:
            # Delete permits inserted by onboard (after April 9)
            d = conn.execute("""
                DELETE FROM permits WHERE source_city_key = ?
                AND collected_at > '2026-04-09 00:00'
            """, (slug,)).rowcount
            total_deleted += d

        # Reset prod_cities
        conn.execute("""
            UPDATE prod_cities SET status = 'pending', source_type = NULL, source_id = NULL,
            total_permits = 0, last_successful_collection = NULL, health_status = 'unknown'
            WHERE city_slug IN ('las-vegas', 'saint-paul', 'san-jose', 'oklahoma-city', 'district-of-columbia-dc')
        """)

        # Delete bad city_sources
        conn.execute("""
            DELETE FROM city_sources WHERE source_key IN (
                'las-vegas-socrata', 'saint-paul-socrata', 'san-jose-socrata',
                'oklahoma-city-socrata', 'district-of-columbia-dc-socrata',
                'las-vegas-arcgis', 'saint-paul-arcgis', 'san-jose-arcgis',
                'oklahoma-city-arcgis', 'district-of-columbia-dc-arcgis'
            )
        """)
        conn.commit()
        print(f"REARCH-FIX: Cleanup: {total_deleted} contaminated permits deleted, 5 cities reset", flush=True)
        return jsonify({'status': 'complete', 'permits_deleted': total_deleted, 'cities_reset': bad_slugs}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/onboard-next', methods=['POST'])
def admin_onboard_next():
    """V121: Process next N cities by population. Google-test-add."""
    valid, error = check_admin_key()
    if not valid:
        return error
    data = request.json or {}
    count = min(data.get('count', 1), 10)

    conn = permitdb.get_connection()
    candidates = conn.execute("""
        SELECT city_slug, city, state, population FROM prod_cities
        WHERE population >= 12907
        AND city_slug NOT IN (SELECT DISTINCT source_city_key FROM permits WHERE collected_at > datetime('now', '-7 days') AND source_city_key IS NOT NULL)
        AND (health_status IS NULL OR health_status != 'no_source_found')
        ORDER BY population DESC LIMIT ?
    """, (count,)).fetchall()

    try:
        from onboard import onboard_single_city
    except ImportError as e:
        return jsonify({'error': f'onboard module not available: {e}'}), 500
    results = []
    for row in candidates:
        result = onboard_single_city(row[0], row[1], row[2], row[3])
        results.append(result)
        time.sleep(2)

    successes = sum(1 for r in results if r.get('outcome') == 'success')
    return jsonify({'processed': len(results), 'successes': successes, 'results': results}), 200


@app.route('/api/admin/onboard-city', methods=['POST'])
def admin_onboard_city():
    """V121: Google it, test it, add it. Uses onboard.py module.

    Body: {"city_slug": "las-vegas"} or {"city_slugs": [...]} or {"top_pending": 20}
    """
    valid, error = check_admin_key()
    if not valid:
        return error

    try:
        from onboard import onboard_single_city
    except ImportError as e:
        return jsonify({'error': f'onboard module not available: {e}'}), 500
    data = request.json or {}
    conn = permitdb.get_connection()

    # Build city list
    city_slugs = data.get('city_slugs', [])
    if data.get('city_slug'):
        city_slugs = [data['city_slug']]
    if data.get('top_pending'):
        limit = min(int(data['top_pending']), 50)
        rows = conn.execute("""
            SELECT city_slug FROM prod_cities
            WHERE (status IN ('pending', 'paused') OR (status = 'active' AND total_permits = 0))
            AND population >= 12907 AND health_status != 'no_source'
            ORDER BY population DESC LIMIT ?
        """, (limit,)).fetchall()
        city_slugs = [r[0] for r in rows]

    if not city_slugs:
        return jsonify({'error': 'No cities to process'}), 400

    results = []
    for slug in city_slugs:
        city = conn.execute(
            "SELECT city, state, population, total_permits FROM prod_cities WHERE city_slug = ?",
            (slug,)
        ).fetchone()
        if not city:
            results.append({'city_slug': slug, 'outcome': 'not_found'})
            continue
        city_name, state, pop, tp = city[0], city[1], city[2], city[3]

        if tp and tp > 100:
            results.append({'city_slug': slug, 'city': city_name, 'outcome': 'already_has_data', 'permits': tp})
            continue

        # V121: Use the new onboard module
        result = onboard_single_city(slug, city_name, state, pop)
        results.append(result)
        time.sleep(2)

    successes = sum(1 for r in results if r.get('outcome') == 'success')
    total_inserted = sum(r.get('permits_inserted', 0) for r in results)
    return jsonify({
        'summary': {'successes': successes, 'total_permits': total_inserted, 'total': len(city_slugs)},
        'results': results
    }), 200


def _test_source(source, city_name, state):
    """REARCH-FIX: Pull data and return BOTH raw records and normalized permits.
    Returns (raw_records, permits_list, error). Raw records are for validation."""
    import requests as req
    from datetime import datetime as dt, timedelta

    platform = source['platform']
    try:
        if platform == 'socrata':
            endpoint = source['endpoint']
            date_field = source.get('date_field')
            params = {'$limit': 5000, '$order': ':id'}
            if date_field:
                since = (dt.utcnow() - timedelta(days=180)).strftime('%Y-%m-%dT00:00:00')
                params['$where'] = f"{date_field} >= '{since}'"
            r = req.get(endpoint, params=params, timeout=30)
            if not r.ok:
                return [], [], f"HTTP {r.status_code}"
            raw = r.json()
            permits = [_normalize_permit(row, city_name, state) for row in raw]
            permits = [p for p in permits if p]
            return raw, permits, None

        elif platform == 'arcgis':
            endpoint = source['endpoint']
            query_url = f"{endpoint}/query" if '/query' not in endpoint else endpoint
            params = {'where': '1=1', 'outFields': '*', 'resultRecordCount': 2000,
                      'f': 'json', 'orderByFields': 'OBJECTID DESC'}
            r = req.get(query_url, params=params, timeout=30)
            if not r.ok:
                return [], [], f"HTTP {r.status_code}"
            data = r.json()
            if data.get('error'):
                return [], [], f"ArcGIS error: {data['error'].get('message', '')}"
            features = data.get('features', [])
            raw = [f.get('attributes', {}) for f in features]
            permits = [_normalize_permit(attrs, city_name, state) for attrs in raw]
            permits = [p for p in permits if p]
            return raw, permits, None

        return [], [], f"Unknown platform: {platform}"
    except Exception as e:
        return [], [], str(e)[:200]


def _normalize_permit(raw, city_name, state):
    """V120: Flexible field mapping for any permit data source."""
    from datetime import datetime as dt

    permit = {'city': city_name, 'state': state, 'collected_at': dt.utcnow().isoformat()}

    # Permit number
    for k in ['permit_number', 'permitnumber', 'FOLDERNUMBER', 'PERMIT_ID', 'APNO',
              'PermitNumber', 'permit_no', 'PERMIT_NUMBER', 'record_id', 'case_number',
              'APPLICATION_NUMBER', 'permit_id', 'permitno']:
        v = raw.get(k) or raw.get(k.lower()) or raw.get(k.upper())
        if v:
            permit['permit_number'] = str(v).strip()
            break
    if not permit.get('permit_number'):
        for k, v in raw.items():
            kl = k.lower()
            if ('permit' in kl or 'record' in kl) and ('num' in kl or 'no' in kl or 'id' in kl) and v:
                permit['permit_number'] = str(v).strip()
                break
    if not permit.get('permit_number'):
        return None

    # Description
    for k in ['description', 'DESCRIPTION', 'WORKDESCRIPTION', 'WorkDescription',
              'DESC_OF_WORK', 'WORKDESC', 'permit_type', 'FOLDERDESC', 'worktype']:
        v = raw.get(k) or raw.get(k.lower()) or raw.get(k.upper())
        if v:
            permit['description'] = str(v).strip()[:500]
            break

    # Address
    for k in ['address', 'ADDRESS', 'FULL_ADDRESS', 'CalculatedAddress', 'ADDR',
              'site_address', 'staddress', 'street_address', 'location']:
        v = raw.get(k) or raw.get(k.lower()) or raw.get(k.upper())
        if v:
            permit['address'] = str(v).strip()[:300]
            break

    # Date
    for k in ['issued_date', 'issueddate', 'ISSUE_DATE', 'DateIssued', 'ISSUE_DT',
              'ISSUEDATE', 'permit_date', 'date_issued', 'applicationdate', 'permitdate']:
        v = raw.get(k) or raw.get(k.lower()) or raw.get(k.upper())
        if v:
            if isinstance(v, (int, float)) and v > 1e12:
                permit['filing_date'] = dt.utcfromtimestamp(v / 1000).strftime('%Y-%m-%d')
            elif isinstance(v, str) and v.strip():
                permit['filing_date'] = v.strip()[:26]
            break

    # Contractor
    for k in ['contractor', 'CONTRACTOR', 'contractor_name', 'contractorname']:
        v = raw.get(k) or raw.get(k.lower()) or raw.get(k.upper())
        if v:
            permit['contractor_name'] = str(v).strip()[:200]
            break

    # Cost
    for k in ['estimated_cost', 'PERMITVALUATION', 'Valuation', 'FEES_PAID',
              'job_value', 'construction_value', 'value', 'VALUATION']:
        v = raw.get(k) or raw.get(k.lower()) or raw.get(k.upper())
        if v:
            try:
                permit['estimated_cost'] = float(str(v).replace('$', '').replace(',', ''))
            except (ValueError, TypeError):
                pass
            break

    return permit


def _add_city_permits(conn, source, source_key, permits, slug, city_name, state):
    """V120: Insert permits and activate city. Only called after test succeeds."""
    # Upsert city_sources
    conn.execute("""
        INSERT OR REPLACE INTO city_sources
        (source_key, name, state, platform, endpoint, dataset_id, date_field,
         mode, status, limit_per_page, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'city', 'active', 2000, datetime('now'), datetime('now'))
    """, (source_key, city_name, state, source['platform'],
          source['endpoint'], source.get('dataset_id', ''), source.get('date_field', '')))

    # Insert permits
    inserted = 0
    for p in permits:
        try:
            pn = p.get('permit_number', '')
            if not pn:
                continue
            conn.execute("""
                INSERT OR IGNORE INTO permits
                (permit_number, city, state, address, description, filing_date, date,
                 estimated_cost, contractor_name, source_city_key, collected_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """, (pn, city_name, state, p.get('address', ''), p.get('description', ''),
                  p.get('filing_date', ''), p.get('filing_date', ''),
                  p.get('estimated_cost', 0), p.get('contractor_name', ''), slug))
            inserted += 1
        except Exception:
            continue
    conn.commit()

    # Activate city
    conn.execute("""
        UPDATE prod_cities SET source_type = ?, source_id = ?, status = 'active',
        health_status = 'collecting', total_permits = total_permits + ?,
        last_successful_collection = datetime('now')
        WHERE city_slug = ?
    """, (source['platform'], source_key, inserted, slug))
    conn.commit()
    return inserted




@app.route('/api/admin/sweep-status')
def admin_sweep_status():
    """V114: Check catalog sweep results."""
    valid, error = check_admin_key()
    if not valid:
        return error
    try:
        conn = permitdb.get_connection()
        stats = conn.execute("""
            SELECT status, COUNT(*) as cnt, SUM(permits_found) as permits
            FROM sweep_sources GROUP BY status ORDER BY cnt DESC
        """).fetchall()
        recent = conn.execute("""
            SELECT city_slug, platform, name, permits_found, status, discovered_at
            FROM sweep_sources WHERE status = 'confirmed'
            ORDER BY discovered_at DESC LIMIT 30
        """).fetchall()
        return jsonify({
            'stats': [{'status': r[0], 'count': r[1], 'permits': r[2]} for r in stats],
            'confirmed': [{'slug': r[0], 'platform': r[1], 'name': r[2],
                           'permits': r[3], 'discovered': r[5]} for r in recent]
        }), 200
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500




@app.route('/api/admin/city-health-legacy')
def admin_city_health_legacy():
    """V100: Legacy city-health summary — health_status buckets + stale list
    + never-worked platforms. Kept under a new path because V226 repurposed
    the /api/admin/city-health route for the 20-top-cities per-row rollup
    used by the /admin HTML dashboard. Same two endpoints collided at
    deploy time (AssertionError: endpoint admin_city_health already
    registered) which is what V228 is fixing.
    """
    valid, error = check_admin_key()
    if not valid:
        return error
    try:
        conn = permitdb.get_connection()

        summary = conn.execute("""
            SELECT health_status, COUNT(*) as cnt,
                   SUM(total_permits) as permits,
                   AVG(days_since_new_data) as avg_days_stale
            FROM prod_cities
            WHERE status = 'active'
            GROUP BY health_status
            ORDER BY cnt DESC
        """).fetchall()

        stale = conn.execute("""
            SELECT city, state, city_slug, total_permits, latest_permit_date,
                   days_since_new_data, last_failure_reason, source_type
            FROM prod_cities
            WHERE status = 'active' AND health_status = 'stale'
            ORDER BY total_permits DESC
            LIMIT 50
        """).fetchall()

        never_worked = conn.execute("""
            SELECT source_type, COUNT(*) as cnt
            FROM prod_cities
            WHERE status = 'active' AND health_status = 'never_worked'
            GROUP BY source_type
            ORDER BY cnt DESC
        """).fetchall()

        # SQLite rows support index access; convert to dicts
        summary_list = []
        for r in summary:
            summary_list.append({
                'health_status': r[0], 'cnt': r[1],
                'permits': r[2], 'avg_days_stale': round(r[3], 1) if r[3] else None
            })

        stale_list = []
        for r in stale:
            stale_list.append({
                'city': r[0], 'state': r[1], 'city_slug': r[2],
                'total_permits': r[3], 'latest_permit_date': r[4],
                'days_since_new_data': r[5], 'last_failure_reason': r[6],
                'source_type': r[7]
            })

        nw_list = []
        for r in never_worked:
            nw_list.append({'source_type': r[0], 'cnt': r[1]})

        return jsonify({
            'summary': summary_list,
            'stale_cities': stale_list,
            'never_worked_by_platform': nw_list
        }), 200
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500




@app.route('/api/admin/validation-results')
def admin_validation_results():
    """V12.31: Get endpoint validation results for applying fixes."""
    valid, error = check_admin_key()
    if not valid:
        return error

    validation_file = os.path.join(DATA_DIR, "endpoint_validation.json")
    if not os.path.exists(validation_file):
        return jsonify({'error': 'No validation results found. Run validate_endpoints.py first.'}), 404

    try:
        with open(validation_file) as f:
            data = json.load(f)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': f'Failed to read validation results: {str(e)}'}), 500


@app.route('/api/admin/suggested-fixes')
def admin_suggested_fixes():
    """V12.31: Get suggested fixes for broken endpoints."""
    valid, error = check_admin_key()
    if not valid:
        return error

    fixes_file = os.path.join(DATA_DIR, "suggested_fixes.json")
    if not os.path.exists(fixes_file):
        return jsonify({'error': 'No suggested fixes found. Run validate_endpoints.py --fix first.'}), 404

    try:
        with open(fixes_file) as f:
            data = json.load(f)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': f'Failed to read suggested fixes: {str(e)}'}), 500


@app.route('/api/admin/coverage')
def admin_coverage():
    """V12.33/V31: Get coverage statistics - which cities/states have data.
    V31: Distinguishes active cities (with live data sources) from historical
    cities that only appear in permit data from bulk sources.
    """
    valid, error = check_admin_key()
    if not valid:
        return error

    # V31: Active cities = prod_cities with status='active' (these are being pulled)
    active_city_count = 0
    active_cities_list = []
    try:
        if permitdb.prod_cities_table_exists():
            active_cities_list = permitdb.get_prod_cities(status='active')
            active_city_count = len(active_cities_list)
    except Exception:
        pass

    # Also get counts by status for a full breakdown
    status_breakdown = {}
    try:
        conn = permitdb.get_connection()
        rows = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM prod_cities GROUP BY status"
        ).fetchall()
        status_breakdown = {r['status']: r['cnt'] for r in rows}
    except Exception:
        pass

    # V34: Analyze coverage from SQLite DB (not permits.json which is deprecated)
    try:
        conn = permitdb.get_connection()

        # Get total permits
        total_row = conn.execute("SELECT COUNT(*) as cnt FROM permits").fetchone()
        total_permits = total_row['cnt'] if total_row else 0

        # Analyze by city and state
        city_rows = conn.execute("""
            SELECT city, state, COUNT(*) as cnt
            FROM permits GROUP BY city, state ORDER BY cnt DESC
        """).fetchall()

        city_counts = {}
        state_counts = {}
        for r in city_rows:
            city_key = f"{r['city']}, {r['state']}"
            city_counts[city_key] = r['cnt']
            state_counts[r['state'] or 'Unknown'] = state_counts.get(r['state'] or 'Unknown', 0) + r['cnt']

        top_cities = list(city_counts.items())[:50]
        states_covered = sorted(state_counts.items(), key=lambda x: -x[1])

        all_states = ['AL', 'AK', 'AZ', 'AR', 'CA', 'CO', 'CT', 'DE', 'FL', 'GA',
                      'HI', 'ID', 'IL', 'IN', 'IA', 'KS', 'KY', 'LA', 'ME', 'MD',
                      'MA', 'MI', 'MN', 'MS', 'MO', 'MT', 'NE', 'NV', 'NH', 'NJ',
                      'NM', 'NY', 'NC', 'ND', 'OH', 'OK', 'OR', 'PA', 'RI', 'SC',
                      'SD', 'TN', 'TX', 'UT', 'VT', 'VA', 'WA', 'WV', 'WI', 'WY']
        states_missing = [s for s in all_states if s not in state_counts]

        # V34: Get verified active city count (cities with actual data)
        verified_count = permitdb.get_prod_city_count()

        return jsonify({
            'active_cities': active_city_count,
            'verified_active_with_data': verified_count,
            'prod_cities_by_status': status_breakdown,
            'distinct_cities_in_permits': len(city_counts),
            'total_permits': total_permits,
            'total_states_with_data': len(state_counts),
            'states_covered': states_covered,
            'states_missing': states_missing,
            'top_50_cities': top_cities,
        })

    except Exception as e:
        return jsonify({'error': f'Failed to analyze coverage: {str(e)}'}), 500


# ===========================
# V34: ADMIN AUDIT & CLEANUP
# ===========================

@app.route('/api/admin/audit')
def admin_audit_cities():
    """V34: Comprehensive audit of all active cities vs actual permit data.
    Returns detailed report showing which cities have data, which don't,
    and recommendations for cleanup.
    """
    valid, error = check_admin_key()
    if not valid:
        return error

    try:
        results = permitdb.audit_prod_cities()
        return jsonify(results)
    except Exception as e:
        return jsonify({'error': f'Audit failed: {str(e)}'}), 500


@app.route('/api/admin/reactivate-paused', methods=['POST'])
def admin_reactivate_paused():
    """V35: Lightweight endpoint to reactivate paused cities that have permit data.
    Only does one fast UPDATE — no heavy cleanup."""
    valid, error = check_admin_key()
    if not valid:
        return error

    try:
        conn = permitdb.get_connection()
        # First sync counts for paused cities only
        paused = conn.execute(
            "SELECT id, city, state FROM prod_cities WHERE status = 'paused'"
        ).fetchall()
        updated_counts = 0
        for row in paused:
            actual = conn.execute(
                "SELECT COUNT(*) as cnt FROM permits WHERE LOWER(city) = LOWER(?) AND state = ?",
                (row['city'], row['state'])
            ).fetchone()['cnt']
            if actual > 0:
                conn.execute(
                    "UPDATE prod_cities SET total_permits = ?, status = 'active' WHERE id = ?",
                    (actual, row['id'])
                )
                updated_counts += 1
        conn.commit()

        # Get the updated list
        reactivated = conn.execute(
            "SELECT city, state, total_permits FROM prod_cities WHERE status = 'active' ORDER BY total_permits DESC"
        ).fetchall()

        return jsonify({
            'reactivated_count': updated_counts,
            'total_active': len(reactivated),
            'message': f'Reactivated {updated_counts} paused cities with data'
        })
    except Exception as e:
        return jsonify({'error': f'Reactivation failed: {str(e)}'}), 500


@app.route('/api/admin/scraper-history')
def admin_scraper_history():
    """V35: Per-city collection history from scraper_runs table.
    Shows last collection result for every city to identify broken vs working endpoints.
    """
    valid, error = check_admin_key()
    if not valid:
        return error

    try:
        conn = permitdb.get_connection()
        # Get the most recent run for each city
        runs = conn.execute("""
            SELECT city_slug, source_name, city, state,
                   permits_found, permits_inserted, status, error_message,
                   duration_ms, run_started_at,
                   ROW_NUMBER() OVER (PARTITION BY city_slug ORDER BY run_started_at DESC) as rn
            FROM scraper_runs
        """).fetchall()

        # Filter to most recent per city
        latest = {}
        for r in runs:
            if r['city_slug'] not in latest or r['rn'] == 1:
                if r['rn'] == 1:
                    latest[r['city_slug']] = dict(r)

        # Categorize
        working = []  # returned permits
        empty = []    # success but 0 permits
        errored = []  # error status
        for slug, r in latest.items():
            entry = {
                'slug': slug,
                'name': r.get('source_name') or r.get('city') or slug,
                'state': r.get('state', ''),
                'permits_found': r.get('permits_found', 0),
                'status': r.get('status', ''),
                'error': r.get('error_message', ''),
                'last_run': r.get('run_started_at', ''),
                'duration_ms': r.get('duration_ms', 0),
            }
            if r.get('status') == 'error' or (r.get('error_message') and r.get('error_message') != ''):
                errored.append(entry)
            elif r.get('permits_found', 0) > 0:
                working.append(entry)
            else:
                empty.append(entry)

        return jsonify({
            'total_cities': len(latest),
            'working': len(working),
            'empty': len(empty),
            'errored': len(errored),
            'working_cities': sorted(working, key=lambda x: -x['permits_found']),
            'empty_cities': sorted(empty, key=lambda x: x['name']),
            'errored_cities': sorted(errored, key=lambda x: x['name']),
        })
    except Exception as e:
        return jsonify({'error': f'Scraper history failed: {str(e)}'}), 500


@app.route('/api/admin/test-and-backfill', methods=['POST'])
def admin_test_and_backfill():
    """V35: Test an endpoint, backfill 6 months of data, and activate the source.

    POST body: {"city_key": "phoenix"} — uses existing CITY_REGISTRY config
    OR: {"city_key": "new_city", "config": {...}} — provide full config

    Steps:
    1. Test: fetch 5 records to verify the endpoint works
    2. Backfill: fetch 180 days of historical data
    3. Normalize and insert into DB
    4. Activate: set city_source status='active', create/update prod_city
    5. Report results

    This ensures we KNOW the collector will succeed before activating.
    """
    valid, error = check_admin_key()
    if not valid:
        return error

    try:
        data = request.get_json() or {}
        # V233b: accept `city_slug` as an alias. Cowork tooling sends
        # `city_slug` consistently across admin endpoints; this was the
        # one holdout that still required `city_key`.
        city_key = data.get('city_key') or data.get('city_slug')
        if not city_key:
            return jsonify({'error': 'city_key (or city_slug) is required'}), 400

        days_back = data.get('days_back', 180)

        # Import collection functions
        from collector import fetch_permits, normalize_permit
        from city_source_db import get_city_config

        # Get config (from request body or existing registry)
        config = data.get('config')
        if not config:
            config = get_city_config(city_key)
        if not config:
            return jsonify({'error': f'No config found for {city_key}. Provide config in request body.'}), 404

        # Force active for testing
        config['active'] = True

        # Step 1: TEST — fetch a small sample to verify endpoint works
        from collector import fetch_socrata, fetch_arcgis, fetch_ckan, fetch_carto
        platform = config.get('platform', 'socrata')
        test_config = dict(config)
        test_config['limit'] = 5  # Just 5 records for testing

        test_config['limit'] = 10  # Small sample for freshness check
        try:
            # V128: Test with 90-day window (was 30 — too strict for monthly-updating portals)
            if platform == 'socrata':
                test_raw = fetch_socrata(test_config, 90)
            elif platform == 'arcgis':
                test_raw = fetch_arcgis(test_config, 90)
            elif platform == 'ckan':
                test_raw = fetch_ckan(test_config, 90)
            elif platform == 'carto':
                test_raw = fetch_carto(test_config, 90)
            elif platform == 'accela':
                from accela_portal_collector import fetch_accela as _portal_fetch
                test_raw = _portal_fetch(test_config, 90)
            else:
                return jsonify({'error': f'Unsupported platform: {platform}'}), 400
        except Exception as e:
            return jsonify({
                'status': 'FAILED',
                'step': 'test',
                'error': str(e),
                'message': f'Endpoint test failed for {city_key}. Do NOT activate.'
            }), 400

        if not test_raw:
            return jsonify({
                'status': 'FAILED',
                'step': 'test',
                'error': 'No permits in last 30 days',
                'message': f'{city_key} has no data in the last 90 days. Stale source — do NOT activate.'
            }), 400

        # Step 2: BACKFILL — fetch full historical data
        config['limit'] = config.get('limit', 2000)  # Restore normal limit
        try:
            if platform == 'socrata':
                raw = fetch_socrata(config, days_back)
            elif platform == 'arcgis':
                raw = fetch_arcgis(config, days_back)
            elif platform == 'ckan':
                raw = fetch_ckan(config, days_back)
            elif platform == 'carto':
                raw = fetch_carto(config, days_back)
            elif platform == 'accela':
                from accela_portal_collector import fetch_accela as _portal_fetch
                raw = _portal_fetch(config, days_back)
        except Exception as e:
            return jsonify({
                'status': 'FAILED',
                'step': 'backfill_fetch',
                'error': str(e),
                'test_passed': True,
                'test_records': len(test_raw),
            }), 500

        # V126: Save config to city_sources BEFORE normalizing so normalize_permit can find it
        try:
            from city_source_db import upsert_city_source
            import json as _json
            upsert_city_source({
                'source_key': city_key,
                'name': config.get('name', city_key),
                'state': config.get('state', ''),
                'platform': platform,
                'endpoint': config.get('endpoint', ''),
                'dataset_id': config.get('dataset_id', ''),
                'date_field': config.get('date_field', ''),
                'field_map': config.get('field_map', {}),
                'mode': 'city',
                'status': 'active',
            })
        except Exception as e:
            print(f"[V126] Warning: could not save config to city_sources: {e}", flush=True)

        # Step 3: NORMALIZE — convert raw records to our schema
        normalized = []
        for record in raw:
            try:
                permit = normalize_permit(record, city_key)
                if permit and permit.get('permit_number'):
                    normalized.append(permit)
            except Exception:
                continue

        if not normalized:
            return jsonify({
                'status': 'WARNING',
                'step': 'normalize',
                'raw_fetched': len(raw),
                'normalized': 0,
                'message': f'Got {len(raw)} raw records but 0 normalized. Check field_map config.'
            }), 400

        # Step 4: INSERT into DB
        # Always use hyphen-format of city_key as source_city_key — matches score query: REPLACE(source_key, '_', '-')
        source_slug = city_key.replace('_', '-')
        inserted = permitdb.upsert_permits(normalized, source_city_key=source_slug)

        # Step 5: ACTIVATE — update city_sources and prod_cities
        conn = permitdb.get_connection()

        # Activate in city_sources
        existing = conn.execute(
            "SELECT source_key FROM city_sources WHERE source_key = ?", (city_key,)
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE city_sources SET status = 'active' WHERE source_key = ?",
                (city_key,)
            )
        # No else — if it's not in city_sources, the CITY_REGISTRY dict entry is used

        # Create/update prod_city
        city_name = config.get('name', city_key.replace('_', ' ').title())
        state = config.get('state', '')
        from db import normalize_city_slug
        city_slug = normalize_city_slug(city_name)
        # Look up by city+state OR by slug (handles cases where slug exists from prior attempt)
        existing_prod = conn.execute(
            "SELECT id FROM prod_cities WHERE (city = ? AND state = ?) OR city_slug = ?",
            (city_name, state, city_slug)
        ).fetchone()
        if existing_prod:
            conn.execute("""
                UPDATE prod_cities SET status = 'active', total_permits = ?, source_id = ?,
                    city = ?, state = ?
                WHERE id = ?
            """, (len(normalized), city_key, city_name, state, existing_prod['id']))
        else:
            conn.execute("""
                INSERT INTO prod_cities (city, state, city_slug, source_id, status, total_permits)
                VALUES (?, ?, ?, ?, 'active', ?)
            """, (city_name, state, city_slug, city_key, len(normalized)))

        conn.commit()

        # V145: Update sources table metadata after successful test-and-backfill
        try:
            source_slug = city_key.replace('_', '-')
            # Try multiple key formats
            for sk in [source_slug, city_key, source_slug.split('-')[0]]:
                permitdb.get_connection().execute("""
                    UPDATE sources SET
                        total_permits = ?, newest_permit_date = (SELECT MAX(date) FROM permits WHERE source_city_key = ?),
                        last_attempt_at = datetime('now'), last_attempt_status = 'success',
                        last_success_at = datetime('now'), last_permits_found = ?, last_permits_inserted = ?,
                        consecutive_failures = 0, updated_at = datetime('now')
                    WHERE source_key = ?
                """, (len(normalized), source_slug, len(raw), inserted[0] if isinstance(inserted, (list, tuple)) else inserted, sk))
            permitdb.get_connection().commit()
        except Exception as src_e:
            print(f"[V145] sources update in test-and-backfill: {str(src_e)[:50]}")

        return jsonify({
            'status': 'SUCCESS',
            'city_key': city_key,
            'city_name': city_name,
            'state': state,
            'platform': platform,
            'test_records': len(test_raw),
            'raw_fetched': len(raw),
            'normalized': len(normalized),
            'inserted': inserted,
            'days_back': days_back,
            'message': f'✓ {city_name} is live. {len(normalized)} permits backfilled. Collector will pick it up next run.'
        })

    except Exception as e:
        return jsonify({'error': f'Test and backfill failed: {str(e)}'}), 500


@app.route('/api/admin/discover-and-activate', methods=['POST'])
def admin_discover_and_activate():
    """V35: Auto-discover fresh endpoints for stale cities, test, backfill, and activate.

    POST body (all optional):
      {"cities": ["milwaukee", "sacramento"]}  — specific cities to process
      If omitted, processes ALL stale cities from the discovery module.

    For each city:
    1. Search Socrata Discovery API, ArcGIS Hub, CKAN catalogs
    2. Test each discovered endpoint for 30-day freshness
    3. Build field mapping from sample data
    4. Backfill 180 days of historical data
    5. Normalize, insert, and activate

    Returns detailed results for each city.
    """
    valid, error = check_admin_key()
    if not valid:
        return error

    try:
        from discover_fresh_endpoints import discover_all, STALE_CITIES
        from collector import normalize_permit, fetch_socrata, fetch_arcgis, fetch_ckan, fetch_carto
        from city_source_db import get_city_config
        from db import normalize_city_slug

        data = request.get_json() or {}
        target_cities = data.get('cities')  # None = all stale cities
        days_back = data.get('days_back', 180)
        dry_run = data.get('dry_run', False)

        # Step 1: Discover fresh endpoints
        discovery_results = discover_all(target_cities)

        # Step 2: For each FOUND city, run test-and-backfill
        activation_results = {}
        for city_key, disc in discovery_results.items():
            if disc["status"] not in ("FOUND", "EXISTING_WORKS"):
                activation_results[city_key] = {
                    "status": disc["status"],
                    "message": f"No fresh endpoint found: {disc['status']}",
                }
                continue

            if dry_run:
                activation_results[city_key] = {
                    "status": "DRY_RUN",
                    "config": disc["config"],
                    "freshness": disc["freshness"],
                    "message": f"Would activate with {disc['config']['platform']} endpoint",
                }
                continue

            config = disc["config"]
            platform = config.get("platform", "socrata")

            try:
                # Backfill: fetch 180 days
                config["active"] = True
                if platform == "socrata":
                    raw = fetch_socrata(config, days_back)
                elif platform == "arcgis":
                    raw = fetch_arcgis(config, days_back)
                elif platform == "ckan":
                    raw = fetch_ckan(config, days_back)
                elif platform == "carto":
                    raw = fetch_carto(config, days_back)
                else:
                    activation_results[city_key] = {"status": "ERROR", "error": f"Unknown platform: {platform}"}
                    continue

                if not raw:
                    activation_results[city_key] = {"status": "ERROR", "error": "Backfill returned 0 records"}
                    continue

                # Normalize — we need a config in the registry or city_sources for normalize_permit to work.
                # Use the discovered config's field_map directly.
                normalized = []
                fmap = config.get("field_map", {})
                city_name = config.get("name", city_key)
                state = config.get("state", "")

                for record in raw:
                    try:
                        # Manual normalization using discovered field_map
                        import re as _re
                        def _get(field_name):
                            raw_key = fmap.get(field_name, "")
                            if not raw_key:
                                return ""
                            return str(record.get(raw_key, "")).strip()

                        permit_number = _get("permit_number")
                        if not permit_number:
                            continue

                        # Parse date
                        date_str = _get("filing_date") or _get("date") or _get("issued_date")
                        parsed_date = ""
                        if date_str:
                            if str(date_str).isdigit() and len(str(date_str)) >= 10:
                                try:
                                    parsed_date = datetime.fromtimestamp(int(date_str) / 1000).strftime("%Y-%m-%d")
                                except (ValueError, OSError):
                                    pass
                            if not parsed_date:
                                for fmt in ["%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%m/%d/%Y"]:
                                    try:
                                        parsed_date = datetime.strptime(str(date_str)[:26], fmt).strftime("%Y-%m-%d")
                                        break
                                    except ValueError:
                                        continue
                            if not parsed_date and '/' in str(date_str):
                                try:
                                    parts = str(date_str).split()[0].split('/')
                                    if len(parts) == 3:
                                        m, d, y = int(parts[0]), int(parts[1]), int(parts[2])
                                        parsed_date = f"{y:04d}-{m:02d}-{d:02d}"
                                except (ValueError, IndexError):
                                    pass
                            if not parsed_date:
                                parsed_date = str(date_str)[:10]

                        # Parse cost
                        cost_str = _get("estimated_cost")
                        try:
                            cost = float(_re.sub(r'[^\d.]', '', cost_str)) if cost_str else 0
                        except (ValueError, TypeError):
                            cost = 0
                        if cost > 50_000_000:
                            cost = 50_000_000

                        address = _get("address") or "Address not provided"
                        description = _get("description") or _get("work_type") or ""

                        normalized.append({
                            "permit_number": permit_number,
                            "permit_type": _get("permit_type") or "Building Permit",
                            "work_type": _get("work_type") or "",
                            "address": address,
                            "city": city_name,
                            "state": state,
                            "zip": _get("zip") or "",
                            "filing_date": parsed_date,
                            "status": _get("status") or "",
                            "estimated_cost": cost,
                            "description": description,
                            "owner_name": _get("owner_name") or "",
                            "contact_name": _get("contact_name") or "",
                        })
                    except Exception:
                        continue

                if not normalized:
                    activation_results[city_key] = {
                        "status": "ERROR",
                        "error": f"Got {len(raw)} raw records but 0 normalized. Field map may be wrong.",
                        "config": config,
                    }
                    continue

                # Insert
                inserted = permitdb.upsert_permits(normalized, source_city_key=city_key)

                # Activate in city_sources
                conn = permitdb.get_connection()
                existing = conn.execute(
                    "SELECT source_key FROM city_sources WHERE source_key = ?", (city_key,)
                ).fetchone()

                if existing:
                    # Update existing source with new endpoint info
                    conn.execute("""
                        UPDATE city_sources SET
                            status = 'active',
                            endpoint = ?,
                            platform = ?,
                            date_field = ?,
                            field_map = ?
                        WHERE source_key = ?
                    """, (
                        config["endpoint"],
                        platform,
                        config.get("date_field", ""),
                        json.dumps(config.get("field_map", {})),
                        city_key,
                    ))
                else:
                    # Insert new city_source
                    conn.execute("""
                        INSERT INTO city_sources (source_key, name, state, platform, endpoint,
                            date_field, field_map, status, mode)
                        VALUES (?, ?, ?, ?, ?, ?, ?, 'active', 'city')
                    """, (
                        city_key,
                        city_name,
                        state,
                        platform,
                        config["endpoint"],
                        config.get("date_field", ""),
                        json.dumps(config.get("field_map", {})),
                    ))

                # Create/update prod_city (lookup by slug too to avoid UNIQUE constraint)
                city_slug = normalize_city_slug(city_name)
                existing_prod = conn.execute(
                    "SELECT id FROM prod_cities WHERE (city = ? AND state = ?) OR city_slug = ?",
                    (city_name, state, city_slug)
                ).fetchone()
                if existing_prod:
                    conn.execute("""
                        UPDATE prod_cities SET status = 'active', total_permits = ?, source_id = ?,
                            city = ?, state = ?
                        WHERE id = ?
                    """, (len(normalized), city_key, city_name, state, existing_prod['id']))
                else:
                    conn.execute("""
                        INSERT INTO prod_cities (city, state, city_slug, source_id, status, total_permits)
                        VALUES (?, ?, ?, ?, 'active', ?)
                    """, (city_name, state, city_slug, city_key, len(normalized)))

                conn.commit()

                activation_results[city_key] = {
                    "status": "ACTIVATED",
                    "raw_fetched": len(raw),
                    "normalized": len(normalized),
                    "inserted": inserted,
                    "platform": platform,
                    "endpoint": config["endpoint"],
                    "date_field": config.get("date_field"),
                    "newest_date": disc["freshness"].get("newest_date"),
                    "message": f"✓ {city_name} is live. {len(normalized)} permits backfilled.",
                }

            except Exception as e:
                activation_results[city_key] = {
                    "status": "ERROR",
                    "error": str(e),
                    "config": config,
                }

        # Summary
        activated = [k for k, v in activation_results.items() if v.get("status") == "ACTIVATED"]
        failed = [k for k, v in activation_results.items() if v.get("status") == "ERROR"]
        not_found = [k for k, v in activation_results.items() if v.get("status") in ("NOT_FOUND", "STALE")]

        return jsonify({
            "summary": {
                "activated": len(activated),
                "failed": len(failed),
                "not_found": len(not_found),
                "dry_run": dry_run,
            },
            "activated_cities": activated,
            "failed_cities": failed,
            "not_found_cities": not_found,
            "details": activation_results,
            "discovery": {k: {
                "status": v["status"],
                "endpoints_found": len(v.get("all_discovered", [])),
                "endpoints_tested": len(v.get("all_tested", [])),
            } for k, v in discovery_results.items()},
        })

    except Exception as e:
        import traceback
        return jsonify({'error': f'Discovery failed: {str(e)}', 'traceback': traceback.format_exc()}), 500


@app.route('/api/admin/pause-empty', methods=['POST'])
def admin_pause_empty_cities():
    """V34: Pause all active prod_cities that have 0 actual permits in DB.
    This cleans up cities that are marked active but have never successfully collected data.
    """
    valid, error = check_admin_key()
    if not valid:
        return error

    try:
        paused = permitdb.pause_cities_without_data()
        return jsonify({
            'paused_count': len(paused),
            'paused_cities': paused,
            'message': f'Paused {len(paused)} cities with no permit data'
        })
    except Exception as e:
        return jsonify({'error': f'Pause operation failed: {str(e)}'}), 500


@app.route('/api/admin/cleanup-data', methods=['POST'])
def admin_cleanup_data():
    """V35: Run comprehensive data cleanup — fix wrong states, remove garbage records.
    This is safe to run multiple times (idempotent).
    """
    valid, error = check_admin_key()
    if not valid:
        return error

    try:
        conn = permitdb.get_connection()
        before = conn.execute("SELECT COUNT(*) as cnt FROM permits").fetchone()['cnt']

        # Step 1: Clean prod_cities names first (so state lookups work)
        prod_all = conn.execute("SELECT id, city, state FROM prod_cities").fetchall()
        name_fixes = 0
        for row in prod_all:
            cleaned = permitdb.clean_city_name_for_prod(row['city'], row['state'])
            if cleaned != row['city']:
                # Check if cleaned name already exists (avoid UNIQUE constraint violation)
                existing = conn.execute(
                    "SELECT id FROM prod_cities WHERE city = ? AND state = ?",
                    (cleaned, row['state'])
                ).fetchone()
                if existing:
                    conn.execute("DELETE FROM prod_cities WHERE id = ?", (row['id'],))
                else:
                    conn.execute("UPDATE prod_cities SET city = ? WHERE id = ?", (cleaned, row['id']))
                name_fixes += 1
        conn.commit()

        # Step 2: Fix wrong states using cleaned prod_cities as truth
        prod_rows = conn.execute(
            "SELECT city, state FROM prod_cities WHERE state IS NOT NULL AND state != ''"
        ).fetchall()
        state_fixes = 0
        for row in prod_rows:
            result = conn.execute(
                "UPDATE permits SET state = ? WHERE (city = ? OR LOWER(city) = LOWER(?)) AND state != ?",
                (row['state'], row['city'], row['city'], row['state'])
            )
            if result.rowcount > 0:
                state_fixes += result.rowcount
        conn.commit()

        # Step 3: Run V34/V35 data cleanup (garbage deletion, casing fixes)
        cleanup_fixed = permitdb._run_v34_data_cleanup(conn)

        # Step 4: Sync permit counts
        permitdb._sync_prod_city_counts(conn)

        after = conn.execute("SELECT COUNT(*) as cnt FROM permits").fetchone()['cnt']

        return jsonify({
            'prod_city_names_fixed': name_fixes,
            'state_assignments_fixed': state_fixes,
            'cleanup_records_affected': cleanup_fixed,
            'permits_before': before,
            'permits_after': after,
            'permits_removed': before - after,
        })
    except Exception as e:
        return jsonify({'error': f'Cleanup failed: {str(e)}'}), 500


@app.route('/api/admin/migrate-violations', methods=['POST'])
def admin_migrate_violations():
    """V162: Drop and recreate violations table with new schema."""
    valid, error = check_admin_key()
    if not valid:
        return error
    try:
        conn = permitdb.get_connection()
        conn.execute("DROP TABLE IF EXISTS violations")
        conn.commit()
        from violation_collector import _ensure_table
        _ensure_table()
        schema = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='violations'").fetchone()
        return jsonify({'success': True, 'schema': schema[0] if schema else 'not found'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/backfill-trade-tags', methods=['POST'])
def admin_backfill_trade_tags():
    """V170 B1: Backfill trade_tag on existing permits. Runs in background thread."""
    valid, error = check_admin_key()
    if not valid:
        return error

    def _backfill():
        from collector import classify_trade
        conn = permitdb.get_connection()
        total = conn.execute("SELECT COUNT(*) FROM permits WHERE trade_category IS NULL OR trade_category = ''").fetchone()[0]
        print(f"[BACKFILL] Starting trade_tag backfill: {total} permits to process")
        offset = 0
        batch_size = 10000
        updated = 0
        while True:
            rows = conn.execute(
                "SELECT id, description, permit_type FROM permits "
                "WHERE (trade_category IS NULL OR trade_category = '') "
                "ORDER BY id LIMIT ? OFFSET ?", (batch_size, offset)
            ).fetchall()
            if not rows:
                break
            for r in rows:
                tag = classify_trade(r['description'] or r['permit_type'], r['permit_type'])
                conn.execute("UPDATE permits SET trade_category = ? WHERE id = ?", (tag, r['id']))
                updated += 1
            conn.commit()
            offset += batch_size
            print(f"[BACKFILL] {updated}/{total} ({100*updated//max(total,1)}%)", flush=True)
        print(f"[BACKFILL] Complete: {updated} permits tagged")

    t = threading.Thread(target=_backfill, daemon=True, name='trade_backfill')
    t.start()
    return jsonify({'status': 'started', 'message': 'Trade tag backfill running in background'})


@app.route('/api/admin/collection-log', methods=['GET'])
def admin_collection_log():
    """V214: Show the N most-recent entries from collection_log — gives operators
    (and future debuggers) a view into silent-failure patterns like the V213
    Carto-parse bug. Requires the usual admin auth."""
    valid, error = check_admin_key()
    if not valid:
        return error
    try:
        limit = int(request.args.get('limit', 50))
    except ValueError:
        limit = 50
    limit = min(max(limit, 1), 500)
    conn = permitdb.get_connection()
    try:
        rows = conn.execute("""
            SELECT city_slug, collection_type, status,
                   records_fetched, records_inserted, error_message,
                   duration_seconds, created_at,
                   api_url, query_params,
                   api_rows_returned, duplicate_rows_skipped
            FROM collection_log
            ORDER BY created_at DESC LIMIT ?
        """, (limit,)).fetchall()
    except Exception as e:
        return jsonify({'error': f'table query failed: {e}'}), 500
    out = []
    for r in rows:
        if hasattr(r, 'keys'):
            out.append({k: r[k] for k in r.keys()})
        else:
            out.append({
                'city_slug': r[0], 'collection_type': r[1], 'status': r[2],
                'records_fetched': r[3], 'records_inserted': r[4],
                'error_message': r[5],
                'duration_seconds': round(r[6], 2) if r[6] is not None else None,
                'created_at': r[7],
                'api_url': r[8], 'query_params': r[9],
                'api_rows_returned': r[10], 'duplicate_rows_skipped': r[11],
            })
    # V215: three-state status surface for quick triage
    totals = {'success': 0, 'caught_up': 0, 'no_api_data': 0,
              'error': 0, 'empty': 0}
    for row in out:
        s = row.get('status', 'unknown')
        totals[s] = totals.get(s, 0) + 1
    return jsonify({'rows': out, 'totals': totals, 'limit': limit})


@app.route('/api/admin/recalc-freshness', methods=['POST'])
def admin_recalc_freshness():
    """V170: One-shot freshness recalc using correct Postgres schema.
    Join: permits.source_city_key = prod_cities.source_id
    Dates: TEXT columns, need substring cast for comparison."""
    valid, error = check_admin_key()
    if not valid:
        return error
    try:
        import psycopg2
        conn = psycopg2.connect(os.environ['DATABASE_URL'])
        conn.autocommit = False
        cur = conn.cursor()
        cur.execute("SET statement_timeout = '600000'")
        # Step 1: backfill NULL newest_permit_date
        cur.execute("""
            UPDATE prod_cities
            SET newest_permit_date = sub.max_date
            FROM (
                SELECT source_city_key,
                       MAX(COALESCE(NULLIF(filing_date, ''), NULLIF(issued_date, ''), NULLIF(date, ''),
                           to_char(collected_at, 'YYYY-MM-DD'))) AS max_date
                FROM permits
                WHERE source_city_key IS NOT NULL
                GROUP BY source_city_key
            ) sub
            WHERE prod_cities.source_id = sub.source_city_key
              AND prod_cities.source_type IS NOT NULL
              AND prod_cities.newest_permit_date IS NULL
              AND sub.max_date IS NOT NULL
              AND sub.max_date ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}'
        """)
        n_dates = cur.rowcount
        # Step 2: bucket data_freshness
        cur.execute("""
            UPDATE prod_cities SET data_freshness = CASE
                WHEN substring(newest_permit_date, 1, 10)::date >= CURRENT_DATE - INTERVAL '7 days' THEN 'fresh'
                WHEN substring(newest_permit_date, 1, 10)::date >= CURRENT_DATE - INTERVAL '30 days' THEN 'aging'
                WHEN substring(newest_permit_date, 1, 10)::date >= CURRENT_DATE - INTERVAL '90 days' THEN 'stale'
                ELSE 'no_data'
            END
            WHERE source_type IS NOT NULL
              AND newest_permit_date IS NOT NULL
              AND newest_permit_date ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}'
        """)
        n_freshness = cur.rowcount
        conn.commit()
        # Get result
        cur.execute("SELECT data_freshness, COUNT(*) as cnt FROM prod_cities WHERE source_type IS NOT NULL GROUP BY 1 ORDER BY cnt DESC")
        rows = cur.fetchall()
        conn.close()
        return jsonify({
            'status': 'complete',
            'dates_updated': n_dates,
            'freshness_updated': n_freshness,
            'distribution': {r[0]: r[1] for r in rows},
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/backfill-normalized-addresses', methods=['POST'])
def admin_backfill_addresses():
    """V170 B5: Backfill normalized_address on permits + violations."""
    valid, error = check_admin_key()
    if not valid:
        return error

    def _backfill():
        from address_utils import normalize_address
        conn = permitdb.get_connection()
        # Add columns if missing
        for table in ('permits', 'violations'):
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN normalized_address TEXT")
            except Exception:
                pass  # Column already exists
            try:
                conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_norm_addr ON {table}(normalized_address, city, state)")
            except Exception:
                pass
        conn.commit()

        for table, addr_col in [('permits', 'address'), ('violations', 'address')]:
            total = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE normalized_address IS NULL AND {addr_col} IS NOT NULL").fetchone()[0]
            print(f"[BACKFILL] {table}: {total} rows to normalize", flush=True)
            offset = 0
            updated = 0
            while True:
                rows = conn.execute(
                    f"SELECT id, {addr_col} FROM {table} WHERE normalized_address IS NULL AND {addr_col} IS NOT NULL ORDER BY id LIMIT 10000 OFFSET ?",
                    (offset,)
                ).fetchall()
                if not rows:
                    break
                for r in rows:
                    norm = normalize_address(r[addr_col] if isinstance(r, dict) else r[1])
                    rid = r['id'] if isinstance(r, dict) else r[0]
                    conn.execute(f"UPDATE {table} SET normalized_address = ? WHERE id = ?", (norm, rid))
                    updated += 1
                conn.commit()
                offset += 10000
                print(f"[BACKFILL] {table}: {updated}/{total} ({100*updated//max(total,1)}%)", flush=True)
            print(f"[BACKFILL] {table}: complete ({updated} normalized)", flush=True)

    t = threading.Thread(target=_backfill, daemon=True, name='addr_backfill')
    t.start()
    return jsonify({'status': 'started', 'message': 'Address normalization backfill running in background'})


@app.route('/api/admin/collect-violations', methods=['POST'])
def admin_collect_violations():
    """V162: Trigger violation collection from all configured Socrata sources."""
    valid, error = check_admin_key()
    if not valid:
        return error
    try:
        from violation_collector import collect_violations
        results = collect_violations()
        # Get totals
        conn = permitdb.get_connection()
        totals = conn.execute(
            "SELECT city, state, COUNT(*) as cnt, MAX(violation_date) as newest "
            "FROM violations GROUP BY city, state ORDER BY cnt DESC"
        ).fetchall()
        # V215: results[slug] is a diagnostic dict — flatten 'inserted'
        # to preserve the shape older consumers of this endpoint expect,
        # but also pass the full diagnostics through.
        _flat = {}
        if isinstance(results, dict):
            for _slug, _agg in results.items():
                if isinstance(_agg, dict):
                    _flat[_slug] = _agg.get('inserted', 0) or 0
                else:
                    _flat[_slug] = int(_agg or 0)
        return jsonify({
            'collection_results': _flat,
            'diagnostics': results,
            'totals': [dict(r) for r in totals],
        })
    except Exception as e:
        return jsonify({'error': f'Violation collection failed: {str(e)}'}), 500


@app.route('/api/violations/<city_slug>')
def api_violations(city_slug):
    """V162: Get recent violations for a city."""
    conn = permitdb.get_connection()
    prod_city = conn.execute(
        "SELECT id, city, state FROM prod_cities WHERE city_slug = ?", (city_slug,)
    ).fetchone()
    if not prod_city:
        return jsonify({'error': f'City not found: {city_slug}'}), 404

    city_name = prod_city['city']
    city_state = prod_city['state']
    pid = prod_city['id']

    # V162: Try prod_city_id first, fall back to city name
    try:
        total = conn.execute("SELECT COUNT(*) as cnt FROM violations WHERE prod_city_id = ?", (pid,)).fetchone()['cnt']
        rows = conn.execute("""
            SELECT violation_date, violation_type, violation_description, status, address, zip
            FROM violations WHERE prod_city_id = ?
            ORDER BY violation_date DESC LIMIT 100
        """, (pid,)).fetchall()
    except Exception:
        # Old schema fallback (no prod_city_id column)
        total = conn.execute("SELECT COUNT(*) as cnt FROM violations WHERE city = ? AND state = ?", (city_name, city_state)).fetchone()['cnt']
        rows = conn.execute("""
            SELECT violation_date, violation_type, COALESCE(description, '') as violation_description,
                   status, address, '' as zip
            FROM violations WHERE city = ? AND state = ?
            ORDER BY violation_date DESC LIMIT 100
        """, (city_name, city_state)).fetchall()

    return jsonify({
        'city': prod_city['city'], 'state': prod_city['state'],
        'count': len(rows), 'total': total,
        'violations': [dict(r) for r in rows],
    })


@app.route('/api/violations/<city_slug>/stats')
def api_violations_stats(city_slug):
    """V162: Get violation summary stats for a city."""
    conn = permitdb.get_connection()
    prod_city = conn.execute(
        "SELECT id, city, state FROM prod_cities WHERE city_slug = ?", (city_slug,)
    ).fetchone()
    if not prod_city:
        return jsonify({'error': f'City not found: {city_slug}'}), 404

    pid = prod_city['id']
    total = conn.execute("SELECT COUNT(*) as cnt FROM violations WHERE prod_city_id = ?", (pid,)).fetchone()['cnt']
    last30 = conn.execute(
        "SELECT COUNT(*) as cnt FROM violations WHERE prod_city_id = ? AND violation_date >= date('now', '-30 days')",
        (pid,)
    ).fetchone()['cnt']
    open_count = conn.execute(
        "SELECT COUNT(*) as cnt FROM violations WHERE prod_city_id = ? AND LOWER(status) IN ('open','active','pending')",
        (pid,)
    ).fetchone()['cnt']
    top_types = conn.execute("""
        SELECT violation_type as type, COUNT(*) as count FROM violations
        WHERE prod_city_id = ? AND violation_type IS NOT NULL AND violation_type != ''
        GROUP BY violation_type ORDER BY count DESC LIMIT 10
    """, (pid,)).fetchall()

    return jsonify({
        'total_violations': total, 'last_30_days': last30, 'open_count': open_count,
        'top_types': [dict(r) for r in top_types],
    })


@app.route('/api/permits/<city_slug>/export.csv')
def export_csv(city_slug):
    """V170 B3: Export permits for a city as CSV.

    V251 F3: gated to paid subscribers. Anonymous → redirect to signup with
    a return URL pointing at the same city page (matches the F1 gated-preview
    CTA pattern). Logged-in free-tier → 402 JSON with upgrade message. Pro
    users (including admin) → CSV as before.
    """
    if 'user_email' not in session:
        return redirect(f'/signup?next=/permits/{city_slug}&message=subscribe_to_export')
    _user = find_user_by_email(session['user_email'])
    if not _user or not is_pro(_user):
        return jsonify({
            'error': 'CSV export is a Pro feature. Subscribe for $149/mo to download contractor lead lists.',
            'upgrade_url': f'/pricing?next=/permits/{city_slug}',
        }), 402

    import csv, io
    conn = permitdb.get_connection()
    prod_city = conn.execute(
        "SELECT id, city, state FROM prod_cities WHERE city_slug = ?", (city_slug,)
    ).fetchone()
    if not prod_city:
        return jsonify({'error': f'City not found: {city_slug}'}), 404

    pid = prod_city['id']
    trade_filter = request.args.get('trade', '')
    tier_filter = request.args.get('tier', '')
    zip_filter = (request.args.get('zip') or '').strip()[:5]
    try:
        days_filter = int(request.args.get('days') or 0)
    except (TypeError, ValueError):
        days_filter = 0
    if days_filter not in (0, 7, 30, 90, 180, 365):
        days_filter = 0
    try:
        min_value_filter = int(request.args.get('min_value') or 0)
    except (TypeError, ValueError):
        min_value_filter = 0
    if min_value_filter not in (0, 10000, 50000, 100000, 500000, 1000000):
        min_value_filter = 0
    limit = min(int(request.args.get('limit', 10000)), 10000)

    query = "SELECT * FROM permits WHERE prod_city_id = ?"
    params = [pid]
    if trade_filter:
        query += " AND trade_category = ?"
        params.append(trade_filter)
    if zip_filter:
        query += " AND zip = ?"
        params.append(zip_filter)
    if days_filter:
        query += f" AND COALESCE(filing_date, issued_date, date) >= date('now', '-{days_filter} days')"
    if min_value_filter:
        query += f" AND estimated_cost >= {min_value_filter}"
    query += " ORDER BY filing_date DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(query, params).fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['date', 'permit_number', 'address', 'type', 'description',
                     'value', 'trade', 'contractor_name', 'owner_name', 'status'])
    for r in rows:
        writer.writerow([
            r['filing_date'] or r['date'], r['permit_number'], r['address'],
            r['permit_type'], (r['description'] or '')[:200],
            r['estimated_cost'], r['trade_category'],
            r['contractor_name'] or r['contact_name'], r['owner_name'], r['status'],
        ])

    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename="{city_slug}-permits.csv"'}
    )


# ==========================================================================
# V251 F18: Public v1 API (Pro-gated). Same data the web UI shows, just
# JSON. Session-authenticated for now (API-key header is a v2 concern).
# ==========================================================================

def _require_pro_api():
    """Returns (None, None) when the request is Pro; otherwise a
    (response, status) tuple the caller should `return`.

    Matches the F3 CSV-export gate: anon→402, free→402 with upgrade_url.
    Response is JSON only — no redirects on API endpoints.
    """
    if 'user_email' not in session:
        return jsonify({
            'error': 'Authentication required',
            'signup_url': '/signup',
        }), 401
    _user = find_user_by_email(session['user_email'])
    if not _user or not is_pro(_user):
        return jsonify({
            'error': 'Pro subscription required for API access',
            'upgrade_url': '/pricing',
        }), 402
    return None, None


@app.route('/api/v1/contractors')
def api_v1_contractors():
    """GET /api/v1/contractors?city=<slug>[&trade=][&limit=]

    Returns the same top-contractors payload the web UI renders, but
    unredacted (Pro-only). Useful for CRM ingestion.
    """
    err, status = _require_pro_api()
    if err:
        return err, status
    city_slug = (request.args.get('city') or '').strip()
    trade = (request.args.get('trade') or '').strip()
    try:
        limit = min(max(int(request.args.get('limit') or 25), 1), 500)
    except (TypeError, ValueError):
        limit = 25
    if not city_slug:
        return jsonify({'error': 'city query param required'}), 400
    contractors = _get_top_contractors_for_city(city_slug, limit=limit)
    if trade:
        contractors = [c for c in contractors if (c.get('primary_trade') or '') == trade]
    return jsonify({
        'city_slug': city_slug,
        'count': len(contractors),
        'contractors': contractors,
    })


@app.route('/api/v1/permits')
def api_v1_permits():
    """GET /api/v1/permits?city=<slug>[&trade=][&zip=][&days=][&min_value=][&limit=]

    Recent permits for the city, honoring the same filter params as the
    web UI. Pro-gated.
    """
    err, status = _require_pro_api()
    if err:
        return err, status
    city_slug = (request.args.get('city') or '').strip()
    if not city_slug:
        return jsonify({'error': 'city query param required'}), 400
    conn = permitdb.get_connection()
    pc_row = conn.execute(
        "SELECT id FROM prod_cities WHERE city_slug = ?", (city_slug,)
    ).fetchone()
    if not pc_row:
        return jsonify({'error': f'city not found: {city_slug}'}), 404
    pid = pc_row[0] if not isinstance(pc_row, dict) else pc_row['id']

    trade = (request.args.get('trade') or '').strip()
    zip_f = (request.args.get('zip') or '').strip()[:5]
    try:
        days = int(request.args.get('days') or 0)
    except (TypeError, ValueError):
        days = 0
    if days not in (0, 7, 30, 90, 180, 365):
        days = 0
    try:
        min_value = int(request.args.get('min_value') or 0)
    except (TypeError, ValueError):
        min_value = 0
    if min_value not in (0, 10000, 50000, 100000, 500000, 1000000):
        min_value = 0
    try:
        limit = min(max(int(request.args.get('limit') or 100), 1), 1000)
    except (TypeError, ValueError):
        limit = 100

    clause = ""
    params = [pid]
    if trade:
        clause += " AND trade_category = ?"
        params.append(trade)
    if zip_f:
        clause += " AND zip = ?"
        params.append(zip_f)
    if days:
        clause += f" AND COALESCE(filing_date, issued_date, date) >= date('now', '-{days} days')"
    if min_value:
        clause += f" AND estimated_cost >= {min_value}"
    rows = conn.execute(f"""
        SELECT permit_number, filing_date, issued_date, date, permit_type,
               address, zip, description, estimated_cost, trade_category,
               contractor_name, contact_name, owner_name, status
        FROM permits
        WHERE prod_city_id = ?{clause}
        ORDER BY COALESCE(filing_date, issued_date, date) DESC
        LIMIT ?
    """, params + [limit]).fetchall()
    return jsonify({
        'city_slug': city_slug,
        'filters': {'trade': trade or None, 'zip': zip_f or None,
                    'days': days or None, 'min_value': min_value or None},
        'count': len(rows),
        'permits': [dict(r) for r in rows],
    })


@app.route('/api/admin/digest/status', methods=['GET'])
def admin_digest_status():
    """V158: Get digest daemon status and recent history."""
    valid, error = check_admin_key()
    if not valid:
        return error

    # Check if email_scheduler thread is alive
    daemon_alive = False
    for t in threading.enumerate():
        if t.name == 'email_scheduler' and t.is_alive():
            daemon_alive = True
            break

    # Get recent digest logs
    conn = permitdb.get_connection()
    recent_logs = []
    try:
        rows = conn.execute(
            "SELECT * FROM digest_log ORDER BY sent_at DESC LIMIT 10"
        ).fetchall()
        recent_logs = [dict(r) for r in rows]
    except Exception:
        pass

    # Get subscriber count
    sub_count = 0
    try:
        row = conn.execute("SELECT COUNT(*) as cnt FROM subscribers WHERE active=1").fetchone()
        sub_count = row['cnt'] if row else 0
    except Exception:
        # Fall back to JSON file
        try:
            from email_alerts import load_subscribers
            sub_count = len(load_subscribers())
        except Exception:
            pass

    return jsonify({
        'daemon_alive': daemon_alive,
        'digest_status': DIGEST_STATUS,
        'subscriber_count': sub_count,
        'recent_logs': recent_logs,
        'smtp_configured': bool(os.environ.get('SMTP_PASS', '')),
    })


@app.route('/api/admin/digest/trigger', methods=['POST'])
def admin_digest_trigger():
    """V158: Manually trigger a digest send."""
    valid, error = check_admin_key()
    if not valid:
        return error

    try:
        from email_alerts import send_daily_digest, send_test_digest
        data = request.get_json() or {}

        # Option 1: Send to specific email
        test_email = data.get('email')
        if test_email:
            result = send_test_digest(test_email, city=data.get('city'))
            _log_digest(test_email, result, 'manual_trigger')
            return jsonify({'status': 'sent', 'email': test_email, 'result': str(result)})

        # Option 2: Send full digest to all subscribers
        sent, failed = send_daily_digest()
        _log_digest('all_subscribers', f'sent={sent},failed={failed}', 'manual_trigger')
        return jsonify({'status': 'sent', 'sent': sent, 'failed': failed})
    except Exception as e:
        _log_digest('error', str(e), 'manual_trigger_error')
        return jsonify({'error': f'Digest trigger failed: {str(e)}'}), 500


def _log_digest(recipient, result, status):
    """V158: Log digest send attempt to digest_log table."""
    try:
        conn = permitdb.get_connection()
        conn.execute("""
            INSERT INTO digest_log (recipient_email, permits_count, status, error_message)
            VALUES (?, 0, ?, ?)
        """, (recipient, status, str(result)[:500]))
        conn.commit()
    except Exception:
        pass  # Table may not exist yet


@app.route('/api/admin/query', methods=['POST'])
def admin_query():
    """V34: Run a read-only SQL query for diagnostics.
    Body: {"sql": "SELECT ...", "limit": 100}
    Only SELECT statements allowed.
    """
    valid, error = check_admin_key()
    if not valid:
        return error

    try:
        data = request.get_json() or {}
        # V232: accept `sql` (canonical) and `query` (older name Cowork's
        # tooling still sends). The `query` param used to return a
        # confusing "Only SELECT queries allowed" — actually the code
        # was reading sql='' which failed the startswith check. Now
        # either name works.
        sql = (data.get('sql') or data.get('query') or '').strip()
        limit = min(data.get('limit', 100), 1000)

        # V66: Safety check — only allow SELECT queries
        # Use word boundaries to avoid false positives on column names like 'last_update'
        import re
        sql_upper = sql.upper()
        if not sql_upper.startswith('SELECT'):
            return jsonify({'error': 'Only SELECT queries allowed'}), 400

        # Check for forbidden keywords as standalone words (not within column names)
        forbidden_keywords = ['INSERT', 'UPDATE', 'DELETE', 'DROP', 'ALTER', 'CREATE', 'ATTACH', 'TRUNCATE']
        for forbidden in forbidden_keywords:
            # \b = word boundary — won't match 'last_update' for 'UPDATE'
            if re.search(rf'\b{forbidden}\b', sql_upper):
                return jsonify({'error': f'Forbidden keyword: {forbidden}'}), 400

        conn = permitdb.get_connection()
        try:
            rows = conn.execute(sql).fetchmany(limit)
            result = [dict(r) for r in rows]
            return jsonify({'rows': result, 'count': len(result)})
        finally:
            conn.close()  # V66: Fix connection leak
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/execute', methods=['POST'])
def admin_execute():
    """V159: Run a write SQL statement (INSERT/UPDATE/DELETE).
    Body: {"sql": "UPDATE ..."}
    Requires X-Admin-Key. DROP/ALTER/ATTACH still forbidden.
    """
    valid, error = check_admin_key()
    if not valid:
        return error
    try:
        data = request.get_json() or {}
        sql = data.get('sql', '').strip()
        if not sql:
            return jsonify({'error': 'sql is required'}), 400

        import re
        sql_upper = sql.upper()
        forbidden = ['DROP', 'ALTER', 'ATTACH', 'TRUNCATE', 'CREATE']
        for kw in forbidden:
            if re.search(rf'\b{kw}\b', sql_upper):
                return jsonify({'error': f'Forbidden keyword: {kw}'}), 400

        conn = permitdb.get_connection()
        try:
            result = conn.execute(sql)
            conn.commit()
            rows_affected = result.rowcount
            print(f"[V159] Admin execute: {sql[:100]}... -> {rows_affected} rows")
            return jsonify({'success': True, 'rows_affected': rows_affected})
        finally:
            conn.close()
    except Exception as e:
        return jsonify({'error': str(e)}), 500




@app.route('/healthz')
def healthz():
    """V167: Lightweight health check for Render's TCP probe. NO DB queries."""
    return 'ok', 200


@app.route('/healthz/deep')
def healthz_deep():
    """V229 F2: Deep health check — probes the pieces /healthz deliberately
    skips. NOT wired to Render's probe (it's too slow and flaky-under-load);
    use it from ops scripts / monitors / the admin dashboard.

    Returns 200 when every subsystem is healthy, 503 when any one fails.
    Always returns JSON with a per-check breakdown so a monitor can alert
    on the specific failing check instead of "503 for reasons unknown".
    """
    import threading as _threading
    checks = {}
    overall_ok = True

    # 1. SQLite reachable + responsive
    try:
        conn = permitdb.get_connection()
        row = conn.execute("SELECT COUNT(*) FROM permits").fetchone()
        checks['sqlite'] = {'ok': True, 'permits': row[0]}
    except Exception as e:
        checks['sqlite'] = {'ok': False, 'error': str(e)[:200]}
        overall_ok = False

    # 2. Daemon thread is alive
    try:
        alive = any(t.name == 'scheduled_collection' and t.is_alive()
                    for t in _threading.enumerate())
        checks['daemon'] = {'ok': alive}
        if not alive:
            overall_ok = False
    except Exception as e:
        checks['daemon'] = {'ok': False, 'error': str(e)[:200]}
        overall_ok = False

    # 3. Last collection_log entry is <2h old (else cycles are stuck)
    try:
        conn = permitdb.get_connection()
        row = conn.execute(
            "SELECT MAX(created_at) FROM collection_log"
        ).fetchone()
        last = row[0] if row else None
        checks['last_collection'] = {'ok': True, 'at': last}
        if last:
            last_dt = datetime.strptime(last[:19], '%Y-%m-%d %H:%M:%S')
            if (datetime.utcnow() - last_dt).total_seconds() > 7200:
                checks['last_collection']['ok'] = False
                checks['last_collection']['reason'] = 'older than 2 hours'
                overall_ok = False
        else:
            checks['last_collection']['ok'] = False
            checks['last_collection']['reason'] = 'no collection_log rows'
            overall_ok = False
    except Exception as e:
        checks['last_collection'] = {'ok': False, 'error': str(e)[:200]}
        overall_ok = False

    status_code = 200 if overall_ok else 503
    return jsonify({'ok': overall_ok, 'checks': checks}), status_code


@app.route('/api/diagnostics')
def api_diagnostics():
    """V167: Full system diagnostics — memory, counts, activity."""
    valid, error = check_admin_key()
    if not valid:
        return error
    try:
        import psutil
        proc = psutil.Process(os.getpid())
        conn = permitdb.get_connection()

        def count(table):
            try:
                return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            except Exception:
                return None

        def fresh(table, col='collected_at', hours=1):
            try:
                return conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE {col} >= datetime('now', '-{hours} hours')"
                ).fetchone()[0]
            except Exception:
                return None

        last_scraper = None
        try:
            row = conn.execute(
                "SELECT run_started_at, status, source_key FROM scraper_runs ORDER BY run_started_at DESC LIMIT 1"
            ).fetchone()
            if row:
                last_scraper = dict(row)
        except Exception:
            pass

        return jsonify({
            'version': APP_VERSION,
            'uptime_seconds': int(time.time() - START_TIME),
            'memory_mb': round(proc.memory_info().rss / 1024 / 1024, 1),
            'cpu_percent': proc.cpu_percent(interval=0.1),
            'open_files': len(proc.open_files()),
            'threads': proc.num_threads(),
            'tables': {
                'prod_cities': count('prod_cities'),
                'permits': count('permits'),
                'violations': count('violations'),
                'subscribers': count('subscribers'),
            },
            'activity_last_hour': {
                'permits_collected': fresh('permits'),
                'violations_collected': fresh('violations'),
            },
            'violations_last_collect_at': conn.execute(
                "SELECT MAX(collected_at) FROM violations"
            ).fetchone()[0] if count('violations') else None,
            'permits_last_collect_at': conn.execute(
                "SELECT MAX(collected_at) FROM permits"
            ).fetchone()[0] if count('permits') else None,
            'last_scraper_run': last_scraper,
            'business': {
                'total_users': count('users'),
                'active_saved_searches': fresh('saved_searches', 'created_at', hours=999999) if count('saved_searches') else 0,
                'alerts_sent_24h': fresh('saved_searches', 'last_sent_at', hours=24) if count('saved_searches') else 0,
            },
            'env': {
                'render_service': os.environ.get('RENDER_SERVICE_ID'),
                'python_version': sys.version.split()[0],
            },
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/data-freshness', methods=['GET'])
def admin_data_freshness():
    """V12.58: Return data freshness stats for all cities. Useful for monitoring stale sources."""
    valid, error = check_admin_key()
    if not valid:
        return error

    conn = permitdb.get_connection()
    cursor = conn.execute("""
        SELECT city, state, COUNT(*) as total_permits, MAX(filing_date) as newest_date
        FROM permits
        WHERE filing_date IS NOT NULL AND filing_date != ''
        GROUP BY city, state
        ORDER BY newest_date ASC
    """)

    results = []
    now = datetime.now()
    for row in cursor:
        newest = row['newest_date']
        days_stale = None
        if newest:
            try:
                newest_dt = datetime.strptime(newest[:10], '%Y-%m-%d')
                days_stale = (now - newest_dt).days
            except (ValueError, TypeError):
                pass
        results.append({
            'city': row['city'],
            'state': row['state'],
            'total_permits': row['total_permits'],
            'newest_filing_date': newest,
            'days_stale': days_stale
        })

    # Also get cities with NULL dates
    null_dates = conn.execute("""
        SELECT city, state, COUNT(*) as count
        FROM permits
        WHERE filing_date IS NULL OR filing_date = ''
        GROUP BY city, state
        ORDER BY count DESC
    """).fetchall()

    return jsonify({
        'cities': results,
        'cities_with_null_dates': [dict(r) for r in null_dates],
        'total_cities': len(results),
        'stale_count': len([r for r in results if r['days_stale'] and r['days_stale'] > 30])
    })


@app.route('/api/admin/stale-cities', methods=['GET'])
def admin_stale_cities():
    """V18: Get stale cities review queue and freshness summary."""
    valid, error = check_admin_key()
    if not valid:
        return error

    try:
        # Get freshness summary
        summary = permitdb.get_freshness_summary()

        # Get review queue
        review_queue = permitdb.get_review_queue()

        # Get currently stale cities (active but stale)
        stale = permitdb.get_stale_cities()

        return jsonify({
            'summary': summary,
            'review_queue': review_queue,
            'currently_stale': stale,
            'thresholds': {
                'stale_days': permitdb.FRESHNESS_STALE_DAYS,
                'very_stale_days': permitdb.FRESHNESS_VERY_STALE_DAYS,
            }
        })
    except Exception as e:
        return jsonify({'error': f'Failed to get stale cities: {str(e)}'}), 500


@app.route('/api/admin/send-welcome', methods=['POST'])
def admin_send_welcome():
    """V12.53: Send welcome email to a specific user."""
    valid, error = check_admin_key()
    if not valid:
        return error

    # V12.59: Read from both query string and JSON body
    email = request.args.get('email') or (request.json or {}).get('email', '')
    email_type = request.args.get('type') or (request.json or {}).get('type', 'free')

    if not email:
        return jsonify({'error': 'Email parameter required'}), 400

    user = find_user_by_email(email)
    if not user:
        return jsonify({'error': f'User {email} not found'}), 404

    try:
        from email_alerts import send_welcome_free, send_welcome_pro_trial
        if email_type == 'pro':
            send_welcome_pro_trial(user)
        else:
            send_welcome_free(user)
        return jsonify({'status': 'sent', 'to': email, 'type': email_type})
    except Exception as e:
        return jsonify({'error': f'Welcome email failed: {str(e)}'}), 500


@app.route('/api/admin/run-trial-check', methods=['POST'])
def admin_run_trial_check():
    """V12.53: Manually run trial lifecycle check."""
    valid, error = check_admin_key()
    if not valid:
        return error

    try:
        from email_alerts import check_trial_lifecycle
        results = check_trial_lifecycle()
        return jsonify({'status': 'done', 'results': results})
    except Exception as e:
        return jsonify({'error': f'Trial check failed: {str(e)}'}), 500


@app.route('/api/admin/run-onboarding-check', methods=['POST'])
def admin_run_onboarding_check():
    """V12.53: Manually run onboarding nudge check."""
    valid, error = check_admin_key()
    if not valid:
        return error

    try:
        from email_alerts import check_onboarding_nudges
        sent = check_onboarding_nudges()
        return jsonify({'status': 'done', 'sent': sent})
    except Exception as e:
        return jsonify({'error': f'Onboarding check failed: {str(e)}'}), 500


@app.route('/api/admin/email-stats')
def admin_email_stats():
    """V12.53: Get email system statistics."""
    valid, error = check_admin_key()
    if not valid:
        return error

    try:
        users = User.query.all()

        stats = {
            'total_users': len(users),
            'digest_active': sum(1 for u in users if u.digest_active),
            'email_verified': sum(1 for u in users if u.email_verified),
            'welcome_sent': sum(1 for u in users if u.welcome_email_sent),
            'pro_trial_users': sum(1 for u in users if u.plan == 'pro_trial'),
            'pro_users': sum(1 for u in users if u.plan == 'pro'),
            'free_users': sum(1 for u in users if u.plan == 'free'),
            'trial_midpoint_sent': sum(1 for u in users if u.trial_midpoint_sent),
            'trial_ending_sent': sum(1 for u in users if u.trial_ending_sent),
            'trial_expired_sent': sum(1 for u in users if u.trial_expired_sent),
        }
        return jsonify(stats)
    except Exception as e:
        return jsonify({'error': f'Stats failed: {str(e)}'}), 500


@app.route('/api/admin/email-status')
def admin_email_status():
    """V73: Comprehensive email system health check.
    V78: Added digest daemon thread status tracking."""
    diagnostics = {
        'timestamp': datetime.now().isoformat(),
        'checks': {}
    }

    # V78: Include daemon thread status
    diagnostics['digest_daemon'] = DIGEST_STATUS.copy()

    # Check 1: SMTP environment variables
    try:
        smtp_pass = os.environ.get('SMTP_PASS', '')
        smtp_host = os.environ.get('SMTP_HOST', 'smtp.sendgrid.net')
        smtp_port = os.environ.get('SMTP_PORT', '587')
        diagnostics['checks']['smtp'] = {
            'pass_configured': bool(smtp_pass),
            'pass_length': len(smtp_pass) if smtp_pass else 0,
            'host': smtp_host,
            'port': smtp_port,
            'status': 'OK' if smtp_pass else 'MISSING SMTP_PASS'
        }
    except Exception as e:
        diagnostics['checks']['smtp'] = {'status': 'ERROR', 'error': str(e)}

    # Check 2: email_alerts.py import
    try:
        from email_alerts import SMTP_PASS, SUBSCRIBERS_FILE, load_subscribers, send_email
        diagnostics['checks']['email_alerts_import'] = {
            'status': 'OK',
            'smtp_pass_in_module': bool(SMTP_PASS)
        }
    except ImportError as e:
        diagnostics['checks']['email_alerts_import'] = {'status': 'FAILED', 'error': str(e)}
        return jsonify(diagnostics), 500

    # Check 3: Subscribers file
    try:
        diagnostics['checks']['subscribers_file'] = {
            'path': str(SUBSCRIBERS_FILE),
            'exists': SUBSCRIBERS_FILE.exists(),
            'status': 'OK' if SUBSCRIBERS_FILE.exists() else 'MISSING'
        }
        if SUBSCRIBERS_FILE.exists():
            with open(SUBSCRIBERS_FILE) as f:
                raw_subs = json.load(f)
            diagnostics['checks']['subscribers_file']['total'] = len(raw_subs)
    except Exception as e:
        diagnostics['checks']['subscribers_file'] = {'status': 'ERROR', 'error': str(e)}

    # Check 4: Load subscribers function
    try:
        subs = load_subscribers()
        diagnostics['checks']['load_subscribers'] = {
            'status': 'OK',
            'active_count': len(subs),
            'sample_emails': [s.get('email', '?')[:3] + '***' for s in subs[:5]]
        }
    except Exception as e:
        diagnostics['checks']['load_subscribers'] = {'status': 'ERROR', 'error': str(e)}

    # Overall status
    all_ok = all(
        c.get('status') == 'OK'
        for c in diagnostics['checks'].values()
    )
    diagnostics['overall_status'] = 'HEALTHY' if all_ok else 'ISSUES_FOUND'

    return jsonify(diagnostics)




@app.route('/api/admin/us-cities')
def admin_us_cities():
    """V12.54: List cities with filters."""
    admin_key = request.headers.get('X-Admin-Key') or request.args.get('key')
    expected = os.environ.get('ADMIN_KEY', 'permitgrab-reset-2026')
    if admin_key != expected:
        return jsonify({"error": "unauthorized"}), 401
    status = request.args.get('status')
    state = request.args.get('state')
    limit = int(request.args.get('limit', 50))
    offset = int(request.args.get('offset', 0))
    try:
        import db as permitdb
        conn = permitdb.get_connection()
        query = "SELECT * FROM us_cities WHERE 1=1"
        params = []
        if status:
            query += " AND status=?"
            params.append(status)
        if state:
            query += " AND state=?"
            params.append(state)
        query += " ORDER BY priority ASC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = conn.execute(query, params).fetchall()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/admin/us-counties')
def admin_us_counties():
    """V12.54: List counties with filters."""
    admin_key = request.headers.get('X-Admin-Key') or request.args.get('key')
    expected = os.environ.get('ADMIN_KEY', 'permitgrab-reset-2026')
    if admin_key != expected:
        return jsonify({"error": "unauthorized"}), 401
    status = request.args.get('status')
    limit = int(request.args.get('limit', 50))
    try:
        import db as permitdb
        conn = permitdb.get_connection()
        query = "SELECT * FROM us_counties WHERE 1=1"
        params = []
        if status:
            query += " AND status=?"
            params.append(status)
        query += " ORDER BY priority ASC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/admin/city-sources')
def admin_city_sources():
    """V12.54: List all data sources."""
    admin_key = request.headers.get('X-Admin-Key') or request.args.get('key')
    expected = os.environ.get('ADMIN_KEY', 'permitgrab-reset-2026')
    if admin_key != expected:
        return jsonify({"error": "unauthorized"}), 401
    try:
        import db as permitdb
        conn = permitdb.get_connection()
        rows = conn.execute("SELECT * FROM city_sources ORDER BY last_collected_at DESC LIMIT 200").fetchall()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/admin/bulk-sources')
def admin_bulk_sources():
    """V87: List bulk sources (county/state level)."""
    admin_key = request.headers.get('X-Admin-Key') or request.args.get('key')
    expected = os.environ.get('ADMIN_KEY', 'permitgrab-reset-2026')
    if admin_key != expected:
        return jsonify({"error": "unauthorized"}), 401
    try:
        import db as permitdb
        conn = permitdb.get_connection()
        rows = conn.execute("""
            SELECT * FROM bulk_sources ORDER BY total_permits_collected DESC LIMIT 100
        """).fetchall()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/admin/architecture-stats')
def admin_architecture_stats():
    """V87: Get clean architecture statistics."""
    admin_key = request.headers.get('X-Admin-Key') or request.args.get('key')
    expected = os.environ.get('ADMIN_KEY', 'permitgrab-reset-2026')
    if admin_key != expected:
        return jsonify({"error": "unauthorized"}), 401
    try:
        import db as permitdb
        conn = permitdb.get_connection()

        # Core counts
        total_cities = conn.execute("SELECT COUNT(*) FROM prod_cities").fetchone()[0]
        cities_with_data = conn.execute("SELECT COUNT(*) FROM prod_cities WHERE total_permits > 0").fetchone()[0]
        total_permits = conn.execute("SELECT COUNT(*) FROM permits").fetchone()[0]
        linked_permits = conn.execute("SELECT COUNT(*) FROM permits WHERE prod_city_id IS NOT NULL").fetchone()[0]

        # Source counts
        city_sources = conn.execute("SELECT COUNT(*) FROM city_sources WHERE status = 'active'").fetchone()[0]
        city_sources_linked = conn.execute("SELECT COUNT(*) FROM city_sources WHERE status = 'active' AND prod_city_id IS NOT NULL").fetchone()[0]

        # Bulk sources (may not exist yet)
        try:
            bulk_sources = conn.execute("SELECT COUNT(*) FROM bulk_sources WHERE status = 'active'").fetchone()[0]
        except Exception:
            bulk_sources = 0

        return jsonify({
            'prod_cities': {
                'total': total_cities,
                'with_data': cities_with_data,
                'without_data': total_cities - cities_with_data
            },
            'permits': {
                'total': total_permits,
                'linked': linked_permits,
                'unlinked': total_permits - linked_permits,
                'link_rate': f"{100 * linked_permits // total_permits}%" if total_permits > 0 else "0%"
            },
            'sources': {
                'city_sources': city_sources,
                'city_sources_linked': city_sources_linked,
                'bulk_sources': bulk_sources
            },
            'architecture': 'V87 - Clean FK-based relationships'
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route('/api/admin/discovery-log')
def admin_discovery_log():
    """V12.54: Recent discovery runs."""
    admin_key = request.headers.get('X-Admin-Key') or request.args.get('key')
    expected = os.environ.get('ADMIN_KEY', 'permitgrab-reset-2026')
    if admin_key != expected:
        return jsonify({"error": "unauthorized"}), 401
    try:
        import db as permitdb
        conn = permitdb.get_connection()
        rows = conn.execute("SELECT * FROM discovery_runs ORDER BY id DESC LIMIT 20").fetchall()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/admin/new-cities')
def admin_new_cities():
    """V17: Get recently activated cities for SEO tracking."""
    admin_key = request.headers.get('X-Admin-Key') or request.args.get('key')
    expected = os.environ.get('ADMIN_KEY', 'permitgrab-reset-2026')
    if admin_key != expected:
        return jsonify({"error": "unauthorized"}), 401

    days = int(request.args.get('days', 7))

    try:
        # Get recent activations
        activations = permitdb.get_recent_activations(days=days)

        # Get totals
        conn = permitdb.get_connection()
        total_active = conn.execute(
            "SELECT COUNT(*) as cnt FROM prod_cities WHERE status = 'active'"
        ).fetchone()['cnt']

        # Enrich with permit counts and page URLs
        enriched = []
        for a in activations:
            enriched.append({
                'city': a.get('city_name'),
                'state': a.get('state'),
                'slug': a.get('city_slug'),
                'activated_at': a.get('activated_at'),
                'permits': a.get('initial_permits', 0),
                'seo_status': a.get('seo_status', 'needs_content'),
                'source': a.get('source'),
                'page_url': f"https://permitgrab.com/permits/{a.get('city_slug')}"
            })

        return jsonify({
            'new_cities': enriched,
            'total_active': total_active,
            'activated_this_week': len([a for a in activations
                if a.get('activated_at', '') >= (datetime.now() - timedelta(days=7)).isoformat()])
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/admin/tracker')
def admin_tracker():
    """V64: Master city tracker — 20K rows, one per US city with coverage and freshness.

    Query params:
      state=TX — filter by state
      status=active — filter by coverage status (active/no_source)
      stale=true — only show stale/no_data cities
      limit=500 — limit rows (default 500, max 5000)
      offset=0 — pagination offset
      sort=population — sort field (population, last_permit_date, city)
    """
    valid, error = check_admin_key()
    if not valid:
        return error

    state = request.args.get('state')
    status = request.args.get('status')
    stale_only = request.args.get('stale') == 'true'
    limit = min(int(request.args.get('limit', 500)), 5000)
    offset = int(request.args.get('offset', 0))
    sort = request.args.get('sort', 'population')

    conn = permitdb.get_connection()
    try:
        # Build the tracker query
        # Join us_cities with prod_cities and scraper_runs for comprehensive view
        query = """
            SELECT
                uc.city_name,
                uc.state,
                uc.population,
                uc.slug as city_slug,
                uc.county,
                uc.covered_by_source,
                uc.status as discovery_status,
                -- Coverage info from prod_cities
                pc.status as coverage_status,
                pc.source_id,
                pc.source_type as platform,
                pc.source_scope,
                -- Freshness from prod_cities
                pc.newest_permit_date as last_permit_date,
                pc.last_collection as last_pull_date,
                pc.total_permits,
                pc.data_freshness,
                pc.consecutive_failures,
                pc.last_error
            FROM us_cities uc
            LEFT JOIN prod_cities pc ON (
                pc.city_slug = uc.slug
                OR pc.city_slug = REPLACE(uc.slug, '-', '_')
                OR pc.source_id = REPLACE(uc.slug, '-', '_')
            )
        """

        # Add WHERE clauses
        conditions = []
        params = []
        if state:
            conditions.append("uc.state = ?")
            params.append(state)
        if status == 'active':
            conditions.append("pc.status = 'active'")
        elif status == 'no_source':
            conditions.append("pc.city_slug IS NULL")
        if stale_only:
            conditions.append("(pc.data_freshness IN ('stale', 'very_stale', 'no_data') OR pc.city_slug IS NULL)")

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        # Sort
        sort_map = {
            'population': 'uc.population DESC',
            'last_permit_date': 'pc.newest_permit_date DESC',
            'city': 'uc.city_name ASC',
        }
        query += f" ORDER BY {sort_map.get(sort, 'uc.population DESC')}"
        query += " LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = conn.execute(query, params).fetchall()

        # Get total count for pagination
        count_query = "SELECT COUNT(*) FROM us_cities uc"
        if conditions:
            count_query += " LEFT JOIN prod_cities pc ON pc.city_slug = uc.slug OR pc.city_slug = REPLACE(uc.slug, '-', '_')"
            count_query += " WHERE " + " AND ".join(conditions)
        total = conn.execute(count_query, params[:-2] if len(params) > 2 else []).fetchone()[0]

        # Summary stats
        summary = {
            'total_us_cities': conn.execute("SELECT COUNT(*) FROM us_cities").fetchone()[0],
            'active_in_prod': conn.execute("SELECT COUNT(*) FROM prod_cities WHERE status='active'").fetchone()[0],
            'with_permits': conn.execute("SELECT COUNT(DISTINCT city) FROM permits WHERE city IS NOT NULL").fetchone()[0],
            'stale_count': conn.execute("SELECT COUNT(*) FROM prod_cities WHERE data_freshness IN ('stale', 'very_stale')").fetchone()[0],
        }

        return jsonify({
            'summary': summary,
            'tracker': [dict(row) for row in rows],
            'pagination': {'limit': limit, 'offset': offset, 'total': total}
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/freshness')
def admin_freshness():
    """V64: Run freshness classification and return results.

    Shows which cities are fresh, stale, broken, or have no data.
    """
    valid, error = check_admin_key()
    if not valid:
        return error

    from collector import classify_city_freshness
    try:
        result = classify_city_freshness()
        return jsonify(result)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/refresh-freshness', methods=['POST'])
def admin_refresh_freshness():
    """V71: Recalculate prod_cities freshness from actual permits table.

    Fixes the issue where 431 cities show 'no_data' despite having real permits.
    The root cause is that newest_permit_date was never populated for these cities.
    """
    valid, error = check_admin_key()
    if not valid:
        return error

    from datetime import datetime, timedelta

    try:
        today = datetime.now().strftime('%Y-%m-%d')
        thirty_days_ago = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')

        conn = permitdb.get_connection()

        # Get all active prod_cities
        cities = conn.execute(
            "SELECT city_slug, source_id, city FROM prod_cities WHERE status='active'"
        ).fetchall()

        updated = 0
        freshness_counts = {'fresh': 0, 'aging': 0, 'stale': 0, 'no_data': 0}

        for row in cities:
            city_slug = row['city_slug'] if isinstance(row, dict) else row[0]
            source_id = row['source_id'] if isinstance(row, dict) else row[1]
            city_name = row['city'] if isinstance(row, dict) else row[2]

            newest = None
            recent = 0

            # Try source_city_key match first (primary join strategy)
            if source_id:
                result = conn.execute(
                    "SELECT MAX(date) as newest, COUNT(CASE WHEN date >= ? THEN 1 END) as recent "
                    "FROM permits WHERE source_city_key = ?",
                    (thirty_days_ago, source_id)
                ).fetchone()
                if result:
                    newest = result['newest'] if isinstance(result, dict) else result[0]
                    recent = (result['recent'] if isinstance(result, dict) else result[1]) or 0

            # Fallback: try city name match
            if not newest and city_name:
                result = conn.execute(
                    "SELECT MAX(date) as newest, COUNT(CASE WHEN date >= ? THEN 1 END) as recent "
                    "FROM permits WHERE city = ?",
                    (thirty_days_ago, city_name)
                ).fetchone()
                if result:
                    newest = result['newest'] if isinstance(result, dict) else result[0]
                    recent = (result['recent'] if isinstance(result, dict) else result[1]) or 0

            # Calculate freshness
            if newest:
                try:
                    days_old = (datetime.now() - datetime.strptime(newest, '%Y-%m-%d')).days
                    if days_old <= 14:
                        freshness = 'fresh'
                    elif days_old <= 30:
                        freshness = 'aging'
                    elif days_old <= 90:
                        freshness = 'stale'
                    else:
                        freshness = 'no_data'
                except Exception:
                    freshness = 'no_data'
            else:
                freshness = 'no_data'

            # Update prod_cities
            conn.execute(
                "UPDATE prod_cities SET newest_permit_date=?, permits_last_30d=?, data_freshness=? "
                "WHERE city_slug=?",
                (newest, recent, freshness, city_slug)
            )

            freshness_counts[freshness] = freshness_counts.get(freshness, 0) + 1
            updated += 1

        conn.commit()

        return jsonify({
            'status': 'success',
            'updated': updated,
            'freshness': freshness_counts
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/activate-city-sources', methods=['POST'])
def admin_activate_city_sources():
    """V71: Activate all inactive city_sources that have matching active prod_cities entries.

    Fixes the issue where 329 city_sources are 'inactive' despite having active prod_cities.
    """
    valid, error = check_admin_key()
    if not valid:
        return error

    try:
        conn = permitdb.get_connection()

        # First: activate city_sources where there's a matching active prod_city
        conn.execute("""
            UPDATE city_sources SET status='active'
            WHERE status='inactive'
            AND source_key IN (SELECT source_id FROM prod_cities WHERE status='active')
        """)
        # Can't get rowcount reliably from all db backends, so we'll count after

        # Count how many are now active vs inactive
        active_result = conn.execute("SELECT COUNT(*) as cnt FROM city_sources WHERE status='active'").fetchone()
        inactive_result = conn.execute("SELECT COUNT(*) as cnt FROM city_sources WHERE status='inactive'").fetchone()

        active_count = active_result['cnt'] if isinstance(active_result, dict) else active_result[0]
        inactive_count = inactive_result['cnt'] if isinstance(inactive_result, dict) else inactive_result[0]

        conn.commit()

        return jsonify({
            'status': 'success',
            'city_sources_active': active_count,
            'city_sources_inactive': inactive_count,
            'message': 'Activated city_sources matching active prod_cities'
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/reactivate-from-configs', methods=['POST'])
def admin_reactivate_from_configs():
    """V84: Reactivate city_sources based on active configs in city_configs.py.

    This fixes the issue where sources were mass-deactivated by V35 but have
    valid active configs. It reactivates sources where:
    1. The source_key matches a key in CITY_REGISTRY or BULK_SOURCES
    2. The config has active=True (or no active field, defaulting to True)
    """
    valid, error = check_admin_key()
    if not valid:
        return error

    try:
        from city_configs import CITY_REGISTRY, BULK_SOURCES

        # Build set of active config keys
        active_config_keys = set()
        for key, cfg in CITY_REGISTRY.items():
            if cfg.get('active', True):  # Default True if not specified
                active_config_keys.add(key)
        for key, cfg in BULK_SOURCES.items():
            if cfg.get('active', True):
                active_config_keys.add(key)

        conn = permitdb.get_connection()

        # Get current inactive sources
        inactive_sources = conn.execute("""
            SELECT source_key FROM city_sources WHERE status = 'inactive'
        """).fetchall()
        inactive_keys = {r['source_key'] for r in inactive_sources}

        # Find which ones should be reactivated
        to_reactivate = inactive_keys & active_config_keys

        if to_reactivate:
            # Reactivate them
            placeholders = ','.join(['?' for _ in to_reactivate])
            conn.execute(f"""
                UPDATE city_sources
                SET status = 'active',
                    last_failure_reason = 'v84_reactivated_from_config',
                    consecutive_failures = 0
                WHERE source_key IN ({placeholders})
            """, list(to_reactivate))
            conn.commit()

        # Get final counts
        active_count = conn.execute("SELECT COUNT(*) as cnt FROM city_sources WHERE status='active'").fetchone()
        inactive_count = conn.execute("SELECT COUNT(*) as cnt FROM city_sources WHERE status='inactive'").fetchone()

        return jsonify({
            'status': 'success',
            'reactivated': len(to_reactivate),
            'active_configs_count': len(active_config_keys),
            'city_sources_active': active_count['cnt'] if isinstance(active_count, dict) else active_count[0],
            'city_sources_inactive': inactive_count['cnt'] if isinstance(inactive_count, dict) else inactive_count[0],
            'message': f'Reactivated {len(to_reactivate)} sources from active configs'
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/reset-failures', methods=['POST'])
def admin_reset_failures():
    """V72: Reset consecutive_failures for a city or all cities.

    POST body: {"city_slug": "kansas-city"} to reset one city
    POST body: {} or {"city_slug": "all"} to reset all cities
    """
    valid, error = check_admin_key()
    if not valid:
        return error

    try:
        data = request.get_json() or {}
        city_slug = data.get('city_slug', 'all')

        conn = permitdb.get_connection()

        if city_slug == 'all':
            conn.execute("UPDATE prod_cities SET consecutive_failures=0, consecutive_no_new=0")
            result = conn.execute("SELECT COUNT(*) as cnt FROM prod_cities").fetchone()
            count = result['cnt'] if isinstance(result, dict) else result[0]
            conn.commit()
            return jsonify({
                'status': 'success',
                'message': f'Reset consecutive_failures for all {count} cities'
            })
        else:
            conn.execute(
                "UPDATE prod_cities SET consecutive_failures=0, consecutive_no_new=0 WHERE city_slug=?",
                (city_slug,)
            )
            conn.commit()
            return jsonify({
                'status': 'success',
                'message': f'Reset consecutive_failures for {city_slug}'
            })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


def fix_known_broken_configs():
    """V75: Fix known broken city_sources configs via SQL UPDATE statements.

    This function runs SQL updates against the SQLite database to fix:
    1. High-failure cities (reset consecutive_failures, reactivate)
    2. Platform mismatches (e.g., Rochester listed as socrata but actually accela)
    3. Slug/key mismatches between prod_cities and city_sources

    IMPORTANT: Python dict changes (BULK_SOURCES, CITY_REGISTRY) do NOT affect
    runtime behavior. The collector reads from city_sources table in SQLite.
    """
    try:
        conn = permitdb.get_connection()
        fixes_applied = []

        # =================================================================
        # FIX 1: Reset high-failure cities in city_sources
        # =================================================================
        high_failure_cities = [
            'san_antonio', 'pittsburgh', 'fort_worth', 'washington_dc',
            'atlanta', 'bloomington_in', 'honolulu', 'kansas_city', 'round_rock'
        ]

        for city_key in high_failure_cities:
            result = conn.execute(
                "SELECT source_key FROM city_sources WHERE source_key = ?",
                (city_key,)
            ).fetchone()
            if result:
                conn.execute("""
                    UPDATE city_sources
                    SET consecutive_failures = 0,
                        last_failure_reason = NULL,
                        status = 'active'
                    WHERE source_key = ?
                """, (city_key,))
                fixes_applied.append(f"Reset failures for {city_key} in city_sources")

        # =================================================================
        # FIX 2: Rochester - update to Accela platform
        # =================================================================
        # Rochester NY uses Accela, not Socrata. Update both city_sources and prod_cities.
        conn.execute("""
            UPDATE city_sources
            SET platform = 'accela',
                endpoint = 'https://aca-prod.accela.com/ROCHESTER/Cap/CapHome.aspx?module=Building&TabName=Building',
                consecutive_failures = 0,
                last_failure_reason = NULL,
                status = 'active'
            WHERE source_key = 'rochester_ny'
        """)
        fixes_applied.append("Updated rochester_ny to Accela platform in city_sources")

        # Also update prod_cities source_type
        conn.execute("""
            UPDATE prod_cities
            SET source_type = 'accela'
            WHERE city_slug = 'rochester' AND source_type = 'socrata'
        """)
        fixes_applied.append("Updated rochester source_type to accela in prod_cities")

        # =================================================================
        # FIX 2b: V76 - Fix platform mismatches for 3 major cities
        # =================================================================
        # These cities had wrong platform labels causing the wrong fetcher to be used.
        # fort_worth: endpoint is ArcGIS FeatureServer, not Socrata
        conn.execute("""
            UPDATE city_sources
            SET platform = 'arcgis'
            WHERE source_key = 'fort_worth' AND platform != 'arcgis'
        """)
        fixes_applied.append("Fixed fort_worth platform to arcgis")

        # san_antonio: endpoint is CKAN API, not Socrata
        conn.execute("""
            UPDATE city_sources
            SET platform = 'ckan'
            WHERE source_key = 'san_antonio' AND platform != 'ckan'
        """)
        fixes_applied.append("Fixed san_antonio platform to ckan")

        # washington_dc: endpoint is ArcGIS FeatureServer, not Socrata
        conn.execute("""
            UPDATE city_sources
            SET platform = 'arcgis'
            WHERE source_key = 'washington_dc' AND platform != 'arcgis'
        """)
        fixes_applied.append("Fixed washington_dc platform to arcgis")

        # =================================================================
        # FIX 2c: V76 - Sync prod_cities source_type for these 3 cities
        # =================================================================
        # Note: prod_cities uses hyphens, city_sources uses underscores
        conn.execute("UPDATE prod_cities SET source_type = 'arcgis' WHERE city_slug = 'fort-worth'")
        conn.execute("UPDATE prod_cities SET source_type = 'ckan' WHERE city_slug = 'san-antonio'")
        conn.execute("UPDATE prod_cities SET source_type = 'arcgis' WHERE city_slug = 'washington-dc'")
        fixes_applied.append("Synced prod_cities source_type for fort-worth, san-antonio, washington-dc")

        # =================================================================
        # FIX 3: Ensure slug/key mappings work
        # =================================================================
        # For cities where prod_cities uses hyphen (kansas-city) but city_sources
        # uses underscore (kansas_city), ensure there's a covers_cities mapping
        # or rename the source_key. For now, add covers_cities entries.

        # Check if kansas_city exists in city_sources
        kc_result = conn.execute(
            "SELECT source_key, covers_cities FROM city_sources WHERE source_key = 'kansas_city'"
        ).fetchone()
        if kc_result:
            # Add kansas-city to covers_cities if not already there
            covers = kc_result[1] if kc_result[1] else ''
            if 'kansas-city' not in covers:
                new_covers = f"{covers},kansas-city" if covers else "kansas-city"
                conn.execute(
                    "UPDATE city_sources SET covers_cities = ? WHERE source_key = 'kansas_city'",
                    (new_covers,)
                )
                fixes_applied.append("Added kansas-city to covers_cities for kansas_city")

        # =================================================================
        # FIX 4: Reset all consecutive_failures in prod_cities for affected cities
        # =================================================================
        affected_slugs = [
            'san-antonio', 'pittsburgh', 'fort-worth', 'washington-dc',
            'atlanta', 'bloomington-in', 'honolulu', 'kansas-city', 'round-rock',
            'rochester', 'milwaukee', 'indianapolis', 'oklahoma-city'
        ]
        conn.execute(f"""
            UPDATE prod_cities
            SET consecutive_failures = 0, consecutive_no_new = 0
            WHERE city_slug IN ({','.join('?' for _ in affected_slugs)})
        """, affected_slugs)
        fixes_applied.append(f"Reset failures in prod_cities for {len(affected_slugs)} cities")

        conn.commit()

        # =================================================================
        # FIX 5: V76 - Run platform audit with auto-fix
        # =================================================================
        # This catches any remaining platform/endpoint mismatches we didn't
        # explicitly handle above
        try:
            audit_result = audit_platform_mismatches(auto_fix=True)
            if audit_result.get('auto_fixed'):
                fixes_applied.append(f"Auto-fixed {len(audit_result['auto_fixed'])} platform mismatches: {audit_result['auto_fixed']}")
        except Exception as audit_err:
            print(f"[V76] Platform audit error (non-fatal): {audit_err}")

        print(f"[V76] fix_known_broken_configs applied {len(fixes_applied)} fixes:")
        for fix in fixes_applied:
            print(f"  - {fix}")

        return fixes_applied

    except Exception as e:
        import traceback
        print(f"[V75] fix_known_broken_configs error: {e}")
        traceback.print_exc()
        return [f"ERROR: {str(e)}"]


@app.route('/api/admin/fix-broken-configs', methods=['POST'])
def admin_fix_broken_configs():
    """V75: Apply known fixes to broken city_sources configs.

    This endpoint runs SQL UPDATE statements to fix:
    - High-failure cities (reset failures, reactivate)
    - Platform mismatches (e.g., Rochester socrata -> accela)
    - Slug/key mismatches

    POST body: {} (no parameters needed)
    """
    valid, error = check_admin_key()
    if not valid:
        return error

    try:
        fixes = fix_known_broken_configs()
        return jsonify({
            'status': 'success',
            'fixes_applied': fixes,
            'count': len(fixes)
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


def audit_platform_mismatches(auto_fix=False):
    """V76: Audit city_sources for platform/endpoint mismatches.

    Checks if the platform field matches the endpoint URL pattern:
    - "arcgis.com" or "FeatureServer" or "MapServer" → should be "arcgis"
    - "/api/3/action/" → should be "ckan"
    - "accela.com" → should be "accela"
    - ".json" with socrata-like domain → likely "socrata"

    Returns a report of mismatches and optionally auto-fixes them.
    """
    try:
        conn = permitdb.get_connection()
        mismatches = []
        fixed = []

        rows = conn.execute("""
            SELECT source_key, platform, endpoint FROM city_sources
            WHERE endpoint IS NOT NULL AND endpoint != ''
        """).fetchall()

        for row in rows:
            source_key = row[0]
            current_platform = row[1] or ''
            endpoint = row[2] or ''
            endpoint_lower = endpoint.lower()

            detected_platform = None

            # Detect platform from endpoint URL
            if 'arcgis.com' in endpoint_lower or 'featureserver' in endpoint_lower or 'mapserver' in endpoint_lower:
                detected_platform = 'arcgis'
            elif '/api/3/action/' in endpoint_lower:
                detected_platform = 'ckan'
            elif 'accela.com' in endpoint_lower:
                detected_platform = 'accela'
            elif endpoint_lower.endswith('.json') and '.gov' in endpoint_lower:
                detected_platform = 'socrata'

            # Check for mismatch
            if detected_platform and current_platform != detected_platform:
                mismatch = {
                    'source_key': source_key,
                    'current_platform': current_platform,
                    'detected_platform': detected_platform,
                    'endpoint': endpoint[:80] + '...' if len(endpoint) > 80 else endpoint
                }
                mismatches.append(mismatch)

                if auto_fix:
                    conn.execute(
                        "UPDATE city_sources SET platform = ? WHERE source_key = ?",
                        (detected_platform, source_key)
                    )
                    fixed.append(source_key)

        if auto_fix and fixed:
            conn.commit()

        return {
            'total_checked': len(rows),
            'mismatches_found': len(mismatches),
            'mismatches': mismatches,
            'auto_fixed': fixed if auto_fix else []
        }

    except Exception as e:
        import traceback
        traceback.print_exc()
        return {'error': str(e)}


@app.route('/api/admin/audit-platforms', methods=['POST'])
def admin_audit_platforms():
    """V76: Audit city_sources for platform/endpoint mismatches.

    POST body:
    {
        "auto_fix": true  // optional, default false - automatically fix mismatches
    }
    """
    valid, error = check_admin_key()
    if not valid:
        return error

    try:
        data = request.get_json() or {}
        auto_fix = data.get('auto_fix', False)

        report = audit_platform_mismatches(auto_fix=auto_fix)
        return jsonify({
            'status': 'success',
            **report
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/update-source', methods=['POST'])
def admin_update_source():
    """V75: Update a city_sources row via SQL.

    This endpoint allows runtime updates to city_sources without redeploying.
    Useful for fixing broken configs, updating endpoints, resetting failures, etc.

    POST body:
    {
        "source_key": "kansas_city",
        "updates": {
            "endpoint": "https://data.kcmo.org/resource/NEW_ID.json",
            "dataset_id": "NEW_ID",
            "status": "active",
            "consecutive_failures": 0,
            "last_failure_reason": null
        }
    }

    Allowed fields to update:
    - endpoint, dataset_id, platform, date_field, field_map
    - status, consecutive_failures, last_failure_reason
    - covers_cities, limit_per_page
    """
    valid, error = check_admin_key()
    if not valid:
        return error

    try:
        data = request.get_json() or {}
        source_key = data.get('source_key')
        updates = data.get('updates', {})

        if not source_key:
            return jsonify({'error': 'source_key is required'}), 400

        if not updates:
            return jsonify({'error': 'updates object is required'}), 400

        # Whitelist of allowed fields to update
        allowed_fields = {
            'endpoint', 'dataset_id', 'platform', 'date_field', 'field_map',
            'status', 'consecutive_failures', 'last_failure_reason',
            'covers_cities', 'limit_per_page', 'name', 'state'
        }

        # Filter to only allowed fields
        filtered_updates = {k: v for k, v in updates.items() if k in allowed_fields}
        if not filtered_updates:
            return jsonify({'error': f'No valid fields to update. Allowed: {allowed_fields}'}), 400

        conn = permitdb.get_connection()

        # Check if source exists
        existing = conn.execute(
            "SELECT source_key FROM city_sources WHERE source_key = ?",
            (source_key,)
        ).fetchone()

        if not existing:
            return jsonify({'error': f'Source {source_key} not found in city_sources'}), 404

        # Build UPDATE query
        set_clauses = []
        values = []
        for field, value in filtered_updates.items():
            set_clauses.append(f"{field} = ?")
            # Handle None/null for last_failure_reason
            values.append(value if value is not None else None)

        values.append(source_key)

        query = f"UPDATE city_sources SET {', '.join(set_clauses)} WHERE source_key = ?"
        conn.execute(query, values)
        conn.commit()

        return jsonify({
            'status': 'success',
            'source_key': source_key,
            'fields_updated': list(filtered_updates.keys())
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/cleanup-prod-cities', methods=['POST'])
def admin_cleanup_prod_cities():
    """V75: Clean up inflated prod_cities entries.

    This removes or deactivates prod_cities entries that:
    1. Have never been collected (last_collection IS NULL)
    2. Have 0 total_permits
    3. Don't have a matching active city_sources entry

    POST body:
    {
        "mode": "deactivate"  // or "delete" (default: deactivate)
    }
    """
    valid, error = check_admin_key()
    if not valid:
        return error

    try:
        data = request.get_json() or {}
        mode = data.get('mode', 'deactivate')

        conn = permitdb.get_connection()

        if mode == 'delete':
            # Delete entries that have never collected and have 0 permits
            result = conn.execute("""
                DELETE FROM prod_cities
                WHERE last_collection IS NULL
                AND total_permits = 0
                AND source_id NOT IN (SELECT source_key FROM city_sources WHERE status='active')
            """)
            action = 'deleted'
        else:
            # V76: Use 'paused' instead of 'inactive' — CHECK constraint only allows
            # 'active', 'paused', 'failed', 'pending'
            conn.execute("""
                UPDATE prod_cities SET status = 'paused'
                WHERE last_collection IS NULL
                AND total_permits = 0
                AND status = 'active'
                AND source_id NOT IN (SELECT source_key FROM city_sources WHERE status='active')
            """)
            action = 'paused'

        # Get counts
        active_count = conn.execute(
            "SELECT COUNT(*) FROM prod_cities WHERE status='active'"
        ).fetchone()[0]
        paused_count = conn.execute(
            "SELECT COUNT(*) FROM prod_cities WHERE status='paused'"
        ).fetchone()[0]
        no_data_count = conn.execute(
            "SELECT COUNT(*) FROM prod_cities WHERE status='active' AND total_permits=0"
        ).fetchone()[0]

        conn.commit()

        return jsonify({
            'status': 'success',
            'action': action,
            'prod_cities_active': active_count,
            'prod_cities_paused': paused_count,
            'no_data_count': no_data_count
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/activate-paused-cities', methods=['POST'])
def admin_activate_paused_cities():
    """V77: Bulk-activate paused cities that have valid CITY_REGISTRY configs.

    This activates the 529+ cities that were synced from CITY_REGISTRY but
    inserted as status='paused' and never collected.

    For each paused city:
    1. Check if source_id has valid config in city_sources (active) OR in CITY_REGISTRY (active=True)
    2. If yes: activate in prod_cities AND activate matching city_sources entry
    3. Return count of activated cities

    POST body: {} (no parameters needed)
    """
    valid, error = check_admin_key()
    if not valid:
        return error

    try:
        from city_configs import CITY_REGISTRY

        conn = permitdb.get_connection()
        activated = []
        skipped = []
        city_sources_activated = []

        # Get all paused cities
        paused_cities = conn.execute("""
            SELECT city_slug, source_id, city, state FROM prod_cities WHERE status = 'paused'
        """).fetchall()

        for row in paused_cities:
            city_slug = row[0]
            source_id = row[1]
            city_name = row[2]
            state = row[3]

            # Check if source_id has valid config
            has_valid_config = False

            # Check 1: city_sources table
            cs_row = conn.execute(
                "SELECT source_key, status FROM city_sources WHERE source_key = ?",
                (source_id,)
            ).fetchone()

            if cs_row:
                has_valid_config = True
                # Also activate city_sources if it's inactive
                if cs_row[1] != 'active':
                    conn.execute(
                        "UPDATE city_sources SET status = 'active' WHERE source_key = ?",
                        (source_id,)
                    )
                    city_sources_activated.append(source_id)

            # Check 2: CITY_REGISTRY dict (if not found in city_sources)
            if not has_valid_config and source_id in CITY_REGISTRY:
                if CITY_REGISTRY[source_id].get('active', False):
                    has_valid_config = True

            # Check 3: Try hyphen-to-underscore conversion
            if not has_valid_config:
                underscore_id = source_id.replace('-', '_')
                if underscore_id in CITY_REGISTRY:
                    if CITY_REGISTRY[underscore_id].get('active', False):
                        has_valid_config = True

            if has_valid_config:
                # Activate in prod_cities
                conn.execute("""
                    UPDATE prod_cities SET status = 'active', notes = 'V77: Bulk activated from paused'
                    WHERE city_slug = ?
                """, (city_slug,))
                activated.append({'city_slug': city_slug, 'source_id': source_id})
            else:
                skipped.append({'city_slug': city_slug, 'source_id': source_id, 'reason': 'no valid config'})

        conn.commit()

        # Get final counts
        active_count = conn.execute("SELECT COUNT(*) FROM prod_cities WHERE status='active'").fetchone()[0]
        paused_count = conn.execute("SELECT COUNT(*) FROM prod_cities WHERE status='paused'").fetchone()[0]

        return jsonify({
            'status': 'success',
            'activated_count': len(activated),
            'skipped_count': len(skipped),
            'city_sources_activated': len(city_sources_activated),
            'prod_cities_active': active_count,
            'prod_cities_paused': paused_count,
            'activated': activated[:50],  # Limit response size
            'skipped': skipped[:50]
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/trigger-search', methods=['POST'])
def admin_trigger_search():
    """V12.54: Manually trigger search for a city or county."""
    admin_key = request.headers.get('X-Admin-Key') or request.args.get('key')
    expected = os.environ.get('ADMIN_KEY', 'permitgrab-reset-2026')
    if admin_key != expected:
        return jsonify({"error": "unauthorized"}), 401
    data = request.get_json() or {}
    slug = data.get('slug')
    fips = data.get('fips')
    try:
        if fips:
            from city_source_db import update_county_status
            update_county_status(fips, 'not_started')
            return jsonify({"status": "ok", "message": f"County {fips} reset to not_started"})
        elif slug:
            from city_source_db import update_city_status
            update_city_status(slug, 'not_started')
            return jsonify({"status": "ok", "message": f"City {slug} reset to not_started"})
        return jsonify({"error": "provide slug or fips"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/admin/traffic', methods=['GET'])
def admin_traffic():
    """V12.59b: Query persistent page view data from PostgreSQL."""
    admin_key = request.headers.get('X-Admin-Key', '')
    if admin_key != os.environ.get('ADMIN_KEY', ''):
        return jsonify({'error': 'Unauthorized'}), 401

    try:
        import psycopg2
        conn = psycopg2.connect(os.environ.get('DATABASE_URL', ''))
        cur = conn.cursor()

        hours = int(request.args.get('hours', 24))

        # Total page views
        cur.execute("SELECT COUNT(*) FROM page_views WHERE created_at > NOW() - INTERVAL '%s hours'", (hours,))
        total_views = cur.fetchone()[0]

        # Unique IPs (proxy for unique visitors)
        cur.execute("SELECT COUNT(DISTINCT ip_address) FROM page_views WHERE created_at > NOW() - INTERVAL '%s hours'", (hours,))
        unique_ips = cur.fetchone()[0]

        # Views by path
        cur.execute("""
            SELECT path, COUNT(*) as hits
            FROM page_views WHERE created_at > NOW() - INTERVAL '%s hours'
            GROUP BY path ORDER BY hits DESC LIMIT 20
        """, (hours,))
        paths = [{'path': r[0], 'hits': r[1]} for r in cur.fetchall()]

        # Views by user agent type
        cur.execute("""
            SELECT
                CASE
                    WHEN user_agent ILIKE '%%googlebot%%' THEN 'Googlebot'
                    WHEN user_agent ILIKE '%%bingbot%%' THEN 'Bingbot'
                    WHEN user_agent ILIKE '%%curl%%' THEN 'curl'
                    WHEN user_agent ILIKE '%%python%%' THEN 'Python'
                    WHEN user_agent ILIKE '%%chrome%%' THEN 'Chrome'
                    WHEN user_agent ILIKE '%%firefox%%' THEN 'Firefox'
                    WHEN user_agent ILIKE '%%safari%%' AND user_agent NOT ILIKE '%%chrome%%' THEN 'Safari'
                    WHEN user_agent ILIKE '%%bot%%' OR user_agent ILIKE '%%spider%%' OR user_agent ILIKE '%%crawl%%' THEN 'Other Bot'
                    ELSE 'Other'
                END as agent_type,
                COUNT(*) as hits
            FROM page_views WHERE created_at > NOW() - INTERVAL '%s hours'
            GROUP BY agent_type ORDER BY hits DESC
        """, (hours,))
        agents = [{'agent': r[0], 'hits': r[1]} for r in cur.fetchall()]

        # Recent views (last 10)
        cur.execute("""
            SELECT path, user_agent, ip_address, created_at::text
            FROM page_views ORDER BY created_at DESC LIMIT 10
        """)
        recent = [{'path': r[0], 'user_agent': r[1][:80] if r[1] else '', 'ip': r[2], 'time': r[3]} for r in cur.fetchall()]

        # Hourly breakdown (last 24h)
        cur.execute("""
            SELECT date_trunc('hour', created_at)::text as hour, COUNT(*) as hits
            FROM page_views WHERE created_at > NOW() - INTERVAL '24 hours'
            GROUP BY hour ORDER BY hour
        """)
        hourly = [{'hour': r[0], 'hits': r[1]} for r in cur.fetchall()]

        cur.close()
        conn.close()

        return jsonify({
            'period_hours': hours,
            'total_views': total_views,
            'unique_visitors': unique_ips,
            'paths': paths,
            'user_agents': agents,
            'recent': recent,
            'hourly': hourly
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ===========================
# DATABASE SETUP (V7)
# ===========================
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.exc import IntegrityError

# Configure PostgreSQL database (Render provides DATABASE_URL)
database_url = os.environ.get('DATABASE_URL', '')
# Render uses 'postgres://' but SQLAlchemy needs 'postgresql://'
if database_url.startswith('postgres://'):
    database_url = database_url.replace('postgres://', 'postgresql://', 1)

if database_url:
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
    print(f"[Database] Using PostgreSQL database")
else:
    # Fallback to SQLite for local development
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///permitgrab.db'
    print(f"[Database] Using SQLite (local development)")

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
# V11 Hotfix: pool_pre_ping verifies connections, pool_recycle prevents stale connections
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_pre_ping': True,
    'pool_recycle': 300,
}

# V12.24: Google Analytics and Search Console from env vars
app.config['GOOGLE_ANALYTICS_ID'] = os.environ.get('GOOGLE_ANALYTICS_ID', '')
app.config['GOOGLE_SITE_VERIFICATION'] = os.environ.get('GOOGLE_SITE_VERIFICATION', '')
# V30: Remarketing pixel IDs — set these env vars on Render to activate
app.config['GOOGLE_ADS_ID'] = os.environ.get('GOOGLE_ADS_ID', '')  # e.g. AW-XXXXXXXXX
app.config['META_PIXEL_ID'] = os.environ.get('META_PIXEL_ID', '')   # Facebook/Meta pixel ID

db = SQLAlchemy(app)


class User(db.Model):
    """User model for PostgreSQL storage (V7 - replaces JSON file)."""
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    name = db.Column(db.String(255), nullable=False, default='')
    password_hash = db.Column(db.String(255), nullable=False)
    plan = db.Column(db.String(50), default='free')
    city = db.Column(db.String(255))
    trade = db.Column(db.String(255))
    daily_alerts = db.Column(db.Boolean, default=False)
    onboarding_completed = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    stripe_customer_id = db.Column(db.String(255))
    stripe_subscription_id = db.Column(db.String(255))
    stripe_subscription_status = db.Column(db.String(50))
    # V12.26: Competitor Watch - JSON list of competitor names to track
    watched_competitors = db.Column(db.Text, default='[]')
    # V12.26: Weekly digest city subscriptions - JSON list of city names
    digest_cities = db.Column(db.Text, default='[]')
    # V251 F4: per-user filter defaults for daily digest.
    digest_zip_filter = db.Column(db.String(16), nullable=True)
    digest_trade_filter = db.Column(db.String(64), nullable=True)

    # V12.53: Email system fields
    email_verified = db.Column(db.Boolean, default=False)
    email_verified_at = db.Column(db.DateTime)
    email_verification_token = db.Column(db.String(64))
    unsubscribe_token = db.Column(db.String(64))
    digest_active = db.Column(db.Boolean, default=True)  # Can receive digest emails
    last_login_at = db.Column(db.DateTime)
    last_digest_sent_at = db.Column(db.DateTime)
    last_reengagement_sent_at = db.Column(db.DateTime)
    # Trial tracking
    trial_started_at = db.Column(db.DateTime)
    trial_end_date = db.Column(db.DateTime)
    trial_midpoint_sent = db.Column(db.Boolean, default=False)
    trial_ending_sent = db.Column(db.Boolean, default=False)
    trial_expired_sent = db.Column(db.Boolean, default=False)
    # Welcome email tracking
    welcome_email_sent = db.Column(db.Boolean, default=False)

    def to_dict(self):
        """Convert to dictionary for JSON responses."""
        return {
            'id': self.id,
            'email': self.email,
            'name': self.name,
            'plan': self.plan,
            'city': self.city,
            'trade': self.trade,
            'daily_alerts': self.daily_alerts,
            'onboarding_completed': self.onboarding_completed,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'stripe_customer_id': self.stripe_customer_id,
            'stripe_subscription_id': self.stripe_subscription_id,
            'stripe_subscription_status': self.stripe_subscription_status,
            # V12.26: Competitor Watch and Digest Cities
            'watched_competitors': json.loads(self.watched_competitors or '[]'),
            'digest_cities': json.loads(self.digest_cities or '[]'),
            # V12.53: Email system fields
            'email_verified': self.email_verified,
            'digest_active': self.digest_active,
            'trial_end_date': self.trial_end_date.isoformat() if self.trial_end_date else None,
        }

    def is_pro(self):
        """Check if user has Pro access (paid or trial)."""
        if self.plan in ('professional', 'pro', 'enterprise'):
            # Check if trial has expired
            if self.trial_end_date and datetime.utcnow() > self.trial_end_date:
                return False
            return True
        return False

    def days_until_trial_ends(self):
        """Get days remaining in trial, or None if not on trial."""
        if not self.trial_end_date:
            return None
        delta = self.trial_end_date - datetime.utcnow()
        return max(0, delta.days)


class SavedSearch(db.Model):
    """V170 B4: User saved search for daily alerts."""
    __tablename__ = 'saved_searches'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    name = db.Column(db.String(255), nullable=False)
    city_slug = db.Column(db.String(255))
    trade = db.Column(db.String(100))
    tier = db.Column(db.String(50))
    min_value = db.Column(db.Integer)
    frequency = db.Column(db.String(20), nullable=False, default='daily')
    last_sent_at = db.Column(db.DateTime)
    active = db.Column(db.Integer, nullable=False, default=1)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    user = db.relationship('User', backref=db.backref('saved_searches', lazy=True))

    def to_dict(self):
        return {
            'id': self.id, 'name': self.name, 'city_slug': self.city_slug,
            'trade': self.trade, 'tier': self.tier, 'min_value': self.min_value,
            'frequency': self.frequency, 'active': self.active,
            'last_sent_at': self.last_sent_at.isoformat() if self.last_sent_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


# Create tables on startup
with app.app_context():
    db.create_all()

    # V12.57: Auto-migrate missing columns — db.create_all() only creates new tables,
    # it won't add columns to existing tables. This fixes the daily digest crash
    # caused by users.watched_competitors not existing in Postgres.
    migration_columns = [
        ("watched_competitors", "TEXT DEFAULT '[]'"),
        ("digest_cities", "TEXT DEFAULT '[]'"),
        # V251 F4: per-user filter defaults for the daily digest.
        ("digest_zip_filter", "VARCHAR(16)"),
        ("digest_trade_filter", "VARCHAR(64)"),
        ("email_verified", "BOOLEAN DEFAULT FALSE"),
        ("email_verified_at", "TIMESTAMP"),
        ("email_verification_token", "VARCHAR(64)"),
        ("unsubscribe_token", "VARCHAR(64)"),
        ("digest_active", "BOOLEAN DEFAULT TRUE"),
        ("last_login_at", "TIMESTAMP"),
        ("last_digest_sent_at", "TIMESTAMP"),
        ("last_reengagement_sent_at", "TIMESTAMP"),
        ("trial_started_at", "TIMESTAMP"),
        ("trial_end_date", "TIMESTAMP"),
        ("trial_midpoint_sent", "BOOLEAN DEFAULT FALSE"),
        ("trial_ending_sent", "BOOLEAN DEFAULT FALSE"),
        ("trial_expired_sent", "BOOLEAN DEFAULT FALSE"),
        ("welcome_email_sent", "BOOLEAN DEFAULT FALSE"),
    ]
    try:
        for col_name, col_type in migration_columns:
            db.session.execute(db.text(
                f"ALTER TABLE users ADD COLUMN IF NOT EXISTS {col_name} {col_type}"
            ))
        db.session.commit()
        print("[Database] Tables created/verified, columns migrated")
    except Exception as e:
        db.session.rollback()
        print(f"[Database] Tables created, migration warning: {e}")


# Rate limiter setup
limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=["2000 per day", "200 per hour"],
    storage_uri="memory://",
)

# Use Render persistent disk if available, otherwise local data directory
# Render disk is mounted at /var/data and persists across deploys
if os.path.isdir('/var/data'):
    DATA_DIR = '/var/data'
    print("[Server] Using Render persistent disk at /var/data")
else:
    DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
    print(f"[Server] Using local data directory at {DATA_DIR}")

# V11 Hotfix: Diagnostic logging for data directory
print(f"[Server] /var/data exists: {os.path.isdir('/var/data')}")
print(f"[Server] /var/data contents: {os.listdir('/var/data') if os.path.isdir('/var/data') else 'N/A'}")
print(f"[Server] DATA_DIR = {DATA_DIR}")
print(f"[Server] DATA_DIR exists: {os.path.isdir(DATA_DIR)}")
if os.path.isdir(DATA_DIR):
    print(f"[Server] DATA_DIR contents: {os.listdir(DATA_DIR)}")

# V12.1: Removed _sanitize_permits_file() - raw byte stripping corrupted JSON structure
# The correct approach is parse-then-rewrite in load_permits() using strict=False

# ============================================================================
# V12.32: AUTO-DISCOVER CITIES FROM PERMIT DATA
# ============================================================================
# Bulk sources create permits for cities not in CITY_REGISTRY. This module
# scans permit data to discover all cities and enables routing for them.

import re
_discovered_cities_cache = {}
_discovered_cities_timestamp = 0

def slugify_for_lookup(city_name, state):
    """Generate a URL slug from city name and state."""
    if not city_name:
        return None
    name = city_name.strip()
    # Remove common suffixes
    for suffix in [" City", " Township", " Borough", " Town", " Village"]:
        if name.endswith(suffix):
            name = name[:-len(suffix)]
    slug = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')
    return f"{slug}-{state.lower()}" if slug else None


def normalize_city_key(city_name, state):
    """V12.36: Create normalized key for deduplication (case-insensitive, trimmed)."""
    if not city_name or not state:
        return None
    # Normalize: lowercase, strip whitespace, collapse multiple spaces
    name = ' '.join(city_name.lower().split())
    return (name, state.upper())


def discover_cities_from_permits():
    """
    V12.32: Scan permit data to discover all cities including bulk-sourced ones.
    Returns dict of {slug: city_config} for all discovered cities.
    Caches results for 5 minutes to avoid repeated scans.
    V12.36: Fixed deduplication - merges cities by normalized (name, state).
    """
    global _discovered_cities_cache, _discovered_cities_timestamp

    # Check cache validity (5 minute TTL)
    cache_age = time.time() - _discovered_cities_timestamp
    if _discovered_cities_cache and cache_age < 300:
        return _discovered_cities_cache

    print("[V12.36] Discovering cities from permit data (with dedup)...")

    # V12.36: Track by normalized (name, state) key to prevent duplicates
    all_cities = {}
    seen_normalized = {}  # Maps normalized_key -> slug for dedup

    # Start with explicit configs from CITY_REGISTRY
    for key, config in CITY_REGISTRY.items():
        if config.get('active', False):
            slug = config.get('slug', key)
            name = config.get('name', key)
            state = config.get('state', '')

            # Track by normalized key for dedup
            norm_key = normalize_city_key(name, state)
            if norm_key and norm_key not in seen_normalized:
                seen_normalized[norm_key] = slug
                all_cities[slug] = {
                    'key': key,
                    'name': name,
                    'state': state,
                    'slug': slug,
                    'configured': True,
                    'active': True,
                }

    # Scan permits for additional cities
    permits_path = os.path.join(DATA_DIR, 'permits.json')
    if os.path.exists(permits_path):
        try:
            with open(permits_path) as f:
                permits = json.load(f, strict=False)

            # Find unique (city, state) pairs from permits
            permit_cities = set()
            for permit in permits:
                city_name = permit.get('city', '').strip()
                state = permit.get('state', '').strip()
                if city_name and state:
                    permit_cities.add((city_name, state))

            # Add cities not already tracked (by normalized key)
            added_count = 0
            for city_name, state in permit_cities:
                norm_key = normalize_city_key(city_name, state)
                if norm_key and norm_key not in seen_normalized:
                    slug = slugify_for_lookup(city_name, state)
                    if slug:
                        seen_normalized[norm_key] = slug
                        all_cities[slug] = {
                            'key': slug,
                            'name': city_name,
                            'state': state,
                            'slug': slug,
                            'configured': False,  # Auto-discovered from bulk permit data
                            'active': False,  # V31: Not actively pulled — just has historical permit data
                            'source_bulk': True,
                        }
                        added_count += 1

            print(f"[V12.36] Found {len(all_cities)} unique cities "
                  f"({len(permit_cities)} in permits, {added_count} new)")

        except Exception as e:
            print(f"[V12.36] Error scanning permits: {e}")

    _discovered_cities_cache = all_cities
    _discovered_cities_timestamp = time.time()
    return all_cities


def get_city_by_slug_auto(slug):
    """
    V12.32: Look up city config by slug, checking CITY_REGISTRY,
    auto-discovered cities from bulk source data, and prod_cities.
    V32: Added prod_cities fallback for bulk source cities whose slugs
    include state suffixes (e.g., 'lakewood-nj' for URL 'lakewood').
    Returns (city_key, city_config) or (None, None) if not found.
    """
    # First try explicit registry (faster, has full config)
    city_key, city_config = get_city_by_slug(slug)
    if city_config:
        return city_key, city_config

    # Try auto-discovered cities
    discovered = discover_cities_from_permits()
    if slug in discovered:
        city_info = discovered[slug]
        # Build a minimal config compatible with existing code
        return city_info['key'], {
            'name': city_info['name'],
            'state': city_info['state'],
            'slug': slug,
            'active': True,
            'auto_discovered': True,
        }

    # V32: Check prod_cities table (handles bulk source slugs like 'lakewood-nj')
    try:
        city_name, state, prod_slug = permitdb.lookup_prod_city_by_slug(slug)
        if city_name:
            return prod_slug, {
                'name': city_name,
                'state': state,
                'slug': prod_slug,
                'active': True,
                'auto_discovered': True,
                'from_prod_cities': True,
            }
    except Exception as e:
        print(f"[V32] Error looking up prod_city for slug '{slug}': {e}")

    return None, None


def get_cities_by_state_auto(state_abbrev):
    """
    V12.32: Get all cities for a state, including auto-discovered ones.
    Returns list of city info dicts.
    """
    state_abbrev = state_abbrev.upper()
    discovered = discover_cities_from_permits()

    cities = []
    for slug, info in discovered.items():
        if info.get('state', '').upper() == state_abbrev:
            cities.append(info)

    return sorted(cities, key=lambda x: x.get('name', ''))


def get_total_city_count_auto():
    """V15/V31: Get total count of actively collected cities.

    V31: Only counts cities with live data collection (prod_cities status='active').
    Does NOT include historical bulk-source cities that aren't being actively pulled.
    Falls back to get_cities_with_data() heuristics if prod_cities is empty.
    """
    try:
        # V15: Try prod_cities first (collector redesign)
        if permitdb.prod_cities_table_exists():
            count = permitdb.get_prod_city_count()
            if count > 0:
                return count

        # Fall back to heuristics (pre-V15 behavior)
        filtered_cities = get_cities_with_data()
        return len(filtered_cities)
    except Exception as e:
        print(f"[V15] Error getting city count: {e}")
        return 160  # Fallback


# V12.53: DEPRECATED - subscribers now stored in User model with digest_cities field
# These constants and functions are kept for backward compatibility but not used
SUBSCRIBERS_FILE = os.path.join(DATA_DIR, 'subscribers.json')  # DEPRECATED
USERS_FILE = os.path.join(DATA_DIR, 'users.json')  # DEPRECATED - use PostgreSQL User model

# V12.12: Startup data loading state
# Track whether initial data has been loaded from disk
_initial_data_loaded = False
_collection_in_progress = False
# V16: Track last successful collection run for health monitoring
_last_collection_run = None

# V12.51: Removed V12.49 cache code (_permits_cache, _permits_cache_mtime, _permits_cache_lock)
# SQLite handles all permit storage now - no JSON file caching needed

def preload_data_from_disk():
    """V12.51: Initialize SQLite database on startup.

    V12.50 migrated from JSON files to SQLite. This function now just
    initializes the database and reports the current permit count.
    """
    global _initial_data_loaded

    permitdb.init_db()

    # V13.2: Clean up invalid date fields (e.g., Mesa permits with reviewer names)
    permitdb.cleanup_invalid_dates()

    stats = permitdb.get_permit_stats()
    print(f"[Server] V12.51: SQLite ready - {stats['total_permits']} permits, {stats['city_count']} cities")
    _initial_data_loaded = True

def is_data_loading():
    """V12.51: Check if we're in a loading state (no data available)."""
    if _initial_data_loaded:
        return False
    # Check SQLite for data
    try:
        stats = permitdb.get_permit_stats()
        return stats['total_permits'] == 0
    except Exception:
        return True


def sync_city_registry_to_prod_cities():
    """V97: DISABLED in V145 — this function wiped city_sources in V143.
    prod_cities (2,395 active) is the source of truth. Do not sync from CITY_REGISTRY.
    """
    print("[V145] sync_city_registry_to_prod_cities DISABLED — prevented data wipe")
    return

    # ORIGINAL CODE BELOW (dead code, kept for reference):
    """V97: Complete sync — CITY_REGISTRY + BULK_SOURCES → prod_cities + city_sources.

    V97 FIXES (replaces broken V95/V96 logic):
    - Builds THREE lookups upfront: by_source, by_slug, by_citystate
    - Uses config.get('slug', city_key) for slug — NOT normalize_city_slug()
    - Direct SQL INSERT instead of upsert_prod_city() which was failing silently
    - Tracks already_active to avoid unnecessary updates
    - NO DELETES: Never delete prod_cities rows.

    Runs on every startup. Must be idempotent and fast (< 30 seconds).
    """
    from city_configs import CITY_REGISTRY, BULK_SOURCES
    from city_source_db import upsert_city_source

    result = {
        'already_active': 0,
        'prod_activated': 0,
        'prod_created': 0,
        'prod_updated': 0,
        'cs_created': 0,
        'errors': 0
    }
    conn = None

    try:
        print(f"[V97] Starting registry sync...")
        conn = permitdb.get_connection()

        # =================================================================
        # STEP 1: CITY_REGISTRY → city_sources
        # =================================================================
        print(f"[V97] Phase 1: Syncing CITY_REGISTRY → city_sources...")
        for city_key, config in CITY_REGISTRY.items():
            if not config.get('active', False):
                continue
            try:
                upsert_city_source({
                    'source_key': city_key,
                    'name': config.get('name', city_key),
                    'state': config.get('state', ''),
                    'platform': config.get('platform', ''),
                    'mode': 'city',
                    'endpoint': config.get('endpoint', ''),
                    'dataset_id': config.get('dataset_id', ''),
                    'field_map': config.get('field_map', {}),
                    'date_field': config.get('date_field', ''),
                    'city_field': config.get('city_field', ''),
                    'limit_per_page': config.get('limit', 2000),
                    'status': 'active'
                })
                result['cs_created'] += 1
            except Exception as e:
                print(f"  [V97] WARN: city_sources upsert failed for {city_key}: {e}")
                result['errors'] += 1

        # =================================================================
        # STEP 2: BULK_SOURCES → city_sources
        # =================================================================
        print(f"[V97] Phase 2: Syncing BULK_SOURCES → city_sources...")
        for source_key, config in BULK_SOURCES.items():
            if not config.get('active', True):
                continue
            try:
                upsert_city_source({
                    'source_key': source_key,
                    'name': config.get('name', source_key),
                    'state': config.get('state', ''),
                    'platform': config.get('platform', ''),
                    'mode': 'bulk',
                    'endpoint': config.get('endpoint', ''),
                    'dataset_id': config.get('dataset_id', ''),
                    'field_map': config.get('field_map', {}),
                    'date_field': config.get('date_field', ''),
                    'city_field': config.get('city_field', ''),
                    'limit_per_page': config.get('limit', 50000),
                    'status': 'active'
                })
                result['cs_created'] += 1
            except Exception as e:
                print(f"  [V97] WARN: city_sources upsert failed for bulk {source_key}: {e}")
                result['errors'] += 1

        # =================================================================
        # STEP 3: CITY_REGISTRY → prod_cities (V98 FIX)
        # =================================================================
        print(f"[V97] Phase 3: Syncing CITY_REGISTRY → prod_cities...")

        # V98: Re-acquire connection — upsert_city_source() in Phase 1/2
        # closes the thread-local conn (V66 conn.close()), so the original
        # conn from line 6102 is dead by now.
        conn = permitdb.get_connection()

        # V97: Build THREE lookups upfront for fast matching
        by_source = {}
        by_slug = {}
        by_citystate = {}
        for row in conn.execute("SELECT id, city_slug, source_id, city, state, status FROM prod_cities"):
            row_dict = dict(row) if hasattr(row, 'keys') else {
                'id': row[0], 'city_slug': row[1], 'source_id': row[2],
                'city': row[3], 'state': row[4], 'status': row[5]
            }
            if row_dict['source_id']:
                by_source[row_dict['source_id']] = row_dict
            if row_dict['city_slug']:
                by_slug[row_dict['city_slug']] = row_dict
            city_lower = row_dict['city'].lower() if row_dict['city'] else ''
            state_val = row_dict['state'] or ''
            by_citystate[(city_lower, state_val)] = row_dict

        # Process each active CITY_REGISTRY entry
        for city_key, config in CITY_REGISTRY.items():
            if not config.get('active', False):
                continue

            name = config.get('name', '')
            state = config.get('state', '')
            platform = config.get('platform', '')
            # V97: Use slug from config, fallback to city_key — NOT normalize_city_slug()
            slug = config.get('slug', city_key)

            if not name or not state:
                continue

            # Match 1: by source_id (most reliable)
            if city_key in by_source:
                row = by_source[city_key]
                if row['status'] == 'active':
                    result['already_active'] += 1
                else:
                    conn.execute(
                        "UPDATE prod_cities SET status = ?, source_type = ? WHERE id = ?",
                        ('active', platform, row['id'])
                    )
                    result['prod_activated'] += 1
                continue

            # Match 2: by slug (V102: also verify state matches to prevent cross-state mislinks)
            if slug in by_slug:
                row = by_slug[slug]
                row_state = row.get('state', '')
                if row_state and state and row_state != state:
                    pass  # V102: State mismatch — don't link (e.g., long_beach_nj vs Long Beach CA)
                else:
                    conn.execute(
                        "UPDATE prod_cities SET status = ?, source_id = ?, source_type = ? WHERE id = ?",
                        ('active', city_key, platform, row['id'])
                    )
                    result['prod_activated'] += 1
                    by_source[city_key] = row  # prevent double-match
                    continue

            # Match 3: by city+state
            cs_key = (name.lower(), state)
            if cs_key in by_citystate:
                row = by_citystate[cs_key]
                # V102: Don't overwrite source_id if already set to a different active entry
                existing_source = row.get('source_id', '')
                if existing_source and existing_source != city_key and existing_source in by_source:
                    # Already linked to another source — skip to avoid overwrite
                    result['already_active'] += 1
                    continue
                conn.execute(
                    "UPDATE prod_cities SET status = ?, source_id = ?, source_type = ?, city_slug = ? WHERE id = ?",
                    ('active', city_key, platform, slug, row['id'])
                )
                result['prod_activated'] += 1
                by_source[city_key] = row
                continue

            # Match 4: INSERT new city (V97: direct SQL, not upsert_prod_city)
            try:
                conn.execute("""
                    INSERT INTO prod_cities (city, state, city_slug, source_id, source_type, status, added_by)
                    VALUES (?, ?, ?, ?, ?, 'active', 'v97_sync')
                """, (name, state, slug, city_key, platform))
                result['prod_created'] += 1
                by_source[city_key] = {'id': -1}  # prevent double-match
                by_citystate[cs_key] = {'id': -1}
            except Exception as e:
                print(f"  [V97] ERROR: Failed to insert {city_key} ({name}, {state}): {e}")
                result['errors'] += 1

        # =================================================================
        # STEP 4: Deactivate inactive CITY_REGISTRY entries (no deletes)
        # V102: Also check by slug and city+state, not just by_source
        # =================================================================
        for city_key, config in CITY_REGISTRY.items():
            if config.get('active', False):
                continue
            # V102: Find the matching prod_cities row by source_id, slug, or city+state
            row = None
            if city_key in by_source:
                row = by_source[city_key]
            else:
                slug = config.get('slug', city_key)
                if slug in by_slug:
                    row = by_slug[slug]
                else:
                    name = config.get('name', '')
                    state = config.get('state', '')
                    if name and state:
                        cs_key = (name.lower(), state)
                        if cs_key in by_citystate:
                            row = by_citystate[cs_key]
            if row and row.get('status') == 'active':
                # V103: Don't pause if the row's source_id points to a DIFFERENT active entry
                row_source = row.get('source_id', '')
                if row_source and row_source != city_key and row_source in by_source:
                    # This row is linked to another (possibly active) source — don't pause
                    continue
                conn.execute(
                    "UPDATE prod_cities SET status = 'paused' WHERE id = ?",
                    (row['id'],)
                )
                result['prod_updated'] += 1

        conn.commit()

        # V97: Log actual count for verification
        actual_active = conn.execute(
            "SELECT COUNT(*) FROM prod_cities WHERE status = 'active'"
        ).fetchone()[0]

        print(f"[V97] Sync complete: "
              f"already_active={result['already_active']}, activated={result['prod_activated']}, "
              f"created={result['prod_created']}, paused={result['prod_updated']}, errors={result['errors']} | "
              f"city_sources={result['cs_created']} | "
              f"ACTUAL ACTIVE: {actual_active}")

        return result

    except Exception as e:
        print(f"[V95] Sync error: {e}")
        import traceback
        traceback.print_exc()
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        return result
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


# ===========================
# LEAD SCORING ENGINE
# ===========================
# V7: FORCED LINEAR SPREAD - exact implementation from spec
# Output range: 40-99, guaranteed by linear mapping.

import hashlib


def calculate_lead_score(permit):
    """
    V13.1: ABSOLUTE lead scoring with WIDER SPREAD for better differentiation.
    Returns integer 0-100. No normalization across dataset.

    Score breakdown (max 100 points):
      A: Project value     0-35 pts (absolute brackets)
      B: Recency          0-30 pts (days since filed)
      C: Address quality  0-15 pts (has street number)
      D: Contact info     0-15 pts (phone/email/names)
      E: Status           0-5 pts  (issued > pending > other)
    """
    score = 0.0

    # A: Project value (0-35 pts) — ABSOLUTE brackets with wider spread
    value = 0.0
    for key in ['estimated_cost', 'project_value', 'value']:
        v = permit.get(key)
        if v is not None:
            try:
                value = float(str(v).replace('$', '').replace(',', ''))
                break
            except (ValueError, TypeError):
                pass

    if value <= 0:
        score += 0    # V13.1: Missing = 0 (creates bigger gap)
    elif value < 10000:
        score += 5
    elif value < 50000:
        score += 10
    elif value < 100000:
        score += 16
    elif value < 200000:
        score += 22
    elif value < 500000:
        score += 28
    else:
        score += 35   # $500K+ = max

    # B: Recency (0-30 pts) — V13.1: Invalid dates = 0, not default
    recency_added = False
    for key in ['filing_date', 'issued_date', 'date']:
        d = permit.get(key)
        if d:
            try:
                if isinstance(d, str):
                    # V13.1: Must start with digit to be a valid date
                    if not d[0].isdigit():
                        continue  # Skip non-date strings like "WROCCO"
                    d = datetime.strptime(d[:10], '%Y-%m-%d')
                days_old = (datetime.now() - d).days
                if days_old < 0:
                    score += 0    # Future date = bad data
                elif days_old <= 7:
                    score += 30
                elif days_old <= 30:
                    score += 24
                elif days_old <= 90:
                    score += 18
                elif days_old <= 180:
                    score += 12
                elif days_old <= 365:
                    score += 6
                else:
                    score += 0    # V13.1: Older than 1 year = 0
                recency_added = True
                break
            except (ValueError, TypeError):
                pass
    # V13.1: No valid date = 0 (not 8), creates bigger differentiation
    # recency_added stays False, score += 0 implied

    # C: Address quality (0-15 pts) — V13.1: Increased weight
    # V19: Explicitly exclude placeholder addresses from scoring
    address = str(permit.get('address', '')).strip()
    address_lower = address.lower()
    is_placeholder = (
        not address or
        address_lower in ('address not provided', 'not provided', 'n/a', 'na', 'none', 'unknown', 'tbd', '-') or
        address_lower.startswith('address not')
    )
    if is_placeholder:
        score += 0    # V19: No address = 0 points (keeps out of Best Leads)
    elif any(c.isdigit() for c in address):
        score += 15   # Has street number = full points
    elif len(address) > 5:
        score += 7    # Has name but no number
    # else 0

    # D: Contact info (0-15 pts) — V13.1: More granular
    has_phone = bool(permit.get('contact_phone'))
    has_email = bool(permit.get('contact_email'))
    has_contractor = bool(permit.get('contractor_name'))
    has_owner = bool(permit.get('owner_name'))

    if has_phone and has_email:
        score += 15
    elif has_phone or has_email:
        score += 12
    elif has_contractor:
        score += 8
    elif has_owner:
        score += 5
    # else 0

    # E: Status (0-5 pts) — V13.1: Reduced weight, least important
    status = str(permit.get('status', '')).lower().strip()
    if status in ('issued', 'approved', 'active', 'permitted', 'finaled'):
        score += 5
    elif status in ('pending', 'in review', 'under review', 'plan review', 'filed', 'submitted'):
        score += 3
    # else 0

    return max(0, min(100, round(score)))


def add_lead_scores(permits):
    """
    V13.1: Apply absolute lead scoring with wider spread.
    Also assigns lead_quality tier based on score.
    """
    if not permits:
        return permits

    for p in permits:
        score = calculate_lead_score(p)
        p['lead_score'] = score

        # V13.1: Adjusted thresholds for wider score distribution
        if score >= 60:
            p['lead_quality'] = 'hot'
        elif score >= 40:
            p['lead_quality'] = 'warm'
        else:
            p['lead_quality'] = 'standard'

    return permits


# ===========================
# TRADE CLASSIFICATION
# ===========================
# V200-1: Removed duplicate classify_trade definition here (was drifting from
# the canonical one in collector.py against the same TRADE_CATEGORIES dict).
# reclassify_permit now imports the canonical version.


def reclassify_permit(permit):
    """Re-classify a permit's trade category based on its description and type fields."""
    from collector import classify_trade
    text_parts = [
        permit.get('description', ''),
        permit.get('work_type', ''),
        permit.get('permit_type', '')
    ]
    text = ' '.join(filter(None, text_parts))
    permit['trade_category'] = classify_trade(text)
    return permit


def generate_permit_description(permit):
    """
    Generate a unique, factual description based on actual permit data.
    Falls back to building a description from permit fields if no real description exists,
    or if the description appears templated (same as others).
    """
    existing_desc = permit.get('description', '')

    # Build a factual description from permit data
    parts = []

    # Permit type
    permit_type = permit.get('permit_type', '') or permit.get('work_type', '')
    if permit_type:
        parts.append(permit_type.strip())

    # Trade category
    trade = permit.get('trade_category', '')
    if trade and trade not in ['General Construction', 'Other']:
        if not any(trade.lower() in p.lower() for p in parts):
            parts.append(f"({trade})")

    # Address — V12.57: Clean raw JSON/GeoJSON before displaying
    address = permit.get('address', '')
    if address and ('{' in str(address)):
        # Address contains JSON — try to parse it
        from collector import parse_address_value
        address = parse_address_value(address)
    if address:
        parts.append(f"at {address}")

    # Value - V12.27: Skip if at $50M cap (unreliable data)
    cost = permit.get('estimated_cost', 0) or 0
    MAX_REASONABLE_COST = 50_000_000
    if cost > 0 and cost != MAX_REASONABLE_COST:
        if cost >= 1000000:
            parts.append(f"— ${cost/1000000:.1f}M project")
        elif cost >= 1000:
            parts.append(f"— ${cost/1000:.0f}K project")
        else:
            parts.append(f"— ${cost:,.0f}")

    # Status
    status = permit.get('status', '')
    if status:
        parts.append(f"[{status}]")

    # Permit number for uniqueness
    permit_num = permit.get('permit_number', '')
    if permit_num:
        parts.append(f"(Permit #{permit_num})")

    # Combine parts
    generated_desc = ' '.join(parts)

    # Return existing description if it's substantial and unique-looking
    # (has actual address or permit number in it), otherwise use generated
    if existing_desc and len(existing_desc) > 30:
        # Check if existing description contains unique identifiers
        has_address = address and address[:10] in existing_desc
        has_permit_num = permit_num and permit_num in existing_desc
        if has_address or has_permit_num:
            return existing_desc

    return generated_desc if generated_desc else existing_desc


# ===========================
# DATA LOADING
# ===========================
def format_permit_address(permit):
    """V12.11: Format address field appropriately.

    For county datasets, addresses may be location/area names (no street number).
    Detect these and label them as "Location:" instead of pretending they're street addresses.
    """
    address = permit.get('address', '') or ''
    if not address.strip():
        permit['display_address'] = 'Address not provided'
        permit['address_type'] = 'none'
        return

    address_clean = address.strip()

    # Check if it looks like a real street address (has a number at the start)
    import re
    has_street_number = bool(re.match(r'^\d+\s', address_clean))

    # Common area/location-only patterns (no street number, short, all caps)
    is_location_only = (
        not has_street_number and
        len(address_clean) < 30 and
        (address_clean.isupper() or
         address_clean.upper() in ['MONTGOMERY', 'ROCK SPRING', 'BETHESDA', 'SILVER SPRING',
                                   'ROCKVILLE', 'WHEATON', 'GERMANTOWN', 'GAITHERSBURG',
                                   'POTOMAC', 'CHEVY CHASE', 'TAKOMA PARK', 'KENSINGTON'])
    )

    if is_location_only:
        permit['display_address'] = f"Area: {address_clean.title()}"
        permit['address_type'] = 'location'
    elif not has_street_number and len(address_clean.split()) <= 3:
        # Short address without number - likely a location name
        permit['display_address'] = f"Location: {address_clean.title()}"
        permit['address_type'] = 'location'
    else:
        permit['display_address'] = address_clean
        permit['address_type'] = 'street'


def validate_permit_dates(permit):
    """V12.9: Validate and relabel future-dated permits.

    If filing_date is >30 days in the future, it's likely an expiration date,
    not a filing date. Relabel it appropriately.
    """
    filing_date_str = permit.get('filing_date', '')
    if not filing_date_str:
        return

    try:
        filing_date = datetime.strptime(str(filing_date_str)[:10], '%Y-%m-%d')
        days_from_now = (filing_date - datetime.now()).days

        if days_from_now > 30:
            # This is likely an expiration/completion date, not a filing date
            permit['expiration_date'] = filing_date_str
            permit['date_label'] = 'Expires'
            # Try to find an alternative filing date
            for alt_key in ['issued_date', 'issue_date', 'created_date', 'application_date']:
                alt_date = permit.get(alt_key)
                if alt_date:
                    try:
                        alt_parsed = datetime.strptime(str(alt_date)[:10], '%Y-%m-%d')
                        if (alt_parsed - datetime.now()).days <= 30:
                            permit['filing_date'] = str(alt_date)[:10]
                            permit['date_label'] = 'Filed'
                            return
                    except:
                        pass
            # No alternative found, keep expiration date but mark it
            permit['filing_date'] = filing_date_str
            permit['date_label'] = 'Expires'
        else:
            permit['date_label'] = 'Filed'
    except (ValueError, TypeError):
        permit['date_label'] = 'Filed'  # Default label


# V12.51: Removed _load_permits_from_disk() and load_permits()
# All permit data now comes from SQLite via permitdb.query_permits()
# This eliminates the JSON file parsing that caused OOM crashes

def load_stats():
    """Load collection stats. V12.51: Falls back to SQLite if JSON not found."""
    path = os.path.join(DATA_DIR, 'collection_stats.json')
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f, strict=False)
        except Exception as e:
            print(f"[Server] ERROR: Failed to parse collection_stats.json: {e}")
            # Fall through to SQLite fallback

    # V12.51: SQLite fallback
    try:
        return permitdb.get_collection_stats()
    except Exception:
        return {}

def get_cities_with_data():
    """V15/V34: Get cities with VERIFIED data, sorted by permit volume.

    V34: Now filters out cities with 0 actual permits in the DB.
    Only returns cities that genuinely have permit data, regardless of
    what prod_cities.total_permits says (that column can be stale).

    V15: Uses prod_cities table if available (collector redesign).
    Falls back to heuristics-based filtering if prod_cities is empty.
    """
    # V15/V34: Try prod_cities first (collector redesign)
    # V34: total_permits is synced with actual DB counts on startup,
    # so we can trust it for filtering. No expensive JOIN needed per-request.
    try:
        if permitdb.prod_cities_table_exists():
            # min_permits=1 filters out cities with 0 real permits
            prod_cities = permitdb.get_prod_cities(status='active', min_permits=1)
            if prod_cities:
                return prod_cities
    except Exception as e:
        print(f"[V15] Error getting prod_cities: {e}")

    # Fall back to heuristics (pre-V15 behavior)
    # V13.2: Valid US state/territory codes - filter out Canadian provinces etc.
    VALID_US_STATES = {
        'AL', 'AK', 'AZ', 'AR', 'CA', 'CO', 'CT', 'DE', 'DC', 'FL', 'GA',
        'HI', 'ID', 'IL', 'IN', 'IA', 'KS', 'KY', 'LA', 'ME', 'MD', 'MA',
        'MI', 'MN', 'MS', 'MO', 'MT', 'NE', 'NV', 'NH', 'NJ', 'NM', 'NY',
        'NC', 'ND', 'OH', 'OK', 'OR', 'PA', 'RI', 'SC', 'SD', 'TN', 'TX',
        'UT', 'VT', 'VA', 'WA', 'WV', 'WI', 'WY',
        'AS', 'GU', 'MP', 'PR', 'VI'  # territories
    }

    # V13.2: US state names to filter out as city entries
    US_STATE_NAMES = {
        'alabama', 'alaska', 'arizona', 'arkansas', 'california', 'colorado',
        'connecticut', 'delaware', 'florida', 'georgia', 'hawaii', 'idaho',
        'illinois', 'indiana', 'iowa', 'kansas', 'kentucky', 'louisiana',
        'maine', 'maryland', 'massachusetts', 'michigan', 'minnesota',
        'mississippi', 'missouri', 'montana', 'nebraska', 'nevada',
        'new hampshire', 'new jersey', 'new mexico', 'new york',
        'north carolina', 'north dakota', 'ohio', 'oklahoma', 'oregon',
        'pennsylvania', 'rhode island', 'south carolina', 'south dakota',
        'tennessee', 'texas', 'utah', 'vermont', 'virginia', 'washington',
        'west virginia', 'wisconsin', 'wyoming', 'district of columbia'
    }

    # V13.3: Garbage patterns - dataset names, permit types, and other junk
    # V13.9: Added 'building:' for "Building: Addition/Alteration" entries
    GARBAGE_PATTERNS = [
        'dob now', 'build –', 'build-', 'applications', 'certificate',
        'permits table', 'data_wfl', 'epic-la', 'bureau of', '_wgs84',
        'inspections', 'case history', 'building and safety',
        'development permits', 'sewer data', 'engineering permit',
        'permit information', 'county permit', 'limited alteration',
        'building:'
    ]

    # Get city counts from SQLite - this has ALL cities with permits
    city_rows = permitdb.get_cities_with_permits()

    # Get static registry for cities that have extra config
    all_cities = get_all_cities_info()  # Active only - for display
    city_lookup = {c['name']: c for c in all_cities}
    city_lookup_lower = {c['name'].lower(): c for c in all_cities}

    # V13.4: Build registry lookup from ALL configs (including inactive)
    # This fixes Houston OK -> TX (Houston's config is inactive but has state="TX")
    registry_state_by_city = {}
    for key, cfg in CITY_REGISTRY.items():
        city_lower = cfg.get('name', '').lower()
        state = cfg.get('state', '').upper()
        if city_lower and state:
            # If city appears multiple times, prefer active config's state
            if city_lower not in registry_state_by_city or cfg.get('active'):
                registry_state_by_city[city_lower] = state

    # V13.3: Build registry lookup by (city_lower, state_upper) for state priority
    registry_by_city_state = {}
    for c in all_cities:
        key = (c['name'].lower(), c.get('state', '').upper())
        registry_by_city_state[key] = c

    # Known city name corrections (partial names -> full names)
    CITY_NAME_FIXES = {
        'orleans': 'New Orleans',
        'york': 'New York',
    }

    # PASS 1: Group by normalized key (lowercase city + state) to deduplicate
    city_groups = {}
    for row in city_rows:
        name = row['city']
        state = row.get('state', '')
        permit_count = row.get('permit_count', 0)

        if not name or not name.strip():
            continue

        # Normalize the city name first (needed for registry lookup)
        name_lower = name.lower().strip()

        # V13.4: Use registry to correct state (fixes Houston OK -> TX)
        registry_state = registry_state_by_city.get(name_lower)
        if registry_state:
            state = registry_state

        # V13.5: Fix state corruption - reassign misassigned cities
        # The DB has cities incorrectly tagged from past bulk runs
        state_upper = state.upper() if state else ''

        KNOWN_OK_CITIES = {
            'oklahoma city', 'tulsa', 'norman', 'broken arrow', 'edmond',
            'lawton', 'moore', 'midwest city', 'enid', 'stillwater',
            'muskogee', 'bartlesville', 'owasso', 'shawnee', 'ponca city',
            'ardmore', 'duncan', 'del city', 'bixby', 'sapulpa', 'altus',
            'bethany', 'sand springs', 'yukon', 'mustang', 'claremore'
        }
        if state_upper == 'OK' and name_lower not in KNOWN_OK_CITIES:
            state = 'TX'

        # V13.6: Fix NV state corruption - ~98 Texas towns tagged as NV
        KNOWN_NV_CITIES = {
            'las vegas', 'henderson', 'reno', 'north las vegas', 'sparks',
            'carson city', 'elko', 'mesquite', 'boulder city', 'fernley',
            'fallon', 'winnemucca', 'west wendover', 'ely', 'yerington'
        }
        if state_upper == 'NV' and name_lower not in KNOWN_NV_CITIES:
            state = 'TX'

        # V13.6: Fix IN state corruption - ~25 Florida cities tagged as IN
        KNOWN_IN_CITIES = {
            'indianapolis', 'fort wayne', 'evansville', 'south bend', 'carmel',
            'fishers', 'bloomington', 'hammond', 'gary', 'lafayette', 'muncie',
            'terre haute', 'kokomo', 'anderson', 'noblesville', 'greenwood',
            'elkhart', 'mishawaka', 'lawrence', 'jeffersonville', 'columbus'
        }
        if state_upper == 'IN' and name_lower not in KNOWN_IN_CITIES:
            state = 'FL'

        # V13.6: Fix LA state corruption - ~70 LA (Los Angeles) cities tagged as LA (Louisiana)
        KNOWN_LA_CITIES = {
            'new orleans', 'baton rouge', 'shreveport', 'lafayette', 'lake charles',
            'kenner', 'bossier city', 'monroe', 'alexandria', 'houma', 'slidell',
            'metairie', 'new iberia', 'laplace', 'central', 'ruston', 'sulphur',
            'hammond', 'natchitoches', 'gretna', 'opelousas', 'zachary', 'thibodaux'
        }
        if state_upper == 'LA' and name_lower not in KNOWN_LA_CITIES:
            state = 'CA'

        # V13.4: Require valid US state (eliminates "Other Locations" garbage)
        if not state or state.upper() not in VALID_US_STATES:
            continue

        # V13.2: Filter out state names appearing as city names
        if name_lower in US_STATE_NAMES:
            continue

        # V13.3: Filter garbage city names (dataset names, permit types, etc.)
        if any(p in name_lower for p in GARBAGE_PATTERNS):
            continue

        # V13.6: Filter county names and abbreviations
        # V13.8: Added 'general', 'electrical', 'roof' per UAT Round 7 (trade names)
        if 'county' in name_lower or name_lower in ('uninc', 'unincorporated', 'general', 'electrical', 'roof'):
            continue

        # V13.6: Skip very short names (likely abbreviations or garbage)
        if len(name) < 3:
            continue

        # V13.3: Skip names that are too long (real city names are rarely >35 chars)
        if len(name) > 35:
            continue

        # Apply known fixes for partial names
        if name_lower in CITY_NAME_FIXES:
            name = CITY_NAME_FIXES[name_lower]
            name_lower = name.lower()

        # Create dedup key (city + state)
        key = (name_lower, state.upper() if state else '')

        if key not in city_groups:
            city_groups[key] = {
                'names': [],
                'state': state,
                'permit_count': 0
            }

        city_groups[key]['names'].append(name)
        city_groups[key]['permit_count'] += permit_count

    # PASS 2: Cross-state dedup - merge same city name across different states
    # Group by city name only, then pick the state with highest permit count
    name_only_groups = {}
    for (name_lower, state_code), group in city_groups.items():
        if name_lower not in name_only_groups:
            name_only_groups[name_lower] = []
        name_only_groups[name_lower].append({
            'state_code': state_code,
            'state': group['state'],
            'names': group['names'],
            'permit_count': group['permit_count']
        })

    # Build final city list - for each city name, pick best state
    cities_with_counts = []
    for name_lower, state_entries in name_only_groups.items():
        # Sum ALL permit counts across all states for this city
        total_count = sum(e['permit_count'] for e in state_entries)

        # V13.3: Prioritize registry state over permit count
        # First check if any (city, state) combo is in the registry
        registry_entry = None
        registry_state_entry = None
        for entry in state_entries:
            key = (name_lower, entry['state_code'])
            if key in registry_by_city_state:
                registry_entry = registry_by_city_state[key]
                registry_state_entry = entry
                break

        # If registry match found, use that; otherwise use highest permit count
        if registry_entry:
            city_info = registry_entry.copy()
            city_info['permit_count'] = total_count
            cities_with_counts.append(city_info)
            continue

        # Also check registry by name only (case-insensitive)
        if name_lower in city_lookup_lower:
            registry_city = city_lookup_lower[name_lower]
            city_info = registry_city.copy()
            city_info['permit_count'] = total_count
            cities_with_counts.append(city_info)
            continue

        # Not in registry - pick state with highest permit count
        best_entry = max(state_entries, key=lambda x: x['permit_count'])

        # Pick best display name from variants
        best_name = None
        for n in best_entry['names']:
            if n == n.title():
                best_name = n
                break
        if not best_name:
            best_name = best_entry['names'][0].title()

        state = best_entry['state']
        slug = best_name.lower().replace(' ', '-').replace(',', '').replace('.', '')

        city_info = {
            'name': best_name,
            'state': state,
            'slug': slug,
            'permit_count': total_count,
            'active': True
        }
        cities_with_counts.append(city_info)

    # V13.7: Filter out cities with very few permits (reduces TX from 1,170 to ~100)
    # Cities with <10 permits aren't useful leads and inflate the city count
    MIN_PERMIT_THRESHOLD = 10
    cities_with_counts = [c for c in cities_with_counts if c.get('permit_count', 0) >= MIN_PERMIT_THRESHOLD]

    # Sort by permit count descending (top cities first)
    cities_with_counts.sort(key=lambda x: x.get('permit_count', 0), reverse=True)
    return cities_with_counts


def get_suggested_cities(searched_slug, limit=6):
    """V12.9: Get similar city suggestions for 404 page using fuzzy matching."""
    all_cities = get_all_cities_info()
    active_cities = [c for c in all_cities if c.get('active', True)]

    # Calculate similarity scores
    suggestions = []
    searched_lower = searched_slug.lower().replace('-', ' ')

    for city in active_cities:
        slug_lower = city['slug'].lower().replace('-', ' ')
        name_lower = city['name'].lower()

        # Check multiple matching criteria
        slug_score = SequenceMatcher(None, searched_lower, slug_lower).ratio()
        name_score = SequenceMatcher(None, searched_lower, name_lower).ratio()

        # Boost if searched term is contained in name
        contains_boost = 0.3 if searched_lower in name_lower or name_lower in searched_lower else 0

        best_score = max(slug_score, name_score) + contains_boost
        if best_score > 0.3:  # Only include if somewhat similar
            suggestions.append((city, best_score))

    # Sort by score, take top matches
    suggestions.sort(key=lambda x: -x[1])
    return [s[0] for s in suggestions[:limit]]


def get_popular_cities(limit=12):
    """V12.51: Get popular cities for 404 page (SQL-backed)."""
    conn = permitdb.get_connection()
    rows = conn.execute("""
        SELECT city, COUNT(*) as cnt FROM permits
        WHERE city IS NOT NULL AND city != ''
        GROUP BY city ORDER BY cnt DESC LIMIT ?
    """, (limit * 2,)).fetchall()  # Fetch extra in case some aren't in city_lookup

    all_cities = get_all_cities_info()
    city_lookup = {c['name']: c for c in all_cities}

    popular = []
    for row in rows:
        name = row['city']
        if name in city_lookup:
            city_info = city_lookup[name].copy()
            popular.append(city_info)
            if len(popular) >= limit:
                break

    return popular


def render_city_not_found(searched_slug):
    """V12.9: Render branded 404 page with city suggestions."""
    suggestions = get_suggested_cities(searched_slug)
    popular_cities = get_popular_cities()
    footer_cities = get_cities_with_data()

    return render_template(
        '404.html',
        searched_slug=searched_slug,
        suggestions=suggestions,
        popular_cities=popular_cities,
        footer_cities=footer_cities,
        show_city_suggestions=True,
    ), 404


# V12.53: DEPRECATED - Use User model with digest_cities and digest_active fields
def load_subscribers():
    """DEPRECATED: Load subscriber list from JSON file.
    V12.53: Use User.query.filter(User.digest_active == True) instead.
    """
    if os.path.exists(SUBSCRIBERS_FILE):
        with open(SUBSCRIBERS_FILE) as f:
            return json.load(f)
    return []


def save_subscribers(subs):
    """DEPRECATED: Save subscriber list to JSON file.
    V12.53: Use User model with db.session.commit() instead.
    """
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(SUBSCRIBERS_FILE, 'w') as f:
        json.dump(subs, f, indent=2)


# ===========================
# USER DATABASE FUNCTIONS (V7)
# ===========================
# All user operations now use PostgreSQL instead of JSON files


def find_user_by_email(email):
    """Find a user by email (case-insensitive). Returns User object or None."""
    if not email:
        return None
    email_lower = email.lower().strip()
    return User.query.filter(db.func.lower(User.email) == email_lower).first()


def get_current_user():
    """Get the currently logged-in user from session. Returns dict for backward compatibility."""
    user_email = session.get('user_email')
    if not user_email:
        return None
    user = find_user_by_email(user_email)
    if user:
        return user.to_dict()
    return None


def get_current_user_object():
    """Get the currently logged-in user as User object (for database operations)."""
    user_email = session.get('user_email')
    if not user_email:
        return None
    return find_user_by_email(user_email)


# ===========================
# BACKWARD COMPATIBILITY SHIMS (V7)
# ===========================
# These functions provide backward compatibility with code that expects
# the old dict-based user storage while actually using the database.

def load_users():
    """Load all users from database as list of dicts (backward compatibility)."""
    users = User.query.all()
    return [u.to_dict() for u in users]


def save_users(users):
    """Save users to database (backward compatibility - DEPRECATED).
    This is a no-op shim. Individual user updates should use db.session.commit().
    Kept for backward compatibility with code that still calls this.
    """
    # No-op: database operations should be done directly
    # Individual updates use db.session.commit()
    pass


def update_user_by_email(email, updates):
    """Update a user's fields by email (V7 helper)."""
    user = find_user_by_email(email)
    if user:
        for key, value in updates.items():
            if hasattr(user, key):
                setattr(user, key, value)
        db.session.commit()
        return True
    return False


def get_user_plan(user):
    """
    Returns 'pro', 'free', or 'anonymous'.
    Centralizes all plan checking logic to avoid inconsistencies.
    Recognizes Pro status from:
    - user.plan == 'pro'
    - user.plan == 'professional' (Stripe)
    - user.plan == 'enterprise'
    - user.stripe_subscription_status == 'active'
    """
    if not user:
        return 'anonymous'

    plan = (user.get('plan') or '').lower()

    # Check admin-set or Stripe-set plans
    if plan in ('pro', 'professional', 'enterprise'):
        return 'pro'

    # Check Stripe subscription status
    if user.get('stripe_subscription_status') == 'active':
        return 'pro'

    return 'free'


def is_pro(user):
    """Returns True if user has Pro access."""
    return get_user_plan(user) == 'pro'


# V69: COMPLETELY STATIC nav context — NO database access whatsoever
@app.context_processor
def inject_nav_context():
    """V69: Return static empty data. NO DB calls until server is stable."""
    return {
        'user': None,
        'user_plan': 'anonymous',
        'is_pro': False,
        'nav_cities': []
    }


# ===========================
# V29: SEO — www to non-www redirect + trailing slash normalization
# ===========================

@app.before_request
def seo_redirects():
    """V29: Redirect www.permitgrab.com → permitgrab.com (301) and normalize trailing slashes."""
    # www → non-www redirect
    if request.host.startswith('www.'):
        return redirect(request.url.replace('://www.', '://', 1), code=301)
    # Remove trailing slashes (except root)
    if request.path != '/' and request.path.endswith('/'):
        return redirect(request.url.replace(request.path, request.path.rstrip('/')), code=301)


# ===========================
# ANALYTICS HOOKS
# ===========================

@app.before_request
def analytics_before_request():
    """Capture UTM parameters and ensure session ID exists."""
    try:
        # Ensure analytics session ID
        if 'analytics_session_id' not in session:
            session['analytics_session_id'] = str(uuid.uuid4())

        # Capture UTM parameters
        utm_params = {}
        for key in ['utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content']:
            val = request.args.get(key)
            if val:
                utm_params[key] = val
        if utm_params:
            session['utm_params'] = utm_params
    except Exception:
        pass  # Never break the request


@app.after_request
def analytics_track_page_view(response):
    """Track page views for all successful page loads."""
    try:
        if response.status_code < 400 and request.endpoint:
            # Don't track static files, API calls, or health endpoint
            skip_prefixes = ('/static', '/api/', '/health', '/favicon', '/robots', '/sitemap')
            if not any(request.path.startswith(p) for p in skip_prefixes):
                analytics.track_event(
                    event_type='page_view',
                    page=request.path,
                    event_data={
                        'status_code': response.status_code,
                        'method': request.method
                    }
                )
                # V12.59b: Persistent page view logging to PostgreSQL
                try:
                    import psycopg2
                    pg_conn = psycopg2.connect(os.environ.get('DATABASE_URL', ''))
                    pg_cur = pg_conn.cursor()
                    pg_cur.execute(
                        """INSERT INTO page_views (path, method, status_code, user_agent, ip_address, referrer, session_id, user_id)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                        (
                            request.path,
                            request.method,
                            response.status_code,
                            request.headers.get('User-Agent', '')[:500],
                            request.headers.get('X-Forwarded-For', request.remote_addr or ''),
                            request.headers.get('Referer', ''),
                            request.cookies.get('session_id', ''),
                            getattr(g, 'user_id', None) if hasattr(g, 'user_id') else None
                        )
                    )
                    pg_conn.commit()
                    pg_cur.close()
                    pg_conn.close()
                except Exception:
                    pass  # Never break the page for analytics
    except Exception:
        pass  # Never break the response
    return response


def generate_unsubscribe_token():
    """Generate a unique unsubscribe token."""
    return secrets.token_urlsafe(32)


SAVED_LEADS_FILE = os.path.join(DATA_DIR, 'saved_leads.json')
PERMIT_HISTORY_FILE = os.path.join(DATA_DIR, 'permit_history.json')
VIOLATIONS_FILE = os.path.join(DATA_DIR, 'violations.json')
SIGNALS_FILE = os.path.join(DATA_DIR, 'signals.json')


def load_permit_history():
    """Load permit history index from JSON file."""
    if os.path.exists(PERMIT_HISTORY_FILE):
        with open(PERMIT_HISTORY_FILE) as f:
            return json.load(f)
    return {}


def load_violations():
    """Load code violations from JSON file."""
    if os.path.exists(VIOLATIONS_FILE):
        try:
            with open(VIOLATIONS_FILE) as f:
                return json.load(f, strict=False)
        except Exception as e:
            print(f"[Server] ERROR: Failed to parse violations.json: {e}")
            return []
    return []


def load_signals():
    """Load pre-construction signals from JSON file."""
    if os.path.exists(SIGNALS_FILE):
        try:
            with open(SIGNALS_FILE) as f:
                return json.load(f, strict=False)
        except Exception as e:
            print(f"[Server] ERROR: Failed to parse signals.json: {e}")
            return []
    return []


def normalize_address_for_lookup(address):
    """Normalize an address for lookup (matches collector.py logic)."""
    import re
    if not address:
        return ""
    addr = address.lower().strip()
    addr = re.sub(r'\s+', ' ', addr)
    replacements = [
        (r'\bstreet\b', 'st'),
        (r'\bavenue\b', 'ave'),
        (r'\bboulevard\b', 'blvd'),
        (r'\bdrive\b', 'dr'),
        (r'\broad\b', 'rd'),
        (r'\blane\b', 'ln'),
        (r'\bcourt\b', 'ct'),
        (r'\bplace\b', 'pl'),
        (r'\bapartment\b', 'apt'),
        (r'\bsuite\b', 'ste'),
        (r'\bnorth\b', 'n'),
        (r'\bsouth\b', 's'),
        (r'\beast\b', 'e'),
        (r'\bwest\b', 'w'),
    ]
    for pattern, replacement in replacements:
        addr = re.sub(pattern, replacement, addr)
    addr = re.sub(r'[^\w\s#-]', '', addr)
    return addr


def load_saved_leads():
    """Load saved leads from JSON file."""
    if os.path.exists(SAVED_LEADS_FILE):
        with open(SAVED_LEADS_FILE) as f:
            return json.load(f)
    return []


def save_saved_leads(leads):
    """Save saved leads to JSON file."""
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(SAVED_LEADS_FILE, 'w') as f:
        json.dump(leads, f, indent=2)


def get_user_saved_leads(user_email):
    """Get saved leads for a specific user."""
    all_leads = load_saved_leads()
    return [l for l in all_leads if l.get('user_email') == user_email]


# ===========================
# API ROUTES
# ===========================

@app.route('/')
def index():
    """Serve the dashboard."""
    # V8: Redirect new users to onboarding
    # V9 Fix: Only redirect truly new users - existing users with preferences or Pro plan skip onboarding
    if 'user_email' in session:
        user = find_user_by_email(session['user_email'])
        if user and not user.onboarding_completed:
            # Existing users who already have preferences or are Pro don't need onboarding
            has_preferences = user.city or user.trade
            is_pro = user.plan == 'pro'
            if has_preferences or is_pro:
                # Mark as completed so we don't check again
                user.onboarding_completed = True
                db.session.commit()
            else:
                return redirect('/onboarding')
    footer_cities = get_cities_with_data()

    # V9 Fix 5: Pass user preferences as default filters
    default_city = ''
    default_trade = ''
    if 'user_email' in session:
        user = find_user_by_email(session['user_email'])
        if user:
            default_city = user.city or ''
            default_trade = user.trade or ''

    # V31/V160: City count = only cities with fresh data (last 30 days)
    city_count = get_total_city_count_auto()

    # V160: State count — only states with fresh data
    try:
        _sc = permitdb.get_connection()
        state_count = _sc.execute(
            "SELECT COUNT(DISTINCT state) as cnt FROM prod_cities WHERE newest_permit_date >= date('now', '-30 days') AND source_type IS NOT NULL AND status = 'active'"
        ).fetchone()['cnt']
    except Exception:
        state_count = 38

    # V13: Pass ALL cities for dropdown (sorted by state then city name)
    # This ensures dropdown shows all 555+ cities, not just those in the paginated API response
    all_dropdown_cities = get_cities_with_data()  # Now returns all cities from permits table

    # V13.7: Pass stats for server-side rendering (fixes H5: stat counters showing dashes)
    stats = permitdb.get_permit_stats()
    initial_stats = {
        'total_permits': stats.get('total_permits', 0),
        'total_value': stats.get('total_value', 0),
        'high_value_count': stats.get('high_value_count', 0),
    }

    return render_template('dashboard.html', footer_cities=footer_cities,
                          default_city=default_city, default_trade=default_trade,
                          city_count=city_count, state_count=state_count,
                          all_dropdown_cities=all_dropdown_cities,
                          initial_stats=initial_stats,
                          # V224 T1: hide the sticky filter bar and the 50-card
                          # permit grid on the homepage itself — they're from
                          # the dashboard view and inflate the homepage to
                          # 17,000+ px of mostly-locked cards that users never
                          # scroll through. Moved behind a CTA link to /browse
                          # so the homepage stays a marketing landing page.
                          is_homepage=True)


# V9 Fix 9: /dashboard redirects to homepage (V13.7: redirect to login if not authenticated)
@app.route('/dashboard')
def dashboard_redirect():
    """Redirect /dashboard to /browse for authenticated users, /login for unauthenticated."""
    if 'user_email' not in session:
        return redirect('/login?redirect=dashboard&message=login_required')
    return redirect('/browse')


@app.route('/browse')
def browse_permits():
    """V224 T1: Full interactive permit grid — this is what was living at /
    (the homepage) and making it 17k px tall. Splitting the marketing
    landing page from the data-browse experience: / stays short, /browse
    is the filter-and-scroll dashboard."""
    footer_cities = get_cities_with_data()
    default_city = ''
    default_trade = ''
    if 'user_email' in session:
        user = find_user_by_email(session['user_email'])
        if user:
            default_city = user.city or ''
            default_trade = user.trade or ''
    city_count = get_total_city_count_auto()
    try:
        _sc = permitdb.get_connection()
        state_count = _sc.execute(
            "SELECT COUNT(DISTINCT state) as cnt FROM prod_cities WHERE newest_permit_date >= date('now', '-30 days') AND source_type IS NOT NULL AND status = 'active'"
        ).fetchone()['cnt']
    except Exception:
        state_count = 38
    all_dropdown_cities = get_cities_with_data()
    stats = permitdb.get_permit_stats()
    initial_stats = {
        'total_permits': stats.get('total_permits', 0),
        'total_value': stats.get('total_value', 0),
        'high_value_count': stats.get('high_value_count', 0),
    }
    return render_template('dashboard.html', footer_cities=footer_cities,
                          default_city=default_city, default_trade=default_trade,
                          city_count=city_count, state_count=state_count,
                          all_dropdown_cities=all_dropdown_cities,
                          initial_stats=initial_stats,
                          is_homepage=False)


# V10 Fix 5: /alerts redirects to account page
@app.route('/alerts')
def alerts_redirect():
    """V30: Redirect to appropriate alerts page based on login status."""
    user = get_current_user()
    if user:
        return redirect('/account')
    return redirect('/get-alerts')


@app.route('/api/admin/health')
def admin_daemon_health():
    """V146: Daemon health check — no auth required (for Render health checks)."""
    try:
        conn = permitdb.get_connection()
        # Last collection
        last_coll = conn.execute("SELECT MAX(run_started_at) FROM scraper_runs").fetchone()
        last_coll_at = last_coll[0] if last_coll else None
        # Collections last 24h
        colls_24h = conn.execute("SELECT COUNT(*) FROM scraper_runs WHERE run_started_at > datetime('now', '-24 hours')").fetchone()[0]
        errors_24h = conn.execute("SELECT COUNT(*) FROM scraper_runs WHERE run_started_at > datetime('now', '-24 hours') AND status = 'error'").fetchone()[0]
        # Fresh cities
        fresh = conn.execute("SELECT COUNT(DISTINCT city) FROM permits WHERE date >= date('now', '-7 days') AND date <= date('now')").fetchone()[0]
        conn.close()

        daemon_running = _collector_started
        is_healthy = daemon_running and colls_24h > 0

        status_code = 200 if is_healthy else 503
        return jsonify({
            'status': 'healthy' if is_healthy else 'unhealthy',
            'daemon_running': daemon_running,
            'last_collection_at': last_coll_at,
            'collections_last_24h': colls_24h,
            'errors_last_24h': errors_24h,
            'fresh_city_count': fresh,
        }), status_code
    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)[:100]}), 503


@app.route('/api/health')
def health_check():
    """
    V12.51: Health check endpoint with SQLite data availability check.
    V67: Always return 200 during startup to prevent Render restart loop.
    """
    # V67: During startup, return healthy without touching DB
    # This prevents pool exhaustion from killing health checks
    if not _startup_done:
        return jsonify({
            'status': 'starting',
            'version': APP_VERSION,
            'timestamp': datetime.now().isoformat(),
            'message': 'V67: Background init in progress, service is alive'
        }), 200

    # After startup, do the full health check
    try:
        stats = permitdb.get_permit_stats()
        permit_count = stats['total_permits']
    except Exception as e:
        # V67: Return degraded (still 200!) if DB is temporarily unavailable
        return jsonify({
            'status': 'degraded',
            'version': APP_VERSION,
            'timestamp': datetime.now().isoformat(),
            'message': f'DB temporarily unavailable: {str(e)[:100]}',
            'data_loaded': _initial_data_loaded
        }), 200

    if permit_count == 0 and is_data_loading():
        # No data and we're in a loading state - still return 200 but indicate loading
        return jsonify({
            'status': 'loading',
            'version': APP_VERSION,
            'timestamp': datetime.now().isoformat(),
            'message': 'Data collection in progress',
            'permit_count': 0
        }), 200  # V67: Changed from 503 to 200 to prevent restart loop

    # V16: Collection health tracking
    collection_status = 'never'
    hours_since_collection = None
    if _last_collection_run:
        hours_since_collection = (datetime.now() - _last_collection_run).total_seconds() / 3600
        if hours_since_collection > 12:
            collection_status = 'stale'  # Warning: collection hasn't run recently
        else:
            collection_status = 'healthy'

    return jsonify({
        'status': 'ok',
        'version': APP_VERSION,
        'timestamp': datetime.now().isoformat(),
        'permit_count': permit_count,
        'data_loaded': _initial_data_loaded,
        'collection_status': collection_status,
        'last_collection_run': _last_collection_run.isoformat() if _last_collection_run else None,
        'hours_since_collection': round(hours_since_collection, 1) if hours_since_collection else None
    }), 200


@app.route('/api/permits')
@limiter.limit("60 per minute")
def api_permits():
    """
    GET /api/permits — V12.50: SQL-backed queries.
    Query params: city, trade, value, status, search, quality, page, per_page
    Returns paginated, filtered permit data with lead scores.

    FREEMIUM GATING: Non-Pro users see masked contact info on ALL permits.
    """
    # Parse filters — V174: accept both 'city' and 'city_slug' param names
    city = request.args.get('city', '') or request.args.get('city_slug', '')
    trade = request.args.get('trade', '')
    value = request.args.get('value', '')
    status_filter = request.args.get('status', '')
    quality = request.args.get('quality', '')
    search = request.args.get('search', '')
    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 50))

    # V32: Resolve city slug to name and state for cross-state filtering.
    # V202-4 + V203: Two-stage lookup with a shared alias map.
    # V217 T4: NYC permits have been normalized to city='New York City', so
    # the old ('New York City','NY')→'New York' alias is removed. Kept the
    # CHICAGO case because CITY_REGISTRY chicago_il still carries the
    # uppercase display name while permits rows use titlecase.
    PERMIT_CITY_ALIAS = {
        ('CHICAGO', 'IL'): 'Chicago',          # CITY_REGISTRY chicago_il uppercase
    }
    city_name = None
    city_state = None
    if city:
        city_key, city_config = get_city_by_slug(city)
        if city_config:
            city_name = city_config.get('name', city)
            city_state = city_config.get('state', '')
        else:
            try:
                conn_tmp = permitdb.get_connection()
                row = conn_tmp.execute(
                    "SELECT city, state FROM prod_cities WHERE city_slug = ? LIMIT 1",
                    (city,)
                ).fetchone()
                if row:
                    city_name = row['city'] if isinstance(row, dict) else row[0]
                    city_state = row['state'] if isinstance(row, dict) else row[1]
                else:
                    city_name = city  # Use as-is if not a valid slug
            except Exception:
                city_name = city
        # Apply alias after resolution — covers both registry and prod_cities paths.
        if city_name is not None:
            city_name = PERMIT_CITY_ALIAS.get((city_name, city_state), city_name)

    # Resolve trade slug to name if needed
    trade_name = None
    if trade and trade != 'all-trades':
        trade_config = get_trade(trade)
        if trade_config:
            trade_name = trade_config.get('name', trade)
        else:
            trade_name = trade  # Use as-is if not a valid slug

    # V13.2: SQL ORDER BY prioritizes data quality so "All Cities" default shows
    # best data first, not just Mesa permits with garbage dates (which sort high
    # lexicographically because "WROCCO" > "2026-03-24").
    #
    # Priority: high cost → valid date → has address → has contact → recent date
    # This ensures Austin (85 pts) and Chicago (72 pts) surface before Mesa (28 pts).
    data_quality_order = """
        CASE WHEN estimated_cost > 100000 THEN 0
             WHEN estimated_cost > 10000 THEN 1
             WHEN estimated_cost > 0 THEN 2
             ELSE 3 END,
        CASE WHEN filing_date GLOB '[0-9][0-9][0-9][0-9]-*' THEN 0 ELSE 1 END,
        CASE WHEN address IS NOT NULL AND address != '' THEN 0 ELSE 1 END,
        CASE WHEN contractor_name IS NOT NULL OR contact_phone IS NOT NULL THEN 0 ELSE 1 END,
        filing_date DESC
    """

    # V12.50: Query SQLite database (replaces loading 100K permits into memory)
    # V32: Pass state to prevent cross-state data pollution
    permits, total = permitdb.query_permits(
        city=city_name,
        state=city_state,
        trade=trade_name,
        value=value or None,
        status=status_filter or None,
        search=search or None,
        page=page,
        per_page=per_page,
        order_by=data_quality_order
    )

    # Add lead scores to page results
    permits = add_lead_scores(permits)

    # Sort by lead score (hot leads first) within page
    permits.sort(key=lambda x: x.get('lead_score', 0), reverse=True)

    # Quality filter (post-query since lead_score is computed)
    if quality:
        if quality == 'hot':
            permits = [p for p in permits if p.get('lead_quality') == 'hot']
        elif quality == 'warm':
            permits = [p for p in permits if p.get('lead_quality') in ('hot', 'warm')]

    # FREEMIUM GATING: Strip contact info for ALL permits for non-Pro users
    user = get_current_user()
    user_is_pro = is_pro(user)

    if not user_is_pro:
        for permit in permits:
            permit['contact_phone'] = None
            permit['contact_name'] = None
            permit['contact_email'] = None
            permit['owner_name'] = None
            permit['is_gated'] = True
    else:
        for permit in permits:
            permit['is_gated'] = False

    # V12.50: Aggregate stats from SQL (not from loading all permits!)
    stats_data = permitdb.get_permit_stats()
    collection_stats = load_stats()  # Keep this for collected_at timestamp

    return jsonify({
        'permits': permits,
        'total': total,
        'page': page,
        'per_page': per_page,
        'total_pages': (total + per_page - 1) // per_page,
        'user_is_pro': user_is_pro,
        'last_updated': collection_stats.get('collected_at', '') or datetime.now().isoformat(),
        'total_value': stats_data['total_value'],
        'high_value_count': stats_data['high_value_count'],
        'total_permits': stats_data['total_permits'],
    })

@app.route('/api/stats')
def api_stats():
    """GET /api/stats — V12.50: SQL-backed stats."""
    stats_data = permitdb.get_permit_stats()
    collection_stats = load_stats()

    return jsonify({
        'total_permits': stats_data['total_permits'],
        'total_value': stats_data['total_value'],
        'high_value_count': stats_data['high_value_count'],
        'cities': stats_data['city_count'],
        'trade_breakdown': collection_stats.get('trade_breakdown', {}),
        'value_breakdown': collection_stats.get('value_breakdown', {}),
        'last_updated': collection_stats.get('collected_at', ''),
    })

@app.route('/api/filters')
def api_filters():
    """GET /api/filters - Available filter options (V12.51: SQL-backed)."""
    conn = permitdb.get_connection()

    cities = [r[0] for r in conn.execute(
        "SELECT DISTINCT city FROM permits WHERE city IS NOT NULL AND city != '' ORDER BY city"
    ).fetchall()]

    trades = [r[0] for r in conn.execute(
        "SELECT DISTINCT trade_category FROM permits WHERE trade_category IS NOT NULL AND trade_category != '' ORDER BY trade_category"
    ).fetchall()]

    statuses = [r[0] for r in conn.execute(
        "SELECT DISTINCT status FROM permits WHERE status IS NOT NULL AND status != '' ORDER BY status"
    ).fetchall()]

    return jsonify({
        'cities': cities,
        'trades': trades,
        'statuses': statuses,
    })


@app.route('/api/cities')
def api_cities():
    """GET /api/cities - Get all active cities with permit data.

    V90: Now reads from prod_cities table (database) instead of static CITY_REGISTRY.
    Only returns cities with actual permit data (total_permits > 0).
    """
    # Get cities with data from database
    cities = permitdb.get_prod_cities(status='active', min_permits=1)

    # Format for frontend compatibility
    formatted_cities = []
    for city in cities:
        formatted_cities.append({
            'name': city['name'],
            'state': city['state'],
            'slug': city['slug'],
            'permit_count': city['permit_count'],
            'active': city['active'],
        })

    return jsonify({
        'count': len(formatted_cities),
        'cities': formatted_cities,
    })


@app.route('/api/city-health')
def api_city_health():
    """GET /api/city-health - Get city API health status."""
    health_file = os.path.join(DATA_DIR, 'city_health.json')
    if os.path.exists(health_file):
        with open(health_file) as f:
            return jsonify(json.load(f))
    return jsonify({'status': 'no health data available'})


@app.route('/api/subscribe', methods=['POST'])
@limiter.limit("5 per minute")
def api_subscribe():
    """POST /api/subscribe - Add email alert subscriber.

    V12.53: Now uses User model instead of subscribers.json.
    Creates a lightweight User record for digest subscriptions.
    """
    data = request.get_json()
    if not data or not data.get('email'):
        return jsonify({'error': 'Email required'}), 400

    email = data['email'].strip().lower()
    # V218 T5B: server-side email format validation. The previous 'email
    # required' check let literal 'notanemail' land in the User table,
    # polluting subscriber lists and breaking downstream SendGrid calls.
    # RFC 5322 is more permissive than this but for our digest signup
    # the local@domain.tld shape is exactly what we want to enforce.
    import re as _re
    if not _re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', email) or len(email) > 254:
        return jsonify({'error': 'Please enter a valid email address'}), 400

    city = data.get('city', '').strip().title()  # V12.64: Normalize to titlecase
    trade = data.get('trade', '')
    # V251 F4: capture active-filter context so the digest narrows to what
    # the subscriber was actually viewing. Stored per-user (last wins), which
    # is a compromise — multi-city subscribers with different filters per
    # city need a proper user_city_subscriptions table. Fine for MVP.
    zip_filter = (data.get('zip') or '').strip()[:16]
    trade_filter = (data.get('trade') or '').strip()[:64]

    # Check if user already exists
    existing = find_user_by_email(email)
    if existing:
        # Update their digest settings
        cities = json.loads(existing.digest_cities or '[]')
        if city and city not in cities:
            cities.append(city)
            existing.digest_cities = json.dumps(cities)
        existing.digest_active = True
        if trade:
            existing.trade = trade
        # V251 F4: persist filters (clearing if empty, so the user can unset
        # by resubscribing without filters).
        existing.digest_zip_filter = zip_filter or None
        existing.digest_trade_filter = trade_filter or None
        db.session.commit()

        return jsonify({
            'message': f'Updated digest settings for {email}',
            'subscriber': {'email': email, 'city': city, 'trade': trade, 'zip': zip_filter},
        }), 200

    # Create new lightweight user for digest subscription
    import secrets
    try:
        new_user = User(
            email=email,
            name=data.get('name', ''),
            password_hash='',  # No password - digest-only user
            plan='free',
            digest_active=True,
            digest_cities=json.dumps([city]) if city else '[]',
            trade=trade,
            digest_zip_filter=zip_filter or None,
            digest_trade_filter=trade_filter or None,
            unsubscribe_token=secrets.token_urlsafe(32),
        )
        db.session.add(new_user)
        db.session.commit()
        print(f"[Subscribe] Created digest user: {email}")
    except IntegrityError:
        db.session.rollback()
        return jsonify({'error': 'Email already exists'}), 409

    # Track alert signup event
    analytics.track_event('alert_signup', event_data={
        'city': city,
        'trade': trade
    }, city_filter=city)

    return jsonify({
        'message': f'Successfully subscribed {email}',
        'subscriber': {'email': email, 'city': city, 'trade': trade, 'zip': zip_filter},
    }), 201


@app.route('/api/subscribers')
def api_subscribers():
    """GET /api/subscribers - List all digest subscribers (admin endpoint).

    V12.53: Now queries User model instead of subscribers.json.
    """
    # Check admin authentication
    if not session.get('admin_authenticated'):
        return jsonify({'error': 'Admin authentication required'}), 401

    users = User.query.filter(User.digest_active == True).all()
    subs = []
    for u in users:
        subs.append({
            'email': u.email,
            'name': u.name,
            'city': json.loads(u.digest_cities or '[]'),
            'trade': u.trade,
            'plan': u.plan,
            'subscribed_at': u.created_at.isoformat() if u.created_at else None,
        })

    return jsonify({
        'total': len(subs),
        'subscribers': subs,
    })

@app.route('/api/export')
def api_export():
    """GET /api/export - Export filtered permits as CSV with lead scores.

    PRO FEATURE: Non-Pro users cannot export and are redirected to pricing.
    """
    # Check if user is Pro - exports are a Pro feature
    user = get_current_user()
    if not is_pro(user):
        return jsonify({
            'error': 'Export is a Pro feature',
            'upgrade_url': '/pricing'
        }), 403

    # V12.51: SQL-backed export
    city = request.args.get('city', '')
    trade = request.args.get('trade', '')
    quality = request.args.get('quality', '')

    permits, _ = permitdb.query_permits(
        city=city or None,
        trade=trade or None,
        page=1,
        per_page=50000,  # Export limit
        order_by='filing_date DESC'
    )
    permits = add_lead_scores(permits)

    # Quality filter (post-query since lead_score is computed)
    if quality:
        if quality == 'hot':
            permits = [p for p in permits if p.get('lead_quality') == 'hot']
        elif quality == 'warm':
            permits = [p for p in permits if p.get('lead_quality') in ('hot', 'warm')]

    # Sort by lead score
    permits.sort(key=lambda x: x.get('lead_score', 0), reverse=True)

    # Build CSV
    if not permits:
        return "No permits match your filters", 404

    headers = ['address', 'city', 'state', 'zip', 'trade_category', 'estimated_cost',
               'status', 'lifecycle_stage', 'filing_date', 'contact_name', 'contact_phone', 'description',
               'lead_score', 'lead_quality']

    lines = [','.join(headers)]
    for p in permits:
        # Build row with lifecycle stage
        row = []
        for h in headers:
            if h == 'lifecycle_stage':
                row.append(get_lifecycle_label(p))
            else:
                row.append(str(p.get(h, '')).replace(',', ';').replace('"', "'").replace('\n', ' ')[:200])
        lines.append(','.join(f'"{v}"' for v in row))

    csv_content = '\n'.join(lines)

    # Track CSV export event
    analytics.track_event('csv_export', event_data={
        'row_count': len(permits),
        'filters': {'city': city, 'trade': trade, 'quality': quality}
    }, city_filter=city, trade_filter=trade)

    return Response(
        csv_content,
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename=permitgrab_leads_{datetime.now().strftime("%Y%m%d")}.csv'}
    )


# ===========================
# SAVED LEADS / CRM API
# ===========================

@app.route('/api/saved-leads', methods=['GET'])
def get_saved_leads():
    """GET /api/saved-leads - Get saved leads for logged-in user."""
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Login required'}), 401

    user_leads = get_user_saved_leads(user['email'])

    # V12.51: Enrich with permit data from SQLite
    all_permits, _ = permitdb.query_permits(page=1, per_page=100000)
    permits = add_lead_scores(all_permits)
    permit_map = {p.get('permit_number'): p for p in permits}

    enriched_leads = []
    for lead in user_leads:
        permit = permit_map.get(lead.get('permit_id'), {})
        enriched_leads.append({
            **lead,
            'permit': permit,
        })

    # Calculate stats
    total_value = sum(l['permit'].get('estimated_cost', 0) for l in enriched_leads if l.get('permit'))
    status_counts = {}
    for l in enriched_leads:
        status = l.get('status', 'new')
        status_counts[status] = status_counts.get(status, 0) + 1

    return jsonify({
        'leads': enriched_leads,
        'total': len(enriched_leads),
        'total_value': total_value,
        'status_counts': status_counts,
    })


@app.route('/api/saved-leads', methods=['POST'])
def save_lead():
    """POST /api/saved-leads - Save a lead for the logged-in user.

    PRO FEATURE: Only Pro users can save leads.
    """
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Login required'}), 401

    # Save Lead is a Pro feature
    if not is_pro(user):
        return jsonify({
            'error': 'Save Lead is a Pro feature',
            'upgrade_url': '/pricing'
        }), 403

    data = request.get_json()
    if not data or not data.get('permit_id'):
        return jsonify({'error': 'permit_id required'}), 400

    all_leads = load_saved_leads()

    # Check if already saved
    existing = next((l for l in all_leads if l['user_email'] == user['email'] and l['permit_id'] == data['permit_id']), None)
    if existing:
        return jsonify({'error': 'Lead already saved'}), 409

    new_lead = {
        'permit_id': data['permit_id'],
        'user_email': user['email'],
        'status': 'new',
        'notes': '',
        'date_saved': datetime.now().isoformat(),
    }

    all_leads.append(new_lead)
    save_saved_leads(all_leads)

    # Track lead save event
    analytics.track_event('lead_save', event_data={
        'permit_id': data['permit_id'],
        'permit_value': data.get('permit_value', 0)
    })

    return jsonify({'message': 'Lead saved', 'lead': new_lead}), 201


@app.route('/api/saved-leads/<permit_id>', methods=['PUT'])
def update_saved_lead(permit_id):
    """PUT /api/saved-leads/<permit_id> - Update status/notes for a saved lead."""
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Login required'}), 401

    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400

    all_leads = load_saved_leads()
    lead = next((l for l in all_leads if l['user_email'] == user['email'] and l['permit_id'] == permit_id), None)

    if not lead:
        return jsonify({'error': 'Lead not found'}), 404

    # Update fields
    if 'status' in data:
        lead['status'] = data['status']
    if 'notes' in data:
        lead['notes'] = data['notes']
    lead['updated_at'] = datetime.now().isoformat()

    save_saved_leads(all_leads)

    return jsonify({'message': 'Lead updated', 'lead': lead})


@app.route('/api/saved-leads/<permit_id>', methods=['DELETE'])
def delete_saved_lead(permit_id):
    """DELETE /api/saved-leads/<permit_id> - Remove a saved lead."""
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Login required'}), 401

    all_leads = load_saved_leads()
    original_count = len(all_leads)
    all_leads = [l for l in all_leads if not (l['user_email'] == user['email'] and l['permit_id'] == permit_id)]

    if len(all_leads) == original_count:
        return jsonify({'error': 'Lead not found'}), 404

    save_saved_leads(all_leads)

    return jsonify({'message': 'Lead removed'})


@app.route('/api/saved-leads/export')
def export_saved_leads():
    """GET /api/saved-leads/export - Export saved leads as CSV.

    PRO FEATURE: Only Pro users can export.
    """
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Login required'}), 401

    if not is_pro(user):
        return jsonify({
            'error': 'Export is a Pro feature',
            'upgrade_url': '/pricing'
        }), 403

    user_leads = get_user_saved_leads(user['email'])
    # V12.51: SQL-backed
    all_permits, _ = permitdb.query_permits(page=1, per_page=100000)
    permits = add_lead_scores(all_permits)
    permit_map = {p.get('permit_number'): p for p in permits}

    if not user_leads:
        return "No saved leads to export", 404

    headers = ['address', 'city', 'state', 'zip', 'trade_category', 'estimated_cost',
               'permit_status', 'lifecycle_stage', 'filing_date', 'contact_name', 'contact_phone', 'description',
               'lead_score', 'lead_quality', 'crm_status', 'notes', 'date_saved']

    lines = [','.join(headers)]
    for lead in user_leads:
        permit = permit_map.get(lead.get('permit_id'), {})
        row = [
            str(permit.get('address', '')).replace(',', ';').replace('"', "'"),
            str(permit.get('city', '')),
            str(permit.get('state', '')),
            str(permit.get('zip', '')),
            str(permit.get('trade_category', '')),
            str(permit.get('estimated_cost', '')),
            str(permit.get('status', '')),
            get_lifecycle_label(permit),
            str(permit.get('filing_date', '')),
            str(permit.get('contact_name', '')).replace(',', ';'),
            str(permit.get('contact_phone', '')),
            str(permit.get('description', '')).replace(',', ';').replace('"', "'").replace('\n', ' ')[:150],
            str(permit.get('lead_score', '')),
            str(permit.get('lead_quality', '')),
            str(lead.get('status', '')),
            str(lead.get('notes', '')).replace(',', ';').replace('"', "'").replace('\n', ' ')[:100],
            str(lead.get('date_saved', ''))[:10],
        ]
        lines.append(','.join(f'"{v}"' for v in row))

    csv_content = '\n'.join(lines)

    return Response(
        csv_content,
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename=permitgrab_my_leads_{datetime.now().strftime("%Y%m%d")}.csv'}
    )


# ===========================
# SAVED SEARCHES API
# ===========================

SAVED_SEARCHES_FILE = os.path.join(DATA_DIR, 'saved_searches.json')

def load_saved_searches():
    """Load all saved searches from JSON file."""
    if os.path.exists(SAVED_SEARCHES_FILE):
        try:
            with open(SAVED_SEARCHES_FILE, 'r') as f:
                return json.load(f)
        except:
            return []
    return []

def save_saved_searches(searches):
    """Save all saved searches to JSON file."""
    with open(SAVED_SEARCHES_FILE, 'w') as f:
        json.dump(searches, f, indent=2)

def get_user_saved_searches(email):
    """Get saved searches for a specific user."""
    all_searches = load_saved_searches()
    return [s for s in all_searches if s.get('user_email') == email]

## Old saved-searches JSON-file-based API removed in V170 B4 — replaced by SQLAlchemy model


# ===========================
# PERMIT HISTORY API
# ===========================

@app.route('/api/permit-history/<path:address>')
def api_permit_history(address):
    """
    GET /api/permit-history/<address>
    Returns historical permits at the given address.
    """
    history = load_permit_history()

    # Normalize the input address for lookup
    normalized_addr = normalize_address_for_lookup(address)

    if not normalized_addr:
        return jsonify({'error': 'Address required'}), 400

    # Look up in history index
    entry = history.get(normalized_addr)

    if not entry:
        # Try partial match
        for key, value in history.items():
            if normalized_addr in key or key in normalized_addr:
                entry = value
                break

    if not entry:
        return jsonify({
            'address': address,
            'permits': [],
            'permit_count': 0,
            'is_repeat_renovator': False,
        })

    permit_count = len(entry.get('permits', []))

    return jsonify({
        'address': entry.get('address', address),
        'city': entry.get('city', ''),
        'state': entry.get('state', ''),
        'permits': entry.get('permits', []),
        'permit_count': permit_count,
        'is_repeat_renovator': permit_count >= 3,
    })


## Old violations API (V82/V156) removed in V163 — replaced by V162 database-backed version


# ===========================
# CONTRACTOR INTELLIGENCE API
# ===========================

@app.route('/api/contractors')
def api_contractors():
    """
    GET /api/contractors
    Query params: city, search, sort_by, sort_order, page, per_page
    Returns aggregated contractor data from permits.
    V12.51: SQL-backed, V13.5: Added error handling
    """
    try:
        city = request.args.get('city', '')
        permits, _ = permitdb.query_permits(city=city or None, page=1, per_page=100000)

        # Aggregate by contractor name
        contractors = {}
        for p in permits:
            name = p.get('contact_name', '').strip()
            if not name or name.lower() in ('n/a', 'unknown', 'none', ''):
                continue

            if name not in contractors:
                contractors[name] = {
                    'name': name,
                    'total_permits': 0,
                    'total_value': 0,
                    'cities': set(),
                    'trades': {},
                    'most_recent_date': '',
                    'permits': [],
                }

            contractors[name]['total_permits'] += 1
            contractors[name]['total_value'] += p.get('estimated_cost', 0) or 0
            contractors[name]['cities'].add(p.get('city', ''))

            trade = p.get('trade_category', 'Other')
            contractors[name]['trades'][trade] = contractors[name]['trades'].get(trade, 0) + 1

            filing_date = p.get('filing_date', '')
            if filing_date > contractors[name]['most_recent_date']:
                contractors[name]['most_recent_date'] = filing_date

            contractors[name]['permits'].append(p.get('permit_number'))

        # Convert to list and determine primary trade
        contractor_list = []
        for name, data in contractors.items():
            primary_trade = max(data['trades'].items(), key=lambda x: x[1])[0] if data['trades'] else 'Unknown'
            contractor_list.append({
                'name': data['name'],
                'total_permits': data['total_permits'],
                'total_value': data['total_value'],
                'cities': sorted(list(data['cities'])),
                'city_count': len(data['cities']),
                'primary_trade': primary_trade,
                'most_recent_date': data['most_recent_date'],
                'permit_ids': data['permits'][:50],
            })

        # Search filter
        search = request.args.get('search', '').lower()
        if search:
            contractor_list = [c for c in contractor_list if search in c['name'].lower()]

        # Sorting
        sort_by = request.args.get('sort_by', 'total_permits')
        sort_order = request.args.get('sort_order', 'desc')
        reverse = sort_order == 'desc'

        if sort_by == 'name':
            contractor_list.sort(key=lambda x: x['name'].lower(), reverse=reverse)
        elif sort_by == 'total_value':
            contractor_list.sort(key=lambda x: x['total_value'], reverse=reverse)
        elif sort_by == 'most_recent_date':
            contractor_list.sort(key=lambda x: x['most_recent_date'] or '', reverse=reverse)
        else:
            contractor_list.sort(key=lambda x: x['total_permits'], reverse=reverse)

        # Pagination
        page = int(request.args.get('page', 1))
        per_page = int(request.args.get('per_page', 50))
        total = len(contractor_list)
        start = (page - 1) * per_page
        page_contractors = contractor_list[start:start + per_page]

        return jsonify({
            'contractors': page_contractors,
            'total': total,
            'page': page,
            'per_page': per_page,
            'total_pages': (total + per_page - 1) // per_page,
        })
    except Exception as e:
        print(f"[ERROR] /api/contractors failed: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e), 'contractors': [], 'total': 0}), 500


@app.route('/api/contractors/<path:name>')
def api_contractor_detail(name):
    """
    GET /api/contractors/<name>
    Returns all permits for a specific contractor.
    V12.51: SQL-backed
    """
    permits, _ = permitdb.query_permits(page=1, per_page=100000)
    permits = add_lead_scores(permits)

    # Find permits by contractor name (case-insensitive)
    contractor_permits = [p for p in permits if p.get('contact_name', '').lower() == name.lower()]

    if not contractor_permits:
        return jsonify({'error': 'Contractor not found'}), 404

    # Calculate stats
    total_value = sum(p.get('estimated_cost', 0) or 0 for p in contractor_permits)
    cities = sorted(set(p.get('city', '') for p in contractor_permits))
    trades = {}
    for p in contractor_permits:
        trade = p.get('trade_category', 'Other')
        trades[trade] = trades.get(trade, 0) + 1

    return jsonify({
        'name': name,
        'permits': contractor_permits,
        'total_permits': len(contractor_permits),
        'total_value': total_value,
        'cities': cities,
        'trade_breakdown': trades,
    })


@app.route('/api/contractors/top')
def api_top_contractors():
    """
    GET /api/contractors/top
    Query params: city, limit
    Returns top contractors by permit volume.
    V12.51: SQL-backed
    """
    city = request.args.get('city', '')
    permits, _ = permitdb.query_permits(city=city or None, page=1, per_page=100000)

    limit = int(request.args.get('limit', 5))

    # V12.55: Aggregate by contractor with improved junk name filter
    JUNK_NAMES = {'n/a', 'unknown', 'none', 'na', 'tbd', 'tba', 'pending',
                  'various', 'multiple', 'owner', 'owner/builder', 'self',
                  'homeowner', 'not provided', 'not applicable', 'see plans',
                  'not listed', 'not available', 'exempt', '---', '--', '-'}

    contractors = {}
    for p in permits:
        name = (p.get('contact_name') or '').strip()
        if not name:
            continue
        name_lower = name.lower()

        # Skip exact junk matches
        if name_lower in JUNK_NAMES:
            continue

        # Skip names that START with common junk prefixes
        if name_lower.startswith(('none ', 'n/a ', 'unknown ', 'tbd ', 'owner ')):
            continue

        # Skip very short names (likely data artifacts)
        if len(name) < 3:
            continue

        if name not in contractors:
            contractors[name] = {'name': name, 'permits': 0, 'value': 0}

        contractors[name]['permits'] += 1
        contractors[name]['value'] += p.get('estimated_cost', 0) or 0

    # Sort by permit count
    top_list = sorted(contractors.values(), key=lambda x: x['permits'], reverse=True)[:limit]

    return jsonify({
        'top_contractors': top_list,
        'city': city or 'All Cities',
    })


# ===========================
# V79: BLOG SYSTEM
# ===========================

@app.route('/blog')
def blog_index():
    """V79: Blog index page listing all posts by category."""
    footer_cities = get_cities_with_data()

    # Group posts by category
    categories = {
        'permit-costs': {
            'title': 'Permit Cost Guides',
            'posts': get_blog_posts_by_category('permit-costs')
        },
        'contractor-leads': {
            'title': 'Finding Construction Leads',
            'posts': get_blog_posts_by_category('contractor-leads')
        },
        'trade-guides': {
            'title': 'Trade-Specific Guides',
            'posts': get_blog_posts_by_category('trade-guides')
        }
    }

    return render_template('blog_index.html',
                           categories=categories,
                           all_posts=BLOG_POSTS,
                           footer_cities=footer_cities)


@app.route('/blog/<slug>')
def blog_post(slug):
    """V79: Individual blog post page."""
    post = next((p for p in BLOG_POSTS if p['slug'] == slug), None)
    if not post:
        abort(404)

    footer_cities = get_cities_with_data()
    related_posts = get_related_posts(slug, limit=3)

    return render_template('blog_post.html',
                           post=post,
                           related_posts=related_posts,
                           footer_cities=footer_cities)


@app.route('/contractors')
def contractors_page():
    """Render the Contractors Intelligence page."""
    footer_cities = get_cities_with_data()
    return render_template('contractors.html', footer_cities=footer_cities)


@app.route('/contractor/<int:contractor_id>')
def contractor_detail(contractor_id):
    """V251 F6: Contractor detail page.

    Shows: business name, velocity + recency, gated phone, full permit
    history, first-seen, violations-at-their-job-sites (address join).
    Non-Pro visitors see the name + velocity + permit count but the phone
    row is replaced with a "Subscribe to reveal" CTA matching F1/F3.
    """
    from contractor_profiles import is_license_number
    from datetime import date as _date, datetime as _dt
    conn = permitdb.get_connection()
    prof = conn.execute("""
        SELECT id, contractor_name_raw, contractor_name_normalized,
               source_city_key, city, state,
               total_permits, permits_90d, permits_30d, primary_trade,
               trade_breakdown, avg_project_value, max_project_value,
               total_project_value, primary_area, first_permit_date,
               last_permit_date, permit_frequency, phone, website, email,
               license_number, license_status, enrichment_status
        FROM contractor_profiles WHERE id = ?
    """, (contractor_id,)).fetchone()
    if not prof:
        return render_template('404.html'), 404

    raw = prof['contractor_name_raw']
    is_license = is_license_number(prof['contractor_name_normalized'])
    display_name = f"License #{raw}" if is_license else raw

    # V251 F5 velocity/recency (mirror of city-page computation)
    today = _date.today()
    def _days_since(s):
        if not s: return None
        try:
            return (today - _dt.strptime(s[:10], '%Y-%m-%d').date()).days
        except Exception:
            return None
    last_age = _days_since(prof['last_permit_date'])
    first_age = _days_since(prof['first_permit_date'])
    p30 = prof['permits_30d'] or 0
    if p30 >= 10:
        velocity_color = 'red'
    elif p30 >= 5:
        velocity_color = 'orange'
    elif p30 >= 1:
        velocity_color = 'blue'
    else:
        velocity_color = ''
    if last_age is not None and last_age <= 7:
        recency = 'green'
    elif last_age is not None and last_age <= 30:
        recency = 'yellow'
    else:
        recency = 'gray'
    is_new = first_age is not None and first_age <= 7

    # Permit history — per V250 Phase 1A finding, permits for a city are
    # spread across multiple source_city_key values (e.g. chicago,
    # chicago-il, chicago_il) all linked to the same prod_cities.id. Use
    # prod_city_id so we catch all of them.
    _profile_pc = conn.execute(
        "SELECT id FROM prod_cities WHERE city_slug = ?", (prof['source_city_key'],)
    ).fetchone()
    _profile_pc_id = _profile_pc[0] if _profile_pc else None
    if _profile_pc_id:
        # Exact-case match — permit data arrives pre-upper from Chicago and
        # most large cities. Avoids the UPPER() full-scan on 25k+ permits
        # which was timing out the detail page on big-city contractors.
        # Both raw + normalized are tried so DB-normalized entries still hit.
        permits = conn.execute("""
            SELECT filing_date, issued_date, date, permit_type, address,
                   description, estimated_cost, trade_category, status,
                   zip, source_city_key
            FROM permits
            WHERE prod_city_id = ?
              AND (contractor_name = ? OR contractor_name = ?
                   OR contact_name = ? OR contact_name = ?)
            ORDER BY COALESCE(filing_date, issued_date, date) DESC
            LIMIT 200
        """, (_profile_pc_id, raw, (prof['contractor_name_normalized'] or ''),
              raw, (prof['contractor_name_normalized'] or ''))).fetchall()
    else:
        permits = conn.execute("""
            SELECT filing_date, issued_date, date, permit_type, address,
                   description, estimated_cost, trade_category, status,
                   zip, source_city_key
            FROM permits
            WHERE source_city_key = ?
              AND (UPPER(contractor_name) = UPPER(?)
                   OR UPPER(contractor_name) = UPPER(?)
                   OR UPPER(contact_name) = UPPER(?))
            ORDER BY COALESCE(filing_date, issued_date, date) DESC
            LIMIT 200
        """, (prof['source_city_key'], raw, prof['contractor_name_normalized'], raw)).fetchall()

    # Cities this contractor is also active in
    other_cities = conn.execute("""
        SELECT source_city_key, COUNT(*) as n
        FROM permits
        WHERE UPPER(contractor_name) = UPPER(?)
          AND source_city_key != ?
        GROUP BY source_city_key ORDER BY n DESC LIMIT 5
    """, (raw, prof['source_city_key'])).fetchall()

    # Violations at addresses this contractor worked. Join on normalized address.
    violations = []
    if permits:
        addrs = list({p['address'] for p in permits if p['address']})[:50]
        if addrs:
            placeholders = ','.join(['?'] * len(addrs))
            violations = conn.execute(f"""
                SELECT violation_date, violation_type, violation_description,
                       status, address
                FROM violations
                WHERE UPPER(address) IN ({placeholders})
                ORDER BY violation_date DESC LIMIT 25
            """, [a.upper() for a in addrs]).fetchall()

    # Pretty city name for the nav/breadcrumb
    city_name = prof['city'] or prof['source_city_key']

    return render_template(
        'contractor_detail.html',
        contractor={
            'id': prof['id'],
            'display_name': display_name,
            'is_license_number': is_license,
            'source_city_key': prof['source_city_key'],
            'city': city_name,
            'state': prof['state'],
            'total_permits': prof['total_permits'] or 0,
            'permits_90d': prof['permits_90d'] or 0,
            'permits_30d': p30,
            'primary_trade': prof['primary_trade'],
            'avg_project_value': prof['avg_project_value'],
            'max_project_value': prof['max_project_value'],
            'phone': prof['phone'],
            'website': prof['website'],
            'email': prof['email'],
            'license_number': prof['license_number'],
            'first_permit_date': prof['first_permit_date'],
            'last_permit_date': prof['last_permit_date'],
            'velocity_color': velocity_color,
            'recency': recency,
            'is_new': is_new,
        },
        permits=[dict(p) for p in permits],
        other_cities=[dict(c) for c in other_cities],
        violations=[dict(v) for v in violations],
        canonical_url=f"{SITE_URL}/contractor/{contractor_id}",
    )


@app.route('/pricing')
def pricing_page():
    """Render the Pricing page. V12.51: SQL-backed"""
    user = get_current_user()
    cities = get_all_cities_info()
    city_count = get_total_city_count_auto()  # V31: Active cities only
    footer_cities = get_cities_with_data()
    # V12.51: Get permit count from SQLite
    stats = permitdb.get_permit_stats()
    permit_count = stats['total_permits']
    return render_template('pricing.html', user=user, cities=cities, city_count=city_count, footer_cities=footer_cities, permit_count=permit_count)


@app.route('/signup')
def signup_page():
    """Render the Sign Up page."""
    # Redirect if already logged in
    if get_current_user():
        return redirect('/')
    footer_cities = get_cities_with_data()
    return render_template('signup.html', footer_cities=footer_cities)


@app.route('/login')
def login_page():
    """Render the Login page."""
    # Redirect if already logged in
    if get_current_user():
        return redirect('/')
    footer_cities = get_cities_with_data()
    # V13.7: Handle redirect messages (e.g., from /dashboard redirect)
    message = request.args.get('message', '')
    login_message = None
    if message == 'login_required':
        login_message = 'Please log in to access your dashboard.'
    return render_template('login.html', footer_cities=footer_cities, login_message=login_message)


# ===========================
# PASSWORD RESET
# ===========================
PASSWORD_RESET_FILE = os.path.join(DATA_DIR, 'password_reset_tokens.json')


def load_reset_tokens():
    """Load password reset tokens from JSON file."""
    if os.path.exists(PASSWORD_RESET_FILE):
        with open(PASSWORD_RESET_FILE) as f:
            return json.load(f)
    return {}


def save_reset_tokens(tokens):
    """Save password reset tokens to JSON file."""
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(PASSWORD_RESET_FILE, 'w') as f:
        json.dump(tokens, f, indent=2)


def generate_reset_token():
    """Generate a secure random token for password reset."""
    return secrets.token_urlsafe(32)


def cleanup_expired_tokens():
    """Remove expired reset tokens."""
    tokens = load_reset_tokens()
    now = datetime.now().isoformat()
    valid_tokens = {k: v for k, v in tokens.items() if v.get('expires', '') > now}
    save_reset_tokens(valid_tokens)
    return valid_tokens


@app.route('/forgot-password')
def forgot_password_page():
    """Render the Forgot Password page."""
    footer_cities = get_cities_with_data()
    return render_template('forgot_password.html', footer_cities=footer_cities)


@app.route('/api/forgot-password', methods=['POST'])
@limiter.limit("5 per minute")
def api_forgot_password():
    """
    POST /api/forgot-password - Request a password reset email.
    Body: { email: string }
    """
    data = request.get_json()
    if not data or not data.get('email'):
        return jsonify({'error': 'Email is required'}), 400

    email = data['email'].lower().strip()

    # Check if user exists
    users = load_users()
    user = next((u for u in users if u['email'].lower() == email), None)

    # Always return success to prevent email enumeration
    if not user:
        return jsonify({'success': True, 'message': 'If that email exists, a reset link has been sent.'})

    # Generate token with 1-hour expiry
    token = generate_reset_token()
    expires = (datetime.now() + timedelta(hours=1)).isoformat()

    # Save token
    tokens = load_reset_tokens()
    tokens[token] = {
        'email': email,
        'expires': expires,
        'used': False
    }
    save_reset_tokens(tokens)

    # Send reset email
    reset_url = f"https://permitgrab.com/reset-password/{token}"
    html_body = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; color: #333; }}
            .container {{ max-width: 600px; margin: 0 auto; padding: 40px 20px; }}
            .logo {{ font-size: 24px; font-weight: 700; color: #111; margin-bottom: 24px; }}
            .logo span {{ color: #f97316; }}
            .btn {{ display: inline-block; background: #2563eb; color: white; padding: 14px 28px; border-radius: 8px; text-decoration: none; font-weight: 600; }}
            .footer {{ margin-top: 32px; font-size: 13px; color: #666; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="logo">Permit<span>Grab</span></div>
            <h2>Reset Your Password</h2>
            <p>We received a request to reset your password. Click the button below to create a new password:</p>
            <p><a href="{reset_url}" class="btn">Reset Password</a></p>
            <p>Or copy and paste this link into your browser:</p>
            <p style="word-break: break-all; color: #2563eb;">{reset_url}</p>
            <p><strong>This link expires in 1 hour.</strong></p>
            <p>If you didn't request this, you can safely ignore this email.</p>
            <div class="footer">
                <p>&copy; 2026 PermitGrab. All rights reserved.</p>
            </div>
        </div>
    </body>
    </html>
    """

    try:
        from email_alerts import send_email
        send_email(email, "Reset Your PermitGrab Password", html_body)
    except Exception as e:
        print(f"Failed to send password reset email: {e}")
        # Still return success to prevent email enumeration

    return jsonify({'success': True, 'message': 'If that email exists, a reset link has been sent.'})


@app.route('/reset-password/<token>')
def reset_password_page(token):
    """Render the Reset Password page."""
    # Validate token
    cleanup_expired_tokens()
    tokens = load_reset_tokens()

    if token not in tokens:
        return render_template('reset_password.html', error='Invalid or expired reset link. Please request a new one.', token=None)

    token_data = tokens[token]
    if token_data.get('used'):
        return render_template('reset_password.html', error='This reset link has already been used.', token=None)

    now = datetime.now().isoformat()
    if token_data.get('expires', '') < now:
        return render_template('reset_password.html', error='This reset link has expired. Please request a new one.', token=None)

    footer_cities = get_cities_with_data()
    return render_template('reset_password.html', token=token, error=None, footer_cities=footer_cities)


@app.route('/api/reset-password', methods=['POST'])
@limiter.limit("10 per minute")
def api_reset_password():
    """
    POST /api/reset-password - Reset password with valid token.
    Body: { token: string, password: string }
    """
    data = request.get_json()
    if not data or not data.get('token') or not data.get('password'):
        return jsonify({'error': 'Token and password are required'}), 400

    token = data['token']
    new_password = data['password']

    # Validate password length
    if len(new_password) < 8:
        return jsonify({'error': 'Password must be at least 8 characters'}), 400

    # Validate token
    cleanup_expired_tokens()
    tokens = load_reset_tokens()

    if token not in tokens:
        return jsonify({'error': 'Invalid or expired reset link'}), 400

    token_data = tokens[token]
    if token_data.get('used'):
        return jsonify({'error': 'This reset link has already been used'}), 400

    now = datetime.now().isoformat()
    if token_data.get('expires', '') < now:
        return jsonify({'error': 'This reset link has expired'}), 400

    email = token_data['email']

    # Update user password (V7: direct database update)
    user = find_user_by_email(email)
    if not user:
        return jsonify({'error': 'User not found'}), 400

    user.password_hash = generate_password_hash(new_password)
    db.session.commit()

    # Mark token as used
    tokens[token]['used'] = True
    save_reset_tokens(tokens)

    return jsonify({'success': True, 'message': 'Password has been reset. You can now log in.'})


@app.route('/get-alerts')
def get_alerts_page():
    """Render the Get Alerts page."""
    cities = get_cities_with_data()  # Only show cities with data
    footer_cities = cities
    return render_template('get_alerts.html', cities=cities, footer_cities=footer_cities)


@app.route('/privacy')
def privacy_page():
    """Render the Privacy Policy page."""
    footer_cities = get_cities_with_data()
    return render_template('privacy.html', footer_cities=footer_cities)


@app.route('/terms')
def terms_page():
    """Render the Terms of Service page."""
    footer_cities = get_cities_with_data()
    return render_template('terms.html', footer_cities=footer_cities)


@app.route('/about')
def about_page():
    """Render the About page. V13.6: Pass city_count for consistency."""
    footer_cities = get_cities_with_data()
    city_count = get_total_city_count_auto()
    return render_template('about.html', footer_cities=footer_cities, city_count=city_count)


@app.route('/stats')
def stats_page():
    """V12.51: Render building permit statistics page (SQL-backed)."""
    conn = permitdb.get_connection()
    footer_cities = get_cities_with_data()

    # Get totals from SQLite
    stats = permitdb.get_permit_stats()
    total_permits = stats['total_permits']
    total_value = stats['total_value']
    high_value_count = stats['high_value_count']

    # Top cities by permit count
    top_cities_rows = conn.execute("""
        SELECT city, state, COUNT(*) as permit_count, SUM(COALESCE(estimated_cost, 0)) as total_value
        FROM permits WHERE city IS NOT NULL AND city != ''
        GROUP BY city, state ORDER BY permit_count DESC LIMIT 10
    """).fetchall()
    top_cities = []
    for row in top_cities_rows:
        top_cities.append({
            'name': row['city'],
            'state': row['state'] or '',
            'slug': row['city'].lower().replace(' ', '-'),
            'permit_count': row['permit_count'],
            'total_value': row['total_value'] or 0,
            'avg_value': (row['total_value'] or 0) / row['permit_count'] if row['permit_count'] > 0 else 0
        })

    # Trade breakdown
    trade_rows = conn.execute("""
        SELECT trade_category, COUNT(*) as cnt FROM permits
        WHERE trade_category IS NOT NULL AND trade_category != ''
        GROUP BY trade_category ORDER BY cnt DESC
    """).fetchall()
    trade_breakdown = [
        {'name': row['trade_category'], 'count': row['cnt'],
         'percentage': (row['cnt'] / total_permits * 100) if total_permits > 0 else 0}
        for row in trade_rows
    ]

    return render_template('stats.html',
                           total_permits=total_permits,
                           total_value=total_value,
                           high_value_count=high_value_count,
                           city_count=get_total_city_count_auto(),
                           top_cities=top_cities,
                           trade_breakdown=trade_breakdown,
                           last_updated=datetime.now().strftime('%Y-%m-%d'),
                           footer_cities=footer_cities)


@app.route('/map')
def map_page():
    """V12.26: Interactive permit heat map with Leaflet.js."""
    user = get_current_user()
    is_pro = user and user.plan == 'pro'
    cities = get_all_cities_info()
    footer_cities = get_cities_with_data()
    city_count = get_total_city_count_auto()  # V13.9: Pass for dynamic meta desc
    return render_template('map.html',
                           is_pro=is_pro,
                           cities=cities,
                           footer_cities=footer_cities,
                           city_count=city_count)


@app.route('/contact')
def contact_page():
    """Render the Contact page."""
    footer_cities = get_cities_with_data()
    return render_template('contact.html', footer_cities=footer_cities)


@app.route('/api/contact', methods=['POST'])
def api_contact():
    """Handle contact form submissions."""
    data = request.get_json()
    if not data or not data.get('email') or not data.get('message'):
        return jsonify({'error': 'Email and message required'}), 400

    # Store contact message (in production, would email this)
    contact_file = os.path.join(DATA_DIR, 'contact_messages.json')
    messages = []
    if os.path.exists(contact_file):
        with open(contact_file) as f:
            messages = json.load(f)

    messages.append({
        'name': data.get('name', ''),
        'email': data['email'],
        'subject': data.get('subject', 'general'),
        'message': data['message'],
        'timestamp': datetime.now().isoformat()
    })

    with open(contact_file, 'w') as f:
        json.dump(messages, f, indent=2)

    return jsonify({'success': True})


@app.route('/onboarding')
def onboarding_page():
    """Render the post-signup onboarding flow."""
    # Require login
    user = get_current_user()
    if not user:
        return redirect('/signup')
    # V9 Fix 10: Only show cities with actual permit data (not all 300+ cities)
    cities = get_cities_with_data()
    trades = get_all_trades()
    return render_template('onboarding.html', cities=cities, trades=trades)


@app.route('/api/onboarding', methods=['POST'])
def api_onboarding():
    """Save user onboarding preferences (city, trade, alerts)."""
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Not logged in'}), 401

    data = request.get_json()
    if not data:
        return jsonify({'error': 'Invalid request'}), 400

    city = data.get('city', '')
    trade = data.get('trade', '')
    daily_alerts = data.get('daily_alerts', False)

    # Update user preferences (V7: direct database update)
    user_obj = find_user_by_email(user['email'])
    if user_obj:
        user_obj.city = city
        user_obj.trade = trade
        user_obj.daily_alerts = daily_alerts
        user_obj.onboarding_completed = True

        # V12.53: Update digest settings in User model instead of subscribers.json
        if daily_alerts and city:
            cities = json.loads(user_obj.digest_cities or '[]')
            if city not in cities:
                cities.append(city)
            user_obj.digest_cities = json.dumps(cities)
            user_obj.digest_active = True
        db.session.commit()

    # Track onboarding complete event
    analytics.track_event('onboarding_complete', event_data={
        'city': city,
        'trade': trade,
        'daily_alerts': daily_alerts
    }, city_filter=city, trade_filter=trade)

    return jsonify({'success': True})


@app.route('/register')
def register_redirect():
    """Redirect /register to /signup."""
    return redirect('/signup', code=301)


# ===========================
# TREND ANALYTICS API
# ===========================

@app.route('/api/analytics/volume')
def api_analytics_volume():
    """
    GET /api/analytics/volume
    Query params: city, weeks (default 12)
    Returns weekly permit counts for trend analysis.
    V12.51: Uses SQLite for efficient aggregation.
    """
    city = request.args.get('city', '')
    weeks = int(request.args.get('weeks', 12))

    conn = permitdb.get_connection()
    now = datetime.now()
    cutoff = (now - timedelta(weeks=weeks)).strftime('%Y-%m-%d')

    # Build query with optional city filter
    if city:
        cursor = conn.execute("""
            SELECT filing_date, COUNT(*) as cnt
            FROM permits
            WHERE city = ? AND filing_date >= ? AND filing_date IS NOT NULL
            GROUP BY filing_date
        """, (city, cutoff))
    else:
        cursor = conn.execute("""
            SELECT filing_date, COUNT(*) as cnt
            FROM permits
            WHERE filing_date >= ? AND filing_date IS NOT NULL
            GROUP BY filing_date
        """, (cutoff,))

    # Aggregate by week
    weekly_counts = {}
    for i in range(weeks):
        week_start = now - timedelta(weeks=i+1)
        week_key = week_start.strftime('%Y-%m-%d')
        weekly_counts[week_key] = 0

    for row in cursor:
        filing_date = row['filing_date']
        if not filing_date:
            continue
        try:
            filed = datetime.strptime(filing_date[:10], '%Y-%m-%d')
            weeks_ago = (now - filed).days // 7
            if 0 <= weeks_ago < weeks:
                week_start = now - timedelta(weeks=weeks_ago+1)
                week_key = week_start.strftime('%Y-%m-%d')
                if week_key in weekly_counts:
                    weekly_counts[week_key] += row['cnt']
        except (ValueError, TypeError):
            continue

    # Convert to sorted list
    volume_data = sorted(weekly_counts.items())

    # Calculate trend
    if len(volume_data) >= 2:
        recent_avg = sum(v for _, v in volume_data[-4:]) / min(4, len(volume_data))
        older_avg = sum(v for _, v in volume_data[:4]) / min(4, len(volume_data))
        if older_avg > 0:
            trend_pct = ((recent_avg - older_avg) / older_avg) * 100
        else:
            trend_pct = 0
        trend_direction = 'up' if trend_pct > 0 else 'down' if trend_pct < 0 else 'flat'
    else:
        trend_pct = 0
        trend_direction = 'flat'

    return jsonify({
        'volume': [{'week': k, 'count': v} for k, v in volume_data],
        'total': sum(v for _, v in volume_data),
        'trend_percentage': round(trend_pct, 1),
        'trend_direction': trend_direction,
        'city': city or 'All Cities',
    })


@app.route('/api/analytics/trades')
def api_analytics_trades():
    """
    GET /api/analytics/trades
    Query params: city
    Returns trade breakdown for the selected city.
    V12.51: Uses SQLite for efficient aggregation.
    """
    city = request.args.get('city', '')
    conn = permitdb.get_connection()

    if city:
        cursor = conn.execute("""
            SELECT COALESCE(trade_category, 'Other') as trade, COUNT(*) as cnt
            FROM permits
            WHERE city = ?
            GROUP BY trade_category
            ORDER BY cnt DESC
        """, (city,))
        total_row = conn.execute("SELECT COUNT(*) FROM permits WHERE city = ?", (city,)).fetchone()
    else:
        cursor = conn.execute("""
            SELECT COALESCE(trade_category, 'Other') as trade, COUNT(*) as cnt
            FROM permits
            GROUP BY trade_category
            ORDER BY cnt DESC
        """)
        total_row = conn.execute("SELECT COUNT(*) FROM permits").fetchone()

    trades = [{'trade': row['trade'] or 'Other', 'count': row['cnt']} for row in cursor]
    total = total_row[0] if total_row else 0

    return jsonify({
        'trades': trades,
        'total': total,
        'city': city or 'All Cities',
    })


@app.route('/api/analytics/values')
def api_analytics_values():
    """
    GET /api/analytics/values
    Query params: city, weeks (default 12)
    Returns weekly average project values.
    V12.51: Uses SQLite for efficient aggregation.
    """
    city = request.args.get('city', '')
    weeks = int(request.args.get('weeks', 12))

    conn = permitdb.get_connection()
    now = datetime.now()
    cutoff = (now - timedelta(weeks=weeks)).strftime('%Y-%m-%d')

    # Build query with optional city filter
    if city:
        cursor = conn.execute("""
            SELECT filing_date, SUM(estimated_cost) as total_value, COUNT(*) as cnt
            FROM permits
            WHERE city = ? AND filing_date >= ? AND filing_date IS NOT NULL
                  AND estimated_cost > 0
            GROUP BY filing_date
        """, (city, cutoff))
    else:
        cursor = conn.execute("""
            SELECT filing_date, SUM(estimated_cost) as total_value, COUNT(*) as cnt
            FROM permits
            WHERE filing_date >= ? AND filing_date IS NOT NULL
                  AND estimated_cost > 0
            GROUP BY filing_date
        """, (cutoff,))

    # Initialize week buckets
    weekly_values = {}
    weekly_counts = {}
    for i in range(weeks):
        week_start = now - timedelta(weeks=i+1)
        week_key = week_start.strftime('%Y-%m-%d')
        weekly_values[week_key] = 0
        weekly_counts[week_key] = 0

    # Aggregate by week
    for row in cursor:
        filing_date = row['filing_date']
        if not filing_date:
            continue
        try:
            filed = datetime.strptime(filing_date[:10], '%Y-%m-%d')
            weeks_ago = (now - filed).days // 7
            if 0 <= weeks_ago < weeks:
                week_start = now - timedelta(weeks=weeks_ago+1)
                week_key = week_start.strftime('%Y-%m-%d')
                if week_key in weekly_values:
                    weekly_values[week_key] += row['total_value'] or 0
                    weekly_counts[week_key] += row['cnt']
        except (ValueError, TypeError):
            continue

    # Calculate averages
    value_data = []
    for week_key in sorted(weekly_values.keys()):
        count = weekly_counts[week_key]
        avg = weekly_values[week_key] / count if count > 0 else 0
        value_data.append({'week': week_key, 'average_value': round(avg, 2), 'count': count})

    # Calculate trend
    recent_values = [d['average_value'] for d in value_data[-4:] if d['average_value'] > 0]
    older_values = [d['average_value'] for d in value_data[:4] if d['average_value'] > 0]

    if recent_values and older_values:
        recent_avg = sum(recent_values) / len(recent_values)
        older_avg = sum(older_values) / len(older_values)
        if older_avg > 0:
            trend_pct = ((recent_avg - older_avg) / older_avg) * 100
        else:
            trend_pct = 0
        trend_direction = 'up' if trend_pct > 0 else 'down' if trend_pct < 0 else 'flat'
    else:
        trend_pct = 0
        trend_direction = 'flat'

    return jsonify({
        'values': value_data,
        'trend_percentage': round(trend_pct, 1),
        'trend_direction': trend_direction,
        'city': city or 'All Cities',
    })


@app.route('/analytics')
def analytics_page():
    """Render the Analytics page (Pro users only)."""
    user = get_current_user()

    # Check if user has Pro plan using centralized utility
    if not is_pro(user):
        footer_cities = get_cities_with_data()
        return render_template('upgrade_gate.html',
            title="Analytics",
            icon="📊",
            heading="Analytics is a Pro Feature",
            description="Upgrade to Professional to access trend analytics, market insights, and contractor intelligence.",
            footer_cities=footer_cities
        )

    footer_cities = get_cities_with_data()
    return render_template('analytics.html', user=user, footer_cities=footer_cities)


# ===========================
# PRE-CONSTRUCTION SIGNALS API
# ===========================

SIGNAL_TYPES = {
    "zoning_application": {"label": "Zoning Application", "color": "purple"},
    "planning_approval": {"label": "Planning Approval", "color": "blue"},
    "variance_request": {"label": "Variance Request", "color": "orange"},
    "demolition_filing": {"label": "Demolition Filing", "color": "red"},
    "new_building_filing": {"label": "New Building Filing", "color": "green"},
    "land_use_review": {"label": "Land Use Review", "color": "purple"},
}


def calculate_lead_potential(signal):
    """Calculate lead potential for a signal."""
    estimated_value = signal.get('estimated_value') or 0
    signal_type = signal.get('signal_type', '')

    if estimated_value >= 500000 or signal_type == 'new_building_filing':
        return 'high'
    elif signal_type in ('zoning_application', 'planning_approval', 'land_use_review'):
        return 'medium'
    else:
        return 'low'


@app.route('/api/signals')
def api_signals():
    """
    GET /api/signals
    Query params: city, type, status, page, per_page
    Returns pre-construction signals.
    """
    signals = load_signals()

    city = request.args.get('city', '')
    signal_type = request.args.get('type', '')
    status = request.args.get('status', '')

    if city:
        signals = [s for s in signals if s.get('city') == city]
    if signal_type:
        signals = [s for s in signals if s.get('signal_type') == signal_type]
    if status:
        signals = [s for s in signals if s.get('status') == status]

    # Add lead potential
    for s in signals:
        s['lead_potential'] = calculate_lead_potential(s)
        s['has_permit'] = len(s.get('linked_permits', [])) > 0

    # Sort by date_filed desc
    signals.sort(key=lambda x: x.get('date_filed', '') or '', reverse=True)

    # Pagination
    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 50))
    total = len(signals)
    start = (page - 1) * per_page
    page_signals = signals[start:start + per_page]

    # Get available cities and types for filters
    all_signals = load_signals()
    cities = sorted(set(s.get('city', '') for s in all_signals if s.get('city')))
    types = sorted(set(s.get('signal_type', '') for s in all_signals if s.get('signal_type')))

    return jsonify({
        'signals': page_signals,
        'total': total,
        'page': page,
        'per_page': per_page,
        'total_pages': (total + per_page - 1) // per_page,
        'cities': cities,
        'types': types,
    })


@app.route('/api/signals/<signal_id>')
def api_signal_detail(signal_id):
    """
    GET /api/signals/<signal_id>
    Returns a single signal with linked permits.
    V12.51: Uses SQLite for permit lookups.
    """
    signals = load_signals()
    signal = next((s for s in signals if s.get('signal_id') == signal_id), None)

    if not signal:
        return jsonify({'error': 'Signal not found'}), 404

    # Add lead potential
    signal['lead_potential'] = calculate_lead_potential(signal)

    # Load linked permits from SQLite
    linked_permits = []
    if signal.get('linked_permits'):
        conn = permitdb.get_connection()
        permit_numbers = signal['linked_permits']
        placeholders = ','.join('?' * len(permit_numbers))
        cursor = conn.execute(
            f"SELECT * FROM permits WHERE permit_number IN ({placeholders})",
            permit_numbers
        )
        linked_permits = [dict(row) for row in cursor]
        linked_permits = add_lead_scores(linked_permits)

    return jsonify({
        'signal': signal,
        'linked_permits': linked_permits,
    })


@app.route('/api/signals/stats')
def api_signal_stats():
    """
    GET /api/signals/stats
    Query params: city
    Returns signal counts by type and status.
    """
    signals = load_signals()

    city = request.args.get('city', '')
    if city:
        signals = [s for s in signals if s.get('city') == city]

    type_counts = {}
    status_counts = {'pending': 0, 'approved': 0, 'denied': 0, 'withdrawn': 0}
    lead_potential_counts = {'high': 0, 'medium': 0, 'low': 0}
    linked_count = 0

    for s in signals:
        signal_type = s.get('signal_type', 'unknown')
        type_counts[signal_type] = type_counts.get(signal_type, 0) + 1

        status = s.get('status', 'pending')
        if status in status_counts:
            status_counts[status] += 1

        potential = calculate_lead_potential(s)
        lead_potential_counts[potential] += 1

        if s.get('linked_permits'):
            linked_count += 1

    return jsonify({
        'total': len(signals),
        'type_breakdown': type_counts,
        'status_breakdown': status_counts,
        'lead_potential_breakdown': lead_potential_counts,
        'linked_to_permits': linked_count,
        'unlinked': len(signals) - linked_count,
        'city': city or 'All Cities',
    })


@app.route('/api/address-intel/<path:address>')
def api_address_intel(address):
    """
    GET /api/address-intel/<address>
    Returns ALL intelligence for an address: permits, signals, violations, history.
    V12.51: Uses SQLite for permits and history lookups.
    """
    normalized = normalize_address_for_lookup(address)

    if not normalized:
        return jsonify({'error': 'Address required'}), 400

    conn = permitdb.get_connection()

    # Find matching permits from SQLite (LIKE search on address)
    cursor = conn.execute(
        "SELECT * FROM permits WHERE LOWER(address) LIKE ?",
        (f"%{normalized}%",)
    )
    matching_permits = [dict(row) for row in cursor]
    matching_permits = add_lead_scores(matching_permits)

    # Signals and violations still use JSON (not in SQLite)
    signals = load_signals()
    violations = load_violations()

    # Find matching signals
    matching_signals = []
    for s in signals:
        s_addr = s.get('address_normalized', '')
        if normalized in s_addr or s_addr in normalized:
            s['lead_potential'] = calculate_lead_potential(s)
            matching_signals.append(s)

    # Find matching violations
    matching_violations = []
    for v in violations:
        v_addr = normalize_address_for_lookup(v.get('address', ''))
        if normalized in v_addr or v_addr in normalized:
            matching_violations.append(v)

    # Find permit history from SQLite
    history_permits = permitdb.get_address_history(normalized)
    history_entry = {}
    if history_permits:
        history_entry = {
            'address': history_permits[0].get('address'),
            'city': history_permits[0].get('city'),
            'state': history_permits[0].get('state'),
            'permits': history_permits,
            'permit_count': len(history_permits),
        }

    return jsonify({
        'address': address,
        'address_normalized': normalized,
        'permits': matching_permits,
        'permit_count': len(matching_permits),
        'signals': matching_signals,
        'signal_count': len(matching_signals),
        'violations': matching_violations,
        'violation_count': len(matching_violations),
        'has_active_violations': any(v.get('status', '').lower() in ('open', 'active', 'pending') for v in matching_violations),
        'history': history_entry,
        'historical_permit_count': history_entry.get('permit_count', 0),
        'is_repeat_renovator': history_entry.get('permit_count', 0) >= 3,
    })


@app.route('/early-intel')
def early_intel_page():
    """Render the Early Intel page (Pro users only)."""
    user = get_current_user()

    # Check if user has Pro plan using centralized utility
    if not is_pro(user):
        footer_cities = get_cities_with_data()
        return render_template('upgrade_gate.html',
            title="Early Intel",
            icon="🔮",
            heading="Early Intel is a Pro Feature",
            description="Upgrade to Professional to access pre-construction signals, zoning applications, and early-stage filings before permits are issued.",
            footer_cities=footer_cities
        )

    footer_cities = get_cities_with_data()
    return render_template('early_intel.html', user=user, footer_cities=footer_cities)


# ===========================
# STRIPE PAYMENT ENDPOINTS
# ===========================

# Stripe configuration from environment variables
STRIPE_SECRET_KEY = os.environ.get('STRIPE_SECRET_KEY', '')
STRIPE_PRICE_ID = os.environ.get('STRIPE_PRICE_ID', '')  # Monthly price ID ($149/mo)
# REPLACE WITH ANNUAL STRIPE PRICE ID - Create a new price in Stripe for $1,548/year ($129/mo)
STRIPE_ANNUAL_PRICE_ID = os.environ.get('STRIPE_ANNUAL_PRICE_ID', '')
STRIPE_WEBHOOK_SECRET = os.environ.get('STRIPE_WEBHOOK_SECRET', '')
SITE_URL = os.environ.get('SITE_URL', 'http://localhost:5000')

@app.route('/api/create-checkout-session', methods=['POST'])
def create_checkout_session():
    """Create a Stripe Checkout Session for the requested plan.

    V211-2: If Stripe env vars aren't configured yet, return a graceful
    200 with a `fallback` mailto URL so the JS on /pricing can redirect
    the visitor to an email signup instead of alerting 'Stripe not
    configured'. Same behaviour when a plan's price id is missing.

    Plan-aware: accepts {plan: 'starter'|'pro'|'enterprise'}, looks up
    STRIPE_PRICE_STARTER / STRIPE_PRICE_PRO / STRIPE_PRICE_ENTERPRISE
    env vars first, falls back to the V12-era STRIPE_PRICE_ID + billing
    period for the existing Professional plan.
    """
    data = request.get_json() or {}
    plan = (data.get('plan') or '').strip().lower()
    customer_email = data.get('email')
    billing_period = data.get('billing_period', 'monthly')

    # V211-2: Graceful fallback — no Stripe keys means 'payments launching
    # soon' not 500 error. Let the pricing page JS direct to mailto.
    mailto_fallback = (
        f"mailto:wcrainshaw@gmail.com?subject=PermitGrab+"
        f"{plan.title() or 'Professional'}+Signup"
    )
    if not STRIPE_SECRET_KEY:
        return jsonify({
            'error': 'Payments launching soon!',
            'fallback': mailto_fallback,
        }), 200

    # Plan-aware price lookup (new path, V211-2)
    per_plan_price = {
        'starter': os.environ.get('STRIPE_PRICE_STARTER', ''),
        'pro': os.environ.get('STRIPE_PRICE_PRO', ''),
        'enterprise': os.environ.get('STRIPE_PRICE_ENTERPRISE', ''),
    }
    price_id = per_plan_price.get(plan)
    plan_name = plan + '_monthly' if plan else None

    # Legacy path: if no per-plan env var, use V12-era single-price flow
    if not price_id:
        if billing_period == 'annual' and STRIPE_ANNUAL_PRICE_ID:
            price_id = STRIPE_ANNUAL_PRICE_ID
            plan_name = 'professional_annual'
        elif STRIPE_PRICE_ID:
            price_id = STRIPE_PRICE_ID
            plan_name = 'professional_monthly'

    if not price_id:
        return jsonify({
            'error': 'Plan not fully configured yet',
            'fallback': mailto_fallback,
        }), 200

    stripe.api_key = STRIPE_SECRET_KEY

    analytics.track_event('checkout_started', event_data={
        'plan': plan_name,
        'billing': billing_period
    })

    try:
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price': price_id,
                'quantity': 1,
            }],
            mode='subscription',
            # V218 T5D: route to the dedicated /success page so customers
            # land on a real confirmation with "what happens next" steps
            # instead of the generic homepage.
            success_url=f'{SITE_URL}/success?session_id={{CHECKOUT_SESSION_ID}}',
            cancel_url=f'{SITE_URL}/pricing?payment=cancelled',
            customer_email=customer_email,
            metadata={
                'plan': plan_name,
                'billing_period': billing_period,
            },
            allow_promotion_codes=True,
        )
        return jsonify({'url': checkout_session.url})
    except stripe.error.StripeError as e:
        return jsonify({'error': str(e), 'fallback': mailto_fallback}), 400


@app.route('/success', methods=['GET'])
def checkout_success():
    """V218 T5D: Dedicated post-payment landing page. Stripe success_url
    redirects here with ?session_id=cs_xxx. We don't verify the session
    server-side (the webhook handler does that for real subscription
    state) — this page is purely the customer-facing confirmation."""
    return render_template('success.html')


@app.route('/api/stripe-webhook', methods=['POST'])
def stripe_webhook():
    """Handle Stripe webhook events."""
    payload = request.get_data(as_text=True)
    sig_header = request.headers.get('Stripe-Signature')

    if not STRIPE_SECRET_KEY:
        return jsonify({'error': 'Stripe not configured'}), 500

    stripe.api_key = STRIPE_SECRET_KEY

    try:
        if STRIPE_WEBHOOK_SECRET:
            event = stripe.Webhook.construct_event(
                payload, sig_header, STRIPE_WEBHOOK_SECRET
            )
        else:
            # For testing without webhook signature verification
            event = json.loads(payload)
    except ValueError:
        return jsonify({'error': 'Invalid payload'}), 400
    except stripe.error.SignatureVerificationError:
        return jsonify({'error': 'Invalid signature'}), 400

    event_type = event['type']
    event_id = event.get('id') or ''
    print(f"[Stripe] Received event: {event_type} ({event_id})")

    # V218 T5C: webhook idempotency. Stripe retries on delivery failure,
    # and the current handler would re-fire payment_success emails each
    # time. Track event IDs we've already processed and no-op on repeat.
    if event_id:
        try:
            # V229 addendum J2: table now lives in db.py init_database().
            # Previously the CREATE TABLE ran on every webhook event.
            _wh_conn = permitdb.get_connection()
            already = _wh_conn.execute(
                "SELECT 1 FROM stripe_webhook_events WHERE event_id = ?",
                (event_id,),
            ).fetchone()
            if already:
                print(f"[Stripe] event {event_id} already processed — skipping")
                return jsonify({'status': 'duplicate', 'event_id': event_id})
            _wh_conn.execute(
                "INSERT OR IGNORE INTO stripe_webhook_events (event_id, event_type) VALUES (?, ?)",
                (event_id, event_type),
            )
            _wh_conn.commit()
        except Exception as _e:
            # Don't fail the webhook over idempotency bookkeeping — better
            # to risk a duplicate email than 500 and let Stripe retry.
            print(f"[Stripe] idempotency check skipped (non-fatal): {_e}")

    # V12.53: Handle all subscription lifecycle events
    if event_type == 'checkout.session.completed':
        # New subscription or upgrade
        session_obj = event['data']['object']
        customer_email = session_obj.get('customer_email') or session_obj.get('customer_details', {}).get('email')
        plan = session_obj.get('metadata', {}).get('plan', 'professional')

        if customer_email:
            user = find_user_by_email(customer_email)
            if user:
                user.plan = 'pro'
                user.stripe_customer_id = session_obj.get('customer')
                user.subscription_id = session_obj.get('subscription')
                # Clear trial fields since they're now a paying customer
                user.trial_end_date = None
                user.trial_started_at = None
                db.session.commit()
                print(f"[Stripe] User {customer_email} upgraded to {plan}")

                # V12.53: Send payment success email
                try:
                    from email_alerts import send_payment_success
                    send_payment_success(user, plan)
                except Exception as e:
                    print(f"[Stripe] Payment success email failed: {e}")

            # Track payment success event
            analytics.track_event('payment_success', event_data={
                'plan': plan,
                'stripe_customer_id': session_obj.get('customer')
            }, user_id_override=customer_email)

    elif event_type == 'invoice.payment_failed':
        # Payment failed
        invoice = event['data']['object']
        customer_email = invoice.get('customer_email')

        if customer_email:
            user = find_user_by_email(customer_email)
            if user:
                print(f"[Stripe] Payment failed for {customer_email}")
                try:
                    from email_alerts import send_payment_failed
                    send_payment_failed(user)
                except Exception as e:
                    print(f"[Stripe] Payment failed email failed: {e}")

    elif event_type == 'invoice.payment_succeeded':
        # Renewal payment succeeded
        invoice = event['data']['object']
        customer_email = invoice.get('customer_email')
        # Only send renewal email if this is not the first payment
        billing_reason = invoice.get('billing_reason')

        if customer_email and billing_reason == 'subscription_cycle':
            user = find_user_by_email(customer_email)
            if user:
                print(f"[Stripe] Subscription renewed for {customer_email}")
                try:
                    from email_alerts import send_subscription_renewed
                    send_subscription_renewed(user)
                except Exception as e:
                    print(f"[Stripe] Renewal email failed: {e}")

    elif event_type == 'customer.subscription.deleted':
        # Subscription cancelled
        subscription = event['data']['object']
        customer_id = subscription.get('customer')

        # Find user by stripe_customer_id
        user = User.query.filter_by(stripe_customer_id=customer_id).first()
        if user:
            user.plan = 'free'
            db.session.commit()
            print(f"[Stripe] Subscription cancelled for {user.email}")
            try:
                from email_alerts import send_subscription_cancelled
                send_subscription_cancelled(user)
            except Exception as e:
                print(f"[Stripe] Cancellation email failed: {e}")

    return jsonify({'status': 'success'})


@app.route('/api/webhooks/sendgrid', methods=['POST'])
def sendgrid_webhook():
    """
    Handle SendGrid Event Webhooks for email engagement tracking.
    NOTE: Configure this URL in SendGrid dashboard > Settings > Mail Settings > Event Webhook
    URL: https://permitgrab.com/api/webhooks/sendgrid
    Enable events: Delivered, Opened, Clicked, Bounced, Unsubscribed, Spam Report
    """
    try:
        events = request.get_json()
        if not events or not isinstance(events, list):
            return '', 200

        for event in events:
            sg_type = event.get('event')  # 'delivered', 'open', 'click', 'bounce', etc.
            email = event.get('email', '')

            if not sg_type:
                continue

            # Find user by email (if exists) for user_id
            user_id = None
            if email:
                users = load_users()
                user = next((u for u in users if u.get('email', '').lower() == email.lower()), None)
                if user:
                    user_id = user.get('email')

            # Track the email event
            analytics.track_event(
                event_type=f'email_{sg_type}',  # email_delivered, email_open, email_click, etc.
                event_data={
                    'email': email,
                    'subject': event.get('subject', ''),
                    'url': event.get('url', ''),  # For click events
                    'sg_event_id': event.get('sg_event_id', ''),
                    'sg_message_id': event.get('sg_message_id', ''),
                    'category': event.get('category', []),
                    'reason': event.get('reason', ''),  # For bounce/drop events
                },
                user_id_override=user_id
            )

    except Exception as e:
        print(f"[SendGrid Webhook] Error processing events: {e}")

    return '', 200


# ===========================
# USER AUTHENTICATION
# ===========================

@app.route('/api/register', methods=['POST'])
@limiter.limit("10 per hour")
def api_register():
    """POST /api/register - Register a new user.

    V7: Uses PostgreSQL database with UNIQUE constraint on email.
    Database constraint prevents duplicates even under race conditions.
    """
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Invalid request'}), 400

    email = data.get('email', '').strip().lower()
    password = data.get('password', '')
    name = data.get('name', '').strip()

    if not email or not password:
        return jsonify({'error': 'Email and password required'}), 400

    if len(password) < 8:
        return jsonify({'error': 'Password must be at least 8 characters'}), 400

    # Validate email format (basic check)
    if '@' not in email or '.' not in email.split('@')[-1]:
        return jsonify({'error': 'Please enter a valid email address'}), 400

    # Check for existing account BEFORE creating user
    existing = find_user_by_email(email)
    if existing:
        print(f"[Register] DUPLICATE BLOCKED: {email}")
        return jsonify({'error': 'An account with this email already exists. Please log in instead.'}), 409

    # Create new user in database
    try:
        import secrets
        plan = data.get('plan', 'free')
        is_trial = plan == 'pro_trial'

        new_user = User(
            email=email,
            name=name,
            password_hash=generate_password_hash(password),
            plan='pro_trial' if is_trial else 'free',
            # V12.53: Email system fields
            unsubscribe_token=secrets.token_urlsafe(32),
            digest_active=True,
            trial_started_at=datetime.utcnow() if is_trial else None,
            trial_end_date=(datetime.utcnow() + timedelta(days=14)) if is_trial else None,
        )
        db.session.add(new_user)
        db.session.commit()
        print(f"[Register] User created in database: {email} (plan: {new_user.plan})")
    except IntegrityError:
        # Database UNIQUE constraint caught a race condition
        db.session.rollback()
        print(f"[Register] DUPLICATE BLOCKED (IntegrityError): {email}")
        return jsonify({'error': 'An account with this email already exists. Please log in instead.'}), 409

    # Log in the user
    session['user_email'] = email

    # Track signup event
    analytics.track_event('signup', event_data={'method': 'email', 'plan': new_user.plan})

    # V12.53: Send welcome email (async to not block registration)
    try:
        from email_alerts import send_welcome_free, send_welcome_pro_trial
        if new_user.plan == 'pro_trial':
            send_welcome_pro_trial(new_user)
            new_user.welcome_email_sent = True
            db.session.commit()
            print(f"[Register] Welcome Pro Trial email sent to {email}")
        else:
            send_welcome_free(new_user)
            new_user.welcome_email_sent = True
            db.session.commit()
            print(f"[Register] Welcome Free email sent to {email}")
    except Exception as e:
        print(f"[Register] Welcome email failed for {email}: {e}")

    # Return user without password hash
    return jsonify({
        'message': 'Registration successful',
        'user': {
            'email': new_user.email,
            'name': new_user.name,
            'plan': new_user.plan,
        }
    }), 201


@app.route('/api/login', methods=['POST'])
@limiter.limit("20 per minute")
def api_login():
    """POST /api/login - Log in a user (V7: uses PostgreSQL)."""
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Invalid request'}), 400

    email = data.get('email', '').strip().lower()
    password = data.get('password', '')

    if not email or not password:
        return jsonify({'error': 'Email and password required'}), 400

    # Find user in database
    user = find_user_by_email(email)

    if not user:
        print(f"[Login] No user found for email: {email}")
        return jsonify({'error': 'Invalid email or password'}), 401

    if not check_password_hash(user.password_hash, password):
        print(f"[Login] Invalid password for email: {email}")
        return jsonify({'error': 'Invalid email or password'}), 401

    # Log in the user
    session['user_email'] = email

    # V12.53: Update last_login_at timestamp
    user.last_login_at = datetime.utcnow()
    db.session.commit()

    # Track login event
    analytics.track_event('login')

    return jsonify({
        'message': 'Login successful',
        'user': {
            'email': user.email,
            'name': user.name,
            'plan': user.plan,
        }
    })


@app.route('/api/logout', methods=['POST'])
def api_logout():
    """POST /api/logout - Log out the current user."""
    session.pop('user_email', None)
    return jsonify({'message': 'Logged out'})


@app.route('/api/me')
def api_me():
    """GET /api/me - Get current logged-in user."""
    user = get_current_user()
    if not user:
        return jsonify({'user': None})

    # V9 Fix 8: Include daily_alerts and city for alert widget status
    return jsonify({
        'user': {
            'email': user['email'],
            'name': user['name'],
            'plan': user['plan'],
            'daily_alerts': user.get('daily_alerts', False),
            'city': user.get('city', ''),
            'trade': user.get('trade', ''),
        }
    })


# ===========================
# UNSUBSCRIBE
# ===========================

@app.route('/api/unsubscribe')
def api_unsubscribe():
    """GET /api/unsubscribe?token=xxx - Unsubscribe from email alerts."""
    token = request.args.get('token', '')

    if not token:
        return render_template_string('''
            <!DOCTYPE html>
            <html><head><title>Error</title></head>
            <body style="font-family: sans-serif; padding: 40px; text-align: center;">
                <h1>Invalid Link</h1>
                <p>This unsubscribe link is invalid or has expired.</p>
            </body></html>
        '''), 400

    # V12.53: Use User model instead of subscribers.json
    user = User.query.filter_by(unsubscribe_token=token).first()

    if not user:
        return render_template_string('''
            <!DOCTYPE html>
            <html><head><title>Error</title></head>
            <body style="font-family: sans-serif; padding: 40px; text-align: center;">
                <h1>Invalid Link</h1>
                <p>This unsubscribe link is invalid or has already been used.</p>
            </body></html>
        '''), 404

    # Mark digest as inactive
    user.digest_active = False
    db.session.commit()

    return render_template_string('''
        <!DOCTYPE html>
        <html><head><title>Unsubscribed</title></head>
        <body style="font-family: sans-serif; padding: 40px; text-align: center;">
            <h1>You've been unsubscribed</h1>
            <p>{{ email }} will no longer receive permit alerts.</p>
            <p style="margin-top: 20px; color: #666;">
                Changed your mind? <a href="/">Re-subscribe anytime</a>
            </p>
        </body></html>
    ''', email=user.email)


# ===========================
# ADMIN PAGE
# ===========================

@app.route('/admin/legacy')
def admin_page():
    """GET /admin/legacy - V12-era admin dashboard, password-gated. V229 A1:
    was @app.route('/admin') but silently shadowed V227's X-Admin-Key-gated
    HTML command center at /admin. Flask registered both, last-decorator-
    wins silently, so V227's dashboard was unreachable. Moved here; the
    before_request handler below still matches legacy path."""
    # Check for admin password in query param or session
    password = request.args.get('password', '')

    if password and ADMIN_PASSWORD and password == ADMIN_PASSWORD:
        session['admin_authenticated'] = True

    if not session.get('admin_authenticated'):
        if not ADMIN_PASSWORD:
            return render_template_string('''
                <!DOCTYPE html>
                <html><head><title>Admin</title></head>
                <body style="font-family: sans-serif; padding: 40px; text-align: center;">
                    <h1>Admin Not Configured</h1>
                    <p>Set the ADMIN_PASSWORD environment variable to enable admin access.</p>
                </body></html>
            '''), 500

        return render_template_string('''
            <!DOCTYPE html>
            <html><head><title>Admin Login</title></head>
            <body style="font-family: sans-serif; padding: 40px; max-width: 400px; margin: 0 auto;">
                <h1>Admin Login</h1>
                <form method="GET" action="/admin">
                    <input type="password" name="password" placeholder="Admin Password"
                           style="width: 100%; padding: 12px; margin-bottom: 12px; border: 1px solid #ccc; border-radius: 4px;">
                    <button type="submit" style="width: 100%; padding: 12px; background: #2563eb; color: white; border: none; border-radius: 4px; cursor: pointer;">
                        Login
                    </button>
                </form>
            </body></html>
        ''')

    # V12.51: Load data from SQLite for admin dashboard
    permit_stats = permitdb.get_permit_stats()
    # V12.53: Count digest subscribers from User model instead of subscribers.json
    digest_subscribers = User.query.filter(User.digest_active == True).all()
    stats = load_stats()

    # V11 Fix 2.1: Get real user stats from database
    all_users = User.query.all()
    pro_users = User.query.filter(User.plan.in_(['pro', 'professional', 'enterprise'])).all()
    alert_users = User.query.filter_by(daily_alerts=True).all()

    # Stats from SQLite
    city_count = permit_stats['city_count']

    # V12: Load collection diagnostic
    diag_path = os.path.join(DATA_DIR, 'collection_diagnostic.json')
    diagnostic = {}
    if os.path.exists(diag_path):
        try:
            with open(diag_path) as f:
                diagnostic = json.load(f, strict=False)
        except Exception:
            pass

    return render_template_string('''
        <!DOCTYPE html>
        <html>
        <head>
            <title>PermitGrab Admin</title>
            <style>
                * { margin: 0; padding: 0; box-sizing: border-box; }
                body { font-family: -apple-system, BlinkMacSystemFont, sans-serif; background: #f3f4f6; }
                .header { background: #111827; color: white; padding: 20px 32px; }
                .header h1 { font-size: 24px; }
                .container { max-width: 1200px; margin: 0 auto; padding: 24px; }
                .stats-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-bottom: 24px; }
                .stat-card { background: white; padding: 20px; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,.1); }
                .stat-card .value { font-size: 32px; font-weight: 700; color: #111827; }
                .stat-card .label { font-size: 14px; color: #6b7280; margin-top: 4px; }
                .section { background: white; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,.1); margin-bottom: 24px; }
                .section-header { padding: 16px 20px; border-bottom: 1px solid #e5e7eb; font-weight: 600; }
                .section-body { padding: 20px; }
                table { width: 100%; border-collapse: collapse; }
                th, td { padding: 12px; text-align: left; border-bottom: 1px solid #e5e7eb; }
                th { background: #f9fafb; font-weight: 600; font-size: 13px; color: #6b7280; }
                .badge { padding: 2px 8px; border-radius: 12px; font-size: 12px; font-weight: 500; }
                .badge-active { background: #dcfce7; color: #166534; }
                .badge-inactive { background: #fee2e2; color: #991b1b; }
                .badge-pro { background: #dbeafe; color: #1e40af; }
                .logout-link { color: rgba(255,255,255,.7); text-decoration: none; font-size: 14px; }
                .form-row { display: flex; gap: 12px; align-items: flex-end; }
                .form-group { display: flex; flex-direction: column; gap: 4px; }
                .form-group label { font-size: 13px; font-weight: 500; color: #374151; }
                .form-group input, .form-group select { padding: 8px 12px; border: 1px solid #d1d5db; border-radius: 6px; font-size: 14px; }
                .btn-upgrade { background: #2563eb; color: white; border: none; padding: 8px 20px; border-radius: 6px; font-weight: 500; cursor: pointer; }
                .btn-upgrade:hover { background: #1d4ed8; }
                .alert { padding: 12px 16px; border-radius: 6px; margin-bottom: 16px; font-size: 14px; }
                .alert-success { background: #dcfce7; color: #166534; }
                .alert-error { background: #fee2e2; color: #991b1b; }
            </style>
        </head>
        <body>
            <div class="header">
                <div style="display: flex; justify-content: space-between; align-items: center; max-width: 1200px; margin: 0 auto;">
                    <h1>PermitGrab Admin</h1>
                    <a href="/admin?logout=1" class="logout-link">Logout</a>
                </div>
            </div>
            <div class="container">
                <div class="stats-grid">
                    <div class="stat-card">
                        <div class="value">{{ total_permits }}</div>
                        <div class="label">Total Permits</div>
                    </div>
                    <div class="stat-card">
                        <div class="value">{{ city_count }}</div>
                        <div class="label">Active Cities</div>
                    </div>
                    <div class="stat-card">
                        <div class="value">{{ total_users }}</div>
                        <div class="label">Registered Users</div>
                    </div>
                    <div class="stat-card">
                        <div class="value">{{ pro_users }}</div>
                        <div class="label">Pro Users</div>
                    </div>
                </div>
                {% if last_updated %}
                <div style="text-align: center; margin-bottom: 16px; padding: 8px; background: #dbeafe; border-radius: 6px; font-size: 14px; color: #1e40af;">
                    Last data collection: {{ last_updated }}
                </div>
                {% endif %}

                {% if success_msg %}
                <div class="alert alert-success">{{ success_msg }}</div>
                {% endif %}
                {% if error_msg %}
                <div class="alert alert-error">{{ error_msg }}</div>
                {% endif %}

                <div class="section">
                    <div class="section-header">Upgrade User</div>
                    <div class="section-body">
                        <form method="POST" action="/admin/upgrade-user" class="form-row">
                            <div class="form-group">
                                <label for="email">Email</label>
                                <input type="email" id="email" name="email" placeholder="user@example.com" required style="width: 280px;">
                            </div>
                            <div class="form-group">
                                <label for="plan">Plan</label>
                                <select id="plan" name="plan" required>
                                    <option value="free">Free</option>
                                    <option value="pro">Pro</option>
                                    <option value="enterprise">Enterprise</option>
                                </select>
                            </div>
                            <button type="submit" class="btn-upgrade">Upgrade</button>
                        </form>
                    </div>
                </div>

                <div class="section">
                    <div class="section-header">Collection Status</div>
                    <div class="section-body">
                        <p><strong>Last Updated:</strong> {{ last_updated or 'Never' }}</p>
                        <p><strong>Total Users:</strong> {{ total_users }}</p>
                        {% if diagnostic %}
                        <hr style="margin: 16px 0; border: none; border-top: 1px solid #e5e7eb;">
                        <h4 style="margin-bottom: 12px; color: #374151;">Collection Diagnostic</h4>
                        <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin-bottom: 16px;">
                            <div style="background: #dcfce7; padding: 12px; border-radius: 6px; text-align: center;">
                                <div style="font-size: 24px; font-weight: 700; color: #16a34a;">{{ diagnostic.cities_with_permits }}</div>
                                <div style="font-size: 12px; color: #166534;">Cities With Permits</div>
                            </div>
                            <div style="background: #fef9c3; padding: 12px; border-radius: 6px; text-align: center;">
                                <div style="font-size: 24px; font-weight: 700; color: #ca8a04;">{{ diagnostic.cities_zero_permits }}</div>
                                <div style="font-size: 12px; color: #854d0e;">Zero Permits</div>
                            </div>
                            <div style="background: #fee2e2; padding: 12px; border-radius: 6px; text-align: center;">
                                <div style="font-size: 24px; font-weight: 700; color: #dc2626;">{{ diagnostic.cities_with_errors }}</div>
                                <div style="font-size: 12px; color: #991b1b;">Errors</div>
                            </div>
                        </div>
                        <p style="font-size: 13px; color: #6b7280;"><strong>Timeouts:</strong> {{ diagnostic.cities_timeout }} | <strong>Connection Errors:</strong> {{ diagnostic.cities_connection_error }}</p>
                        {% endif %}
                    </div>
                </div>

                <div class="section">
                    <div class="section-header">Subscribers ({{ total_subscribers }})</div>
                    <div class="section-body" style="overflow-x: auto;">
                        <table>
                            <thead>
                                <tr>
                                    <th>Email</th>
                                    <th>Name</th>
                                    <th>City</th>
                                    <th>Trade</th>
                                    <th>Plan</th>
                                    <th>Status</th>
                                    <th>Subscribed</th>
                                </tr>
                            </thead>
                            <tbody>
                                {% for sub in subscribers %}
                                <tr>
                                    <td>{{ sub.email }}</td>
                                    <td>{{ sub.name or '-' }}</td>
                                    <td>{{ sub.city or '-' }}</td>
                                    <td>{{ sub.trade or '-' }}</td>
                                    <td>
                                        {% if sub.plan in ['professional', 'enterprise'] %}
                                        <span class="badge badge-pro">{{ sub.plan }}</span>
                                        {% else %}
                                        {{ sub.plan or 'free' }}
                                        {% endif %}
                                    </td>
                                    <td>
                                        {% if sub.active != false %}
                                        <span class="badge badge-active">Active</span>
                                        {% else %}
                                        <span class="badge badge-inactive">Inactive</span>
                                        {% endif %}
                                    </td>
                                    <td>{{ sub.subscribed_at[:10] if sub.subscribed_at else '-' }}</td>
                                </tr>
                                {% endfor %}
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
        </body>
        </html>
    ''',
        total_permits=permit_stats['total_permits'],
        city_count=city_count,
        total_users=len(all_users),
        pro_users=len(pro_users),
        last_updated=stats.get('collected_at', ''),
        subscribers=digest_subscribers,
        total_subscribers=len(digest_subscribers),
        diagnostic=diagnostic,
        success_msg=request.args.get('success', ''),
        error_msg=request.args.get('error', ''),
    )


# Handle admin logout
@app.before_request
def check_admin_logout():
    if request.path in ('/admin', '/admin/legacy') and request.args.get('logout'):
        session.pop('admin_authenticated', None)


@app.route('/api/collection-status')
def api_collection_status():
    """GET /api/collection-status - Check data collection status (admin only).
    V12.51: Uses SQLite for permit stats.
    """
    if not session.get('admin_authenticated'):
        return jsonify({'error': 'Admin authentication required'}), 401

    stats = load_stats()
    permit_stats = permitdb.get_permit_stats()

    # Check data directory (some JSON files still exist for signals/violations)
    data_files = {}
    for filename in ['violations.json', 'signals.json', 'city_health.json']:
        filepath = os.path.join(DATA_DIR, filename)
        if os.path.exists(filepath):
            data_files[filename] = {
                'exists': True,
                'size_kb': round(os.path.getsize(filepath) / 1024, 1),
                'modified': datetime.fromtimestamp(os.path.getmtime(filepath)).isoformat(),
            }
        else:
            data_files[filename] = {'exists': False}

    # Add SQLite database info
    if os.path.exists(permitdb.DB_PATH):
        data_files['permitgrab.db'] = {
            'exists': True,
            'size_kb': round(os.path.getsize(permitdb.DB_PATH) / 1024, 1),
            'modified': datetime.fromtimestamp(os.path.getmtime(permitdb.DB_PATH)).isoformat(),
        }

    return jsonify({
        'data_dir': DATA_DIR,
        'total_permits': permit_stats['total_permits'],
        'unique_cities': permit_stats['city_count'],
        'last_collection': stats.get('collected_at', 'Never'),
        'city_stats': stats.get('city_stats', {}),
        'data_files': data_files,
        'collector_started': _collector_started,
    })


@app.route('/admin/trigger-collection', methods=['POST'])
def admin_trigger_collection():
    """V12.2: Manually trigger data collection (admin only)."""
    if not session.get('admin_authenticated'):
        return jsonify({"error": "Unauthorized"}), 403

    import threading
    from collector import collect_all

    # Run in background thread so it doesn't block
    # V12.38: Expanded from 60 to 180 days
    thread = threading.Thread(target=collect_all, kwargs={"days_back": 180}, daemon=True)
    thread.start()

    return jsonify({
        "status": "Collection triggered",
        "message": "Running in background. Check /api/stats in a few minutes."
    })


@app.route('/admin/collector-health')
def admin_collector_health():
    """V15: Collector health dashboard - shows status of all prod cities."""
    if not session.get('admin_authenticated'):
        return redirect('/admin?error=Please+log+in')

    # Get health data
    try:
        health_data = permitdb.get_city_health_status()
        summary = permitdb.get_daily_collection_summary()
        recent_runs = permitdb.get_recent_scraper_runs(limit=20)
    except Exception as e:
        health_data = []
        summary = None
        recent_runs = []
        print(f"[V15] Error loading collector health: {e}")

    # Count by status
    green_count = sum(1 for c in health_data if c.get('health_color') == 'GREEN')
    yellow_count = sum(1 for c in health_data if c.get('health_color') == 'YELLOW')
    red_count = sum(1 for c in health_data if c.get('health_color') == 'RED')

    html = f'''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Collector Health - PermitGrab Admin</title>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; margin: 20px; background: #1a1a2e; color: #e0e0e0; }}
            h1 {{ color: #00d4ff; }}
            h2 {{ color: #888; margin-top: 30px; }}
            table {{ border-collapse: collapse; width: 100%; margin-bottom: 30px; }}
            th, td {{ border: 1px solid #333; padding: 8px 12px; text-align: left; }}
            th {{ background: #252540; color: #00d4ff; }}
            tr:nth-child(even) {{ background: #1e1e35; }}
            .green {{ color: #00ff88; font-weight: bold; }}
            .yellow {{ color: #ffcc00; font-weight: bold; }}
            .red {{ color: #ff4444; font-weight: bold; }}
            .summary {{ display: flex; gap: 20px; margin-bottom: 30px; }}
            .summary-card {{ background: #252540; padding: 20px; border-radius: 8px; text-align: center; min-width: 120px; }}
            .summary-card .value {{ font-size: 32px; font-weight: bold; }}
            .summary-card .label {{ font-size: 14px; color: #888; }}
            a {{ color: #00d4ff; }}
            .back-link {{ margin-bottom: 20px; display: block; }}
        </style>
    </head>
    <body>
        <a href="/admin" class="back-link">&larr; Back to Admin</a>
        <h1>Collector Health Dashboard</h1>

        <div class="summary">
            <div class="summary-card">
                <div class="value green">{green_count}</div>
                <div class="label">Healthy (0-2 days)</div>
            </div>
            <div class="summary-card">
                <div class="value yellow">{yellow_count}</div>
                <div class="label">Warning (3-7 days)</div>
            </div>
            <div class="summary-card">
                <div class="value red">{red_count}</div>
                <div class="label">Critical (7+ days)</div>
            </div>
            <div class="summary-card">
                <div class="value">{len(health_data)}</div>
                <div class="label">Total Cities</div>
            </div>
        </div>
    '''

    if summary:
        html += f'''
        <h2>Today's Collection Summary</h2>
        <table>
            <tr>
                <th>Total Runs</th>
                <th>Successful</th>
                <th>Errors</th>
                <th>No New Data</th>
                <th>Permits Inserted</th>
                <th>Avg Duration</th>
            </tr>
            <tr>
                <td>{summary.get('total_runs', 0)}</td>
                <td class="green">{summary.get('successful', 0)}</td>
                <td class="red">{summary.get('errors', 0)}</td>
                <td>{summary.get('no_new_data', 0)}</td>
                <td>{summary.get('total_permits_inserted', 0)}</td>
                <td>{int(summary.get('avg_duration_ms', 0) or 0)}ms</td>
            </tr>
        </table>
        '''

    html += '''
        <h2>City Health Status</h2>
        <table>
            <tr>
                <th>City</th>
                <th>State</th>
                <th>Status</th>
                <th>Days Since Data</th>
                <th>Total Permits</th>
                <th>Failures</th>
                <th>Last Error</th>
            </tr>
    '''

    for city in health_data:
        color_class = city.get('health_color', 'RED').lower()
        days = city.get('days_since_data', 'N/A')
        if days is None:
            days = 'Never'
        html += f'''
            <tr>
                <td>{city.get('city', '')}</td>
                <td>{city.get('state', '')}</td>
                <td class="{color_class}">{city.get('status', '').upper()}</td>
                <td class="{color_class}">{days}</td>
                <td>{city.get('total_permits', 0)}</td>
                <td>{city.get('consecutive_failures', 0)}</td>
                <td>{(city.get('last_error') or '')[:50]}</td>
            </tr>
        '''

    html += '''
        </table>

        <h2>Recent Collection Runs</h2>
        <table>
            <tr>
                <th>Time</th>
                <th>City</th>
                <th>Status</th>
                <th>Permits Found</th>
                <th>Inserted</th>
                <th>Duration</th>
                <th>Error</th>
            </tr>
    '''

    for run in recent_runs:
        status_class = 'green' if run.get('status') == 'success' else ('yellow' if run.get('status') == 'no_new' else 'red')
        html += f'''
            <tr>
                <td>{run.get('run_started_at', '')}</td>
                <td>{run.get('city', '')} {run.get('state', '')}</td>
                <td class="{status_class}">{run.get('status', '')}</td>
                <td>{run.get('permits_found', 0)}</td>
                <td>{run.get('permits_inserted', 0)}</td>
                <td>{run.get('duration_ms', '')}ms</td>
                <td>{(run.get('error_message') or '')[:30]}</td>
            </tr>
        '''

    html += '''
        </table>

        <p style="color: #666; margin-top: 40px;">
            V15 Collector Redesign - prod_cities table
        </p>
    </body>
    </html>
    '''

    return html


@app.route('/admin/upgrade-user', methods=['POST'])
def admin_upgrade_user():
    """POST /admin/upgrade-user - Upgrade a user's subscription plan."""
    # Check admin authentication
    if not session.get('admin_authenticated'):
        return jsonify({'error': 'Unauthorized'}), 401

    email = request.form.get('email', '').strip().lower()
    plan = request.form.get('plan', 'free').strip().lower()

    if not email:
        return redirect('/admin?error=Email+is+required')

    if plan not in ('free', 'pro', 'enterprise'):
        return redirect('/admin?error=Invalid+plan')

    # V12.53: Direct User model update (removed subscribers.json dependency)
    user_obj = find_user_by_email(email)
    if user_obj:
        user_obj.plan = plan
        db.session.commit()
        return redirect(f'/admin?success=Upgraded+{email}+to+{plan}')
    else:
        return redirect(f'/admin?error=User+{email}+not+found')


# ===========================
# ADMIN ANALYTICS DASHBOARD
# ===========================

# Admin emails list - add emails that should have admin access
ADMIN_EMAILS = os.environ.get('ADMIN_EMAILS', 'wcrainshaw@gmail.com').lower().split(',')

@app.route('/admin/analytics')
def admin_analytics_page():
    """Admin analytics dashboard."""
    # Check admin authentication
    user = get_current_user()
    if not user or user.get('email', '').lower() not in ADMIN_EMAILS:
        if not session.get('admin_authenticated'):
            return "Unauthorized - Admin access required", 403

    # Gather all analytics data
    data = {
        'visitors_today': analytics.get_visitors_today(),
        'signups_week': analytics.get_signups_this_week(),
        'active_users_7d': analytics.get_active_users_7d(),
        'trial_starts_30d': analytics.get_trial_starts_30d(),
        'daily_traffic': analytics.get_daily_traffic(30),
        'top_pages': analytics.get_top_pages(7, 20),
        'funnel': analytics.get_conversion_funnel(30),
        'event_counts': analytics.get_event_counts(7),
        'city_engagement': analytics.get_city_engagement(30),
        'traffic_sources': analytics.get_traffic_sources(30),
        'health_status': analytics.get_latest_health_status(),
        'health_failures': analytics.get_health_failures_recent(20),
        'city_health': analytics.get_city_health_summary(),
        'route_health': analytics.get_route_health_summary(),
        'service_health': analytics.get_service_health_status(),
        'email_perf_7d': analytics.get_email_performance(7),
        'email_perf_30d': analytics.get_email_performance(30),
    }

    return render_template('admin_analytics.html', data=data)


# ===========================
# MY LEADS CRM PAGE
# ===========================

@app.route('/my-leads')
def my_leads_page():
    """Render the My Leads CRM page."""
    user = get_current_user()
    if not user:
        # Redirect to login with message
        return redirect('/login?redirect=my-leads')

    footer_cities = get_cities_with_data()
    return render_template('my_leads.html', user=user, footer_cities=footer_cities)


# ===========================
# SAVED SEARCHES PAGE
# ===========================

@app.route('/saved-searches')
def saved_searches_page():
    """Render the Saved Searches page."""
    user = get_current_user()
    if not user:
        return redirect('/login?redirect=saved-searches')

    searches = get_user_saved_searches(user['email'])
    footer_cities = get_cities_with_data()
    return render_template('saved_searches.html', user=user, searches=searches, footer_cities=footer_cities)


# ===========================
# SEO CITY LANDING PAGES
# ===========================

# City configurations with SEO content
CITY_SEO_CONFIG = {
    "new-york": {
        "name": "New York City",
        "state": "NY",
        "meta_title": "New York City Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse 2000+ active building permits in New York City. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>New York City's construction market is one of the largest and most dynamic in the world. With constant development across all five boroughs, NYC building permits represent billions of dollars in annual construction activity. From luxury high-rise developments in Manhattan to residential renovations in Brooklyn and Queens, the opportunities for contractors are endless.</p>
            <p>The NYC construction industry spans every trade imaginable—HVAC installations in commercial towers, electrical upgrades in historic brownstones, plumbing renovations in pre-war buildings, and roofing projects across thousands of residential properties. New York City construction permits are filed daily with the Department of Buildings, creating a steady stream of new contractor leads.</p>
            <p>For contractors seeking NYC building permits and construction leads, timing is everything. PermitGrab delivers fresh New York City permit data daily, giving you the edge to connect with property owners before your competition even knows the project exists.</p>
        """
    },
    "los-angeles": {
        "name": "Los Angeles",
        "state": "CA",
        "meta_title": "Los Angeles Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse 2000+ active building permits in Los Angeles. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Los Angeles is experiencing unprecedented construction growth, making it one of the hottest markets for contractor leads in the nation. From Santa Monica to Downtown LA to the Valley, LA building permits cover everything from ADU (Accessory Dwelling Unit) construction to major commercial developments and earthquake retrofit projects.</p>
            <p>The LA construction market is unique in its diversity—solar panel installations are booming, pool construction remains strong year-round, and seismic retrofitting creates steady demand for structural contractors. Los Angeles construction permits also reflect the city's focus on sustainability, with green building projects and EV charger installations on the rise.</p>
            <p>Contractors looking for Los Angeles building permits need fast access to new filings. PermitGrab pulls LA permit data directly from official city sources, delivering actionable contractor leads for every trade from roofing to HVAC to general construction.</p>
        """
    },
    "chicago": {
        "name": "Chicago",
        "state": "IL",
        "meta_title": "Chicago Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse 2000+ active building permits in Chicago. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Chicago's construction industry is thriving, with billions invested in residential, commercial, and infrastructure projects each year. Chicago building permits cover everything from downtown high-rise construction to single-family renovations in neighborhoods across the city. The Windy City's harsh winters create strong seasonal demand for HVAC, roofing, and weatherization projects.</p>
            <p>The Chicago contractor market benefits from the city's aging housing stock—thousands of greystone and brick buildings require ongoing maintenance, window replacements, tuckpointing, and interior renovations. Chicago construction permits also reflect the city's industrial heritage, with many warehouse-to-residential conversions creating opportunities for general contractors and specialty trades alike.</p>
            <p>For contractors seeking Chicago building permits and construction leads, staying ahead of the competition means accessing permit data as soon as it's filed. PermitGrab delivers fresh Chicago permit leads daily, helping you find and win jobs across Cook County.</p>
        """
    },
    "san-francisco": {
        "name": "San Francisco",
        "state": "CA",
        "meta_title": "San Francisco Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse 2000+ active building permits in San Francisco. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>San Francisco's construction market commands some of the highest project values in the nation. SF building permits range from tech company office buildouts to Victorian home renovations in Pacific Heights to seismic retrofitting in older buildings. The city's strict building codes and permitting requirements mean property owners actively seek qualified, reliable contractors.</p>
            <p>The San Francisco construction industry reflects the city's unique character—historic preservation projects, ADU construction under California's housing laws, and high-end residential renovations drive steady permit activity. San Francisco construction permits also include significant solar and green building projects as the city pushes toward sustainability goals.</p>
            <p>Contractors targeting San Francisco building permits face stiff competition in this premium market. PermitGrab gives you the advantage of seeing new SF permit filings first, so you can reach property owners while they're still evaluating contractors.</p>
        """
    },
    "austin": {
        "name": "Austin",
        "state": "TX",
        "meta_title": "Austin Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse 2000+ active building permits in Austin. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Austin is one of America's fastest-growing cities, and the construction boom shows no signs of slowing. Austin building permits reflect the city's explosive growth—new residential developments, commercial construction, and infrastructure projects create constant demand for contractors of every trade. From Round Rock to South Austin, the permit pipeline is full.</p>
            <p>The Austin construction market offers unique opportunities including new home construction in master-planned communities, office buildouts for tech companies relocating to Texas, and renovation projects in established neighborhoods like Hyde Park and Travis Heights. Austin construction permits span HVAC installations critical for Texas summers, pool construction, and outdoor living projects.</p>
            <p>For contractors seeking Austin building permits, speed matters in this competitive market. PermitGrab delivers fresh Austin permit data daily, connecting you with property owners and builders who need quality contractors now.</p>
        """
    },
    "seattle": {
        "name": "Seattle",
        "state": "WA",
        "meta_title": "Seattle Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse 900+ active building permits in Seattle. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Seattle's construction industry continues to boom, driven by tech industry growth and population influx. Seattle building permits cover high-rise development downtown, residential construction in neighborhoods like Capitol Hill and Ballard, and renovation projects across King County. The Pacific Northwest climate creates strong demand for roofing, weatherization, and moisture-control projects.</p>
            <p>The Seattle construction market includes significant green building activity—the city leads in LEED-certified construction, solar installations, and energy-efficient upgrades. Seattle construction permits also reflect the region's seismic concerns, with retrofit and structural reinforcement projects common in older buildings.</p>
            <p>Contractors pursuing Seattle building permits benefit from accessing new filings before they become public knowledge. PermitGrab pulls permit data from official Seattle sources daily, delivering contractor leads for every specialty from plumbing to electrical to general construction.</p>
        """
    },
    "new-orleans": {
        "name": "New Orleans",
        "state": "LA",
        "meta_title": "New Orleans Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse 2000+ active building permits in New Orleans. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>New Orleans has a vibrant construction market shaped by the city's unique architecture, climate, and ongoing revitalization efforts. New Orleans building permits cover historic preservation in the French Quarter, residential renovations in the Garden District, and new construction in rapidly developing neighborhoods like the Bywater and Mid-City.</p>
            <p>The New Orleans construction industry requires specialized knowledge—hurricane-resistant construction, moisture control, foundation work in challenging soil conditions, and historic preservation standards create demand for skilled contractors. NOLA construction permits reflect seasonal patterns, with roofing and exterior work concentrated outside hurricane season.</p>
            <p>For contractors seeking New Orleans building permits and construction leads, local market knowledge combined with fast permit access creates winning opportunities. PermitGrab delivers fresh NOLA permit data to help you find and win jobs throughout the Crescent City.</p>
        """
    },
    "baton-rouge": {
        "name": "Baton Rouge",
        "state": "LA",
        "meta_title": "Baton Rouge Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse 1200+ active building permits in Baton Rouge. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Baton Rouge's construction market benefits from Louisiana's capital city status and growing economy. Baton Rouge building permits cover residential construction in areas like Prairieville and Denham Springs, commercial development along I-10 and I-12 corridors, and renovation projects throughout East Baton Rouge Parish.</p>
            <p>The Baton Rouge construction industry reflects regional priorities—flood mitigation, hurricane-resistant construction, and energy-efficient HVAC systems are common project types. BR construction permits also include significant industrial and petrochemical-related construction given the area's economic base.</p>
            <p>Contractors pursuing Baton Rouge building permits find steady work in this growing market. PermitGrab delivers fresh EBR permit data daily, connecting contractors with property owners who need quality work done right.</p>
        """
    },
    "nashville": {
        "name": "Nashville",
        "state": "TN",
        "meta_title": "Nashville Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse 900+ active building permits in Nashville. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Nashville is one of America's hottest construction markets, with unprecedented growth driving demand for every type of contractor. Nashville building permits reflect the city's transformation—luxury condo towers downtown, new residential developments in surrounding counties, and renovations in trendy neighborhoods like East Nashville and The Nations.</p>
            <p>The Nashville construction industry benefits from the city's booming entertainment, healthcare, and corporate relocation activity. Music City construction permits include high-end residential work, commercial tenant improvements, and hospitality projects serving the tourism industry. HVAC installation is critical given Tennessee's hot summers.</p>
            <p>For contractors seeking Nashville building permits, getting to leads first is essential in this competitive market. PermitGrab delivers fresh Nashville permit data daily, giving you the inside track on new construction projects throughout Davidson County.</p>
        """
    },
    "atlanta": {
        "name": "Atlanta",
        "state": "GA",
        "meta_title": "Atlanta Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse 500+ active building permits in Atlanta. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Atlanta's construction market is booming, fueled by corporate relocations, population growth, and major infrastructure investments. Atlanta building permits span high-rise development in Midtown and Buckhead, residential construction in metro Atlanta suburbs, and renovation projects in historic neighborhoods like Virginia-Highland and Inman Park.</p>
            <p>The Atlanta construction industry reflects the region's diversity—from luxury home construction in North Fulton to commercial buildouts in the Perimeter area to adaptive reuse projects in emerging neighborhoods. ATL construction permits include significant HVAC and electrical work given the hot Georgia summers and aging housing stock.</p>
            <p>Contractors pursuing Atlanta building permits compete in a fast-moving market where early access to permits means more wins. PermitGrab delivers fresh Atlanta permit data daily, connecting you with property owners and developers who need quality contractors now.</p>
        """
    },
    "cincinnati": {
        "name": "Cincinnati",
        "state": "OH",
        "meta_title": "Cincinnati Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse 300+ active building permits in Cincinnati. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Cincinnati's construction market is experiencing a renaissance, with major investments in downtown development and neighborhood revitalization. Cincinnati building permits cover riverfront development projects, residential renovations in historic neighborhoods like Over-the-Rhine and Mount Adams, and commercial construction throughout Hamilton County.</p>
            <p>The Cincinnati construction industry benefits from the city's aging housing stock—Victorian-era homes require ongoing maintenance, window replacements, roofing projects, and interior renovations. Cincy construction permits also reflect the region's industrial legacy with many warehouse-to-residential conversions and adaptive reuse projects.</p>
            <p>For contractors seeking Cincinnati building permits, accessing new filings quickly means beating the competition to quality leads. PermitGrab delivers fresh Cincinnati permit data daily, helping you find and win jobs throughout the Queen City.</p>
        """
    },
    "cambridge": {
        "name": "Cambridge",
        "state": "MA",
        "meta_title": "Cambridge Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Cambridge, MA. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Cambridge, home to Harvard and MIT, has a thriving construction market driven by academic institutions, biotech companies, and residential demand. Cambridge building permits span laboratory construction, commercial office space, and renovations to the city's historic housing stock.</p>
            <p>The Cambridge construction industry benefits from the city's density and ongoing development around Kendall Square and Central Square. Cambridge construction permits reflect strong demand for HVAC, electrical, and plumbing work in both commercial and residential sectors.</p>
            <p>For contractors seeking Cambridge building permits, timing is key in this competitive market. PermitGrab delivers fresh Cambridge permit data daily, helping you connect with project owners across Middlesex County.</p>
        """
    },
    "washington-dc": {
        "name": "Washington DC",
        "state": "DC",
        "meta_title": "Washington DC Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Washington DC. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Washington DC's construction market is driven by government buildings, commercial development, and a dense residential market. DC building permits cover everything from federal facility renovations to row house restorations in Capitol Hill, Georgetown, and Adams Morgan.</p>
            <p>The DC construction industry benefits from constant government investment and the city's historic preservation requirements. Washington DC construction permits reflect strong demand for structural work, window replacements, and interior renovations in the city's iconic architecture.</p>
            <p>For contractors seeking DC building permits, quick access to new filings means getting ahead of the competition. PermitGrab delivers fresh Washington DC permit data daily, helping you win contracts across the District.</p>
        """
    },
    "san-antonio": {
        "name": "San Antonio",
        "state": "TX",
        "meta_title": "San Antonio Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in San Antonio. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>San Antonio is one of the fastest-growing cities in Texas, with a booming construction market across residential and commercial sectors. San Antonio building permits cover new home construction, commercial development along the I-35 corridor, and renovations throughout Bexar County.</p>
            <p>The San Antonio construction industry benefits from the city's affordable land and strong population growth. San Antonio construction permits reflect high demand for HVAC in the Texas heat, roofing projects, and general construction work.</p>
            <p>For contractors seeking San Antonio building permits, early access to new filings is essential. PermitGrab delivers fresh San Antonio permit data daily, helping you connect with property owners across the Alamo City.</p>
        """
    },
    "kansas-city": {
        "name": "Kansas City",
        "state": "MO",
        "meta_title": "Kansas City Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Kansas City. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Kansas City's construction market spans both Missouri and Kansas, with major development in downtown KC, the Plaza, and surrounding suburbs. Kansas City building permits cover commercial construction, residential development, and renovations across the metro area.</p>
            <p>The KC construction industry benefits from the region's central location and ongoing revitalization efforts. Kansas City construction permits reflect demand across all trades, from HVAC and electrical to general construction and roofing.</p>
            <p>For contractors seeking Kansas City building permits, quick access to permit data helps you beat the competition. PermitGrab delivers fresh KC permit data daily, helping you find quality leads across the metro.</p>
        """
    },
    "detroit": {
        "name": "Detroit",
        "state": "MI",
        "meta_title": "Detroit Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Detroit. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Detroit's construction market is experiencing a renaissance, with major investments in downtown development and neighborhood revitalization. Detroit building permits cover commercial construction in the central business district, residential renovations across the city's historic neighborhoods, and industrial development.</p>
            <p>The Detroit construction industry benefits from the city's comeback story—historic buildings being restored, new developments rising, and a growing population demanding quality contractors. Detroit construction permits reflect strong demand for renovation work, electrical upgrades, and HVAC installations.</p>
            <p>For contractors seeking Detroit building permits, early access to new filings is crucial. PermitGrab delivers fresh Detroit permit data daily, helping you win jobs across the Motor City.</p>
        """
    },
    "pittsburgh": {
        "name": "Pittsburgh",
        "state": "PA",
        "meta_title": "Pittsburgh Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Pittsburgh. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Pittsburgh's construction market is thriving, driven by tech industry growth, healthcare development, and residential demand. Pittsburgh building permits cover commercial construction downtown, university expansions, and renovations in neighborhoods like Shadyside, Lawrenceville, and the South Side.</p>
            <p>The Pittsburgh construction industry benefits from the city's transformation from industrial powerhouse to tech hub. Pittsburgh construction permits reflect strong demand for HVAC, electrical, and renovation work in both commercial and residential sectors.</p>
            <p>For contractors seeking Pittsburgh building permits, quick access to new filings helps you connect with project owners first. PermitGrab delivers fresh Pittsburgh permit data daily across Allegheny County.</p>
        """
    },
    "denver": {
        "name": "Denver",
        "state": "CO",
        "meta_title": "Denver Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Denver. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Denver's construction market is one of the hottest in the nation, with explosive growth in both residential and commercial development. Denver building permits cover high-rise construction downtown, residential development across the metro, and renovations throughout the Front Range.</p>
            <p>The Denver construction industry benefits from the city's population boom and strong economy. Denver construction permits reflect high demand for all trades—HVAC, electrical, plumbing, roofing, and general construction work are all in constant demand.</p>
            <p>For contractors seeking Denver building permits, timing is everything in this competitive market. PermitGrab delivers fresh Denver permit data daily, helping you win contracts across the Mile High City.</p>
        """
    },
    "portland": {
        "name": "Portland",
        "state": "OR",
        "meta_title": "Portland Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Portland, OR. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Portland's construction market reflects the city's commitment to sustainability and urban density. Portland building permits cover green building projects, residential development, ADU construction, and commercial renovations throughout Multnomah County.</p>
            <p>The Portland construction industry benefits from the city's unique building codes and environmental focus. Portland construction permits reflect strong demand for energy-efficient upgrades, solar installations, and sustainable building practices.</p>
            <p>For contractors seeking Portland building permits, early access to new filings helps you connect with eco-conscious project owners. PermitGrab delivers fresh Portland permit data daily, helping you win jobs across the Rose City.</p>
        """
    },
    "miami": {
        "name": "Miami-Dade County",
        "state": "FL",
        # V233 P1-2: was "Miami Building Permits …" — no state abbrev,
        # inconsistent with every other entry. The V230 read-time
        # normalizer doesn't rescue this one because it looks for
        # "{name} Building" (name="Miami-Dade County") which isn't in
        # the title either. Hardcoded fix is cleanest.
        "meta_title": "Miami, FL Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Miami, FL (Miami-Dade County). Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Miami's construction market is among the most active in the nation, with constant development across residential, commercial, and hospitality sectors. Miami building permits cover high-rise condo construction, luxury home development, and renovations throughout Miami-Dade County.</p>
            <p>The Miami construction industry benefits from the region's year-round building season and strong demand from domestic and international buyers. Miami construction permits reflect high demand for hurricane-resistant construction, HVAC work in the tropical climate, and pool construction.</p>
            <p>For contractors seeking Miami building permits, quick access to new filings is essential in this competitive market. PermitGrab delivers fresh Miami permit data daily, helping you win contracts across South Florida.</p>
        """
    },
    "raleigh": {
        "name": "Raleigh",
        "state": "NC",
        "meta_title": "Raleigh Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Raleigh, NC. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Raleigh's construction market is booming as part of the Research Triangle's explosive growth. Raleigh building permits cover residential development, commercial construction, and tech campus expansions throughout Wake County.</p>
            <p>The Raleigh construction industry benefits from the region's strong job growth and influx of new residents. Raleigh construction permits reflect high demand for new home construction, HVAC installations, and commercial build-outs.</p>
            <p>For contractors seeking Raleigh building permits, early access to permit data helps you stay ahead of the competition. PermitGrab delivers fresh Raleigh permit data daily, helping you win jobs across the Triangle.</p>
        """
    },
    "phoenix": {
        "name": "Phoenix",
        "state": "AZ",
        "meta_title": "Phoenix Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Phoenix. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Phoenix's construction market is one of the largest in the Southwest, with constant development across the Valley of the Sun. Phoenix building permits cover new home construction, commercial development, and renovations throughout Maricopa County.</p>
            <p>The Phoenix construction industry benefits from year-round building weather and strong population growth. Phoenix construction permits reflect high demand for HVAC in the desert heat, pool construction, and solar installations.</p>
            <p>For contractors seeking Phoenix building permits, quick access to new filings is crucial. PermitGrab delivers fresh Phoenix permit data daily, helping you win contracts across the Valley.</p>
        """
    },
    "san-jose": {
        "name": "San Jose",
        "state": "CA",
        "meta_title": "San Jose Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in San Jose. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>San Jose's construction market is driven by Silicon Valley's tech industry and strong housing demand. San Jose building permits cover tech campus construction, residential development, and ADU projects throughout Santa Clara County.</p>
            <p>The San Jose construction industry benefits from the region's high property values and constant development pressure. San Jose construction permits reflect strong demand for electrical work, seismic retrofitting, and energy-efficient upgrades.</p>
            <p>For contractors seeking San Jose building permits, timing is key in this premium market. PermitGrab delivers fresh San Jose permit data daily, helping you connect with project owners across the South Bay.</p>
        """
    },
    "san-diego": {
        "name": "San Diego",
        "state": "CA",
        "meta_title": "San Diego Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in San Diego. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>San Diego's construction market benefits from year-round building weather and strong residential demand. San Diego building permits cover new home construction, ADU development, and commercial projects throughout San Diego County.</p>
            <p>The San Diego construction industry reflects the region's military presence, biotech sector, and tourism industry. San Diego construction permits show strong demand for HVAC, solar installations, and pool construction.</p>
            <p>For contractors seeking San Diego building permits, early access to permit data helps you win more jobs. PermitGrab delivers fresh San Diego permit data daily, helping you grow your business across America's Finest City.</p>
        """
    },
    "sacramento": {
        "name": "Sacramento",
        "state": "CA",
        "meta_title": "Sacramento Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Sacramento. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Sacramento's construction market is thriving as California's capital attracts new residents and businesses. Sacramento building permits cover new home construction, commercial development, and renovations throughout the Sacramento Valley.</p>
            <p>The Sacramento construction industry benefits from the region's more affordable land compared to the Bay Area. Sacramento construction permits reflect strong demand for HVAC in the hot summers, roofing, and residential construction.</p>
            <p>For contractors seeking Sacramento building permits, quick access to new filings helps you compete effectively. PermitGrab delivers fresh Sacramento permit data daily, helping you win contracts across the region.</p>
        """
    },
    "boston": {
        "name": "Boston",
        "state": "MA",
        "meta_title": "Boston Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Boston. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Boston's construction market is driven by world-class universities, healthcare institutions, and a dense residential market. Boston building permits cover commercial development in the Seaport, residential renovations in historic neighborhoods, and institutional construction throughout Greater Boston.</p>
            <p>The Boston construction industry benefits from the region's strong economy and aging housing stock requiring constant maintenance. Boston construction permits reflect high demand for HVAC, electrical upgrades, and renovation work in the city's historic buildings.</p>
            <p>For contractors seeking Boston building permits, early access to permit data is essential. PermitGrab delivers fresh Boston permit data daily, helping you win jobs across the Greater Boston area.</p>
        """
    },
    "philadelphia": {
        "name": "Philadelphia",
        "state": "PA",
        "meta_title": "Philadelphia Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Philadelphia. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Philadelphia's construction market is experiencing strong growth, with major development downtown and in surrounding neighborhoods. Philadelphia building permits cover commercial construction, residential renovations in historic rowhomes, and new development across the city.</p>
            <p>The Philadelphia construction industry benefits from the city's affordability relative to NYC and DC. Philly construction permits reflect strong demand for HVAC, electrical work, and renovations in the city's historic housing stock.</p>
            <p>For contractors seeking Philadelphia building permits, quick access to new filings helps you stay competitive. PermitGrab delivers fresh Philly permit data daily, helping you win contracts across the City of Brotherly Love.</p>
        """
    },
    "baltimore": {
        "name": "Baltimore",
        "state": "MD",
        "meta_title": "Baltimore Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Baltimore. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Baltimore's construction market is driven by waterfront development, historic preservation, and neighborhood revitalization. Baltimore building permits cover commercial projects in the Inner Harbor, residential renovations in rowhouse neighborhoods, and institutional construction.</p>
            <p>The Baltimore construction industry benefits from major redevelopment initiatives and proximity to Washington DC. Baltimore construction permits reflect strong demand for renovation work, HVAC upgrades, and historic preservation projects.</p>
            <p>For contractors seeking Baltimore building permits, quick access to new filings is essential. PermitGrab delivers fresh Baltimore permit data daily, helping you win contracts across Charm City.</p>
        """
    },
    "charlotte": {
        "name": "Charlotte",
        "state": "NC",
        "meta_title": "Charlotte Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Charlotte. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Charlotte's construction market is booming as one of the fastest-growing cities in the Southeast. Charlotte building permits cover high-rise construction uptown, residential development across Mecklenburg County, and commercial projects throughout the metro.</p>
            <p>The Charlotte construction industry benefits from strong population growth and corporate relocations. Charlotte construction permits reflect high demand for new home construction, commercial build-outs, and HVAC installations.</p>
            <p>For contractors seeking Charlotte building permits, early access to permit data helps you stay competitive. PermitGrab delivers fresh Charlotte permit data daily, helping you win jobs across the Queen City.</p>
        """
    },
    "columbus": {
        "name": "Columbus",
        "state": "OH",
        "meta_title": "Columbus Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Columbus, OH. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Columbus is Ohio's fastest-growing city with a thriving construction market. Columbus building permits cover commercial development downtown, residential growth in suburbs like Dublin and Westerville, and university-related construction around Ohio State.</p>
            <p>The Columbus construction industry benefits from a diverse economy and strong housing demand. Columbus construction permits reflect steady demand for new construction, renovations, and commercial projects.</p>
            <p>For contractors seeking Columbus building permits, quick access to new filings helps you win more jobs. PermitGrab delivers fresh Columbus permit data daily across Franklin County.</p>
        """
    },
    "fort-worth": {
        "name": "Fort Worth",
        "state": "TX",
        "meta_title": "Fort Worth Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Fort Worth. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Fort Worth's construction market is thriving as part of the Dallas-Fort Worth metroplex's explosive growth. Fort Worth building permits cover residential development, commercial construction, and industrial projects across Tarrant County.</p>
            <p>The Fort Worth construction industry benefits from Texas's business-friendly environment and strong population growth. Fort Worth construction permits reflect high demand for HVAC, roofing, and general construction work.</p>
            <p>For contractors seeking Fort Worth building permits, early access to new filings is key. PermitGrab delivers fresh Fort Worth permit data daily, helping you compete across the DFW metroplex.</p>
        """
    },
    "honolulu": {
        "name": "Honolulu",
        "state": "HI",
        "meta_title": "Honolulu Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Honolulu. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Honolulu's construction market serves Hawaii's most populous island with residential, commercial, and hospitality projects. Honolulu building permits cover high-rise condos, resort renovations, and residential construction across Oahu.</p>
            <p>The Honolulu construction industry is unique with island-specific challenges and opportunities. Honolulu construction permits reflect steady demand for renovation work, HVAC installations, and new development.</p>
            <p>For contractors seeking Honolulu building permits, quick access to new filings is valuable. PermitGrab delivers fresh Honolulu permit data daily, helping you win contracts across the island.</p>
        """
    },
    "indianapolis": {
        "name": "Indianapolis",
        "state": "IN",
        "meta_title": "Indianapolis Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Indianapolis. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Indianapolis has a strong construction market driven by downtown development and suburban growth. Indianapolis building permits cover commercial construction, residential development across Marion County, and industrial projects.</p>
            <p>The Indianapolis construction industry benefits from the city's central location and affordable market. Indy construction permits reflect steady demand for all trades from HVAC to general construction.</p>
            <p>For contractors seeking Indianapolis building permits, early access to permit data is essential. PermitGrab delivers fresh Indy permit data daily, helping you grow your business across the Circle City.</p>
        """
    },
    "louisville": {
        "name": "Louisville",
        "state": "KY",
        "meta_title": "Louisville Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Louisville. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Louisville's construction market spans residential, commercial, and industrial development across Jefferson County. Louisville building permits cover downtown revitalization, residential growth in the Highlands and East End, and commercial construction.</p>
            <p>The Louisville construction industry benefits from the city's strategic location and growing economy. Louisville construction permits reflect demand for renovation work, new construction, and commercial build-outs.</p>
            <p>For contractors seeking Louisville building permits, quick access to new filings helps you compete. PermitGrab delivers fresh Louisville permit data daily across Kentucky's largest city.</p>
        """
    },
    "memphis": {
        "name": "Memphis",
        "state": "TN",
        "meta_title": "Memphis Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Memphis. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Memphis has a diverse construction market with residential, commercial, and industrial projects. Memphis building permits cover downtown development, residential renovations, and logistics/warehouse construction due to FedEx's headquarters.</p>
            <p>The Memphis construction industry benefits from the city's role as a logistics hub and growing economy. Memphis construction permits reflect steady demand for all construction trades.</p>
            <p>For contractors seeking Memphis building permits, early access to permit data is valuable. PermitGrab delivers fresh Memphis permit data daily, helping you win contracts across the Bluff City.</p>
        """
    },
    "mesa": {
        "name": "Mesa",
        "state": "AZ",
        "meta_title": "Mesa Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Mesa, AZ. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Mesa is Arizona's third-largest city with a thriving construction market. Mesa building permits cover residential development, commercial construction, and renovation projects across the East Valley.</p>
            <p>The Mesa construction industry benefits from Phoenix metro growth and year-round building weather. Mesa construction permits reflect high demand for HVAC, pool construction, and residential work.</p>
            <p>For contractors seeking Mesa building permits, quick access to new filings is essential. PermitGrab delivers fresh Mesa permit data daily, helping you compete across the Valley.</p>
        """
    },
    "milwaukee": {
        "name": "Milwaukee",
        "state": "WI",
        "meta_title": "Milwaukee Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Milwaukee. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Milwaukee's construction market includes residential, commercial, and industrial projects across the metro. Milwaukee building permits cover downtown development, residential renovations in historic neighborhoods, and commercial construction.</p>
            <p>The Milwaukee construction industry benefits from the city's manufacturing heritage and revitalization efforts. Milwaukee construction permits reflect strong demand for renovation work, HVAC, and general construction.</p>
            <p>For contractors seeking Milwaukee building permits, early access to permit data helps you compete. PermitGrab delivers fresh Milwaukee permit data daily across Wisconsin's largest city.</p>
        """
    },
    "oakland": {
        "name": "Oakland",
        "state": "CA",
        "meta_title": "Oakland Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Oakland. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Oakland's construction market is thriving with development across the East Bay. Oakland building permits cover high-density residential projects, commercial development, and renovations in neighborhoods from Rockridge to Jack London Square.</p>
            <p>The Oakland construction industry benefits from San Francisco Bay Area growth and relative affordability. Oakland construction permits reflect strong demand for residential construction and seismic retrofitting.</p>
            <p>For contractors seeking Oakland building permits, quick access to new filings is valuable. PermitGrab delivers fresh Oakland permit data daily across Alameda County.</p>
        """
    },
    "oklahoma-city": {
        "name": "Oklahoma City",
        "state": "OK",
        "meta_title": "Oklahoma City Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Oklahoma City. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Oklahoma City has a strong construction market driven by energy industry investment and population growth. OKC building permits cover commercial development downtown, residential growth in suburbs like Edmond, and industrial construction.</p>
            <p>The Oklahoma City construction industry benefits from affordable land and business-friendly policies. OKC construction permits reflect steady demand for all construction trades.</p>
            <p>For contractors seeking Oklahoma City building permits, early access to permit data is key. PermitGrab delivers fresh OKC permit data daily across the metro area.</p>
        """
    },
    "omaha": {
        "name": "Omaha",
        "state": "NE",
        "meta_title": "Omaha Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Omaha. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Omaha's construction market serves Nebraska's largest city with residential, commercial, and industrial projects. Omaha building permits cover downtown development, suburban residential growth, and commercial construction throughout Douglas County.</p>
            <p>The Omaha construction industry benefits from a stable economy and steady population growth. Omaha construction permits reflect consistent demand for new construction and renovation work.</p>
            <p>For contractors seeking Omaha building permits, quick access to new filings helps you compete. PermitGrab delivers fresh Omaha permit data daily across the metro.</p>
        """
    },
    "st-louis": {
        "name": "St. Louis",
        "state": "MO",
        "meta_title": "St. Louis Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in St. Louis. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>St. Louis has a diverse construction market with residential, commercial, and institutional projects. St. Louis building permits cover downtown revitalization, residential renovations in historic neighborhoods, and commercial development.</p>
            <p>The St. Louis construction industry benefits from the city's affordability and architectural heritage. STL construction permits reflect strong demand for renovation work, HVAC, and general construction.</p>
            <p>For contractors seeking St. Louis building permits, early access to permit data is valuable. PermitGrab delivers fresh St. Louis permit data daily across the Gateway City.</p>
        """
    },
    "tucson": {
        "name": "Tucson",
        "state": "AZ",
        "meta_title": "Tucson Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Tucson. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Tucson's construction market serves Arizona's second-largest city with residential, commercial, and university-related projects. Tucson building permits cover new home construction, commercial development, and renovation work across Pima County.</p>
            <p>The Tucson construction industry benefits from year-round building weather and steady growth. Tucson construction permits reflect high demand for HVAC, pool construction, and solar installations.</p>
            <p>For contractors seeking Tucson building permits, quick access to new filings is essential. PermitGrab delivers fresh Tucson permit data daily across Southern Arizona.</p>
        """
    },
    "long-beach": {
        "name": "Long Beach",
        "state": "CA",
        "meta_title": "Long Beach Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Long Beach. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Long Beach has a thriving construction market as LA County's second-largest city. Long Beach building permits cover waterfront development, residential renovations, and commercial construction throughout the port city.</p>
            <p>The Long Beach construction industry benefits from port-related development and residential demand. Long Beach construction permits reflect steady work for all trades.</p>
            <p>For contractors seeking Long Beach building permits, early access to permit data helps you compete. PermitGrab delivers fresh Long Beach permit data daily.</p>
        """
    },
    "fresno": {
        "name": "Fresno",
        "state": "CA",
        "meta_title": "Fresno Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Fresno. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Fresno's construction market serves California's Central Valley with residential, commercial, and agricultural projects. Fresno building permits cover new home construction, commercial development, and renovation work throughout Fresno County.</p>
            <p>The Fresno construction industry benefits from affordable land and California's housing demand. Fresno construction permits reflect steady work across all construction trades.</p>
            <p>For contractors seeking Fresno building permits, quick access to new filings is valuable. PermitGrab delivers fresh Fresno permit data daily across the Valley.</p>
        """
    },
    "las-vegas": {
        "name": "Las Vegas",
        "state": "NV",
        "meta_title": "Las Vegas Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Las Vegas. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Las Vegas has one of the most dynamic construction markets in the country. Las Vegas building permits cover casino and resort development, residential construction in rapidly growing suburbs, and commercial projects across Clark County.</p>
            <p>The Las Vegas construction industry benefits from constant tourism investment and population growth. Vegas construction permits reflect high demand for HVAC, pool construction, and commercial build-outs.</p>
            <p>For contractors seeking Las Vegas building permits, early access to permit data is essential. PermitGrab delivers fresh Las Vegas permit data daily, helping you win contracts across the Valley.</p>
        """
    },
    "orlando": {
        "name": "Orlando",
        "state": "FL",
        "meta_title": "Orlando Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Orlando. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Orlando's construction market is driven by tourism, population growth, and theme park expansion. Orlando building permits cover hospitality projects, residential development across Orange County, and commercial construction throughout Central Florida.</p>
            <p>The Orlando construction industry benefits from year-round building weather and strong economic growth. Orlando construction permits reflect high demand for all construction trades.</p>
            <p>For contractors seeking Orlando building permits, quick access to new filings helps you compete. PermitGrab delivers fresh Orlando permit data daily across the I-4 corridor.</p>
        """
    },
    "tampa": {
        "name": "Tampa",
        "state": "FL",
        "meta_title": "Tampa Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Tampa. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Tampa's construction market is thriving with waterfront development, residential growth, and commercial projects. Tampa building permits cover downtown high-rises, residential construction across Hillsborough County, and commercial development.</p>
            <p>The Tampa construction industry benefits from Florida's growth and year-round building weather. Tampa construction permits reflect high demand for HVAC, hurricane-resistant construction, and pool work.</p>
            <p>For contractors seeking Tampa building permits, early access to permit data is key. PermitGrab delivers fresh Tampa permit data daily across the Tampa Bay area.</p>
        """
    },
    "jacksonville": {
        "name": "Jacksonville",
        "state": "FL",
        "meta_title": "Jacksonville Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Jacksonville. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Jacksonville is Florida's largest city by area with a diverse construction market. Jacksonville building permits cover residential development, commercial construction, and port-related projects across Duval County.</p>
            <p>The Jacksonville construction industry benefits from affordable land and Florida's population growth. Jax construction permits reflect steady demand for all construction trades.</p>
            <p>For contractors seeking Jacksonville building permits, quick access to new filings is valuable. PermitGrab delivers fresh Jacksonville permit data daily across Northeast Florida.</p>
        """
    },
    "virginia-beach": {
        "name": "Virginia Beach",
        "state": "VA",
        "meta_title": "Virginia Beach Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Virginia Beach. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Virginia Beach has a strong construction market serving the Hampton Roads region. Virginia Beach building permits cover residential development, commercial construction, and military-related projects.</p>
            <p>The Virginia Beach construction industry benefits from military investment and tourism. VB construction permits reflect steady demand for renovation work and new construction.</p>
            <p>For contractors seeking Virginia Beach building permits, early access to permit data helps you compete. PermitGrab delivers fresh Virginia Beach permit data daily.</p>
        """
    },
    "albuquerque": {
        "name": "Albuquerque",
        "state": "NM",
        "meta_title": "Albuquerque Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Albuquerque. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Albuquerque's construction market serves New Mexico's largest city with residential, commercial, and institutional projects. Albuquerque building permits cover new home construction, commercial development, and renovation work across Bernalillo County.</p>
            <p>The Albuquerque construction industry benefits from film industry growth and steady population. ABQ construction permits reflect demand for HVAC, solar, and general construction work.</p>
            <p>For contractors seeking Albuquerque building permits, quick access to new filings is valuable. PermitGrab delivers fresh Albuquerque permit data daily.</p>
        """
    },
    "cleveland": {
        "name": "Cleveland",
        "state": "OH",
        "meta_title": "Cleveland Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Cleveland. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Cleveland's construction market includes downtown revitalization, residential renovations, and healthcare/university projects. Cleveland building permits cover commercial construction, residential work in neighborhoods like Ohio City and Tremont, and institutional development.</p>
            <p>The Cleveland construction industry benefits from major hospital systems and ongoing revitalization. Cleveland construction permits reflect strong demand for renovation work and HVAC.</p>
            <p>For contractors seeking Cleveland building permits, early access to permit data is essential. PermitGrab delivers fresh Cleveland permit data daily across Cuyahoga County.</p>
        """
    },
}

# Dynamic city list from city_configs.py
def get_all_cities_list():
    """Get all active cities for navigation."""
    return [{"slug": c["slug"], "name": c["name"]} for c in get_all_cities_info()]

ALL_CITIES = get_all_cities_list()

# V12.23 SEO: State hub pages
# V12.28 MEGA EXPANSION: Added 28 new states (38 total with 3+ cities)
STATE_CONFIG = {
    'texas': {'name': 'Texas', 'abbrev': 'TX'},
    'california': {'name': 'California', 'abbrev': 'CA'},
    'maryland': {'name': 'Maryland', 'abbrev': 'MD'},
    'colorado': {'name': 'Colorado', 'abbrev': 'CO'},
    'florida': {'name': 'Florida', 'abbrev': 'FL'},
    'louisiana': {'name': 'Louisiana', 'abbrev': 'LA'},
    'new-york-state': {'name': 'New York', 'abbrev': 'NY'},
    'illinois': {'name': 'Illinois', 'abbrev': 'IL'},
    'ohio': {'name': 'Ohio', 'abbrev': 'OH'},
    'washington': {'name': 'Washington', 'abbrev': 'WA'},
    # V12.28 New state hubs
    'arizona': {'name': 'Arizona', 'abbrev': 'AZ'},
    'massachusetts': {'name': 'Massachusetts', 'abbrev': 'MA'},
    'new-jersey': {'name': 'New Jersey', 'abbrev': 'NJ'},
    'north-carolina': {'name': 'North Carolina', 'abbrev': 'NC'},
    'virginia': {'name': 'Virginia', 'abbrev': 'VA'},
    'connecticut': {'name': 'Connecticut', 'abbrev': 'CT'},
    'utah': {'name': 'Utah', 'abbrev': 'UT'},
    'wisconsin': {'name': 'Wisconsin', 'abbrev': 'WI'},
    'nevada': {'name': 'Nevada', 'abbrev': 'NV'},
    'iowa': {'name': 'Iowa', 'abbrev': 'IA'},
    'missouri': {'name': 'Missouri', 'abbrev': 'MO'},
    'pennsylvania': {'name': 'Pennsylvania', 'abbrev': 'PA'},
    'georgia': {'name': 'Georgia', 'abbrev': 'GA'},
    'indiana': {'name': 'Indiana', 'abbrev': 'IN'},
    'south-carolina': {'name': 'South Carolina', 'abbrev': 'SC'},
    'idaho': {'name': 'Idaho', 'abbrev': 'ID'},
    'michigan': {'name': 'Michigan', 'abbrev': 'MI'},
    'tennessee': {'name': 'Tennessee', 'abbrev': 'TN'},
    'nebraska': {'name': 'Nebraska', 'abbrev': 'NE'},
    'new-mexico': {'name': 'New Mexico', 'abbrev': 'NM'},
    'alabama': {'name': 'Alabama', 'abbrev': 'AL'},
    'kansas': {'name': 'Kansas', 'abbrev': 'KS'},
    'rhode-island': {'name': 'Rhode Island', 'abbrev': 'RI'},
    'minnesota': {'name': 'Minnesota', 'abbrev': 'MN'},
    'oregon': {'name': 'Oregon', 'abbrev': 'OR'},
    'kentucky': {'name': 'Kentucky', 'abbrev': 'KY'},
    'oklahoma': {'name': 'Oklahoma', 'abbrev': 'OK'},
    'mississippi': {'name': 'Mississippi', 'abbrev': 'MS'},
    'district-of-columbia': {'name': 'District of Columbia', 'abbrev': 'DC'},
    'arkansas': {'name': 'Arkansas', 'abbrev': 'AR'},
    'south-dakota': {'name': 'South Dakota', 'abbrev': 'SD'},
    'west-virginia': {'name': 'West Virginia', 'abbrev': 'WV'},
    'new-hampshire': {'name': 'New Hampshire', 'abbrev': 'NH'},
    'wyoming': {'name': 'Wyoming', 'abbrev': 'WY'},
    'maine': {'name': 'Maine', 'abbrev': 'ME'},
}


def get_state_data(state_slug):
    """Get aggregated data for a state hub page.
    V12.36: Fixed to use normalized city names for accurate counting.
    V12.51: Uses SQLite aggregation for efficiency.
    """
    if state_slug not in STATE_CONFIG:
        return None

    state_info = STATE_CONFIG[state_slug]
    state_abbrev = state_info['abbrev']

    # V12.32: Get all cities in this state, including auto-discovered bulk source cities
    state_cities = get_cities_by_state_auto(state_abbrev)

    # V12.51: Use SQL aggregation for city stats
    # V13.7: Include cities that were misassigned to other states using same heuristics
    conn = permitdb.get_connection()

    # Known cities for state correction heuristics (same as get_cities_with_data)
    KNOWN_OK_CITIES = {
        'oklahoma city', 'tulsa', 'norman', 'broken arrow', 'edmond',
        'lawton', 'moore', 'midwest city', 'enid', 'stillwater',
        'muskogee', 'bartlesville', 'owasso', 'shawnee', 'ponca city'
    }
    KNOWN_NV_CITIES = {
        'las vegas', 'henderson', 'reno', 'north las vegas', 'sparks',
        'carson city', 'elko', 'mesquite', 'boulder city', 'fernley'
    }

    if state_abbrev == 'TX':
        # TX gets its own permits PLUS misassigned OK/NV permits
        cursor = conn.execute("""
            SELECT city, state, COUNT(*) as permit_count, COALESCE(SUM(estimated_cost), 0) as total_value
            FROM permits
            WHERE state IN ('TX', 'OK', 'NV')
            GROUP BY city, state
        """)
    else:
        cursor = conn.execute("""
            SELECT city, state, COUNT(*) as permit_count, COALESCE(SUM(estimated_cost), 0) as total_value
            FROM permits
            WHERE state = ?
            GROUP BY city, state
        """, (state_abbrev,))

    # Build city stats from SQL
    city_permit_counts = {}
    city_values = {}
    city_display_names = {}

    for row in cursor:
        city_name = row['city'] or ''
        row_state = row['state'] if 'state' in row.keys() else state_abbrev
        if not city_name:
            continue

        norm_name = ' '.join(city_name.lower().split())

        # V13.7: Apply state correction heuristics for TX state hub
        if state_abbrev == 'TX':
            # Include TX cities directly
            if row_state == 'TX':
                pass  # Include
            # Include OK cities that are NOT in KNOWN_OK_CITIES (they're actually TX)
            elif row_state == 'OK' and norm_name not in KNOWN_OK_CITIES:
                pass  # Include
            # Include NV cities that are NOT in KNOWN_NV_CITIES (they're actually TX)
            elif row_state == 'NV' and norm_name not in KNOWN_NV_CITIES:
                pass  # Include
            else:
                continue  # Skip actual OK/NV cities
        else:
            # For other states, only include cities with matching state
            if row_state != state_abbrev:
                continue

        # Aggregate permit counts (in case same city appears with different states)
        if norm_name in city_permit_counts:
            city_permit_counts[norm_name] += row['permit_count']
            city_values[norm_name] += row['total_value']
        else:
            city_permit_counts[norm_name] = row['permit_count']
            city_values[norm_name] = row['total_value']

        # Keep track of display name (prefer title case)
        if norm_name not in city_display_names or city_name.istitle():
            city_display_names[norm_name] = city_name

    # Add counts to city info, matching by normalized name
    cities_with_data = []
    seen_norm_names = set()  # Prevent duplicates in output

    for c in state_cities:
        norm_name = ' '.join(c['name'].lower().split())
        if norm_name in seen_norm_names:
            continue  # Skip duplicate

        city_data = c.copy()
        city_data['permit_count'] = city_permit_counts.get(norm_name, 0)
        city_data['total_value'] = city_values.get(norm_name, 0)

        # Use best display name if available
        if norm_name in city_display_names:
            display_name = city_display_names[norm_name]
            # Prefer title case version
            if display_name.istitle():
                city_data['name'] = display_name

        if city_data['permit_count'] > 0:
            cities_with_data.append(city_data)
            seen_norm_names.add(norm_name)

    # V13.6: Add cities from DB that have permits but aren't in registry (e.g., Houston)
    # V13.7: Minimum threshold to avoid showing tiny cities on state hub
    MIN_STATE_HUB_PERMITS = 50
    for norm_name, permit_count in city_permit_counts.items():
        if norm_name not in seen_norm_names and permit_count >= MIN_STATE_HUB_PERMITS:
            display_name = city_display_names.get(norm_name, norm_name.title())
            slug = display_name.lower().replace(' ', '-').replace(',', '').replace('.', '')
            cities_with_data.append({
                'name': display_name,
                'state': state_abbrev,
                'slug': slug,
                'permit_count': permit_count,
                'total_value': city_values.get(norm_name, 0),
                'active': True,
            })
            seen_norm_names.add(norm_name)

    # Sort by permit count
    cities_with_data.sort(key=lambda x: x['permit_count'], reverse=True)

    # Calculate totals
    total_permits = sum(city_permit_counts.values())
    total_value = sum(city_values.values())

    return {
        'state_name': state_info['name'],
        'state_slug': state_slug,
        'cities': cities_with_data,
        'total_permits': total_permits,
        'total_value': total_value,
    }


@app.route('/permits/<state_slug>')
def state_or_city_landing(state_slug):
    """Route that handles both state hub pages and city landing pages."""
    # Check if it's a state slug first
    if state_slug in STATE_CONFIG:
        state_data = get_state_data(state_slug)
        if state_data and state_data['cities']:
            footer_cities = get_cities_with_data()

            # V14.0: Find blog posts for cities in this state
            state_blog_posts = []
            state_abbrev = STATE_CONFIG[state_slug]['abbrev'].lower()
            blog_dir = os.path.join(os.path.dirname(__file__), 'blog')
            for city in state_data['cities'][:20]:  # Check top 20 cities
                city_slug = city.get('slug', city['name'].lower().replace(' ', '-').replace(',', ''))
                blog_slug = f"building-permits-{city_slug}-{state_abbrev}-contractor-guide"
                blog_path = os.path.join(blog_dir, f"{blog_slug}.md")
                if os.path.exists(blog_path):
                    state_blog_posts.append({
                        'name': city['name'],
                        'url': f"/blog/{blog_slug}"
                    })

            return render_template('state_landing.html',
                                   footer_cities=footer_cities,
                                   state_blog_posts=state_blog_posts,
                                   **state_data)

    # Otherwise, fall through to city landing page logic
    return city_landing_inner(state_slug)


def _get_top_contractors_for_city(city_slug, limit=25, new_this_week_only=False):
    """V182 PR2: Top active contractors for a city's landing page.

    Returns [] if:
      - city fails city_passes_public_filter (bulk-misattribution guard)
      - no active contractor_profiles exist for the slug

    License-number-only entries render as "License #<raw>" per Wes's
    direction (tells the user a licensed contractor pulled the permit
    even when the company name wasn't captured).

    V251 F12: when new_this_week_only is True, restricts to contractors
    whose first_permit_date is within the last 7 days — highest-intent
    leads (ramping-up contractors hiring subs, buying tools).
    """
    from contractor_profiles import is_license_number, city_passes_public_filter
    conn = permitdb.get_connection()
    try:
        pc_row = conn.execute(
            "SELECT population, total_permits FROM prod_cities WHERE city_slug = ?",
            (city_slug,)
        ).fetchone()
        if pc_row and not city_passes_public_filter(
            pc_row['population'] or 0, pc_row['total_permits'] or 0
        ):
            return []
        _where_extra = ""
        if new_this_week_only:
            _where_extra = " AND first_permit_date >= date('now', '-7 days')"
        rows = conn.execute(f"""
            SELECT id, contractor_name_raw, contractor_name_normalized,
                   total_permits, permits_90d, permits_30d, primary_trade,
                   avg_project_value, phone, website, enrichment_status,
                   permit_frequency, first_permit_date, last_permit_date
            FROM contractor_profiles
            WHERE source_city_key = ? AND is_active = 1{_where_extra}
            ORDER BY total_permits DESC, permits_90d DESC
            LIMIT ?
        """, (city_slug, limit)).fetchall()

        # V251 F11: per-contractor violation count — "has a code violation at
        # any address where this contractor pulled a permit." Single aggregate
        # query (not per-row) joined through permits by address; keyed back to
        # the contractor's raw name so the loop below can just dict-lookup.
        # Limited to prod_city scope so Chicago doesn't scan national data.
        #
        # Perf note: the original query did UPPER(p.address) = UPPER(v.address)
        # which forced a full scan × full scan on cities with 20k+ permits
        # (Chicago). Dropped the UPPER — permit and violation addresses both
        # arrive already uppercased from their source APIs, and the cost of
        # missing a mixed-case edge case (→ zero flag rendered) is much less
        # than page-load timeouts on every city-page visit.
        violations_by_name = {}
        try:
            pc_id_row = conn.execute(
                "SELECT id FROM prod_cities WHERE city_slug = ?", (city_slug,)
            ).fetchone()
            pc_id = pc_id_row[0] if pc_id_row else None
            if pc_id and rows:
                rendered_names = tuple(set(
                    n for r in rows for n in (r['contractor_name_raw'],) if n
                ))
                if rendered_names:
                    ph = ','.join(['?'] * len(rendered_names))
                    viol_rows = conn.execute(f"""
                        SELECT p.contractor_name, COUNT(DISTINCT v.id) as n
                        FROM violations v
                        JOIN permits p ON p.address = v.address
                                      AND p.prod_city_id = ?
                        WHERE v.prod_city_id = ?
                          AND p.contractor_name IN ({ph})
                        GROUP BY p.contractor_name
                    """, (pc_id, pc_id) + rendered_names).fetchall()
                    for v in viol_rows:
                        violations_by_name[v[0]] = v[1]
        except Exception as e:
            print(f"[V251 F11] violation cross-ref failed for {city_slug}: {e}", flush=True)

        out = []
        # V251 F5: compute velocity + recency bucket in Python so the template
        # stays dumb. velocity: red ≥10/mo, orange 5–9, blue 1–4, '' otherwise.
        # recency: green ≤7 days, yellow ≤30 days, gray older/unknown. "new"
        # flag = first_permit_date within last 7 days (highest-intent lead).
        from datetime import date, datetime as _dt
        today = date.today()
        for r in rows:
            norm = r['contractor_name_normalized']
            raw = r['contractor_name_raw']
            is_license = is_license_number(norm)
            p30 = r['permits_30d'] or 0
            if p30 >= 10:
                velocity_color = 'red'
            elif p30 >= 5:
                velocity_color = 'orange'
            elif p30 >= 1:
                velocity_color = 'blue'
            else:
                velocity_color = ''
            def _days_since(s):
                if not s: return None
                try:
                    return (today - _dt.strptime(s[:10], '%Y-%m-%d').date()).days
                except Exception:
                    return None
            last_age = _days_since(r['last_permit_date'])
            first_age = _days_since(r['first_permit_date'])
            if last_age is not None and last_age <= 7:
                recency = 'green'
            elif last_age is not None and last_age <= 30:
                recency = 'yellow'
            else:
                recency = 'gray'
            is_new = first_age is not None and first_age <= 7
            # V251 F21: composite lead score (0-5) from signals we already
            # compute. Buyers scan for "Hot Lead" pills to prioritize who to
            # call first. Weights chosen so the floor is "a legit contractor"
            # and the ceiling is "actively pulling permits AND reachable".
            #   recency: green +2, yellow +1
            #   velocity: p30 ≥10 +2, ≥5 +1
            #   phone: +1
            # Ties broken in favor of phone presence (since sellable leads need
            # phones). NEW contractors (first permit within 7d) get a +1 bump
            # since they're the highest-intent prospects in the pipeline.
            _score = 0
            if recency == 'green': _score += 2
            elif recency == 'yellow': _score += 1
            if p30 >= 10: _score += 2
            elif p30 >= 5: _score += 1
            if r['phone']: _score += 1
            if is_new: _score += 1
            _score = min(_score, 5)
            if _score >= 4:
                score_tier = 'hot'
            elif _score >= 2:
                score_tier = 'warm'
            else:
                score_tier = 'cold'
            out.append({
                'id': r['id'],
                'display_name': f"License #{raw}" if is_license else raw,
                'is_license_number': is_license,
                'total_permits': r['total_permits'],
                'permits_90d': r['permits_90d'],
                'permits_30d': p30,
                'primary_trade': r['primary_trade'],
                'avg_project_value': r['avg_project_value'],
                'phone': r['phone'],
                'website': r['website'],
                'enriched': r['enrichment_status'] == 'enriched',
                'permit_frequency': r['permit_frequency'],
                'velocity_color': velocity_color,
                'recency': recency,
                'is_new': is_new,
                'last_permit_date': r['last_permit_date'],
                'violations_count': violations_by_name.get(raw, 0),  # V251 F11
                'lead_score': _score,  # V251 F21
                'score_tier': score_tier,  # V251 F21
            })
        # Prefer real business names on the city page — NYC in particular
        # returns a lot of DOB license-number-only rows in the top-volume
        # band (e.g. "License #626264") which look like garbage next to a
        # proper business name. Keep them only when we don't have enough
        # real names to fill a table of 10 — otherwise sort them to the
        # back and cap the table. Don't drop the underlying data so
        # contractor-profile detail links still resolve.
        real_name_rows = [r for r in out if not r.get('is_license_number')]
        if len(real_name_rows) >= 10:
            return real_name_rows
        return real_name_rows + [r for r in out if r.get('is_license_number')]
    finally:
        conn.close()


def _get_market_insights(prod_city_id=None, city_name=None, city_state=None):
    """V236 PR#5: compute a data-driven blurb for the city page —
    permits filed in the last 30 days, top-3 trade categories, and
    average project value. Rendered as a paragraph on each city
    landing page so pages aren't all identical template boilerplate
    (Google Ads Quality Score + SEO anti-doorway-page).

    Returns None if we can't compute (falls back to static prose).
    """
    if not prod_city_id and not city_name:
        return None
    _conn = permitdb.get_connection()
    try:
        if prod_city_id:
            count_row = _conn.execute("""
                SELECT COUNT(*) as cnt, COALESCE(AVG(CASE WHEN estimated_cost > 0
                                                        THEN estimated_cost END), 0) as avg_cost
                FROM permits
                WHERE prod_city_id = ?
                  AND filing_date >= date('now', '-30 days')
            """, (prod_city_id,)).fetchone()
            trade_rows = _conn.execute("""
                SELECT COALESCE(NULLIF(trade_category,''), 'General Construction') as trade,
                       COUNT(*) as cnt
                FROM permits
                WHERE prod_city_id = ?
                  AND filing_date >= date('now', '-30 days')
                GROUP BY trade_category
                ORDER BY cnt DESC
                LIMIT 3
            """, (prod_city_id,)).fetchall()
        else:
            count_row = _conn.execute("""
                SELECT COUNT(*) as cnt, COALESCE(AVG(CASE WHEN estimated_cost > 0
                                                        THEN estimated_cost END), 0) as avg_cost
                FROM permits
                WHERE city = ? AND state = ?
                  AND filing_date >= date('now', '-30 days')
            """, (city_name, city_state or '')).fetchone()
            trade_rows = _conn.execute("""
                SELECT COALESCE(NULLIF(trade_category,''), 'General Construction') as trade,
                       COUNT(*) as cnt
                FROM permits
                WHERE city = ? AND state = ?
                  AND filing_date >= date('now', '-30 days')
                GROUP BY trade_category
                ORDER BY cnt DESC
                LIMIT 3
            """, (city_name, city_state or '')).fetchall()
    except Exception:
        return None
    if not count_row or not count_row['cnt']:
        return None
    trades = [(r['trade'], r['cnt']) for r in trade_rows]
    avg_cost = count_row['avg_cost']
    return {
        'permits_30d': count_row['cnt'],
        'top_trades': trades,
        'avg_project_value': int(avg_cost) if avg_cost else None,
    }


def city_landing_inner(city_slug):
    """Render SEO-optimized city landing page."""
    # V157: Slug aliases for cities where URL slug differs from DB slug.
    # V221 T1B: keep the original request slug for canonical + display; use
    # the aliased `db_slug` only for internal DB lookups. Previously the
    # whole function saw `city_slug='chicago-il'` when the visitor typed
    # `/permits/chicago`, so the canonical link pointed at
    # `/permits/chicago-il` — a canonical mismatch Google flagged.
    _INNER_ALIASES = {
        'new-york': 'new-york-city',
        'chicago': 'chicago-il',
        'washington-dc': 'washington-dc',
        'little-rock': 'little-rock-ar',
        'mesa': 'mesa-az-accela',
    }
    request_slug = city_slug
    db_slug = _INNER_ALIASES.get(city_slug, city_slug)
    city_slug = db_slug  # legacy var name used below for internal queries

    # V251 F2: URL-param filters for the permits table (zip / trade / days).
    # V251 F8: added min_value to the same filter machinery so "$100k+" is
    # reachable in one query param. Whitelisted to prevent arbitrary ORDER
    # injection when the value is concatenated into SQL below.
    _filter_zip = (request.args.get('zip') or '').strip()[:5]
    _filter_trade = (request.args.get('trade') or '').strip()
    try:
        _filter_days = int(request.args.get('days') or 0)
    except (TypeError, ValueError):
        _filter_days = 0
    if _filter_days not in (0, 7, 30, 90, 180, 365):
        _filter_days = 0
    try:
        _filter_min_value = int(request.args.get('min_value') or 0)
    except (TypeError, ValueError):
        _filter_min_value = 0
    if _filter_min_value not in (0, 10000, 50000, 100000, 500000, 1000000):
        _filter_min_value = 0
    _filters_active = bool(_filter_zip or _filter_trade or _filter_days or _filter_min_value)
    filtered_permit_count = None  # V251 F2: set below in prod-id branch

    # V15: Check prod_cities status for this city
    is_prod_city = False
    prod_city_status = None
    city_freshness = 'fresh'
    newest_permit_date = None

    try:
        if permitdb.prod_cities_table_exists():
            is_prod_city, prod_city_status = permitdb.is_prod_city(city_slug)

            # V18: Check if city is paused due to stale data
            if prod_city_status == 'paused':
                conn = permitdb.get_connection()
                row = conn.execute("""
                    SELECT pause_reason, newest_permit_date, city, state
                    FROM prod_cities WHERE city_slug = ?
                """, (city_slug,)).fetchone()
                if row and row['pause_reason'] == 'stale_data':
                    # Show a "data updating" page instead of normal city page
                    return render_template('city_paused.html',
                        city_name=row['city'],
                        state=row['state'],
                        last_updated=row['newest_permit_date'],
                        canonical_url=f"{SITE_URL}/permits/{request_slug}",
                        robots="noindex, follow"
                    )

            # V18: Get freshness info for stale indicator
            if is_prod_city:
                conn = permitdb.get_connection()
                row = conn.execute("""
                    SELECT data_freshness, newest_permit_date
                    FROM prod_cities WHERE city_slug = ?
                """, (city_slug,)).fetchone()
                if row:
                    city_freshness = row['data_freshness'] or 'fresh'
                    newest_permit_date = row['newest_permit_date']
    except Exception as e:
        print(f"[V15] Error checking prod_cities: {e}")

    # Check for SEO config, or create fallback from city_configs
    if city_slug in CITY_SEO_CONFIG:
        config = dict(CITY_SEO_CONFIG[city_slug])
        # V231 P2-10: ensure ", <state>" appears in meta_title + description
        # for SEO. The 51 hardcoded CITY_SEO_CONFIG entries were written
        # city-only ("Los Angeles Building Permits …") before the fallback
        # path standardized on "{City}, {State} Building Permits …". This
        # normalizes at read time so every route goes through one format.
        _city_display = config.get('name', '')
        _state_abbrev = config.get('state', '')
        if _city_display and _state_abbrev:
            _city_comma_state = f"{_city_display}, {_state_abbrev}"
            if (_city_comma_state not in config.get('meta_title', '')
                    and config.get('meta_title')):
                config['meta_title'] = config['meta_title'].replace(
                    f"{_city_display} Building", f"{_city_comma_state} Building", 1
                )
            if (_city_comma_state not in config.get('meta_description', '')
                    and config.get('meta_description')):
                config['meta_description'] = config['meta_description'].replace(
                    f" in {_city_display}.", f" in {_city_comma_state}.", 1
                )
    else:
        # V12.32: Try both explicit configs AND auto-discovered bulk source cities
        city_key, city_config = get_city_by_slug_auto(city_slug)
        if not city_config:
            # V18: Slug fallback - handle city-state format (e.g., san-antonio-tx -> san-antonio)
            # Check if slug ends with a state abbreviation suffix
            import re
            state_suffix_match = re.match(r'^(.+)-([a-z]{2})$', city_slug)
            if state_suffix_match:
                bare_slug = state_suffix_match.group(1)
                state_suffix = state_suffix_match.group(2).upper()
                # Verify it's a valid US state abbreviation
                VALID_STATE_ABBREVS = {
                    'AL', 'AK', 'AZ', 'AR', 'CA', 'CO', 'CT', 'DE', 'DC', 'FL', 'GA',
                    'HI', 'ID', 'IL', 'IN', 'IA', 'KS', 'KY', 'LA', 'ME', 'MD', 'MA',
                    'MI', 'MN', 'MS', 'MO', 'MT', 'NE', 'NV', 'NH', 'NJ', 'NM', 'NY',
                    'NC', 'ND', 'OH', 'OK', 'OR', 'PA', 'RI', 'SC', 'SD', 'TN', 'TX',
                    'UT', 'VT', 'VA', 'WA', 'WV', 'WI', 'WY'
                }
                if state_suffix in VALID_STATE_ABBREVS:
                    # Try the bare slug
                    bare_key, bare_config = get_city_by_slug_auto(bare_slug)
                    if bare_config:
                        # Redirect to canonical bare slug URL
                        return redirect(f'/permits/{bare_slug}', code=301)
            return render_city_not_found(city_slug)
        # Generate basic SEO config with properly formatted city name
        display_name = format_city_name(city_config["name"])
        config = {
            "name": display_name,
            "raw_name": city_config["name"],  # For filtering permits
            "state": city_config["state"],
            "meta_title": f"{display_name}, {city_config['state']} Building Permits & Contractor Leads | PermitGrab",
            "meta_description": f"Browse active building permits in {display_name}, {city_config['state']}. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
            "seo_content": f"""
                <p>Track new building permits in {display_name}, {city_config['state']}. PermitGrab delivers fresh permit data daily, helping contractors find quality leads across the region.</p>
                <p>Access permit data including project values, contact information, and trade categories. Start browsing {display_name} construction permits today.</p>
            """
        }

    # V161: Use prod_city_id for permit queries (fixes NYC name mismatch)
    filter_name = config.get('raw_name', config['name'])
    filter_state = config.get('state', '')
    conn = permitdb.get_connection()

    # V161: Look up prod_city_id for accurate FK-based queries
    _prod_city_id = None
    try:
        _pc_row = conn.execute("SELECT id FROM prod_cities WHERE city_slug = ?", (city_slug,)).fetchone()
        if _pc_row:
            _prod_city_id = _pc_row['id'] if isinstance(_pc_row, dict) else _pc_row[0]
    except Exception:
        pass

    # V161: Query by prod_city_id if available, fall back to city name.
    # V231 P0-7: LIMIT 50 (was 100). Template only renders 25 rows and
    # state_city_landing already uses 50; no reason to haul back 100.
    if _prod_city_id:
        # V251 F2: build the filter clause shared by the permits query and
        # the filtered-count display. Stats (permit_count/total_value/high)
        # always reflect the UNFILTERED city totals so template gates like
        # `{% if permit_count > 0 %}` don't collapse the filter bar when a
        # filter combo returns 0 rows. `filtered_permit_count` is the
        # filter-aware count shown in the "Showing N permits for X" label.
        _filter_sql = ""
        _filter_params = [_prod_city_id]
        if _filter_zip:
            _filter_sql += " AND zip = ?"
            _filter_params.append(_filter_zip)
        if _filter_trade:
            _filter_sql += " AND trade_category = ?"
            _filter_params.append(_filter_trade)
        if _filter_days:
            _filter_sql += f" AND COALESCE(filing_date, issued_date, date) >= date('now', '-{_filter_days} days')"
        if _filter_min_value:
            # Whitelisted int above — safe to inline.
            _filter_sql += f" AND estimated_cost >= {_filter_min_value}"
        stats_row = conn.execute("""
            SELECT COUNT(*) as permit_count,
                   COALESCE(SUM(estimated_cost), 0) as total_value,
                   COUNT(CASE WHEN estimated_cost >= 100000 THEN 1 END) as high_value_count
            FROM permits WHERE prod_city_id = ?
        """, (_prod_city_id,)).fetchone()
        if _filters_active:
            _filtered_row = conn.execute(f"""
                SELECT COUNT(*) as n FROM permits WHERE prod_city_id = ?{_filter_sql}
            """, _filter_params).fetchone()
            filtered_permit_count = _filtered_row['n'] if _filtered_row else 0
        else:
            filtered_permit_count = None
        # V247 P1: was ORDER BY estimated_cost DESC, which meant the "Recent
        # Permits" table showed the 50 most-expensive historical permits, not
        # the 50 most recent. On Chicago this surfaced a batch of Oct 2025
        # high-value self-cert filings and made the page look 6 months stale
        # even after fresh collections. Sort by filing_date so "Recent" is
        # actually recent; tiebreak by estimated_cost for stable ordering.
        cursor = conn.execute(f"""
            SELECT * FROM permits WHERE prod_city_id = ?{_filter_sql}
            ORDER BY COALESCE(filing_date, issued_date, date) DESC, estimated_cost DESC
            LIMIT 50
        """, _filter_params)
    else:
        if filter_state:
            state_clause = " AND state = ?"
            state_params = (filter_name, filter_state)
        else:
            state_clause = ""
            state_params = (filter_name,)
        stats_row = conn.execute(f"""
            SELECT COUNT(*) as permit_count,
                   COALESCE(SUM(estimated_cost), 0) as total_value,
                   COUNT(CASE WHEN estimated_cost >= 100000 THEN 1 END) as high_value_count
            FROM permits WHERE city = ?{state_clause}
        """, state_params).fetchone()
        # V247 P1: sort by date, not cost — see prod_city_id branch above.
        cursor = conn.execute(f"""
            SELECT * FROM permits WHERE city = ?{state_clause}
            ORDER BY COALESCE(filing_date, issued_date, date) DESC, estimated_cost DESC
            LIMIT 50
        """, state_params)

    permit_count = stats_row['permit_count']
    total_value = stats_row['total_value']
    high_value_count = stats_row['high_value_count']
    city_permits = [dict(row) for row in cursor]

    # V223 T2: freshness badge fallback. Some cities (Houston, Dallas, SA,
    # Austin) have prod_cities.newest_permit_date = NULL even though real
    # permits exist — the badge template hides itself in that case. Pull
    # MAX(filing_date) straight from the permits table as a fallback so
    # every city with any permit data shows an "Updated as of" line.
    if permit_count > 0 and not newest_permit_date:
        try:
            if _prod_city_id:
                _max_row = conn.execute(
                    "SELECT MAX(COALESCE(NULLIF(filing_date,''),NULLIF(issued_date,''),NULLIF(date,''))) "
                    "FROM permits WHERE prod_city_id = ?",
                    (_prod_city_id,),
                ).fetchone()
            else:
                _max_row = conn.execute(f"""
                    SELECT MAX(COALESCE(NULLIF(filing_date,''),NULLIF(issued_date,''),NULLIF(date,'')))
                    FROM permits WHERE city = ?{state_clause}
                """, state_params).fetchone()
            if _max_row and _max_row[0]:
                newest_permit_date = _max_row[0]
        except Exception:
            pass

    # V223 T1: clean up literal "None" values in permit rows before the
    # Jinja2 template renders them. SQLite returns NULL for missing
    # fields, which Python stores as None, which {{ permit.x }} renders
    # as the literal string "None". Replace with an em-dash here so the
    # table shows "—" for missing values instead of spelling out "None".
    for _p in city_permits:
        for _k, _v in list(_p.items()):
            if _v is None or _v == 'None':
                _p[_k] = '—'

    # V15: noindex for non-prod cities or empty cities
    # If prod_cities table is active and this city isn't in it, treat as coming soon
    if permitdb.prod_cities_table_exists() and not is_prod_city:
        robots_directive = "noindex, follow"
        is_coming_soon = True
    else:
        # V12.5 → V236 PR#6: noindex thin pages. Pre-V236 only gated at
        # 0 permits, but Google de-indexes programmatic pages with ≤20
        # records worth of content. Lifting the bar to 20 keeps our
        # index clean and concentrates crawl budget on pages that have
        # enough data to rank for "<city> building permits" queries.
        robots_directive = "noindex, follow" if permit_count < 20 else "index, follow"
        # V12.11: Coming Soon flag for empty cities
        is_coming_soon = permit_count == 0

    # V161: Trade breakdown — use prod_city_id if available
    if _prod_city_id:
        trade_cursor = conn.execute("""
            SELECT COALESCE(trade_category, 'Other') as trade, COUNT(*) as cnt
            FROM permits WHERE prod_city_id = ? GROUP BY trade_category
        """, (_prod_city_id,))
    else:
        trade_cursor = conn.execute("""
            SELECT COALESCE(trade_category, 'Other') as trade, COUNT(*) as cnt
            FROM permits WHERE city = ? GROUP BY trade_category
        """, (filter_name,))
    trade_breakdown = {row['trade'] or 'Other': row['cnt'] for row in trade_cursor}

    # Permits already sorted by value from SQL
    sorted_permits = city_permits

    # V12.17: Add "is_new" flag for permits filed in last 7 days
    seven_days_ago = datetime.now() - timedelta(days=7)
    for p in sorted_permits:
        filing_date_str = p.get('filing_date', '')
        if filing_date_str:
            try:
                filing_date = datetime.strptime(filing_date_str, '%Y-%m-%d')
                p['is_new'] = filing_date >= seven_days_ago
            except (ValueError, TypeError):
                p['is_new'] = False
        else:
            p['is_new'] = False

    # V12.9: Calculate alternative stats for cities without value data
    # V12.51: Use SQL for accurate counts
    new_this_month = permit_count  # Fallback to total count
    unique_contractors = 0
    if total_value == 0:
        thirty_days_ago = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
        recent_row = conn.execute("""
            SELECT COUNT(*) as cnt FROM permits
            WHERE city = ? AND filing_date >= ?
        """, (filter_name, thirty_days_ago)).fetchone()
        new_this_month = recent_row['cnt'] if recent_row else permit_count

        contractor_row = conn.execute("""
            SELECT COUNT(DISTINCT LOWER(COALESCE(contractor_name, ''))) as cnt
            FROM permits WHERE city = ? AND contractor_name IS NOT NULL AND contractor_name != ''
        """, (filter_name,)).fetchone()
        unique_contractors = contractor_row['cnt'] if contractor_row and contractor_row['cnt'] > 0 else '50+'

    # V17c: Freshness badge — compute human-readable "last updated" time
    last_collected_row = conn.execute("""
        SELECT MAX(collected_at) as latest FROM permits WHERE city = ?
    """, (filter_name,)).fetchone()
    last_collected = None
    if last_collected_row and last_collected_row['latest']:
        try:
            latest_dt = datetime.strptime(last_collected_row['latest'], '%Y-%m-%d %H:%M:%S')
            delta = datetime.now() - latest_dt
            if delta.total_seconds() < 3600:
                last_collected = f"{int(delta.total_seconds() / 60)} minutes ago"
            elif delta.total_seconds() < 86400:
                last_collected = f"{int(delta.total_seconds() / 3600)} hours ago"
            elif delta.days == 1:
                last_collected = "yesterday"
            elif delta.days < 7:
                last_collected = f"{delta.days} days ago"
            else:
                last_collected = latest_dt.strftime('%b %d, %Y')
        except (ValueError, TypeError):
            last_collected = None

    # V12.60: Moved current_state assignment before its first use (was at line 5626)
    current_state = config.get('state', '')

    # V17d: Related blog articles for cross-linking SEO
    # V13.7: Fixed H6 - only show city-specific articles, not state-level
    related_articles = []
    try:
        all_posts = get_all_blog_posts()
        city_lower = config['name'].lower()
        # Only match posts that specifically mention this city (not just the state)
        for post in all_posts:
            title_lower = post.get('title', '').lower()
            keywords_lower = post.get('keywords', '').lower()
            slug_lower = post.get('slug', '').lower()
            # Match city name in title, keywords, or slug
            if city_lower in title_lower or city_lower in keywords_lower or city_lower in slug_lower:
                related_articles.append(post)
        # If no city-specific matches, add general articles (not state-level)
        if not related_articles:
            general_slugs = ['what-is-a-building-permit', 'how-to-find-construction-leads', 'construction-leads-from-building-permits']
            for post in all_posts:
                if post.get('slug') in general_slugs:
                    related_articles.append(post)
        related_articles = related_articles[:3]  # Max 3 articles
    except Exception:
        pass

    # V12.17: Other cities for footer links - sorted by permit volume
    cities_by_volume = get_cities_with_data()  # Pre-sorted by permit count descending
    other_cities = [c for c in cities_by_volume if c['slug'] != city_slug]

    # V12.17: Nearby cities sorted by permit volume (not alphabetical)
    # V224 T2: drop entries with empty city or zero permits — they were
    # rendering as blank tiles with "0 permits" (up to 57 per page on LA),
    # making the whole grid look empty. A nearby-city tile that won't
    # surface useful data is worse than no tile at all.
    def _nearby_ok(c):
        return bool(c.get('city') or c.get('name')) and (c.get('total_permits') or c.get('permit_count') or 0) > 0
    nearby_cities = [c for c in cities_by_volume
                     if _nearby_ok(c)
                     and c.get('state') == current_state
                     and c['slug'] != city_slug]
    # If fewer than 6 same-state cities, add top cities from other states
    if len(nearby_cities) < 6:
        other_state_cities = [c for c in cities_by_volume
                              if _nearby_ok(c) and c.get('state') != current_state][:6 - len(nearby_cities)]
        nearby_cities = nearby_cities + other_state_cities
    nearby_cities = nearby_cities[:12]  # cap so the grid stays visually tight

    # V14.0: Top neighborhoods by zip code for city enrichment
    top_neighborhoods = []
    try:
        zip_cursor = conn.execute("""
            SELECT zip_code, COUNT(*) as permit_count
            FROM permits
            WHERE city = ? AND zip_code IS NOT NULL AND zip_code != ''
            GROUP BY zip_code
            ORDER BY permit_count DESC
            LIMIT 5
        """, (filter_name,))
        for row in zip_cursor:
            top_neighborhoods.append({
                'zip_code': row['zip_code'],
                'permit_count': row['permit_count']
            })
    except Exception:
        pass

    # V14.0: State info for internal linking
    state_slug = None
    state_name = current_state
    for slug, info in STATE_CONFIG.items():
        if info['abbrev'] == current_state:
            state_slug = slug
            state_name = info['name']
            break

    # V14.0: City blog URL if exists
    city_blog_url = None
    if current_state:
        blog_slug = f"building-permits-{city_slug}-{current_state.lower()}-contractor-guide"
        blog_path = os.path.join(os.path.dirname(__file__), 'blog', f"{blog_slug}.md")
        if os.path.exists(blog_path):
            city_blog_url = f"/blog/{blog_slug}"

    # V14.0: Top trades for Related Content links
    top_trades = [
        {'name': 'Plumbing', 'slug': 'plumbing'},
        {'name': 'Electrical', 'slug': 'electrical'},
        {'name': 'HVAC', 'slug': 'hvac'},
        {'name': 'Roofing', 'slug': 'roofing'},
        {'name': 'General Construction', 'slug': 'general-construction'},
    ]

    # V182 PR2: top contractors for this city (empty if city fails
    # population sanity filter or has no active profiles).
    # V251 F12: ?new_week=1 → only contractors whose first permit ever
    # was within the last 7 days. Highest-intent leads.
    _new_week_only = request.args.get('new_week') == '1'
    top_contractors = _get_top_contractors_for_city(
        city_slug, limit=25, new_this_week_only=_new_week_only
    )

    # V251 F13: zip heatmap — top 10 zip codes by permit volume with
    # contractor counts and 90-day permit counts for trend signal.
    # Skipped silently when the city's permit feed doesn't populate zip
    # (Chicago, Orlando, Phoenix data sources don't). Template renders a
    # grid with color intensity proportional to max_permits_in_zip.
    zip_heatmap = []
    if _prod_city_id:
        try:
            zip_heatmap = [dict(r) for r in conn.execute("""
                SELECT zip,
                       COUNT(*) as permits_total,
                       COUNT(CASE WHEN COALESCE(filing_date, issued_date, date)
                                  >= date('now', '-90 days') THEN 1 END) as permits_90d,
                       COUNT(DISTINCT contractor_name) as contractors
                FROM permits
                WHERE prod_city_id = ? AND zip IS NOT NULL AND zip != ''
                GROUP BY zip
                ORDER BY permits_total DESC
                LIMIT 10
            """, (_prod_city_id,)).fetchall()]
        except Exception as e:
            print(f"[V251 F13] zip heatmap query failed for {city_slug}: {e}", flush=True)

    # V226 T10: compute freshness age in days so the template can bucket
    # the badge into fresh / aging / stale. None when we don't have a date.
    _freshness_age_days = None
    if newest_permit_date:
        try:
            _fd = newest_permit_date[:10]
            _freshness_age_days = (datetime.now().date()
                                   - datetime.strptime(_fd, '%Y-%m-%d').date()).days
        except Exception:
            _freshness_age_days = None

    # V236 PR#5: per-city market insights for the data-driven paragraph.
    market_insights = _get_market_insights(
        prod_city_id=_prod_city_id,
        city_name=filter_name,
        city_state=filter_state,
    )

    # V251 F2: available zips and trades for the filter dropdowns. Narrowed
    # to the top 20 most-common zips so the select stays usable (some big
    # cities have 100+ zips). Use a fresh connection (the page-scoped `conn`
    # already has an open cursor from the permits SELECT above, and on some
    # drivers that wedges any further exec on the same handle). Log failures
    # loudly so they don't ghost into empty dropdowns.
    available_zips = []
    available_trades = []
    if _prod_city_id:
        try:
            _fconn = permitdb.get_connection()
            available_zips = [r[0] for r in _fconn.execute(
                """SELECT zip, COUNT(*) as c FROM permits
                   WHERE prod_city_id = ? AND zip IS NOT NULL AND zip != ''
                   GROUP BY zip ORDER BY c DESC LIMIT 20""",
                (_prod_city_id,)
            ).fetchall()]
            available_trades = [r[0] for r in _fconn.execute(
                """SELECT trade_category, COUNT(*) as c FROM permits
                   WHERE prod_city_id = ? AND trade_category IS NOT NULL AND trade_category != ''
                   GROUP BY trade_category ORDER BY c DESC LIMIT 15""",
                (_prod_city_id,)
            ).fetchall()]
        except Exception as e:
            print(f"[V251 F2] filter dropdown query failed for {city_slug}: {e}", flush=True)

    return render_template(
        'city_landing_v77.html',  # V175: Unified to one template (was city_landing.html)
        city_name=config['name'],
        city_slug=city_slug,
        state_abbrev=current_state,  # V77 template expects state_abbrev, not city_state
        city_state=current_state,
        meta_title=config['meta_title'],
        meta_description=config['meta_description'],
        seo_content=config['seo_content'],
        canonical_url=f"{SITE_URL}/permits/{request_slug}",
        robots_directive=robots_directive,  # V12.5: noindex empty pages
        permit_count=permit_count,
        total_value=total_value,
        high_value_count=high_value_count,
        new_this_month=new_this_month,
        unique_contractors=unique_contractors,
        trade_breakdown=trade_breakdown,
        # V231 P0-7: was `permits=sorted_permits` — a 100-row list that
        # the template never iterates directly (only recent_permits[:25]
        # is rendered). Passing 100 rows per request doubled memory for
        # every city page and bloated the Jinja context for no benefit.
        permits=sorted_permits[:50],
        top_contractors=top_contractors,  # V182 PR2
        other_cities=other_cities,
        nearby_cities=nearby_cities,  # V12.11: Same-state cities for internal linking
        current_year=datetime.now().year,
        current_date=datetime.now().strftime('%Y-%m-%d'),
        current_week_start=(datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d'),  # V209-3: for NEW badge
        freshness_age_days=_freshness_age_days,  # V226 T10: fresh/aging/stale bucketing
        last_collected=last_collected,  # V17c: Freshness badge
        related_articles=related_articles,  # V17d: Cross-linked blog articles
        is_coming_soon=is_coming_soon,  # V12.11: Coming Soon badge
        top_neighborhoods=top_neighborhoods,  # V14.0: Top zip codes
        state_slug=state_slug,  # V14.0: For state hub link
        state_name=state_name,  # V14.0: For display
        city_blog_url=city_blog_url,  # V14.0: City guide link
        top_trades=top_trades,  # V14.0: Trade page links
        data_freshness=city_freshness,  # V18: stale indicator
        newest_permit_date=newest_permit_date,  # V18: for "last updated" display
        # V175: Additional vars expected by city_landing_v77.html
        recent_permits=sorted_permits[:50],
        permit_types=trade_breakdown,
        blog_posts=related_articles,
        footer_cities=other_cities[:20],
        earliest_date=None,
        latest_date=None,
        total_permits=permit_count,
        last_collection=last_collected,
        is_active=not is_coming_soon,
        violations=[],
        violations_count=0,
        market_insights=market_insights,  # V236 PR#5
        # V251 F2: filter dropdown context
        available_zips=available_zips,
        available_trades=available_trades,
        filter_zip=_filter_zip,
        filter_trade=_filter_trade,
        filter_days=_filter_days,
        filter_min_value=_filter_min_value,  # V251 F8
        filters_active=_filters_active,
        filtered_permit_count=filtered_permit_count,
        new_week_only=_new_week_only,  # V251 F12
        zip_heatmap=zip_heatmap,  # V251 F13
    )


@app.route('/permits/<state_slug>/<city_slug>')
def state_city_landing(state_slug, city_slug):
    """V77: Render SEO-optimized city landing page with state/city URL format.

    URL format: /permits/{state}/{city} e.g., /permits/texas/fort-worth

    This is the primary city page route for SEO. Each city page targets
    "[city] building permits" keywords for contractors.
    """
    # V231 P1-9: state-slug aliases. STATE_CONFIG keys "New York" as
    # 'new-york-state' because the bare 'new-york' slug is reserved for
    # NYC in the 1-segment city route. But the 2-segment
    # /permits/<state>/<city> pattern sends 'new-york' as the state —
    # so without this alias every /permits/new-york/new-york-city hit
    # 404'd. Same family of aliases a human would naturally type.
    _STATE_SLUG_ALIASES = {
        'new-york': 'new-york-state',
        'ny': 'new-york-state',
    }
    state_slug_key = _STATE_SLUG_ALIASES.get(state_slug, state_slug)

    # Check if state_slug is a valid state
    if state_slug_key not in STATE_CONFIG:
        # Not a valid state — fall through to city/trade route
        # by calling city_trade_landing directly
        return city_trade_landing(state_slug, city_slug)

    state_abbrev = STATE_CONFIG[state_slug_key]['abbrev']
    state_name = STATE_CONFIG[state_slug_key]['name']

    # V156: Slug aliases for cities where URL slug differs from DB slug
    _SLUG_ALIASES = {
        'new-york': 'new-york-city',
        'chicago': 'chicago-il',
        'washington': 'washington-dc',
        'washington-dc': 'washington-dc',
        'little-rock': 'little-rock-ar',
        'mesa': 'mesa-az-accela',
    }
    city_slug = _SLUG_ALIASES.get(city_slug, city_slug)

    # Look up city in prod_cities first (authoritative source)
    conn = permitdb.get_connection()
    city_row = conn.execute("""
        SELECT id, city, state, city_slug, source_id, source_type, total_permits,
               newest_permit_date, last_collection, data_freshness, status
        FROM prod_cities
        WHERE city_slug = ? AND state = ?
    """, (city_slug, state_abbrev)).fetchone()

    if not city_row:
        # Try without state filter (some cities might not have state stored correctly)
        city_row = conn.execute("""
            SELECT id, city, state, city_slug, source_id, source_type, total_permits,
                   newest_permit_date, last_collection, data_freshness, status
            FROM prod_cities WHERE city_slug = ?
        """, (city_slug,)).fetchone()

    if not city_row:
        # Fall back to CITY_REGISTRY lookup
        city_key, city_config = get_city_by_slug_auto(city_slug)
        if not city_config:
            return render_city_not_found(city_slug)
        city_name = city_config['name']
        city_state = city_config.get('state', state_abbrev)
        total_permits = 0
        newest_permit_date = None
        last_collection = None
        data_freshness = 'no_data'
        is_active = city_config.get('active', False)
    else:
        city_name = city_row['city']
        city_state = city_row['state']
        total_permits = city_row['total_permits'] or 0
        newest_permit_date = city_row['newest_permit_date']
        last_collection = city_row['last_collection']
        data_freshness = city_row['data_freshness'] or 'no_data'
        is_active = city_row['status'] == 'active'

    # V160: Get recent permits using prod_city_id FK (not city name string match)
    prod_city_id = city_row['id'] if city_row else None
    if prod_city_id:
        permits_cursor = conn.execute("""
            SELECT * FROM permits
            WHERE prod_city_id = ?
            ORDER BY filing_date DESC, estimated_cost DESC
            LIMIT 50
        """, (prod_city_id,))
        recent_permits = [dict(row) for row in permits_cursor]
    else:
        recent_permits = []

    # Fallback: try city name match if FK returned nothing
    if not recent_permits:
        filter_name = city_name
        permits_cursor = conn.execute("""
            SELECT * FROM permits
            WHERE city = ? AND state = ?
            ORDER BY filing_date DESC, estimated_cost DESC
            LIMIT 50
        """, (filter_name, city_state))
        recent_permits = [dict(row) for row in permits_cursor]

    # V160: Get permit stats using prod_city_id
    if prod_city_id:
        stats_row = conn.execute("""
            SELECT COUNT(*) as permit_count,
                   MIN(filing_date) as earliest_date,
                   MAX(filing_date) as latest_date
            FROM permits WHERE prod_city_id = ?
        """, (prod_city_id,)).fetchone()
    else:
        filter_name = city_name
        stats_row = conn.execute("""
            SELECT COUNT(*) as permit_count,
                   MIN(filing_date) as earliest_date,
                   MAX(filing_date) as latest_date
            FROM permits WHERE city = ?
        """, (filter_name,)).fetchone()

    permit_count = stats_row['permit_count'] if stats_row else 0
    earliest_date = stats_row['earliest_date'] if stats_row else None
    latest_date = stats_row['latest_date'] if stats_row else None

    # V160: Get permit types breakdown using prod_city_id
    if prod_city_id:
        types_cursor = conn.execute("""
            SELECT COALESCE(permit_type, 'Other') as ptype, COUNT(*) as cnt
            FROM permits WHERE prod_city_id = ?
            GROUP BY permit_type ORDER BY cnt DESC LIMIT 10
        """, (prod_city_id,))
    else:
        types_cursor = conn.execute("""
            SELECT COALESCE(permit_type, 'Other') as ptype, COUNT(*) as cnt
            FROM permits WHERE city = ?
            GROUP BY permit_type ORDER BY cnt DESC LIMIT 10
        """, (city_name,))
    permit_types = {row['ptype']: row['cnt'] for row in types_cursor}

    # Get nearby cities in same state for internal linking
    nearby_cities = conn.execute("""
        SELECT city_slug, city, total_permits
        FROM prod_cities
        WHERE state = ? AND city_slug != ? AND status = 'active' AND total_permits > 0
        ORDER BY total_permits DESC
        LIMIT 10
    """, (city_state, city_slug)).fetchall()

    # Format display name
    display_name = format_city_name(city_name)

    # V156: SEO-optimized meta for top cities, generic fallback for others
    _pc = f"{int(permit_count or 0):,}"
    # V231 P2-10: every title includes ", <state>" for SEO parity with
    # the CITY_SEO_CONFIG path. LA was showing a bare "Los Angeles"
    # title while NYC had ", NY" — inconsistent and hurts the
    # "<city> <state> building permits" search match.
    _SEO_TITLES = {
        ('New York City', 'NY'): 'New York City, NY Building Permits & Contractor Leads — Daily Updates | PermitGrab',
        ('Los Angeles', 'CA'): 'Los Angeles, CA Building Permits & Contractor Leads | PermitGrab',
        ('Chicago', 'IL'): 'Chicago, IL Building Permits & Contractor Leads | PermitGrab',
        ('Austin', 'TX'): 'Austin, TX Building Permits & Contractor Leads — Updated Daily | PermitGrab',
        ('San Antonio', 'TX'): 'San Antonio, TX Building Permits & Contractor Leads | PermitGrab',
        ('Mesa', 'AZ'): 'Mesa, AZ Building Permits & Contractor Leads | PermitGrab',
        ('Fort Worth', 'TX'): 'Fort Worth, TX Building Permits & Contractor Leads | PermitGrab',
        ('Washington', 'DC'): 'Washington, DC Building Permits & Contractor Leads | PermitGrab',
        ('Little Rock', 'AR'): 'Little Rock, AR Building Permits & Contractor Leads | PermitGrab',
        ('Cape Coral', 'FL'): 'Cape Coral, FL Building Permits & Contractor Leads | PermitGrab',
    }
    _SEO_METAS = {
        ('New York City', 'NY'): f'Track {_pc}+ NYC building permits updated daily. Find DOB permits, code violations, and contractor leads by address. 14-day free trial.',
        ('Los Angeles', 'CA'): f'Search {_pc}+ LA building permits. Find LADBS permits, code enforcement cases, and construction leads daily. 14-day free trial.',
        ('Chicago', 'IL'): f'Track {_pc}+ Chicago building permits and code violations. Find new construction projects and contractor leads updated daily.',
        ('Austin', 'TX'): f'Search {_pc}+ Austin TX building permits. Track new construction, code enforcement cases, and find contractor leads. Free trial.',
        ('San Antonio', 'TX'): f'Track {_pc}+ San Antonio building permits updated daily. Find new construction projects and contractor leads by trade.',
        ('Mesa', 'AZ'): f'Search {_pc}+ Mesa building permits. Track new construction projects, code enforcement cases, and find leads daily.',
        ('Fort Worth', 'TX'): f'Track {_pc}+ Fort Worth building permits and code violations. Find new construction projects and leads updated daily.',
        ('Washington', 'DC'): f'Search {_pc}+ DC building permits updated daily. Find construction projects and contractor leads in the DMV area.',
        ('Little Rock', 'AR'): f'Track {_pc}+ Little Rock building permits. Find new construction projects and code enforcement leads updated daily.',
        ('Cape Coral', 'FL'): f'Search {_pc}+ Cape Coral FL building permits. Track new construction projects and find contractor leads daily.',
    }
    _key = (city_name, city_state)
    meta_title = _SEO_TITLES.get(_key, f"{display_name}, {state_name} Building Permits | PermitGrab")
    meta_description = _SEO_METAS.get(_key, f"Browse recent building permits in {display_name}, {state_name}. Track new construction, renovations, and remodeling permits updated daily. Built for contractors and builders.")

    # Robots directive — V236 PR#6: thin-page suppression at <20 permits.
    robots_directive = "index, follow" if permit_count >= 20 and is_active else "noindex, follow"

    # Canonical URL — use the canonical state slug so /permits/new-york/..
    # and /permits/new-york-state/.. both point at the same canonical.
    canonical_url = f"{SITE_URL}/permits/{state_slug_key}/{city_slug}"

    footer_cities = get_cities_with_data()

    # V79: Get relevant blog posts for this city
    city_link = f"/permits/{state_slug}/{city_slug}"
    city_blog_posts = get_blog_posts_for_city(city_link)

    # V162: Get violation data for cities that have it
    violations_data = []
    violations_count = 0
    try:
        # Try prod_city_id first (V162 schema), fall back to city name (V156 schema)
        try:
            if prod_city_id:
                v_count = conn.execute("SELECT COUNT(*) as cnt FROM violations WHERE prod_city_id = ?", (prod_city_id,)).fetchone()
                violations_count = v_count['cnt'] if v_count else 0
        except Exception:
            pass
        if violations_count == 0:
            v_count = conn.execute("SELECT COUNT(*) as cnt FROM violations WHERE city = ? AND state = ?", (city_name, city_state)).fetchone()
            violations_count = v_count['cnt'] if v_count else 0
        if violations_count > 0:
            try:
                v_rows = conn.execute("""
                    SELECT violation_date, violation_type, COALESCE(violation_description, description, '') as violation_description,
                           status, address
                    FROM violations WHERE city = ? AND state = ?
                    ORDER BY violation_date DESC LIMIT 25
                """, (city_name, city_state)).fetchall()
                violations_data = [dict(r) for r in v_rows]
            except Exception:
                pass
    except Exception:
        pass

    # V182 PR2: top contractors (empty if city fails public filter)
    top_contractors = _get_top_contractors_for_city(city_slug, limit=25)

    # V229-hotfix: compute freshness_age_days for template (same calc as
    # city_landing_inner). Without this, city_landing_v77.html at line 672
    # throws UndefinedError and every /permits/<state>/<city> page 500s.
    _freshness_age_days = None
    if newest_permit_date:
        try:
            _freshness_age_days = (
                datetime.now().date()
                - datetime.strptime(str(newest_permit_date)[:10], '%Y-%m-%d').date()
            ).days
        except Exception:
            _freshness_age_days = None

    # V230 T1-T13: variable parity with city_landing_inner. Every kwarg
    # that route passes must appear here or the template falls through to
    # a broken/empty section (and one of them — freshness_age_days — was
    # 500ing before the V229 hotfix). Aliases and zero-defaults are fine
    # for the stats we don't compute here; the primary route can fill in
    # richer values.
    # V236 PR#5: data-driven insights paragraph.
    market_insights = _get_market_insights(
        prod_city_id=prod_city_id,
        city_name=city_name,
        city_state=city_state,
    )
    return render_template('city_landing_v77.html',
        city_name=display_name,
        city_slug=city_slug,
        state_abbrev=city_state,
        city_state=city_state,
        state_name=state_name,
        state_slug=state_slug,
        total_permits=total_permits,
        permit_count=permit_count,
        earliest_date=earliest_date,
        latest_date=latest_date,
        newest_permit_date=newest_permit_date,
        last_collection=last_collection,
        last_collected=last_collection,  # V230 T1: alias
        data_freshness=data_freshness,
        freshness_age_days=_freshness_age_days,  # V229-hotfix
        is_active=is_active,
        is_coming_soon=not is_active,  # V230 T11
        recent_permits=recent_permits,
        permits=recent_permits,  # V230: alias city_landing_inner uses
        top_contractors=top_contractors,  # V182 PR2
        permit_types=permit_types,
        trade_breakdown=permit_types,  # V230 T6: template references either
        nearby_cities=nearby_cities,
        other_cities=nearby_cities,  # V230 T7: alias
        meta_title=meta_title,
        meta_description=meta_description,
        seo_content=None,  # V230 T8: only computed by legacy city_landing_inner
        robots_directive=robots_directive,
        canonical_url=canonical_url,
        footer_cities=footer_cities,
        blog_posts=city_blog_posts,
        related_articles=city_blog_posts,  # V230 T10: alias
        violations=violations_data,
        violations_count=violations_count,
        # V230 T2-T5: stats the legacy route computes. Keeping these at 0
        # rather than running extra queries; if the template shows the
        # "Total Construction Value" card it'll read $0 which is accurate
        # until we wire per-city aggregates into this route.
        total_value=0,
        high_value_count=0,
        new_this_month=0,
        unique_contractors=0,
        # V230 T9: footer/copyright
        current_year=datetime.now().year,
        current_date=datetime.now().strftime('%Y-%m-%d'),
        current_week_start=(datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d'),  # V209-3: NEW badge
        # V230 T12/T13: safe defaults for optional sections
        top_neighborhoods=[],
        city_blog_url=None,
        top_trades=[],
        market_insights=market_insights,  # V236 PR#5
    )


@app.route('/permits/<city_slug>/<trade_slug>')
def city_trade_landing(city_slug, trade_slug):
    """Render SEO-optimized city × trade landing page."""
    # V12.60: Use get_city_by_slug_auto() to match sitemap-generated URLs
    # (previously used get_city_by_slug which missed auto-discovered cities)
    city_key, city_config = get_city_by_slug_auto(city_slug)
    if not city_config:
        return render_city_not_found(city_slug)

    # Get trade from config
    trade = get_trade(trade_slug)
    if not trade:
        return render_city_not_found(trade_slug)

    # V12.51: Use SQLite for city permits
    conn = permitdb.get_connection()

    # V12.9: Format city name for display
    display_name = format_city_name(city_config['name'])

    # V14.1: Filter permits using SQL LIKE patterns for both city AND trade
    # This is more efficient than loading all city permits then filtering in Python
    city_name = city_config['name']
    matching_permits = []

    # V14.1: Get LIKE patterns for this trade from TRADE_MAPPING
    trade_patterns = TRADE_MAPPING.get(trade_slug, [f'%{trade_slug}%'])

    # V14.1: Build SQL query with trade patterns
    # Check description, permit_type, work_type, trade_category for trade keywords
    def query_trade_permits(city_filter, city_param):
        """Helper to query permits matching city filter AND trade patterns."""
        # Build OR conditions for trade patterns across multiple fields
        trade_conditions = []
        trade_params = []
        for pattern in trade_patterns:
            trade_conditions.append("""
                (LOWER(COALESCE(description, '')) LIKE ?
                 OR LOWER(COALESCE(permit_type, '')) LIKE ?
                 OR LOWER(COALESCE(work_type, '')) LIKE ?
                 OR LOWER(COALESCE(trade_category, '')) LIKE ?)
            """)
            trade_params.extend([pattern.lower(), pattern.lower(), pattern.lower(), pattern.lower()])

        trade_clause = " OR ".join(trade_conditions)
        sql = f"""
            SELECT * FROM permits
            WHERE {city_filter}
              AND ({trade_clause})
            ORDER BY filing_date DESC
            LIMIT 500
        """
        params = [city_param] + trade_params
        cursor = conn.execute(sql, params)
        return [dict(row) for row in cursor]

    # Strategy 1: Exact city match
    matching_permits = query_trade_permits("city = ?", city_name)

    # Strategy 2: Case-insensitive city match if no results
    if not matching_permits:
        matching_permits = query_trade_permits("LOWER(city) = LOWER(?)", city_name)

    # Strategy 3: Partial city match (without state suffix) if still no results
    if not matching_permits and ',' in city_name:
        base_name = city_name.split(',')[0].strip()
        matching_permits = query_trade_permits("LOWER(city) LIKE ?", f"%{base_name.lower()}%")

    # V14.1: Fallback to Python-side matching if SQL patterns didn't match
    # This handles edge cases where data format differs from expected patterns
    if not matching_permits:
        # Load city permits and filter with trade_configs keywords
        city_permits = []
        cursor = conn.execute(
            "SELECT * FROM permits WHERE LOWER(city) LIKE ? ORDER BY filing_date DESC LIMIT 2000",
            (f"%{city_name.split(',')[0].strip().lower()}%",)
        )
        city_permits = [dict(row) for row in cursor]

        trade_keywords = [kw.lower() for kw in trade['keywords']]
        for p in city_permits:
            text = ""
            for field in ['description', 'permit_type', 'work_type', 'trade_category']:
                if p.get(field):
                    text += p[field].lower() + " "
            if any(kw in text for kw in trade_keywords):
                matching_permits.append(p)
                if len(matching_permits) >= 500:
                    break

    # Results are already sorted by date from SQL

    # V30: Fallback — show recent city permits if no trade-specific matches found
    # This prevents empty trade pages which kill conversion and risk thin content penalties
    trade_fallback = False
    if not matching_permits:
        cursor = conn.execute(
            "SELECT * FROM permits WHERE LOWER(city) LIKE ? ORDER BY filing_date DESC LIMIT 20",
            (f"%{city_name.split(',')[0].strip().lower()}%",)
        )
        matching_permits = [dict(row) for row in cursor]
        trade_fallback = True

    # Calculate stats
    # V12.23: Use module-level datetime/timedelta imports
    now = datetime.now()
    month_ago = (now - timedelta(days=30)).isoformat()
    week_ago = (now - timedelta(days=7)).isoformat()

    monthly_count = len([p for p in matching_permits if (p.get('filing_date') or '') >= month_ago])
    weekly_count = len([p for p in matching_permits if (p.get('filing_date') or '') >= week_ago])

    values = [p.get('estimated_cost', 0) for p in matching_permits if p.get('estimated_cost')]
    avg_value = int(sum(values) / len(values)) if values else 0

    # Build city dict for template with formatted name
    city_dict = {
        "name": display_name,
        "state": city_config['state'],
        "slug": city_slug,
    }

    # V231 P0-8: pass avg_value as a number (or None). V230 changed the
    # template to do number formatting ($1.2M / $1,234) but the route was
    # still passing a pre-formatted string ("1,234" / "N/A"), which blew
    # up the new Jinja `avg_value > 0` comparison and 503'd every trade
    # page. Template now does the number formatting.
    stats = {
        "monthly_count": monthly_count or len(matching_permits),
        "weekly_count": weekly_count,
        "avg_value": avg_value if avg_value else None,
    }

    # Other trades for cross-linking (exclude current)
    other_trades = [t for t in get_all_trades() if t['slug'] != trade_slug]

    # Other cities for cross-linking (exclude current)
    other_cities = [{"name": c['name'], "slug": c['slug']} for c in ALL_CITIES if c['slug'] != city_slug]

    # V227 T9: Trade-page indexation policy.
    # Google's 2025-2026 core updates have been deindexing programmatic
    # pages that only differ by city/trade name in a data table. V222
    # dropped noindex from any page with >=1 permits; this tightens to
    # >=20 permits as the threshold for an indexable page (below that
    # the page is thin enough that Google treats it as template spam and
    # it hurts the site's overall crawl score). The remaining thin pages
    # stay reachable by internal link but carry noindex.
    _MIN_INDEX_PERMITS = 20
    robots_directive = (
        "noindex, follow"
        if (trade_fallback or len(matching_permits) < _MIN_INDEX_PERMITS)
        else "index, follow"
    )

    # V227 T9: Build an insights paragraph for the pages that ARE indexable.
    # Pure template pages with just a data table rank poorly; a prose
    # paragraph that can't exist on any other page is the cheapest way to
    # make Google treat this as real content.
    trade_insights = None
    if robots_directive == "index, follow" and len(matching_permits) >= _MIN_INDEX_PERMITS:
        try:
            _t_conn = permitdb.get_connection()
            # Last 30d vs prior 30d (for trend)
            _this_30 = _t_conn.execute(
                "SELECT COUNT(*) FROM permits WHERE source_city_key = ? "
                "AND trade_category = ? AND filing_date >= date('now','-30 days')",
                (city_slug, trade.get('name', ''))).fetchone()[0]
            _prior_30 = _t_conn.execute(
                "SELECT COUNT(*) FROM permits WHERE source_city_key = ? "
                "AND trade_category = ? AND filing_date >= date('now','-60 days') "
                "AND filing_date < date('now','-30 days')",
                (city_slug, trade.get('name', ''))).fetchone()[0]
            _six_mo = _t_conn.execute(
                "SELECT COUNT(*) FROM permits WHERE source_city_key = ? "
                "AND trade_category = ? AND filing_date >= date('now','-180 days')",
                (city_slug, trade.get('name', ''))).fetchone()[0]
            _top_contractors = [r[0] for r in _t_conn.execute(
                "SELECT contractor_name, COUNT(*) c FROM permits "
                "WHERE source_city_key = ? AND trade_category = ? "
                "AND contractor_name IS NOT NULL AND contractor_name != '' "
                "GROUP BY contractor_name ORDER BY c DESC LIMIT 3",
                (city_slug, trade.get('name', ''))).fetchall()]

            trend = "steady"
            if _prior_30 > 0:
                _delta = (_this_30 - _prior_30) / _prior_30 * 100
                if _delta > 20:
                    trend = "accelerating"
                elif _delta < -20:
                    trend = "slowing"
            trade_insights = {
                "trend": trend,
                "this_month": _this_30,
                "prior_month": _prior_30,
                "avg_monthly": round((_six_mo or 0) / 6.0, 1),
                "top_contractors": _top_contractors,
            }
        except Exception as e:
            print(f"[V227] trade_insights error: {e}")

    # V14.0: Get state info for internal linking
    state_abbrev = city_config.get('state', '')
    state_slug = None
    state_name = state_abbrev  # Fallback to abbrev
    for slug, info in STATE_CONFIG.items():
        if info['abbrev'] == state_abbrev:
            state_slug = slug
            state_name = info['name']
            break

    # V14.0: Check if city blog post exists
    city_blog_url = None
    if state_abbrev:
        blog_slug = f"building-permits-{city_slug}-{state_abbrev.lower()}-contractor-guide"
        blog_path = os.path.join(os.path.dirname(__file__), 'blog', f"{blog_slug}.md")
        if os.path.exists(blog_path):
            city_blog_url = f"/blog/{blog_slug}"

    return render_template(
        'city_trade_landing.html',
        city=city_dict,
        trade=trade,
        permits=matching_permits[:10],
        stats=stats,
        other_trades=other_trades,
        other_cities=other_cities,
        robots_directive=robots_directive,
        state_slug=state_slug,
        state_name=state_name,
        city_blog_url=city_blog_url,
        trade_fallback=trade_fallback,
        trade_insights=trade_insights,  # V227 T9: per-page prose data insights
    )


# ===========================
# V28: SEARCH PAGE — Required for SearchAction schema (sitelinks searchbox)
# ===========================

@app.route('/search')
def search_page():
    """V28: Search page for SearchAction schema.
    Redirects to cities browse filtered by query, or shows permits filtered by query.
    """
    query = request.args.get('q', '').strip()
    if not query:
        return redirect('/cities')

    # Try to find a matching city
    all_cities = get_cities_with_data()
    query_lower = query.lower()

    # Direct city match (exact query == city name)
    for city in all_cities:
        city_name = city.get('name', '') or city.get('city', '')
        if city_name.lower() == query_lower:
            slug = city.get('slug', city_name.lower().replace(' ', '-'))
            return redirect(f'/permits/{slug}')

    # City name appears in query (e.g. "denver roofing" contains "denver")
    best_match = None
    best_len = 0
    for city in all_cities:
        city_name = (city.get('name', '') or city.get('city', '')).lower()
        if city_name and city_name in query_lower and len(city_name) > best_len:
            best_match = city
            best_len = len(city_name)
    if best_match:
        slug = best_match.get('slug', (best_match.get('name', '') or best_match.get('city', '')).lower().replace(' ', '-'))
        # Check if query has a trade keyword after the city name
        remainder = query_lower.replace((best_match.get('name', '') or best_match.get('city', '')).lower(), '').strip()
        if remainder:
            # Try to match a trade — redirect to city+trade page
            return redirect(f'/permits/{slug}/{remainder.replace(" ", "-")}')
        return redirect(f'/permits/{slug}')

    # Query appears in a city name (e.g. "den" matches "denver")
    matching_cities = [c for c in all_cities if query_lower in (c.get('name', '') or c.get('city', '')).lower()]
    if matching_cities:
        city = matching_cities[0]
        slug = city.get('slug', (city.get('name', '') or city.get('city', '')).lower().replace(' ', '-'))
        return redirect(f'/permits/{slug}')

    # No match - redirect to cities browse
    return redirect('/cities')


# ===========================
# V17e: CITIES BROWSE PAGE — Hub for all city landing pages
# ===========================

@app.route('/cities')
def cities_browse():
    """V17e: Dedicated browse page for all cities, organized by state.
    Reduces homepage link dilution by moving 300+ city links here.
    Acts as an SEO hub that distributes PageRank to all city pages.

    V182 PR2: filters out bulk-misattribution cities (e.g. Fenner NY with
    39K permits on 1,900 residents) so they don't appear in public rankings.
    """
    raw_cities = get_cities_with_data()
    footer_cities = raw_cities

    # V182 PR2: apply public-ranking filter (population vs permit volume).
    from contractor_profiles import city_passes_public_filter
    all_cities = [
        c for c in raw_cities
        if city_passes_public_filter(c.get('population', 0), c.get('permit_count', 0))
    ]
    filtered_out = len(raw_cities) - len(all_cities)
    if filtered_out:
        print(f"[V182 cities] Filtered {filtered_out} bulk-misattribution cities from rankings", flush=True)

    # Group cities by state
    states = {}
    no_state = []
    for city in all_cities:
        state = city.get('state', '').strip()
        if state:
            if state not in states:
                states[state] = []
            states[state].append(city)
        else:
            no_state.append(city)

    # Sort states alphabetically, cities within each state by permit count
    sorted_states = sorted(states.items(), key=lambda x: x[0])
    for state_name, cities in sorted_states:
        cities.sort(key=lambda c: c.get('permit_count', 0), reverse=True)

    # Top cities across all states (for hero section)
    # V13.2: Increased from 12 to 20 for better coverage
    top_cities = all_cities[:20]

    total_cities = len(all_cities)
    total_states = len(states)

    return render_template('cities_browse.html',
        footer_cities=footer_cities,
        sorted_states=sorted_states,
        no_state_cities=no_state,
        top_cities=top_cities,
        total_cities=total_cities,
        total_states=total_states,
        canonical_url=f"{SITE_URL}/cities",
    )


# ===========================
# SITEMAP & ROBOTS.TXT
# ===========================

def _get_city_lastmod_map():
    """V28: Get lastmod timestamps per city from permit data."""
    city_lastmod = {}
    try:
        conn = permitdb.get_connection()
        rows = conn.execute("SELECT city, MAX(collected_at) as latest FROM permits GROUP BY city").fetchall()
        for row in rows:
            if row['city'] and row['latest']:
                city_slug_key = row['city'].lower().replace(' ', '-').replace(',', '').replace('.', '')
                try:
                    city_lastmod[city_slug_key] = row['latest'][:10]  # YYYY-MM-DD
                except (TypeError, IndexError):
                    pass
    except Exception:
        pass
    return city_lastmod


def _generate_sitemap_xml(urls):
    """V28: Generate XML sitemap from list of URL dicts."""
    xml_parts = ['<?xml version="1.0" encoding="UTF-8"?>\n',
                 '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n']
    for url in urls:
        xml_parts.append('  <url>\n')
        xml_parts.append(f"    <loc>{url['loc']}</loc>\n")
        xml_parts.append(f"    <lastmod>{url['lastmod']}</lastmod>\n")
        xml_parts.append(f"    <changefreq>{url['changefreq']}</changefreq>\n")
        xml_parts.append(f"    <priority>{url['priority']}</priority>\n")
        xml_parts.append('  </url>\n')
    xml_parts.append('</urlset>')
    return ''.join(xml_parts)


@app.route('/sitemap.xml')
def sitemap_index():
    """V28: Sitemap index pointing to child sitemaps."""
    today = datetime.now().strftime('%Y-%m-%d')

    xml_parts = ['<?xml version="1.0" encoding="UTF-8"?>\n',
                 '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n']

    child_sitemaps = [
        ('sitemap-pages.xml', today),
        ('sitemap-cities.xml', today),
        ('sitemap-states.xml', today),  # V233 P1-3
        ('sitemap-trades.xml', today),
        ('sitemap-blog.xml', today),
    ]

    for sitemap_name, lastmod in child_sitemaps:
        xml_parts.append('  <sitemap>\n')
        xml_parts.append(f"    <loc>{SITE_URL}/{sitemap_name}</loc>\n")
        xml_parts.append(f"    <lastmod>{lastmod}</lastmod>\n")
        xml_parts.append('  </sitemap>\n')

    xml_parts.append('</sitemapindex>')
    return Response(''.join(xml_parts), mimetype='application/xml')


@app.route('/sitemap-pages.xml')
def sitemap_pages():
    """V28: Sitemap for static pages."""
    today = datetime.now().strftime('%Y-%m-%d')
    urls = [
        {'loc': SITE_URL, 'changefreq': 'daily', 'priority': '1.0', 'lastmod': today},
        {'loc': f"{SITE_URL}/pricing", 'changefreq': 'weekly', 'priority': '0.9', 'lastmod': today},
        {'loc': f"{SITE_URL}/contractors", 'changefreq': 'daily', 'priority': '0.8', 'lastmod': today},
        {'loc': f"{SITE_URL}/map", 'changefreq': 'daily', 'priority': '0.8', 'lastmod': today},
        {'loc': f"{SITE_URL}/get-alerts", 'changefreq': 'weekly', 'priority': '0.7', 'lastmod': today},
        {'loc': f"{SITE_URL}/blog", 'changefreq': 'weekly', 'priority': '0.7', 'lastmod': today},
        {'loc': f"{SITE_URL}/cities", 'changefreq': 'daily', 'priority': '0.9', 'lastmod': today},
        {'loc': f"{SITE_URL}/stats", 'changefreq': 'daily', 'priority': '0.7', 'lastmod': today},
        {'loc': f"{SITE_URL}/about", 'changefreq': 'monthly', 'priority': '0.6', 'lastmod': today},
        {'loc': f"{SITE_URL}/contact", 'changefreq': 'monthly', 'priority': '0.5', 'lastmod': today},
        {'loc': f"{SITE_URL}/privacy", 'changefreq': 'monthly', 'priority': '0.3', 'lastmod': today},
        {'loc': f"{SITE_URL}/terms", 'changefreq': 'monthly', 'priority': '0.3', 'lastmod': today},
    ]
    return Response(_generate_sitemap_xml(urls), mimetype='application/xml')


@app.route('/sitemap-cities.xml')
def sitemap_cities():
    """V28: Sitemap for city pages and state hub pages.
    V77: Added city URLs in /permits/{state}/{city} format for SEO.
    """
    today = datetime.now().strftime('%Y-%m-%d')
    url_map = {}

    # V77: Create reverse mapping from state abbrev to state slug
    abbrev_to_state_slug = {v['abbrev']: k for k, v in STATE_CONFIG.items()}

    # State hub pages
    for state_slug in STATE_CONFIG.keys():
        url_map[f"{SITE_URL}/permits/{state_slug}"] = {
            'loc': f"{SITE_URL}/permits/{state_slug}",
            'changefreq': 'daily',
            'priority': '0.85',
            'lastmod': today
        }

    # Get cities with permits
    cities_with_data = get_cities_with_data()
    cities_with_permits = {c['name'] for c in cities_with_data}
    city_lastmod = _get_city_lastmod_map()
    state_slugs = set(STATE_CONFIG.keys())

    # City pages from discovered cities (old format: /permits/{city})
    all_discovered_cities = discover_cities_from_permits()
    for slug, city_info in all_discovered_cities.items():
        if slug in state_slugs:
            continue
        if city_info['name'] not in cities_with_permits:
            continue

        lastmod = city_lastmod.get(slug, today)
        loc = f"{SITE_URL}/permits/{slug}"
        if loc not in url_map:
            url_map[loc] = {
                'loc': loc,
                'changefreq': 'daily',
                'priority': '0.8',
                'lastmod': lastmod
            }

    # V58: Also include ALL active CITY_REGISTRY cities for SEO (even without data yet)
    for key, config in CITY_REGISTRY.items():
        if not config.get('active'):
            continue
        slug = config.get('slug', key.replace('_', '-'))
        loc = f"{SITE_URL}/permits/{slug}"
        if loc not in url_map and slug not in state_slugs:
            url_map[loc] = {
                'loc': loc,
                'changefreq': 'weekly',
                'priority': '0.6',
                'lastmod': today
            }

    # V77: Add city URLs in new /permits/{state}/{city} format
    # These are the SEO-optimized URLs targeting "[city] building permits" keywords
    try:
        conn = permitdb.get_connection()
        active_cities = conn.execute("""
            SELECT city_slug, state, last_collection, data_freshness, total_permits
            FROM prod_cities
            WHERE status = 'active'
              AND data_freshness != 'no_data'
              AND total_permits > 0
        """).fetchall()

        for city_row in active_cities:
            city_slug = city_row['city_slug']
            state_abbrev = city_row['state']
            last_collection = city_row['last_collection']

            # Get state slug from abbreviation
            state_slug = abbrev_to_state_slug.get(state_abbrev)
            if not state_slug:
                continue  # Skip if state not in our config

            # Format lastmod
            lastmod = last_collection[:10] if last_collection else today

            # New format: /permits/{state}/{city}
            loc = f"{SITE_URL}/permits/{state_slug}/{city_slug}"
            if loc not in url_map:
                url_map[loc] = {
                    'loc': loc,
                    'changefreq': 'daily',
                    'priority': '0.7',
                    'lastmod': lastmod
                }
    except Exception as e:
        print(f"[sitemap_cities] V77 city URLs error: {e}")

    return Response(_generate_sitemap_xml(url_map.values()), mimetype='application/xml')


@app.route('/sitemap-states.xml')
def sitemap_states():
    """V233 P1-3: dedicated state-URL sitemap. State routes (both the
    state hub /permits/{state} and the /permits/{state}/{city} SEO URLs)
    were already emitted inside sitemap-cities.xml, but Cowork's audit
    showed Google wasn't surfacing them — the cities sitemap is enormous
    and state URLs got lost in the mix. Breaking them out into a
    dedicated sub-sitemap gives the state pattern its own crawl budget.
    """
    today = datetime.now().strftime('%Y-%m-%d')
    url_map = {}
    abbrev_to_state_slug = {v['abbrev']: k for k, v in STATE_CONFIG.items()}

    # State hub pages
    for state_slug in STATE_CONFIG.keys():
        loc = f"{SITE_URL}/permits/{state_slug}"
        url_map[loc] = {
            'loc': loc,
            'changefreq': 'daily',
            'priority': '0.85',
            'lastmod': today,
        }

    # /permits/{state}/{city} URLs for active cities with data
    try:
        conn = permitdb.get_connection()
        active_cities = conn.execute("""
            SELECT city_slug, state, last_collection
            FROM prod_cities
            WHERE status = 'active'
              AND data_freshness != 'no_data'
              AND total_permits > 0
        """).fetchall()
        for row in active_cities:
            state_slug = abbrev_to_state_slug.get(row['state'])
            if not state_slug:
                continue
            lastmod = row['last_collection'][:10] if row['last_collection'] else today
            loc = f"{SITE_URL}/permits/{state_slug}/{row['city_slug']}"
            if loc not in url_map:
                url_map[loc] = {
                    'loc': loc,
                    'changefreq': 'daily',
                    'priority': '0.7',
                    'lastmod': lastmod,
                }
    except Exception as e:
        print(f"[sitemap_states] state/city URL error: {e}")

    return Response(_generate_sitemap_xml(url_map.values()), mimetype='application/xml')


@app.route('/sitemap-trades.xml')
def sitemap_trades():
    """V28: Sitemap for city × trade pages."""
    today = datetime.now().strftime('%Y-%m-%d')
    url_map = {}

    cities_with_data = get_cities_with_data()
    cities_with_permits = {c['name'] for c in cities_with_data}
    city_lastmod = _get_city_lastmod_map()
    state_slugs = set(STATE_CONFIG.keys())
    trade_slugs = [t for t in get_trade_slugs() if t != 'all-trades']

    all_discovered_cities = discover_cities_from_permits()
    for slug, city_info in all_discovered_cities.items():
        if slug in state_slugs:
            continue
        if city_info['name'] not in cities_with_permits:
            continue

        lastmod = city_lastmod.get(slug, today)
        for trade_slug in trade_slugs:
            loc = f"{SITE_URL}/permits/{slug}/{trade_slug}"
            if loc not in url_map:
                url_map[loc] = {
                    'loc': loc,
                    'changefreq': 'daily',
                    'priority': '0.7',
                    'lastmod': lastmod
                }

    return Response(_generate_sitemap_xml(url_map.values()), mimetype='application/xml')


@app.route('/sitemap-blog.xml')
def sitemap_blog():
    """V79: Sitemap for blog posts — uses BLOG_POSTS data structure."""
    urls = []

    # Add blog index page
    urls.append({
        'loc': f"{SITE_URL}/blog",
        'changefreq': 'weekly',
        'priority': '0.7',
        'lastmod': '2026-04-06'
    })

    # Add all blog posts
    for post in BLOG_POSTS:
        urls.append({
            'loc': f"{SITE_URL}/blog/{post['slug']}",
            'changefreq': 'weekly',
            'priority': '0.6',
            'lastmod': post['date']
        })

    return Response(_generate_sitemap_xml(urls), mimetype='application/xml')


@app.route('/logout')
def logout_page():
    """Log out and redirect to homepage."""
    session.clear()
    return redirect('/')


# ===========================
# ACCOUNT SETTINGS (V8)
# ===========================
@app.route('/account')
def account_page():
    """Account settings page."""
    if 'user_email' not in session:
        return redirect('/login')
    user = find_user_by_email(session['user_email'])
    if not user:
        session.clear()
        return redirect('/login')
    footer_cities = get_cities_with_data()
    is_pro = user.plan in ('pro', 'professional', 'enterprise')
    return render_template('account.html', user=user, is_pro=is_pro, footer_cities=footer_cities)


@app.route('/api/account', methods=['PUT'])
def api_update_account():
    """Update account settings."""
    if 'user_email' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    user = find_user_by_email(session['user_email'])
    if not user:
        return jsonify({'error': 'User not found'}), 404

    data = request.get_json()
    if not data:
        return jsonify({'error': 'Invalid request'}), 400

    if 'name' in data:
        user.name = data['name'].strip()
    if 'city' in data:
        user.city = data['city']
    if 'trade' in data:
        user.trade = data['trade']
    if 'daily_alerts' in data:
        user.daily_alerts = bool(data['daily_alerts'])
    # V12.26: Competitor Watch and Digest Cities
    if 'watched_competitors' in data:
        competitors = data['watched_competitors']
        if isinstance(competitors, list):
            # Limit to 5 competitors, Pro only
            if user.plan == 'pro':
                user.watched_competitors = json.dumps(competitors[:5])
    if 'digest_cities' in data:
        cities = data['digest_cities']
        if isinstance(cities, list):
            user.digest_cities = json.dumps(cities)

    db.session.commit()
    return jsonify({'success': True, 'user': user.to_dict()})


# V170 B4: Saved Searches API
@app.route('/api/saved-searches', methods=['GET'])
def list_saved_searches():
    """List user's saved searches."""
    if 'user_email' not in session:
        return jsonify({'error': 'Login required'}), 401
    user = find_user_by_email(session['user_email'])
    if not user:
        return jsonify({'error': 'User not found'}), 404
    searches = SavedSearch.query.filter_by(user_id=user.id).order_by(SavedSearch.created_at.desc()).all()
    return jsonify({'searches': [s.to_dict() for s in searches]})


@app.route('/api/saved-searches', methods=['POST'])
def create_saved_search():
    """Create a new saved search."""
    if 'user_email' not in session:
        return jsonify({'error': 'Login required'}), 401
    user = find_user_by_email(session['user_email'])
    if not user:
        return jsonify({'error': 'User not found'}), 404
    data = request.get_json() or {}
    search = SavedSearch(
        user_id=user.id,
        name=data.get('name', 'Untitled Search'),
        city_slug=data.get('city_slug'),
        trade=data.get('trade'),
        tier=data.get('tier'),
        min_value=data.get('min_value'),
        frequency=data.get('frequency', 'daily'),
    )
    db.session.add(search)
    db.session.commit()
    return jsonify({'search': search.to_dict(), 'created': True}), 201


@app.route('/api/saved-searches/<int:search_id>', methods=['PATCH'])
def update_saved_search(search_id):
    """Update a saved search (active/name)."""
    if 'user_email' not in session:
        return jsonify({'error': 'Login required'}), 401
    user = find_user_by_email(session['user_email'])
    search = SavedSearch.query.filter_by(id=search_id, user_id=user.id).first()
    if not search:
        return jsonify({'error': 'Not found'}), 404
    data = request.get_json() or {}
    if 'active' in data:
        search.active = int(data['active'])
    if 'name' in data:
        search.name = data['name']
    db.session.commit()
    return jsonify({'search': search.to_dict()})


@app.route('/api/saved-searches/<int:search_id>', methods=['DELETE'])
def delete_saved_search(search_id):
    """Delete a saved search."""
    if 'user_email' not in session:
        return jsonify({'error': 'Login required'}), 401
    user = find_user_by_email(session['user_email'])
    search = SavedSearch.query.filter_by(id=search_id, user_id=user.id).first()
    if not search:
        return jsonify({'error': 'Not found'}), 404
    db.session.delete(search)
    db.session.commit()
    return jsonify({'deleted': True})


# V170 C3: Daily alert emails
def send_daily_alerts():
    """Send digest emails for active daily saved_searches."""
    DATABASE_URL = os.environ.get('DATABASE_URL')
    if not DATABASE_URL:
        print("[ALERTS] DATABASE_URL not set, skipping")
        return

    import psycopg2
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    cur.execute("""
        SELECT s.id, s.user_id, s.name, s.city_slug, s.trade, s.tier, s.min_value
        FROM saved_searches s
        WHERE s.active = 1
          AND s.frequency = 'daily'
          AND (s.last_sent_at IS NULL OR s.last_sent_at::date < CURRENT_DATE)
    """)
    searches = cur.fetchall()
    print(f"[ALERTS] {len(searches)} saved searches due for send")

    from email_alerts import send_email as _send_email

    for sid, uid, name, city_slug, trade, tier, min_value in searches:
        cur.execute("SELECT email FROM users WHERE id = %s", (uid,))
        row = cur.fetchone()
        if not row:
            continue
        email = row[0]

        where = ["p.collected_at >= NOW() - INTERVAL '24 hours'"]
        params = []
        if city_slug:
            where.append('pc.city_slug = %s')
            params.append(city_slug)
        if trade:
            where.append('p.trade_category = %s')
            params.append(trade)
        if min_value:
            where.append('p.estimated_cost >= %s')
            params.append(min_value)

        sql = f"""
            SELECT p.permit_number, p.address, p.permit_type, p.description,
                   p.estimated_cost, p.value_tier, p.trade_category,
                   p.contractor_name, p.contact_phone, p.filing_date
            FROM permits p
            JOIN prod_cities pc ON pc.source_id = p.source_city_key
            WHERE {' AND '.join(where)}
            ORDER BY p.filing_date DESC NULLS LAST
            LIMIT 50
        """
        cur.execute(sql, params)
        permits = cur.fetchall()

        if not permits:
            continue

        try:
            html = render_template('emails/saved_search_digest.html',
                search_name=name, permits=permits, city_slug=city_slug,
                unsubscribe_url=f'https://permitgrab.com/unsubscribe/{sid}'
            )
            _send_email(email, f"PermitGrab: {len(permits)} new matches for '{name}'", html)
            print(f"[ALERTS] Sent search_id={sid} to {email} permits={len(permits)}")
        except Exception as e:
            print(f"[ALERTS] Error sending to {email}: {e}")
            continue

        cur.execute("UPDATE saved_searches SET last_sent_at = NOW() WHERE id = %s", (sid,))
        conn.commit()

    conn.close()


@app.route('/api/admin/run-daily-alerts', methods=['POST'])
def admin_run_daily_alerts():
    """V170 C3: Manually trigger daily alert emails."""
    valid, error = check_admin_key()
    if not valid:
        return error
    try:
        send_daily_alerts()
        return jsonify({'triggered': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/unsubscribe/<int:search_id>')
def unsubscribe_search(search_id):
    """V170 C3: Unsubscribe from a saved search alert."""
    search = SavedSearch.query.get(search_id)
    if search:
        search.active = 0
        db.session.commit()
    return "Unsubscribed. You won't receive more emails for this saved search.", 200


# V12.26: Competitor Watch API
@app.route('/api/competitors/watch', methods=['GET', 'POST', 'DELETE'])
def api_competitor_watch():
    """Manage watched competitors for Competitor Watch alerts."""
    if 'user_email' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    user = find_user_by_email(session['user_email'])
    if not user:
        return jsonify({'error': 'User not found'}), 404
    if user.plan != 'pro':
        return jsonify({'error': 'Competitor Watch is a Pro feature', 'upgrade_url': '/pricing'}), 403

    competitors = json.loads(user.watched_competitors or '[]')

    if request.method == 'GET':
        return jsonify({'competitors': competitors})

    elif request.method == 'POST':
        data = request.get_json()
        name = (data.get('name', '') if data else '').strip()
        if not name:
            return jsonify({'error': 'Competitor name required'}), 400
        if len(competitors) >= 5:
            return jsonify({'error': 'Maximum 5 competitors allowed'}), 400
        if name.lower() not in [c.lower() for c in competitors]:
            competitors.append(name)
            user.watched_competitors = json.dumps(competitors)
            db.session.commit()
        return jsonify({'competitors': competitors})

    elif request.method == 'DELETE':
        data = request.get_json()
        name = (data.get('name', '') if data else '').strip()
        competitors = [c for c in competitors if c.lower() != name.lower()]
        user.watched_competitors = json.dumps(competitors)
        db.session.commit()
        return jsonify({'competitors': competitors})


# V12.26: Check for competitor matches in recent permits
@app.route('/api/competitors/matches')
def api_competitor_matches():
    """Get permits matching watched competitors."""
    if 'user_email' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    user = find_user_by_email(session['user_email'])
    if not user:
        return jsonify({'error': 'User not found'}), 404
    if user.plan != 'pro':
        return jsonify({'error': 'Competitor Watch is a Pro feature'}), 403

    competitors = json.loads(user.watched_competitors or '[]')
    if not competitors:
        return jsonify({'matches': [], 'message': 'No competitors being watched'})

    # V12.51: Use SQLite with LIKE queries for competitor matching
    conn = permitdb.get_connection()
    matches = []

    for comp in competitors:
        cursor = conn.execute("""
            SELECT * FROM permits
            WHERE LOWER(contact_name) LIKE ?
            ORDER BY filing_date DESC
            LIMIT 50
        """, (f"%{comp.lower()}%",))

        for row in cursor:
            matches.append({
                'permit': dict(row),
                'matched_competitor': comp
            })

    # Sort by filing date, most recent first
    matches.sort(key=lambda x: x['permit'].get('filing_date', ''), reverse=True)

    return jsonify({'matches': matches[:50]})  # Limit to 50 most recent


@app.route('/api/change-password', methods=['POST'])
def api_change_password():
    """Change user password."""
    if 'user_email' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    user = find_user_by_email(session['user_email'])
    if not user:
        return jsonify({'error': 'User not found'}), 404

    data = request.get_json()
    if not data:
        return jsonify({'error': 'Invalid request'}), 400

    current_password = data.get('current_password', '')
    new_password = data.get('new_password', '')

    if not check_password_hash(user.password_hash, current_password):
        return jsonify({'error': 'Current password is incorrect'}), 400

    if len(new_password) < 8:
        return jsonify({'error': 'New password must be at least 8 characters'}), 400

    user.password_hash = generate_password_hash(new_password)
    db.session.commit()
    return jsonify({'success': True, 'message': 'Password changed successfully'})


@app.route('/robots.txt')
def robots():
    """V12.11: Serve robots.txt for search engines."""
    content = f"""User-agent: *
Allow: /
Disallow: /admin/
Disallow: /api/
Disallow: /dashboard/
Disallow: /my-leads
Disallow: /early-intel
Disallow: /analytics
Disallow: /account
Disallow: /saved-leads
Disallow: /saved-searches
Disallow: /billing
Disallow: /onboarding
Disallow: /logout
Disallow: /reset-password
Disallow: /login
Disallow: /signup

# Crawl-delay for polite crawling
Crawl-delay: 1

Sitemap: {SITE_URL}/sitemap.xml
"""
    return Response(content, mimetype='text/plain')


# ===========================
# BLOG SYSTEM
# ===========================
import markdown
import re

BLOG_DIR = os.path.join(os.path.dirname(__file__), 'blog')


def parse_blog_post(filename):
    """Parse a markdown blog post with frontmatter."""
    filepath = os.path.join(BLOG_DIR, filename)
    if not os.path.exists(filepath):
        return None

    with open(filepath, 'r') as f:
        content = f.read()

    # Parse frontmatter (YAML between --- markers)
    meta = {}
    body = content
    if content.startswith('---'):
        parts = content.split('---', 2)
        if len(parts) >= 3:
            for line in parts[1].strip().split('\n'):
                if ':' in line:
                    key, val = line.split(':', 1)
                    meta[key.strip()] = val.strip().strip('"').strip("'")
            body = parts[2]

    # Convert markdown to HTML
    html = markdown.markdown(body, extensions=['fenced_code', 'tables'])

    # Extract excerpt (first 160 chars of text)
    text_only = re.sub(r'<[^>]+>', '', html)
    excerpt = text_only[:160].strip() + '...' if len(text_only) > 160 else text_only

    # V12.26: Parse FAQs if present in frontmatter (JSON array format)
    faqs = []
    if 'faqs' in meta:
        try:
            faqs = json.loads(meta['faqs'])
        except (json.JSONDecodeError, TypeError):
            pass

    return {
        'slug': filename.replace('.md', ''),
        'title': meta.get('title', 'Untitled'),
        'date': meta.get('date', ''),
        'author': meta.get('author', 'PermitGrab Team'),
        'excerpt': excerpt,
        'content': html,
        'keywords': meta.get('keywords', ''),
        'faqs': faqs,
    }


def get_all_blog_posts():
    """Get all blog posts sorted by date."""
    if not os.path.exists(BLOG_DIR):
        return []

    posts = []
    for filename in os.listdir(BLOG_DIR):
        if filename.endswith('.md'):
            post = parse_blog_post(filename)
            if post:
                posts.append(post)

    # Sort by date descending
    posts.sort(key=lambda x: x.get('date', ''), reverse=True)
    return posts


# V79: Old blog routes removed — now using BLOG_POSTS data structure (see line ~6178)

# ===========================
# SCHEDULED DATA COLLECTION
# ===========================
def scheduled_collection():
    """V12.50: Run delta collection every 6 hours. V13.2: Added auto-discovery."""
    # Wait for initial data to be ready (reduced from 30 — SQLite startup is instant)
    print(f"[{datetime.now()}] V12.50: Scheduled collector waiting 5 minutes for startup...")
    time.sleep(300)  # 5 minutes

    # Track when we last ran permit history (run weekly)
    last_history_run = None
    # V13.2: Track when we last ran auto-discovery (run daily)
    last_discovery_run = None

    while True:
        # V242 P0: yield to license-import. Concurrent writes against
        # SQLite WAL stretch a 12s NY import into 51 min and block the
        # health endpoint. Check every 30s until the import finishes.
        try:
            from license_enrichment import is_import_running
            if is_import_running():
                print(f"[{datetime.now()}] V242: collection cycle paused — license import in progress")
                time.sleep(30)
                continue
        except Exception:
            pass  # license_enrichment import failure is non-fatal

        # V229 C1: capture cycle start for dynamic sleep calculation below
        _v229_cycle_start = time.time()
        print(f"[{datetime.now()}] V12.50: Starting scheduled collection cycle...")

        # V13.3: Each task has its own try/except so one failure doesn't block others
        # Permit collection - V72.1: Disabled include_scrapers to prevent memory crash
        try:
            from collector import collect_refresh, collect_permit_history
            collect_refresh(days_back=7)
            print(f"[{datetime.now()}] Refresh collection complete.")
        except Exception as e:
            print(f"[{datetime.now()}] Permit collection error: {e}")
            import traceback
            traceback.print_exc()

        # V229 D2: Prune old permits (keep last 365 days; was 90).
        # delete_old_permits also skips cities stale >30d so a dead-upstream
        # city doesn't get its entire history wiped by the daily prune.
        try:
            deleted = permitdb.delete_old_permits(days=365)
            if deleted > 0:
                print(f"[{datetime.now()}] Pruned {deleted} old permits.")
        except Exception as e:
            print(f"[{datetime.now()}] Prune error: {e}")

        # V183: Refresh contractor profiles from new permit data.
        # Runs BEFORE violations so that update_city_emblems (called at
        # end of collect_violations) reflects both new profiles AND
        # new violations in a single pass.
        try:
            import time as _timer
            _t_prof = _timer.time()
            from contractor_profiles import refresh_contractor_profiles
            prof_result = refresh_contractor_profiles()
            print(f"[{datetime.now()}] [DAEMON] Profile refresh: "
                  f"{prof_result['profiles_upserted']} profiles across "
                  f"{prof_result['cities_processed']} cities, "
                  f"{_timer.time() - _t_prof:.1f}s", flush=True)
        except Exception as e:
            print(f"[{datetime.now()}] [DAEMON] Profile refresh error (non-fatal): {e}", flush=True)

        # V162: Violation collection (daily)
        # V182 PR2: collect_violations() calls update_city_emblems() at end,
        # which sets has_enrichment (from profiles above) + has_violations.
        # V214: wrap in collection_log so silent insert failures (V213 Carto
        # bug class) show up as status='error' / records_inserted=0 instead of
        # disappearing into the underlying try/except in violation_collector.
        try:
            import time as _v214_t
            from violation_collector import collect_violations
            _v214_start = _v214_t.time()
            results = collect_violations()
            _v214_elapsed = _v214_t.time() - _v214_start
            print(f"[{datetime.now()}] Violation collection complete.")
            # V215: results[slug] is now a dict with per-city diagnostics
            # (inserted, api_rows_returned, duplicate_rows_skipped, api_url,
            # query_params). Three-state status:
            #   success      — actually inserted rows
            #   caught_up    — API returned rows but all were dupes (healthy)
            #   no_api_data  — API returned nothing (broken feed / bad query)
            #   error        — endpoint raised
            try:
                _v214_conn = permitdb.get_connection()
                for _slug, _agg in (results or {}).items():
                    if not isinstance(_agg, dict):
                        # Back-compat path if collector ever reverts to int
                        _agg = {'inserted': int(_agg or 0),
                                'api_rows_returned': 0,
                                'duplicate_rows_skipped': 0,
                                'api_url': None, 'query_params': None,
                                'error': None}
                    _ins = _agg.get('inserted') or 0
                    _api_rows = _agg.get('api_rows_returned') or 0
                    _dupes = _agg.get('duplicate_rows_skipped') or 0
                    _err = _agg.get('error')
                    if _err:
                        _status = 'error'
                    elif _ins > 0:
                        _status = 'success'
                    elif _api_rows > 0:
                        _status = 'caught_up'
                    else:
                        # V220 T2: 0 rows returned is ambiguous — could be a
                        # broken endpoint OR legitimately caught up when the
                        # date filter excludes everything. Disambiguate by
                        # checking whether we have any existing violations
                        # for this city. If we do, the source has worked
                        # before and this cycle just had nothing new.
                        _has_prior = False
                        try:
                            _cnt_row = _v214_conn.execute(
                                "SELECT 1 FROM violations WHERE prod_city_id IN "
                                "(SELECT id FROM prod_cities WHERE city_slug = ?) LIMIT 1",
                                (_slug,),
                            ).fetchone()
                            _has_prior = _cnt_row is not None
                        except Exception:
                            pass
                        _status = 'caught_up' if _has_prior else 'no_api_data'
                    _v214_conn.execute("""
                        INSERT INTO collection_log
                          (city_slug, collection_type, status,
                           records_fetched, records_inserted,
                           duration_seconds, api_url, query_params,
                           api_rows_returned, duplicate_rows_skipped,
                           error_message)
                        VALUES (?, 'violations', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (_slug, _status, _api_rows, _ins,
                          round(_v214_elapsed, 2),
                          _agg.get('api_url'), _agg.get('query_params'),
                          _api_rows, _dupes, _err))
                _v214_conn.commit()
            except Exception as _e:
                print(f"[{datetime.now()}] [V215] collection_log write failed "
                      f"(non-fatal): {_e}")
        except Exception as e:
            print(f"[{datetime.now()}] Violation collection error: {e}")
            try:
                _v214_conn = permitdb.get_connection()
                _v214_conn.execute("""
                    INSERT INTO collection_log
                      (city_slug, collection_type, status, error_message)
                    VALUES ('__all__', 'violations', 'error', ?)
                """, (str(e)[:500],))
                _v214_conn.commit()
            except Exception:
                pass

        # V229 B2: deleted the V201/V203 Google Places block. GCP billing
        # has been off since V210, so every daemon cycle was printing
        # "Enrichment skipped (no GOOGLE_PLACES_API_KEY)" and doing nothing.
        # The V209 web_enrichment.enrich_batch() call below covers the same
        # ground using DDG HTML + domain guess, with zero API spend.

        # V209: Bulk web enrichment — complements the V201 priority+tail loop by
        # sweeping pending profiles across ALL cities (ordered by total_permits
        # DESC), not just the top-20 buckets. Cap of 50 lookups/cycle at
        # ~$0.034 each = ~$1.70/cycle extra, $40/day at hourly cadence.
        try:
            from web_enrichment import enrich_batch as _v209_enrich
            # V229 C5: bumped limit 50 -> 200. DDG accepts bursts of this size
            # when spread over 3-5s per query. With 84K unenriched profiles
            # and a 20-40% hit rate, 50/hour (= 1,200/day) would take 70+
            # days to drain the queue; 200/hour is ~18 days.
            v209_result = _v209_enrich(limit=200)
            print(f"[{datetime.now()}] [V209] web_enrichment cycle: {v209_result}")
        except Exception as e:
            print(f"[{datetime.now()}] [V209] web_enrichment error (non-fatal): {e}")

        # V226 T8: staleness alert pass. Cities whose MAX(filing_date) is
        # >21 days behind today haven't been updated this whole cycle
        # series. Log a stale_warning to collection_log so the admin
        # dashboard surfaces them without us having to ssh + sqlite each
        # morning. Does NOT auto-retry those — most of the >21d cases
        # have turned out to be upstream-dead sources and retrying wastes
        # the HTTP round trip. Retries 7-21d range where the collector
        # might actually pick up newer rows on the next try.
        try:
            _stale_conn = permitdb.get_connection()
            # V227 T5: pull expected_freshness_days (default 14 if NULL),
            # compare actual age, escalate when age > 3 * expected by
            # flipping prod_cities.needs_attention so the admin dashboard
            # surfaces it without further SSH-ing.
            _stale_rows = _stale_conn.execute("""
                SELECT pc.id, pc.city_slug, pc.source_type,
                       COALESCE(pc.expected_freshness_days, 14) AS expected_days,
                       MAX(p.filing_date) newest,
                       CAST(julianday('now') - julianday(MAX(p.filing_date)) AS INTEGER) age_days
                FROM prod_cities pc
                JOIN permits p ON p.source_city_key = pc.city_slug
                WHERE pc.status = 'active'
                GROUP BY pc.city_slug
                HAVING age_days > expected_days
                ORDER BY age_days DESC
                LIMIT 50
            """).fetchall()
            _escalated = 0
            for _row in _stale_rows:
                if hasattr(_row, 'keys'):
                    _pcid, _slug, _st, _exp, _new, _age = (
                        _row['id'], _row['city_slug'], _row['source_type'],
                        _row['expected_days'], _row['newest'], _row['age_days'])
                else:
                    _pcid, _slug, _st, _exp, _new, _age = _row
                # Three-tier escalation
                if _age is not None and _exp and _age > _exp * 3:
                    _status = 'critical_stale'  # needs manual attention
                elif _age is not None and _exp and _age > _exp:
                    _status = 'stale_warning'
                else:
                    _status = 'aging'
                try:
                    _stale_conn.execute("""
                        INSERT INTO collection_log
                          (city_slug, collection_type, status, error_message,
                           duration_seconds)
                        VALUES (?, 'staleness_check', ?, ?, 0)
                    """, (_slug, _status,
                          f"newest permit is {_age}d old (expected {_exp}d)"))
                except Exception:
                    pass
                # V227 T5: flip needs_attention for critical-stale cities
                if _status == 'critical_stale':
                    try:
                        _stale_conn.execute("""
                            UPDATE prod_cities
                            SET needs_attention = 1,
                                attention_reason = ?
                            WHERE id = ?
                        """, (f"stale {_age}d, {_exp*3}d threshold exceeded", _pcid))
                        _escalated += 1
                    except Exception:
                        pass
            _stale_conn.commit()
            if _stale_rows:
                print(f"[{datetime.now()}] [V227] staleness check logged "
                      f"{len(_stale_rows)} stale cities, escalated {_escalated}")
        except Exception as e:
            print(f"[{datetime.now()}] [V227] staleness check error: {e}")

        # V212-3: Resync prod_cities.newest_permit_date from actual permits each
        # cycle so the freshness badge doesn't go stale while the collector
        # itself is still working. The existing admin recalc endpoint
        # (/api/admin/recalc-freshness V170) only fills NULL rows; this covers
        # the case where a row has a valid-but-stale newest_permit_date and the
        # permits table has fresher data for the same (city,state).
        try:
            _freshness_conn = permitdb.get_connection()
            _fc = _freshness_conn.cursor()
            _fc.execute("""
                UPDATE prod_cities
                SET newest_permit_date = COALESCE(
                        (SELECT MAX(filing_date) FROM permits
                         WHERE permits.city = prod_cities.city
                           AND permits.state = prod_cities.state),
                        newest_permit_date),
                    last_permit_date = COALESCE(
                        (SELECT MAX(filing_date) FROM permits
                         WHERE permits.city = prod_cities.city
                           AND permits.state = prod_cities.state),
                        last_permit_date)
                WHERE source_type IS NOT NULL
                  AND source_type NOT IN ('', 'none')
                  AND total_permits > 0
            """)
            _freshness_conn.commit()
            # V212-3: Re-bucket data_freshness from the just-refreshed dates
            _fc.execute("""
                UPDATE prod_cities
                SET data_freshness = CASE
                    WHEN newest_permit_date IS NULL OR newest_permit_date = '' THEN 'no_data'
                    WHEN DATE(SUBSTR(newest_permit_date, 1, 10)) >= DATE('now', '-7 days') THEN 'fresh'
                    WHEN DATE(SUBSTR(newest_permit_date, 1, 10)) >= DATE('now', '-30 days') THEN 'current'
                    WHEN DATE(SUBSTR(newest_permit_date, 1, 10)) >= DATE('now', '-60 days') THEN 'aging'
                    WHEN DATE(SUBSTR(newest_permit_date, 1, 10)) >= DATE('now', '-90 days') THEN 'stale'
                    ELSE 'very_stale'
                END
                WHERE source_type IS NOT NULL AND source_type NOT IN ('', 'none')
            """)
            _freshness_conn.commit()
            print(f"[{datetime.now()}] [V212] prod_cities freshness resync complete.")
        except Exception as e:
            print(f"[{datetime.now()}] [V212] freshness resync error (non-fatal): {e}")

        # V168: Removed dead signal_collector, city_health, discovery calls (files deleted in V163)

        print(f"[{datetime.now()}] All collection tasks complete.")

        # V18: Run staleness check after collection
        try:
            from collector import staleness_check
            print(f"[{datetime.now()}] V18: Running staleness check...")
            staleness_stats = staleness_check()
            print(f"[{datetime.now()}] V18: Staleness check done - {staleness_stats.get('paused', 0)} paused, {staleness_stats.get('stale', 0)} stale")
        except Exception as e:
            print(f"[{datetime.now()}] V18: Staleness check error: {e}")

        # V64: Run freshness classification
        print(f"[{datetime.now()}] V64: Running freshness classification...")
        try:
            from collector import classify_city_freshness
            freshness = classify_city_freshness()
            summary = freshness.get('summary', {})
            attention_count = freshness.get('total_needing_attention', 0)
            print(f"[{datetime.now()}] V64: Freshness: {summary}")
            if attention_count > 0:
                print(f"[{datetime.now()}] V64: WARNING: {attention_count} cities need attention")
        except Exception as e:
            print(f"[{datetime.now()}] V64: Freshness classification failed: {e}")

        # V16: Track last successful collection run
        global _last_collection_run
        _last_collection_run = datetime.now()

        # V229 C1: dynamic cycle sleep. Was a flat time.sleep(3600) regardless
        # of how long the cycle took, so a 45-min cycle became a real 1h45m
        # interval. Target 30 min between cycle completions (minimum 5-min
        # rest, maximum 1h rest) — under normal load that's ~2 full passes
        # per hour instead of <1.
        _duration = max(0, int(time.time() - _v229_cycle_start))
        _sleep_for = max(300, min(3600, 1800 - _duration))
        print(f"[{datetime.now()}] V229 C1: cycle took {_duration}s, "
              f"sleeping {_sleep_for}s", flush=True)
        time.sleep(_sleep_for)


# ===========================
# V12.53: EMAIL SCHEDULER
# ===========================

def schedule_email_tasks():
    """V12.53: Schedule all email tasks to run at specific times daily.

    - Daily digest: 7 AM ET (12:00 UTC)
    - Trial lifecycle check: 8 AM ET (13:00 UTC)
    - Onboarding nudges: 9 AM ET (14:00 UTC)

    V64: Added robust error logging, heartbeat, and crash recovery.
    V78: Added DIGEST_STATUS tracking and fixed timing (5-min checks during 7 AM window).
    """
    global DIGEST_STATUS

    # V78: Mark thread as started
    DIGEST_STATUS['thread_started'] = datetime.now().isoformat()
    DIGEST_STATUS['thread_alive'] = True

    # V64: Wrap imports in try/except to catch missing dependencies
    try:
        import pytz
        from email_alerts import send_daily_digest, check_trial_lifecycle, check_onboarding_nudges
    except ImportError as e:
        print(f"[{datetime.now()}] [CRITICAL] Email scheduler failed to import: {e}")
        import traceback
        traceback.print_exc()
        DIGEST_STATUS['thread_alive'] = False
        DIGEST_STATUS['last_digest_result'] = f'import_error: {e}'
        return  # Don't silently die — exit with error logged

    # V78: Auto-create subscribers.json if it doesn't exist
    try:
        from pathlib import Path
        subscribers_path = Path("/var/data/subscribers.json")
        if not subscribers_path.exists():
            # Check if /var/data exists (Render persistent disk)
            var_data = Path("/var/data")
            if var_data.exists():
                default_subscribers = [
                    {
                        "email": "wcrainshaw@gmail.com",
                        "active": True,
                        "digest_cities": ["atlanta"],
                        "created_at": datetime.now().strftime("%Y-%m-%d")
                    }
                ]
                subscribers_path.write_text(json.dumps(default_subscribers, indent=2))
                print(f"[{datetime.now()}] V78: Created subscribers.json with default subscriber")
            else:
                print(f"[{datetime.now()}] V78: /var/data not found - running locally, skipping subscribers.json creation")
        else:
            print(f"[{datetime.now()}] V78: subscribers.json already exists")
    except Exception as e:
        print(f"[{datetime.now()}] V78: Could not create subscribers.json: {e}")

    # V68: Wait 3 minutes for initial startup (increased from 2)
    print(f"[{datetime.now()}] V68: Email scheduler waiting 3 minutes for startup...")
    time.sleep(180)

    et = pytz.timezone('America/New_York')

    # V78: Track if we've already run digest today to prevent duplicates
    last_digest_date = None
    # V229 addendum H1: Track lifecycle/onboarding runs too. Without these,
    # the 5-min polling inside the 8:00-8:29 / 9:00-9:29 windows fired
    # check_trial_lifecycle() and check_onboarding_nudges() up to 6 times
    # per day, spamming trial users with duplicate emails.
    last_lifecycle_date = None
    last_onboarding_date = None

    while True:
        try:
            now_utc = datetime.utcnow()
            now_et = datetime.now(et)
            today_date = now_et.date()

            # V78: Update heartbeat timestamp
            DIGEST_STATUS['last_heartbeat'] = datetime.now().isoformat()

            # V64: Heartbeat every cycle so we can verify thread is alive in Render logs
            print(f"[{datetime.now()}] V78: Email scheduler heartbeat: {now_et.strftime('%I:%M %p ET')} (thread_alive=True)")

            # Check if it's time for daily tasks (7-9 AM ET window)
            if 7 <= now_et.hour <= 9:
                # Daily digest at 7 AM ET (run once per day)
                if now_et.hour == 7 and last_digest_date != today_date:
                    print(f"[{datetime.now()}] V78: Running daily digest...")
                    DIGEST_STATUS['last_digest_attempt'] = datetime.now().isoformat()
                    try:
                        sent, failed = send_daily_digest()
                        print(f"[{datetime.now()}] V78: Daily digest complete - {sent} sent, {failed} failed")
                        DIGEST_STATUS['last_digest_result'] = 'success'
                        DIGEST_STATUS['last_digest_sent'] = sent
                        DIGEST_STATUS['last_digest_failed'] = failed
                        last_digest_date = today_date  # Mark as done for today
                        # V158: Log success to DB
                        try:
                            _conn = permitdb.get_connection()
                            _conn.execute("INSERT OR REPLACE INTO system_state (key, value, updated_at) VALUES ('digest_last_success', ?, datetime('now'))", (datetime.now().isoformat(),))
                            _conn.execute("INSERT OR REPLACE INTO system_state (key, value, updated_at) VALUES ('digest_last_sent_count', ?, datetime('now'))", (str(sent),))
                            _conn.execute("INSERT INTO digest_log (recipient_email, permits_count, status) VALUES ('scheduled', ?, 'sent')", (sent,))
                            _conn.commit()
                        except Exception:
                            pass
                    except Exception as e:
                        print(f"[{datetime.now()}] [ERROR] Daily digest failed: {e}")
                        import traceback
                        traceback.print_exc()
                        DIGEST_STATUS['last_digest_result'] = f'error: {e}'
                        # V158: Log failure to DB
                        try:
                            _conn = permitdb.get_connection()
                            _conn.execute("INSERT OR REPLACE INTO system_state (key, value, updated_at) VALUES ('digest_last_error', ?, datetime('now'))", (str(e)[:500],))
                            _conn.execute("INSERT INTO digest_log (recipient_email, status, error_message) VALUES ('scheduled', 'failed', ?)", (str(e)[:500],))
                            _conn.commit()
                        except Exception:
                            pass

                # Trial lifecycle at 8 AM ET (V229 addendum H1: once-per-day guard)
                if now_et.hour == 8 and last_lifecycle_date != today_date:
                    print(f"[{datetime.now()}] V64: Checking trial lifecycle...")
                    try:
                        results = check_trial_lifecycle()
                        print(f"[{datetime.now()}] V64: Trial lifecycle complete - {results}")
                        last_lifecycle_date = today_date
                    except Exception as e:
                        print(f"[{datetime.now()}] [ERROR] Trial lifecycle failed: {e}")
                        import traceback
                        traceback.print_exc()

                # Onboarding nudges at 9 AM ET (V229 addendum H1: once-per-day guard)
                if now_et.hour == 9 and last_onboarding_date != today_date:
                    print(f"[{datetime.now()}] V64: Checking onboarding nudges...")
                    try:
                        sent = check_onboarding_nudges()
                        print(f"[{datetime.now()}] V64: Onboarding nudges complete - {sent} sent")
                        last_onboarding_date = today_date
                    except Exception as e:
                        print(f"[{datetime.now()}] [ERROR] Onboarding nudges failed: {e}")
                        import traceback
                        traceback.print_exc()

        except Exception as e:
            print(f"[{datetime.now()}] [ERROR] Email scheduler error: {e}")
            import traceback
            traceback.print_exc()
            DIGEST_STATUS['last_digest_result'] = f'loop_error: {e}'
            # V64: Wait 5 min on error before retrying, don't die
            time.sleep(300)
            continue

        # V78: Check every 5 minutes during 7-9 AM ET window to not miss digest
        # Check every 30 minutes outside that window to save resources
        if 6 <= now_et.hour <= 9:
            time.sleep(300)  # 5 minutes during morning window
        else:
            time.sleep(1800)  # 30 minutes otherwise


def run_initial_collection():
    """V12.57: Clear stale lock, then run a quick REFRESH (not full 365-day rebuild).
    The SQLite DB on the persistent disk already has all historical data.
    Full collections blocked every REFRESH cycle by holding the lock for hours.
    V68: Added 120s initial delay to prevent pool exhaustion."""
    # V68: Wait 120s before starting to prevent pool exhaustion at startup
    print(f"[{datetime.now()}] V68: initial_collection waiting 120s before starting...")
    time.sleep(120)

    try:
        # V12.57: Clear orphaned lock files from killed instances
        lock_file = os.path.join(DATA_DIR, ".collection_lock")
        if os.path.exists(lock_file):
            try:
                os.remove(lock_file)
                print(f"[{datetime.now()}] V12.57: Cleared stale collection lock file.")
            except Exception as e:
                print(f"[{datetime.now()}] V12.57: Could not clear lock: {e}")

        # V100: 180-day lookback for initial collection to ensure 6-month backfill
        # V72.1: Disabled include_scrapers to prevent memory crash on Render
        print(f"[{datetime.now()}] V100: Running initial collection (180 days for backfill)...")
        from collector import collect_refresh
        collect_refresh(days_back=180)

        # V162: Violation collection
        try:
            from violation_collector import collect_violations
            collect_violations()
        except Exception as e:
            print(f"[{datetime.now()}] Initial violation collection error: {e}")

        # Signal collection
        try:
            from signal_collector import collect_all_signals
            collect_all_signals(days_back=90)
        except Exception as e:
            print(f"[{datetime.now()}] Initial signal collection error: {e}")

        print(f"[{datetime.now()}] V12.57: Initial collection complete.")
    except Exception as e:
        print(f"[{datetime.now()}] Initial collection error: {e}")


# ===========================
# ERROR HANDLERS
# ===========================
@app.errorhandler(404)
def page_not_found(e):
    """Handle 404 errors with a styled page."""
    footer_cities = get_cities_with_data()
    return render_template('404.html', footer_cities=footer_cities), 404


# V230 S2: dedicated Jinja UndefinedError handler. The 500->503 fallback
# already catches these, but logging them under their own handler makes
# them visible in Render logs with a "[TEMPLATE UNDEFINED]" prefix we
# can grep for — these are nearly always missing render_template kwargs
# (see V229 hotfix: freshness_age_days on state_city_landing).
from jinja2.exceptions import UndefinedError as _JinjaUndefinedError

@app.errorhandler(_JinjaUndefinedError)
def handle_template_undefined(error):
    try:
        import traceback
        print(f"[TEMPLATE UNDEFINED] {request.path} — {error}", flush=True)
        traceback.print_exc()
    except Exception:
        pass
    try:
        footer_cities = get_cities_with_data()
    except Exception:
        footer_cities = []
    # Reuse the 404 template as a graceful "try again" shell; Retry-After
    # keeps Googlebot from dropping the URL while we fix the template.
    resp = render_template('404.html', footer_cities=footer_cities,
                           show_city_suggestions=False)
    return resp, 503, {'Retry-After': '600'}


@app.errorhandler(500)
def internal_server_error(e):
    """V222 T2: Convert uncaught exceptions into 503 (Service Unavailable)
    with a Retry-After hint. 500 signals to Googlebot that the URL is
    permanently broken and should be removed from the index; 503 tells
    Googlebot "try again later" and preserves the URL's indexation.
    Search Console flagged 176 pages as 5xx before this change — each
    one was hurting our crawl budget.

    Also logs the exception so we can diagnose the root cause in Render
    logs without depending on visible user reports.
    """
    try:
        import traceback
        print(f"[500->503] {request.path} — {e.__class__.__name__}: {e}", flush=True)
        traceback.print_exc()
    except Exception:
        pass
    try:
        footer_cities = get_cities_with_data()
    except Exception:
        footer_cities = []
    resp = render_template('404.html', footer_cities=footer_cities,
                           show_city_suggestions=False)
    # 503 + Retry-After tells Googlebot to try again, not drop the URL.
    return resp, 503, {'Retry-After': '600'}


# ===========================
# STARTUP: DATA COLLECTION
# ===========================
# This runs when gunicorn imports the module (or when running directly).
# Use a flag to prevent multiple workers from each starting collectors.

_collector_started = False


def _test_outbound_connectivity():
    """V12.2: Quick test of outbound API access on startup."""
    import requests
    test_session = requests.Session()
    test_session.headers.update({
        "User-Agent": "PermitGrab/1.0 (permit lead aggregator; contact@permitgrab.com)",
        "Accept": "application/json",
    })
    test_urls = [
        "https://data.cityofnewyork.us/resource/rbx6-tga4.json?$limit=1",
        "https://data.cityofchicago.org/resource/ydr8-5enu.json?$limit=1",
        "https://data.lacity.org/resource/yv23-pmwf.json?$limit=1",
    ]
    print(f"[{datetime.now()}] Testing outbound API connectivity...")
    for url in test_urls:
        try:
            resp = test_session.get(url, timeout=15)
            print(f"  [NET TEST] {url[:50]}... → {resp.status_code} ({len(resp.content)} bytes)")
        except Exception as e:
            print(f"  [NET TEST] {url[:50]}... → FAILED: {type(e).__name__}: {str(e)[:80]}")


def start_collectors():
    """Start background data collection threads. Safe to call multiple times.

    V66: Stagger thread starts to prevent connection pool stampede.
    """
    global _collector_started
    if _collector_started:
        return
    _collector_started = True

    os.makedirs(DATA_DIR, exist_ok=True)

    # V12.2: Test network connectivity before starting threads
    _test_outbound_connectivity()

    # V75: Apply known fixes to broken city_sources configs
    print(f"[{datetime.now()}] V75: Applying known config fixes...")
    try:
        fixes = fix_known_broken_configs()
        print(f"[{datetime.now()}] V75: Applied {len(fixes)} config fixes")
    except Exception as e:
        print(f"[{datetime.now()}] V75: Config fix error (non-fatal): {e}")

    # V77: Sync CITY_REGISTRY to prod_cities — activates paused cities
    print(f"[{datetime.now()}] V77: Syncing CITY_REGISTRY to prod_cities...")
    try:
        sync_city_registry_to_prod_cities()
        print(f"[{datetime.now()}] V77: Registry sync complete")
    except Exception as e:
        print(f"[{datetime.now()}] V77: Registry sync error (non-fatal): {e}")

    print(f"[{datetime.now()}] V67: Starting background collectors with staggered init...")

    # V67: Stagger each thread start by 30 seconds to avoid pool exhaustion (was 10s in V66)
    # Initial collection thread
    initial_thread = threading.Thread(target=run_initial_collection, name='initial_collection', daemon=True)
    initial_thread.start()
    print(f"[{datetime.now()}] V67: Initial collection thread started, waiting 30s...")
    time.sleep(30)

    # Scheduled daily collection thread
    collector_thread = threading.Thread(target=scheduled_collection, name='scheduled_collection', daemon=True)
    collector_thread.start()
    print(f"[{datetime.now()}] V67: Scheduled collection thread started, waiting 30s...")
    time.sleep(30)

    # V229 C5: dedicated enrichment daemon. scheduled_collection still runs
    # enrich_batch(limit=200) per cycle, but that only fires when the
    # collection cycle completes (30-60 min apart). This second thread
    # runs enrichment on its own 30-min cadence so the queue drains
    # steadily even when a collection cycle is slow/stuck.
    def _enrichment_daemon():
        time.sleep(600)  # let collection warm up first
        while True:
            # V242 P0: same import-pause guard the collection loop uses.
            try:
                from license_enrichment import is_import_running
                if is_import_running():
                    print(f"[{datetime.now()}] V242: enrichment daemon paused — license import in progress",
                          flush=True)
                    time.sleep(30)
                    continue
            except Exception:
                pass
            try:
                from web_enrichment import enrich_batch
                result = enrich_batch(limit=200)
                print(f"[{datetime.now()}] [V229 C5] enrichment daemon: {result}",
                      flush=True)
            except Exception as e:
                print(f"[{datetime.now()}] [V229 C5] enrichment daemon error: {e}",
                      flush=True)
            time.sleep(1800)  # 30 min between passes

    try:
        enrichment_thread = threading.Thread(
            target=_enrichment_daemon, name='enrichment_daemon', daemon=True
        )
        enrichment_thread.start()
        print(f"[{datetime.now()}] V229 C5: enrichment daemon started")
    except Exception as e:
        print(f"[{datetime.now()}] V229 C5: enrichment daemon failed to start: {e}")

    # V12.53: Email scheduler thread
    # V229 B6: removed — _deferred_startup() at line ~1041 already spawns
    # this same thread. Having two copies run simultaneously risked sending
    # duplicate digest emails. The earlier spawn wins; this block is a
    # no-op now, kept as a comment so the numbering in the startup sequence
    # still matches prior-version logs.

    # V166: Removed dead _fix_socrata_addresses thread (function deleted in V163)
    # V166: Removed dead autonomy_engine thread (file deleted in V163)

    print(f"[{datetime.now()}] V67: All collector threads started (staggered over ~2 minutes).")

# V12.12: Preload existing data from disk BEFORE starting collectors
# This ensures stale data is served immediately rather than showing 0 permits
preload_data_from_disk()

# V229 addendum K1: single, lock-guarded spawner for _deferred_startup.
# Called from the before_request hook AND from module import. This
# guarantees daemon threads start even if no HTTP request arrives
# through the before_request chain (e.g. healthcheck routes registered
# before the hook, or edge cases around app.test_client). The
# threading.Lock makes double-spawn impossible regardless of caller.
_DEFERRED_STARTUP_LOCK = threading.Lock()
_DEFERRED_STARTUP_SPAWNED = False

def _ensure_deferred_startup_spawned():
    global _DEFERRED_STARTUP_SPAWNED
    with _DEFERRED_STARTUP_LOCK:
        if _DEFERRED_STARTUP_SPAWNED:
            return
        _DEFERRED_STARTUP_SPAWNED = True
    threading.Thread(
        target=_deferred_startup, daemon=True, name='deferred_startup'
    ).start()

# V229 addendum K1: kick daemons off at import time so they don't depend
# on an HTTP request landing first.
_ensure_deferred_startup_spawned()

# V66: Removed module-level DB init — now deferred to first request via _deferred_startup()
# This prevents connection pool exhaustion during gunicorn startup.
# The sync_city_registry_to_prod(), sync_city_registry_to_prod_cities(), and
# start_collectors() are all called from _deferred_startup() on first HTTP request.
print(f"[{datetime.now()}] V70: Module loaded — Postgres pool DISABLED, SQLite only mode")


# ===========================
# MAIN
# ===========================
if __name__ == '__main__':
    # Local development only (gunicorn handles production)
    print("=" * 50)
    print("PermitGrab Server Starting")
    print(f"Dashboard: http://localhost:5000")
    print(f"API: http://localhost:5000/api/permits")
    print("=" * 50)

    app.run(host='0.0.0.0', port=5000, debug=True)
# V146: Double-deploy test Sun Apr 12 09:44:58 CDT 2026
