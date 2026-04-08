"""
V108 — City Onboarding Pipeline

Automated source discovery and 6-month backfill, city by city.
Searches Socrata Discovery API, ArcGIS Hub, CKAN portals, and Accela.
The 6-month pull IS the test — if data lands, the city is done.

Usage:
    POST /api/admin/run-pipeline {"min_population": 100000, "batch_size": 25}
"""

import json
import re
import requests
import time
from datetime import datetime, timedelta

import db as permitdb

SIX_MONTHS_AGO = (datetime.utcnow() - timedelta(days=180)).strftime('%Y-%m-%dT00:00:00')
SIX_MONTHS_DATE = (datetime.utcnow() - timedelta(days=180)).strftime('%Y-%m-%d')


# ================================================================
# MAIN PIPELINE
# ================================================================

def run_city_pipeline(min_population=50000, batch_size=25):
    """Process cities one by one, highest population first.
    For each: find source → pull 6 months → if data lands, city is done."""
    conn = permitdb.get_connection()

    rows = conn.execute("""
        SELECT city_slug, city, state, population, source_type, source_id
        FROM prod_cities
        WHERE population >= ?
        AND (total_permits = 0 OR total_permits IS NULL)
        AND status = 'active'
        AND city NOT LIKE 'Balance of%%'
        AND (pipeline_checked_at IS NULL OR pipeline_checked_at < datetime('now', '-7 days'))
        ORDER BY population DESC
        LIMIT ?
    """, (min_population, batch_size)).fetchall()

    results = []
    for row in rows:
        slug = row[0]
        city, state, pop = row[1], row[2], row[3]
        source_type, source_id = row[4], row[5]

        print(f"\n[PIPELINE] Processing: {city}, {state} (pop {pop:,}, slug: {slug})")

        # Try to find a working source and pull 6 months
        source = find_working_source(city, state, slug)

        if source and source.get('permits_inserted', 0) > 0:
            _mark_city_done(slug, source)
            results.append({
                'city': city, 'state': state, 'pop': pop,
                'status': 'DONE', 'source': source.get('source_id', ''),
                'permits': source['permits_inserted']
            })
        else:
            _mark_city_no_source(slug)
            results.append({
                'city': city, 'state': state, 'pop': pop,
                'status': 'NO_SOURCE',
                'error': source.get('error') if source else 'nothing found'
            })

        time.sleep(1)  # Rate limit between cities

    # Log run results
    try:
        conn = permitdb.get_connection()
        conn.execute("""
            INSERT INTO pipeline_runs (started_at, completed_at, results_json, cities_processed, cities_succeeded)
            VALUES (datetime('now'), datetime('now'), ?, ?, ?)
        """, (json.dumps(results), len(results),
              sum(1 for r in results if r['status'] == 'DONE')))
        conn.commit()
    except Exception as e:
        print(f"[PIPELINE] Error logging run: {e}")

    done = sum(1 for r in results if r['status'] == 'DONE')
    print(f"\n[PIPELINE] Complete: {done}/{len(results)} cities got data")
    return results


def find_working_source(city, state, slug):
    """Try every platform to find a working permit data source."""

    # Strategy 1: Socrata Discovery API
    result = try_socrata(city, state, slug)
    if result and result.get('permits_inserted', 0) > 0:
        return result

    # Strategy 2: ArcGIS Hub
    result = try_arcgis(city, state, slug)
    if result and result.get('permits_inserted', 0) > 0:
        return result

    return None


# ================================================================
# SOCRATA SEARCH + PULL
# ================================================================

