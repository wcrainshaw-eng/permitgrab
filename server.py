"""
PermitGrab — Production Server
Flask app that serves the dashboard and API endpoints
Deploy to any VPS (DigitalOcean, Railway, Render, etc.)
"""

from flask import Flask, jsonify, request, send_from_directory, session, render_template, Response, redirect, abort, g, make_response
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
# V459 (CODE_V456): flask-login per directive. Imported here so the
# rest of the module can reference current_user and login_user without
# late imports. Initialization (LoginManager().init_app + user_loader)
# happens after the User SQLAlchemy model is defined, below.
from flask_login import (
    LoginManager,
    UserMixin,
    login_user as _flask_login_user,
    logout_user as _flask_logout_user,
    current_user,
)
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
# V414 (CODE_V365b PHASE B.2): WORKER_MODE flag — when set on the Render
# web service, start_collectors() returns immediately so the web process
# is HTTP-only. Daemon threads run in the separate worker.py service.
# Default unset for backward compatibility (single-process deployments
# + local dev still work).
WORKER_MODE = os.environ.get('WORKER_MODE', '').lower() in ('1', 'true', 'yes')
if WORKER_MODE:
    print(f"[V414] WORKER_MODE=true — web process will not start collector daemons.", flush=True)

_v229_fallback_key = os.environ.get('SECRET_KEY')
if not _v229_fallback_key:
    _v229_fallback_key = secrets.token_hex(32)
    print("[V229 E2] WARNING: SECRET_KEY env var is not set — using an "
          "ephemeral random key. User sessions will be invalidated on every "
          "restart. Set SECRET_KEY on Render to persist sessions.", flush=True)
app.secret_key = _v229_fallback_key

# V398 (CODE_V364 Part 5.1): gzip-compress responses larger than 500 bytes.
# Per the directive: "Typically reduces HTML/JSON response size by 60-70%."
# That's a lot of bytes off Render's 512MB-shared bandwidth + faster TTFB
# for cold ad-click visitors on slow connections. Wrapped in try/except so
# the dep is a soft requirement — if Flask-Compress isn't installed yet
# the app still boots normally, just without compression.
try:
    from flask_compress import Compress  # noqa: E402
    Compress(app)
    print("[V398] flask-compress enabled (gzip on responses >500 bytes)", flush=True)
