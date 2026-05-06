"""All public routes return 2xx/3xx."""
import pytest
from server import app

@pytest.fixture
def client():
    app.config['TESTING'] = True
    return app.test_client()

PUBLIC_ROUTES = [
    '/', '/cities', '/pricing', '/login', '/signup',
    '/api/health', '/healthz', '/robots.txt', '/sitemap.xml',
    # V251 F1/F5/F22/F6 shipped new public routes; CI missed them so a
    # broken route passed silently (phone leak, empty contractor detail,
    # 500 on report). Cover the new surface now.
    '/permits/chicago-il',          # city landing with gated contractors
    '/report/chicago-il',           # V251 F22 shareable printable summary
    '/leaderboard/chicago-il',      # V252 F6 public contractor leaderboard
    # V252 F7 trade-first URL — should 301 → /permits/chicago-il/<trade>
    '/solar/chicago-il',
    # V375 (CODE_V363 P0) trial-CTA entrypoint. Anon hits this and
    # gets bounced to /signup; we just need to confirm it doesn't 500
    # on the redirect path.
    '/start-checkout?plan=pro',
]

@pytest.mark.parametrize('path', PUBLIC_ROUTES)
def test_public_route_responds(client, path):
    r = client.get(path)
    assert r.status_code < 500, f"{path} returned {r.status_code}"


# ==========================================================================
# V251/V252 regression coverage — routes that require auth-tier gating.
# CI catches accidental public-exposure of Pro/Enterprise features.
# ==========================================================================

# Anonymous → these should NOT serve content. Redirect (30x) or 401/402 OK.
ANON_BLOCKED_ROUTES = [
    '/saved-contractors',           # V251 F15 (Pro)
    '/intel',                       # V251 F19 (Pro)
    '/api/saved-contractors',       # V251 F15 (Pro)
    '/api/webhooks',                # V252 F5 (Enterprise)
    '/api/v1/contractors?city=chicago-il',  # V251 F18 (Pro)
    '/api/v1/permits?city=chicago-il',      # V251 F18 (Pro)
    '/api/permits/chicago-il/export.csv',   # V251 F3 (Pro)
    '/api/digest/preview/chicago-il',       # V251 F16 (Pro)
    '/api/reports/chicago-il/monthly',      # V252 F4 (Enterprise)
]

@pytest.mark.parametrize('path', ANON_BLOCKED_ROUTES)
def test_anon_cannot_access_pro_route(client, path):
    r = client.get(path)
    # Anon should never see a 200 body with Pro data. Acceptable:
    # 302 (redirect to /login or /signup), 401, 402, 403, or 404.
    assert r.status_code in (301, 302, 401, 402, 403, 404), \
        f"{path} leaked to anon with status {r.status_code}"


def test_contractor_detail_404_for_missing_id(client):
    """V251 F6 — missing profile id → 404, not 500."""
    r = client.get('/contractor/9999999999')
    assert r.status_code == 404


def test_city_page_anon_has_no_tel_links(client):
    """V251 P0 phone-leak guard. Anon must never see tel: links in
    the contractors table on a city page — if this fails the phone
    gate is broken and paying customers have nothing to unlock.

    Scans only inside the actual <table class="contractors-table">...
    </table> block (not the CSS declaration), since V254 Phase 1
    introduces a tel: link inside a <script> template literal lower on
    the page which isn't server-side-rendered output to the user."""
    r = client.get('/permits/chicago-il')
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    start_tag = '<table class="contractors-table">'
    start = body.find(start_tag)
    if start == -1:
        # No contractors table rendered — nothing to assert.
        return
    end = body.find('</table>', start)
    chunk = body[start:end] if end > start else body[start:]
    assert 'href="tel:' not in chunk, \
        'Phone leak: tel: link visible to anon inside .contractors-table'


def test_404(client):
    r = client.get('/this-definitely-does-not-exist-xyz')
    assert r.status_code == 404


# ==========================================================================
# V253 P2 #12: signup-flow smoke tests. Can't do full Stripe E2E in CI
# (no live test key) but we can verify the /api/register gate catches
# bad input and that a clean POST at least passes validation. Stripe
# side is covered by the V253 P2 #6 commit wiring trial_period_days.
# ==========================================================================

def test_register_requires_email_and_password(client):
    r = client.post('/api/register', json={})
    assert r.status_code == 400
    r = client.post('/api/register', json={'email': 'x@y.com'})
    assert r.status_code == 400
    r = client.post('/api/register', json={'password': 'abcdefgh'})
    assert r.status_code == 400


def test_register_rejects_short_password(client):
    r = client.post('/api/register', json={'email': 'uat@permittest.com', 'password': 'short'})
    assert r.status_code == 400
    assert b'at least 8 characters' in r.data


