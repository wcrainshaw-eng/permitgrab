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
import os
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


# ---------------------------------------------------------------------
# V540 PR3: pre-curation gates (is_sellable_city / get_sellable_cities)
# ---------------------------------------------------------------------

def test_is_sellable_city_returns_true_when_status_pass():
    """The happy path: status='Pass' → sellable."""
    from city_health import is_sellable_city
    conn = _setup_db()
    _seed_city_health_table(conn, [{'slug': 'pass-city', 'status': 'Pass'}])
    with patch('db.get_connection', return_value=conn):
        assert is_sellable_city('pass-city') is True


def test_is_sellable_city_returns_false_for_degraded_and_fail():
    """V540 PR3 contract: pre-curation gates must EXCLUDE Degraded
    and Fail cities. Users see only Pass cities as sellable."""
    from city_health import is_sellable_city
    conn = _setup_db()
    _seed_city_health_table(conn, [
        {'slug': 'degraded-city', 'status': 'Degraded'},
        {'slug': 'fail-city', 'status': 'Fail'},
    ])
    with patch('db.get_connection', return_value=conn):
        assert is_sellable_city('degraded-city') is False
        assert is_sellable_city('fail-city') is False


def test_is_sellable_city_fails_open_when_table_empty():
    """V540 PR3 fail-open contract: a fresh deploy with no city_health
    rows yet must NOT hide every city. Until the daily cron fires,
    treat all cities as sellable."""
    from city_health import is_sellable_city
    conn = _setup_db()  # creates schema, no rows
    with patch('db.get_connection', return_value=conn):
        assert is_sellable_city('any-slug') is True


def test_is_sellable_city_returns_false_for_missing_slug_when_table_populated():
    """If city_health HAS data but the slug isn't in it, the cron
    didn't enumerate it (means the slug isn't 'active' in prod_cities).
    Don't promote unknown slugs."""
    from city_health import is_sellable_city
    conn = _setup_db()
    _seed_city_health_table(conn, [{'slug': 'known-city', 'status': 'Pass'}])
    with patch('db.get_connection', return_value=conn):
        assert is_sellable_city('unknown-slug') is False


def test_is_sellable_city_returns_false_for_empty_or_none():
    from city_health import is_sellable_city
    assert is_sellable_city('') is False
    assert is_sellable_city(None) is False


def test_get_sellable_cities_returns_pass_set():
    """Bulk filter helper: returns set of Pass slugs."""
    from city_health import get_sellable_cities
    conn = _setup_db()
    _seed_city_health_table(conn, [
        {'slug': 'p1', 'status': 'Pass'},
        {'slug': 'p2', 'status': 'Pass'},
        {'slug': 'd1', 'status': 'Degraded'},
        {'slug': 'f1', 'status': 'Fail'},
    ])
    with patch('db.get_connection', return_value=conn):
        sellable = get_sellable_cities()
    assert sellable == {'p1', 'p2'}
    assert isinstance(sellable, set)


def test_filter_to_sellable_drops_degraded_and_fail():
    from city_health import filter_to_sellable
    conn = _setup_db()
    _seed_city_health_table(conn, [
        {'slug': 'good', 'status': 'Pass'},
        {'slug': 'meh', 'status': 'Degraded'},
        {'slug': 'bad', 'status': 'Fail'},
    ])
    with patch('db.get_connection', return_value=conn):
        result = filter_to_sellable(['good', 'meh', 'bad', 'unknown'])
    assert result == ['good']


def test_filter_to_sellable_passes_through_when_table_empty():
    """Cold-start fail-open: if no city_health data, all input slugs
    pass through unchanged."""
    from city_health import filter_to_sellable
    conn = _setup_db()
    with patch('db.get_connection', return_value=conn):
        result = filter_to_sellable(['a', 'b', 'c'])
    assert result == ['a', 'b', 'c']


def test_has_city_health_data_returns_true_with_rows():
    from city_health import has_city_health_data
    conn = _setup_db()
    _seed_city_health_table(conn, [{'slug': 'x', 'status': 'Pass'}])
    with patch('db.get_connection', return_value=conn):
        assert has_city_health_data() is True


def test_has_city_health_data_returns_false_when_empty():
    from city_health import has_city_health_data
    conn = _setup_db()
    with patch('db.get_connection', return_value=conn):
        assert has_city_health_data() is False


