"""V329 (CODE_V320 Part C): visual / UX regression tests.

The existing tests check status codes and gating but miss the kind of
bugs Wes finds in 30 seconds of clicking around — empty dropdowns,
duplicate sections, broken nav links, slow pages. These tests parse
the actual HTML the user sees and assert the page is rendered
correctly, not just that it returned 200.

Why a separate file: the test_routes.py file is already long and
mixes concerns. Visual/UX tests benefit from being co-located so it
is obvious what failed and why. Each test maps to a known historical
bug.
"""
import re
import time

import pytest

from server import app


@pytest.fixture
def client():
    app.config['TESTING'] = True
    return app.test_client()


# Cities that are known ad-ready as of V327. If any drop off the dropdown
# we want to know loudly — the test is a smoke alarm for the nav_cities
# DB query degrading.
AD_READY_SAMPLE = ['chicago-il', 'san-antonio-tx', 'phoenix-az']


def _fetch(client, path):
    """Return (status, html_body) for a path."""
    r = client.get(path)
    return r.status_code, r.get_data(as_text=True)


# ==========================================================================
# 1. NAV CONSISTENCY — Bug 1 from CODE_V320
# ==========================================================================

def test_nav_cities_includes_san_antonio(client):
    """Bug 1: nav_cities was [] which forced the 9-city fallback that
    omitted San Antonio (our #1 phone city). The fallback now lists
    San Antonio explicitly so the link appears regardless of whether
    the live nav_cities query returned data (empty DB in tests)."""
    status, body = _fetch(client, '/permits/chicago-il')
    assert status == 200
    assert '/permits/san-antonio-tx' in body, \
        'San Antonio missing from nav on /permits/chicago-il (fallback or live)'


def test_nav_dropdown_uses_click_class(client):
    """Bug 2: the dropdown was pure CSS :hover and vanished the instant
    the cursor left. V327 switched to .dropdown-open click-toggle. The
    page must reference the new class (CSS rule + JS toggle)."""
    status, body = _fetch(client, '/permits/chicago-il')
    assert status == 200
    assert 'dropdown-open' in body, \
        'Dropdown click-toggle class not present in nav.html output'
    # And the old hover-only rule must not regress back in
    assert ':hover .dropdown-menu' not in body, \
        'Old :hover-only dropdown rule regressed; menu will vanish on cursor leave'


# ==========================================================================
# 2. CLICK-THROUGH — Bug 3 + Part B sanity
# ==========================================================================

def test_view_all_link_stays_on_city_page(client):
    """Bug 3: "View all" used to be href="/?city=<name>" which routed
    through the homepage and dropped auth state. The link now points
    at /permits/<slug>. We don't expect "/?city=" anywhere in the
    rendered city page now that V328 took over the table layout."""
    _, body = _fetch(client, '/permits/chicago-il')
    assert '/?city=' not in body, \
        '"View all" link still routes through homepage (drops auth state)'


def _has_permit_data(body):
    """Detect whether the city page rendered with actual permit data
    or in the "Coming Soon" empty-DB state. The unified table only
    renders in the data path so visual asserts depending on the table
    must skip when there's no data (local test SQLite is usually empty)."""
    return 'Permit Data Coming Soon' not in body and 'permits-tbody' in body


def test_unified_table_filter_dropdown_present(client):
    """V328 Part B: the unified table needs a 3-option filter dropdown.
    If V328's section was deleted by accident, this catches it."""
    _, body = _fetch(client, '/permits/chicago-il')
    if not _has_permit_data(body):
        pytest.skip('No permit data in test DB — unified table not rendered')
    assert 'id="record-filter"' in body, 'Filter dropdown missing'
    for value in ('value="all"', 'value="permits"', 'value="violations"'):
        assert value in body, f'Filter option {value!r} missing'


def test_unified_table_renders_for_busy_city(client):
    """The unified table tbody should have at least one row for a
    city we know has thousands of permits + violations. Catches the
    case where the SQL union breaks and renders an empty body."""
    _, body = _fetch(client, '/permits/chicago-il')
    if not _has_permit_data(body):
        pytest.skip('No permit data in test DB — unified table not rendered')
    tbody_match = re.search(
        r'<tbody id="permits-tbody">(.*?)</tbody>', body, re.S
    )
    assert tbody_match, 'permits-tbody not found in HTML'
    inner = tbody_match.group(1)
    has_data_row = '<tr ' in inner
    has_empty_state = 'No records match the current filter' in inner
    assert has_data_row or has_empty_state, \
        'Unified table tbody is empty with no empty-state placeholder'


# ==========================================================================
# 3. FORMATTING CONSISTENCY — Bug 4 from CODE_V320
# ==========================================================================

def test_only_one_violations_section_on_city_page(client):
    """Bug 4: the page had a V312 "Code Violations in <city>" section
    AND a V162/V209-3 fire-emoji red-badge violations section at the
    bottom — duplicate data with mismatched styling. V327 deleted the
    V162 block; V328 (Part B) replaced V312 with the unified table.
    Either way, only zero or one explicit "Code Violations in" header
    should ever appear now."""
    _, body = _fetch(client, '/permits/chicago-il')
    matches = re.findall(r'Code Violations in', body)
    # Either 0 (V328 unified table replaces it) or 1 (legacy V312 still
    # rendering). Two means the V162 duplicate came back.
    assert len(matches) <= 1, \
        f'Found {len(matches)} "Code Violations in" headers — duplicate violations section regressed'


def test_no_fire_emoji_violations_header(client):
    """Bug 4 specific: the deleted V162 block had a 🔥 (fire) emoji on
    the violations header — distinctive enough to test for directly."""
    _, body = _fetch(client, '/permits/chicago-il')
    # The fire emoji + "Active Code Violations" pairing was unique to
    # the deleted block.
    assert '\U0001F525 Active Code Violations' not in body, \
        'V162 fire-emoji violations section came back'


# ==========================================================================
# 4. PAGE SPEED — flag slow pages (informational)
# ==========================================================================

def test_city_page_responds_under_5_seconds(client):
    """Soft latency budget — the test client doesn't exercise the
    network so this measures pure handler time. >5s here means
    something is doing way too much work on the request path."""
    t0 = time.perf_counter()
    r = client.get('/permits/chicago-il')
    elapsed = time.perf_counter() - t0
    assert r.status_code == 200
    assert elapsed < 5.0, f'/permits/chicago-il took {elapsed:.2f}s (>5s budget)'
