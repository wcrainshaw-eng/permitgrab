"""Fast smoke tests — must pass in < 30 seconds. Run before every push."""
import pytest
from server import app
import os

@pytest.fixture
def client():
    app.config['TESTING'] = True
    return app.test_client()

def test_app_imports():
    """If this fails, server.py is broken at import time."""
    from server import app
    assert app is not None

def test_no_duplicate_flask_endpoints():
    """Catches the V162 'api_violations' duplicate-route bug."""
    seen = set()
    for rule in app.url_map.iter_rules():
        assert rule.endpoint not in seen, f"Duplicate endpoint: {rule.endpoint}"
        seen.add(rule.endpoint)

def test_health_endpoint(client):
    r = client.get('/api/health')
    assert r.status_code == 200
    assert b'V' in r.data

def test_healthz_endpoint(client):
    """V167 lightweight probe."""
    r = client.get('/healthz')
    assert r.status_code == 200

def test_homepage(client):
    r = client.get('/')
    assert r.status_code == 200
    assert b'PermitGrab' in r.data

def test_cities_page(client):
    r = client.get('/cities')
    assert r.status_code == 200

def test_robots_and_sitemap(client):
    assert client.get('/robots.txt').status_code == 200
    assert client.get('/sitemap.xml').status_code == 200

def test_diagnostics_requires_auth(client):
    r = client.get('/api/diagnostics')
    assert r.status_code in (401, 403)

def test_saved_searches_requires_auth(client):
    """Anon users get 401 on saved-searches endpoints."""
    r = client.post('/api/saved-searches', json={'name': 'test'})
    assert r.status_code in (401, 403)

def test_saved_searches_route_registered():
    """Confirm the route exists in the URL map."""
    from server import app
    paths = {rule.rule for rule in app.url_map.iter_rules()}
    assert '/api/saved-searches' in paths

@pytest.mark.parametrize('slug', ['houston', 'new-york-city', 'chicago-il', 'los-angeles'])
def test_city_pages_render(client, slug):
    """V176: City pages must render without 500 errors."""
    r = client.get(f'/permits/{slug}')
    assert r.status_code < 500, f'/permits/{slug} returned {r.status_code}'

def test_unsubscribe_route_exists():
    """V170 C3: Unsubscribe route registered."""
    from server import app
    paths = {rule.rule for rule in app.url_map.iter_rules()}
    assert '/unsubscribe/<int:search_id>' in paths

def test_admin_run_daily_alerts_requires_auth(client):
    """V170 C3: Daily alerts trigger requires admin key."""
    r = client.post('/api/admin/run-daily-alerts')
    assert r.status_code in (401, 403)

def test_admin_query_rejects_non_select(client):
    key = os.environ.get('ADMIN_KEY', '122f635f639857bd9296150ba2e64419')
    r = client.post('/api/admin/query',
        json={'sql': 'DELETE FROM permits'},
        headers={'X-Admin-Key': key})
    body = r.get_json() or {}
    assert r.status_code in (400, 403) or 'error' in body
