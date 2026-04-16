"""
V162: Code Violation Collector — Socrata SODA API
Fetches building/housing code violations from public open data portals.
Stores in the violations table with prod_city_id FK for fast lookups.
"""

import json
import time
import requests
from datetime import datetime, timedelta

import db as permitdb

SESSION = requests.Session()
SESSION.headers.update({'Accept': 'application/json'})

# ---------------------------------------------------------------------------
# Violation source configs — hardcoded per instructions
# ---------------------------------------------------------------------------

VIOLATION_SOURCES = {
    'new-york-city': {
        'prod_city_id': 1,
        'city': 'New York City',
        'state': 'NY',
        'endpoints': [
            {
                'name': 'HPD Violations',
                'domain': 'data.cityofnewyork.us',
                'resource_id': 'wvxf-dwi5',
                'date_field': 'inspectiondate',
                'id_field': 'violationid',
                'description_field': 'novdescription',
                'status_field': 'currentstatus',
                'type_field': 'class',
                'address_fields': {'number': 'housenumber', 'street': 'streetname'},
                'zip_field': 'zip',
                'lat_field': 'latitude',
                'lng_field': 'longitude',
            },
            {
                'name': 'DOB Violations',
                'domain': 'data.cityofnewyork.us',
                'resource_id': '3h2n-5cm9',
                'date_field': 'issue_date',
                'id_field': 'isn_dob_bis_viol',
                'description_field': 'violation_type_code',
                'status_field': 'violation_category',
                'type_field': 'violation_type',
                'address_fields': {'number': 'house_number', 'street': 'street'},
                'zip_field': None,
                'lat_field': None,
                'lng_field': None,
            },
        ],
    },
    'los-angeles': {
        'prod_city_id': 3,
        'city': 'Los Angeles',
        'state': 'CA',
        'endpoints': [
            {
                'name': 'Open Cases',
                'domain': 'data.lacity.org',
                'resource_id': 'u82d-eh7z',
                'date_field': 'adddttm',
                'id_field': 'apno',
                'description_field': 'aptype',
                'status_field': 'stat',
                'type_field': 'aptype',
                'address_fields': {'number': 'stno', 'street': 'stname', 'prefix': 'predir', 'suffix': 'suffix'},
                'zip_field': 'zip',
                'lat_field': None,
                'lng_field': None,
            },
            {
                'name': 'Closed Cases',
                'domain': 'data.lacity.org',
                'resource_id': 'rken-a55j',
                'date_field': 'adddttm',
                'id_field': 'apno',
                'description_field': 'aptype',
                'status_field': 'stat',
                'type_field': 'aptype',
                'address_fields': {'number': 'stno', 'street': 'stname', 'prefix': 'predir', 'suffix': 'suffix'},
                'zip_field': 'zip',
                'lat_field': None,
                'lng_field': None,
            },
        ],
    },
    'chicago-il': {
        'prod_city_id': 2,
        'city': 'Chicago',
        'state': 'IL',
        'endpoints': [
            {
                'name': 'Building Violations',
                'domain': 'data.cityofchicago.org',
                'resource_id': '22u3-xenr',
                'date_field': 'violation_date',
                'id_field': 'id',
                'description_field': 'violation_description',
                'status_field': 'violation_status',
                'type_field': 'violation_code',
                'address_fields': {'full': 'address'},
                'zip_field': None,
                'lat_field': 'latitude',
                'lng_field': 'longitude',
            },
        ],
    },
    'austin-tx': {
        'prod_city_id': None,  # Looked up dynamically by (city, state)
        'city': 'Austin',
        'state': 'TX',
        'endpoints': [
            {
                'name': 'Code Enforcement Cases',
                'domain': 'data.austintexas.gov',
                'resource_id': '6wtj-zbtb',
                'date_field': 'opened_date',
                'id_field': 'case_id',
                'description_field': 'description',
                'status_field': 'status',
                'type_field': 'case_type',
                'address_fields': {'full': 'address'},
                'zip_field': 'zip_code',
                'lat_field': 'latitude',
                'lng_field': 'longitude',
            },
        ],
    },
    # V182 PR2: Fort Worth and Mesa skipped after endpoint discovery:
    #   - data.fortworthtexas.gov redirects to ArcGIS (needs new collector shape)
    #   - data.mesaaz.gov resources nnr9-eg5e returns empty, amsn-zipb 404s
    # Document here to prevent re-investigation next iteration.
    'philadelphia': {
        'prod_city_id': None,  # Looked up dynamically
        'city': 'Philadelphia',
        'state': 'PA',
        'platform': 'carto',
        'endpoints': [
            {
                'name': 'L&I Violations',
                'carto_base': 'https://phl.carto.com/api/v2/sql',
                'carto_table': 'violations',
                'date_field': 'casecreateddate',
                'id_field': 'violationnumber',
                'description_field': 'violationcodetitle',
                'status_field': 'violationstatus',
                'type_field': 'casetype',
                'address_fields': {'full': 'address'},
                'zip_field': 'zip',
                'lat_field': None,
                'lng_field': None,
            },
        ],
    },
}


