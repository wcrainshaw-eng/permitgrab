"""V527 regression tests for the collectors/ package.

Phase A pins the contract:
  - Each platform module exports fetch / parse / health_check.
  - get_collector_for() resolves canonical names + synonyms.
  - health_check() returns the documented dict shape and behaves
    correctly for a handful of fixture scenarios driven from an
    in-memory SQLite mirror of the production schema.

Phase B will add per-platform integration tests with recorded
fixtures (snapshot the actual API response for chicago-il,
miami-dade-county, etc.) so tests don't hit the network. That's
out of scope for V527 Phase A — the directive says small, gated on
green tests, and the shim layer is already enough surface for one
PR.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta
from unittest.mock import patch


# ---------------------------------------------------------------------
# Contract-surface tests (don't touch the DB)
# ---------------------------------------------------------------------

def test_package_exports_supported_platforms():
    import collectors
    assert collectors.SUPPORTED_PLATFORMS == (
        'socrata', 'arcgis', 'accela', 'ckan', 'csv_state'
    )


def test_each_platform_module_has_required_api():
    """fetch + parse + health_check + PLATFORM constant — the
    'thin contract' that Phase B can rely on without breaking
    callers."""
    import collectors
    for name in collectors.SUPPORTED_PLATFORMS:
        mod = getattr(collectors, name)
        assert hasattr(mod, 'PLATFORM'), name
        assert mod.PLATFORM == name, (
            f'{name}: PLATFORM={mod.PLATFORM!r}, expected {name!r}'
        )
        assert callable(getattr(mod, 'fetch', None)), name
        assert callable(getattr(mod, 'parse', None)), name
        assert callable(getattr(mod, 'health_check', None)), name


def test_get_collector_for_resolves_canonical_names():
    import collectors
    for name in collectors.SUPPORTED_PLATFORMS:
        mod = collectors.get_collector_for(name)
        assert mod is not None, name
        assert mod.PLATFORM == name


def test_get_collector_for_resolves_synonyms():
    """ArcGIS FeatureServer and MapServer flow through the same
    fetcher; the V527 module map normalizes them. accela_arcgis_hybrid
    is an Accela variant per V476 (Tampa pattern). Pinning these
    aliases so a future refactor can't silently break dispatch."""
    import collectors
    assert collectors.get_collector_for('arcgis_featureserver') is collectors.arcgis
    assert collectors.get_collector_for('arcgis_mapserver') is collectors.arcgis
    assert collectors.get_collector_for('accela_arcgis_hybrid') is collectors.accela


def test_get_collector_for_returns_none_on_unknown():
    import collectors
    assert collectors.get_collector_for(None) is None
    assert collectors.get_collector_for('') is None
    assert collectors.get_collector_for('flintstones') is None


# ---------------------------------------------------------------------
# health_check() behavior — driven by fixture scenarios
# ---------------------------------------------------------------------

def _seed_db_for_health(scenarios):
    """Create an in-memory SQLite that mirrors the slice of production
    schema the health_check helper reads. `scenarios` is a list of
    dicts with keys: slug, last_run (ISO string or None),
    last_run_status, consecutive_failures, newest_permit_date."""
    conn = sqlite3.connect(':memory:')
    conn.executescript("""
        CREATE TABLE prod_cities (
            city_slug TEXT,
            status TEXT,
            consecutive_failures INTEGER DEFAULT 0
        );
        CREATE TABLE scraper_runs (
            id INTEGER PRIMARY KEY,
            city_slug TEXT,
            run_started_at TEXT,
            status TEXT,
            error_message TEXT
        );
        CREATE TABLE permits (
            id INTEGER PRIMARY KEY,
            permit_number TEXT,
            source_city_key TEXT,
            collected_at TEXT,
            date TEXT
        );
    """)
    for s in scenarios:
        conn.execute(
            "INSERT INTO prod_cities (city_slug, status, consecutive_failures) "
            "VALUES (?, 'active', ?)",
            (s['slug'], s.get('consecutive_failures', 0)),
        )
        if s.get('last_run'):
            conn.execute(
                "INSERT INTO scraper_runs (city_slug, run_started_at, status, error_message) "
                "VALUES (?, ?, ?, ?)",
                (s['slug'], s['last_run'], s.get('last_run_status', 'success'), s.get('error_message')),
            )
        if s.get('newest_permit_date'):
            # Insert a permit row matching the slug + date
            conn.execute(
                "INSERT INTO permits (permit_number, source_city_key, collected_at, date) "
                "VALUES (?, ?, ?, ?)",
                (
                    f"P-{s['slug']}",
                    s['slug'],
                    s.get('newest_collected_at', s['newest_permit_date']),
                    s['newest_permit_date'],
                ),
            )
    conn.commit()
    return conn