def try_socrata(city, state, slug):
    """Search Socrata Discovery API for building permits, then pull 6 months."""
    print(f"[PIPELINE]   Trying Socrata for {city}, {state}...")

    discovery_results = _socrata_discovery_search(city, state)

    for dr in discovery_results:
        domain = dr['domain']
        dataset_id = dr['dataset_id']
        name = dr.get('name', '')

        name_lower = name.lower()
        if not any(kw in name_lower for kw in ['permit', 'building', 'construction', 'development']):
            continue

        print(f"[PIPELINE]     Found: {name} on {domain} ({dataset_id})")

        result = _socrata_pull_6months(domain, dataset_id, city, state, slug)
        if result and result.get('permits_inserted', 0) > 0:
            return result

    # Also try city-specific Socrata domains directly
    city_clean = re.sub(r'[^a-z]', '', city.lower())
    domains_to_check = [
        f"data.{city_clean}.gov",
        f"data.{city_clean}{state.lower()}.gov",
        f"data.cityof{city_clean}.org",
        f"opendata.{city_clean}.gov",
    ]

    for domain in domains_to_check:
        try:
            resp = requests.get(f"https://{domain}/api/catalog/v1?q=permit&limit=5", timeout=5)
            if resp.status_code != 200:
                continue
            data = resp.json()
            for ds in data.get('results', []):
                resource = ds.get('resource', {})
                ds_name = resource.get('name', '').lower()
                dataset_id = resource.get('id')
                if any(kw in ds_name for kw in ['permit', 'building', 'construction']):
                    print(f"[PIPELINE]     Found on {domain}: {resource.get('name')} ({dataset_id})")
                    result = _socrata_pull_6months(domain, dataset_id, city, state, slug)
                    if result and result.get('permits_inserted', 0) > 0:
                        return result
        except Exception:
            continue

    return None


def _socrata_discovery_search(city, state):
    """Search Socrata Discovery API across all portals nationally."""
    results = []
    try:
        for q in [f"building permits {city} {state}", f"permits {city}"]:
            url = f"http://api.us.socrata.com/api/catalog/v1?q={q}&limit=10"
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                for item in data.get('results', []):
                    resource = item.get('resource', {})
                    metadata = item.get('metadata', {})
                    domain = metadata.get('domain', '')
                    dataset_id = resource.get('id', '')
                    if domain and dataset_id:
                        results.append({
                            'domain': domain,
                            'dataset_id': dataset_id,
                            'name': resource.get('name', ''),
                        })
            time.sleep(0.3)
    except Exception as e:
        print(f"[PIPELINE]     Socrata discovery error: {e}")
    return results


def _socrata_pull_6months(domain, dataset_id, city, state, slug):
    """Pull 6 months of permit data from a Socrata dataset."""
    try:
        # Get metadata
        meta_url = f"https://{domain}/api/views/{dataset_id}.json"
        meta_resp = requests.get(meta_url, timeout=10)
        if meta_resp.status_code != 200:
            return {'permits_inserted': 0, 'error': f'metadata HTTP {meta_resp.status_code}'}

        meta = meta_resp.json()
        columns = meta.get('columns', [])
        col_names = [c['fieldName'] for c in columns]
        col_types = {c['fieldName']: c.get('dataTypeName', '') for c in columns}

        field_map = _auto_detect_fields(col_names, col_types)
        date_field = field_map.get('date_field')
        if not date_field:
            return {'permits_inserted': 0, 'error': 'no date field detected'}

        # Build query
        where = f"{date_field} >= '{SIX_MONTHS_AGO}'"
        city_field = field_map.get('city_field')
        if city_field:
            variations = _get_city_name_variations(city)
            city_filter = " OR ".join([f"{city_field} = '{v}'" for v in variations])
            where = f"({where}) AND ({city_filter})"

        # Pull with pagination
        all_records = []
        offset = 0
        while offset < 50000:
            url = (f"https://{domain}/resource/{dataset_id}.json"
                   f"?$where={where}&$limit=1000&$offset={offset}"
                   f"&$order={date_field} DESC")
            resp = requests.get(url, timeout=30)
            if resp.status_code != 200:
                break
            records = resp.json()
            if not records:
                break
            all_records.extend(records)
            if len(records) < 1000:
                break
            offset += 1000
            time.sleep(0.2)

        if not all_records:
            return {'permits_inserted': 0, 'error': 'no records returned'}

        # Insert into permits table
        inserted = _insert_permits(slug, city, state, all_records, field_map, 'socrata')
        print(f"[PIPELINE]     Socrata pull: {len(all_records)} found, {inserted} inserted")
        return {
            'permits_inserted': inserted,
            'source_id': f"{domain}:{dataset_id}",
            'source_type': 'socrata',
        }
    except Exception as e:
        return {'permits_inserted': 0, 'error': str(e)[:200]}


