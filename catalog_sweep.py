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


# V115: Quality filters
BAD_CITY_COLUMNS = {'contractorcity', 'contractor_city', 'applicantcity', 'applicant_city',
                     'contact_city', 'contactcity', 'owner_city', 'ownercity',
                     'mailing_city', 'mailingcity', 'billing_city', 'agent_city', ''}

GOOD_CITY_COLUMNS = {'city', 'municipality', 'town', 'jurisdiction', 'communityname',
                      'community', 'community_name', 'originalcity', 'original_city',
                      'city_name', 'cityname', 'municipal', 'muni', 'location_city',
                      'property_city'}

SKIP_DOMAINS = {'data.calgary.ca', 'data.edmonton.ca', 'data.winnipeg.ca', 'data.surrey.ca',
                'data.gov.au', 'data.gov.uk', 'austin-metro.demo.socrata.com',
                'health.data.ny.gov'}

EXCLUDE_KEYWORDS = ['elevator permit', 'child care', 'food permit', 'health permit',
                    'liquor permit', 'taxi permit', 'film permit', 'parking permit',
                    'street permit', 'right-of-way', 'sidewalk cafe', 'special event',
                    'vendor permit', 'cannabis', 'marijuana', 'liquor', 'tobacco',
                    'key economic', 'economic indicator']


def _is_valid_domain(domain):
    """V115: Skip non-US and demo domains."""
    d = domain.lower()
    if d in SKIP_DOMAINS:
        return False
    if d.endswith('.ca') and not d.endswith('.ca.gov'):
        return False
    return True


def _is_building_permit_dataset(name, description=''):
    """V115: Check if dataset is about building/construction permits."""
    text = (name + ' ' + description).lower()
    if any(kw in text for kw in EXCLUDE_KEYWORDS):
        return False
    return 'permit' in text or 'construction' in text or 'building' in text


def _v115_cleanup():
    """V115: One-time purge of junk sweep data."""
    conn = permitdb.get_connection()
    if conn.execute("SELECT 1 FROM system_state WHERE key='v115_cleanup_done'").fetchone():
        return
    _log("[V115] Starting data cleanup...")

    # Delete bad city column entries
    placeholders = ','.join(['?' for _ in BAD_CITY_COLUMNS])
    d1 = conn.execute(f"DELETE FROM sweep_sources WHERE city_column IN ({placeholders})",
                       list(BAD_CITY_COLUMNS)).rowcount

    # Delete non-US domains
    d2 = 0
    for domain in SKIP_DOMAINS:
        d2 += conn.execute("DELETE FROM sweep_sources WHERE domain = ?", (domain,)).rowcount

    # Delete junk permits (SOC- prefix from sweep, matching bad dataset IDs)
    d3 = conn.execute("DELETE FROM permits WHERE permit_number LIKE 'SOC-%' AND source_city_key IN (SELECT city_slug FROM sweep_sources WHERE status = 'error' OR city_column IN ('contractorcity','applicant_city','contact_city'))").rowcount

    conn.execute("INSERT OR REPLACE INTO system_state (key, value) VALUES ('v115_cleanup_done', '1')")
    conn.commit()
    _log(f"[V115] Cleanup: {d1} bad column entries, {d2} bad domain entries, {d3} junk permits deleted")


# ================================================================
# SOCRATA CATALOG SWEEP
# ================================================================

