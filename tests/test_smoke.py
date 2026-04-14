"""Fast smoke tests — must pass in < 30 seconds. Run before every push."""
import pytest
from server import app
import json
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
    assert b'V' in r.data  # version string present

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

def test_admin_query_requires_auth(client):
    r = client.post('/api/admin/query', json={'sql':'SELECT 1'})
    assert r.status_code in (401, 403)

def test_admin_query_works_with_key(client):
    key = os.environ.get('ADMIN_KEY', '122f635f639857bd9296150ba2e64419')
    r = client.post('/api/admin/query',
        json={'sql':'SELECT 1 as n'},
        headers={'X-Admin-Key': key})
    assert r.status_code == 200
    data = r.get_json()
    assert data['rows'][0]['n'] == 1

def test_admin_query_rejects_non_select(client):
    key = os.environ.get('ADMIN_KEY', '122f635f639857bd9296150ba2e64419')
    r = client.post('/api/admin/query',
        json={'sql':'DELETE FROM permits'},
        headers={'X-Admin-Key': key})
    assert r.status_code in (400, 403) or 'error' in (r.get_json() or {})