# ---------------------------------------------------------------------------
# Collection logic
# ---------------------------------------------------------------------------

def _ensure_table():
    """Create violations table if it doesn't exist (V162 schema)."""
    conn = permitdb.get_connection()
    try:
        # Check if table has the right schema (prod_city_id column)
        test = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='violations'").fetchone()
        if test:
            schema = test[0] if isinstance(test, tuple) else test['sql']
            if 'prod_city_id' not in schema:
                print("[V162] Dropping old violations table (missing prod_city_id)")
                conn.execute("DROP TABLE IF EXISTS violations")
                conn.commit()

        conn.executescript("""
            CREATE TABLE IF NOT EXISTS violations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                prod_city_id INTEGER NOT NULL,
                city TEXT NOT NULL,
                state TEXT NOT NULL,
                source_violation_id TEXT UNIQUE,
                violation_date TEXT,
                violation_type TEXT,
                violation_description TEXT,
                status TEXT,
                address TEXT,
                zip TEXT,
                latitude REAL,
                longitude REAL,
                raw_data TEXT,
                collected_at TEXT DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_violations_prod_city_id ON violations(prod_city_id);
            CREATE INDEX IF NOT EXISTS idx_violations_date ON violations(violation_date);
            CREATE INDEX IF NOT EXISTS idx_violations_city_state ON violations(city, state);
        """)
        conn.commit()
    except Exception as e:
        print(f"[V162] Table creation note: {e}")


def _build_address(record, addr_config):
    """Build address string from configured fields."""
    if 'full' in addr_config:
        return str(record.get(addr_config['full'], '')).strip()
    parts = []
    for key in ('number', 'prefix', 'street', 'suffix'):
        if key in addr_config:
            val = record.get(addr_config[key])
            if val and str(val).strip():
                parts.append(str(val).strip())
    return ' '.join(parts)


def _parse_date(date_str):
    """Parse SODA date formats to ISO YYYY-MM-DD."""
    if not date_str or not isinstance(date_str, str):
        return None
    s = date_str.strip()
    # Skip obviously bad dates
    if s.startswith('Y') or len(s) < 8:
        return None
    for fmt in ('%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d'):
        try:
            dt = datetime.strptime(s[:26], fmt)
            if dt.year < 2020 or dt > datetime.now() + timedelta(days=7):
                return None
            return dt.strftime('%Y-%m-%d')
        except ValueError:
            continue
    return None


def normalize_violation(record, city_config, endpoint):
    """Map source fields to normalized schema."""
    address = _build_address(record, endpoint['address_fields'])
    vid = record.get(endpoint['id_field'], '')
    source_id = f"{endpoint['resource_id']}_{vid}" if vid else None

    return {
        'prod_city_id': city_config['prod_city_id'],
        'city': city_config['city'],
        'state': city_config['state'],
        'source_violation_id': source_id,
        'violation_date': _parse_date(record.get(endpoint['date_field'])),
        'violation_type': str(record.get(endpoint.get('type_field', ''), '') or '')[:200],
        'violation_description': str(record.get(endpoint.get('description_field', ''), '') or '')[:500],
        'status': str(record.get(endpoint.get('status_field', ''), '') or '')[:100],
        'address': address,
        'zip': str(record.get(endpoint['zip_field'], '') or '') if endpoint.get('zip_field') else '',
        'latitude': record.get(endpoint['lat_field']) if endpoint.get('lat_field') else None,
        'longitude': record.get(endpoint['lng_field']) if endpoint.get('lng_field') else None,
        'raw_data': json.dumps(record, default=str),
    }