def test_health_check_pass_when_recent_visit_and_fresh_permits():
    """Happy path: visited 6h ago, newest permit 2 days old → pass."""
    from collectors import socrata
    now = datetime.utcnow()
    conn = _seed_db_for_health([{
        'slug': 'happy-city',
        'last_run': (now - timedelta(hours=6)).isoformat(sep=' ', timespec='seconds'),
        'last_run_status': 'success',
        'newest_permit_date': (now - timedelta(days=2)).date().isoformat(),
    }])
    with patch('db.get_connection', return_value=conn):
        result = socrata.health_check('happy-city')
    assert result['status'] == 'pass', f'expected pass, got {result}'
    assert result['platform'] == 'socrata'
    assert result['consecutive_failures'] == 0
    assert result['last_run_status'] == 'success'


def test_health_check_fail_when_no_scraper_runs_entry():
    """A city in prod_cities that has never been visited → fail
    with reason 'never visited'."""
    from collectors import socrata
    conn = _seed_db_for_health([{'slug': 'never-visited', 'last_run': None}])
    with patch('db.get_connection', return_value=conn):
        result = socrata.health_check('never-visited')
    assert result['status'] == 'fail'
    assert 'never been visited' in result['reason']


def test_health_check_fail_when_visit_too_old_for_platform_threshold():
    """Socrata threshold is 36h; visit 48h ago should trip fail."""
    from collectors import socrata
    now = datetime.utcnow()
    conn = _seed_db_for_health([{
        'slug': 'stale-city',
        'last_run': (now - timedelta(hours=48)).isoformat(sep=' ', timespec='seconds'),
        'last_run_status': 'success',
    }])
    with patch('db.get_connection', return_value=conn):
        result = socrata.health_check('stale-city')
    assert result['status'] == 'fail'
    assert 'h ago' in result['reason']
    assert '36h threshold' in result['reason']


def test_health_check_accela_uses_72h_threshold_not_36h():
    """Accela cities run less often by design — 48h should still pass
    for accela but already fail for socrata. Pins the per-platform
    threshold contract from collectors/_base.py."""
    from collectors import accela, socrata
    now = datetime.utcnow()
    conn = _seed_db_for_health([{
        'slug': 'mid-stale',
        'last_run': (now - timedelta(hours=48)).isoformat(sep=' ', timespec='seconds'),
        'last_run_status': 'success',
        'newest_permit_date': (now - timedelta(days=3)).date().isoformat(),
    }])
    with patch('db.get_connection', return_value=conn):
        socrata_result = socrata.health_check('mid-stale')
        # Reset connection cursor for second call
        conn2 = _seed_db_for_health([{
            'slug': 'mid-stale',
            'last_run': (now - timedelta(hours=48)).isoformat(sep=' ', timespec='seconds'),
            'last_run_status': 'success',
            'newest_permit_date': (now - timedelta(days=3)).date().isoformat(),
        }])
    with patch('db.get_connection', return_value=conn2):
        accela_result = accela.health_check('mid-stale')
    assert socrata_result['status'] == 'fail', (
        f"socrata at 48h should fail (threshold 36h), got {socrata_result}"
    )
    assert accela_result['status'] == 'pass', (
        f"accela at 48h should pass (threshold 72h), got {accela_result}"
    )


