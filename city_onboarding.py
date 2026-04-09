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
import sys
import time
from datetime import datetime, timedelta

import db as permitdb


def _log(msg):
    """V111b: Print with flush so logs appear in Render/gunicorn."""
    print(msg, flush=True)

SIX_MONTHS_AGO = (datetime.utcnow() - timedelta(days=180)).strftime('%Y-%m-%dT00:00:00')
SIX_MONTHS_DATE = (datetime.utcnow() - timedelta(days=180)).strftime('%Y-%m-%d')

STATE_NAMES = {
    'AL': 'alabama', 'AK': 'alaska', 'AZ': 'arizona', 'AR': 'arkansas',
    'CA': 'california', 'CO': 'colorado', 'CT': 'connecticut', 'DE': 'delaware',
    'FL': 'florida', 'GA': 'georgia', 'HI': 'hawaii', 'ID': 'idaho',
    'IL': 'illinois', 'IN': 'indiana', 'IA': 'iowa', 'KS': 'kansas',
    'KY': 'kentucky', 'LA': 'louisiana', 'ME': 'maine', 'MD': 'maryland',
    'MA': 'massachusetts', 'MI': 'michigan', 'MN': 'minnesota', 'MS': 'mississippi',
    'MO': 'missouri', 'MT': 'montana', 'NE': 'nebraska', 'NV': 'nevada',
    'NH': 'newhampshire', 'NJ': 'newjersey', 'NM': 'newmexico', 'NY': 'newyork',
    'NC': 'northcarolina', 'ND': 'northdakota', 'OH': 'ohio', 'OK': 'oklahoma',
    'OR': 'oregon', 'PA': 'pennsylvania', 'RI': 'rhodeisland', 'SC': 'southcarolina',
    'SD': 'southdakota', 'TN': 'tennessee', 'TX': 'texas', 'UT': 'utah',
    'VT': 'vermont', 'VA': 'virginia', 'WA': 'washington', 'WV': 'westvirginia',
    'WI': 'wisconsin', 'WY': 'wyoming',
}


# ================================================================
# V109: SOURCE VALIDATION
# ================================================================

def _is_relevant_socrata_domain(domain, city, state):
    """V109: Only accept Socrata datasets from domains matching the target city/state."""
    domain_lower = domain.lower()
    city_lower = city.lower().replace(' ', '').replace('.', '').replace("'", "")
    state_lower = state.lower()
    state_name = STATE_NAMES.get(state.upper(), '')

    if city_lower in domain_lower:
        return True
    if f"cityof{city_lower}" in domain_lower:
        return True
    if state_name and state_name in domain_lower:
        return True
    if f"data.{state_lower}." in domain_lower:
        return True
    if f"data.{state_lower}.gov" in domain_lower:
        return True
    if f"county{state_lower}" in domain_lower or f"{state_lower}county" in domain_lower:
        return True
    # V110: Match data.XX.gov pattern for 2-letter state abbreviations
    if re.match(r'^data\.[a-z]{2}\.gov$', domain_lower) and domain_lower.split('.')[1] == state_lower:
        return True
    return False


def _classify_socrata_domain(domain, city, state):
    """V110: Classify domain as city-specific, state, or county portal."""
    domain_lower = domain.lower()
    city_lower = city.lower().replace(' ', '').replace('.', '').replace("'", "")

    # State portals: data.XX.gov or known state domains
    if re.match(r'^data\.[a-z]{2}\.gov$', domain_lower):
        return 'state'
    state_name = STATE_NAMES.get(state.upper(), '')
    if state_name and state_name in domain_lower and city_lower not in domain_lower:
        return 'state'

    # County portals
    if 'county' in domain_lower:
        return 'county'

    # City-specific: domain contains city name
    if city_lower in domain_lower.replace('.', '').replace('-', ''):
        return 'city'
    if f"cityof{city_lower}" in domain_lower.replace('.', '').replace('-', ''):
        return 'city'

    return 'unknown'