def sweep_socrata_catalog():
    """Sweep Socrata Discovery API for all building permit datasets."""
    _v115_cleanup()  # Purge junk from previous runs
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

    # V116: Multiple search terms to find more datasets
    SEARCH_TERMS = ['building permits', 'construction permits', 'code enforcement',
                    'zoning permits', 'demolition permits', 'certificate of occupancy',
                    'residential permits']

    all_datasets = []
    seen_ids = set()
    for search_term in SEARCH_TERMS:
        offset = 0
        term_count = 0
        while True:
            try:
                resp = requests.get("https://api.us.socrata.com/api/catalog/v1",
                                    params={'q': search_term, 'only': 'datasets',
                                            'limit': 100, 'offset': offset},
                                    timeout=15)
                if resp.status_code != 200:
                    break
                data = resp.json()
                results = data.get('results', [])
                total = data.get('resultSetSize', 0)
                for r in results:
                    ds_id = r.get('resource', {}).get('id', '')
                    if ds_id and ds_id not in seen_ids:
                        seen_ids.add(ds_id)
                        all_datasets.append(r)
                        term_count += 1
                if len(results) < 100:
                    break
                offset += 100
                time.sleep(0.5)
            except Exception as e:
                _log(f"[SWEEP] Socrata error for '{search_term}': {e}")
                break
        _log(f"[SWEEP] '{search_term}': {term_count} new datasets (total unique: {len(all_datasets)})")

    _log(f"[SWEEP] Socrata: {len(all_datasets)} unique datasets across {len(SEARCH_TERMS)} search terms")

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

            # V115: Skip non-US/demo domains
            if not _is_valid_domain(domain):
                continue

            # V115: Skip non-building-permit datasets
            description = resource.get('description', '')
            if not _is_building_permit_dataset(name, description):
                continue

            # City columns for multi-city datasets
            city_columns = [c for c in columns if any(
                kw in c for kw in ['city', 'municipality', 'town', 'jurisdiction', 'community']
            )]
            # V115: Filter out bad columns (contractor/applicant/contact addresses)
            city_columns = [c for c in city_columns if c.lower() not in BAD_CITY_COLUMNS]
            # V116: Prefer known-good columns
            good = [c for c in city_columns if c.lower() in GOOD_CITY_COLUMNS]
            if good:
                city_columns = good

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
    """V116: Sweep ArcGIS Hub — fixed pagination (meta.next, not links.next)."""
    _log("[ARCGIS] Starting ArcGIS Hub sweep...")
    conn = permitdb.get_connection()

    # Cache top cities for matching (in memory for speed)
    cities = conn.execute(
        "SELECT city_slug, city, state FROM prod_cities WHERE population >= 25000 AND status = 'active' ORDER BY population DESC"
    ).fetchall()
    city_lookup = [(r[0], r[1].lower(), r[2]) for r in cities]
    _log(f"[ARCGIS] Loaded {len(city_lookup)} cities for matching")

    search_url = "https://hub.arcgis.com/api/v3/search"
    params = {'q': 'building permits', 'filter[type]': 'Feature Service'}
    # NOTE: page[size] is ignored by API — always returns 20 per page

    total_found = 0
    total_processed = 0
    page = 1
    max_pages = 500  # 500 × 20 = 10,000 (covers all 9,592)

    while page <= max_pages:
        try:
            resp = requests.get(search_url, params=params, timeout=30)
            if resp.status_code != 200:
                _log(f"[ARCGIS] Page {page}: HTTP {resp.status_code}, stopping")
                break

            data = resp.json()
            results = data.get('data', [])
            if not results:
                break

            if page == 1:
                total_count = data.get('meta', {}).get('stats', {}).get('totalCount', 0)
                _log(f"[ARCGIS] Total available: {total_count}")

            for item in results:
                try:
                    attrs = item.get('attributes', {})
                    name = attrs.get('name', '')
                    svc_url = attrs.get('url', '')
                    source_org = attrs.get('source', '')

                    if not svc_url or not _is_building_permit_dataset(name):
                        continue

                    # Match using source org (most reliable) + name
                    text = (source_org + ' ' + name).lower()
                    for slug, city_lower, state in city_lookup:
                        if city_lower in text and len(city_lower) > 3:
                            existing = conn.execute(
                                "SELECT 1 FROM sweep_sources WHERE source_url = ?", (svc_url,)
                            ).fetchone()
                            if not existing:
                                conn.execute("""
                                    INSERT OR IGNORE INTO sweep_sources
                                    (city_slug, source_type, source_url, platform, name,
                                     city_value, status, discovered_at)
                                    VALUES (?, 'arcgis_hub', ?, 'arcgis', ?, ?, 'pending_test', datetime('now'))
                                """, (slug, svc_url, name, source_org))
                                total_found += 1
                                _log(f"[ARCGIS] Match: {slug} — {name} (org: {source_org})")
                            break
                except Exception:
                    continue

            total_processed += len(results)

            # V116 FIX: next URL is in meta.next, NOT links.next
            next_url = data.get('meta', {}).get('next')
            if not next_url:
                _log(f"[ARCGIS] No next URL on page {page}, done")
                break

            search_url = next_url
            params = {}  # Next URL includes all params
            page += 1

            if page % 25 == 0:
                conn.commit()
                _log(f"[ARCGIS] Progress: page {page}, {total_processed} processed, {total_found} matched")

            time.sleep(0.5)

        except requests.exceptions.Timeout:
            _log(f"[ARCGIS] Timeout page {page}, retrying...")
            time.sleep(2)
            continue
        except Exception as e:
            _log(f"[ARCGIS] Error page {page}: {e}")
            page += 1
            time.sleep(1)
            continue

    conn.commit()
    _log(f"[ARCGIS] Complete: {page} pages, {total_processed} processed, {total_found} matched")

    conn.commit()
    _log(f"[SWEEP] ArcGIS: {matched} new city sources discovered")
    return matched


