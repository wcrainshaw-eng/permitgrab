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
]

@pytest.mark.parametrize('path', PUBLIC_ROUTES)
def test_public_route_responds(client, path):
    r = client.get(path)
    assert r.status_code < 500, f"{path} returned {r.status_code}"

def test_404(client):
    r = client.get('/this-definitely-does-not-exist-xyz')
    assert r.status_code == 404
