"""
V114: Catalog-First Mass Sweep — Socrata Discovery + ArcGIS Hub

Sweeps public catalogs for ALL building permit datasets, matches them to cities.
This is the fastest path to 2,500 cities — one sweep discovers hundreds of sources.

Usage: POST /api/admin/sweep-catalogs
"""

import json
import re
import requests
import time
from datetime import datetime, timedelta

import db as permitdb


def _log(msg):
    print(msg, flush=True)


# ================================================================
# SOCRATA CATALOG SWEEP
# ================================================================

def sweep_socrata_catalog():
    """Sweep Socrata Discovery API for all building permit datasets."""
    _log("[SWEEP] Starting Socrata catalog sweep...")
    conn = permitdb.get_connection()

    # Ensure sweep_sources table exists
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sweep_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city_slug TEXT NOT NULL,
            source_type TEXT NOT NULL,
            source_url TEXT NOT NULL,
            platform TEXT NOT NULL,
            dataset_id TEXT DEFAULT '',
            domain TEXT DEFAULT '',
            name TEXT DEFAULT '',
            city_column TEXT DEFAULT '',
            city_value TEXT DEFAULT '',
            status TEXT DEFAULT 'pending_test',
            permits_found INTEGER DEFAULT 0,
            discovered_at TEXT DEFAULT '',
            tested_at TEXT DEFAULT '',
            UNIQUE(city_slug, dataset_id)
        )
    """)
    conn.commit()

    # Paginate through all Socrata catalog results
    all_datasets = []
    offset = 0
    while True:
        try:
            resp = requests.get("https://api.us.socrata.com/api/catalog/v1",
                                params={'q': 'building permits', 'only': 'datasets',
                                        'limit': 100, 'offset': offset},
                                timeout=15)
            if resp.status_code != 200:
                break
            data = resp.json()
            results = data.get('results', [])
            total = data.get('resultSetSize', 0)
            all_datasets.extend(results)
            _log(f"[SWEEP] Socrata: fetched {len(all_datasets)}/{total} datasets")
            if len(results) < 100:
                break
            offset += 100
            time.sleep(1)
        except Exception as e:
            _log(f"[SWEEP] Socrata catalog error: {e}")
            break

    _log(f"[SWEEP] Socrata: {len(all_datasets)} total datasets found")

    # Process each dataset
    matched = 0
    for ds in all_datasets:
        try:
            resource = ds.get('resource', {})
            metadata = ds.get('metadata', {})
            domain = metadata.get('domain', '')
            dataset_id = resource.get('id', '')
            name = resource.get('name', '')
            columns = [c.lower() for c in resource.get('columns_name', [])]

            if not dataset_id:
                continue

            # City columns for multi-city datasets
            city_columns = [c for c in columns if any(
                kw in c for kw in ['city', 'municipality', 'town', 'jurisdiction', 'community']
            )]

            # Try to match domain to a specific city
            city_match = _match_domain_to_city(domain, conn)

            if city_match:
                _process_single_city_dataset(conn, city_match, domain, dataset_id, name, columns)
                matched += 1
            elif city_columns:
                _process_multi_city_dataset(conn, domain, dataset_id, name, city_columns[0])
                matched += 1
        except Exception as e:
            _log(f"[SWEEP] Error processing dataset {dataset_id}: {e}")
            continue

    _log(f"[SWEEP] Socrata: processed {matched} relevant datasets")
    return matched


def _match_domain_to_city(domain, conn):
    """Match a Socrata domain to a city in prod_cities."""
    domain_lower = domain.lower()
    patterns = [
        r'data\.(\w+)\.gov',
        r'data\.cityof(\w+)\.',
        r'(\w+)data\.',
        r'(\w+)\.data\.socrata\.com',
    ]
    for pattern in patterns:
        m = re.search(pattern, domain_lower)
        if m:
            slug_guess = m.group(1)
            row = conn.execute(
                "SELECT city_slug, city, state FROM prod_cities WHERE city_slug LIKE ? AND status = 'active' LIMIT 1",
                (f'{slug_guess}%',)
            ).fetchone()
            if row:
                return {'slug': row[0], 'city_name': row[1], 'state': row[2]}
    return None


def _process_single_city_dataset(conn, city_match, domain, dataset_id, name, columns):
    """Record a city-specific Socrata dataset."""
    slug = city_match['slug']

    # Skip if already discovered
    existing = conn.execute(
        "SELECT 1 FROM sweep_sources WHERE city_slug = ? AND dataset_id = ?",
        (slug, dataset_id)
    ).fetchone()
    if existing:
        return

    # Must have permit-like columns
    permit_cols = [c for c in columns if any(
        kw in c for kw in ['permit', 'address', 'parcel', 'contractor', 'applicant', 'valuation', 'fee']
    )]
    if len(permit_cols) < 2:
        return

    # Quick test — does it return data?
    try:
        resp = requests.get(f"https://{domain}/resource/{dataset_id}.json",
                            params={'$limit': 5}, timeout=10)
        if resp.status_code == 200 and len(resp.json()) > 0:
            _log(f"[SWEEP] CONFIRMED: {city_match['city_name']} — {name} on {domain}")
            conn.execute("""
                INSERT OR IGNORE INTO sweep_sources
                (city_slug, source_type, source_url, platform, dataset_id, domain, name,
                 status, discovered_at)
                VALUES (?, 'socrata', ?, 'socrata', ?, ?, ?, 'confirmed', datetime('now'))
            """, (slug, f"https://{domain}/resource/{dataset_id}.json", dataset_id, domain, name))
            conn.commit()
    except Exception:
        pass


def _process_multi_city_dataset(conn, domain, dataset_id, name, city_column):
    """Process a state/county Socrata dataset — enumerate cities."""
    _log(f"[SWEEP] Multi-city: {domain}/{dataset_id} ({name}), city_col={city_column}")

    try:
        resp = requests.get(f"https://{domain}/resource/{dataset_id}.json",
                            params={'$select': f'distinct {city_column} as city_name',
                                    '$limit': 500, '$order': f'{city_column} ASC'},
                            timeout=15)
        if resp.status_code != 200:
            return

        cities_in_dataset = resp.json()
        city_names = [r.get('city_name', '').strip() for r in cities_in_dataset if r.get('city_name')]
        _log(f"[SWEEP] {dataset_id}: {len(city_names)} unique cities")

        new_count = 0
        for cn in city_names:
            if not cn or len(cn) < 3:
                continue
            row = conn.execute(
                "SELECT city_slug, city, state, total_permits FROM prod_cities WHERE upper(city) = upper(?) AND status = 'active' LIMIT 1",
                (cn,)
            ).fetchone()

            if row and (not row[3] or row[3] == 0):
                slug = row[0]
                conn.execute("""
                    INSERT OR IGNORE INTO sweep_sources
                    (city_slug, source_type, source_url, platform, dataset_id, domain, name,
                     city_column, city_value, status, discovered_at)
                    VALUES (?, 'socrata_state', ?, 'socrata', ?, ?, ?, ?, ?, 'pending_test', datetime('now'))
                """, (slug, f"https://{domain}/resource/{dataset_id}.json",
                      dataset_id, domain, name, city_column, cn))
                new_count += 1

        if new_count:
            conn.commit()
            _log(f"[SWEEP] {dataset_id}: {new_count} new city matches")

    except Exception as e:
        _log(f"[SWEEP] Error processing multi-city {dataset_id}: {e}")


# ================================================================
# ARCGIS HUB SWEEP
# ================================================================

def sweep_arcgis_hub():
    """Sweep ArcGIS Hub for building permit Feature Services."""
    _log("[SWEEP] Starting ArcGIS Hub sweep...")
    conn = permitdb.get_connection()

    # Ensure table exists
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sweep_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city_slug TEXT NOT NULL, source_type TEXT NOT NULL, source_url TEXT NOT NULL,
            platform TEXT NOT NULL, dataset_id TEXT DEFAULT '', domain TEXT DEFAULT '',
            name TEXT DEFAULT '', city_column TEXT DEFAULT '', city_value TEXT DEFAULT '',
            status TEXT DEFAULT 'pending_test', permits_found INTEGER DEFAULT 0,
            discovered_at TEXT DEFAULT '', tested_at TEXT DEFAULT '',
            UNIQUE(city_slug, dataset_id)
        )
    """)
    conn.commit()

    # Cache top cities for matching
    cities = conn.execute(
        "SELECT city_slug, city, state FROM prod_cities WHERE population >= 50000 AND status = 'active' ORDER BY population DESC"
    ).fetchall()
    city_lookup = [(r[0], r[1].lower(), r[2]) for r in cities]

    all_services = []
    page = 1
    while len(all_services) < 2000:
        try:
            resp = requests.get("https://hub.arcgis.com/api/v3/search",
                                params={'q': 'building permits', 'filter[type]': 'Feature Service',
                                        'page[size]': 100, 'page[number]': page},
                                timeout=15)
            if resp.status_code != 200:
                break
            data = resp.json()
            results = data.get('data', [])
            if not results:
                break
            all_services.extend(results)
            _log(f"[SWEEP] ArcGIS: fetched {len(all_services)} services")
            if len(results) < 100:
                break
            page += 1
            time.sleep(1)
        except Exception as e:
            _log(f"[SWEEP] ArcGIS Hub error: {e}")
            break

    _log(f"[SWEEP] ArcGIS: {len(all_services)} Feature Services found")

    matched = 0
    for svc in all_services:
        attrs = svc.get('attributes', {})
        name = attrs.get('name', '')
        owner = attrs.get('owner', '')
        svc_url = attrs.get('url', '')

        if not svc_url:
            continue
        name_lower = name.lower()
        if not any(kw in name_lower for kw in ['permit', 'building', 'construction']):
            continue
        if any(kw in name_lower for kw in ['template', 'test', 'sample', 'demo']):
            continue

        # Match to city
        text = (svc_url + ' ' + owner + ' ' + name).lower()
        for slug, city_lower, state in city_lookup:
            if city_lower in text:
                # Check not already discovered
                existing = conn.execute(
                    "SELECT 1 FROM sweep_sources WHERE city_slug = ? AND source_url = ?",
                    (slug, svc_url)
                ).fetchone()
                if not existing:
                    has_data = conn.execute(
                        "SELECT total_permits FROM prod_cities WHERE city_slug = ?", (slug,)
                    ).fetchone()
                    if has_data and (not has_data[0] or has_data[0] == 0):
                        _log(f"[SWEEP] ArcGIS match: {slug} — {name}")
                        conn.execute("""
                            INSERT OR IGNORE INTO sweep_sources
                            (city_slug, source_type, source_url, platform, name,
                             status, discovered_at)
                            VALUES (?, 'arcgis', ?, 'arcgis', ?, 'pending_test', datetime('now'))
                        """, (slug, svc_url, name))
                        matched += 1
                break  # Only match first city

    conn.commit()
    _log(f"[SWEEP] ArcGIS: {matched} new city sources discovered")
    return matched