# ================================================================
# TEST + ACTIVATE DISCOVERED SOURCES
# ================================================================

def test_sweep_sources(limit=500):
    """Test pending sweep sources and pull permits from confirmed ones."""
    _v115_cleanup()  # Purge junk first
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
        tested_count = pending.index(row) + 1 if row in pending else 0
        if tested_count % 10 == 0:
            _log(f"[TEST] Progress: {tested_count}/{len(pending)} tested, {confirmed} confirmed")

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


# ================================================================
# V116: DATA.GOV CKAN SWEEP
# ================================================================

def sweep_data_gov():
    """V116: Sweep data.gov CKAN catalog for building permit datasets."""
    _log("[DATAGOV] Starting data.gov sweep...")
    conn = permitdb.get_connection()
    total_found = 0

    for term in ['building permits', 'construction permits', 'zoning permits']:
        offset = 0
        while offset < 1000:
            try:
                resp = requests.get("https://catalog.data.gov/api/3/action/package_search",
                                    params={'q': term, 'rows': 100, 'start': offset},
                                    timeout=30)
                if resp.status_code != 200:
                    break
                data = resp.json()
                if not data.get('success'):
                    break
                results = data['result']['results']
                total = data['result']['count']
                if not results:
                    break
                if offset == 0:
                    _log(f"[DATAGOV] '{term}': {total} total datasets")

                for pkg in results:
                    try:
                        title = pkg.get('title', '')
                        if not _is_building_permit_dataset(title, pkg.get('notes', '')):
                            continue
                        for resource in pkg.get('resources', []):
                            res_url = resource.get('url', '')
                            if not res_url:
                                continue
                            # Socrata datasets
                            match = re.search(r'([\w.-]+\.(?:gov|org|com)).*?([a-z0-9]{4}-[a-z0-9]{4})', res_url)
                            if match and _is_valid_domain(match.group(1)):
                                domain = match.group(1)
                                dataset_id = match.group(2)
                                existing = conn.execute("SELECT 1 FROM sweep_sources WHERE dataset_id = ?", (dataset_id,)).fetchone()
                                if not existing:
                                    org = pkg.get('organization', {})
                                    org_name = org.get('title', '') if org else ''
                                    conn.execute("""
                                        INSERT OR IGNORE INTO sweep_sources
                                        (city_slug, source_type, source_url, platform, dataset_id, domain, name,
                                         status, discovered_at)
                                        VALUES ('_datagov', 'data_gov', ?, 'socrata', ?, ?, ?, 'pending_test', datetime('now'))
                                    """, (res_url, dataset_id, domain, title))
                                    total_found += 1
                            # ArcGIS endpoints
                            elif 'FeatureServer' in res_url or 'MapServer' in res_url:
                                existing = conn.execute("SELECT 1 FROM sweep_sources WHERE source_url = ?", (res_url,)).fetchone()
                                if not existing:
                                    conn.execute("""
                                        INSERT OR IGNORE INTO sweep_sources
                                        (city_slug, source_type, source_url, platform, name,
                                         status, discovered_at)
                                        VALUES ('_datagov', 'data_gov', ?, 'arcgis', ?, 'pending_test', datetime('now'))
                                    """, (res_url, title))
                                    total_found += 1
                    except Exception:
                        continue

                offset += 100
                if offset >= total:
                    break
                time.sleep(0.5)
            except Exception as e:
                _log(f"[DATAGOV] Error for '{term}': {e}")
                break

    conn.commit()
    _log(f"[DATAGOV] Complete: {total_found} new sources found")