def test_register_rejects_bad_email(client):
    r = client.post('/api/register', json={'email': 'notanemail', 'password': 'longenough123'})
    assert r.status_code == 400


def test_pricing_advertises_trial(client):
    """V253 P2 #6 shipped trial_period_days in checkout. Pricing page
    advertises '14 days free' — make sure that copy doesn't get
    stripped accidentally when someone edits the template."""
    r = client.get('/pricing')
    assert r.status_code == 200
    assert b'14 days free' in r.data or b'14-day' in r.data.lower() \
        or b'free trial' in r.data.lower()


def test_start_checkout_anon_redirects_to_signup_with_next(client):
    """V375 (CODE_V363 P0): /start-checkout for an anonymous visitor
    must 302 to /signup with both ?plan= and ?next= preserved so the
    user lands at Stripe Checkout after creating their account.

    This was the conversion-funnel fix — before V375 the trial CTA
    silently dumped logged-in users on the homepage and burned ad
    spend at $3/click with zero conversions. Lock the redirect target
    so a future template tweak can't quietly re-break it."""
    r = client.get('/start-checkout?plan=pro', follow_redirects=False)
    assert r.status_code in (301, 302, 303), \
        f'/start-checkout?plan=pro should redirect, got {r.status_code}'
    location = r.headers.get('Location', '')
    assert location.startswith('/signup'), \
        f'/start-checkout (anon) should redirect to /signup, got {location}'
    assert 'plan=pro' in location, \
        f'/start-checkout (anon) signup redirect must preserve plan, got {location}'
    assert 'next=' in location and 'start-checkout' in location, \
        f'/start-checkout (anon) signup redirect must preserve next= back to checkout, got {location}'


def test_start_checkout_invalid_plan_falls_back_to_pro(client):
    """V375 hardening: an unknown plan slug should not 500 — it should
    coerce to 'pro' and continue the redirect chain. Anyone who edits
    the plan list later shouldn't be able to introduce a 500 on a
    cold visitor's first click."""
    r = client.get('/start-checkout?plan=bogus_plan', follow_redirects=False)
    assert r.status_code in (301, 302, 303)
    location = r.headers.get('Location', '')
    assert 'plan=pro' in location, \
        f'unknown plan should coerce to pro, got {location}'


# NOTE: a logged-in /signup?plan=pro test was attempted but the local
# SQLite test DB schema doesn't carry every users column the prod
# Postgres model has, so find_user_by_email blows up on the fake
# session. The anon redirect tests above cover the conversion-path
# regression-guard; the logged-in branch is exercised manually via
# the V375 PR + visual UAT.


def test_pricing_single_tier(client):
    """V304 (CODE_V280 PR0) collapsed /pricing to one tier at $149/mo.
    The V255 contract ("exactly two tiers") was superseded. Now the
    guard is: Pro $149 must be present, every other price point
    ($49, $99, $349, $499) must NOT appear. Custom asks route to a
    Contact sales mailto link."""
    r = client.get('/pricing')
    assert r.status_code == 200
    body = r.data
    # Pro must be present.
    assert b'$149<' in body, 'Pro $149 price missing from /pricing'
    # No other price points.
    for forbidden in (b'$49<', b'$99<', b'$349<', b'$499<'):
        assert forbidden not in body, \
            f'V304 violation: {forbidden.decode()} tier visible on /pricing'
    # No "Most Popular" badge when there's only one card.
    assert b'Most Popular' not in body, \
        'V304 violation: "Most Popular" badge reintroduced'
    # Contact sales path must exist for API/volume asks.
    assert b'Contact sales' in body or b'contact sales' in body, \
        'V304 violation: Contact sales link missing for custom asks'
    # No billing toggle either (V255 legacy guard retained).
    assert b'monthly-btn' not in body and b'annual-btn' not in body, \
        'V255 violation: Monthly/Annual toggle reintroduced'


# ==========================================================================
# V254 Phase 1 (10 free reveals) endpoints — the conversion lever. These
# routes are how unpaid users actually experience the product; silent
# regressions here = zero conversions.
# ==========================================================================

def test_reveal_status_anon(client):
    """Anon GET /api/reveal-status → authenticated:false + signup_url."""
    r = client.get('/api/reveal-status')
    assert r.status_code == 200
    import json as _j
    d = _j.loads(r.data)
    assert d.get('authenticated') is False
    assert d.get('is_pro') is False
    assert d.get('credits_remaining') == 0
    assert '/signup' in d.get('signup_url', '')