def test_health_check_degraded_when_consecutive_errors_below_threshold():
    """1-2 consecutive errors → degraded; 3+ → fail."""
    from collectors import arcgis
    now = datetime.utcnow()
    conn = _seed_db_for_health([{
        'slug': 'flaky-city',
        'last_run': (now - timedelta(hours=2)).isoformat(sep=' ', timespec='seconds'),
        'last_run_status': 'error',
        'error_message': 'http_500',
        'consecutive_failures': 2,
    }])
    with patch('db.get_connection', return_value=conn):
        result = arcgis.health_check('flaky-city')
    assert result['status'] == 'degraded', f'expected degraded, got {result}'
    assert 'consecutive failures' in result['reason']


def test_health_check_fail_at_three_consecutive_failures():
    from collectors import arcgis
    now = datetime.utcnow()
    conn = _seed_db_for_health([{
        'slug': 'broken-city',
        'last_run': (now - timedelta(hours=2)).isoformat(sep=' ', timespec='seconds'),
        'last_run_status': 'error',
        'error_message': 'http_500',
        'consecutive_failures': 3,
    }])
    with patch('db.get_connection', return_value=conn):
        result = arcgis.health_check('broken-city')
    assert result['status'] == 'fail'
    assert '3 consecutive failures' in result['reason']


def test_health_check_degraded_when_source_idle_but_daemon_visiting():
    """The V526-shape pattern: visited recently, but newest permit
    is months old (source has gone idle). health_check flags this
    as degraded so a city-health dashboard can show 'daemon ok,
    upstream stale'."""
    from collectors import socrata
    now = datetime.utcnow()
    conn = _seed_db_for_health([{
        'slug': 'idle-source-city',
        'last_run': (now - timedelta(hours=1)).isoformat(sep=' ', timespec='seconds'),
        'last_run_status': 'success',
        'newest_permit_date': (now - timedelta(days=120)).date().isoformat(),
    }])
    with patch('db.get_connection', return_value=conn):
        result = socrata.health_check('idle-source-city')
    assert result['status'] == 'degraded'
    assert 'source has gone idle' in result['reason']


def test_resolve_health_for_unknown_platform():
    """An active city with NULL source_type goes degraded with the
    right reason (V526 audit found 1,025 such rows — they're bulk-
    source recipients, not directly fetchable)."""
    import collectors
    conn = sqlite3.connect(':memory:')
    conn.execute("CREATE TABLE prod_cities (city_slug TEXT, source_type TEXT, status TEXT)")
    conn.execute(
        "INSERT INTO prod_cities (city_slug, source_type, status) VALUES "
        "('bulk-recipient', NULL, 'active')"
    )
    conn.commit()
    with patch('db.get_connection', return_value=conn):
        result = collectors._resolve_health('bulk-recipient')
    assert result['status'] in ('degraded', 'fail'), result
    # The exact reason mentions the platform (None or NULL)
    assert 'unsupported' in result['reason'].lower() or 'null' in result['reason'].lower(), (
        f"Expected mention of unsupported/null platform, got: {result['reason']!r}"
    )


def test_resolve_health_for_inactive_or_missing_city():
    import collectors
    conn = sqlite3.connect(':memory:')
    conn.execute("CREATE TABLE prod_cities (city_slug TEXT, source_type TEXT, status TEXT)")
    # no rows
    conn.commit()
    with patch('db.get_connection', return_value=conn):
        result = collectors._resolve_health('does-not-exist')
    assert result['status'] == 'fail'
    assert 'not in prod_cities' in result['reason']


# ---------------------------------------------------------------------
# Phase A integrity: shims don't break existing callers
# ---------------------------------------------------------------------

def test_collectors_dont_eagerly_import_collector_py():
    """Importing the collectors package must NOT pull collector.py into
    sys.modules. Phase A shims import collector.py inside functions
    only, so test collectors are usable in lighter contexts (admin
    routes that just want health_check) without paying collector.py's
    full module-load cost."""
    import sys
    # Save a snapshot — the test itself triggers import collectors
    # at the top of the file via the other tests, but we only care
    # that collector.py wasn't pulled in by collectors's __init__.
    if 'collector' in sys.modules:
        # Some other test already imported it; can't run this assertion
        # cleanly. Skip.
        import pytest
        pytest.skip('collector already in sys.modules from another test')
    import collectors  # noqa: F401
    assert 'collector' not in sys.modules, (
        "V527: importing collectors must not transitively load "
        "collector.py — keep the dispatch shims lazy."
    )


