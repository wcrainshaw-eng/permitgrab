"""V526 regression tests for collector.py's per-cycle city selection.

The 2026-05-05 cutover post-mortem found that the V488 stale-first
sort ranked cities by `MAX(permits.collected_at)`. That column only
advances when the source returns NEW permits — so cities visited
regularly but with idle sources (no new permits returned by the API
for weeks) kept "ageing" under that metric and dominated the 75-cap
budget every cycle. Result: 5 cities (gainesville-fl, cary,
clarksville, fulton, granger) ran 36-51 times in 24h each, while
1,319 cities never got visited in 21+ days.

V526 changes the sort key to `MAX(scraper_runs.run_started_at)`,
which advances on every daemon visit attempt regardless of source
output. Cities the daemon has touched recently fall to the bottom
of the sort; cities visited longest ago bubble up.

These tests pin that behavior. They MUST fail under the V488 query
shape and pass under the V526 query shape.
"""
from __future__ import annotations

import os
import sqlite3


def _make_db():
    """In-memory SQLite mirror of the production schema slice."""
    conn = sqlite3.connect(':memory:')
    conn.executescript("""
        CREATE TABLE prod_cities (
            id INTEGER PRIMARY KEY,
            city_slug TEXT UNIQUE,
            source_id TEXT,
            source_type TEXT,
            status TEXT
        );
        CREATE TABLE permits (
            id INTEGER PRIMARY KEY,
            permit_number TEXT,
            source_city_key TEXT,
            collected_at TEXT
        );
        CREATE TABLE scraper_runs (
            id INTEGER PRIMARY KEY,
            city_slug TEXT,
            run_started_at TEXT
        );
    """)
    return conn


# The two query shapes we're comparing
_OLD_V488_QUERY = """
    SELECT pc.city_slug, pc.source_id,
           (SELECT MAX(collected_at) FROM permits
            WHERE source_city_key = pc.city_slug) AS newest
    FROM prod_cities pc
    WHERE pc.status = 'active' AND pc.source_id IS NOT NULL
      AND pc.source_type IS NOT NULL
    ORDER BY (newest IS NULL) DESC, newest ASC
"""

_NEW_V526_QUERY = """
    SELECT pc.city_slug, pc.source_id,
           (SELECT MAX(run_started_at) FROM scraper_runs
            WHERE city_slug = pc.city_slug) AS last_run
    FROM prod_cities pc
    WHERE pc.status = 'active' AND pc.source_id IS NOT NULL
      AND pc.source_type IS NOT NULL
    ORDER BY (last_run IS NULL) DESC, last_run ASC
"""


def test_v526_starved_city_bubbles_up_under_new_sort():
    """The exact failure mode from production:

      - city_a: visited every cycle (last_run = now - 5min) but
        source has no new permits since 30d ago → permits.collected_at
        = 30d-old. Under V488, ranks AT THE TOP and gets visited again.
      - city_b: visited 21 days ago (last_run = 21d ago), source had
        new permits at the time → permits.collected_at = 21d-1d.
        Under V488, ranks BELOW city_a (collected_at is newer).
        Under V526, ranks ABOVE city_a (last_run is older).

    Production saw 5 city_a-shaped cities winning the budget while
    1,319 city_b-shaped cities starved.
    """
    conn = _make_db()
    conn.executescript("""
        INSERT INTO prod_cities (city_slug, source_id, source_type, status) VALUES
            ('city_a', 'source_a', 'socrata', 'active'),
            ('city_b', 'source_b', 'socrata', 'active');

        -- city_a: source went idle 30 days ago, but daemon visits constantly
        INSERT INTO permits (permit_number, source_city_key, collected_at) VALUES
            ('A-1', 'city_a', datetime('now', '-30 days'));
        INSERT INTO scraper_runs (city_slug, run_started_at) VALUES
            ('city_a', datetime('now', '-5 minutes')),
            ('city_a', datetime('now', '-35 minutes')),
            ('city_a', datetime('now', '-1 hour'));

        -- city_b: visited 21 days ago, source had fresh permits then
        INSERT INTO permits (permit_number, source_city_key, collected_at) VALUES
            ('B-1', 'city_b', datetime('now', '-21 days'));
        INSERT INTO scraper_runs (city_slug, run_started_at) VALUES
            ('city_b', datetime('now', '-21 days'));
    """)

    old_order = [r[0] for r in conn.execute(_OLD_V488_QUERY).fetchall()]
    new_order = [r[0] for r in conn.execute(_NEW_V526_QUERY).fetchall()]

    # V488 (the bug): city_a is "stalest" by collected_at and ranks first.
    # V526 (the fix): city_b is stalest by last_run and ranks first.
    assert old_order == ['city_a', 'city_b'], (
        f"V488 query did not exhibit the production bug shape: {old_order}"
    )
    assert new_order == ['city_b', 'city_a'], (
        f"V526 regression: round-robin sort did not promote starved city_b "
        f"above frequently-visited city_a. Got {new_order}, expected "
        f"['city_b', 'city_a']."
    )