# ================================================================
# TEST + ACTIVATE DISCOVERED SOURCES
# ================================================================

def test_sweep_sources(limit=100):
    """Test pending discovered sources and do 6-month pull for confirmed ones."""
    conn = permitdb.get_connection()

    pending = conn.execute("""
        SELECT id, city_slug, source_type, source_url, platform,
               dataset_id, domain, city_column, city_value, name
        FROM sweep_sources
        WHERE status = 'pending_test'
        LIMIT ?
    """, (limit,)).fetchall()

    _log(f"[TEST] Testing {len(pending)} discovered sources...")
    confirmed = 0

    for row in pending:
        src_id = row[0]
        slug, src_type, url, platform = row[1], row[2], row[3], row[4]
        dataset_id, domain, city_col, city_val, name = row[5], row[6], row[7], row[8], row[9]

        try:
            if platform == 'socrata':
                count = _test_and_pull_socrata(conn, slug, url, dataset_id, domain,
                                               city_col, city_val)
            elif platform == 'arcgis':
                count = _test_and_pull_arcgis(conn, slug, url)
            else:
                count = 0

            status = 'confirmed' if count > 0 else 'no_data'
            conn.execute("""
                UPDATE sweep_sources SET status = ?, permits_found = ?, tested_at = datetime('now')
                WHERE id = ?
            """, (status, count, src_id))
            conn.commit()

            if count > 0:
                confirmed += 1
                _log(f"[TEST] SUCCESS: {slug} — {count} permits from {platform}")

        except Exception as e:
            _log(f"[TEST] Error testing {slug}: {e}")
            conn.execute("UPDATE sweep_sources SET status = 'error', tested_at = datetime('now') WHERE id = ?", (src_id,))
            conn.commit()

        time.sleep(0.5)

    _log(f"[TEST] Complete: {confirmed}/{len(pending)} sources confirmed with data")
    return confirmed