# ================================================================
# ARCGIS SEARCH + PULL
# ================================================================

def try_arcgis(city, state, slug):
    """Search ArcGIS Hub for building permit data."""
    print(f"[PIPELINE]   Trying ArcGIS for {city}, {state}...")
    try:
        search_url = (f"https://hub.arcgis.com/api/feed/dcat-us/1.1"
                      f"?q={city}+{state}+building+permits")
        resp = requests.get(search_url, timeout=15)
        if resp.status_code != 200:
            return None

        data = resp.json()
        for ds in data.get('dataset', [])[:5]:
            title = ds.get('title', '').lower()
            if not any(kw in title for kw in ['permit', 'building', 'construction']):
                continue

            api_url = None
            for dist in ds.get('distribution', []):
                access_url = dist.get('accessURL', '')
                if 'FeatureServer' in access_url or 'MapServer' in access_url:
                    api_url = access_url
                    break
            if not api_url:
                continue

            print(f"[PIPELINE]     Found: {ds.get('title')} at {api_url}")
            result = _arcgis_pull_6months(api_url, city, state, slug)
            if result and result.get('permits_inserted', 0) > 0:
                return result

    except Exception as e:
        print(f"[PIPELINE]     ArcGIS search error: {e}")
    return None


def _arcgis_pull_6months(api_url, city, state, slug):
    """Pull 6 months from an ArcGIS FeatureServer."""
    try:
        base_url = api_url.rstrip('/')
        if '/query' in base_url:
            base_url = base_url.split('/query')[0]
        if not base_url.endswith('/0'):
            base_url = base_url + '/0'

        # Get field info
        resp = requests.get(f"{base_url}?f=json", timeout=10)
        if resp.status_code != 200:
            return {'permits_inserted': 0, 'error': f'HTTP {resp.status_code}'}

        layer_info = resp.json()
        fields = layer_info.get('fields', [])

        # Find date field
        date_field = None
        for f in fields:
            if f.get('type') == 'esriFieldTypeDate':
                name_lower = f['name'].lower()
                if any(kw in name_lower for kw in ['issued', 'date', 'created', 'applied', 'filed']):
                    date_field = f['name']
                    break
        if not date_field:
            for f in fields:
                if f.get('type') == 'esriFieldTypeDate':
                    date_field = f['name']
                    break
        if not date_field:
            return {'permits_inserted': 0, 'error': 'no date field found'}

        # Pull with pagination
        six_months_ms = int((datetime.utcnow() - timedelta(days=180)).timestamp() * 1000)
        where = f"{date_field} >= {six_months_ms}"

        all_features = []
        offset = 0
        while True:
            query_url = (f"{base_url}/query?where={where}"
                         f"&outFields=*&resultRecordCount=1000"
                         f"&resultOffset={offset}&f=json")
            resp = requests.get(query_url, timeout=30)
            if resp.status_code != 200:
                break
            data = resp.json()
            features = data.get('features', [])
            if not features:
                break
            all_features.extend(features)
            if len(features) < 1000:
                break
            offset += 1000
            time.sleep(0.2)

        if not all_features:
            return {'permits_inserted': 0, 'error': 'no features returned'}

        # Insert
        field_names = [f['name'] for f in fields]
        inserted = _insert_arcgis_permits(slug, city, state, all_features, field_names)
        print(f"[PIPELINE]     ArcGIS pull: {len(all_features)} found, {inserted} inserted")
        return {
            'permits_inserted': inserted,
            'source_id': api_url,
            'source_type': 'arcgis',
        }
    except Exception as e:
        return {'permits_inserted': 0, 'error': str(e)[:200]}


# ================================================================
# HELPERS
# ================================================================

