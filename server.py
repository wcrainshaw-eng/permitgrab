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
import threading
import time
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

# V12.17: static_url_path='' serves static files from root (needed for GSC verification)
app = Flask(__name__, static_folder='static', static_url_path='', template_folder='templates')
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))

# V68: WSGI middleware to bypass ALL Flask processing for /api/health
# This ensures health checks ALWAYS return 200, even during pool exhaustion
class HealthCheckMiddleware:
    def __init__(self, wsgi_app):
        self.app = wsgi_app

    def __call__(self, environ, start_response):
        if environ.get('PATH_INFO') in ('/api/health', '/health'):
            import json
            status = '200 OK'
            response_headers = [('Content-Type', 'application/json')]
            start_response(status, response_headers)
            body = json.dumps({
                'status': 'ok',
                'version': 'V70',
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

@app.before_request
def _deferred_startup():
    """V69: Mark startup done but DO NOT start any background threads."""
    global _startup_done
    if _startup_done:
        return
    _startup_done = True
    # V70: NO background threads. NO Postgres pool. SQLite only. Just serve requests.
    print(f"[{datetime.now()}] V70: Server starting — Postgres DISABLED, SQLite only")
    print(f"[{datetime.now()}] V70: POST /api/admin/enable-postgres to enable Postgres pool")
    print(f"[{datetime.now()}] V70: POST /api/admin/start-collectors to start background threads")


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
    """V12.58: Validate admin key without hardcoded fallback. Returns (is_valid, error_response)."""
    secret = request.headers.get('X-Admin-Key')
    expected = os.environ.get('ADMIN_KEY')
    if not expected:
        return False, (jsonify({'error': 'Admin key not configured'}), 503)
    if secret != expected:
        return False, (jsonify({'error': 'Unauthorized'}), 401)
    return True, None


@app.route('/api/admin/reset-permits', methods=['POST'])
def admin_reset_permits():
    """Delete corrupted permits.json so next collection writes clean data."""
    valid, error = check_admin_key()
    if not valid:
        return error

    # DATA_DIR is defined later, use the same logic
    data_dir = '/var/data' if os.path.isdir('/var/data') else os.path.join(os.path.dirname(__file__), 'data')
    filepath = os.path.join(data_dir, 'permits.json')
    deleted = False
    if os.path.exists(filepath):
        os.remove(filepath)
        deleted = True
        print(f"[Admin] Deleted corrupted permits.json at {filepath}")

    return jsonify({
        'deleted': deleted,
        'path': filepath,
        'message': 'File deleted. Next collection cycle will write clean data.'
    })


@app.route('/api/admin/fix-addresses', methods=['POST'])
def admin_fix_addresses():
    """V12.55c: Fix Socrata location objects stored as raw JSON in address field."""
    valid, error = check_admin_key()
    if not valid:
        return error

    def run_fix():
        try:
            _fix_socrata_addresses()
        except Exception as e:
            print(f"[Admin] Address fix error: {e}")
            import traceback
            traceback.print_exc()

    thread = threading.Thread(target=run_fix, daemon=True)
    thread.start()
    return jsonify({'message': 'Address cleanup started in background'})


def _fix_socrata_addresses():
    """V12.57: Find and fix permits with raw JSON in address or description fields.
    Handles Socrata location objects, GeoJSON Points, and regenerates descriptions."""
    import ast
    from collector import parse_address_value

    try:
        conn = sqlite3.connect('/var/data/permitgrab.db')
        conn.row_factory = sqlite3.Row
    except Exception as e:
        print(f"[V12.57] Address cleanup: cannot connect to DB: {e}", flush=True)
        return

    # Find permits with JSON in address field
    cursor = conn.execute(
        "SELECT permit_number, address, zip, description, display_description FROM permits "
        "WHERE address LIKE '%{%' OR display_description LIKE '%{%'"
    )
    rows = cursor.fetchall()
    if not rows:
        print("[V12.57] No bad addresses/descriptions found — nothing to fix.", flush=True)
        return
    print(f"[V12.57] Found {len(rows)} permits with JSON in address/description. Fixing...", flush=True)

    fixed_addr = 0
    fixed_desc = 0
    for row in rows:
        pn = row['permit_number']
        raw_addr = row['address'] or ''
        existing_zip = row['zip'] or ''
        desc = row['description'] or ''
        disp_desc = row['display_description'] or ''

        updates = {}

        # Fix address if it contains JSON
        if '{' in raw_addr:
            try:
                clean_addr = parse_address_value(raw_addr)
                if clean_addr != raw_addr:
                    updates['address'] = clean_addr or ''
                    fixed_addr += 1
                    # Also try to extract zip from Socrata location
                    if not existing_zip:
                        try:
                            parsed = ast.literal_eval(raw_addr)
                            if isinstance(parsed, dict):
                                human = parsed.get('human_address', '')
                                if isinstance(human, str):
                                    import json as _json
                                    human = _json.loads(human)
                                if isinstance(human, dict):
                                    updates['zip'] = human.get('zip', '')
                        except Exception:
                            pass
            except Exception:
                pass

        # Fix description/display_description if it contains JSON
        for field in ['description', 'display_description']:
            val = row[field] or ''
            if '{' in val and ('human_address' in val or 'latitude' in val or "'type': 'Point'" in val or 'coordinates' in val):
                # Strip out the JSON portion from the description
                import re
                cleaned = re.sub(r"\{[^}]*'(?:human_address|latitude|type)'[^}]*\}", '', val)
                cleaned = re.sub(r'\s+', ' ', cleaned).strip()
                cleaned = cleaned.replace('at  ', 'at ').replace('at [', '[').strip()
                if cleaned != val:
                    updates[field] = cleaned
                    fixed_desc += 1

        if updates:
            set_clause = ', '.join(f"{k} = ?" for k in updates)
            values = list(updates.values()) + [pn]
            conn.execute(f"UPDATE permits SET {set_clause} WHERE permit_number = ?", values)

    conn.commit()
    # V12.60: Do NOT close thread-local SQLite connection — it poisons the pool
    print(f"[V12.57] Fixed {fixed_addr} addresses, {fixed_desc} descriptions.", flush=True)


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
    include_scrapers = data.get('include_scrapers', False)

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
        # Background thread for full/filtered collection
        def run_collection():
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

        thread = threading.Thread(target=run_collection, daemon=True)
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

    def run_collection():
        try:
            from collector import collect_full
            print("[Admin] Starting FULL collection (rebuild mode)...")
            collect_full(days_back=365)
            print("[Admin] Full collection complete.")
        except Exception as e:
            print(f"[Admin] Full collection error: {e}")

    thread = threading.Thread(target=run_collection, daemon=True)
    thread.start()

    return jsonify({
        'message': 'FULL collection started (rebuild mode)',
        'note': 'V12.50: Rebuilds SQLite database. Takes 30-60 minutes.'
    })


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

    def run_collection():
        try:
            from collector import collect_single_source
            print(f"[Admin] Adding single source: {source_key} ({source_type})...")
            collect_single_source(source_key, source_type)
            print(f"[Admin] Source {source_key} added successfully.")
        except Exception as e:
            print(f"[Admin] Add source error: {e}")

    thread = threading.Thread(target=run_collection, daemon=True)
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


@app.route('/api/admin/enable-postgres', methods=['POST'])
def admin_enable_postgres():
    """V70: Manually enable Postgres pool after server is confirmed stable."""
    valid, error = check_admin_key()
    if not valid:
        return error

    try:
        from db_engine import enable_pg_pool, is_pg_pool_enabled
        if is_pg_pool_enabled():
            return jsonify({'status': 'already_enabled', 'message': 'Postgres pool already active'}), 200

        success = enable_pg_pool()
        if success:
            return jsonify({'status': 'enabled', 'message': 'Postgres pool created'}), 200
        else:
            return jsonify({'status': 'failed', 'message': 'Failed to create pool - check logs'}), 500
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/admin/pg-status')
def admin_pg_status():
    """V70: Check if Postgres pool is active."""
    valid, error = check_admin_key()
    if not valid:
        return error

    try:
        from db_engine import _pg_pool, _pg_pool_enabled, is_pg_pool_enabled
        return jsonify({
            'pool_enabled': is_pg_pool_enabled(),
            'pool_exists': _pg_pool is not None,
            '_pg_pool_enabled_flag': _pg_pool_enabled,
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


@app.route('/api/admin/city-health')
def admin_city_health():
    """V35: City health report with last permit dates for proactive monitoring.
    Returns every prod_city with status, permit count, and last permit date.
    """
    valid, error = check_admin_key()
    if not valid:
        return error

    try:
        conn = permitdb.get_connection()
        # Get last permit date per city
        dates = conn.execute("""
            SELECT LOWER(city) as city_lower, state, MAX(filing_date) as last_date,
                   COUNT(*) as permit_count
            FROM permits
            WHERE filing_date IS NOT NULL AND filing_date != ''
            GROUP BY LOWER(city), state
        """).fetchall()
        date_map = {}
        for r in dates:
            date_map[(r['city_lower'], r['state'])] = {
                'last_date': r['last_date'],
                'permit_count': r['permit_count']
            }

        prod = conn.execute(
            "SELECT city, state, status, total_permits, source_id FROM prod_cities ORDER BY city"
        ).fetchall()

        cities = []
        stale_count = 0
        for r in prod:
            key = (r['city'].lower(), r['state'])
            info = date_map.get(key, {})
            last_date = info.get('last_date', '')
            # Flag as stale if last permit is >30 days old
            stale = False
            if last_date and len(str(last_date)) >= 10:
                from datetime import datetime, timedelta
                try:
                    ld = datetime.strptime(str(last_date)[:10], '%Y-%m-%d')
                    if ld < datetime.now() - timedelta(days=30):
                        stale = True
                        stale_count += 1
                except:
                    pass

            cities.append({
                'city': r['city'],
                'state': r['state'],
                'status': r['status'],
                'total_permits': r['total_permits'],
                'source_id': r['source_id'],
                'last_permit_date': str(last_date)[:10] if last_date else None,
                'stale': stale
            })

        return jsonify({
            'total_cities': len(cities),
            'stale_count': stale_count,
            'cities': cities
        })
    except Exception as e:
        return jsonify({'error': f'Health check failed: {str(e)}'}), 500


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
        city_key = data.get('city_key')
        if not city_key:
            return jsonify({'error': 'city_key is required'}), 400

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
        try:
            from accela_scraper import fetch_accela
            _accela_available = True
        except ImportError:
            _accela_available = False
        platform = config.get('platform', 'socrata')
        test_config = dict(config)
        test_config['limit'] = 5  # Just 5 records for testing

        test_config['limit'] = 10  # Small sample for freshness check
        try:
            # Test with 30-day window — if there's no data in the last 30 days,
            # the source is stale and not worth activating for leads
            if platform == 'socrata':
                test_raw = fetch_socrata(test_config, 30)
            elif platform == 'arcgis':
                test_raw = fetch_arcgis(test_config, 30)
            elif platform == 'ckan':
                test_raw = fetch_ckan(test_config, 30)
            elif platform == 'carto':
                test_raw = fetch_carto(test_config, 30)
            elif platform == 'accela':
                if not _accela_available:
                    return jsonify({'error': 'Accela scraper not available (Playwright not installed)'}), 400
                test_raw = fetch_accela(test_config, 30)
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
                'message': f'{city_key} has no data in the last 30 days. Stale source — do NOT activate.'
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
                raw = fetch_accela(config, days_back)
        except Exception as e:
            return jsonify({
                'status': 'FAILED',
                'step': 'backfill_fetch',
                'error': str(e),
                'test_passed': True,
                'test_records': len(test_raw),
            }), 500

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
        inserted = permitdb.upsert_permits(normalized, source_city_key=city_key)

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
        sql = data.get('sql', '').strip()
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


@app.route('/api/admin/fix-states', methods=['POST'])
def admin_fix_states():
    """V34b: Targeted state fix — fix specific city+wrong_state → correct_state.
    Body: {"fixes": [["city_name", "wrong_state", "correct_state"], ...]}
    Or use {"auto": true} to fix all known misattributions.
    """
    valid, error = check_admin_key()
    if not valid:
        return error

    try:
        data = request.get_json() or {}
        conn = permitdb.get_connection()
        total_fixed = 0
        details = []

        if data.get('auto'):
            # Auto-fix: use prod_cities state as truth for all permits
            prod_rows = conn.execute(
                "SELECT city, state FROM prod_cities WHERE state IS NOT NULL AND state != ''"
            ).fetchall()
            for row in prod_rows:
                city = row['city']
                correct_state = row['state']
                # Fix exact match and LOWER match
                result = conn.execute(
                    "UPDATE permits SET state = ? WHERE (city = ? OR LOWER(city) = LOWER(?)) AND state != ?",
                    (correct_state, city, city, correct_state)
                )
                if result.rowcount > 0:
                    details.append(f"{city} → {correct_state}: {result.rowcount} fixed")
                    total_fixed += result.rowcount
            conn.commit()
        else:
            fixes = data.get('fixes', [])
            for city, wrong_state, correct_state in fixes:
                result = conn.execute(
                    "UPDATE permits SET state = ? WHERE LOWER(city) = LOWER(?) AND state = ?",
                    (correct_state, city, wrong_state)
                )
                if result.rowcount > 0:
                    details.append(f"{city} {wrong_state}→{correct_state}: {result.rowcount}")
                    total_fixed += result.rowcount
            conn.commit()

        return jsonify({'total_fixed': total_fixed, 'details': details})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/fix-prod-cities', methods=['POST'])
def admin_fix_prod_cities():
    """V34b: Fix prod_cities table — clean city names, remove state from city names."""
    valid, error = check_admin_key()
    if not valid:
        return error

    try:
        conn = permitdb.get_connection()
        fixes = []

        # Fix city names that have state appended (e.g., "Norfolk Va" → "Norfolk")
        rows = conn.execute("SELECT id, city, state FROM prod_cities").fetchall()
        for row in rows:
            city = row['city']
            state = row['state'] or ''
            original = city

            # Remove state abbreviation from end (e.g., "Norfolk Va" → "Norfolk")
            import re
            # Match " XX" or " Xx" at end where XX matches state
            if state and len(state) == 2:
                pattern = rf'\s+{re.escape(state)}$'
                cleaned = re.sub(pattern, '', city, flags=re.IGNORECASE)
                if cleaned != city:
                    city = cleaned

            # Fix "Prince George'South County Md" → "Prince George's County"
            city = city.replace("George'South", "George's")

            # Fix "Saint." → "St."
            city = city.replace("Saint.", "St.")

            # Fix "Little Rock Ar Metro" → "Little Rock"
            city = re.sub(r'\s+(Metro|Area|Region)$', '', city, flags=re.IGNORECASE)

            # Fix double state references
            if city != original:
                conn.execute("UPDATE prod_cities SET city = ? WHERE id = ?", (city, row['id']))
                fixes.append(f"{original} → {city}")

        conn.commit()

        # Also sync the counts after name fixes
        permitdb._sync_prod_city_counts(conn)

        return jsonify({'fixes': fixes, 'count': len(fixes)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ===========================
# V12.53: ADMIN EMAIL ENDPOINTS
# ===========================

@app.route('/api/admin/send-digest', methods=['POST'])
@app.route('/api/admin/test-digest', methods=['POST'])  # V12.58: Route alias
def admin_send_digest():
    """V12.53: Manually trigger daily digest for testing."""
    valid, error = check_admin_key()
    if not valid:
        return error

    # V12.58: Read email from both query string and request body
    email = request.args.get('email') or (request.json or {}).get('email', '')

    try:
        from email_alerts import send_daily_digest, send_test_digest
        if email:
            # Send to specific email for testing
            result = send_test_digest(email)
            return jsonify({'status': 'sent', 'to': email, 'result': result})
        else:
            # Send to all subscribers
            sent, failed = send_daily_digest()
            return jsonify({'status': 'done', 'sent': sent, 'failed': failed})
    except Exception as e:
        return jsonify({'error': f'Digest failed: {str(e)}'}), 500


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
    """V64: Check email system health — SMTP, subscribers file, active count."""
    try:
        from email_alerts import SMTP_PASS, SUBSCRIBERS_FILE, load_subscribers
        subs = load_subscribers()
        return jsonify({
            'smtp_configured': bool(SMTP_PASS),
            'subscribers_file': str(SUBSCRIBERS_FILE),
            'subscribers_file_exists': SUBSCRIBERS_FILE.exists(),
            'active_subscribers': len(subs),
            'subscriber_emails': [s.get('email', '?')[:3] + '***' for s in subs]
        })
    except Exception as e:
        return jsonify({'error': f'Email status check failed: {str(e)}'}), 500


@app.route('/api/admin/sync-registry', methods=['POST'])
def admin_sync_registry():
    """V64: Manually sync CITY_REGISTRY to city_sources and prod_cities."""
    valid, error = check_admin_key()
    if not valid:
        return error
    try:
        from collector import sync_city_registry_to_prod
        sources, cities = sync_city_registry_to_prod()
        return jsonify({
            'sources_synced': sources,
            'new_cities_added': cities
        })
    except Exception as e:
        return jsonify({'error': f'Sync failed: {str(e)}'}), 500


# ===========================
# V12.54: AUTONOMY ENGINE ADMIN ROUTES
# ===========================

@app.route('/api/admin/autonomy-status')
def admin_autonomy_status():
    """V12.54: Get autonomy engine status."""
    admin_key = request.headers.get('X-Admin-Key') or request.args.get('key')
    expected = os.environ.get('ADMIN_KEY', 'permitgrab-reset-2026')
    if admin_key != expected:
        return jsonify({"error": "unauthorized"}), 401
    try:
        from city_source_db import get_autonomy_status
        return jsonify(get_autonomy_status())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


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


# Create tables on startup
with app.app_context():
    db.create_all()

    # V12.57: Auto-migrate missing columns — db.create_all() only creates new tables,
    # it won't add columns to existing tables. This fixes the daily digest crash
    # caused by users.watched_competitors not existing in Postgres.
    migration_columns = [
        ("watched_competitors", "TEXT DEFAULT '[]'"),
        ("digest_cities", "TEXT DEFAULT '[]'"),
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
    default_limits=["200 per day", "50 per hour"],
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
    """V57: Authoritative sync — prod_cities mirrors CITY_REGISTRY exactly.

    Rules:
    - If a city_key is active in CITY_REGISTRY → it MUST be active in prod_cities
    - If a city_key is NOT active in CITY_REGISTRY → it MUST NOT be in prod_cities
    - source_type is always updated to match the current config
    - Orphan rows (source_id not in ANY CITY_REGISTRY key) are deleted
    - No more 'paused' status — you pull or you don't exist in the tracker

    V66: Fixed connection leak — connection is now properly released in finally block.
    """
    from city_configs import CITY_REGISTRY

    added = 0
    updated = 0
    deleted = 0
    conn = None  # V66: Initialize to None for finally block

    try:
        conn = permitdb.get_connection()

        # Step 1: Build the set of active city_keys from CITY_REGISTRY
        active_keys = set()
        for city_key, config in CITY_REGISTRY.items():
            if config.get('active', False):
                active_keys.add(city_key)

        # Step 2: Get all existing prod_cities rows
        existing_by_source = {}
        existing_by_slug = {}
        for row in conn.execute("SELECT id, city_slug, source_id, source_type, status FROM prod_cities"):
            existing_by_source[row['source_id']] = row
            existing_by_slug[row['city_slug']] = row

        # Step 3: Upsert active configs into prod_cities
        for city_key in active_keys:
            config = CITY_REGISTRY[city_key]
            city_name = config.get('name', '')
            state = config.get('state', '')
            platform = config.get('platform', '')
            slug = config.get('slug', city_name.lower().replace(' ', '-'))

            if not city_name or not state:
                continue

            try:
                normalized_slug = permitdb.normalize_city_slug(city_name)
            except Exception:
                normalized_slug = slug

            if city_key in existing_by_source:
                # Exists with matching source_id — update source_type and ensure active
                row = existing_by_source[city_key]
                if row['status'] != 'active' or row['source_type'] != platform:
                    conn.execute("""
                        UPDATE prod_cities SET status = 'active',
                            source_type = ?,
                            notes = 'V57: Synced — active in CITY_REGISTRY'
                        WHERE source_id = ?
                    """, (platform, city_key))
                    updated += 1
                    print(f"  [V57] Updated {city_key} -> active, source_type={platform}")
                # else: already active with correct source_type, skip

            elif normalized_slug in existing_by_slug:
                # Slug exists but under a different source_id — update the row to point to new source
                old_row = existing_by_slug[normalized_slug]
                conn.execute("""
                    UPDATE prod_cities SET source_id = ?, source_type = ?,
                        status = 'active',
                        notes = ?
                    WHERE id = ?
                """, (city_key, platform, f'V57: Updated source from {old_row["source_id"]} to {city_key}', old_row['id']))
                updated += 1
                print(f"  [V57] Repointed {normalized_slug}: {old_row['source_id']} -> {city_key}")

            else:
                # Brand new city — insert
                try:
                    permitdb.upsert_prod_city(
                        city=city_name,
                        state=state,
                        city_slug=normalized_slug,
                        source_type=platform,
                        source_id=city_key,
                        source_scope='city',
                        status='active',
                        added_by='v57_sync',
                        notes='V57: Auto-synced from CITY_REGISTRY'
                    )
                    added += 1
                    print(f"  [V57] Added {city_key} ({city_name}, {state})")
                except Exception as e:
                    print(f"  [V57] Error adding {city_key}: {e}")

        # Step 4: Delete any prod_cities row whose source_id is NOT an active key
        # This removes: paused cities, orphans, and deactivated configs
        rows_to_delete = conn.execute(
            "SELECT id, city_slug, source_id, status FROM prod_cities"
        ).fetchall()

        for row in rows_to_delete:
            if row['source_id'] not in active_keys:
                conn.execute("DELETE FROM prod_cities WHERE id = ?", (row['id'],))
                deleted += 1
                print(f"  [V57] Deleted {row['city_slug']} (source_id={row['source_id']}, was {row['status']})")

        conn.commit()
        print(f"[V57] Sync complete: {added} added, {updated} updated, {deleted} deleted. "
              f"Active keys in CITY_REGISTRY: {len(active_keys)}")

    except Exception as e:
        print(f"[V57] Sync error: {e}")
        import traceback
        traceback.print_exc()
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
    finally:
        # V66: Always release connection back to pool
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
def classify_trade(text):
    """Classify a permit into a trade category based on description text."""
    if not text:
        return "General Construction"

    text_lower = text.lower()
    scores = {}

    for trade, keywords in TRADE_CATEGORIES.items():
        score = sum(1 for kw in keywords if kw in text_lower)
        if score > 0:
            scores[trade] = score

    if not scores:
        return "General Construction"

    # Priority order for ties
    priority_trades = [
        "Electrical", "Plumbing", "HVAC", "Roofing", "Solar", "Fire Protection",
        "Demolition", "Signage", "Windows & Doors", "Structural",
        "Interior Renovation", "Landscaping & Exterior",
        "New Construction", "Addition", "General Construction"
    ]

    specific_matches = {t: s for t, s in scores.items() if t != "General Construction"}

    if specific_matches:
        max_score = max(specific_matches.values())
        top_matches = [t for t, s in specific_matches.items() if s == max_score]

        if len(top_matches) == 1:
            return top_matches[0]

        for trade in priority_trades:
            if trade in top_matches:
                return trade

        return top_matches[0]

    return "General Construction"


def reclassify_permit(permit):
    """Re-classify a permit's trade category based on its description and type fields."""
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

    # V31: City count = only actively pulled cities (not historical bulk data)
    city_count = get_total_city_count_auto()

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
                          city_count=city_count, all_dropdown_cities=all_dropdown_cities,
                          initial_stats=initial_stats)


# V9 Fix 9: /dashboard redirects to homepage (V13.7: redirect to login if not authenticated)
@app.route('/dashboard')
def dashboard_redirect():
    """Redirect /dashboard to / for authenticated users, /login for unauthenticated."""
    if 'user_email' not in session:
        return redirect('/login?redirect=dashboard&message=login_required')
    return redirect('/')


# V10 Fix 5: /alerts redirects to account page
@app.route('/alerts')
def alerts_redirect():
    """V30: Redirect to appropriate alerts page based on login status."""
    user = get_current_user()
    if user:
        return redirect('/account')
    return redirect('/get-alerts')


@app.route('/health')
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
            'timestamp': datetime.now().isoformat(),
            'message': f'DB temporarily unavailable: {str(e)[:100]}',
            'data_loaded': _initial_data_loaded
        }), 200

    if permit_count == 0 and is_data_loading():
        # No data and we're in a loading state - still return 200 but indicate loading
        return jsonify({
            'status': 'loading',
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
    # Parse filters
    city = request.args.get('city', '')
    trade = request.args.get('trade', '')
    value = request.args.get('value', '')
    status_filter = request.args.get('status', '')
    quality = request.args.get('quality', '')
    search = request.args.get('search', '')
    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 50))

    # V32: Resolve city slug to name and state for cross-state filtering
    city_name = None
    city_state = None
    if city:
        city_key, city_config = get_city_by_slug(city)
        if city_config:
            city_name = city_config.get('name', city)
            city_state = city_config.get('state', '')
        else:
            city_name = city  # Use as-is if not a valid slug

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
        'last_updated': collection_stats.get('collected_at', ''),
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
    """GET /api/cities - Get all active cities with info."""
    return jsonify({
        'count': get_city_count(),
        'cities': get_all_cities_info(),
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
    city = data.get('city', '').strip().title()  # V12.64: Normalize to titlecase
    trade = data.get('trade', '')

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
        db.session.commit()

        return jsonify({
            'message': f'Updated digest settings for {email}',
            'subscriber': {'email': email, 'city': city, 'trade': trade},
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
        'subscriber': {'email': email, 'city': city, 'trade': trade},
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

@app.route('/api/saved-searches', methods=['GET'])
def get_saved_searches():
    """GET /api/saved-searches - Get user's saved searches."""
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Login required'}), 401

    searches = get_user_saved_searches(user['email'])
    return jsonify({'searches': searches})

@app.route('/api/saved-searches', methods=['POST'])
def create_saved_search():
    """POST /api/saved-searches - Create a new saved search.

    PRO FEATURE: Only Pro users can save searches.
    """
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Login required'}), 401

    # Save Search is a Pro feature
    if not is_pro(user):
        return jsonify({
            'error': 'Save Search is a Pro feature',
            'upgrade_url': '/pricing'
        }), 403

    data = request.get_json()
    if not data:
        return jsonify({'error': 'Missing search data'}), 400

    # Create the saved search object
    search = {
        'id': str(uuid.uuid4()),
        'user_email': user['email'],
        'city': data.get('city', ''),
        'trade': data.get('trade', ''),
        'value_tier': data.get('value_tier', ''),
        'status': data.get('status', ''),
        'quality': data.get('quality', ''),
        'search_text': data.get('search_text', ''),
        'daily_alerts': True,  # Default to daily alerts enabled
        'created_at': datetime.now().isoformat(),
    }

    # Build a human-readable name for the search
    parts = []
    if search['city']:
        parts.append(search['city'])
    if search['trade']:
        parts.append(search['trade'])
    if search['value_tier']:
        parts.append(f"Value: {search['value_tier']}")
    search['name'] = ' | '.join(parts) if parts else 'All Permits'

    all_searches = load_saved_searches()
    all_searches.append(search)
    save_saved_searches(all_searches)

    return jsonify({'message': 'Search saved', 'search': search})

@app.route('/api/saved-searches/<search_id>', methods=['DELETE'])
def delete_saved_search(search_id):
    """DELETE /api/saved-searches/<id> - Delete a saved search."""
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Login required'}), 401

    all_searches = load_saved_searches()
    original_count = len(all_searches)
    all_searches = [s for s in all_searches if not (s['user_email'] == user['email'] and s['id'] == search_id)]

    if len(all_searches) == original_count:
        return jsonify({'error': 'Search not found'}), 404

    save_saved_searches(all_searches)
    return jsonify({'message': 'Search deleted'})

@app.route('/api/saved-searches/<search_id>', methods=['PUT'])
def update_saved_search(search_id):
    """PUT /api/saved-searches/<id> - Update a saved search (e.g., toggle alerts)."""
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Login required'}), 401

    data = request.get_json()
    all_searches = load_saved_searches()

    for search in all_searches:
        if search['id'] == search_id and search['user_email'] == user['email']:
            if 'daily_alerts' in data:
                search['daily_alerts'] = data['daily_alerts']
            if 'name' in data:
                search['name'] = data['name']
            save_saved_searches(all_searches)
            return jsonify({'message': 'Search updated', 'search': search})

    return jsonify({'error': 'Search not found'}), 404


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


# ===========================
# CODE VIOLATIONS API
# ===========================

@app.route('/api/violations')
def api_violations():
    """
    GET /api/violations
    Query params: city
    Returns recent code violations, flagged as pre-leads if no matching permit.
    """
    violations = load_violations()
    # V12.51: SQL-backed permits
    permits, _ = permitdb.query_permits(page=1, per_page=100000)

    city = request.args.get('city', '')

    if city:
        violations = [v for v in violations if v.get('city') == city]

    # Build set of permit addresses for cross-reference
    permit_addresses = set()
    for p in permits:
        addr = normalize_address_for_lookup(p.get('address', ''))
        if addr:
            permit_addresses.add(addr)

    # Mark violations as pre-leads if no matching permit
    for v in violations:
        v_addr = normalize_address_for_lookup(v.get('address', ''))
        v['has_matching_permit'] = v_addr in permit_addresses
        v['is_pre_lead'] = not v['has_matching_permit']

    # Sort: pre-leads first, then by date
    violations.sort(key=lambda x: (not x.get('is_pre_lead', False), x.get('violation_date', '') or ''), reverse=True)

    # Stats
    pre_lead_count = sum(1 for v in violations if v.get('is_pre_lead'))
    cities = sorted(set(v.get('city', '') for v in load_violations() if v.get('city')))

    return jsonify({
        'violations': violations[:200],  # Limit response size
        'total': len(violations),
        'pre_lead_count': pre_lead_count,
        'cities': cities,
    })


@app.route('/api/violations/<path:address>')
def api_violations_by_address(address):
    """
    GET /api/violations/<address>
    Returns violations at a specific address.
    """
    violations = load_violations()
    normalized_addr = normalize_address_for_lookup(address)

    if not normalized_addr:
        return jsonify({'violations': [], 'count': 0})

    # Find violations at this address
    matching = []
    for v in violations:
        v_addr = normalize_address_for_lookup(v.get('address', ''))
        if normalized_addr == v_addr or normalized_addr in v_addr or v_addr in normalized_addr:
            matching.append(v)

    return jsonify({
        'violations': matching,
        'count': len(matching),
        'has_active_violations': any(v.get('status', '').lower() in ('open', 'active', 'pending') for v in matching),
    })


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


@app.route('/contractors')
def contractors_page():
    """Render the Contractors Intelligence page."""
    footer_cities = get_cities_with_data()
    return render_template('contractors.html', footer_cities=footer_cities)


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
    """Create a Stripe Checkout Session for Professional plan (monthly or annual)."""
    if not STRIPE_SECRET_KEY or not STRIPE_PRICE_ID:
        return jsonify({'error': 'Stripe not configured'}), 500

    stripe.api_key = STRIPE_SECRET_KEY

    data = request.get_json() or {}
    customer_email = data.get('email')
    billing_period = data.get('billing_period', 'monthly')

    # Choose the correct price based on billing period
    if billing_period == 'annual' and STRIPE_ANNUAL_PRICE_ID:
        price_id = STRIPE_ANNUAL_PRICE_ID
        plan_name = 'professional_annual'
    else:
        price_id = STRIPE_PRICE_ID
        plan_name = 'professional_monthly'

    # Track checkout started event
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
            success_url=f'{SITE_URL}/?payment=success',
            cancel_url=f'{SITE_URL}/?payment=cancelled',
            customer_email=customer_email,
            metadata={
                'plan': plan_name,
                'billing_period': billing_period,
            },
        )
        return jsonify({'url': checkout_session.url})
    except stripe.error.StripeError as e:
        return jsonify({'error': str(e)}), 400


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
    print(f"[Stripe] Received event: {event_type}")

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

@app.route('/admin')
def admin_page():
    """GET /admin - Admin dashboard (password protected)."""
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
        subscribers=subscribers,
        total_subscribers=len(digest_subscribers),
        diagnostic=diagnostic,
        success_msg=request.args.get('success', ''),
        error_msg=request.args.get('error', ''),
    )


# Handle admin logout
@app.before_request
def check_admin_logout():
    if request.path == '/admin' and request.args.get('logout'):
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
        "meta_title": "Miami Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Miami-Dade County. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
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


def city_landing_inner(city_slug):
    """Render SEO-optimized city landing page."""
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
                        canonical_url=f"{SITE_URL}/permits/{city_slug}",
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
        config = CITY_SEO_CONFIG[city_slug]
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

    # V12.51: Use SQLite for city permits
    filter_name = config.get('raw_name', config['name'])
    filter_state = config.get('state', '')
    conn = permitdb.get_connection()

    # V32: Add state filter to prevent cross-state data pollution
    # (e.g., Newark NJ showing Oklahoma permits)
    if filter_state:
        state_clause = " AND state = ?"
        state_params = (filter_name, filter_state)
    else:
        state_clause = ""
        state_params = (filter_name,)

    # Get stats via SQL aggregation
    stats_row = conn.execute(f"""
        SELECT COUNT(*) as permit_count,
               COALESCE(SUM(estimated_cost), 0) as total_value,
               COUNT(CASE WHEN estimated_cost >= 100000 THEN 1 END) as high_value_count
        FROM permits WHERE city = ?{state_clause}
    """, state_params).fetchone()

    permit_count = stats_row['permit_count']
    total_value = stats_row['total_value']
    # V12.18: High-value = $100K+ projects (more meaningful to contractors)
    high_value_count = stats_row['high_value_count']

    # Get permits for display (limited set, sorted by value)
    cursor = conn.execute(f"""
        SELECT * FROM permits WHERE city = ?{state_clause} ORDER BY estimated_cost DESC LIMIT 100
    """, state_params)
    city_permits = [dict(row) for row in cursor]

    # V15: noindex for non-prod cities or empty cities
    # If prod_cities table is active and this city isn't in it, treat as coming soon
    if permitdb.prod_cities_table_exists() and not is_prod_city:
        robots_directive = "noindex, follow"
        is_coming_soon = True
    else:
        # V12.5: noindex for empty city pages to avoid thin content in Google
        robots_directive = "noindex, follow" if permit_count == 0 else "index, follow"
        # V12.11: Coming Soon flag for empty cities
        is_coming_soon = permit_count == 0

    # V12.51: Trade breakdown via SQL for full accuracy
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
    nearby_cities = [c for c in cities_by_volume if c.get('state') == current_state and c['slug'] != city_slug]
    # If fewer than 6 same-state cities, add top cities from other states
    if len(nearby_cities) < 6:
        other_state_cities = [c for c in cities_by_volume if c.get('state') != current_state][:6 - len(nearby_cities)]
        nearby_cities = nearby_cities + other_state_cities

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

    return render_template(
        'city_landing.html',
        city_name=config['name'],
        city_slug=city_slug,
        city_state=current_state,
        meta_title=config['meta_title'],
        meta_description=config['meta_description'],
        seo_content=config['seo_content'],
        canonical_url=f"{SITE_URL}/permits/{city_slug}",
        robots_directive=robots_directive,  # V12.5: noindex empty pages
        permit_count=permit_count,
        total_value=total_value,
        high_value_count=high_value_count,
        new_this_month=new_this_month,
        unique_contractors=unique_contractors,
        trade_breakdown=trade_breakdown,
        permits=sorted_permits,
        other_cities=other_cities,
        nearby_cities=nearby_cities,  # V12.11: Same-state cities for internal linking
        current_year=datetime.now().year,
        current_date=datetime.now().strftime('%Y-%m-%d'),
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

    monthly_count = len([p for p in matching_permits if p.get('filing_date', '') >= month_ago])
    weekly_count = len([p for p in matching_permits if p.get('filing_date', '') >= week_ago])

    values = [p.get('estimated_cost', 0) for p in matching_permits if p.get('estimated_cost')]
    avg_value = int(sum(values) / len(values)) if values else 0

    # Build city dict for template with formatted name
    city_dict = {
        "name": display_name,
        "state": city_config['state'],
        "slug": city_slug,
    }

    stats = {
        "monthly_count": monthly_count or len(matching_permits),
        "weekly_count": weekly_count,
        "avg_value": f"{avg_value:,}" if avg_value else "N/A",
    }

    # Other trades for cross-linking (exclude current)
    other_trades = [t for t in get_all_trades() if t['slug'] != trade_slug]

    # Other cities for cross-linking (exclude current)
    other_cities = [{"name": c['name'], "slug": c['slug']} for c in ALL_CITIES if c['slug'] != city_slug]

    # V30: noindex for thin trade pages (fewer than 5 trade-specific permits, or fallback mode)
    robots_directive = "noindex, follow" if (trade_fallback or len(matching_permits) < 5) else "index, follow"

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
    """
    all_cities = get_cities_with_data()
    footer_cities = all_cities

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
    """V28: Sitemap for city pages and state hub pages."""
    today = datetime.now().strftime('%Y-%m-%d')
    url_map = {}

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

    # City pages from discovered cities
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
    """V28: Sitemap for blog posts."""
    today = datetime.now().strftime('%Y-%m-%d')
    urls = []

    blog_dir = os.path.join(os.path.dirname(__file__), 'blog')
    if os.path.exists(blog_dir):
        for filename in os.listdir(blog_dir):
            if filename.endswith('.md'):
                slug = filename.replace('.md', '')
                urls.append({
                    'loc': f"{SITE_URL}/blog/{slug}",
                    'changefreq': 'monthly',
                    'priority': '0.6',
                    'lastmod': today
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


@app.route('/blog')
def blog_index():
    """Blog index page. V14.0: Added pagination."""
    all_posts = get_all_blog_posts()

    # V14.0: Pagination
    page = request.args.get('page', 1, type=int)
    per_page = 20
    total_posts = len(all_posts)
    total_pages = math.ceil(total_posts / per_page) if total_posts > 0 else 1

    # Ensure page is valid
    if page < 1:
        page = 1
    elif page > total_pages:
        page = total_pages

    # Slice posts for current page
    start_idx = (page - 1) * per_page
    end_idx = start_idx + per_page
    posts = all_posts[start_idx:end_idx]

    # V29: Build prev/next URLs for rel="prev"/rel="next" pagination SEO
    prev_url = f"/blog?page={page - 1}" if page > 1 else None
    next_url = f"/blog?page={page + 1}" if page < total_pages else None

    return render_template('blog_index.html',
                           posts=posts,
                           page=page,
                           total_pages=total_pages,
                           total_posts=total_posts,
                           per_page=per_page,
                           prev_url=prev_url,
                           next_url=next_url)


@app.route('/blog/<slug>')
def blog_post(slug):
    """Individual blog post page."""
    post = parse_blog_post(f"{slug}.md")
    if not post:
        # V12.60: Use branded 404 instead of bare string
        footer_cities = get_cities_with_data()
        return render_template('404.html', footer_cities=footer_cities), 404
    return render_template('blog_post.html', post=post)


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
        print(f"[{datetime.now()}] V12.50: Starting scheduled collection cycle...")

        # V13.3: Each task has its own try/except so one failure doesn't block others
        # Permit collection
        try:
            from collector import collect_refresh, collect_permit_history
            collect_refresh(days_back=7)
            print(f"[{datetime.now()}] Refresh collection complete.")
        except Exception as e:
            print(f"[{datetime.now()}] Permit collection error: {e}")
            import traceback
            traceback.print_exc()

        # V12.50: Prune old permits (keep last 90 days)
        try:
            deleted = permitdb.delete_old_permits(days=90)
            if deleted > 0:
                print(f"[{datetime.now()}] Pruned {deleted} old permits.")
        except Exception as e:
            print(f"[{datetime.now()}] Prune error: {e}")

        # Violation collection (daily)
        try:
            from violation_collector import collect_all_violations
            collect_all_violations(days_back=90)
            print(f"[{datetime.now()}] Violation collection complete.")
        except Exception as e:
            print(f"[{datetime.now()}] Violation collection error: {e}")

        # Signal collection (daily)
        try:
            from signal_collector import collect_all_signals
            collect_all_signals(days_back=90)
            print(f"[{datetime.now()}] Signal collection complete.")
        except Exception as e:
            print(f"[{datetime.now()}] Signal collection error: {e}")

        # Permit history collection (weekly or first run)
        now = datetime.now()
        if last_history_run is None or (now - last_history_run).days >= 7:
            try:
                collect_permit_history(years_back=1)
                last_history_run = now
                print(f"[{datetime.now()}] Permit history collection complete.")
            except Exception as e:
                print(f"[{datetime.now()}] Permit history collection error: {e}")

        # City health check (daily)
        try:
            from city_health import check_all_cities
            check_all_cities()
            print(f"[{datetime.now()}] City health check complete.")
        except Exception as e:
            print(f"[{datetime.now()}] City health check error: {e}")

        # V17: Auto-discovery pipeline (daily)
        # 1. Discover new sources (Socrata + ArcGIS)
        # 2. Auto-test and activate pending cities
        # 3. Send SEO notification email for new activations
        if permitdb.should_run_daily('discovery'):
            print(f"[{datetime.now()}] V17b: Starting accelerated discovery pipeline...")

            total_new_sources = 0

            # V17b: Use accelerated parallel discovery (Socrata + ArcGIS combined)
            try:
                from auto_discover import run_accelerated_discovery
                total_new_sources = run_accelerated_discovery(max_results=300, max_workers=5)
                print(f"[{datetime.now()}] V17b accelerated discovery: {total_new_sources} new sources")
            except ImportError:
                # Fallback to sequential if accelerated not available
                print(f"[{datetime.now()}] V17b not available, using sequential discovery...")
                try:
                    from auto_discover import run_full_discovery, run_arcgis_bulk_discovery
                    total_new_sources += run_full_discovery(max_results=100)
                    total_new_sources += run_arcgis_bulk_discovery(max_results=100)
                except Exception as e:
                    print(f"[{datetime.now()}] Sequential discovery error: {e}")
            except Exception as e:
                print(f"[{datetime.now()}] V17b discovery error: {e}")

            # Auto-test pending cities and activate the good ones
            try:
                from collector import activate_pending_cities
                activated, failed = activate_pending_cities()
                print(f"[{datetime.now()}] Pending cities: {len(activated)} activated, {len(failed)} failed")

                # Send SEO notification if new cities activated
                if activated:
                    try:
                        from email_alerts import send_new_cities_alert
                        send_new_cities_alert(activated)
                        print(f"[{datetime.now()}] SEO notification sent for {len(activated)} cities")
                    except Exception as e:
                        print(f"[{datetime.now()}] SEO notification error: {e}")
            except Exception as e:
                print(f"[{datetime.now()}] Pending activation error: {e}")

            permitdb.mark_daily_complete('discovery')
            print(f"[{datetime.now()}] V17 discovery pipeline complete: {total_new_sources} new sources")

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

        # V12.50: Sleep 6 hours (reduced from 24 — deltas are lightweight)
        time.sleep(21600)


# ===========================
# V12.53: EMAIL SCHEDULER
# ===========================

def schedule_email_tasks():
    """V12.53: Schedule all email tasks to run at specific times daily.

    - Daily digest: 7 AM ET (12:00 UTC)
    - Trial lifecycle check: 8 AM ET (13:00 UTC)
    - Onboarding nudges: 9 AM ET (14:00 UTC)

    V64: Added robust error logging, heartbeat, and crash recovery.
    """
    # V64: Wrap imports in try/except to catch missing dependencies
    try:
        import pytz
        from email_alerts import send_daily_digest, check_trial_lifecycle, check_onboarding_nudges
    except ImportError as e:
        print(f"[{datetime.now()}] [CRITICAL] Email scheduler failed to import: {e}")
        import traceback
        traceback.print_exc()
        return  # Don't silently die — exit with error logged

    # V68: Wait 3 minutes for initial startup (increased from 2)
    print(f"[{datetime.now()}] V68: Email scheduler waiting 3 minutes for startup...")
    time.sleep(180)

    et = pytz.timezone('America/New_York')

    while True:
        try:
            now_utc = datetime.utcnow()
            now_et = datetime.now(et)

            # V64: Heartbeat every cycle so we can verify thread is alive in Render logs
            print(f"[{datetime.now()}] V64: Email scheduler heartbeat: {now_et.strftime('%I:%M %p ET')}")

            # Check if it's time for daily tasks (7-9 AM ET window)
            if 7 <= now_et.hour <= 9:
                # Daily digest at 7 AM ET
                if now_et.hour == 7 and now_et.minute < 30:
                    print(f"[{datetime.now()}] V64: Running daily digest...")
                    try:
                        sent, failed = send_daily_digest()
                        print(f"[{datetime.now()}] V64: Daily digest complete - {sent} sent, {failed} failed")
                    except Exception as e:
                        print(f"[{datetime.now()}] [ERROR] Daily digest failed: {e}")
                        import traceback
                        traceback.print_exc()

                # Trial lifecycle at 8 AM ET
                if now_et.hour == 8 and now_et.minute < 30:
                    print(f"[{datetime.now()}] V64: Checking trial lifecycle...")
                    try:
                        results = check_trial_lifecycle()
                        print(f"[{datetime.now()}] V64: Trial lifecycle complete - {results}")
                    except Exception as e:
                        print(f"[{datetime.now()}] [ERROR] Trial lifecycle failed: {e}")
                        import traceback
                        traceback.print_exc()

                # Onboarding nudges at 9 AM ET
                if now_et.hour == 9 and now_et.minute < 30:
                    print(f"[{datetime.now()}] V64: Checking onboarding nudges...")
                    try:
                        sent = check_onboarding_nudges()
                        print(f"[{datetime.now()}] V64: Onboarding nudges complete - {sent} sent")
                    except Exception as e:
                        print(f"[{datetime.now()}] [ERROR] Onboarding nudges failed: {e}")
                        import traceback
                        traceback.print_exc()

        except Exception as e:
            print(f"[{datetime.now()}] [ERROR] Email scheduler error: {e}")
            import traceback
            traceback.print_exc()
            # V64: Wait 5 min on error before retrying, don't die
            time.sleep(300)
            continue

        # Check every 30 minutes
        time.sleep(1800)


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

        print(f"[{datetime.now()}] V12.57: Running initial REFRESH collection (7 days)...")

        # V12.57: Quick 7-day refresh instead of 365-day full rebuild
        from collector import collect_refresh
        collect_refresh(days_back=7)

        # Violation collection
        try:
            from violation_collector import collect_all_violations
            collect_all_violations(days_back=90)
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

    # V12.53: Email scheduler thread
    email_thread = threading.Thread(target=schedule_email_tasks, name='email_scheduler', daemon=True)
    email_thread.start()
    print(f"[{datetime.now()}] V67: Email scheduler thread started, waiting 30s...")
    time.sleep(30)

    # V12.55c: One-time fix for Socrata location JSON in address fields
    try:
        fix_thread = threading.Thread(target=_fix_socrata_addresses, name='socrata_fix', daemon=True)
        fix_thread.start()
        print(f"[{datetime.now()}] V67: Address cleanup thread started, waiting 30s...")
        time.sleep(30)
    except Exception as e:
        print(f"[{datetime.now()}] V12.55c: Address cleanup error: {e}")

    # V12.54: Autonomous city discovery engine
    try:
        from autonomy_engine import run_autonomy_engine
        autonomy_thread = threading.Thread(target=run_autonomy_engine, name='autonomy_engine', daemon=True)
        autonomy_thread.start()
        print(f"[{datetime.now()}] V67: Autonomy engine thread started.")
    except ImportError:
        print(f"[{datetime.now()}] V12.54: autonomy_engine.py not found, skipping.")

    print(f"[{datetime.now()}] V67: All collector threads started (staggered over ~2 minutes).")

# V12.12: Preload existing data from disk BEFORE starting collectors
# This ensures stale data is served immediately rather than showing 0 permits
preload_data_from_disk()

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