def _test_and_pull_socrata(conn, slug, url, dataset_id, domain, city_col, city_val):
    """Test a Socrata source and pull 6 months if it has data."""
    six_months_ago = (datetime.now() - timedelta(days=180)).strftime('%Y-%m-%dT00:00:00')
    where = f":created_at >= '{six_months_ago}'"
    if city_col and city_val:
        where = f"upper({city_col}) = '{city_val.upper()}'"

    try:
        resp = requests.get(url, params={'$where': where, '$limit': 5000, '$order': ':id'},
                            timeout=30)
        if resp.status_code != 200 or not resp.json():
            return 0

        records = resp.json()
        if len(records) < 5:
            return 0

        # Get city info
        city_row = conn.execute("SELECT city, state, id FROM prod_cities WHERE city_slug = ?", (slug,)).fetchone()
        if not city_row:
            return 0
        city_name, state, pc_id = city_row[0], city_row[1], city_row[2]

        # Auto-detect fields and insert
        col_names = list(records[0].keys())
        from city_onboarding import _auto_detect_fields, _parse_date
        field_map = _auto_detect_fields(col_names, {})

        inserted = 0
        for record in records:
            try:
                permit_num = str(record.get(field_map.get('permit_number_field', ''), ''))
                date_raw = record.get(field_map.get('date_field', ''), '')
                address = str(record.get(field_map.get('address_field', ''), ''))[:200]
                desc = str(record.get(field_map.get('description_field', ''), ''))[:500]

                permit_date = _parse_date(date_raw)
                if not permit_date:
                    continue
                if not permit_num:
                    permit_num = f"SOC-{slug[:3].upper()}-{hash(str(record)) & 0xFFFFFFFF:08x}"

                conn.execute("""
                    INSERT OR IGNORE INTO permits
                    (permit_number, city, state, address, description, filing_date, date,
                     source_city_key, prod_city_id, collected_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """, (permit_num, city_name, state, address, desc,
                      permit_date, permit_date, slug, pc_id))
                inserted += 1
            except Exception:
                continue

        conn.commit()

        if inserted > 0:
            conn.execute("""
                UPDATE prod_cities SET total_permits = total_permits + ?,
                health_status = 'collecting', source_type = 'socrata',
                source_id = ?, last_successful_collection = datetime('now')
                WHERE city_slug = ?
            """, (inserted, f"{domain}:{dataset_id}", slug))
            conn.commit()

        return inserted

    except Exception as e:
        _log(f"[PULL] Socrata error for {slug}: {e}")
        return 0