def _auto_detect_fields(col_names, col_types):
    """Auto-detect Socrata field mappings."""
    field_map = {}

    date_patterns = [
        'issued_date', 'issue_date', 'permit_issued_date', 'issueddate',
        'date_issued', 'approval_date', 'applied_date', 'application_date',
        'permit_date', 'filed_date', 'created_date', 'status_date',
        'permitissuedate', 'issuedate', 'finaldate',
    ]
    for col in col_names:
        if any(p in col.lower() for p in date_patterns):
            field_map['date_field'] = col
            break
    if not field_map.get('date_field'):
        for col in col_names:
            if col_types.get(col) in ('calendar_date', 'floating_timestamp'):
                field_map['date_field'] = col
                break

    for patterns, key in [
        (['permit_number', 'permit_no', 'permitnumber', 'permit_num',
          'record_id', 'application_number', 'case_number', 'permit_id'], 'permit_number_field'),
        (['address', 'site_address', 'location', 'street_address',
          'project_address', 'work_location', 'property_address'], 'address_field'),
        (['description', 'work_description', 'permit_type', 'work_type',
          'project_description', 'permit_description'], 'description_field'),
        (['city', 'municipality', 'jurisdiction', 'town'], 'city_field'),
        (['estimated_cost', 'job_value', 'construction_cost', 'valuation',
          'total_cost', 'project_value', 'cost'], 'value_field'),
        (['contractor', 'contractor_name', 'applicant', 'builder'], 'contractor_field'),
    ]:
        for col in col_names:
            if any(p in col.lower() for p in patterns):
                field_map[key] = col
                break

    return field_map


def _get_city_name_variations(city):
    """Generate common city name variations for Socrata filtering."""
    variations = [city, city.upper(), city.lower(), city.title()]
    for old, new in [('Fort ', 'Ft '), ('Fort ', 'Ft. '), ('Mount ', 'Mt '),
                     ('Mount ', 'Mt. '), ('Saint ', 'St '), ('Saint ', 'St. '),
                     ('Ft ', 'Fort '), ('Mt ', 'Mount '), ('St ', 'Saint ')]:
        if old in city:
            variations.append(city.replace(old, new))
    return list(dict.fromkeys(variations))


def _parse_date(raw):
    """Try multiple date formats."""
    if not raw:
        return None
    for fmt in ['%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S',
                '%Y-%m-%d', '%m/%d/%Y', '%m/%d/%y',
                '%Y-%m-%d %H:%M:%S', '%m-%d-%Y']:
        try:
            return datetime.strptime(str(raw)[:26], fmt).strftime('%Y-%m-%d')
        except (ValueError, TypeError):
            continue
    return None


def _insert_permits(slug, city, state, records, field_map, source):
    """Insert Socrata records into the permits table."""
    conn = permitdb.get_connection()
    inserted = 0

    for record in records:
        try:
            permit_num = str(record.get(field_map.get('permit_number_field', ''), ''))
            date_raw = record.get(field_map.get('date_field', ''), '')
            address = str(record.get(field_map.get('address_field', ''), ''))
            description = str(record.get(field_map.get('description_field', ''), ''))
            value = record.get(field_map.get('value_field', ''), '')
            contractor = str(record.get(field_map.get('contractor_field', ''), ''))

            permit_date = _parse_date(date_raw)
            if not permit_date:
                continue

            if not permit_num:
                permit_num = f"PL-{slug[:3].upper()}-{hash(str(record)) & 0xFFFFFFFF:08x}"

            # Use INSERT OR IGNORE (permit_number is PRIMARY KEY)
            conn.execute("""
                INSERT OR IGNORE INTO permits
                (permit_number, city, state, address, description,
                 estimated_cost, contractor_name, filing_date, date,
                 source_city_key, collected_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """, (permit_num, city, state, address[:200], description[:500],
                  float(value) if value and str(value).replace('.', '').replace('-', '').isdigit() else 0,
                  contractor[:100], permit_date, permit_date, slug))

            if conn.total_changes:
                inserted += 1
        except Exception:
            continue

    conn.commit()

    # Update prod_cities counters
    _update_city_counters(slug)
    return inserted