def _validate_pulled_data(records, city, state, field_map, domain_type='city'):
    """V110: Verify pulled data belongs to this city. Stricter for state/county portals."""
    if not records:
        return False, "no records"

    # V110: State/county portals with no city field = REJECT
    city_field = field_map.get('city_field', '')
    if domain_type in ('state', 'county', 'unknown') and not city_field:
        return False, f"{domain_type} portal with no city field — can't verify data belongs to {city}"

    city_lower = city.lower()
    sample = records[:50]
    matches = 0

    for record in sample:
        record_text = ' '.join(str(v) for v in record.values()).lower()
        if city_lower in record_text:
            matches += 1

    match_rate = matches / len(sample) if sample else 0

    # V110: Higher threshold for state/county portals
    threshold = 0.5 if domain_type in ('state', 'county', 'unknown') else 0.1
    _log(f"[PIPELINE]     Validation: {matches}/{len(sample)} mention '{city}' ({match_rate:.0%}, threshold {threshold:.0%})")

    if match_rate >= threshold:
        return True, f"match rate: {match_rate:.0%}"
    else:
        return False, f"match rate {match_rate:.0%} below {threshold:.0%} threshold"


def _save_pipeline_progress(slug, status, source_found='', permits_inserted=0, error=''):
    """V109: Save per-city pipeline progress for resume support."""
    try:
        conn = permitdb.get_connection()
        conn.execute("""
            INSERT OR REPLACE INTO pipeline_progress
            (city_slug, status, source_found, permits_inserted, error_message, processed_at)
            VALUES (?, ?, ?, ?, ?, datetime('now'))
        """, (slug, status, source_found, permits_inserted, error[:500] if error else ''))
        conn.commit()
    except Exception:
        pass


# ================================================================
# MAIN PIPELINE
# ================================================================