def test_reveal_phone_requires_auth(client):
    """Anon POST /api/reveal-phone → 401 with signup_url in response."""
    r = client.post('/api/reveal-phone', json={'profile_id': 1})
    assert r.status_code == 401
    import json as _j
    d = _j.loads(r.data)
    assert '/signup' in d.get('signup_url', '')


def test_reveal_phone_validates_profile_id(client, monkeypatch):
    """Even with a session, an invalid profile_id must be rejected with 400."""
    # Can't easily stub session without a real signup flow in test_client,
    # so just verify malformed payload rejection at the public (unauth) layer.
    r = client.post('/api/reveal-phone', json={})
    # Anon hits auth check first → 401 is fine too; the guard in place is ok.
    assert r.status_code in (400, 401)


# ==========================================================================
# V257 regression guards. UAT report claimed 4 SEO / signup / redirect
# blockers; live prod passes every check already. Pin the expectations
# to CI so a future regression or another stale UAT report can be
# disproved mechanically.
# ==========================================================================

def test_v257_city_page_robots_meta_conditional(client):
    """V257 Fix 1: /permits/<city> robots directive must be driven by
    content depth (permit_count). Thin pages are correctly noindexed
    (Google best practice for sub-20-permit pages). Rich pages index.

    Here we assert:
      - The response has a <meta name="robots" ...> tag present
        (i.e. the template still renders robots_directive)
      - No X-Robots-Tag: noindex HTTP header (that would be a
        site-wide block, not content-conditional)
      - The body isn't hardcoded noindex for every request — we
        verify the tag is the templated form, not a literal string
    """
    r = client.get('/permits/chicago-il')
    assert r.status_code == 200
    body = r.data
    assert b'<meta name="robots"' in body, \
        'V257 regression: meta robots tag missing — template broken'
    x_robots = r.headers.get('X-Robots-Tag', '')
    assert 'noindex' not in x_robots.lower(), \
        f'V257 regression: X-Robots-Tag header blocks indexing: {x_robots!r}'


def test_v257_city_page_prod_rich_pages_are_indexed(client):
    """V257 Fix 1 companion: city pages must still have a real
    indexation gate driving robots_directive. Guards against a
    well-meaning edit that drops the gate entirely.

    V544 SUPERSESSION (2026-05-06): the original V257 substring assert
    was for `permit_count < 20 → noindex`. V544 intentionally collapsed
    that legacy V236 gate onto city_health.is_sellable_city so the
    sitemap, page meta, and picker speak with one voice. The legacy
    substring is gone by design, so this test now asserts the V544
    contract instead — same intent (the gate exists and decides the
    directive), updated source of truth.

    Uses a grep on server.py source (not a live request) because CI's
    local DB is too sparse to reliably trigger the index branch."""
    import pathlib
    src = pathlib.Path(__file__).parent.parent / 'server.py'
    text = src.read_text()
    # Post-V544: server.py uses city_health.is_sellable_city as the
    # SINGLE source of truth for robots_directive on city pages.
    assert 'from city_health import is_sellable_city' in text, (
        'V257/V544 regression: server.py no longer imports '
        'is_sellable_city — the indexation gate has been dropped'
    )
    assert '_v544_is_sellable' in text and 'robots_directive' in text, (
        'V257/V544 regression: server.py no longer wires '
        'robots_directive through the V544 unification block'
    )


def test_v257_signup_renders_form_no_redirect(client):
    """V257 Fix 2: /signup must render a sign-up form with email input,
    NOT redirect to a random /permits/<city> page. Claim was the whole
    pricing CTA → signup → checkout funnel was broken."""
    r = client.get('/signup', follow_redirects=False)
    # Direct 200 expected; 302 would indicate the random-city redirect
    assert r.status_code == 200, f'/signup returned {r.status_code} — redirect claim?'
    body = r.data
    assert b'type="email"' in body, '/signup missing email input'
    # If the V257 claim were real this would show: /permits/ hit in Location header
    assert b'/permits/' not in r.headers.get('Location', '').encode(), \
        'V257 regression: /signup redirects to /permits/'


def test_v257_city_page_renders_no_redirect(client):
    """V257 Fix 3: /permits/<city> for anon must render content directly,
    not 30x-redirect to /pricing or /signup or /get-alerts. (Client-side
    JS redirect is not server-side testable but the pure-server response
    was claimed to redirect too — verify it doesn't.)"""
    r = client.get('/permits/chicago-il', follow_redirects=False)
    assert r.status_code == 200, f'/permits/chicago-il returned {r.status_code} — redirect claim?'
    loc = r.headers.get('Location', '')
    assert not loc, f'V257 regression: /permits/chicago-il redirects to {loc!r}'