def collect_violations_from_endpoint(city_config, endpoint):
    """Fetch violations from a SODA or Carto endpoint."""
    is_carto = 'carto_base' in endpoint
    if is_carto:
        base_url = endpoint['carto_base']
    else:
        base_url = f"https://{endpoint['domain']}/resource/{endpoint['resource_id']}.json"
    date_field = endpoint['date_field']
    prod_city_id = city_config['prod_city_id']

    # V170: Dynamic prod_city_id lookup for cities with None
    if prod_city_id is None:
        try:
            conn_tmp = permitdb.get_connection()
            row = conn_tmp.execute(
                "SELECT id FROM prod_cities WHERE city = ? AND state = ?",
                (city_config['city'], city_config['state'])
            ).fetchone()
            if row:
                prod_city_id = row['id'] if isinstance(row, dict) else row[0]
                city_config['prod_city_id'] = prod_city_id
        except Exception:
            pass
    if not prod_city_id:
        print(f"  [V170] {city_config['city']}: No prod_city_id found, skipping")
        return 0

    # Get last collected date for incremental collection
    conn = permitdb.get_connection()
    try:
        row = conn.execute(
            "SELECT MAX(violation_date) as last_date FROM violations WHERE prod_city_id = ?",
            (prod_city_id,)
        ).fetchone()
        last_date = (row['last_date'] if isinstance(row, dict) else row[0]) if row else None
    except Exception:
        last_date = None

    if not last_date:
        last_date = (datetime.now() - timedelta(days=180)).strftime('%Y-%m-%dT00:00:00')

    print(f"  [V162] {city_config['city']} / {endpoint['name']}: fetching since {last_date[:10]}")

    offset = 0
    batch_size = 1000
    total_inserted = 0
    max_records = 5000  # V166: Reduced from 50K to 5K per run to limit memory

    while total_inserted < max_records:
        # V170: Build request based on platform (SODA vs Carto)
        if is_carto:
            table = endpoint['carto_table']
            sql = (f"SELECT * FROM {table} WHERE {date_field} > '{last_date}' "
                   f"ORDER BY {date_field} DESC LIMIT {batch_size} OFFSET {offset}")
            params = {'q': sql, 'format': 'json'}
        else:
            params = {
                '$limit': batch_size,
                '$offset': offset,
                '$order': f"{date_field} DESC",
                '$where': f"{date_field} > '{last_date}'",
            }

        try:
            resp = SESSION.get(base_url, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            # V170: Carto wraps records in 'rows', SODA returns array directly
            records = data.get('rows', data) if isinstance(data, dict) else data
            resp.close()
        except Exception as e:
            print(f"  [V170] Error fetching page at offset {offset}: {e}")
            break

        if not records or not isinstance(records, list):
            break

        # V166: Stream-process — normalize and insert immediately per page, don't accumulate
        batch = []
        for record in records:
            try:
                norm = normalize_violation(record, city_config, endpoint)
                if norm['source_violation_id'] and norm['violation_date']:
                    batch.append(norm)
            except Exception:
                continue

        if batch:
            inserted = _insert_batch(batch)
            total_inserted += inserted
        del batch, records  # V166: Free memory immediately

        if total_inserted >= max_records:
            break

        offset += batch_size
        time.sleep(1)

    print(f"  [V162] {city_config['city']} / {endpoint['name']}: {total_inserted} violations inserted")
    return total_inserted


def _insert_batch(violations):
    """Insert a batch of violations, skip duplicates."""
    conn = permitdb.get_connection()
    inserted = 0
    for v in violations:
        try:
            conn.execute("""
                INSERT OR IGNORE INTO violations
                (prod_city_id, city, state, source_violation_id, violation_date,
                 violation_type, violation_description, status, address, zip,
                 latitude, longitude, raw_data)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                v['prod_city_id'], v['city'], v['state'], v['source_violation_id'],
                v['violation_date'], v['violation_type'], v['violation_description'],
                v['status'], v['address'], v['zip'],
                v['latitude'], v['longitude'], v['raw_data'],
            ))
            inserted += 1
        except Exception:
            pass
    conn.commit()
    return inserted


def collect_violations():
    """Collect violations for all configured cities."""
    _ensure_table()

    print(f"\n{'='*60}")
    print(f"V162: Violation Collection — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*60}")

    results = {}
    for slug, config in VIOLATION_SOURCES.items():
        city_total = 0
        for endpoint in config['endpoints']:
            try:
                count = collect_violations_from_endpoint(config, endpoint)
                city_total += count
            except Exception as e:
                print(f"  [V162] Error on {config['city']}/{endpoint['name']}: {e}")
        results[slug] = city_total

    print(f"\n{'='*60}")
    print(f"VIOLATION COLLECTION COMPLETE")
    for slug, count in results.items():
        print(f"  {VIOLATION_SOURCES[slug]['city']}: {count:,} new violations")
    print(f"  Total: {sum(results.values()):,}")
    print(f"{'='*60}\n")

    # V182 PR2: refresh emblem flags so cities that just gained violations
    # get their has_violations flag updated before the UI queries them.
    try:
        from contractor_profiles import update_city_emblems
        stats = update_city_emblems()
        print(f"[V182] Emblem refresh after violations: "
              f"{stats['cities_with_violations']} cities with violations, "
              f"{stats['cities_with_enrichment']} with enrichment")
    except Exception as e:
        print(f"[V182] Emblem refresh failed (non-fatal): {e}")

    return results