def _insert_arcgis_permits(slug, city, state, features, field_names):
    """Insert ArcGIS features into permits table."""
    conn = permitdb.get_connection()
    inserted = 0

    def find_field(patterns):
        for p in patterns:
            for fn in field_names:
                if p in fn.lower():
                    return fn
        return None

    permit_field = find_field(['permit_n', 'permitn', 'record_id', 'case_n', 'permit_id'])
    date_field = find_field(['issued', 'issue_date', 'permit_date', 'created', 'applied'])
    address_field = find_field(['address', 'location', 'site_add', 'street'])
    desc_field = find_field(['description', 'work_type', 'permit_type', 'type'])
    value_field = find_field(['value', 'cost', 'valuation', 'estimated'])

    for feature in features:
        try:
            attrs = feature.get('attributes', {})
            permit_num = str(attrs.get(permit_field, '')) if permit_field else ''
            date_val = attrs.get(date_field) if date_field else None
            address = str(attrs.get(address_field, '')) if address_field else ''
            desc = str(attrs.get(desc_field, '')) if desc_field else ''
            value = attrs.get(value_field, 0) if value_field else 0

            if isinstance(date_val, (int, float)) and date_val > 0:
                permit_date = datetime.utcfromtimestamp(date_val / 1000).strftime('%Y-%m-%d')
            else:
                continue

            if not permit_num:
                permit_num = f"ARC-{slug[:3].upper()}-{hash(str(attrs)) & 0xFFFFFFFF:08x}"

            conn.execute("""
                INSERT OR IGNORE INTO permits
                (permit_number, city, state, address, description,
                 estimated_cost, filing_date, date, source_city_key, collected_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """, (permit_num, city, state, address[:200], desc[:500],
                  float(value) if value else 0,
                  permit_date, permit_date, slug))
            if conn.total_changes:
                inserted += 1
        except Exception:
            continue

    conn.commit()
    _update_city_counters(slug)
    return inserted


def _update_city_counters(slug):
    """Update prod_cities permit counts after pipeline insert."""
    conn = permitdb.get_connection()
    try:
        conn.execute("""
            UPDATE prod_cities SET
                total_permits = (SELECT COUNT(*) FROM permits WHERE source_city_key = ?),
                newest_permit_date = (SELECT MAX(filing_date) FROM permits WHERE source_city_key = ?),
                earliest_permit_date = (SELECT MIN(filing_date) FROM permits WHERE source_city_key = ?),
                last_successful_collection = datetime('now'),
                health_status = CASE WHEN (SELECT COUNT(*) FROM permits WHERE source_city_key = ?) > 0
                               THEN 'collecting' ELSE health_status END,
                backfill_status = 'complete'
            WHERE city_slug = ?
        """, (slug, slug, slug, slug, slug))
        conn.commit()
    except Exception as e:
        print(f"[PIPELINE] Error updating counters for {slug}: {e}")


def _mark_city_done(slug, result):
    """Mark a city as successfully onboarded."""
    conn = permitdb.get_connection()
    source_id = result.get('source_id', '')
    source_type = result.get('source_type', '')
    conn.execute("""
        UPDATE prod_cities
        SET health_status = 'collecting', backfill_status = 'complete',
            source_id = COALESCE(NULLIF(?, ''), source_id),
            source_type = COALESCE(NULLIF(?, ''), source_type),
            pipeline_checked_at = datetime('now')
        WHERE city_slug = ?
    """, (source_id, source_type, slug))
    conn.commit()
    print(f"[PIPELINE]   DONE: {slug} — {result.get('permits_inserted', 0)} permits")


def _mark_city_no_source(slug):
    """Mark a city as checked but no source found."""
    conn = permitdb.get_connection()
    conn.execute("""
        UPDATE prod_cities SET pipeline_checked_at = datetime('now')
        WHERE city_slug = ?
    """, (slug,))
    conn.commit()
    print(f"[PIPELINE]   NO SOURCE: {slug}")