except ImportError:
    print("[V398] flask-compress not installed — responses will not be gzipped. "
          "Add 'flask-compress' to requirements.txt to enable.", flush=True)

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
    """V145: Create sources + violations + enrichment tables. V164: Runs ONCE.

    V471 PR2-prep: also runs Postgres schema init (was a module-level
    `with app.app_context(): db.create_all()` block before — moved here
    so module import does no DB I/O). Lock-guarded once-runner; safe to
    call from every before_request, but only the first call does work.
    """
    # Postgres schema (db.create_all + ALTER TABLE migrations + V255
    # consolidation). Idempotent and lock-guarded inside the function.
    _init_postgres_schema()

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
        -- V251 F11 perf: covers the contractor-violation cross-ref JOIN on
        -- each city page. Without these, Chicago's 19k violations × 25k
        -- permits join ran 20+ seconds per page render and intermittently
        -- tipped Render's gateway into 502s.
        CREATE INDEX IF NOT EXISTS idx_violations_prod_city_address
            ON violations(prod_city_id, address);
        CREATE INDEX IF NOT EXISTS idx_permits_prod_city_address
            ON permits(prod_city_id, address);

        -- V255 P0#3: composite indexes covering the city-page + trade-
        -- filter query pattern. Render logs showed /permits/<city>?trade=X
        -- taking 22-99s on big cities for bot crawls — full scan on
        -- 25k-row city tables. These let SQLite hit the filter clause
        -- AND the filing_date sort in one index seek.
        CREATE INDEX IF NOT EXISTS idx_permits_prod_city_trade_date
            ON permits(prod_city_id, trade_category, filing_date DESC);
        CREATE INDEX IF NOT EXISTS idx_permits_prod_city_date
            ON permits(prod_city_id, filing_date DESC);
        CREATE INDEX IF NOT EXISTS idx_permits_prod_city_zip
            ON permits(prod_city_id, zip);

        -- V252 F1.5: property owner append (Enterprise feature). Schema in
        -- place so the per-city import ETL can write here as soon as each
        -- county's assessor API is wired up (Cook County 5pge-nu6u,
        -- Maricopa parcel search, NYC PLUTO 64uk-42ks, etc.). Stays empty
        -- until import endpoints are implemented per-county.
        CREATE TABLE IF NOT EXISTS property_owners (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            address TEXT NOT NULL,
            city TEXT,
            state TEXT,
            zip TEXT,
            owner_name TEXT,
            owner_mailing_address TEXT,
            parcel_id TEXT,
            source TEXT,
            last_updated TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_property_owners_address
            ON property_owners(address);
        CREATE INDEX IF NOT EXISTS idx_property_owners_parcel
            ON property_owners(parcel_id);
    ''')
    # V359 HOTFIX: V278's UNIQUE INDEX on (address, owner_name, source) used
    # to live inside the executescript above, which made any column-mismatch
    # crash atomic — taking down the whole @app.before_request migration
    # chain and triggering "no such table: contractor_profiles" downstream.
    # Wrapping it in try/except so a stale schema or already-existing
    # mismatched index doesn't kill subsequent migrations.
    try:
        conn.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_property_owners_unique
            ON property_owners(address, owner_name, source)""")
    except Exception as _po_idx_err:
        print(f"[V359 HOTFIX] property_owners unique index skipped: {_po_idx_err}", flush=True)

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

    # V471 PR2-prep: daemon startup is THIS hook's job again. The
    # module-level spawn at the bottom of server.py was removed because
    # it raced blueprint imports against Python's import lock during
    # worker re-fork (after V455 self-recycle), wedging the new worker.
    # The lock-guarded spawner makes a re-call here a no-op.
    _ensure_deferred_startup_spawned()

    # V481: V480's auto-spawn-on-first-request was the wrong layer to fix
    # the recycled-daemon problem. Spawning scheduled_collection inside
    # the request hook re-enters the same single-vCPU pool as the 12
    # gthread request handlers — the daemon's stats refresh + concurrent
    # V474 buyer-intent aggregate queries in city_landing_inner saturate
    # the GIL and wedge every request thread. The real fix is to move
    # the daemons to the permitgrab-worker Background Worker service
    # (already declared in render.yaml — never enabled in Render
    # dashboard) and gate the daemon spawn on os.environ['WORKER_MODE'].
    # Until that lands, daemons must be started manually via
    # /api/admin/start-collectors after each deploy or worker recycle —
    # the historical pre-V480 contract.


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


def _v471_sync_city_registry_to_city_sources():
    """V471 PR3: upsert every CITY_REGISTRY + BULK_SOURCES entry into the
    city_sources table so the runtime config can move from a Python dict
    to the DB. Idempotent — safe to call on every startup; uses INSERT OR
    REPLACE so updates flow through too.

    The Python CITY_REGISTRY remains the source of truth (in
    city_registry_data.py) until a follow-up PR retires it; this sync
    populates city_sources so downstream code (collector, scorecards,
    admin endpoints) can switch over without a migration cliff.
    """
    try:
        from city_registry_data import CITY_REGISTRY, BULK_SOURCES
        conn = permitdb.get_connection()
        upserted_city = 0
        upserted_bulk = 0
        for key, cfg in CITY_REGISTRY.items():
            try:
                conn.execute("""
                    INSERT OR REPLACE INTO city_sources
                    (source_key, name, state, platform, mode, endpoint,
                     dataset_id, field_map, date_field, city_field,
                     limit_per_page, status, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """, (
                    key,
                    cfg.get('name', key),
                    cfg.get('state', ''),
                    cfg.get('platform', 'unknown'),
                    'city',
                    cfg.get('endpoint', ''),
                    cfg.get('dataset_id') or '',
                    json.dumps(cfg.get('field_map', {}), default=str),
                    cfg.get('date_field') or 'date',
                    cfg.get('city_field') or cfg.get('city_filter', {}).get('field') if isinstance(cfg.get('city_filter'), dict) else None,
                    cfg.get('limit', 2000),
                    'active' if cfg.get('active') else 'inactive',
                ))
                upserted_city += 1
            except Exception as _e:
                pass
        for key, cfg in BULK_SOURCES.items():
            try:
                conn.execute("""
                    INSERT OR REPLACE INTO city_sources
                    (source_key, name, state, platform, mode, endpoint,
                     dataset_id, field_map, date_field, city_field,
                     limit_per_page, status, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """, (
                    key,
                    cfg.get('name', key),
                    cfg.get('state', ''),
                    cfg.get('platform', 'unknown'),
                    'bulk',
                    cfg.get('endpoint', ''),
                    cfg.get('dataset_id') or '',
                    json.dumps(cfg.get('field_map', {}), default=str),
                    cfg.get('date_field') or 'date',
                    cfg.get('city_field'),
                    cfg.get('limit', 2000),
                    'active' if cfg.get('active') else 'inactive',
                ))
                upserted_bulk += 1
            except Exception as _e:
                pass
        conn.commit()
        conn.close()
        print(f"[{datetime.now()}] V471 PR3: city_sources synced — "
              f"{upserted_city} city + {upserted_bulk} bulk rows", flush=True)
    except Exception as e:
        print(f"[{datetime.now()}] V471 PR3: sync error (non-fatal): {e}", flush=True)


def _deferred_startup():
    """V69: Mark startup done but DO NOT start any background threads.
    V93: Email scheduler is now auto-started (doesn't need Postgres).

    V252 CI fix: short-circuit under TESTING=1 so pytest's import of
    server.py + a client.get() doesn't spawn v146_autostart /
    v106_maintenance / email_scheduler daemon threads that keep
    printing to stdout past interpreter shutdown and core-dump the
    test process (exit 134) after all assertions pass.
    """
    global _startup_done
    if _startup_done:
        return
    _startup_done = True
    if os.environ.get('TESTING') or os.environ.get('PYTEST_CURRENT_TEST'):
        print(f"[{datetime.now()}] V252 CI: _deferred_startup skipped under TESTING", flush=True)
        return
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

    # V471 PR3: sync CITY_REGISTRY + BULK_SOURCES into city_sources so the
    # runtime config can move from a Python dict to the DB. Idempotent;
    # uses INSERT OR REPLACE so updates flow through.
    try:
        _v471_sync_city_registry_to_city_sources()
    except Exception as e:
        print(f"[{datetime.now()}] V471 PR3: city_sources sync error (non-fatal): {e}")

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

    # V462 (CODE_V459 task 3): generate one-time password reset link for Gomes
    # Benjamim if they haven't set a password yet. Gomes paid 2026-04-27 but
    # was provisioned with a temp password they were never told. The existing
    # reset mechanism uses a JSON file (DATA_DIR/password_reset_tokens.json) —
    # not a User column — so we write to that file directly.
    try:
        _gomes = User.query.filter_by(email='main@onpointgb.com').first()
        if _gomes and not getattr(_gomes, 'last_login_at', None):
            import secrets as _v462_secrets
            _v462_existing = load_reset_tokens()
            _v462_has = any(
                v.get('email', '').lower() == 'main@onpointgb.com'
                and not v.get('used', False)
                and v.get('expires', '') > datetime.utcnow().isoformat()
                for v in _v462_existing.values()
            )
            if not _v462_has:
                _v462_token = _v462_secrets.token_urlsafe(32)
                _v462_existing[_v462_token] = {
                    'email': 'main@onpointgb.com',
                    'expires': (datetime.utcnow() + timedelta(days=7)).isoformat(),
                    'used': False,
                }
                save_reset_tokens(_v462_existing)
                print(
                    f"[AUTH] Gomes Benjamim reset link (valid 7 days): "
                    f"https://permitgrab.com/reset-password/{_v462_token}",
                    flush=True,
                )
            else:
                print("[AUTH] Gomes already has unused reset token — not generating new one", flush=True)
        elif _gomes:
            print(
                f"[AUTH] Gomes Benjamim has logged in already (last_login={_gomes.last_login_at}) — skipping reset link",
                flush=True,
            )
        else:
            print("[AUTH] Gomes user row not found — skipping reset link generation", flush=True)
    except Exception as _gomes_err:
        print(f"[AUTH] Gomes reset link generation skipped (non-fatal): {_gomes_err}", flush=True)

    # V462 (CODE_V459 task 4): cleanup test users from prior verification runs.
    # Limit to @example.com domain so real users are never touched.
    try:
        _deleted_test_users = User.query.filter(
            User.email.like('%@example.com')
        ).delete(synchronize_session=False)
        db.session.commit()
        if _deleted_test_users:
            print(f"[AUTH] V462 cleanup: removed {_deleted_test_users} @example.com test users", flush=True)
    except Exception as _cleanup_err:
        print(f"[AUTH] Test user cleanup skipped (non-fatal): {_cleanup_err}", flush=True)
        try:
            db.session.rollback()
        except Exception:
            pass

    # V473b optimization: V183's startup profile refresh REMOVED.
    #
    # The V183 rationale ("only reliable write window — once daemon threads
    # spawn, the SQLite write lock is held near-continuously") dates from
    # before V471 PR4 split the daemon out into worker.py. Now the web
    # process spawns ZERO daemon threads, so the rationale is moot — and
    # running refresh_contractor_profiles() synchronously on every web
    # restart was actively harmful: it iterated all ~2,263 active
    # prod_cities, blocked `_deferred_startup` for 30-60 min, held the
    # SQLite write lock against the worker's collection cycles, and
    # spiked memory.
    #
    # The worker's scheduled_collection() loop in worker.py already calls
    # refresh_contractor_profiles() on every 30-min cycle — that's the
    # right place. Web restart no longer has to do it.
    print(f"[{datetime.now()}] V473b: skipping V183 in-web profile refresh "
          f"(now handled exclusively by permitgrab-worker)", flush=True)

    # V471 PR4: NO daemon threads in the web process. Collection, enrichment,
    # maintenance, and email scheduling all run in the permitgrab-worker
    # service (worker.py) on its own 512MB memory budget. The web process
    # is HTTP-only.
    print(f"[{datetime.now()}] V471 PR4: web process is HTTP-only — "
          f"daemon threads (collection, enrichment, maintenance, email) "
          f"run in the permitgrab-worker service.", flush=True)


# V13.1: Jinja filter for human-readable date formatting
@app.template_filter('format_number')
def _v479_format_number(value):
    """V479: 1234567 → '1,234,567'."""
    try:
        return f"{int(value):,}"
    except (ValueError, TypeError):
        return str(value or 0)


@app.template_filter('format_compact')
def _v479_format_compact(value):
    """V479: 1234567 → '1.23M', 1234 → '1.2K'. Plain numbers, no $."""
    try:
        n = float(value)
        if n >= 1_000_000_000: return f"{n/1_000_000_000:.1f}B"
        if n >= 1_000_000:     return f"{n/1_000_000:.2f}M"
        if n >= 1_000:         return f"{n/1_000:.1f}K"
        return f"{int(n):,}"
    except (ValueError, TypeError):
        return str(value or 0)


@app.template_filter('format_currency')
def _v479_format_currency(value):
    """V479: 80800000000 → '$80.8B'."""
    try:
        n = float(value)
        if n >= 1_000_000_000: return f"${n/1_000_000_000:.1f}B"
        if n >= 1_000_000:     return f"${n/1_000_000:.1f}M"
        if n >= 1_000:         return f"${n/1_000:.0f}K"
        return f"${int(n)}"
    except (ValueError, TypeError):
        return "$0"


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










# V241 P4: global serialization lock for license-import. Each state's
# CSV is 13K-500K rows; loading several concurrently OOM'd the Render
# worker (auto-restart 2026-04-22 after 5-state parallel trigger).
# Only one import may run at a time; a second request returns 409 with
# the state of the in-flight job.
_LICENSE_IMPORT_LOCK = threading.Lock()
_LICENSE_IMPORT_IN_FLIGHT = {'state': None, 'job_id': None, 'started_at': None}






























































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


















# ===========================
# V34: ADMIN AUDIT & CLEANUP
# ===========================

































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


# ==========================================================================
# V254 Phase 1: 10 free phone reveals — the conversion lever.
# ==========================================================================
def _resolve_phone_for_profile(profile_id):
    """Look up phone (and site) for a contractor_profile by id."""
    try:
        row = permitdb.get_connection().execute(
            "SELECT phone, website, contractor_name_raw FROM contractor_profiles WHERE id = ?",
            (int(profile_id),),
        ).fetchone()
        return dict(row) if row and hasattr(row, 'keys') else (dict(zip(['phone', 'website', 'contractor_name_raw'], row)) if row else None)
    except Exception:
        return None














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














# ===========================
# DATABASE SETUP (V7)
# ===========================
from sqlalchemy.exc import IntegrityError

# V471 PR2 (CODE_V471 Part 1C): models live in models.py. Importing here so
# any blueprint / utility module that needs the SQLAlchemy instance or the
# User/SavedSearch classes can `from models import db, User, SavedSearch`
# without re-importing through server.py.
from models import db, User, SavedSearch

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

db.init_app(app)


# V459 (CODE_V456): wire flask-login. Loader queries the User SQLAlchemy
# model so flask-login's current_user / @login_required transparently
# reflect the same session state the rest of the app already uses.
# Cookie hardening matches Flask defaults + the directive's recommendations.
login_manager = LoginManager()
login_manager.login_view = 'login_page'
login_manager.init_app(app)
app.config.setdefault('SESSION_COOKIE_HTTPONLY', True)
app.config.setdefault('SESSION_COOKIE_SECURE', os.environ.get('FLASK_ENV') != 'development')
app.config.setdefault('SESSION_COOKIE_SAMESITE', 'Lax')


@login_manager.user_loader
def _v459_load_user(user_id):
    try:
        return User.query.get(int(user_id))
    except Exception:
        return None


# V471 PR2-prep: Postgres schema init was a module-level
# `with app.app_context(): db.create_all()` block. Moved into a deferred
# function so module import does no DB I/O. The first request triggers
# it via the before_request hook (lock-guarded once-runner).
_POSTGRES_SCHEMA_INITIALIZED = False
_POSTGRES_SCHEMA_LOCK = threading.Lock()

def _init_postgres_schema():
    """Run db.create_all() + ALTER TABLE migrations + V255 source_city_key
    consolidation. Idempotent and lock-guarded — safe to call from every
    before_request, but only the first call does work."""
    global _POSTGRES_SCHEMA_INITIALIZED
    with _POSTGRES_SCHEMA_LOCK:
        if _POSTGRES_SCHEMA_INITIALIZED:
            return
        _POSTGRES_SCHEMA_INITIALIZED = True

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
            # V254 Phase 1: free-tier phone-reveal credits. Default 10 matches
            # the free-trial promise on the signup / pricing page.
            ("reveal_credits", "INTEGER DEFAULT 10"),
            ("revealed_profile_ids", "TEXT DEFAULT '[]'"),
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

        # V255 P0#2: consolidate source_city_key to canonical city_slug.
    # Authoritative rule: any permit whose prod_city_id matches a row in
    # prod_cities should carry that prod_city's city_slug. The collector
    # has historically passed source_id (various formats: "chicago",
    # "chicago_il", "san_jose_ca" etc.) which fragmented per-slug
    # freshness queries. One sweeping UPDATE keyed on prod_city_id
    # normalizes every row regardless of which legacy form it landed on.
    # Idempotent — only touches rows still off-canonical.
    try:
        _conn = permitdb.get_connection()
        _cur = _conn.execute(
            "UPDATE permits SET source_city_key = pc.city_slug "
            "FROM prod_cities pc "
            "WHERE permits.prod_city_id = pc.id "
            "  AND pc.city_slug IS NOT NULL AND pc.city_slug != '' "
            "  AND (permits.source_city_key IS NULL "
            "       OR permits.source_city_key != pc.city_slug)"
        )
        _updated_total = _cur.rowcount or 0
        _conn.commit()
        if _updated_total:
            print(f"[V255 P0#2] Consolidated source_city_key on {_updated_total} permit rows")
        else:
            print("[V255 P0#2] source_city_key already canonical")
    except Exception as e:
        # SQLite < 3.33 doesn't support UPDATE...FROM. Fallback: per-row.
        try:
            _conn = permitdb.get_connection()
            _rows = _conn.execute(
                "SELECT id, city_slug FROM prod_cities "
                "WHERE city_slug IS NOT NULL AND city_slug != ''"
            ).fetchall()
            _updated_total = 0
            for _r in _rows:
                _pid = _r['id'] if hasattr(_r, 'keys') else _r[0]
                _canon = _r['city_slug'] if hasattr(_r, 'keys') else _r[1]
                _c2 = _conn.execute(
                    "UPDATE permits SET source_city_key = ? "
                    "WHERE prod_city_id = ? "
                    "  AND (source_city_key IS NULL OR source_city_key != ?)",
                    (_canon, _pid, _canon),
                )
                _updated_total += _c2.rowcount or 0
            _conn.commit()
            print(f"[V255 P0#2] Consolidated (fallback) {_updated_total} permit rows")
        except Exception as e2:
            print(f"[V255 P0#2] source_city_key consolidation skipped: {e} / fallback: {e2}")


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

# V349 (CODE_V333 Part 4 FIX 3): cache get_cities_with_data() output.
# 47 call sites across the file, called twice per city render (footer +
# nearby cities) plus on every homepage/dashboard load. Each call hits
# permitdb.get_prod_cities() which scans the prod_cities table. Caching
# this for 5 min avoids the per-request scan.
_CITIES_WITH_DATA_CACHE = {'expires_at': 0, 'value': None}
_CITIES_WITH_DATA_TTL = 300

# V366 (CODE_V363 Part F): homepage city-directory grouped by state with
# data-availability badges (P=Permits, C=Contractors, V=Violations, O=Owners).
# 5-min cache keeps the homepage fast; the daemon updates underlying counts
# once per cycle so 5 min of staleness is fine.
_CITY_DIRECTORY_CACHE = {'expires_at': 0, 'value': None}
_CITY_DIRECTORY_TTL = 300


def get_city_directory_stats():
    """Return cities grouped by state with badge counts for homepage directory.

    Returns dict[state] = list of dicts:
      {slug, name, permit_count, profile_count, phone_count,
       violation_count, owner_count}
    States are sorted alphabetically; cities within a state by permit_count desc.
    """
    import time as _t
    _now = _t.time()
    _cached = _CITY_DIRECTORY_CACHE
    if _cached['value'] is not None and _now < _cached['expires_at']:
        return _cached['value']

    grouped = {}
    try:
        conn = permitdb.get_connection()
        # V475 Bug 2 perf fix: replace 4 correlated subqueries (× ~100
        # cities = 400+ subqueries) with three pre-aggregated CTEs joined
        # once. Cuts homepage cold-cache render from 45-60s → ~1-2s.
        # The previous form was the single biggest cause of the
        # homepage 5-minute timeouts and 502s.
        rows = conn.execute("""
            WITH prof AS (
                SELECT source_city_key,
                       COUNT(*) AS profile_count,
                       SUM(CASE WHEN phone IS NOT NULL AND phone <> ''
                                THEN 1 ELSE 0 END) AS phone_count
                FROM contractor_profiles
                WHERE source_city_key IS NOT NULL
                GROUP BY source_city_key
            ),
            viol AS (
                SELECT source_city_key,
                       COUNT(*) AS violation_count
                FROM violations
                WHERE source_city_key IS NOT NULL
                GROUP BY source_city_key
            ),
            owns AS (
                SELECT city, COUNT(*) AS owner_count
                FROM property_owners
                WHERE city IS NOT NULL AND city <> ''
                GROUP BY city
            )
            SELECT pc.city_slug, pc.city, pc.state, pc.total_permits,
                   COALESCE(prof.profile_count, 0) AS profile_count,
                   COALESCE(prof.phone_count, 0) AS phone_count,
                   COALESCE(viol.violation_count, 0) AS violation_count,
                   COALESCE(owns.owner_count, 0) AS owner_count
            FROM prod_cities pc
            LEFT JOIN prof ON prof.source_city_key = pc.city_slug
            LEFT JOIN viol ON viol.source_city_key = pc.city_slug
            LEFT JOIN owns ON owns.city = pc.city
            WHERE pc.status = 'active' AND pc.total_permits > 0
            ORDER BY pc.state, pc.total_permits DESC
        """).fetchall()
        for r in rows:
            state = r['state'] or '?'
            grouped.setdefault(state, []).append({
                'slug': r['city_slug'],
                'name': r['city'],
                'permit_count': r['total_permits'] or 0,
                'profile_count': r['profile_count'] or 0,
                'phone_count': r['phone_count'] or 0,
                'violation_count': r['violation_count'] or 0,
                'owner_count': r['owner_count'] or 0,
            })
    except Exception as e:
        print(f"[V366] city directory query failed: {e}")
        # Defensive: return whatever we have rather than 500 the homepage.
        grouped = {}

    grouped = dict(sorted(grouped.items()))
    _CITY_DIRECTORY_CACHE['value'] = grouped
    _CITY_DIRECTORY_CACHE['expires_at'] = _now + _CITY_DIRECTORY_TTL
    return grouped


def get_cities_with_data():
    """V15/V34: Get cities with VERIFIED data, sorted by permit volume.

    V34: Now filters out cities with 0 actual permits in the DB.
    Only returns cities that genuinely have permit data, regardless of
    what prod_cities.total_permits says (that column can be stale).

    V15: Uses prod_cities table if available (collector redesign).
    Falls back to heuristics-based filtering if prod_cities is empty.

    V349: Internal 5-min in-memory cache. Repeat callers get the same list
    object back without a DB hit.
    """
    import time as _t
    _now = _t.time()
    _cached = _CITIES_WITH_DATA_CACHE
    if _cached['value'] is not None and _now < _cached['expires_at']:
        return _cached['value']

    # V15/V34: Try prod_cities first (collector redesign)
    # V34: total_permits is synced with actual DB counts on startup,
    # so we can trust it for filtering. No expensive JOIN needed per-request.
    try:
        if permitdb.prod_cities_table_exists():
            # min_permits=1 filters out cities with 0 real permits
            prod_cities = permitdb.get_prod_cities(status='active', min_permits=1)
            if prod_cities:
                _CITIES_WITH_DATA_CACHE['value'] = prod_cities
                _CITIES_WITH_DATA_CACHE['expires_at'] = _now + _CITIES_WITH_DATA_TTL
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
    # V349: cache the heuristic-fallback result too.
    _CITIES_WITH_DATA_CACHE['value'] = cities_with_counts
    _CITIES_WITH_DATA_CACHE['expires_at'] = _now + _CITIES_WITH_DATA_TTL
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


# V458 (CODE_V456): standardized auth gates. The auth system already
# works (session['user_email'] + werkzeug password hashing + the User
# SQLAlchemy model), but routes that gate on auth either reimplement
# the check inline or do nothing. These two decorators give us one
# consistent way to require login and to require a paying subscription.
#
# login_required(view) — redirect to /login when no user in session.
# subscription_required(view) — redirect to /pricing?expired=1 when
#   the user isn't on a Pro/Enterprise plan and isn't on an active
#   trial. Includes login_required's behavior implicitly.
from functools import wraps as _v458_wraps


def login_required(view_func):
    """Redirect anonymous visitors to /login?next=<original-url>."""
    @_v458_wraps(view_func)
    def _wrapped(*args, **kwargs):
        if not session.get('user_email'):
            from urllib.parse import quote as _q
            nxt = _q(request.full_path or '/', safe='')
            return redirect(f'/login?next={nxt}')
        return view_func(*args, **kwargs)
    return _wrapped


def subscription_required(view_func):
    """Require login AND an active Pro/Enterprise plan or unexpired trial."""
    @_v458_wraps(view_func)
    def _wrapped(*args, **kwargs):
        if not session.get('user_email'):
            from urllib.parse import quote as _q
            nxt = _q(request.full_path or '/', safe='')
            return redirect(f'/login?next={nxt}')
        u = get_current_user_object()
        if not u:
            return redirect('/login')
        # Pro/Enterprise → allowed
        plan = (getattr(u, 'plan', None) or '').lower()
        if plan in ('pro', 'professional', 'enterprise'):
            # If trial_end_date set + expired, deny
            ted = getattr(u, 'trial_end_date', None)
            if ted and ted < datetime.utcnow():
                return redirect('/pricing?expired=1')
            return view_func(*args, **kwargs)
        # Free/free_trial without active plan → check trial window
        if plan in ('free_trial', 'trial'):
            ted = getattr(u, 'trial_end_date', None)
            if ted and ted > datetime.utcnow():
                return view_func(*args, **kwargs)
        return redirect('/pricing?expired=1')
    return _wrapped


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
    """Returns one of: 'enterprise', 'pro', 'free', 'anonymous'.

    V252 F1: split 'pro' into 'pro' vs 'enterprise' so Enterprise-only
    features (property owners, webhooks, market reports) can gate
    separately. Existing 'professional' Stripe label maps to 'pro'.
    """
    if not user:
        return 'anonymous'

    plan = (user.get('plan') if hasattr(user, 'get') else getattr(user, 'plan', '')) or ''
    plan = plan.lower()

    if plan == 'enterprise':
        return 'enterprise'
    if plan in ('pro', 'professional'):
        return 'pro'

    # Stripe subscription status — safe accessor
    sub_status = (user.get('stripe_subscription_status')
                  if hasattr(user, 'get')
                  else getattr(user, 'stripe_subscription_status', None))
    if sub_status == 'active':
        return 'pro'

    return 'free'


def is_pro(user):
    """Returns True if user has Pro-or-above access. Enterprise counts as Pro."""
    return get_user_plan(user) in ('pro', 'enterprise')


def is_enterprise(user):
    """V252 F1: Enterprise-tier gate for webhook / owner-append / PDF reports."""
    return get_user_plan(user) == 'enterprise'


# V305 (CODE_V280 PR1): nav context now reads the logged-in user from
# session. Previously returned static None for `user`, so every template
# using partials/nav.html's {% if user %} branch rendered the
# Log-In/Sign-Up state — which is why city pages showed the logged-out
# nav even when the homepage (client-side /api/me JS swap) showed
# the right user. One DB lookup per request is fine; V69's "no DB
# access" guard was needed during early boot churn that's long since
# stabilized.
_NAV_CITIES_CACHE = {'expires_at': 0, 'value': []}

# V338 (CODE_V333 Part 4): per-city stats cache keyed by prod_city_id (or
# fallback to city/state pair when there is no prod_cities row). The city
# landing page used to fire 4 separate aggregate queries on the permits
# table per request — stats_row, _max_row (newest date), hide_value_column
# coverage, and violations_total — each scanning the full city slice. On
# Chicago (16K permits) every page load re-scanned a million-row table four
# times. Coalesce them into one cached payload with a 5-min TTL.
_CITY_STATS_CACHE = {}  # {cache_key: (expires_at, payload_dict)}
_CITY_STATS_TTL = 300

def _get_cached_city_stats(cache_key, prod_city_id, city_name, state, conn):
    """Cache (permit_count, total_value, high_value_count, with_value,
    newest_date, violations_total) per city for 5 minutes. cache_key is the
    city_slug for prod_city branches and 'name|state' otherwise."""
    import time as _t
    now = _t.time()
    cached = _CITY_STATS_CACHE.get(cache_key)
    if cached and now < cached[0]:
        return cached[1]

    if prod_city_id:
        row = conn.execute("""
            SELECT COUNT(*) AS permit_count,
                   COALESCE(SUM(estimated_cost), 0) AS total_value,
                   COUNT(CASE WHEN estimated_cost >= 100000 THEN 1 END) AS high_value_count,
                   SUM(CASE WHEN estimated_cost IS NOT NULL AND estimated_cost > 0
                            THEN 1 ELSE 0 END) AS with_value,
                   MAX(COALESCE(NULLIF(filing_date,''),NULLIF(issued_date,''),NULLIF(date,''))) AS newest_date
            FROM permits WHERE prod_city_id = ?
        """, (prod_city_id,)).fetchone()
    elif state:
        row = conn.execute("""
            SELECT COUNT(*) AS permit_count,
                   COALESCE(SUM(estimated_cost), 0) AS total_value,
                   COUNT(CASE WHEN estimated_cost >= 100000 THEN 1 END) AS high_value_count,
                   SUM(CASE WHEN estimated_cost IS NOT NULL AND estimated_cost > 0
                            THEN 1 ELSE 0 END) AS with_value,
                   MAX(COALESCE(NULLIF(filing_date,''),NULLIF(issued_date,''),NULLIF(date,''))) AS newest_date
            FROM permits WHERE city = ? AND state = ?
        """, (city_name, state)).fetchone()
    else:
        row = conn.execute("""
            SELECT COUNT(*) AS permit_count,
                   COALESCE(SUM(estimated_cost), 0) AS total_value,
                   COUNT(CASE WHEN estimated_cost >= 100000 THEN 1 END) AS high_value_count,
                   SUM(CASE WHEN estimated_cost IS NOT NULL AND estimated_cost > 0
                            THEN 1 ELSE 0 END) AS with_value,
                   MAX(COALESCE(NULLIF(filing_date,''),NULLIF(issued_date,''),NULLIF(date,''))) AS newest_date
            FROM permits WHERE city = ?
        """, (city_name,)).fetchone()

    viol_total = 0
    try:
        if state:
            viol_row = conn.execute("""
                SELECT COUNT(*) AS n FROM violations
                WHERE UPPER(city) = UPPER(?) AND UPPER(state) = UPPER(?)
            """, (city_name, state)).fetchone()
            viol_total = viol_row['n'] if viol_row else 0
    except Exception:
        viol_total = 0

    payload = {
        'permit_count': (row['permit_count'] if row else 0) or 0,
        'total_value': (row['total_value'] if row else 0) or 0,
        'high_value_count': (row['high_value_count'] if row else 0) or 0,
        'with_value': (row['with_value'] if row else 0) or 0,
        'newest_date': row['newest_date'] if row else None,
        'violations_total': viol_total,
    }
    _CITY_STATS_CACHE[cache_key] = (now + _CITY_STATS_TTL, payload)
    return payload


def _get_nav_cities():
    """V327 (CODE_V320 Bug 1): cache the ad-ready dropdown list. The old
    context processor returned [] which forced every template into the
    9-city hardcoded fallback in partials/nav.html and dropped San
    Antonio (3,828 phones, our #1 city). Querying prod_cities + profile
    counts on every render would tank Render's single-worker latency
    so we cache for 5 minutes."""
    import time as _t
    now = _t.time()
    if now < _NAV_CITIES_CACHE['expires_at']:
        return _NAV_CITIES_CACHE['value']
    try:
        conn = permitdb.get_connection()
        # V480 P1-3: rank by phones (the ad-ready signal) instead of raw
        # profile count. The old query showed Cook County / Collin County /
        # Henderson high in the dropdown because the assessor backfills
        # inflated profile counts without contributing any phone leads.
        # Gate on phones > 30 to keep owner-only cities out of the nav.
        rows = conn.execute("""
            SELECT pc.city_slug AS slug, pc.city AS name,
                   COALESCE(cp.profiles, 0) AS profile_count,
                   COALESCE(cp.phones, 0)   AS phone_count
            FROM prod_cities pc
            LEFT JOIN (
                SELECT source_city_key,
                       COUNT(*) AS profiles,
                       SUM(CASE WHEN phone IS NOT NULL AND phone <> ''
                                THEN 1 ELSE 0 END) AS phones
                FROM contractor_profiles
                GROUP BY source_city_key
            ) cp ON cp.source_city_key = pc.city_slug
            WHERE pc.status = 'active'
              AND COALESCE(cp.profiles, 0) > 100
              AND COALESCE(cp.phones, 0)   > 30
            ORDER BY phone_count DESC, profile_count DESC
            LIMIT 10
        """).fetchall()
        cities = [{'slug': r['slug'], 'name': r['name']} for r in rows or []]
    except Exception:
        cities = []
    _NAV_CITIES_CACHE['value'] = cities
    _NAV_CITIES_CACHE['expires_at'] = now + 300
    return cities


@app.context_processor
def inject_nav_context():
    """Populate user/is_pro/is_enterprise/nav_cities for every template
    render. Falls back to anonymous defaults on any lookup failure so this
    can never break a request. V252 F1 added is_enterprise. V327 wired
    nav_cities to prod_cities (was an empty list, forcing fallback)."""
    user = None
    plan = 'anonymous'
    pro = False
    enterprise = False
    try:
        email = session.get('user_email')
        if email:
            user = find_user_by_email(email)
            if user:
                plan = get_user_plan(user)
                pro = plan in ('pro', 'professional', 'enterprise')
                enterprise = plan == 'enterprise'
    except Exception:
        # Never break a request on the nav lookup.
        user = None
    # V335 (CODE_V321 Bug H): default footer_cities to the same cached list
    # the nav uses. Per-route render_template(..., footer_cities=...) calls
    # still override this — but routes that don't pass footer_cities (pricing,
    # about, error pages) used to fall through to a 7-city hardcoded list in
    # partials/footer.html that diverged from the nav dropdown.
    nav_cities = _get_nav_cities()
    return {
        'user': user,
        'user_plan': plan,
        'is_pro': pro,
        'is_enterprise': enterprise,
        'nav_cities': nav_cities,
        # V338: footer was overridden per-route to get_cities_with_data() (a
        # full prod_cities scan). The override callers can keep doing that;
        # the default is the cached nav list.
        'footer_cities': nav_cities,
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
def add_cache_headers(response):
    """V428 (CODE_V427b Phase 12): HTTP caching headers.

    City pages (/permits/*) take 1-1.5s of server-side rendering on every
    request because Cloudflare marks them DYNAMIC without a Cache-Control
    header. Adding public caching directives lets Cloudflare serve most
    requests from edge cache, dropping TTFB to <50ms for cache hits and
    eliminating the deploy-time 502 windows for returning visitors.

    Logged-in users get `private` caching only — never `public` — so
    session-tied redirects (homepage → /onboarding, /pricing → upgrade
    flows) can never be cached and served to a different visitor.
    """
    try:
        # Skip non-success responses (errors, redirects, auth-required)
        if response.status_code >= 400 or response.status_code in (301, 302):
            return response
        # If Cache-Control was set explicitly upstream, leave it alone
        if response.headers.get('Cache-Control'):
            return response

        path = request.path or ''
        logged_in = 'user_email' in session

        # Logged-in users: never set public cache (would leak personalized
        # responses if a CDN ignores Vary: Cookie). private + max-age 0
        # forces revalidation but allows browser-history navigation.
        if logged_in:
            response.headers['Cache-Control'] = 'private, max-age=0, must-revalidate'
            return response

        # Anonymous users: route-specific public caching.
        if path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store'
        elif path.startswith('/permits/'):
            response.headers['Cache-Control'] = 'public, max-age=300, s-maxage=3600'
        elif path == '/sitemap.xml' or path.startswith('/sitemap-'):
            response.headers['Cache-Control'] = 'public, max-age=3600'
        elif path == '/cities':
            response.headers['Cache-Control'] = 'public, max-age=1800, s-maxage=86400'
        elif path.startswith('/blog'):
            response.headers['Cache-Control'] = 'public, max-age=3600, s-maxage=86400'
        elif path in ('/about', '/contact', '/privacy', '/terms'):
            response.headers['Cache-Control'] = 'public, max-age=3600, s-maxage=86400'
        elif path.startswith('/report/'):
            response.headers['Cache-Control'] = 'public, max-age=900, s-maxage=3600'
        elif path == '/contractors':
            response.headers['Cache-Control'] = 'public, max-age=300, s-maxage=3600'
        elif path == '/':
            # Homepage redirects logged-in users; the logged_in-skip above
            # already covers that. For anon, short cache OK.
            response.headers['Cache-Control'] = 'public, max-age=300, s-maxage=1800'
        # Don't cache /pricing, /signup, /login — they may redirect or
        # show form errors. Falling through means default browser handling.
    except Exception:
        pass
    return response


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


# V251 F15: saved contractors (bookmark a contractor with notes). File-backed
# JSON matching the saved_leads pattern so we don't need a schema migration.
SAVED_CONTRACTORS_FILE = os.path.join(DATA_DIR, 'saved_contractors.json')


def _load_saved_contractors():
    if os.path.exists(SAVED_CONTRACTORS_FILE):
        try:
            with open(SAVED_CONTRACTORS_FILE) as f:
                return json.load(f)
        except Exception:
            return []
    return []


def _save_saved_contractors(items):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(SAVED_CONTRACTORS_FILE, 'w') as f:
        json.dump(items, f, indent=2)


# ===========================
# API ROUTES
# ===========================



# V9 Fix 9: /dashboard redirects to homepage (V13.7: redirect to login if not authenticated)






# V10 Fix 5: /alerts redirects to account page





















# ===========================
# SAVED LEADS / CRM API
# ===========================









# ==========================================================================
# V251 F15: /api/saved-contractors — Pro bookmarks + notes on contractors
# ==========================================================================






def build_weekly_market_report(city_slug):
    """V251 F16: generate a weekly market report payload for a city.

    Returns {'subject': str, 'html': str, 'text': str, 'stats': {...}}
    so the existing digest daemon can hand it to SendGrid.
    Returns None if the city has no data or isn't active.
    """
    conn = permitdb.get_connection()
    pc = conn.execute(
        "SELECT id, city, state FROM prod_cities WHERE city_slug=? AND status='active'",
        (city_slug,),
    ).fetchone()
    if not pc:
        return None
    pid = pc['id']
    stats = conn.execute("""
        SELECT
          (SELECT COUNT(*) FROM permits WHERE prod_city_id=? AND COALESCE(filing_date,issued_date,date) >= date('now','-7 days')) as permits_this_week,
          (SELECT COUNT(*) FROM permits WHERE prod_city_id=? AND COALESCE(filing_date,issued_date,date) >= date('now','-14 days') AND COALESCE(filing_date,issued_date,date) < date('now','-7 days')) as permits_prev_week,
          (SELECT COUNT(*) FROM contractor_profiles WHERE source_city_key=? AND first_permit_date >= date('now','-7 days')) as new_contractors,
          (SELECT trade_category FROM permits WHERE prod_city_id=? AND trade_category IS NOT NULL AND trade_category != '' AND COALESCE(filing_date,issued_date,date) >= date('now','-7 days') GROUP BY trade_category ORDER BY COUNT(*) DESC LIMIT 1) as top_trade,
          (SELECT zip FROM permits WHERE prod_city_id=? AND zip IS NOT NULL AND zip != '' AND COALESCE(filing_date,issued_date,date) >= date('now','-7 days') GROUP BY zip ORDER BY COUNT(*) DESC LIMIT 1) as top_zip
    """, (pid, pid, city_slug, pid, pid)).fetchone()
    if not stats or (stats['permits_this_week'] or 0) == 0:
        return None
    tw = stats['permits_this_week'] or 0
    pw = stats['permits_prev_week'] or 0
    pct = ((tw - pw) / pw * 100) if pw else None
    trend = f"up {pct:.0f}% from last week" if pct and pct > 0 else (f"down {abs(pct):.0f}% from last week" if pct and pct < 0 else "flat vs. last week")
    subject = f"{pc['city']} permit activity this week — {tw} filings"
    lines = [
        f"{pc['city']}, {pc['state']} permit activity",
        f"{tw} permits this week ({trend}).",
    ]
    if stats['top_trade']: lines.append(f"Top trade: {stats['top_trade']}.")
    if stats['top_zip']:   lines.append(f"Fastest-growing zip: {stats['top_zip']}.")
    if stats['new_contractors']: lines.append(f"{stats['new_contractors']} new contractors pulled their first permit this week.")
    lines.append(f"Full report: {SITE_URL}/permits/{city_slug}")
    text = '\n'.join(lines)
    html = (
        f'<h2 style="font-family:sans-serif;">{pc["city"]} permit activity</h2>'
        f'<p><strong>{tw}</strong> permits this week ({trend}).</p>'
        + (f'<p>Top trade: <strong>{stats["top_trade"]}</strong></p>' if stats['top_trade'] else '')
        + (f'<p>Fastest-growing zip: <strong>{stats["top_zip"]}</strong></p>' if stats['top_zip'] else '')
        + (f'<p>{stats["new_contractors"]} new contractors this week.</p>' if stats['new_contractors'] else '')
        + f'<p><a href="{SITE_URL}/permits/{city_slug}">View the full {pc["city"]} dashboard →</a></p>'
    )
    return {'subject': subject, 'html': html, 'text': text, 'stats': dict(stats)}


def send_weekly_digests(dry_run=False):
    """V253 P2 #9: send this week's market report to every Pro subscriber
    for each city they're watching via digest_cities.

    Uses build_weekly_market_report (V251 F16 generator) + the existing
    email_alerts.send_email SMTP wrapper. Returns per-user send counts
    so the admin cron can log delivery.

    dry_run=True: compose messages but don't actually send. Useful for
    previewing the whole batch before flipping it on.
    """
    try:
        from email_alerts import send_email
    except ImportError as e:
        return {'error': f'email_alerts not importable: {e}'}

    conn = permitdb.get_connection()
    sent = []
    skipped = []

    # Pull every user who has digest_active + at least one digest city.
    # Match digest_cities values (city NAMES like "Chicago") back to
    # city_slugs via prod_cities.
    try:
        users = db.session.execute(
            db.text("SELECT email, digest_cities, digest_active, plan "
                    "FROM users WHERE digest_active = TRUE "
                    "AND digest_cities IS NOT NULL AND digest_cities != '[]'")
        ).fetchall()
    except Exception as e:
        return {'error': f'user query failed: {e}'}

    for u in users:
        email, digest_cities_json, digest_active, plan = u
        if (plan or '').lower() not in ('pro', 'professional', 'enterprise'):
            skipped.append({'email': email, 'reason': 'not_pro'})
            continue
        try:
            city_names = json.loads(digest_cities_json or '[]')
        except Exception:
            city_names = []
        if not city_names:
            continue
        # Resolve city names → slugs (best-effort)
        placeholders = ','.join(['?'] * len(city_names))
        slug_rows = conn.execute(
            f"SELECT city_slug FROM prod_cities WHERE city IN ({placeholders}) AND status='active'",
            city_names,
        ).fetchall()
        slugs = [r[0] for r in slug_rows]
        for slug in slugs:
            report = build_weekly_market_report(slug)
            if not report:
                skipped.append({'email': email, 'slug': slug, 'reason': 'no_activity'})
                continue
            if dry_run:
                sent.append({'email': email, 'slug': slug, 'subject': report['subject'], 'dry_run': True})
                continue
            try:
                send_email(email, report['subject'], report['html'], report['text'])
                sent.append({'email': email, 'slug': slug, 'subject': report['subject']})
            except Exception as e:
                skipped.append({'email': email, 'slug': slug, 'reason': f'smtp_error: {e}'})
    return {'sent': len(sent), 'skipped': len(skipped), 'details': {'sent': sent, 'skipped': skipped}}








# V252 F7: vertical landing pages — /<trade>/<city> SEO route that targets
# "<trade> leads <city>" keywords. Whitelisted to our supported trades so
# this doesn't collide with /pricing, /blog, /account, etc.
V252_TRADE_URL_MAP = {
    'solar': 'solar',
    'roofing': 'roofing',
    'hvac': 'hvac',
    'electrical': 'electrical',
    'plumbing': 'plumbing',
    'demolition': 'demolition',
    'general-construction': 'general-construction',
    'pool': 'pool',
    'fence': 'fence',
    'interior-renovation': 'interior-renovation',
}


# V252 F5: Zapier webhooks — Enterprise tier. File-backed JSON so no
# schema migration; fine for <100 webhook definitions across subscribers.
WEBHOOKS_FILE = os.path.join(DATA_DIR, 'webhooks.json')


def _load_webhooks():
    if os.path.exists(WEBHOOKS_FILE):
        try:
            with open(WEBHOOKS_FILE) as f:
                return json.load(f)
        except Exception:
            return []
    return []


def _save_webhooks(items):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(WEBHOOKS_FILE, 'w') as f:
        json.dump(items, f, indent=2)


def scan_and_fire_webhooks(window_minutes=60):
    """V252 F5 hookup: query recently-collected permits and dispatch them
    through every matching active webhook. Designed to be called on a
    short external cron (every 15min) rather than inline with the
    collector daemon, so a slow webhook URL can't block permit ingestion.
    Returns dict with scanned/fired counts for the admin endpoint to
    report.
    """
    try:
        conn = permitdb.get_connection()
        rows = conn.execute(f"""
            SELECT source_city_key, permit_number, permit_type, address, zip,
                   description, estimated_cost, trade_category,
                   contractor_name, contact_name,
                   COALESCE(filing_date, issued_date, date) as permit_date,
                   collected_at
            FROM permits
            WHERE collected_at >= datetime('now', '-{int(window_minutes)} minutes')
              AND source_city_key IS NOT NULL
            ORDER BY collected_at DESC
            LIMIT 1000
        """).fetchall()
        new_permits = [dict(r) for r in rows]
        fired = fire_webhooks_for_new_permits(new_permits)
        return {'scanned': len(new_permits), 'fired': fired, 'window_minutes': window_minutes}
    except Exception as e:
        print(f"[V252 F5] scan_and_fire_webhooks error: {e}", flush=True)
        return {'error': str(e)}




def fire_webhooks_for_new_permits(new_permits):
    """V252 F5: dispatch new-permit batch to all matching active webhooks.

    Called by the collector after a successful insert batch (wiring is
    a follow-up — ship the mechanism here). Filters per-webhook on
    city_slug, optional trade_filter, optional min_value.
    """
    if not new_permits:
        return 0
    import requests as _req
    items = _load_webhooks()
    fired = 0
    for wh in items:
        if not wh.get('active', True):
            continue
        city = wh.get('city_slug')
        trade = (wh.get('trade_filter') or '').strip() or None
        min_v = int(wh.get('min_value') or 0)
        matching = [
            p for p in new_permits
            if p.get('source_city_key') == city
            and (not trade or (p.get('trade_category') == trade))
            and (int(p.get('estimated_cost') or 0) >= min_v)
        ]
        if not matching:
            continue
        try:
            _req.post(wh['url'], json={'permits': matching, 'webhook_id': wh.get('id')}, timeout=10)
            fired += 1
        except Exception as e:
            print(f"[V252 F5] webhook {wh.get('id')} POST failed: {e}", flush=True)
    return fired


def lookup_property_owner(address):
    """V252 F1.5: property-owner lookup for Enterprise tier.

    Returns {owner_name, owner_mailing_address, parcel_id, source} or None.
    Data must be populated by a per-county import ETL into property_owners
    table — this function is the read side. Returns None silently while
    the table is empty so callers don't crash pre-import.
    """
    if not address:
        return None
    try:
        row = permitdb.get_connection().execute(
            "SELECT owner_name, owner_mailing_address, parcel_id, source "
            "FROM property_owners WHERE UPPER(address) = UPPER(?) LIMIT 1",
            (address,)
        ).fetchone()
    except Exception:
        return None
    if not row:
        return None
    return dict(row) if hasattr(row, 'keys') else {
        'owner_name': row[0], 'owner_mailing_address': row[1],
        'parcel_id': row[2], 'source': row[3],
    }




def compute_competitor_alert_batches(window_days=1):
    """V252 F2: for every user watching competitors, return the new permits
    those competitors pulled in the last `window_days` days.

    Returns [ {user_email, competitor, permits: [...] }, ... ] so the
    existing digest sender can hand the payload to SendGrid.
    """
    conn = permitdb.get_connection()
    batches = []
    try:
        users = db.session.execute(
            db.text("SELECT email, watched_competitors FROM users "
                    "WHERE watched_competitors IS NOT NULL "
                    "AND watched_competitors != '[]' "
                    "AND watched_competitors != ''")
        ).fetchall()
    except Exception as e:
        print(f"[V252 F2] user fetch failed: {e}", flush=True)
        return batches
    for u in users:
        email = u[0]
        try:
            watched = json.loads(u[1] or '[]')
        except Exception:
            watched = []
        for name in watched:
            rows = conn.execute(f"""
                SELECT source_city_key, permit_number, permit_type, address,
                       contractor_name, contact_name, estimated_cost,
                       COALESCE(filing_date, issued_date, date) as permit_date
                FROM permits
                WHERE (UPPER(contractor_name) LIKE ? OR UPPER(contact_name) LIKE ?)
                  AND COALESCE(filing_date, issued_date, date) >= date('now', '-{int(window_days)} days')
                ORDER BY permit_date DESC
                LIMIT 20
            """, (f"%{name.upper()}%", f"%{name.upper()}%")).fetchall()
            if rows:
                batches.append({
                    'user_email': email,
                    'competitor': name,
                    'permits': [dict(r) for r in rows],
                })
    return batches




















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



## Old violations API (V82/V156) removed in V163 — replaced by V162 database-backed version


# ===========================
# CONTRACTOR INTELLIGENCE API
# ===========================







# ===========================
# V79: BLOG SYSTEM
# ===========================

def _generate_market_reports():
    """V318 (CODE_V280b Bug 20): generate data-driven Market Report posts at
    request time using real DB numbers for each ad-ready city. Replaces the
    "all 67 posts identical permit-cost guides dated 2026-04-06" problem.
    """
    posts = []
    try:
        conn = permitdb.get_connection()
        rows = conn.execute("""
            SELECT cp.source_city_key AS slug,
                   MIN(cp.city) AS city,
                   MIN(cp.state) AS state,
                   COUNT(*) AS profiles,
                   SUM(CASE WHEN cp.phone IS NOT NULL AND cp.phone <> ''
                            THEN 1 ELSE 0 END) AS phones
            FROM contractor_profiles cp
            GROUP BY cp.source_city_key
            HAVING profiles >= 100 AND phones >= 50
            ORDER BY phones DESC
            LIMIT 8
        """).fetchall()

        today = datetime.now().strftime('%Y-%m-%d')
        for r in rows:
            slug = r['slug']; city = r['city']; state = r['state']
            try:
                permits_90d = conn.execute("""
                    SELECT COUNT(*) AS n FROM permits
                    WHERE source_city_key = ?
                      AND COALESCE(filing_date, issued_date, date)
                          >= date('now', '-90 days')
                """, (slug,)).fetchone()['n']
                top_trade_row = conn.execute("""
                    SELECT primary_trade, COUNT(*) AS n
                    FROM contractor_profiles
                    WHERE source_city_key = ?
                      AND primary_trade IS NOT NULL
                      AND primary_trade <> ''
                    GROUP BY primary_trade
                    ORDER BY n DESC LIMIT 1
                """, (slug,)).fetchone()
                top_trade = top_trade_row['primary_trade'] if top_trade_row else 'general construction'
                vios = conn.execute("""
                    SELECT COUNT(*) AS n FROM violations
                    WHERE UPPER(city) = UPPER(?) AND UPPER(state) = UPPER(?)
                """, (city, state)).fetchone()['n']
            except Exception:
                permits_90d = r['profiles']; top_trade = 'general construction'; vios = 0

            post_slug = f'market-report-{slug}'
            content = (
                f"<p>Latest construction-permit activity from <strong>{city}, {state}</strong> across "
                f"the PermitGrab data network. Data updated daily from official sources.</p>"
                f"<h2>By the numbers</h2>"
                f"<ul>"
                f"<li><strong>{permits_90d:,}</strong> permits filed in the last 90 days</li>"
                f"<li><strong>{r['profiles']:,}</strong> active contractors tracked in our DB</li>"
                f"<li><strong>{r['phones']:,}</strong> with verified phone numbers</li>"
                f"<li><strong>{vios:,}</strong> code violations on file</li>"
                f"<li>Most active trade: <strong>{top_trade}</strong></li>"
                f"</ul>"
                f"<h2>Why this matters for contractors</h2>"
                f"<p>Each permit in the database is a project that's actively happening. Each contractor "
                f"with a phone number is reachable. Each violation is a property owner who needs work done "
                f"now. <a href=\"/permits/{slug}\">Browse {city} permits</a> or "
                f"<a href=\"/contractors/{slug}\">{city} contractors</a> to drill in.</p>"
            )
            posts.append({
                'slug': post_slug,
                'title': f"{city} Permit Activity Report — {datetime.now().strftime('%B %Y')}",
                'meta_description': (
                    f"Live data report: {permits_90d:,} permits + {r['profiles']:,} contractors + "
                    f"{vios:,} code violations in {city}, {state}. Updated every page load."
                ),
                'date': today,
                'category': 'market-reports',
                'city_link': f'/permits/{slug}',
                'city_name': city,
                'excerpt': (
                    f"{permits_90d:,} permits filed in {city} over the last 90 days, "
                    f"{r['phones']:,} contractors with phone numbers, {vios:,} active code violations. "
                    f"Top trade: {top_trade}."
                ),
                'content': content,
            })
    except Exception as _e:
        print(f"[V318 market reports] generation failed: {_e}", flush=True)
    return posts




def _v469_render_faq_blog_post(slug):
    """V469 (CODE_V467 FAQ blog posts): render one of the 6 buyer-segment
    FAQ blog posts with global DB totals (cities/permits/profiles/phones/
    violations/owners) injected. Different from the per-city SEO posts —
    no city_slug, stats are aggregate.
    """
    try:
        from faq_blog_posts import FAQ_BLOG_POSTS
    except Exception as _imp_err:
        print(f"[V469] FAQ blog import failed: {_imp_err}", flush=True)
        return None

    post = FAQ_BLOG_POSTS.get(slug)
    if not post:
        return None

    conn = permitdb.get_connection()

    def _scalar(sql, params=()):
        try:
            row = conn.execute(sql, params).fetchone()
            v = row[0] if row else 0
            return v if v is not None else 0
        except Exception:
            return 0

    cities = _scalar(
        "SELECT COUNT(DISTINCT source_city_key) FROM permits WHERE date > date('now','-180 days')")
    permits = _scalar(
        "SELECT COUNT(*) FROM permits WHERE date > date('now','-180 days')")
    profiles = _scalar("SELECT COUNT(*) FROM contractor_profiles")
    phones = _scalar(
        "SELECT COUNT(*) FROM contractor_profiles WHERE phone IS NOT NULL AND phone != ''")
    violations = _scalar("SELECT COUNT(*) FROM violations")
    violation_cities = _scalar("SELECT COUNT(DISTINCT prod_city_id) FROM violations")
    owners = _scalar("SELECT COUNT(*) FROM property_owners")
    owner_cities = _scalar("SELECT COUNT(DISTINCT city) FROM property_owners")

    stats = {
        'cities': cities,
        'permits': permits,
        'profiles': profiles,
        'phones': phones,
        'violations': violations,
        'violation_cities': violation_cities,
        'owners': owners,
        'owner_cities': owner_cities,
        # Defaults for the shared template's grid + city-specific fields
        'permits_90d': permits,
        'top_permit_types': [],
        'top_contractors': [],
        'trade_breakdown': [],
        'newest_permit': 'today',
    }

    try:
        body_html = render_template(f'blog/faq/{slug}.html', stats=stats, post=post)
        rendered_faqs = []
        from jinja2 import Template
        for f in post.get('faqs', []):
            rendered_faqs.append({
                'q': Template(f['q']).render(stats=stats, post=post),
                'a': Template(f['a']).render(stats=stats, post=post),
            })
    except Exception as _body_err:
        print(f"[V469] body render failed for {slug}: {_body_err}", flush=True)
        body_html = '<p>Content temporarily unavailable.</p>'
        rendered_faqs = []

    # Pass post object with rendered_faqs so the template can use them
    post_view = dict(post)
    post_view['faqs'] = rendered_faqs

    try:
        from faq_blog_posts import FAQ_BLOG_POSTS as _ALL_FAQ
        seo_related = [(s, p) for s, p in _ALL_FAQ.items() if s != slug]
    except Exception:
        seo_related = []

    footer_cities = get_cities_with_data()
    return render_template(
        'blog_post_seo.html',
        post=post_view, stats=stats, body_html=body_html, slug=slug,
        city_slug=None, seo_related=seo_related,
        footer_cities=footer_cities,
    )


def _v467_render_seo_blog_post(slug):
    """V467 (CODE_V467 SEO blog posts): render one of the 5 ad-ready-city
    SEO blog posts with live DB stats injected via templates/blog/seo/&lt;slug&gt;.html.
    Returns a Flask response or None if the slug isn't an SEO post.
    """
    try:
        from seo_blog_posts import SEO_BLOG_POSTS
    except Exception as _imp_err:
        print(f"[V467] SEO blog import failed: {_imp_err}", flush=True)
        return None

    post = SEO_BLOG_POSTS.get(slug)
    if not post:
        return None

    city_slug = post['city_slug']
    conn = permitdb.get_connection()

    def _scalar(sql, params, key, default=0):
        try:
            row = conn.execute(sql, params).fetchone()
            return row[key] if row and row[key] is not None else default
        except Exception:
            return default

    permits_90d = _scalar(
        "SELECT COUNT(*) as cnt FROM permits WHERE source_city_key = ? AND date > date('now', '-90 days')",
        (city_slug,), 'cnt')
    profiles = _scalar(
        "SELECT COUNT(*) as cnt FROM contractor_profiles WHERE source_city_key = ?",
        (city_slug,), 'cnt')
    phones = _scalar(
        "SELECT COUNT(*) as cnt FROM contractor_profiles WHERE source_city_key = ? AND phone IS NOT NULL AND phone != ''",
        (city_slug,), 'cnt')

    try:
        top_permit_types = [dict(r) for r in conn.execute("""
            SELECT permit_type, COUNT(*) as cnt FROM permits
             WHERE source_city_key = ? AND date > date('now', '-90 days')
               AND permit_type IS NOT NULL AND permit_type != ''
             GROUP BY permit_type ORDER BY cnt DESC LIMIT 10
        """, (city_slug,)).fetchall()]
    except Exception:
        top_permit_types = []

    try:
        top_contractors = [dict(r) for r in conn.execute("""
            SELECT contractor_name as business_name, COUNT(*) as permits FROM permits
             WHERE source_city_key = ? AND contractor_name IS NOT NULL AND contractor_name != ''
               AND date > date('now', '-180 days')
             GROUP BY contractor_name ORDER BY permits DESC LIMIT 15
        """, (city_slug,)).fetchall()]
    except Exception:
        top_contractors = []

    try:
        trade_breakdown = [dict(r) for r in conn.execute("""
            SELECT trade_category, COUNT(*) as cnt FROM contractor_profiles
             WHERE source_city_key = ? AND trade_category IS NOT NULL AND trade_category != ''
             GROUP BY trade_category ORDER BY cnt DESC LIMIT 10
        """, (city_slug,)).fetchall()]
    except Exception:
        trade_breakdown = []

    pc_row = None
    try:
        pc_row = conn.execute(
            "SELECT id, city, state FROM prod_cities WHERE city_slug = ? LIMIT 1",
            (city_slug,)
        ).fetchone()
    except Exception:
        pass
    violations_count = 0
    if pc_row:
        violations_count = _scalar(
            "SELECT COUNT(*) as cnt FROM violations WHERE prod_city_id = ?",
            (pc_row['id'],), 'cnt')

    city_name_variants = {
        'chicago-il': ['Chicago'],
        'miami-dade-county': ['Miami-Dade'],
        'san-antonio-tx': ['San Antonio'],
        'phoenix-az': ['Phoenix'],
        'new-york-city': ['New York City', 'New York'],
    }
    variants = city_name_variants.get(city_slug, [pc_row['city']] if pc_row else [])
    owner_count = 0
    for v in variants:
        if v:
            owner_count += _scalar(
                "SELECT COUNT(*) as cnt FROM property_owners WHERE city = ?",
                (v,), 'cnt')

    newest = _scalar(
        "SELECT MAX(date) as newest FROM permits WHERE source_city_key = ?",
        (city_slug,), 'newest', default='') or 'recently'

    stats = {
        'permits_90d': permits_90d,
        'profiles': profiles,
        'phones': phones,
        'violations': violations_count,
        'owners': owner_count,
        'top_permit_types': top_permit_types,
        'top_contractors': top_contractors,
        'trade_breakdown': trade_breakdown,
        'newest_permit': newest,
    }

    # Render body with stats injected, then safe-pass to the shell template.
    try:
        body_html = render_template(f'blog/seo/{slug}.html', stats=stats, post=post, city_slug=city_slug)
    except Exception as _body_err:
        print(f"[V467] body render failed for {slug}: {_body_err}", flush=True)
        body_html = '<p>Content temporarily unavailable.</p>'

    seo_related = [(s, p) for s, p in SEO_BLOG_POSTS.items() if s != slug]
    footer_cities = get_cities_with_data()
    return render_template(
        'blog_post_seo.html',
        post=post, stats=stats, body_html=body_html, slug=slug,
        city_slug=city_slug, seo_related=seo_related,
        footer_cities=footer_cities,
    )
















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
































# ===========================
# TREND ANALYTICS API
# ===========================









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

# V462 (CODE_V459 task 1): log Stripe config state at startup so deploys
# can be debugged without leaking secret values. We only print presence,
# never the secret itself.
print(
    f"[Stripe] config @ startup: SECRET_KEY={'set' if STRIPE_SECRET_KEY else 'NOT SET'} "
    f"PRICE_ID={'set' if STRIPE_PRICE_ID else 'NOT SET'} "
    f"WEBHOOK_SECRET={'set' if STRIPE_WEBHOOK_SECRET else 'NOT SET'}",
    flush=True,
)









# ===========================
# USER AUTHENTICATION
# ===========================







# V459 (CODE_V456): GET /logout already exists at server.py:17606 below;
# augmenting that route is enough — flask-login's logout_user is invoked
# alongside the existing session.clear() there.




# ===========================
# UNSUBSCRIBE
# ===========================



# ===========================
# ADMIN PAGE
# ===========================



# Handle admin logout
@app.before_request
def check_admin_logout():
    if request.path in ('/admin', '/admin/legacy') and request.args.get('logout'):
        session.pop('admin_authenticated', None)










# ===========================
# ADMIN ANALYTICS DASHBOARD
# ===========================

# Admin emails list - add emails that should have admin access
ADMIN_EMAILS = os.environ.get('ADMIN_EMAILS', 'wcrainshaw@gmail.com').lower().split(',')



# ===========================
# MY LEADS CRM PAGE
# ===========================



# ===========================
# SAVED SEARCHES PAGE
# ===========================



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


# V309 (CODE_V280b Bug 23): 301 redirects for common slug variations that
# users (and Google) try before landing on our canonical slugs. Without
# these, /permits/miami-dade and /permits/nyc 404 — confusing for ad
# clicks and bad for SEO. Maps colloquial/short forms to the real
# prod_cities.city_slug values.
_PERMIT_SLUG_ALIASES = {
    'miami-dade': 'miami-dade-county',
    'miami': 'miami-dade-county',
    'nyc': 'new-york-city',
    'new-york': 'new-york-city',
    'chicago': 'chicago-il',
    'phoenix': 'phoenix-az',
    'san-antonio': 'san-antonio-tx',
    'austin': 'austin-tx',
    'raleigh-nc': 'raleigh',  # DB has bare 'raleigh' slug
    'buffalo': 'buffalo-ny',
    'nashville': 'nashville-tn',
    'orlando': 'orlando-fl',
    'tampa': 'tampa-fl',
    'cleveland': 'cleveland-oh',
    'baton-rouge': 'baton-rouge-la',
    'st-louis': 'st-louis-mo',
    'kansas-city': 'kansas-city-mo',
    'las-vegas-nevada': 'las-vegas',
    'vegas': 'las-vegas',
    # V480 P0-2: nav/footer/sitemap have linked /permits/san-jose-ca for a
    # while but the prod_cities slug is the bare `san-jose`. The mismatch
    # rendered an empty "Data Updating" page. 301 to the canonical so
    # external links and any cached Google results redirect cleanly.
    'san-jose-ca': 'san-jose',
}




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
                'first_permit_date': r['first_permit_date'],
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
    except Exception as e:
        # V359 HOTFIX: defensive net so a contractor_profiles schema
        # mismatch (or missing-table-on-fresh-CI scenario) doesn't 500
        # the entire city page render.
        print(f"[V359] _get_top_contractors_for_city({city_slug}) failed: {e}", flush=True)
        return []
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


def _get_property_owners(city_name, state, limit=10):
    """V284 (CODE_V285 Gate 4): fetch property_owner rows for the
    city-page "Who's Building in {City}?" section.

    property_owners uses (city, state, source) columns. permit_record
    rows carry title-case city ('Chicago'), assessor rows carry
    uppercase ('PHOENIX'). Match case-insensitively. Returns [] on
    any error so the template's {% if property_owners %} guard
    short-circuits — no 500s.
    """
    if not city_name:
        return []
    try:
        conn = permitdb.get_connection()
        rows = conn.execute(
            "SELECT address, owner_name, owner_mailing_address, "
            "parcel_id, source, last_updated "
            "FROM property_owners "
            "WHERE UPPER(city) = UPPER(?) "
            "  AND (state IS NULL OR UPPER(state) = UPPER(?)) "
            "  AND owner_name IS NOT NULL "
            "  AND LENGTH(TRIM(owner_name)) > 2 "
            "ORDER BY last_updated DESC "
            "LIMIT ?",
            (city_name, state or '', limit)
        ).fetchall()
        out = []
        for r in rows:
            if isinstance(r, dict):
                out.append(r)
            else:
                out.append({
                    'address': r[0],
                    'owner_name': r[1],
                    'owner_mailing_address': r[2],
                    'parcel_id': r[3],
                    'source': r[4],
                    'last_updated': r[5],
                })
        return out
    except Exception as e:
        print(f"[V284] _get_property_owners({city_name},{state}) failed: {e}")
        return []


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

    # V475 Bug 5 — EARLY exit for empty cities, BEFORE the expensive
    # registry/auto-discovery lookups. The previous V475 check ran
    # AFTER get_city_by_slug_auto + permitdb.lookup_prod_city_by_slug,
    # which can be slow on cold caches and is what was making
    # /permits/salem hit Render's 30s worker timeout. This runs ONE
    # indexed COUNT against permits + a fallback against
    # contractor_profiles — both ~1ms even on cold cache. If both are
    # zero AND the slug isn't in the hand-curated CITY_SEO_CONFIG, we
    # render the lightweight Coming Soon page and return.
    try:
        _check_conn = permitdb.get_connection()
        _row = _check_conn.execute(
            "SELECT COUNT(*) FROM permits WHERE source_city_key = ? LIMIT 1",
            (city_slug,)
        ).fetchone()
        _early_permits = _row[0] if _row else 0
        if _early_permits == 0 and city_slug not in CITY_SEO_CONFIG:
            _row = _check_conn.execute(
                "SELECT COUNT(*) FROM contractor_profiles "
                "WHERE source_city_key = ? LIMIT 1",
                (city_slug,)
            ).fetchone()
            _early_profiles = _row[0] if _row else 0
            if _early_profiles == 0:
                _display = (city_slug.replace('-', ' ')
                            .replace('  ', ' ').title())
                _resp = render_template(
                    'city_paused.html',
                    city_name=_display,
                    state='',
                    last_updated=None,
                    canonical_url=f"{SITE_URL}/permits/{request_slug}",
                    robots="noindex, follow",
                    is_coming_soon=True,
                )
                _r = make_response(_resp)
                _r.headers['Cache-Control'] = 'public, max-age=86400'
                return _r
    except Exception as _e:
        print(f"[V475] early-exit check failed for {city_slug}: {_e}", flush=True)

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

    # V475 Bug 5: empty-city early exit. If this slug has 0 permits AND
    # 0 contractor profiles, render a lightweight "Coming Soon" page
    # instead of running the dozen expensive queries that follow. The
    # bot army hitting /permits/salem and similar empty cities was the
    # second-largest cause of homepage timeouts (the daemon write lock
    # piled up behind dead-page renders that never produced anything).
    try:
        _check_conn = permitdb.get_connection()
        _row = _check_conn.execute(
            "SELECT COUNT(*) FROM permits WHERE source_city_key = ? LIMIT 1",
            (city_slug,)
        ).fetchone()
        _v475_permit_count = _row[0] if _row else 0
        if _v475_permit_count == 0:
            _row = _check_conn.execute(
                "SELECT COUNT(*) FROM contractor_profiles WHERE source_city_key = ? LIMIT 1",
                (city_slug,)
            ).fetchone()
            _v475_profile_count = _row[0] if _row else 0
            if _v475_profile_count == 0:
                _resp = render_template(
                    'city_paused.html',
                    city_name=config.get('name', city_slug.replace('-', ' ').title()),
                    state=config.get('state', ''),
                    last_updated=None,
                    canonical_url=f"{SITE_URL}/permits/{city_slug}",
                    robots="noindex, follow",
                    is_coming_soon=True,
                )
                _r = make_response(_resp)
                # V475 Bug 6: long-cache empty-city pages so bot retraversal
                # doesn't keep beating the DB.
                _r.headers['Cache-Control'] = 'public, max-age=86400'
                return _r
    except Exception as _e:
        print(f"[V475] empty-city early-exit check failed for {city_slug}: {_e}", flush=True)

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
        # V338: replace the live stats query with cached payload (covers
        # permit_count/total_value/high_value_count + newest_date +
        # with_value + violations_total in one shared 5-min cache slot).
        _stats_payload = _get_cached_city_stats(
            f"slug:{city_slug}", _prod_city_id, filter_name, filter_state, conn
        )
        stats_row = {
            'permit_count': _stats_payload['permit_count'],
            'total_value': _stats_payload['total_value'],
            'high_value_count': _stats_payload['high_value_count'],
        }
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
        # V338: cached stats (see prod_city branch above).
        _stats_payload = _get_cached_city_stats(
            f"name:{filter_name}|{filter_state or ''}", None, filter_name, filter_state, conn
        )
        stats_row = {
            'permit_count': _stats_payload['permit_count'],
            'total_value': _stats_payload['total_value'],
            'high_value_count': _stats_payload['high_value_count'],
        }
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
    # V423 (CODE_V422 Phase 2): also override when prod_cities.newest_permit_date
    # is OLDER than the actual MAX(date) from the cached stats payload.
    # Chicago was showing "Archival data" because prod_cities was stale
    # while real permits existed for 2026-04-24. The recount job updates
    # prod_cities asynchronously, so the page can read a stale value
    # between collection cycles. Prefer the stats payload's newest_date
    # whenever it's newer than prod_cities.newest_permit_date.
    _stats_newest = None
    try:
        _stats_newest = _stats_payload.get('newest_date') if _stats_payload else None
    except Exception:
        _stats_newest = None
    if permit_count > 0 and _stats_newest:
        if not newest_permit_date or _stats_newest[:10] > newest_permit_date[:10]:
            newest_permit_date = _stats_newest

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

    # V355 (CODE_V351 Part 2 Bug E): the stalled-projects (V252 F3) and
    # zip-heatmap (V251 F13) sections are removed from the city page in
    # this same PR. Their backing queries scanned the full per-city permits
    # slice (two more aggregate scans on top of V338's stats cache) and
    # were rendered below-fold where almost nobody scrolls. Defaulting both
    # collections to [] keeps the template variable contract for any other
    # caller / partial that still references them.
    stalled_permits = []
    zip_heatmap = []

    # V358 HOTFIX: V357 added a property_owners_rows query and passed it
    # as property_owners= to render_template, but the existing V284
    # _get_property_owners() call at the render site was already passing
    # property_owners=. Duplicate kwargs are a parse error → server.py
    # failed to import → 502 across the whole site. Removed both the
    # V357 query and the V357 kwarg; V284's helper handles the section.

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

    # V312 (CODE_V280b Bug 8): hide the Value column when <5% of this city's
    # permits have an estimated_cost. Most permit feeds don't expose dollar
    # amounts; rendering a column of em-dashes makes the page look broken.
    # V338: derive value-coverage from the cached stats payload — same
    # numbers, no extra query. The 50-row floor still applies so a brand-new
    # city with 3 permits doesn't suppress the column.
    hide_value_column = False
    try:
        _total = _stats_payload.get('permit_count') or 0
        _with_value = _stats_payload.get('with_value') or 0
        if _total >= 50:
            hide_value_column = (_with_value / _total) < 0.05
    except Exception:
        hide_value_column = False

    # V312 (CODE_V280b Bug 13): pull recent violations for this city.
    # V317 fix: case-insensitive city match. CITY_REGISTRY has chicago_il
    # name="CHICAGO" (uppercase) but violations table city="Chicago" — the
    # original case-sensitive WHERE returned 0 rows for Chicago. Use UPPER()
    # on both sides so it works regardless of registry casing convention.
    # Limit 25 rows for the in-page tab so we don't blow the response size
    # on cities with many violations (NYC HPD has 4M+).
    violations_rows = []
    # V338: read violations_total from the cached stats payload (one query
    # already paid for upstream). Skip the LIMIT 25 sample query when total
    # is zero.
    violations_total = _stats_payload.get('violations_total', 0) if _stats_payload else 0
    try:
        _vconn = permitdb.get_connection()
        if violations_total > 0:
            violations_rows = [dict(r) for r in _vconn.execute("""
                SELECT violation_date AS date, address,
                       violation_description AS description,
                       violation_type, status
                FROM violations
                WHERE UPPER(city) = UPPER(?) AND UPPER(state) = UPPER(?)
                ORDER BY violation_date DESC
                LIMIT 25
            """, (filter_name, filter_state)).fetchall()]
    except Exception as e:
        print(f"[V312 violations] query failed for {city_slug}: {e}", flush=True)

    # V328 (CODE_V320 Part B): unified permits + violations table.
    # Replaces both the "Recent Permits" preview and the V312 violations
    # block with one date-sorted feed plus a filter dropdown and proper
    # pagination. Reads ?page, ?filter, ?per_page from the request.
    try:
        _filter = (request.args.get('filter', 'all') or 'all').lower()
        if _filter not in ('all', 'permits', 'violations'):
            _filter = 'all'
        try:
            _page = max(1, int(request.args.get('page', 1) or 1))
            _per_page = max(10, min(100, int(request.args.get('per_page', 25) or 25)))
        except (TypeError, ValueError):
            _page, _per_page = 1, 25
    except Exception:
        _filter, _page, _per_page = 'all', 1, 25

    unified_records = []
    # V330: reuse counts the route already computed instead of re-running
    # COUNT(*) on permits + violations.
    # V446 P0 (CODE_V446): unified_records previously ignored the V251 F2
    # filter params (trade/days/zip/min_value), so the table always showed
    # the full unfiltered set even when the summary said "Showing 4,959
    # permits for Electrical". Applying the same filter clause as the
    # summary count, plus case-insensitive trade matching so URL-cased
    # values (?trade=roofing) work alongside dropdown-cased values.
    _u_filter_sql = ""
    _u_filter_args = []
    if _filter_zip:
        _u_filter_sql += " AND zip = ?"
        _u_filter_args.append(_filter_zip)
    if _filter_trade:
        _u_filter_sql += " AND LOWER(trade_category) = LOWER(?)"
        _u_filter_args.append(_filter_trade)
    if _filter_days:
        _u_filter_sql += (
            f" AND COALESCE(filing_date, issued_date, date) "
            f">= date('now', '-{_filter_days} days')"
        )
    if _filter_min_value:
        _u_filter_sql += f" AND estimated_cost >= {_filter_min_value}"

    if _filter == 'permits':
        total_records = (
            filtered_permit_count if _filters_active else permit_count
        ) or 0
    elif _filter == 'violations':
        total_records = violations_total or 0
    else:
        total_records = (
            (filtered_permit_count if _filters_active else permit_count) or 0
        ) + (violations_total or 0)
    try:
        _uconn = permitdb.get_connection()
        if _filter == 'permits':
            unified_records = [{
                'record_type': 'permit',
                'record_date': r['record_date'],
                'address': r['address'],
                'type_label': r['type_label'],
                'description': r['description'],
                'contractor_name': r['contractor_name'],
                'status': None,
            } for r in _uconn.execute(f"""
                SELECT COALESCE(filing_date, issued_date, date) AS record_date,
                       address,
                       permit_type AS type_label,
                       description,
                       contractor_name
                FROM permits
                WHERE source_city_key = ?
                  AND COALESCE(filing_date, issued_date, date) IS NOT NULL
                  {_u_filter_sql}
                ORDER BY record_date DESC
                LIMIT ? OFFSET ?
            """, (city_slug, *_u_filter_args, _per_page, (_page - 1) * _per_page)).fetchall()]
        elif _filter == 'violations':
            unified_records = [{
                'record_type': 'violation',
                'record_date': r['record_date'],
                'address': r['address'],
                'type_label': r['type_label'],
                'description': r['description'],
                'contractor_name': None,
                'status': r['status'] or 'Open',
            } for r in _uconn.execute("""
                SELECT violation_date AS record_date,
                       address,
                       COALESCE(violation_type, '') AS type_label,
                       violation_description AS description,
                       status
                FROM violations
                WHERE UPPER(city)=UPPER(?) AND UPPER(state)=UPPER(?)
                ORDER BY violation_date DESC
                LIMIT ? OFFSET ?
            """, (filter_name, filter_state, _per_page, (_page - 1) * _per_page)).fetchall()]
        else:
            # V446 P0: filters apply to permits only. Violations don't have
            # trade_category/zip/estimated_cost columns, so the violation half
            # of the UNION stays unfiltered (it still respects the date filter
            # via violation_date if present). When trade/zip/value are active,
            # narrow the UNION to permits only — mixing filtered permits with
            # all violations would inflate counts and surface unrelated rows.
            _has_perm_only_filter = bool(_filter_zip or _filter_trade or _filter_min_value)
            if _has_perm_only_filter:
                unified_records = [dict(r) for r in _uconn.execute(f"""
                    SELECT 'permit' AS record_type,
                           COALESCE(filing_date, issued_date, date) AS record_date,
                           address,
                           permit_type AS type_label,
                           description,
                           contractor_name,
                           NULL AS status
                    FROM permits
                    WHERE source_city_key = ?
                      AND COALESCE(filing_date, issued_date, date) IS NOT NULL
                      {_u_filter_sql}
                    ORDER BY record_date DESC
                    LIMIT ? OFFSET ?
                """, (city_slug, *_u_filter_args, _per_page, (_page - 1) * _per_page)).fetchall()]
                # Also collapse total_records to permits-only since violations
                # are excluded from the visible page.
                total_records = filtered_permit_count or 0
            else:
                # No permit-side filter (or only days, which applies to both).
                _viol_date_sql = ""
                if _filter_days:
                    _viol_date_sql = (
                        f" AND violation_date >= date('now', '-{_filter_days} days')"
                    )
                unified_records = [dict(r) for r in _uconn.execute(f"""
                    SELECT 'permit' AS record_type,
                           COALESCE(filing_date, issued_date, date) AS record_date,
                           address,
                           permit_type AS type_label,
                           description,
                           contractor_name,
                           NULL AS status
                    FROM permits
                    WHERE source_city_key = ?
                      AND COALESCE(filing_date, issued_date, date) IS NOT NULL
                      {_u_filter_sql}
                    UNION ALL
                    SELECT 'violation' AS record_type,
                           violation_date AS record_date,
                           address,
                           COALESCE(violation_type, '') AS type_label,
                           violation_description AS description,
                           NULL AS contractor_name,
                           COALESCE(status, 'Open') AS status
                    FROM violations
                    WHERE UPPER(city)=UPPER(?) AND UPPER(state)=UPPER(?)
                      {_viol_date_sql}
                    ORDER BY record_date DESC
                    LIMIT ? OFFSET ?
                """, (city_slug, *_u_filter_args, filter_name, filter_state,
                       _per_page, (_page - 1) * _per_page)).fetchall()]
    except Exception as e:
        print(f"[V446 unified] query failed for {city_slug}: {e}", flush=True)

    # V332 (CODE_V321 Bug D): strip the redundant "PERMIT -" / "PERMIT –"
    # prefix that Chicago and a few other Accela-style sources put on
    # every type_label. The full string is still on permits.permit_type
    # (and the detail card) — this is purely cosmetic for the table pill.
    _prefix_strip = ('PERMIT - ', 'PERMIT – ', 'Permit - ', 'Permit – ')
    for _r in unified_records:
        _t = _r.get('type_label')
        if _t and isinstance(_t, str):
            for _p in _prefix_strip:
                if _t.startswith(_p):
                    _r['type_label'] = _t[len(_p):]
                    break

    _total_pages = max(1, (total_records + _per_page - 1) // _per_page)

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

    # V467 (CODE_V467 internal linking): single-segment city URLs (/permits/chicago-il)
    # come through city_landing_inner. Pass seo_blog_slug here too so the
    # "Read report →" CTA renders on these pages, not just the two-segment route.
    _v467_blog_slugs = {
        'chicago-il': 'chicago-building-permits-2026',
        'miami-dade-county': 'miami-dade-solar-permits-2026',
        'phoenix-az': 'phoenix-code-violations-2026',
        'san-antonio-tx': 'san-antonio-building-permits-2026',
        'new-york-city': 'nyc-building-violations-2026',
    }
    _v467_seo_blog_slug = _v467_blog_slugs.get(city_slug)

    # V474 (CODE_V474_BUYER_PERSONAS B+C): data-driven meta + FAQ JSON-LD on
    # this single-segment route too. Mirrors the logic added to
    # state_city_landing in routes/city_pages.py. Pulls live counts via
    # the connection that's already open in this scope.
    _v474_profiles = 0
    _v474_phones = 0
    _v474_owners = 0
    _v474_violations = 0
    # V474b: use positional access (_r[0] / _r[1]) for SQL counts so the
    # logic works regardless of whether the connection has row_factory
    # set. The earlier `_r['c']` lookup silently returned 0 for cities
    # whose conn didn't carry a Row factory.
    # V474c: open a FRESH connection instead of reusing the long-lived
    # `conn` variable from earlier in this function — Henderson test
    # showed all three counts coming back 0 with the shared conn even
    # though the same SQL run via /api/admin/query returned the real
    # counts (2,572 / 871 / 120,939). Suspected thread/lifetime issue.
    _v474_conn = permitdb.get_connection()
    try:
        _r = _v474_conn.execute(
            "SELECT COUNT(*), "
            "SUM(CASE WHEN phone IS NOT NULL AND phone <> '' THEN 1 ELSE 0 END) "
            "FROM contractor_profiles WHERE source_city_key = ?",
            (city_slug,),
        ).fetchone()
        if _r:
            _v474_profiles = _r[0] or 0
            _v474_phones = _r[1] or 0
    except Exception as _e:
        print(f"[V474] profiles count failed for {city_slug}: {_e}", flush=True)
    try:
        if _prod_city_id:
            _r = _v474_conn.execute(
                "SELECT COUNT(*) FROM violations WHERE prod_city_id = ?",
                (_prod_city_id,),
            ).fetchone()
            _v474_violations = _r[0] if _r else 0
    except Exception as _e:
        print(f"[V474] violations count failed for {city_slug}: {_e}", flush=True)
    try:
        _r = _v474_conn.execute(
            "SELECT COUNT(*) FROM property_owners "
            "WHERE LOWER(city) = LOWER(?) AND state = ?",
            (config.get('name', ''), config.get('state', '')),
        ).fetchone()
        _v474_owners = _r[0] if _r else 0
    except Exception as _e:
        print(f"[V474] owners count failed for {city_slug}: {_e}", flush=True)
    print(f"[V474] {city_slug}: profiles={_v474_profiles} phones={_v474_phones} "
          f"violations={_v474_violations} owners={_v474_owners} "
          f"name={config.get('name')!r} state={config.get('state')!r} "
          f"prod_city_id={_prod_city_id}", flush=True)

    # Override meta only if this city is NOT in the hardcoded CITY_SEO_CONFIG
    # (those have hand-tuned copy). For every auto-generated city, swap to
    # buyer-intent data-driven copy.
    _v474_dynamic = city_slug not in CITY_SEO_CONFIG
    if _v474_dynamic:
        _has_p = _v474_profiles >= 100
        _has_ph = _v474_phones >= 50
        _has_v = _v474_violations > 0
        _has_o = _v474_owners > 0
        _vp = f"{_v474_profiles:,}"
        _vh = f"{_v474_phones:,}"
        _vv = f"{_v474_violations:,}"
        _vo = f"{_v474_owners:,}"
        _disp = config.get('name', city_slug)
        # V480 P1-1: see routes/city_pages.py — same fix applied here. Short
        # natural titles under 60 chars; numbers move to meta_description so
        # Google never truncates the title mid-word or strands a trailing pipe.
        if _has_p and _has_v and _has_o:
            config['meta_title'] = f"{_disp} Construction Leads | PermitGrab"
            config['meta_description'] = (
                f"Access {_vp} contractor profiles{' with phone numbers' if _has_ph else ''}, "
                f"{_vv} code violation properties, and {_vo} property owner "
                f"records in {_disp}. Updated daily from official sources. $149/mo."
            )[:200]
        elif _has_p and _has_ph and not _has_v:
            config['meta_title'] = f"{_disp} Contractor Leads | PermitGrab"
            config['meta_description'] = (
                f"Reach {_vp} active contractors in {_disp} — {_vh} with "
                f"verified phone numbers. Filter by trade, see new permits "
                f"daily. $149/mo."
            )[:200]
        elif _has_v and not _has_p:
            config['meta_title'] = f"{_disp} Code Violations | PermitGrab"
            config['meta_description'] = (
                f"{_vv} code violation properties in {_disp} — find "
                f"motivated sellers and distressed properties"
                f"{' with owner names' if _has_o else ''}. Updated daily "
                f"from city code enforcement. $149/mo."
            )[:200]
        elif _has_p:
            config['meta_title'] = f"{_disp} Building Permits | PermitGrab"
            config['meta_description'] = (
                f"Track building permits and {_vp} active contractors in "
                f"{_disp}. Updated daily from official city data. $149/mo."
            )[:200]
        elif _has_o:
            # V474b: owners-only cities (Madison, Reno, Tucson, Tampa, etc.
            # — the V474 sweep landed huge assessor backfills with no
            # paired permit/violation data). Use the homeowner-lead angle.
            config['meta_title'] = f"{_disp} Homeowner Leads | PermitGrab"
            config['meta_description'] = (
                f"Reach {_vo} property owners in {_disp} with names and "
                f"addresses. Ideal homeowner leads for solar, insurance, "
                f"and home services. $149/mo."
            )[:200]

    # FAQ JSON-LD with buyer-intent questions; prune by available data
    _v474_faq = []
    _v474_disp = config.get('name', city_slug)
    if _v474_profiles > 0:
        _v474_faq.append((
            f"How can I find contractors in {_v474_disp}?",
            f"PermitGrab tracks {_v474_profiles:,} active contractors in "
            f"{_v474_disp} who have pulled building permits. "
            f"{_v474_phones:,} have verified phone numbers. Filter by "
            f"trade: electrical, plumbing, HVAC, roofing, and more."
        ))
    if _v474_violations > 0:
        _v474_faq.append((
            f"Where can I find code violation properties in {_v474_disp}?",
            f"PermitGrab has {_v474_violations:,} code violation records "
            f"in {_v474_disp} from official city code enforcement databases. "
            f"These properties may indicate motivated sellers — owners "
            f"facing violations are often willing to sell below market value."
        ))
    if _v474_owners > 0:
        _v474_faq.append((
            f"How do I get homeowner leads from building permits in {_v474_disp}?",
            f"PermitGrab provides {_v474_owners:,} property owner records "
            f"for {_v474_disp} including owner names and addresses. "
            f"Homeowners who just pulled permits are actively investing in "
            f"their property — ideal leads for solar, insurance, and home "
            f"service companies."
        ))
    _v474_faq.append((
        f"How often is the {_v474_disp} permit data updated?",
        f"PermitGrab collects new permit data for {_v474_disp} every 30 "
        f"minutes from official city open data portals. Violation and "
        f"property owner data is refreshed daily to monthly depending on "
        f"the source."
    ))
    _v474_faq_jsonld = {
        '@context': 'https://schema.org',
        '@type': 'FAQPage',
        'mainEntity': [
            {
                '@type': 'Question',
                'name': q,
                'acceptedAnswer': {'@type': 'Answer', 'text': a},
            } for q, a in _v474_faq
        ],
    }

    _v475_html = render_template(
        'city_landing_v77.html',  # V175: Unified to one template (was city_landing.html)
        seo_blog_slug=_v467_seo_blog_slug,
        v474_faq=_v474_faq,
        v474_faq_jsonld=_v474_faq_jsonld,
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
        violations=violations_rows,            # V312 (Bug 13)
        violations_count=violations_total,     # V312 (Bug 13)
        hide_value_column=hide_value_column,   # V312 (Bug 8)
        unified_records=unified_records,       # V328 (CODE_V320 Part B)
        total_records=total_records,           # V328
        current_page=_page,                    # V328
        total_pages=_total_pages,              # V328
        current_filter=_filter,                # V328
        per_page=_per_page,                    # V328
        market_insights=market_insights,  # V236 PR#5
        property_owners=_get_property_owners(config['name'], current_state, limit=10),  # V284
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
        stalled_permits=stalled_permits,  # V252 F3
    )
    # V475 Bug 6: Cache-Control on full city pages. Bot traffic was
    # piling up identical requests and beating the DB. 5-min public
    # cache lets Cloudflare-style intermediaries serve repeat hits.
    _v475_resp = make_response(_v475_html)
    _v475_resp.headers['Cache-Control'] = 'public, max-age=300'
    return _v475_resp






# ===========================
# V28: SEARCH PAGE — Required for SearchAction schema (sitelinks searchbox)
# ===========================



# ===========================
# V17e: CITIES BROWSE PAGE — Hub for all city landing pages
# ===========================



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
















# ===========================
# ACCOUNT SETTINGS (V8)
# ===========================




# V170 B4: Saved Searches API








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






# V12.26: Competitor Watch API


# V12.26: Check for competitor matches in recent permits






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

        # V476 Bug 3: pre-cycle memory guardrail. If system memory is
        # already > 75% (e.g. an admin-triggered import is consuming
        # RAM, or a previous cycle's data is still GC-pending), skip
        # this cycle and try again in 5 min. This prevents the daemon
        # from compounding memory pressure to OOM (UAT round 1 had
        # site totally down at 83.8%). The mid-cycle V457 check
        # remains as a backstop that triggers SIGTERM at 1700MB RSS.
        try:
            import psutil as _v476_ps, gc as _v476_gc
            _v476_gc.collect()
            _v476_pct = _v476_ps.virtual_memory().percent
            if _v476_pct > 75:
                print(f"[{datetime.now()}] V476: skipping collection cycle — "
                      f"memory at {_v476_pct:.1f}% (> 75% guardrail)", flush=True)
                time.sleep(300)
                continue
        except Exception:
            pass

        # V229 C1: capture cycle start for dynamic sleep calculation below
        _v229_cycle_start = time.time()
        print(f"[{datetime.now()}] V12.50: Starting scheduled collection cycle...")

        # V479: refresh stats cache up-front the first time so a fresh
        # worker has a populated cache available to templates. Failures
        # here are non-fatal — pages will fall back to defaults.
        try:
            from stats_cache import refresh_stats_cache as _v479_refresh, get_cached_stats as _v479_get
            if not _v479_get().get('updated_at'):
                _v479_refresh(permitdb.get_connection())
        except Exception as _e:
            print(f"[{datetime.now()}] V479: pre-cycle stats refresh failed: {_e}", flush=True)

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

        # V457 (CODE_V455 Phase 2 follow-on): mid-cycle memory check.
        # V455's recycle-on-high-memory only fires AT cycle end, but
        # heavy phases (refresh_contractor_profiles, collect_violations)
        # can push RSS to 2GB+ DURING the cycle. Check between phases
        # — if RSS > 1700MB, SIGTERM ourselves now so gunicorn drains
        # and respawns before we OOM.
        def _v457_mem_check(phase):
            try:
                import psutil as _ps, os as _ox, signal as _sg, gc as _gc457
                _gc457.collect()
                _rss = _ps.Process().memory_info().rss / 1024 / 1024
                if _rss > 1700:
                    print(
                        f"[{datetime.now()}] V457: after {phase} RSS={_rss:.0f}MB > 1700MB — "
                        f"SIGTERM self for recycle",
                        flush=True,
                    )
                    _ox.kill(_ox.getpid(), _sg.SIGTERM)
                    return True
            except Exception:
                pass
            return False

        if _v457_mem_check("collect_refresh"):
            return

        # V229 D2: Prune old permits (keep last 365 days; was 90).
        # delete_old_permits also skips cities stale >30d so a dead-upstream
        # city doesn't get its entire history wiped by the daily prune.
        try:
            deleted = permitdb.delete_old_permits(days=365)
            if deleted > 0:
                print(f"[{datetime.now()}] Pruned {deleted} old permits.")
        except Exception as e:
            print(f"[{datetime.now()}] Prune error: {e}")

        # V457: gate the heaviest phase on memory headroom. If we're
        # already at 1500MB+ before refresh_contractor_profiles starts,
        # skip it this cycle — the next cycle (after worker recycles)
        # will pick it up. Refresh isn't strictly required for collection.
        try:
            import psutil as _ps_pre
            _rss_pre = _ps_pre.Process().memory_info().rss / 1024 / 1024
            if _rss_pre > 1500:
                print(
                    f"[{datetime.now()}] V457: skipping refresh_contractor_profiles "
                    f"(RSS={_rss_pre:.0f}MB > 1500MB pre-phase headroom)",
                    flush=True,
                )
                raise StopIteration  # skip to except below
        except StopIteration:
            pass
        except Exception:
            pass
        # V183: Refresh contractor profiles from new permit data.
        # Runs BEFORE violations so that update_city_emblems (called at
        # end of collect_violations) reflects both new profiles AND
        # new violations in a single pass.
        try:
            # V457 inline guard — skip if pre-phase memory was high
            import psutil as _ps_g
            if _ps_g.Process().memory_info().rss / 1024 / 1024 > 1500:
                raise RuntimeError("v457_skip_profile_refresh")
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

        # V456 (CODE_V455 Phase 2): truncate the WAL after every cycle so
        # the V445 3.4GB WAL incident can't reoccur. PRAGMA wal_checkpoint
        # (TRUNCATE) merges the WAL back into the main DB and shrinks the
        # WAL file to 0. Will block briefly if any concurrent reader holds
        # a snapshot, but daemon already runs at low contention vs the
        # gthread workers, and the per-connection busy_timeout=60000 from
        # db.py covers transient blocks.
        try:
            _wal_conn = permitdb.get_connection()
            _wal_result = _wal_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            if _wal_result:
                print(
                    f"[{datetime.now()}] V456: WAL checkpoint(TRUNCATE) "
                    f"result={_wal_result}",
                    flush=True,
                )
        except Exception as _wal_e:
            print(f"[{datetime.now()}] V456: WAL checkpoint failed (non-fatal): {_wal_e}", flush=True)

        # V455 (CODE_V455 Phase 2): force worker recycle when memory has
        # accumulated past a safe ceiling. Python doesn't return freed
        # memory to the OS easily, so cycles keep climbing the worker's
        # RSS even though individual cities clean up. V450/V453/V454's
        # bail thresholds only fire INSIDE Phase 2 of collect_refresh,
        # not after violations + staleness + propagate. Hooking in here
        # — at cycle end — catches the cumulative growth and asks
        # gunicorn for a clean recycle BEFORE we OOM and Render kills us.
        # SIGTERM to self triggers gunicorn's graceful_timeout flow:
        # current requests drain, then master forks a fresh worker.
        try:
            import psutil as _psutil_v455
            import os as _os_v455
            import signal as _sig_v455
            _rss_mb = _psutil_v455.Process().memory_info().rss / 1024 / 1024
            if _rss_mb >= 1500:
                print(
                    f"[{datetime.now()}] V455: cycle done, RSS={_rss_mb:.0f}MB "
                    f">= 1500MB ceiling — sending SIGTERM to self for clean "
                    f"gunicorn recycle (master will respawn fresh worker)",
                    flush=True,
                )
                _os_v455.kill(_os_v455.getpid(), _sig_v455.SIGTERM)
                return  # daemon thread exits; new worker will respawn it
        except Exception as _e_v455:
            print(f"[{datetime.now()}] V455: recycle-on-high-memory check failed (non-fatal): {_e_v455}", flush=True)

        # V229 C1: dynamic cycle sleep. Was a flat time.sleep(3600) regardless
        # of how long the cycle took, so a 45-min cycle became a real 1h45m
        # interval. Target 30 min between cycle completions (minimum 5-min
        # rest, maximum 1h rest) — under normal load that's ~2 full passes
        # per hour instead of <1.
        _duration = max(0, int(time.time() - _v229_cycle_start))

        # V479: end-of-cycle stats cache refresh. Runs in this daemon
        # thread (NOT a request handler), so the 1.28M-row GROUP BY can
        # take ~10s without affecting page latency. Templates read from
        # the resulting in-memory dict in < 1ms.
        try:
            from stats_cache import refresh_stats_cache as _v479_refresh
            refresh_stats_cache_thread_started = time.time()
            _v479_refresh(permitdb.get_connection())
            _v479_elapsed = int(time.time() - refresh_stats_cache_thread_started)
            print(f"[{datetime.now()}] V479: stats cache refreshed (took {_v479_elapsed}s)", flush=True)
        except Exception as _e:
            print(f"[{datetime.now()}] V479: stats refresh failed: {_e}", flush=True)

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

    # V276: Deploy-restart dedup. The counters above are in-memory, so three
    # deploys in one morning = three morning threads, each with
    # last_digest_date=None, each independently firing send_daily_digest()
    # once 7 AM ET hits. That's exactly what happened on 2026-04-24 (V271
    # 6:15 AM, V272 6:43 AM, V273 7:44 AM → 3 duplicate digests per user).
    # Cure: seed the counters from system_state rows that the success-path
    # already writes (`digest_last_success`, plus the two new lifecycle
    # keys below). If today's date already appears, the 7-9 AM gate skips.
    try:
        _bootstrap_today = datetime.now(et).date()
        _conn = permitdb.get_connection()
        _seed = {}
        for row in _conn.execute(
            "SELECT key, value FROM system_state WHERE key IN "
            "('digest_last_success', 'lifecycle_last_success', 'onboarding_last_success')"
        ).fetchall():
            # Normalize sqlite3.Row / tuple / dict into (key, value)
            if isinstance(row, dict):
                _seed[row['key']] = row['value']
            else:
                _seed[row[0]] = row[1]
        for key, date_var in (
            ('digest_last_success', 'digest'),
            ('lifecycle_last_success', 'lifecycle'),
            ('onboarding_last_success', 'onboarding'),
        ):
            iso = _seed.get(key)
            if not iso:
                continue
            try:
                stored = datetime.fromisoformat(iso).date()
            except ValueError:
                continue
            if stored == _bootstrap_today:
                if date_var == 'digest':
                    last_digest_date = _bootstrap_today
                elif date_var == 'lifecycle':
                    last_lifecycle_date = _bootstrap_today
                elif date_var == 'onboarding':
                    last_onboarding_date = _bootstrap_today
        print(f"[{datetime.now()}] V276: digest dedup bootstrap — digest={last_digest_date}, "
              f"lifecycle={last_lifecycle_date}, onboarding={last_onboarding_date}")
    except Exception as e:
        print(f"[{datetime.now()}] V276: dedup bootstrap skipped ({e}); counters stay None")

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
                        # V276: persist so deploy restarts skip re-running
                        try:
                            _conn = permitdb.get_connection()
                            _conn.execute(
                                "INSERT OR REPLACE INTO system_state (key, value, updated_at) "
                                "VALUES ('lifecycle_last_success', ?, datetime('now'))",
                                (datetime.now().isoformat(),)
                            )
                            _conn.commit()
                        except Exception:
                            pass
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
                        # V276: persist so deploy restarts skip re-running
                        try:
                            _conn = permitdb.get_connection()
                            _conn.execute(
                                "INSERT OR REPLACE INTO system_state (key, value, updated_at) "
                                "VALUES ('onboarding_last_success', ?, datetime('now'))",
                                (datetime.now().isoformat(),)
                            )
                            _conn.commit()
                        except Exception:
                            pass
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

    V473b corrective: the V471 PR4 no-op was based on a false premise —
    there is no permitgrab-worker Render service (it was defined in
    render.yaml but never created in the actual Render project). The
    web process is the only collector. Calling this function from the
    /api/admin/start-collectors endpoint spawns the daemon threads
    inside this process, which is the intended (and historical) design
    per CLAUDE.md: "Daemon: Does NOT auto-start on deploy. Must call:
    POST /api/admin/start-collectors".

    V471 PR2-prep is preserved: nothing here runs at module import time.
    Spawning happens only on explicit call from the admin endpoint, which
    means worker re-fork (or V455 self-recycle) doesn't deadlock against
    `from server import *` in the blueprint modules.
    """
    global _collector_started
    if _collector_started:
        return
    _collector_started = True

    # V481 (worker/web split): the daemons MUST only run inside the
    # permitgrab-worker Background Worker service. The web process and
    # the worker share the same module, but only the worker has
    # WORKER_MODE=1 set in its Render env. If we ever spawn these
    # threads in the web process again, the V480 wedge returns: 12
    # gthread request handlers + 3 daemons share one Python GIL on a
    # single vCPU and an aggregate-query refresh starves every request.
    if os.environ.get('WORKER_MODE') != '1':
        print(
            f"[{datetime.now()}] V481: start_collectors no-op on web "
            f"process (WORKER_MODE != '1'). Daemons run on the "
            f"permitgrab-worker service via worker.py.",
            flush=True,
        )
        return

    os.makedirs(DATA_DIR, exist_ok=True)

    # V12.2: Test network connectivity before starting threads
    try:
        _test_outbound_connectivity()
    except Exception as _e:
        print(f"[{datetime.now()}] connectivity test error (non-fatal): {_e}", flush=True)

    # V75: Apply known fixes to broken city_sources configs
    print(f"[{datetime.now()}] V75: Applying known config fixes...", flush=True)
    try:
        fixes = fix_known_broken_configs()
        print(f"[{datetime.now()}] V75: Applied {len(fixes)} config fixes", flush=True)
    except Exception as e:
        print(f"[{datetime.now()}] V75: Config fix error (non-fatal): {e}", flush=True)

    # V77: Sync CITY_REGISTRY to prod_cities — activates paused cities
    print(f"[{datetime.now()}] V77: Syncing CITY_REGISTRY to prod_cities...", flush=True)
    try:
        sync_city_registry_to_prod_cities()
        print(f"[{datetime.now()}] V77: Registry sync complete", flush=True)
    except Exception as e:
        print(f"[{datetime.now()}] V77: Registry sync error (non-fatal): {e}", flush=True)

    print(f"[{datetime.now()}] V67: Starting background collectors...", flush=True)
    # V414/V473b: skip initial_collection. scheduled_collection picks up the
    # same cities within ~30 min of starting, and the 180-day backfill is
    # the biggest startup memory spike. Re-enable via direct call only.
    print(f"[{datetime.now()}] V414: skipping run_initial_collection (scheduled_collection picks up same cities ~30 min)", flush=True)

    # Scheduled daily collection thread
    collector_thread = threading.Thread(
        target=scheduled_collection, name='scheduled_collection', daemon=True,
    )
    collector_thread.start()
    print(f"[{datetime.now()}] V67: Scheduled collection thread started", flush=True)

    # V229 C5: dedicated enrichment daemon. scheduled_collection runs
    # enrich_batch per cycle (30-60 min apart). This second thread runs
    # enrichment on its own 30-min cadence so the queue drains steadily
    # even when a collection cycle is slow/stuck.
    def _enrichment_daemon():
        time.sleep(600)  # let collection warm up first
        while True:
            try:
                from license_enrichment import is_import_running
                if is_import_running():
                    print(f"[{datetime.now()}] V242: enrichment daemon paused — license import in progress", flush=True)
                    time.sleep(30)
                    continue
            except Exception:
                pass
            try:
                from web_enrichment import enrich_batch
                result = enrich_batch(limit=200)
                print(f"[{datetime.now()}] [V229 C5] enrichment daemon: {result}", flush=True)
            except Exception as e:
                print(f"[{datetime.now()}] [V229 C5] enrichment daemon error: {e}", flush=True)
            time.sleep(1800)

    try:
        enrichment_thread = threading.Thread(
            target=_enrichment_daemon, name='enrichment_daemon', daemon=True,
        )
        enrichment_thread.start()
        print(f"[{datetime.now()}] V229 C5: enrichment daemon started", flush=True)
    except Exception as e:
        print(f"[{datetime.now()}] V229 C5: enrichment daemon failed to start: {e}", flush=True)

    # V475 corrective: V471 PR4 deleted three daemon threads from this
    # function (scheduled_collection, enrichment_daemon, email_scheduler).
    # The V473b corrective (commit 2c91a2a) restored the first two but
    # forgot email_scheduler — so daily digests stopped firing on
    # 2026-04-30 (yesterday's 11:01 UTC digest was the last one before
    # the V471 PR4 nerf at 2026-04-29 21:21 UTC; today's 11 AM UTC
    # window had no thread to run send_daily_digest()). Restoring the
    # V93 spawn pattern: threading.Thread targeting schedule_email_tasks.
    try:
        email_thread = threading.Thread(
            target=schedule_email_tasks, name='email_scheduler', daemon=True,
        )
        email_thread.start()
        print(f"[{datetime.now()}] V475: email_scheduler thread started", flush=True)
    except Exception as e:
        print(f"[{datetime.now()}] V475: email_scheduler failed to start: {e}", flush=True)

    print(f"[{datetime.now()}] V67: All collector threads started.", flush=True)

# V229 addendum K1 / V471 PR2-prep: single, lock-guarded spawner for
# _deferred_startup. Called only from the before_request hook now (the
# module-level call here was the cause of the V471 PR2 worker-restart
# deadlock — `from server import *` in blueprint modules raced the
# daemon-thread spawn against Python's import lock, wedging the new
# worker after V455 self-recycle). preload_data_from_disk() is also
# deferred — it does file I/O that shouldn't run before the WSGI app
# is ready to serve traffic.
_DEFERRED_STARTUP_LOCK = threading.Lock()
_DEFERRED_STARTUP_SPAWNED = False
_PRELOAD_DONE = False

def _ensure_deferred_startup_spawned():
    """Spawn the _deferred_startup daemon thread exactly once.

    V471 PR2-prep: also runs preload_data_from_disk() the first time, so
    `from server import app` does no I/O and spawns no threads at module
    import time. This decouples module load from worker startup, so the
    worker can re-import server.py cleanly after a V455 self-recycle
    even when blueprints do `from server import *`.
    """
    global _DEFERRED_STARTUP_SPAWNED, _PRELOAD_DONE
    with _DEFERRED_STARTUP_LOCK:
        if _DEFERRED_STARTUP_SPAWNED:
            return
        _DEFERRED_STARTUP_SPAWNED = True
        if not _PRELOAD_DONE:
            try:
                preload_data_from_disk()
            except Exception as _e:
                print(f"[V471] preload_data_from_disk failed: {_e}", flush=True)
            _PRELOAD_DONE = True
    threading.Thread(
        target=_deferred_startup, daemon=True, name='deferred_startup'
    ).start()

# V66: Module-level DB init removed — now deferred to first request via
# _deferred_startup(). V471 PR2-prep: daemon spawn + disk preload also
# moved out of module body; both fire from the before_request hook on
# the first HTTP request (already wired in this codebase). Local imports
# (`python3 -c "from server import app"`) and worker fork-imports both
# complete in <2s with zero side effects.
print(f"[{datetime.now()}] V471: Module loaded — daemon spawn deferred to first request", flush=True)


# ===========================
# MAIN
# ===========================


# ===========================
# V471 PR2 (CODE_V471 Part 1B): Flask Blueprints
# ===========================
# Routes that used to live in this file are in routes/<category>.py.
# Registered at the very end of server.py module body so blueprints can
# `from server import *` and get everything that was defined above.
# V471 PR2-prep moved daemon spawn + DB init out of module-level code,
# so blueprint imports no longer race against import-time side effects.
from routes.admin import admin_bp
from routes.api import api_bp
from routes.auth import auth_bp
from routes.city_pages import city_pages_bp
from routes.health import health_bp
from routes.seo import seo_bp
# V474: persona landing pages (real estate investors / contractors / home services)
from routes.persona_pages import persona_bp

app.register_blueprint(admin_bp)
app.register_blueprint(api_bp)
app.register_blueprint(auth_bp)
app.register_blueprint(city_pages_bp)
app.register_blueprint(health_bp)
app.register_blueprint(seo_bp)
app.register_blueprint(persona_bp)

if __name__ == '__main__':
    # Local development only (gunicorn handles production)
    print("=" * 50)
    print("PermitGrab Server Starting")
    print(f"Dashboard: http://localhost:5000")
    print(f"API: http://localhost:5000/api/permits")
    print("=" * 50)

    app.run(host='0.0.0.0', port=5000, debug=True)
# V146: Double-deploy test Sun Apr 12 09:44:58 CDT 2026