def _test_and_pull_arcgis(conn, slug, url):
    """Test an ArcGIS source and pull if it has data."""
    base_url = url.rstrip('/')
    if '/query' not in base_url:
        if not base_url.endswith('/0'):
            base_url += '/0'
        base_url += '/query'

    try:
        resp = requests.get(base_url,
                            params={'where': '1=1', 'outFields': '*',
                                    'resultRecordCount': 2000, 'f': 'json'},
                            timeout=30)
        if resp.status_code != 200:
            return 0
        data = resp.json()
        features = data.get('features', [])
        if len(features) < 5:
            return 0

        city_row = conn.execute("SELECT city, state, id FROM prod_cities WHERE city_slug = ?", (slug,)).fetchone()
        if not city_row:
            return 0
        city_name, state, pc_id = city_row[0], city_row[1], city_row[2]

        # Find date and address fields
        field_names = list(features[0].get('attributes', {}).keys()) if features else []

        def find_field(patterns):
            for p in patterns:
                for fn in field_names:
                    if p in fn.lower():
                        return fn
            return None

        date_field = find_field(['issued', 'date', 'created', 'applied'])
        address_field = find_field(['address', 'location', 'street'])
        permit_field = find_field(['permit', 'record', 'case'])

        inserted = 0
        for feature in features:
            try:
                attrs = feature.get('attributes', {})
                date_val = attrs.get(date_field) if date_field else None
                if isinstance(date_val, (int, float)) and date_val > 0:
                    permit_date = datetime.utcfromtimestamp(date_val / 1000).strftime('%Y-%m-%d')
                else:
                    continue

                permit_num = str(attrs.get(permit_field, '')) if permit_field else ''
                if not permit_num:
                    permit_num = f"ARC-{slug[:3].upper()}-{hash(str(attrs)) & 0xFFFFFFFF:08x}"
                address = str(attrs.get(address_field, ''))[:200] if address_field else ''

                conn.execute("""
                    INSERT OR IGNORE INTO permits
                    (permit_number, city, state, address, filing_date, date,
                     source_city_key, prod_city_id, collected_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """, (permit_num, city_name, state, address,
                      permit_date, permit_date, slug, pc_id))
                inserted += 1
            except Exception:
                continue

        conn.commit()

        if inserted > 0:
            conn.execute("""
                UPDATE prod_cities SET total_permits = total_permits + ?,
                health_status = 'collecting', source_type = 'arcgis',
                source_id = ?, last_successful_collection = datetime('now')
                WHERE city_slug = ?
            """, (inserted, url, slug))
            conn.commit()

        return inserted

    except Exception as e:
        _log(f"[PULL] ArcGIS error for {slug}: {e}")
        return 0