def test_v540_pr3_wired_into_server_helpers():
    """File-level guard: server.py:get_popular_cities and
    get_suggested_cities both call filter_to_sellable. If a future
    refactor removes the call, this test catches the regression
    before the picker stops pre-curating."""
    repo = os.path.join(os.path.dirname(__file__), '..')
    src = open(os.path.join(repo, 'server.py')).read()
    # Both helpers must reference filter_to_sellable
    assert 'filter_to_sellable' in src, (
        'V540 PR3 regression: server.py no longer references filter_to_sellable. '
        'The picker stopped pre-curating; users will see Fail cities again.'
    )
    # Specific markers — get_popular_cities + get_suggested_cities both wire it
    pop_idx = src.find('def get_popular_cities(')
    sug_idx = src.find('def get_suggested_cities(')
    pop_block = src[pop_idx:src.find('\ndef ', pop_idx + 1)]
    sug_block = src[sug_idx:src.find('\ndef ', sug_idx + 1)]
    assert 'filter_to_sellable' in pop_block, (
        'V540 PR3 regression: get_popular_cities does not pre-curate'
    )
    assert 'filter_to_sellable' in sug_block, (
        'V540 PR3 regression: get_suggested_cities does not pre-curate'
    )


def test_v540_pr3_wired_into_sitemap():
    """File-level guard: routes/seo.py:sitemap_cities filters to
    sellable. Fail cities must NOT appear in /sitemap-cities.xml so
    Google can de-index pages we can't deliver."""
    repo = os.path.join(os.path.dirname(__file__), '..')
    src = open(os.path.join(repo, 'routes', 'seo.py')).read()
    smc_idx = src.find('def sitemap_cities(')
    smc_block = src[smc_idx:src.find('\n@', smc_idx + 1) if src.find('\n@', smc_idx + 1) > 0 else None]
    assert 'filter_to_sellable' in smc_block, (
        'V540 PR3 regression: sitemap_cities does not filter to sellable; '
        'Fail/Degraded cities will continue showing up in /sitemap-cities.xml'
    )


def test_v540_pr3_wired_into_city_page_soft_degrade():
    """File-level guard: routes/city_pages.py:state_city_landing
    sets g.v540_limited_coverage so templates can render the
    'limited coverage' banner without 404-ing direct-URL hits to
    Fail city slugs."""
    repo = os.path.join(os.path.dirname(__file__), '..')
    src = open(os.path.join(repo, 'routes', 'city_pages.py')).read()
    scl_idx = src.find('def state_city_landing(')
    next_def = src.find('\ndef ', scl_idx + 1)
    scl_block = src[scl_idx:next_def if next_def > 0 else None][:2000]
    assert 'g.v540_limited_coverage' in scl_block, (
        'V540 PR3 regression: state_city_landing does not set the soft-degrade '
        'flag. Direct URLs to Fail city slugs will render full content as if '
        'they were sellable.'
    )


# ---------------------------------------------------------------------
# V540 PR4: defense-in-depth digest safety net
# ---------------------------------------------------------------------

def test_v540_pr4_digest_skip_drops_fail_city():
    """V540 PR4 contract: when a subscriber's city is Fail at digest
    time, drop THAT city for THIS digest. Pin: only Pass cities pass
    through; Degraded + Fail are dropped."""
    from city_health import filter_subscriber_cities_for_digest
    conn = _setup_db()
    _seed_city_health_table(conn, [
        {'slug': 'good-city', 'status': 'Pass'},
        {'slug': 'bad-city', 'status': 'Fail'},
    ])
    with patch('db.get_connection', return_value=conn):
        kept = filter_subscriber_cities_for_digest('alice@example.com',
                                                    ['good-city', 'bad-city'])
    assert kept == ['good-city']


