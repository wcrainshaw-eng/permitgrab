"""DB schema integrity tests. Runs against real DB (local or prod)."""
import sqlite3, os, pytest

DB_PATH = os.environ.get('DB_PATH', '/var/data/permitgrab.db')

@pytest.fixture
def conn():
    if not os.path.exists(DB_PATH):
        pytest.skip(f"DB not found at {DB_PATH}")
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    yield c
    c.close()

def test_required_tables_exist(conn):
    required = ['prod_cities', 'permits', 'violations', 'subscribers',
                'city_sources', 'us_cities', 'scraper_runs']
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    names = {r['name'] for r in rows}
    missing = [t for t in required if t not in names]
    assert not missing, f"Missing tables: {missing}"

def test_prod_cities_has_data(conn):
    n = conn.execute("SELECT COUNT(*) FROM prod_cities").fetchone()[0]
    assert n > 100, f"prod_cities only has {n} rows"

def test_violations_table_shape(conn):
    cols = [r[1] for r in conn.execute("PRAGMA table_info(violations)").fetchall()]
    required = ['prod_city_id', 'source_violation_id', 'violation_date',
                'violation_description', 'raw_data', 'collected_at']
    for c in required:
        assert c in cols, f"violations missing column: {c}"