def run_city_pipeline(min_population=50000, batch_size=25):
    """Process cities one by one, highest population first.
    For each: find source → pull 6 months → if data lands, city is done."""
    conn = permitdb.get_connection()

    # V109: Skip cities already processed by pipeline (resume support)
    rows = conn.execute("""
        SELECT pc.city_slug, pc.city, pc.state, pc.population, pc.source_type, pc.source_id
        FROM prod_cities pc
        LEFT JOIN pipeline_progress pp ON pc.city_slug = pp.city_slug
        WHERE pc.population >= ?
        AND (pc.total_permits = 0 OR pc.total_permits IS NULL)
        AND pc.status = 'active'
        AND pc.city NOT LIKE 'Balance of%%'
        AND (pp.status IS NULL OR pp.status = 'error')
        ORDER BY pc.population DESC
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
            _save_pipeline_progress(slug, 'done', source.get('source_id', ''), source['permits_inserted'])
            results.append({
                'city': city, 'state': state, 'pop': pop,
                'status': 'DONE', 'source': source.get('source_id', ''),
                'permits': source['permits_inserted']
            })
        else:
            _mark_city_no_source(slug)
            err = source.get('error') if source else 'nothing found'
            _save_pipeline_progress(slug, 'no_source', error=err)
            results.append({
                'city': city, 'state': state, 'pop': pop,
                'status': 'NO_SOURCE', 'error': err
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
        _log(f"[PIPELINE] Error logging run: {e}")

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
    _log(f"[PIPELINE]   Trying Socrata for {city}, {state}...")

    discovery_results = _socrata_discovery_search(city, state)

    for dr in discovery_results:
        domain = dr['domain']
        dataset_id = dr['dataset_id']
        name = dr.get('name', '')

        # V109: CRITICAL — only accept domains matching this city/state
        if not _is_relevant_socrata_domain(domain, city, state):
            _log(f"[PIPELINE]     SKIP irrelevant domain: {domain} for {city}, {state}")
            continue

        name_lower = name.lower()
        if not any(kw in name_lower for kw in ['permit', 'building', 'construction', 'development']):
            continue

        # V109: Skip aggregate/census datasets
        skip_kw = ['census', 'age of building', 'vacancy', 'rent', 'median', 'count', 'summary', 'aggregate']
        if any(kw in name_lower for kw in skip_kw):
            _log(f"[PIPELINE]     SKIP aggregate: {name}")
            continue

        _log(f"[PIPELINE]     RELEVANT: {name} on {domain} ({dataset_id})")

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
                    _log(f"[PIPELINE]     Found on {domain}: {resource.get('name')} ({dataset_id})")
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
        _log(f"[PIPELINE]     Socrata discovery error: {e}")
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

        # V110: Classify domain and require city field for state/county portals
        domain_type = _classify_socrata_domain(domain, city, state)
        city_field = field_map.get('city_field')

        if domain_type in ('state', 'county', 'unknown') and not city_field:
            return {'permits_inserted': 0, 'error': f'{domain_type} portal with no city field — cannot filter'}

        # Build query
        where = f"{date_field} >= '{SIX_MONTHS_AGO}'"
        if city_field:
            variations = _get_city_name_variations(city)
            city_filter = " OR ".join([f"upper({city_field}) = '{v.upper()}'" for v in variations])
            where = f"({where}) AND ({city_filter})"
            _log(f"[PIPELINE]     Filtering {domain_type} portal by city: {city_field}='{city}'")

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

        # V109/V110: Validate data belongs to this city before inserting
        is_valid, reason = _validate_pulled_data(all_records, city, state, field_map, domain_type)
        if not is_valid:
            _log(f"[PIPELINE]     REJECTED: {reason}")
            return {'permits_inserted': 0, 'error': f'validation failed: {reason}'}
        _log(f"[PIPELINE]     VALIDATED: {reason}")

        # Insert into permits table
        inserted = _insert_permits(slug, city, state, all_records, field_map, 'socrata')
        _log(f"[PIPELINE]     Socrata pull: {len(all_records)} found, {inserted} inserted")
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
    _log(f"[PIPELINE]   Trying ArcGIS for {city}, {state}...")
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

            # V109: Check if title/URL contains the city or state name
            city_lower = city.lower()
            state_name = STATE_NAMES.get(state.upper(), '')
            if not (city_lower in title or (state_name and state_name in title)):
                _log(f"[PIPELINE]     SKIP irrelevant ArcGIS: {ds.get('title', '')}")
                continue

            api_url = None
            for dist in ds.get('distribution', []):
                access_url = dist.get('accessURL', '')
                if 'FeatureServer' in access_url or 'MapServer' in access_url:
                    api_url = access_url
                    break
            if not api_url:
                continue

            _log(f"[PIPELINE]     RELEVANT ArcGIS: {ds.get('title')} at {api_url}")
            result = _arcgis_pull_6months(api_url, city, state, slug)
            if result and result.get('permits_inserted', 0) > 0:
                return result

    except Exception as e:
        _log(f"[PIPELINE]     ArcGIS search error: {e}")
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
        _log(f"[PIPELINE]     ArcGIS pull: {len(all_features)} found, {inserted} inserted")
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
        _log(f"[PIPELINE] Error updating counters for {slug}: {e}")


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
    _log(f"[PIPELINE]   DONE: {slug} — {result.get('permits_inserted', 0)} permits")


def _mark_city_no_source(slug):
    """Mark a city as checked but no source found."""
    conn = permitdb.get_connection()
    conn.execute("""
        UPDATE prod_cities SET pipeline_checked_at = datetime('now')
        WHERE city_slug = ?
    """, (slug,))
    conn.commit()
    _log(f"[PIPELINE]   NO SOURCE: {slug}")


# ================================================================
# V111: SEARCH-POWERED PIPELINE (DuckDuckGo + URL patterns)
# ================================================================

def run_search_pipeline(min_population=100000, batch_size=25):
    """V111: Process cities by population, using web search to find data sources."""
    conn = permitdb.get_connection()

    rows = conn.execute("""
        SELECT pc.city_slug, pc.city, pc.state, pc.population, pc.id as prod_city_id
        FROM prod_cities pc
        LEFT JOIN pipeline_progress pp ON pc.city_slug = pp.city_slug
        WHERE pc.population >= ?
        AND (pc.total_permits = 0 OR pc.total_permits IS NULL)
        AND pc.status = 'active'
        AND pc.city NOT LIKE 'Balance of%%'
        AND (pp.status IS NULL OR pp.status = 'error')
        ORDER BY pc.population DESC
        LIMIT ?
    """, (min_population, batch_size)).fetchall()

    _log(f"[PIPELINE] V111 Search pipeline: {len(rows)} cities to process")
    results = []

    for row in rows:
        slug, city, state, pop, pc_id = row[0], row[1], row[2], row[3], row[4]
        print(f"\n[PIPELINE] === {city}, {state} (pop {pop:,}, slug: {slug}) ===")

        # Search for sources
        candidates = _search_city_sources(city, state)

        if not candidates:
            _log(f"[PIPELINE]   No candidates found")
            _save_pipeline_progress(slug, 'no_source', error='no candidates from search')
            results.append({'city': city, 'state': state, 'status': 'NO_SOURCE'})
            time.sleep(1)
            continue

        # Test each candidate
        found = False
        for candidate in candidates:
            success, count = _test_candidate(candidate, slug, city, state, pc_id)
            if success and count >= 10:
                _save_pipeline_progress(slug, 'done',
                                        source_found=candidate.get('url', '')[:200],
                                        permits_inserted=count)
                results.append({'city': city, 'state': state, 'status': 'DONE',
                                'permits': count, 'source': candidate.get('url', '')[:100]})
                found = True
                break
            elif success and count == 0:
                # V112: Discovery-only platform found (EnerGov, Citizenserve, etc.)
                results.append({'city': city, 'state': state, 'status': 'FOUND_PORTAL',
                                'platform': candidate.get('platform', ''),
                                'source': candidate.get('url', '')[:100]})
                found = True
                break

        if not found:
            _save_pipeline_progress(slug, 'no_source',
                                    error=f'tested {len(candidates)} candidates, none worked')
            results.append({'city': city, 'state': state, 'status': 'NO_SOURCE',
                            'candidates_tried': len(candidates)})

        # V112: Summary log per city
        status = results[-1].get('status', '?')
        _log(f"[PIPELINE] === {city}, {state} === Candidates: {len(candidates)}, Result: {status}")

        time.sleep(1)

    done = sum(1 for r in results if r.get('status') == 'DONE')
    print(f"\n[PIPELINE] V111 complete: {done}/{len(results)} cities got data")

    # Log run
    try:
        conn = permitdb.get_connection()
        conn.execute("""
            INSERT INTO pipeline_runs (started_at, completed_at, results_json, cities_processed, cities_succeeded)
            VALUES (datetime('now'), datetime('now'), ?, ?, ?)
        """, (json.dumps(results), len(results), done))
        conn.commit()
    except Exception:
        pass

    return results


def _search_ddg(query, max_results=10):
    """V112: Search DuckDuckGo with validation and fallback."""
    try:
        from duckduckgo_search import DDGS
        ddgs = DDGS()
        results = ddgs.text(query, max_results=max_results)
        if results:
            _log(f"[SEARCH] Query: {query[:60]} -> {len(results)} results")
            return results
    except Exception as e:
        _log(f"[SEARCH] DDGS error: {e}")

    # V112: Fallback — scrape DuckDuckGo HTML directly
    try:
        resp = requests.get('https://html.duckduckgo.com/html/',
                            params={'q': query},
                            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'},
                            timeout=10)
        if resp.status_code == 200:
            urls = re.findall(r'href="(https?://[^"]+)"', resp.text)
            urls = [u for u in urls if 'duckduckgo.com' not in u][:max_results]
            if urls:
                _log(f"[SEARCH] HTML fallback: {len(urls)} URLs for: {query[:50]}")
                return [{'href': u, 'title': '', 'body': ''} for u in urls]
    except Exception as e:
        _log(f"[SEARCH] HTML fallback error: {e}")

    return []


def _search_city_sources(city, state):
    """V111/V112: Search for data sources using URL patterns + DuckDuckGo."""
    candidates = []

    # Phase 1: Try known URL patterns (reliable, instant)
    candidates.extend(_try_url_patterns(city, state))

    # Phase 2: DuckDuckGo search (fallback)
    queries = [
        f'"{city}" "{state}" building permits open data',
        f'"{city}" {state} building permits socrata OR arcgis OR accela',
        f'"{city}" {state} permit data download',
    ]
    for q in queries:
        results = _search_ddg(q, max_results=10)
        for r in results:
            url = r.get('href', r.get('link', ''))
            title = r.get('title', '')
            parsed = _classify_search_url(url, title, city, state)
            if parsed:
                candidates.append(parsed)
        if results:
            time.sleep(2)  # V112: Rate limit protection

    # Deduplicate
    seen = set()
    unique = []
    for c in candidates:
        key = c.get('dataset_id', c['url'])
        if key not in seen:
            seen.add(key)
            unique.append(c)

    _log(f"[PIPELINE]   Found {len(unique)} candidates")
    return unique


def _try_url_patterns(city, state):
    """V112: Probe known open data portal URL patterns — expanded with EnerGov, Citizenserve, ViewPoint."""
    candidates = []
    city_slug = city.lower().replace(' ', '').replace('.', '').replace("'", "")
    state_lower = state.lower()

    # --- Socrata portal patterns ---
    socrata_domains = [
        f"data.{city_slug}.gov", f"data.cityof{city_slug}.org",
        f"opendata.{city_slug}.gov", f"data.{city_slug}{state_lower}.gov",
        f"data.cityof{city_slug}.gov", f"data.{city_slug}.us",
    ]
    for domain in socrata_domains:
        try:
            cat_resp = requests.get(
                f"https://{domain}/api/catalog/v1?q=building+permits&limit=5", timeout=5)
            if cat_resp.status_code == 200 and 'results' in cat_resp.text:
                _log(f"[PROBE] Socrata portal found: {domain}")
                for ds in cat_resp.json().get('results', []):
                    resource = ds.get('resource', {})
                    ds_id = resource.get('id')
                    ds_name = resource.get('name', '')
                    if ds_id and any(kw in ds_name.lower() for kw in ['permit', 'building', 'construction']):
                        candidates.append({
                            'url': f"https://{domain}/resource/{ds_id}.json",
                            'platform': 'socrata', 'domain': domain,
                            'dataset_id': ds_id, 'title': ds_name, 'source': 'url_pattern'
                        })
        except Exception:
            pass

    # --- EnerGov / Tyler Technologies patterns ---
    energov_patterns = [
        f"https://css.cityof{city_slug}.com/energov_prod/selfservice",
        f"https://css.{city_slug}.gov/energov_prod/selfservice",
        f"https://energov.cityof{city_slug}.com/energov_prod/selfservice",
        f"https://energov.{city_slug}.gov/energov_prod/selfservice",
    ]
    for url in energov_patterns:
        try:
            resp = requests.head(url, timeout=5, allow_redirects=True)
            if resp.status_code == 200:
                candidates.append({'url': url, 'platform': 'energov',
                                   'title': f'{city} EnerGov Portal', 'source': 'url_pattern'})
                _log(f"[PROBE] EnerGov found: {url}")
                break
        except Exception:
            pass

    # --- Citizenserve patterns ---
    for url in [f"https://www.citizenserve.com/Portal/PortalController?CommunityId={city_slug}"]:
        try:
            resp = requests.head(url, timeout=5, allow_redirects=True)
            if resp.status_code == 200:
                candidates.append({'url': url, 'platform': 'citizenserve',
                                   'title': f'{city} Citizenserve', 'source': 'url_pattern'})
                _log(f"[PROBE] Citizenserve found: {url}")
                break
        except Exception:
            pass

    # --- ViewPoint Cloud patterns ---
    for url in [f"https://{city_slug}.viewpointcloud.com",
                f"https://cityof{city_slug}.viewpointcloud.com"]:
        try:
            resp = requests.head(url, timeout=5, allow_redirects=True)
            if resp.status_code == 200:
                candidates.append({'url': url, 'platform': 'viewpoint',
                                   'title': f'{city} ViewPoint Cloud', 'source': 'url_pattern'})
                _log(f"[PROBE] ViewPoint found: {url}")
                break
        except Exception:
            pass

    return candidates


def _classify_search_url(url, title, city, state):
    """V112: Classify a search result URL by platform. Expanded platform support."""
    url_lower = url.lower()
    city_lower = city.lower()

    # V112: Relaxed city check — skip for known platform domains
    known_platforms = ['socrata.com', 'arcgis.com', 'accela.com', 'energov',
                       'citizenserve.com', 'viewpointcloud.com', 'tylerhost.net',
                       'opendatasoft.com', 'iworq.net', 'opengov.com']
    is_known_platform = any(p in url_lower for p in known_platforms)

    if not is_known_platform:
        text = (url_lower + ' ' + title.lower())
        if city_lower.split()[0] not in text:
            return None

    # Socrata dataset link
    socrata_match = re.search(r'([\w.-]+\.(?:gov|org|com))/(?:resource|d)/([\w]{4}-[\w]{4})', url_lower)
    if socrata_match:
        domain = socrata_match.group(1)
        dataset_id = socrata_match.group(2)
        # V110: Validate domain relevance
        if not _is_relevant_socrata_domain(domain, city, state):
            return None
        return {'url': url, 'platform': 'socrata', 'domain': domain,
                'dataset_id': dataset_id, 'title': title, 'source': 'search'}

    # ArcGIS FeatureServer/MapServer
    arcgis_match = re.search(r'(https?://[^/]+/[^\s]+(?:Feature|Map)Server/\d+)', url, re.IGNORECASE)
    if arcgis_match:
        return {'url': arcgis_match.group(1), 'platform': 'arcgis',
                'title': title, 'source': 'search'}

    # ArcGIS Hub / opendata
    if 'hub.arcgis.com' in url_lower or 'opendata.arcgis.com' in url_lower:
        return {'url': url, 'platform': 'arcgis_hub', 'title': title, 'source': 'search'}

    # Accela portals
    if 'accela.com' in url_lower:
        return {'url': url, 'platform': 'accela', 'title': title, 'source': 'search'}

    # V111b: Catch city open data portals (data.{city}.gov, opendata.{city}.gov)
    city_slug = city.lower().replace(' ', '').replace('.', '').replace("'", "")
    if re.match(r'https?://(?:data|opendata)\.[\w-]+\.gov/?', url_lower):
        if city_slug in url_lower.replace('.', '').replace('-', ''):
            return {'url': url, 'platform': 'portal', 'title': title, 'source': 'search'}

    # V112: EnerGov / Tyler Technologies
    if 'energov' in url_lower or ('css.' in url_lower and 'selfservice' in url_lower):
        return {'url': url, 'platform': 'energov', 'title': title, 'source': 'search'}
    if 'tylerhost.net' in url_lower:
        return {'url': url, 'platform': 'tyler', 'title': title, 'source': 'search'}

    # V112: Citizenserve
    if 'citizenserve.com' in url_lower:
        return {'url': url, 'platform': 'citizenserve', 'title': title, 'source': 'search'}

    # V112: ViewPoint Cloud
    if 'viewpointcloud.com' in url_lower:
        return {'url': url, 'platform': 'viewpoint', 'title': title, 'source': 'search'}

    # V111b: Generic open data with permit keywords in title
    permit_keywords = ['permit', 'building', 'construction']
    if any(kw in title.lower() for kw in permit_keywords):
        if any(kw in url_lower for kw in ['.gov', 'opendata', 'data.']):
            return {'url': url, 'platform': 'portal', 'title': title, 'source': 'search'}

    return None


def _test_candidate(candidate, slug, city, state, prod_city_id):
    """V111: Test a candidate source — pull data, validate, save if good."""
    platform = candidate.get('platform', '')
    _log(f"[PIPELINE]   Testing {platform}: {candidate.get('title', candidate['url'][:60])}")

    # V112: Discovery-only platforms — record but can't auto-pull
    DISCOVERY_ONLY = ['energov', 'citizenserve', 'viewpoint', 'tyler', 'city_portal']
    if platform in DISCOVERY_ONLY:
        _log(f"[PIPELINE]   FOUND {platform} portal: {candidate['url']}")
        try:
            conn = permitdb.get_connection()
            conn.execute("""
                INSERT OR REPLACE INTO pipeline_progress
                (city_slug, status, source_found, error_message, processed_at)
                VALUES (?, 'found_portal', ?, ?, datetime('now'))
            """, (slug, candidate['url'][:200],
                  f"Platform: {platform} — no API scraper yet"))
            conn.execute("""
                UPDATE prod_cities SET source_type = ?, pipeline_checked_at = datetime('now')
                WHERE city_slug = ?
            """, (platform, slug))
            conn.commit()
        except Exception:
            pass
        return True, 0  # Found something, stop searching (but 0 permits)

    try:
        if platform == 'socrata' and candidate.get('dataset_id'):
            return _test_socrata(candidate, slug, city, state, prod_city_id)
        elif platform == 'arcgis':
            result = _arcgis_pull_6months(candidate['url'], city, state, slug)
            if result and result.get('permits_inserted', 0) >= 10:
                _mark_city_done(slug, result)
                return True, result['permits_inserted']
        elif platform == 'portal':
            return _explore_portal(candidate, slug, city, state, prod_city_id)
        return False, 0
    except Exception as e:
        _log(f"[PIPELINE]   Test failed: {e}")
        return False, 0


def _test_socrata(candidate, slug, city, state, prod_city_id):
    """V111: Test a specific Socrata dataset."""
    domain = candidate['domain']
    dataset_id = candidate['dataset_id']

    domain_type = _classify_socrata_domain(domain, city, state)

    # Get metadata
    try:
        meta = requests.get(f"https://{domain}/api/views/{dataset_id}.json", timeout=10).json()
        columns = meta.get('columns', [])
        col_names = [c['fieldName'] for c in columns]
        col_types = {c['fieldName']: c.get('dataTypeName', '') for c in columns}
    except Exception:
        return False, 0

    field_map = _auto_detect_fields(col_names, col_types)
    date_field = field_map.get('date_field')
    if not date_field:
        _log(f"[PIPELINE]     No date field detected")
        return False, 0

    city_field = field_map.get('city_field')
    if domain_type in ('state', 'county', 'unknown') and not city_field:
        _log(f"[PIPELINE]     REJECT: {domain_type} portal, no city field")
        return False, 0

    # Build query
    six_months = (datetime.utcnow() - timedelta(days=180)).strftime('%Y-%m-%dT00:00:00')
    where = f"{date_field} >= '{six_months}'"
    if city_field:
        variations = _get_city_name_variations(city)
        city_filter = " OR ".join([f"upper({city_field}) = '{v.upper()}'" for v in variations])
        where = f"({where}) AND ({city_filter})"
        _log(f"[PIPELINE]     Filtering by {city_field}='{city}'")

    # Pull
    url = f"https://{domain}/resource/{dataset_id}.json?$where={where}&$limit=5000&$order=:id"
    try:
        resp = requests.get(url, timeout=30)
        if resp.status_code != 200:
            return False, 0
        records = resp.json()
    except Exception:
        return False, 0

    if not records or len(records) < 10:
        _log(f"[PIPELINE]     Only {len(records) if records else 0} records")
        return False, 0

    # Validate
    is_valid, reason = _validate_pulled_data(records, city, state, field_map, domain_type)
    if not is_valid:
        _log(f"[PIPELINE]     REJECTED: {reason}")
        return False, 0
    _log(f"[PIPELINE]     VALIDATED: {reason}")

    # Save permits
    inserted = _insert_permits(slug, city, state, records, field_map, 'socrata')
    if inserted >= 10:
        # Update prod_cities
        conn = permitdb.get_connection()
        conn.execute("""
            UPDATE prod_cities SET
                source_id = ?, source_type = 'socrata',
                total_permits = (SELECT COUNT(*) FROM permits WHERE source_city_key = ?),
                health_status = 'collecting', backfill_status = 'complete',
                last_successful_collection = datetime('now'),
                pipeline_checked_at = datetime('now')
            WHERE city_slug = ?
        """, (f"{domain}:{dataset_id}", slug, slug))
        conn.commit()
        _log(f"[PIPELINE]   SUCCESS: {city} — {inserted} permits from {domain}:{dataset_id}")
        return True, inserted

    return False, 0


def _explore_portal(candidate, slug, city, state, prod_city_id):
    """V111b: Explore a data portal — search its catalog for permit datasets."""
    url = candidate.get('url', '')
    # Extract domain from URL
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        domain = parsed.netloc
    except Exception:
        return False, 0

    if not domain:
        return False, 0

    _log(f"[PIPELINE]     Exploring portal: {domain}")

    # Try Socrata catalog API
    try:
        cat_url = f"https://{domain}/api/catalog/v1?q=building+permits&limit=10"
        resp = requests.get(cat_url, timeout=8)
        if resp.status_code == 200:
            data = resp.json()
            for ds in data.get('results', []):
                resource = ds.get('resource', {})
                ds_id = resource.get('id')
                ds_name = resource.get('name', '')
                if ds_id and any(kw in ds_name.lower() for kw in ['permit', 'building', 'construction']):
                    _log(f"[PIPELINE]     Found dataset: {ds_name} ({ds_id})")
                    test_candidate = {
                        'url': f"https://{domain}/resource/{ds_id}.json",
                        'platform': 'socrata', 'domain': domain,
                        'dataset_id': ds_id, 'title': ds_name, 'source': 'portal_explore'
                    }
                    success, count = _test_socrata(test_candidate, slug, city, state, prod_city_id)
                    if success:
                        return True, count
    except Exception as e:
        _log(f"[PIPELINE]     Portal catalog error: {e}")

    return False, 0