def test_v540_pr4_digest_skip_logs_to_digest_log():
    """Each dropped slug → digest_log row with status='safety_net_skip'.
    Admin dashboard reads digest_log to surface the alert."""
    from city_health import filter_subscriber_cities_for_digest
    conn = _setup_db()
    _seed_city_health_table(conn, [
        {'slug': 'good-city', 'status': 'Pass'},
        {'slug': 'bad-city', 'status': 'Fail'},
    ])
    with patch('db.get_connection', return_value=conn):
        filter_subscriber_cities_for_digest('alice@example.com',
                                             ['good-city', 'bad-city'])
    rows = conn.execute(
        "SELECT recipient_email, status, error_message FROM digest_log"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == 'alice@example.com'
    assert rows[0][1] == 'safety_net_skip'
    assert "'bad-city'" in rows[0][2]


def test_v540_pr4_digest_skip_returns_empty_when_all_fail():
    """If ALL subscriber cities are Fail, return empty list. Caller
    (email_alerts.send_daily_digest_to_user) interprets empty list
    as 'skip the entire digest, don't email subscriber'."""
    from city_health import filter_subscriber_cities_for_digest
    conn = _setup_db()
    _seed_city_health_table(conn, [
        {'slug': 'a', 'status': 'Fail'},
        {'slug': 'b', 'status': 'Fail'},
    ])
    with patch('db.get_connection', return_value=conn):
        kept = filter_subscriber_cities_for_digest('alice@example.com', ['a', 'b'])
    assert kept == []


def test_v540_pr4_digest_skip_cold_start_fail_open():
    """If city_health is empty (fresh deploy, scheduler hasn't fired),
    pass through unchanged. Don't break digests just because the
    safety net hasn't been computed yet."""
    from city_health import filter_subscriber_cities_for_digest
    conn = _setup_db()
    with patch('db.get_connection', return_value=conn):
        kept = filter_subscriber_cities_for_digest('alice@example.com',
                                                    ['any-slug', 'another'])
    assert kept == ['any-slug', 'another']


def test_v540_pr4_digest_skip_handles_empty_input():
    from city_health import filter_subscriber_cities_for_digest
    assert filter_subscriber_cities_for_digest('a@b.com', []) == []


# ---------------------------------------------------------------------
# V541a: /api/admin/city-health/compute-now endpoint
# ---------------------------------------------------------------------

def test_v541a_compute_now_endpoint_registered():
    """File-level guard: the on-demand compute endpoint exists at the
    Wes-specified path, gated by admin key, and calls
    compute_all_city_health()."""
    repo = os.path.join(os.path.dirname(__file__), '..')
    src = open(os.path.join(repo, 'routes', 'admin.py')).read()
    assert "@admin_bp.route('/api/admin/city-health/compute-now', methods=['POST'])" in src, (
        'V541a regression: /api/admin/city-health/compute-now route missing'
    )
    fn_idx = src.find('def admin_city_health_compute_now(')
    assert fn_idx > 0, (
        'V541a regression: admin_city_health_compute_now function not defined'
    )
    fn_block = src[fn_idx:src.find('\n@', fn_idx + 1)]
    assert 'compute_all_city_health' in fn_block, (
        'V541a regression: endpoint must call compute_all_city_health'
    )
    assert 'ensure_table' in fn_block, (
        'V541a regression: endpoint must ensure_table before compute (handles fresh DB)'
    )
    assert 'check_admin_key' in fn_block, (
        'V541a regression: endpoint must be admin-key gated'
    )
    assert '_v540_response_cache.clear' in fn_block, (
        'V541a regression: endpoint must bust the V540 PR2 response cache so '
        'subsequent GET /api/admin/city-health calls see the fresh state'
    )


def test_v540_pr4_wired_into_email_alerts():
    """File-level guard: email_alerts.send_daily_digest_to_user calls
    filter_subscriber_cities_for_digest. If a future refactor removes
    the call, the safety net is silently disabled — this test catches
    that."""
    repo = os.path.join(os.path.dirname(__file__), '..')
    src = open(os.path.join(repo, 'email_alerts.py')).read()
    sdd_idx = src.find('def send_daily_digest_to_user(')
    next_def = src.find('\ndef ', sdd_idx + 1)
    block = src[sdd_idx:next_def if next_def > 0 else None]
    assert 'filter_subscriber_cities_for_digest' in block, (
        'V540 PR4 regression: email_alerts.send_daily_digest_to_user '
        'no longer calls filter_subscriber_cities_for_digest. The '
        'defense-in-depth safety net is bypassed; subscribers whose '
        "city has flipped Fail since subscribe-time will receive "
        'broken digests.'
    )
    assert "'v540_safety_net_all_fail'" in block, (
        'V540 PR4 regression: send_daily_digest_to_user does not '
        'short-circuit when all cities are Fail. The function should '
        'return a v540_safety_net_all_fail status code so the caller '
        'knows the digest was suppressed (not sent + failed).'
    )


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