def test_collectors_package_directory_structure():
    """File-level guard so a future refactor can't silently delete
    a platform module without breaking this test first."""
    pkg_dir = os.path.join(os.path.dirname(__file__), '..', 'collectors')
    expected = {
        '__init__.py',
        '_base.py',
        'socrata.py',
        'arcgis.py',
        'accela.py',
        'ckan.py',
        'csv_state.py',
    }
    actual = {f for f in os.listdir(pkg_dir) if f.endswith('.py')}
    missing = expected - actual
    assert not missing, f'V527 regression: missing platform modules: {missing}'


def test_v535_collector_fetch_arcgis_is_back_compat_shim_to_collectors():
    """V535 contract: fetch_arcgis body moved to collectors/arcgis.py.
    Same shape as V534 socrata. Tests look for the CODE form of
    distinctive markers (e.g. `"returnGeometry": "false"` as a
    dict-param string, not just the bare word) so the shim's docstring
    can mention them without tripping the test — V535b lesson learned.
    """
    repo_root = os.path.join(os.path.dirname(__file__), '..')
    collector_src = open(os.path.join(repo_root, 'collector.py')).read()
    arcgis_src = open(os.path.join(repo_root, 'collectors', 'arcgis.py')).read()

    assert 'def fetch_arcgis' in collector_src
    fa_idx = collector_src.find('\ndef fetch_arcgis(')
    next_def = collector_src.find('\ndef ', fa_idx + 1)
    fetch_arcgis_block = collector_src[fa_idx:next_def if next_def > 0 else None]
    # Code-form markers: actual ArcGIS body would have these as Python
    # param dicts / function calls / variable assignments. Docstring
    # mentions don't match.
    assert '"returnGeometry": "false"' not in fetch_arcgis_block, (
        f'V535 regression: fetch_arcgis pagination logic still in collector.py:\n'
        f'{fetch_arcgis_block[:300]}'
    )
    assert 'MAX_PAGES = 10' not in fetch_arcgis_block, (
        f'V535 regression: pagination loop still in collector.py'
    )
    assert 'from collectors.arcgis import fetch' in fetch_arcgis_block, (
        "V535 regression: collector.fetch_arcgis shim must call into "
        "collectors.arcgis.fetch."
    )

    # Body lives in collectors/arcgis.py
    assert '"returnGeometry": "false"' in arcgis_src
    assert 'exceededTransferLimit' in arcgis_src
    assert 'MAX_PAGES = 10' in arcgis_src


