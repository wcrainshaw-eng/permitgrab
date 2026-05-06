"""V540 PR1 regression tests for city_health/.

Per Wes's V540 spec: 3+ tests per public function.

Public functions covered:
  - compute_city_health: 6 tests (pass + 4 degraded variants + fail)
  - compute_all_city_health: 3 tests
  - upsert_city_health: 3 tests
  - ensure_table: 3 tests
  - start_thread: 3 tests

Tests use an in-memory SQLite that mirrors the production schema
slice — no Flask, no Render, no network.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from unittest.mock import patch

# Module-level `from server import app` mirrors the pattern in
# tests/test_smoke.py — server.py + routes/admin.py have a circular
# import that resolves only when server is loaded as the FIRST
# importer in the chain. Moving the import inside a function (which
# I tried first) tripped:
#   ImportError: cannot import name 'admin_bp' from partially
#   initialized module 'routes.admin' (most likely due to a circular
#   import)
# because the circular dance only resolves cleanly when the import
# kicks off at module-load time, not lazily inside a test function.
try:
    from server import app as _server_app  # noqa: F401
except Exception as _e:
    # In environments without flask_login etc. the server import will
    # fail. Endpoint tests will skip themselves; the compute/upsert
    # tests above don't need server.
    _server_app = None


def _setup_db(scenarios=None):
    """Create an in-memory SQLite with the schema slice city_health
    needs, plus optional seeded scenarios. Returns the connection.

    `scenarios` is a list of dicts with keys: slug, source_type,
    permits_count, profiles_count, with_phone_count, last_run_at.
    """
    conn = sqlite3.connect(':memory:')
    conn.executescript("""
        CREATE TABLE prod_cities (
            city_slug TEXT PRIMARY KEY,
            source_type TEXT,
            status TEXT
        );
        CREATE TABLE permits (
            id INTEGER PRIMARY KEY,
            permit_number TEXT,
            source_city_key TEXT,
            collected_at TEXT
        );
        CREATE TABLE contractor_profiles (
            id INTEGER PRIMARY KEY,
            source_city_key TEXT,
            phone TEXT
        );
        CREATE TABLE scraper_runs (
            id INTEGER PRIMARY KEY,
            city_slug TEXT,
            run_started_at TEXT,
            status TEXT
        );
        CREATE TABLE city_health (
            city_slug TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            reason_code TEXT,
            reason_detail TEXT,
            evidence_json TEXT,
            computed_at TIMESTAMP
        );
        CREATE TABLE digest_log (
            id INTEGER PRIMARY KEY,
            recipient_email TEXT,
            permits_count INTEGER,
            status TEXT,
            error_message TEXT,
            sent_at TEXT
        );
    """)
    for s in (scenarios or []):
        conn.execute(
            "INSERT INTO prod_cities (city_slug, source_type, status) "
            "VALUES (?, ?, 'active')",
            (s['slug'], s.get('source_type', 'socrata')),
        )
        for i in range(s.get('permits_count', 0)):
            conn.execute(
                "INSERT INTO permits (permit_number, source_city_key) "
                "VALUES (?, ?)",
                (f"P-{s['slug']}-{i}", s['slug']),
            )
        for i in range(s.get('profiles_count', 0)):
            phone = '555-1234' if i < s.get('with_phone_count', 0) else None
            conn.execute(
                "INSERT INTO contractor_profiles (source_city_key, phone) "
                "VALUES (?, ?)",
                (s['slug'], phone),
            )
        if s.get('last_run_at') is not None:
            conn.execute(
                "INSERT INTO scraper_runs (city_slug, run_started_at, status) "
                "VALUES (?, ?, 'success')",
                (s['slug'], s['last_run_at']),
            )
    conn.commit()
    return conn


# ---------------------------------------------------------------------
# compute_city_health: rubric tests
# ---------------------------------------------------------------------

def test_city_health_pass_with_all_thresholds_met():
    """V540 spec test: permits>100 AND profiles>100 AND
    last_collection<7d AND with_phone>0 → Pass."""
    from city_health import compute_city_health, PASS
    now_str = (datetime.utcnow() - timedelta(hours=1)).strftime('%Y-%m-%d %H:%M:%S')
    conn = _setup_db([{
        'slug': 'happy-city',
        'source_type': 'socrata',
        'permits_count': 200,
        'profiles_count': 200,
        'with_phone_count': 50,
        'last_run_at': now_str,
    }])
    with patch('db.get_connection', return_value=conn), \
         patch('city_health.compute._platform_health', return_value={'status': 'pass'}):
        ch = compute_city_health('happy-city')
    assert ch.status == PASS, f'expected Pass, got {ch.status}: {ch.reason_detail}'
    assert ch.reason_code == 'all_thresholds_met'
    assert ch.evidence['permits_count'] == 200
    assert ch.evidence['profiles_count'] == 200


def test_city_health_degraded_when_phones_below_threshold():
    """V540 spec test: with_phone_count == 0 → Degraded."""
    from city_health import compute_city_health, DEGRADED
    now_str = (datetime.utcnow() - timedelta(hours=1)).strftime('%Y-%m-%d %H:%M:%S')
    conn = _setup_db([{
        'slug': 'no-phones',
        'source_type': 'socrata',
        'permits_count': 200,
        'profiles_count': 200,
        'with_phone_count': 0,
        'last_run_at': now_str,
    }])
    with patch('db.get_connection', return_value=conn), \
         patch('city_health.compute._platform_health', return_value={'status': 'pass'}):
        ch = compute_city_health('no-phones')
    assert ch.status == DEGRADED
    assert ch.reason_code == 'no_phones'


def test_city_health_degraded_when_low_permits():
    from city_health import compute_city_health, DEGRADED
    now_str = (datetime.utcnow() - timedelta(hours=1)).strftime('%Y-%m-%d %H:%M:%S')
    conn = _setup_db([{
        'slug': 'low-perm',
        'source_type': 'socrata',
        'permits_count': 50,
        'profiles_count': 200,
        'with_phone_count': 50,
        'last_run_at': now_str,
    }])
    with patch('db.get_connection', return_value=conn), \
         patch('city_health.compute._platform_health', return_value={'status': 'pass'}):
        ch = compute_city_health('low-perm')
    assert ch.status == DEGRADED
    assert ch.reason_code == 'low_permits'
    assert '50' in ch.reason_detail


def test_city_health_degraded_when_stale_collection():
    """Last collection 10 days ago → Degraded with stale_collection."""
    from city_health import compute_city_health, DEGRADED
    stale = (datetime.utcnow() - timedelta(days=10)).strftime('%Y-%m-%d %H:%M:%S')
    conn = _setup_db([{
        'slug': 'stale-city',
        'source_type': 'socrata',
        'permits_count': 200,
        'profiles_count': 200,
        'with_phone_count': 50,
        'last_run_at': stale,
    }])
    with patch('db.get_connection', return_value=conn), \
         patch('city_health.compute._platform_health', return_value={'status': 'pass'}):
        ch = compute_city_health('stale-city')
    assert ch.status == DEGRADED
    assert ch.reason_code == 'stale_collection'


def test_city_health_fail_when_source_dead_21d():
    """V540 spec test: last_collection > 21 days → Fail."""
    from city_health import compute_city_health, FAIL
    dead = (datetime.utcnow() - timedelta(days=30)).strftime('%Y-%m-%d %H:%M:%S')
    conn = _setup_db([{
        'slug': 'dead-source',
        'source_type': 'socrata',
        'permits_count': 200,
        'profiles_count': 200,
        'with_phone_count': 50,
        'last_run_at': dead,
    }])
    with patch('db.get_connection', return_value=conn):
        ch = compute_city_health('dead-source')
    assert ch.status == FAIL
    assert ch.reason_code == 'dead_source'
    assert '30d ago' in ch.reason_detail


def test_city_health_fail_when_never_visited():
    """Active city with no scraper_runs row → Fail (never_visited)."""
    from city_health import compute_city_health, FAIL
    conn = _setup_db([{
        'slug': 'never-visited',
        'source_type': 'socrata',
        'permits_count': 0,
        'profiles_count': 0,
        'with_phone_count': 0,
        'last_run_at': None,
    }])
    with patch('db.get_connection', return_value=conn):
        ch = compute_city_health('never-visited')
    assert ch.status == FAIL
    assert ch.reason_code == 'never_visited'


def test_city_health_degraded_when_unknown_platform():
    """Bulk-source recipient (NULL source_type) → Degraded."""
    from city_health import compute_city_health, DEGRADED
    conn = sqlite3.connect(':memory:')
    conn.executescript("""
        CREATE TABLE prod_cities (city_slug TEXT, source_type TEXT, status TEXT);
        CREATE TABLE permits (id INTEGER, permit_number TEXT, source_city_key TEXT, collected_at TEXT);
        CREATE TABLE contractor_profiles (id INTEGER, source_city_key TEXT, phone TEXT);
        CREATE TABLE scraper_runs (id INTEGER, city_slug TEXT, run_started_at TEXT, status TEXT);
        INSERT INTO prod_cities VALUES ('bulk-recipient', NULL, 'active');
    """)
    conn.commit()
    with patch('db.get_connection', return_value=conn):
        ch = compute_city_health('bulk-recipient')
    assert ch.status == DEGRADED
    assert ch.reason_code == 'unknown_platform'


def test_city_health_empty_slug_returns_fail():
    from city_health import compute_city_health, FAIL
    ch = compute_city_health('')
    assert ch.status == FAIL
    assert ch.reason_code == 'compute_error'


# ---------------------------------------------------------------------
# upsert_city_health
# ---------------------------------------------------------------------

def test_upsert_city_health_inserts_new_row():
    from city_health import CityHealth, upsert_city_health
    conn = _setup_db()
    ch = CityHealth(
        slug='new-city', status='Pass', reason_code='all_thresholds_met',
        reason_detail='ok', evidence={'permits_count': 200},
    )
    with patch('db.get_connection', return_value=conn):
        ok = upsert_city_health(ch)
    assert ok is True
    row = conn.execute("SELECT city_slug, status, reason_code FROM city_health").fetchone()
    assert row == ('new-city', 'Pass', 'all_thresholds_met')


def test_upsert_city_health_updates_existing_row():
    """V525b ON CONFLICT (city_slug) DO UPDATE — second upsert
    overwrites the first."""
    from city_health import CityHealth, upsert_city_health
    conn = _setup_db()
    ch1 = CityHealth(
        slug='evolving', status='Degraded', reason_code='low_permits',
        reason_detail='80 permits', evidence={'permits_count': 80},
    )
    ch2 = CityHealth(
        slug='evolving', status='Pass', reason_code='all_thresholds_met',
        reason_detail='ok', evidence={'permits_count': 200},
    )
    with patch('db.get_connection', return_value=conn):
        upsert_city_health(ch1)
        upsert_city_health(ch2)
    rows = conn.execute("SELECT status, reason_code FROM city_health").fetchall()
    assert len(rows) == 1
    assert rows[0] == ('Pass', 'all_thresholds_met')


def test_upsert_city_health_rejects_non_city_health():
    from city_health import upsert_city_health
    import pytest
    with pytest.raises(TypeError):
        upsert_city_health({'slug': 'x', 'status': 'Pass'})


# ---------------------------------------------------------------------
# compute_all_city_health
# ---------------------------------------------------------------------

def test_compute_all_city_health_iterates_active_cities():
    from city_health import compute_all_city_health
    now_str = (datetime.utcnow() - timedelta(hours=1)).strftime('%Y-%m-%d %H:%M:%S')
    conn = _setup_db([
        {'slug': 'a', 'source_type': 'socrata', 'permits_count': 200,
         'profiles_count': 200, 'with_phone_count': 50,
         'last_run_at': now_str},
        {'slug': 'b', 'source_type': 'socrata', 'permits_count': 50,
         'profiles_count': 200, 'with_phone_count': 50,
         'last_run_at': now_str},
        {'slug': 'c', 'source_type': 'socrata', 'permits_count': 0,
         'profiles_count': 0, 'with_phone_count': 0,
         'last_run_at': None},
    ])
    with patch('db.get_connection', return_value=conn), \
         patch('city_health.compute._platform_health', return_value={'status': 'pass'}):
        summary = compute_all_city_health()
    assert summary['total'] == 3
    assert summary['pass'] == 1   # a
    assert summary['degraded'] == 1  # b (low_permits)
    assert summary['fail'] == 1   # c (never_visited)


def test_compute_all_city_health_writes_rows():
    from city_health import compute_all_city_health
    now_str = (datetime.utcnow() - timedelta(hours=1)).strftime('%Y-%m-%d %H:%M:%S')
    conn = _setup_db([
        {'slug': 'x', 'source_type': 'socrata', 'permits_count': 200,
         'profiles_count': 200, 'with_phone_count': 50,
         'last_run_at': now_str},
    ])
    with patch('db.get_connection', return_value=conn), \
         patch('city_health.compute._platform_health', return_value={'status': 'pass'}):
        compute_all_city_health()
    row = conn.execute("SELECT city_slug, status FROM city_health").fetchone()
    assert row == ('x', 'Pass')


def test_compute_all_city_health_skips_inactive():
    """Inactive cities (status != 'active' in prod_cities) are NOT
    scored."""
    from city_health import compute_all_city_health
    conn = _setup_db()
    conn.execute("INSERT INTO prod_cities VALUES ('paused-city', 'socrata', 'paused')")
    conn.commit()
    with patch('db.get_connection', return_value=conn), \
         patch('city_health.compute._platform_health', return_value={'status': 'pass'}):
        summary = compute_all_city_health()
    assert summary['total'] == 0


# ---------------------------------------------------------------------
# ensure_table
# ---------------------------------------------------------------------

def test_ensure_table_creates_city_health():
    from city_health import ensure_table
    conn = sqlite3.connect(':memory:')
    with patch('db.get_connection', return_value=conn):
        assert ensure_table() is True
    # Table now exists
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='city_health'").fetchall()
    assert len(rows) == 1


def test_ensure_table_idempotent():
    """Second call must not raise."""
    from city_health import ensure_table
    conn = sqlite3.connect(':memory:')
    with patch('db.get_connection', return_value=conn):
        assert ensure_table() is True
        assert ensure_table() is True


def test_ensure_table_creates_indexes():
    from city_health import ensure_table
    conn = sqlite3.connect(':memory:')
    with patch('db.get_connection', return_value=conn):
        ensure_table()
    indexes = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'"
    ).fetchall()}
    assert 'idx_city_health_status' in indexes
    assert 'idx_city_health_computed_at' in indexes


# ---------------------------------------------------------------------
# start_thread (V475 thread-name contract)
# ---------------------------------------------------------------------

def test_start_thread_spawns_with_correct_name():
    """V540 contract: thread name MUST be 'health_scheduler' (V475
    pattern). routes/admin.py /api/admin/debug/threads enumerates
    threads by name; this is the contract."""
    import city_health.scheduler as scheduler_mod
    scheduler_mod._thread = None
    with patch.object(scheduler_mod, 'health_daemon', lambda: None):
        t = scheduler_mod.start_thread()
    try:
        assert t.name == 'health_scheduler', (
            f"V540 regression: thread name is {t.name!r}, must be "
            "'health_scheduler' for the V475 thread-name contract."
        )
        assert t.daemon is True
    finally:
        scheduler_mod._thread = None


def test_start_thread_idempotent():
    """Second call returns same thread (or fresh if first died)."""
    import city_health.scheduler as scheduler_mod
    import time as _time
    scheduler_mod._thread = None
    with patch.object(scheduler_mod, 'health_daemon', lambda: None):
        t = scheduler_mod.start_thread()
        # Let no-op finish
        for _ in range(20):
            if not t.is_alive():
                break
            _time.sleep(0.01)
        t2 = scheduler_mod.start_thread()
    try:
        assert t2.name == 'health_scheduler'
    finally:
        scheduler_mod._thread = None


def test_start_thread_no_eager_compute_import():
    """Importing city_health must NOT eagerly run compute. The
    scheduler's compute_all_city_health is lazy (only fired inside
    the daemon loop)."""
    # If this import order ever crashes, the test catches it before
    # production gets a stuck daemon.
    import city_health
    assert callable(city_health.start_thread)
    assert callable(city_health.compute_city_health)


# ---------------------------------------------------------------------
# V540 PR2: GET /api/admin/city-health endpoint
# ---------------------------------------------------------------------

def _seed_city_health_table(conn, rows):
    """Insert city_health rows + matching prod_cities rows for the
    platform JOIN. `rows` is list of dicts with keys: slug, status,
    reason_code, reason_detail, source_type."""
    for r in rows:
        conn.execute(
            "INSERT INTO city_health "
            "(city_slug, status, reason_code, reason_detail, evidence_json, computed_at) "
            "VALUES (?, ?, ?, ?, ?, datetime('now'))",
            (r['slug'], r['status'], r.get('reason_code', ''),
             r.get('reason_detail', ''), '{}'),
        )
        conn.execute(
            "INSERT INTO prod_cities (city_slug, source_type, status) "
            "VALUES (?, ?, 'active')",
            (r['slug'], r.get('source_type', 'socrata')),
        )
    conn.commit()


def _v540_endpoint_client(conn):
    """Return (app, db_patch). Uses the module-level `from server import
    app as _server_app` (loaded at import time per the circular-import
    workaround above)."""
    import os
    import pytest
    if _server_app is None:
        pytest.skip('server.app failed to import (likely missing flask_login etc.)')
    os.environ['ADMIN_KEY'] = 'test-admin-key'
    _server_app.config['TESTING'] = True
    return _server_app, patch('db.get_connection', return_value=conn)


def test_v540_endpoint_function_registered_with_route():
    """V540 PR2: file-level guard. The canonical /api/admin/city-health
    endpoint should be wired to admin_city_health (not the V226
    dashboard rollup, which is now admin_city_health_dashboard at
    /api/admin/city-health-dashboard).
    """
    import os
    repo = os.path.join(os.path.dirname(__file__), '..')
    src = open(os.path.join(repo, 'routes', 'admin.py')).read()
    assert "@admin_bp.route('/api/admin/city-health', methods=['GET'])\ndef admin_city_health(" in src, (
        'V540 PR2 regression: /api/admin/city-health no longer routes to admin_city_health'
    )
    assert "@admin_bp.route('/api/admin/city-health-dashboard'" in src, (
        'V540 PR2 regression: V226 dashboard rollup not renamed to /api/admin/city-health-dashboard'
    )


def test_v540_cache_helpers_get_put_and_ttl():
    """Test the cache helpers directly without going through the full
    Flask request lifecycle (which trips the server's before_request
    schema migrations against the in-memory test DB)."""
    if _server_app is None:
        import pytest
        pytest.skip('server not importable')
    from routes.admin import _v540_cache_get, _v540_cache_put, _v540_response_cache
    _v540_response_cache.clear()

    key = ('pass', '', '')
    assert _v540_cache_get(key) is None  # empty
    body = {'count': 5, 'by_status': {'Pass': 5, 'Degraded': 0, 'Fail': 0}, 'cities': []}
    _v540_cache_put(key, body)
    assert _v540_cache_get(key) == body  # round trip


def test_v540_cache_expires_after_ttl():
    """The 60s TTL is enforced by _v540_cache_get returning None when
    the cached entry is older than _V540_CACHE_TTL_SECONDS."""
    if _server_app is None:
        import pytest
        pytest.skip('server not importable')
    import routes.admin as admin_mod
    admin_mod._v540_response_cache.clear()

    key = ('expired', '', '')
    body = {'count': 1}
    # Manually inject an entry timestamped >60s ago.
    admin_mod._v540_response_cache[key] = (
        admin_mod._v540_time.time() - 90.0,  # 90s ago
        body,
    )
    assert admin_mod._v540_cache_get(key) is None, (
        'V540 PR2 regression: cache entries must expire after the TTL'
    )


def test_v540_cache_keys_distinguish_filters():
    """Different (status, slug, platform) tuples must NOT share cache
    entries — filtered responses are disjoint."""
    if _server_app is None:
        import pytest
        pytest.skip('server not importable')
    import routes.admin as admin_mod
    admin_mod._v540_response_cache.clear()

    admin_mod._v540_cache_put(('pass', '', ''), {'tag': 'PASS-only'})
    admin_mod._v540_cache_put(('fail', '', ''), {'tag': 'FAIL-only'})
    admin_mod._v540_cache_put(('', '', 'socrata'), {'tag': 'platform=socrata'})
    assert admin_mod._v540_cache_get(('pass', '', ''))['tag'] == 'PASS-only'
    assert admin_mod._v540_cache_get(('fail', '', ''))['tag'] == 'FAIL-only'
    assert admin_mod._v540_cache_get(('', '', 'socrata'))['tag'] == 'platform=socrata'


def test_v540_endpoint_dashboard_renamed_for_back_compat():
    """V540 PR2 reconciliation: V226's dashboard rollup endpoint
    moved from /api/admin/city-health to /api/admin/city-health-dashboard.
    Both endpoint functions must exist + register without Flask
    blueprint collision."""
    if _server_app is None:
        import pytest
        pytest.skip('server not importable')
    import routes.admin as admin_mod
    assert hasattr(admin_mod, 'admin_city_health'), (
        'V540 PR2: canonical endpoint admin_city_health missing'
    )
    assert hasattr(admin_mod, 'admin_city_health_dashboard'), (
        'V540 PR2: renamed dashboard endpoint admin_city_health_dashboard missing'
    )
    # No Flask blueprint duplicate-name collision (V527c lesson)
    src = open(__file__.replace('tests/test_city_health.py', 'routes/admin.py')).read()
    import re
    fn_names = [m.group(1) for m in re.finditer(r'^def\s+([a-zA-Z_][a-zA-Z0-9_]*)', src, re.M)]
    duplicates = [n for n in fn_names if fn_names.count(n) > 1]
    assert not duplicates, (
        f'V527c regression: routes/admin.py has duplicate function names: {duplicates}'
    )