def test_v526_never_visited_city_goes_first():
    """A city in prod_cities that has never appeared in scraper_runs
    must rank FIRST under V526 — last_run is NULL and the sort puts
    NULL-last-run cities ahead of all others."""
    conn = _make_db()
    conn.executescript("""
        INSERT INTO prod_cities (city_slug, source_id, source_type, status) VALUES
            ('never_visited', 'src_nv', 'socrata', 'active'),
            ('visited_today', 'src_vt', 'socrata', 'active'),
            ('visited_yesterday', 'src_vy', 'socrata', 'active');
        INSERT INTO scraper_runs (city_slug, run_started_at) VALUES
            ('visited_today', datetime('now', '-1 hour')),
            ('visited_yesterday', datetime('now', '-1 day'));
    """)
    new_order = [r[0] for r in conn.execute(_NEW_V526_QUERY).fetchall()]
    assert new_order[0] == 'never_visited', (
        f"V526 regression: NULL last_run cities must rank first to bootstrap "
        f"the rotation. Got order: {new_order}"
    )
    assert new_order == ['never_visited', 'visited_yesterday', 'visited_today']


def test_v526_filter_excludes_inactive_and_null_source_type():
    """Bulk-source cities (source_type IS NULL) and inactive cities
    must stay excluded — V526 doesn't change that. They're fed via
    parent state datasets, not the per-city loop."""
    conn = _make_db()
    conn.executescript("""
        INSERT INTO prod_cities (city_slug, source_id, source_type, status) VALUES
            ('typed_active',     'src1',    'socrata', 'active'),
            ('typed_inactive',   'src2',    'socrata', 'inactive'),
            ('null_type_active', 'src3',    NULL,      'active'),
            ('null_id_active',   NULL,      'socrata', 'active');
    """)
    new_order = [r[0] for r in conn.execute(_NEW_V526_QUERY).fetchall()]
    assert new_order == ['typed_active'], (
        f"V526 regression: filter must exclude inactive + NULL source_type "
        f"+ NULL source_id rows. Got: {new_order}"
    )


def test_v526_query_string_matches_collector_py():
    """File-level guard: if a future PR rewrites the query in
    collector.py, this test catches drift from the V526 contract.
    The actual query is in collector.py around line 3995-4015 — if
    you change it, update this test too."""
    src = open(os.path.join(os.path.dirname(__file__), '..', 'collector.py')).read()
    # The V526-shape query: must use scraper_runs.run_started_at, NOT
    # permits.collected_at, as the sort key for the per-cycle priority.
    # If a future refactor reverts to the V488 collected_at shape, this
    # asserts the regression before deploy.
    assert "MAX(run_started_at) FROM scraper_runs" in src, (
        "V526 regression: collector.py no longer sorts by "
        "scraper_runs.run_started_at. Reverting to MAX(permits.collected_at) "
        "re-introduces the 1,319-stale-cities bug — see "
        "tests/test_collector_scheduling.py::test_v526_starved_city_bubbles_up."
    )
    # And the [V526 ROUND-ROBIN] log string must be present so Render
    # logs make the active sort key obvious during cutovers.
    assert "[V526 ROUND-ROBIN]" in src