def test_v543_health_check_handles_sqlite_row_factory():
    """V543 regression: collectors._base.health_check used
    list(row.values())[0] for the dict branch — fails on sqlite3.Row
    (which has keys() but no values()). Same bug class as V541b's
    fix to city_health.compute._measure_city. Pin: with sqlite3.Row
    factory + chicago-shaped seed, health_check returns 'pass' (not
    'fail'/'degraded' from silently-zero permits_24h).
    """
    import sqlite3
    from datetime import datetime, timedelta
    from unittest.mock import patch

    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE prod_cities (city_slug TEXT, source_type TEXT, status TEXT, consecutive_failures INTEGER DEFAULT 0);
        CREATE TABLE permits (id INTEGER PRIMARY KEY, permit_number TEXT, source_city_key TEXT, collected_at TEXT, date TEXT);
        CREATE TABLE scraper_runs (id INTEGER PRIMARY KEY, city_slug TEXT, run_started_at TEXT, status TEXT, error_message TEXT);
    """)
    now_str = (datetime.utcnow() - timedelta(hours=1)).strftime('%Y-%m-%d %H:%M:%S')
    today = datetime.utcnow().strftime('%Y-%m-%d')
    conn.execute("INSERT INTO prod_cities VALUES ('chicago-il', 'socrata', 'active', 0)")
    # 5 permits today (within 24h)
    for i in range(5):
        conn.execute("INSERT INTO permits (permit_number, source_city_key, collected_at, date) VALUES (?, ?, ?, ?)",
                     (f'P{i}', 'chicago-il', now_str, today))
    conn.execute("INSERT INTO scraper_runs (city_slug, run_started_at, status) VALUES (?, ?, 'success')",
                 ('chicago-il', now_str))
    conn.commit()

    from collectors import socrata
    with patch('db.get_connection', return_value=conn):
        result = socrata.health_check('chicago-il')

    assert result['status'] == 'pass', (
        f'V543 regression: collectors.socrata.health_check returned {result} '
        f'instead of pass on sqlite3.Row factory. Bug back in _row_value.'
    )
    assert result['permits_24h'] == 5, (
        f'V543 regression: permits_24h not extracted from SELECT COUNT(*) AS cnt. '
        f'Got {result["permits_24h"]}, expected 5.'
    )
    assert result['newest_permit_date'] == today


def test_v534_collector_fetch_socrata_is_back_compat_shim_to_collectors():
    """V534 contract: after moving the fetch_socrata body into
    collectors/socrata.py, collector.fetch_socrata MUST still be
    callable as a back-compat shim (any caller using
    `from collector import fetch_socrata` should keep working).
    File-level guard: ensure collector.py contains the shim and
    that collectors/socrata.py has the actual body.
    """
    import re as _re
    repo_root = os.path.join(os.path.dirname(__file__), '..')
    collector_src = open(os.path.join(repo_root, 'collector.py')).read()
    socrata_src = open(os.path.join(repo_root, 'collectors', 'socrata.py')).read()

    # collector.py must still export fetch_socrata
    assert 'def fetch_socrata' in collector_src, (
        'V534 regression: collector.fetch_socrata removed entirely; '
        'callers using `from collector import fetch_socrata` will break.'
    )
    # Extract the fetch_socrata function body specifically (between
    # `^def fetch_socrata(` and the next `^def `). Must NOT contain
    # the multi-page pagination logic anymore — that lives in
    # collectors/socrata.py now. The narrow check skips collector.py's
    # OTHER fetch_*() bodies (fetch_arcgis, fetch_carto, etc.) which
    # also have their own MAX_PAGES=10 and stay in collector.py until
    # later Phase-B passes.
    fs_idx = collector_src.find('\ndef fetch_socrata(')
    assert fs_idx >= 0, 'V534: def fetch_socrata not found in collector.py'
    next_def_idx = collector_src.find('\ndef ', fs_idx + 1)
    fetch_socrata_block = collector_src[fs_idx:next_def_idx if next_def_idx > 0 else None]
    assert 'MAX_PAGES' not in fetch_socrata_block, (
        f'V534 regression: fetch_socrata body still has pagination loop '
        f'in collector.py — should live in collectors/socrata.py per '
        f'Phase B. Block:\n{fetch_socrata_block[:300]}'
    )
    assert 'from collectors.socrata import fetch' in fetch_socrata_block, (
        "V534 regression: collector.fetch_socrata back-compat shim "
        "must call into collectors.socrata.fetch."
    )

    # The actual body MUST be in collectors/socrata.py
    assert 'MAX_PAGES = 10' in socrata_src, (
        'V534 regression: fetch_socrata body not found in '
        'collectors/socrata.py.'
    )


def test_v527c_admin_endpoint_function_names_are_unique():
    """V527c regression: routes/admin.py had TWO functions named
    `admin_collector_health` — the V15 HTML dashboard at line ~5884
    and the V527 JSON API endpoint I added at line ~229. Flask
    blueprint silently overwrote my registration on duplicate
    endpoint name, making /api/admin/collector-health 404 in
    production until I renamed the API handler to
    admin_api_collector_health.

    This test scans routes/admin.py for any pair of `def <name>`
    that share an identifier, since Flask's silent-overwrite is
    too easy to re-introduce on the next route addition.
    """
    import re
    src = open(os.path.join(
        os.path.dirname(__file__), '..', 'routes', 'admin.py'
    )).read()
    seen = {}
    for m in re.finditer(r'^def\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(', src, re.M):
        name = m.group(1)
        line = src.count('\n', 0, m.start()) + 1
        if name in seen:
            raise AssertionError(
                f"V527c regression: routes/admin.py has duplicate function "
                f"name {name!r} at lines {seen[name]} and {line}. Flask blueprint "
                f"silently overwrites on duplicate endpoint names — rename one."
            )
        seen[name] = line
