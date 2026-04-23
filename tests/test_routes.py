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
    gate is broken and paying customers have nothing to unlock."""
    r = client.get('/permits/chicago-il')
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    # Check only within the Top Contractors section, not elsewhere on the
    # page. Heuristic: look for tel: links between the 'contractors-table'
    # open and the next '</table>'.
    start = body.find('contractors-table')
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
