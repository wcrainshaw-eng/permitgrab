"""V471 PR2 (CODE_V471 Part 1B): health blueprint extracted from server.py.

Routes: 4 URLs across 4 handlers.

Helpers/globals from server.py are accessed via `from server import *`,
which imports everything server.py defined before this blueprint was loaded
(blueprints are registered at the bottom of server.py, after all globals
are set). Underscored helpers are listed below explicitly because `import *`
skips names starting with `_`.
"""
from flask import Blueprint, request, jsonify, render_template, session, redirect, abort, Response, g, url_for, send_from_directory
from datetime import datetime, timedelta
import os, json, time, re, threading, random, string, hashlib, hmac
from werkzeug.security import generate_password_hash, check_password_hash

# Pull in server.py's helpers, models, and globals (server is fully loaded
# by the time this blueprint module is imported because server.py registers
# blueprints at the very end of its module body).
from server import *
# Underscored helpers / module-level state that `import *` skips:
from server import _initial_data_loaded, _last_collection_run, _startup_done
import server as _s

health_bp = Blueprint('health', __name__)


@health_bp.route('/healthz')
def healthz():
    """V167: Lightweight health check for Render's TCP probe. NO DB queries."""
    return 'ok', 200


@health_bp.route('/healthz/deep')
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


@health_bp.route('/api/diagnostics')
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


@health_bp.route('/api/health')
def health_check():
    """
    V12.51: Health check endpoint with SQLite data availability check.
    V67: Always return 200 during startup to prevent Render restart loop.
    """
    # V67: During startup, return healthy without touching DB
    # This prevents pool exhaustion from killing health checks
    if not _s._startup_done:
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
            'data_loaded': _s._initial_data_loaded
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
    if _s._last_collection_run:
        hours_since_collection = (datetime.now() - _s._last_collection_run).total_seconds() / 3600
        if hours_since_collection > 12:
            collection_status = 'stale'  # Warning: collection hasn't run recently
        else:
            collection_status = 'healthy'

    return jsonify({
        'status': 'ok',
        'version': APP_VERSION,
        'timestamp': datetime.now().isoformat(),
        'permit_count': permit_count,
        'data_loaded': _s._initial_data_loaded,
        'collection_status': collection_status,
        'last_collection_run': _s._last_collection_run.isoformat() if _s._last_collection_run else None,
        'hours_since_collection': round(hours_since_collection, 1) if hours_since_collection else None
    }), 200


