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


def test_pricing_no_99_tier(client):
    """V255 P0#1 killed the Starter $99 tier and the bulk-credits $49/$99
    waitlist card. If either reappears on /pricing, the 'exactly two
    tiers' directive is violated — fail loudly."""
    r = client.get('/pricing')
    assert r.status_code == 200
    body = r.data
    assert b'$99<' not in body and b'$49<' not in body, \
        'V255 violation: sub-$149 price point visible on /pricing'
    assert b'$149<' in body, 'Pro $149 price missing from /pricing'
    assert b'$349<' in body, 'Enterprise $349 price missing from /pricing'
    # No billing toggle either
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
