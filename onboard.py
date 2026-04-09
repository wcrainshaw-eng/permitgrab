"""
V121: Google-Test-Add Onboarding Machine

For each city: web-search for its permit portal, detect the platform,
find the permit dataset, pull 6 months of real data, validate it matches
the city, and add it. One city at a time.
"""

import json
import re
import time
import requests
from urllib.parse import urlparse
from datetime import datetime, timedelta

import db as permitdb


def _log(msg):
    print(msg, flush=True)


# ================================================================
# STEP 1: WEB SEARCH
# ================================================================

def web_search_permits(city, state):
    """Search the web for a city's open permit data portal."""
    candidates = []
    seen = set()

    queries = [
        f"{city} {state} building permits open data",
        f"{city} {state} permit data portal API",
    ]

    try:
        from duckduckgo_search import DDGS
    except ImportError:
        _log("[ONBOARD] duckduckgo_search not installed")
        return candidates

    for q in queries:
        try:
            with DDGS() as ddgs:
                results = list(ddgs.text(q, max_results=5))
            for r in results:
                url = r.get('href', '')
                if url:
                    domain = urlparse(url).netloc.lower()
                    if domain not in seen:
                        seen.add(domain)
                        candidates.append(url)
            time.sleep(1)
        except Exception as e:
            _log(f"[ONBOARD] Search error for '{q[:50]}': {e}")
    return candidates


# ================================================================
# STEP 2: PLATFORM DETECTION
# ================================================================

SKIP_DOMAINS = {'google.com', 'youtube.com', 'facebook.com', 'twitter.com',
                'reddit.com', 'wikipedia.org', 'yelp.com', 'linkedin.com',
                'amazon.com', 'zillow.com', 'indeed.com', 'nextdoor.com'}


def probe_platform(url):
    """Detect what data platform a URL runs on."""
    parsed = urlparse(url)
    domain = parsed.netloc.lower()
    base = f"{parsed.scheme}://{domain}"
    headers = {'User-Agent': 'PermitGrab/1.0'}

    if any(d in domain for d in SKIP_DOMAINS):
        return None

    # ArcGIS (check URL pattern first — no HTTP request needed)
    if '/arcgis/' in url.lower() or '/rest/services' in url.lower() or 'FeatureServer' in url or 'MapServer' in url:
        return {'platform': 'arcgis', 'domain': domain, 'base_url': base, 'original_url': url}

    # Accela
    if 'accela' in domain or 'citizenaccess' in domain:
        return {'platform': 'accela', 'domain': domain, 'base_url': base}

    # Socrata (probe /api/views endpoint)
    try:
        r = requests.get(f"{base}/api/views.json?limit=1", headers=headers, timeout=8)
        if r.status_code == 200 and isinstance(r.json(), list):
            return {'platform': 'socrata', 'domain': domain, 'base_url': base}
    except Exception:
        pass

    # CKAN (probe /api/3/action/status_show)
    try:
        r = requests.get(f"{base}/api/3/action/status_show", headers=headers, timeout=8)
        if r.status_code == 200 and r.json().get('success'):
            return {'platform': 'ckan', 'domain': domain, 'base_url': base}
    except Exception:
        pass

    # ArcGIS (probe /arcgis/rest/services)
    try:
        r = requests.get(f"{base}/arcgis/rest/services?f=json", headers=headers, timeout=8)
        if r.status_code == 200 and ('services' in r.json() or 'folders' in r.json()):
            return {'platform': 'arcgis', 'domain': domain, 'base_url': f"{base}/arcgis"}
    except Exception:
        pass

    return None


# ================================================================
# STEP 3: FIND PERMIT DATASET
# ================================================================

def find_permit_dataset(platform_info, city, state):
    """Search within a detected platform for permit datasets."""
    platform = platform_info['platform']
    base = platform_info['base_url']
    domain = platform_info['domain']
    headers = {'User-Agent': 'PermitGrab/1.0'}

    if platform == 'socrata':
        return _find_socrata_dataset(base, domain, headers)
    elif platform == 'arcgis':
        return _find_arcgis_dataset(platform_info, headers)
    elif platform == 'ckan':
        return _find_ckan_dataset(base, domain, headers)
    return None


def _find_socrata_dataset(base, domain, headers):
    for term in ['building permits', 'permits', 'building permit applications']:
        try:
            r = requests.get(f"{base}/api/views.json", params={'q': term, 'limit': 10},
                             headers=headers, timeout=15)
            if r.status_code != 200:
                continue
            views = r.json()
            if not isinstance(views, list):
                continue
            for view in views:
                name = (view.get('name') or '').lower()
                if not any(kw in name for kw in ['permit', 'building', 'construction']):
                    continue
                dataset_id = view.get('id')
                if not dataset_id:
                    continue

                columns = view.get('columns', [])
                col_names = [c.get('fieldName', '') for c in columns]
                date_field = _find_date_field(col_names)
                if not date_field:
                    continue

                return {
                    'platform': 'socrata', 'endpoint': f"https://{domain}/resource/{dataset_id}.json",
                    'dataset_id': dataset_id, 'date_field': date_field,
                    'field_map': _build_field_map(col_names), 'domain': domain,
                    'source_name': view.get('name', f'Socrata {dataset_id}')
                }
        except Exception:
            continue
    return None


def _find_arcgis_dataset(platform_info, headers):
    original_url = platform_info.get('original_url', '')
    base = platform_info['base_url']
    domain = platform_info['domain']

    # Direct URL to a service
    if '/FeatureServer' in original_url or '/MapServer' in original_url:
        svc_url = original_url.split('/query')[0] if '/query' in original_url else original_url
        if not svc_url.rstrip('/').split('/')[-1].isdigit():
            svc_url = svc_url.rstrip('/') + '/0'
        return {'platform': 'arcgis', 'endpoint': svc_url, 'domain': domain,
                'source_name': f'ArcGIS {domain}'}

    # Enumerate services
    try:
        r = requests.get(f"{base}/rest/services?f=json", headers=headers, timeout=15)
        if r.status_code != 200:
            return None
        data = r.json()
        all_services = data.get('services', [])
        for folder in data.get('folders', []):
            try:
                fr = requests.get(f"{base}/rest/services/{folder}?f=json", headers=headers, timeout=10)
                if fr.status_code == 200:
                    for svc in fr.json().get('services', []):
                        svc['name'] = f"{folder}/{svc['name']}" if '/' not in svc.get('name', '') else svc['name']
                        all_services.append(svc)
            except Exception:
                continue

        for svc in all_services:
            name = (svc.get('name') or '').lower()
            if any(kw in name for kw in ['permit', 'building', 'construction', 'development']):
                svc_type = svc.get('type', 'MapServer')
                svc_url = f"{base}/rest/services/{svc['name']}/{svc_type}/0"
                return {'platform': 'arcgis', 'endpoint': svc_url, 'domain': domain,
                        'source_name': f'ArcGIS {svc["name"]}'}
    except Exception:
        pass
    return None


def _find_ckan_dataset(base, domain, headers):
    for term in ['building permits', 'permits']:
        try:
            r = requests.get(f"{base}/api/3/action/package_search",
                             params={'q': term, 'rows': 5}, headers=headers, timeout=15)
            if r.status_code != 200:
                continue
            data = r.json()
            if not data.get('success'):
                continue
            for pkg in data.get('result', {}).get('results', []):
                name = (pkg.get('title') or '').lower()
                if not any(kw in name for kw in ['permit', 'building', 'construction']):
                    continue
                for res in pkg.get('resources', []):
                    fmt = (res.get('format') or '').lower()
                    if fmt in ('csv', 'json', 'geojson') or res.get('url', '').endswith(('.csv', '.json')):
                        return {'platform': 'ckan', 'endpoint': res['url'], 'domain': domain,
                                'dataset_id': pkg.get('id', ''), 'resource_format': fmt,
                                'source_name': pkg.get('title', f'CKAN {domain}')}
        except Exception:
            continue
    return None


# ================================================================
# STEP 4: TEST PULL (6 months)
# ================================================================

def test_pull_6months(source_config):
    """Pull 6 months of data. Returns (raw_records, normalized_permits, error)."""
    platform = source_config['platform']
    endpoint = source_config['endpoint']
    date_field = source_config.get('date_field')
    field_map = source_config.get('field_map', {})
    headers = {'User-Agent': 'PermitGrab/1.0'}
    six_months_ago = (datetime.utcnow() - timedelta(days=180)).strftime('%Y-%m-%dT00:00:00')
    raw_records = []

    try:
        if platform == 'socrata':
            params = {'$limit': 2000, '$order': f'{date_field} DESC' if date_field else ':id'}
            if date_field:
                params['$where'] = f"{date_field} > '{six_months_ago}'"
            r = requests.get(endpoint, params=params, headers=headers, timeout=30)
            if not r.ok:
                return [], [], f"HTTP {r.status_code}"
            raw_records = r.json() if isinstance(r.json(), list) else []

        elif platform == 'arcgis':
            query_url = endpoint.rstrip('/') + '/query'
            # Get field info
            try:
                info = requests.get(endpoint + '?f=json', headers=headers, timeout=15).json()
                fields = info.get('fields', [])
                field_names = [f.get('name', '') for f in fields]
                if not date_field:
                    for f in fields:
                        if f.get('type') == 'esriFieldTypeDate':
                            fn = f.get('name', '').lower()
                            if any(kw in fn for kw in ['issue', 'date', 'applied', 'created']):
                                date_field = f['name']
                                source_config['date_field'] = date_field
                                break
                if not field_map:
                    field_map = _build_field_map(field_names)
                    source_config['field_map'] = field_map
            except Exception:
                pass

            where = '1=1'
            if date_field:
                epoch_ms = int((datetime.utcnow() - timedelta(days=180)).timestamp() * 1000)
                where = f"{date_field} > {epoch_ms}"

            params = {'where': where, 'outFields': '*', 'returnGeometry': 'false',
                      'f': 'json', 'resultRecordCount': 2000}
            r = requests.get(query_url, params=params, headers=headers, timeout=30)
            if not r.ok:
                return [], [], f"HTTP {r.status_code}"
            data = r.json()
            if 'error' in data:
                return [], [], f"ArcGIS error: {data['error'].get('message', '')}"
            raw_records = [f.get('attributes', f) for f in data.get('features', [])]

        elif platform == 'ckan':
            r = requests.get(endpoint, headers=headers, timeout=30)
            if not r.ok:
                return [], [], f"HTTP {r.status_code}"
            fmt = source_config.get('resource_format', 'json')
            if fmt == 'csv' or endpoint.endswith('.csv'):
                import csv, io
                raw_records = list(csv.DictReader(io.StringIO(r.text)))[-2000:]
            else:
                data = r.json()
                if isinstance(data, list):
                    raw_records = data[-2000:]
                elif isinstance(data, dict) and 'result' in data:
                    recs = data['result'].get('records', data['result'])
                    raw_records = recs[-2000:] if isinstance(recs, list) else []

        if not raw_records or len(raw_records) < 5:
            return raw_records, [], f"Only {len(raw_records)} records"

        # Normalize
        normalized = [_normalize_for_insert(rec, field_map, platform) for rec in raw_records]
        normalized = [p for p in normalized if p]
        return raw_records, normalized, None

    except requests.exceptions.Timeout:
        return [], [], "Timeout (30s)"
    except Exception as e:
        return [], [], str(e)[:200]


# ================================================================
# STEP 5: VALIDATION
# ================================================================

def validate_city_match(raw_records, city, state):
    """Check RAW records (before normalization) for city/state mentions.
    Returns (is_valid, match_rate, details)."""
    if not raw_records:
        return False, 0.0, "No records"

    city_lower = city.lower()
    state_lower = state.lower() if len(state) > 2 else state.lower()
    state_abbrev = state.lower() if len(state) == 2 else ''

    matches = 0
    checked = min(len(raw_records), 200)
    for record in raw_records[:checked]:
        text = ' '.join(str(v) for v in record.values() if v and isinstance(v, str)).lower()
        if city_lower in text or state_lower in text:
            matches += 1
        elif state_abbrev and f' {state_abbrev} ' in f' {text} ':
            matches += 1

    rate = matches / checked if checked > 0 else 0
    details = f"{matches}/{checked} records ({rate:.0%}) mention {city} or {state}"
    return rate >= 0.20, rate, details


def domain_matches_city(domain, city, state):
    """Quick check if a domain plausibly belongs to this city/state."""
    d = domain.lower().replace('-', '').replace('.', '')
    city_clean = city.lower().replace(' ', '').replace('.', '').replace("'", "")
    state_lower = state.lower() if len(state) > 2 else state.lower()

    if city_clean in d:
        return True
    if len(state) == 2 and state.lower() in domain.lower().split('.'):
        return True
    if state_lower.replace(' ', '') in d:
        return True
    # Generic platforms — let data validation handle it
    if any(gp in domain.lower() for gp in ['socrata.com', 'arcgis.com']):
        return True
    return False


# ================================================================
# HELPERS
# ================================================================

def _find_date_field(col_names):
    for candidate in ['issue_date', 'issued_date', 'issueddate', 'permit_issued_date',
                      'filed_date', 'fileddate', 'applied_date', 'application_date',
                      'created_date', 'status_date', 'date']:
        for c in col_names:
            if candidate in c.lower():
                return c
    for c in col_names:
        if 'date' in c.lower() and not c.startswith(':'):
            return c
    return None


def _build_field_map(col_names):
    fm = {}
    for patterns, key in [
        (['permit_number', 'permitnumber', 'permit_num', 'permitno', 'permit_no',
          'record_number', 'application_number', 'permit_id', 'case_number'], 'permit_number'),
        (['address', 'location', 'site_address', 'street_address', 'property_address'], 'address'),
        (['description', 'work_description', 'permit_type', 'work_type', 'project_description'], 'description'),
        (['estimated_value', 'job_value', 'valuation', 'estimated_cost', 'construction_cost'], 'value'),
        (['status', 'permit_status', 'current_status'], 'status'),
    ]:
        for p in patterns:
            matches = [c for c in col_names if p in c.lower().replace('_', '')]
            if matches:
                fm[key] = matches[0]
                break
    return fm


def _normalize_for_insert(record, field_map, platform):
    """Normalize a raw record. Does NOT stamp city/state."""
    permit = {}
    pn = field_map.get('permit_number', '')
    if pn and pn in record:
        permit['permit_number'] = str(record[pn])
    else:
        for k in record:
            if 'permit' in k.lower() and ('num' in k.lower() or 'no' in k.lower() or 'id' in k.lower()):
                if record[k]:
                    permit['permit_number'] = str(record[k])
                    break
    if not permit.get('permit_number'):
        return None

    addr = field_map.get('address', '')
    if addr and addr in record and record[addr]:
        permit['address'] = str(record[addr])[:300]
    else:
        for k in record:
            if 'address' in k.lower() or 'location' in k.lower():
                if record[k] and isinstance(record[k], str) and len(record[k]) > 5:
                    permit['address'] = record[k][:300]
                    break

    desc = field_map.get('description', '')
    if desc and desc in record and record[desc]:
        permit['description'] = str(record[desc])[:500]
    else:
        for k in record:
            if 'description' in k.lower() or 'type' in k.lower() or 'work' in k.lower():
                if record[k] and isinstance(record[k], str):
                    permit['description'] = str(record[k])[:500]
                    break

    # Date
    for k in record:
        if 'date' in k.lower() or 'issued' in k.lower():
            v = record[k]
            if isinstance(v, (int, float)) and v > 1e12:
                permit['filing_date'] = datetime.utcfromtimestamp(v / 1000).strftime('%Y-%m-%d')
            elif isinstance(v, str) and v.strip():
                permit['filing_date'] = v.strip()[:26]
            if permit.get('filing_date'):
                break

    return permit


# ================================================================
# MAIN ONBOARD FUNCTION
# ================================================================

def onboard_single_city(city_slug, city, state, population):
    """Full onboarding pipeline for one city. Returns result dict."""
    conn = permitdb.get_connection()

    result = {'city_slug': city_slug, 'city': city, 'state': state,
              'population': population, 'steps': [], 'outcome': None}

    # Check if already has fresh permits
    fresh = conn.execute(
        "SELECT COUNT(*) FROM permits WHERE source_city_key = ? AND collected_at > datetime('now', '-7 days')",
        (city_slug,)
    ).fetchone()[0]
    if fresh > 0:
        result['outcome'] = 'already_fresh'
        result['steps'].append(f'{fresh} fresh permits exist')
        return result

    # Web search
    result['steps'].append(f'Searching for "{city} {state} building permits"...')
    candidates = web_search_permits(city, state)
    result['steps'].append(f'Found {len(candidates)} candidate URLs')

    if not candidates:
        result['outcome'] = 'no_candidates'
        return result

    # Try each candidate
    attempts = []
    for url in candidates[:8]:
        attempt = {'url': url[:100]}

        platform_info = probe_platform(url)
        if not platform_info:
            attempt['result'] = 'unrecognized_platform'
            attempts.append(attempt)
            continue

        attempt['platform'] = platform_info['platform']
        attempt['domain'] = platform_info['domain']

        if not domain_matches_city(platform_info['domain'], city, state):
            attempt['result'] = 'domain_mismatch'
            attempts.append(attempt)
            continue

        source_config = find_permit_dataset(platform_info, city, state)
        if not source_config:
            attempt['result'] = 'no_permit_dataset'
            attempts.append(attempt)
            continue

        attempt['endpoint'] = source_config.get('endpoint', '')[:100]

        raw, permits, error = test_pull_6months(source_config)
        if error or len(raw) < 5:
            attempt['result'] = 'test_failed'
            attempt['error'] = error or f'only {len(raw)} records'
            attempts.append(attempt)
            continue

        is_valid, rate, details = validate_city_match(raw, city, state)
        attempt['match_rate'] = f'{rate:.0%}'
        attempt['validation'] = details

        if not is_valid:
            attempt['result'] = 'city_mismatch'
            attempts.append(attempt)
            continue

        # SUCCESS — insert
        attempt['result'] = 'SUCCESS'
        attempts.append(attempt)

        try:
            # city_sources
            conn.execute("""
                INSERT OR REPLACE INTO city_sources
                (source_key, name, state, platform, endpoint, dataset_id,
                 date_field, field_map, mode, status, limit_per_page,
                 created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'city', 'active', 2000,
                        datetime('now'), datetime('now'))
            """, (city_slug, city, state, source_config['platform'],
                  source_config['endpoint'], source_config.get('dataset_id', ''),
                  source_config.get('date_field', ''),
                  json.dumps(source_config.get('field_map', {}))))

            # Permits
            inserted = 0
            for p in permits:
                try:
                    pn = p.get('permit_number', '')
                    if not pn:
                        continue
                    conn.execute("""
                        INSERT OR IGNORE INTO permits
                        (permit_number, city, state, address, description, filing_date, date,
                         source_city_key, collected_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                    """, (pn, city, state, p.get('address', ''), p.get('description', ''),
                          p.get('filing_date', ''), p.get('filing_date', ''), city_slug))
                    inserted += 1
                except Exception:
                    continue

            # Activate
            conn.execute("""
                UPDATE prod_cities SET status = 'active', source_type = ?,
                source_id = ?, source_endpoint = ?, health_status = 'collecting',
                total_permits = COALESCE(total_permits, 0) + ?,
                last_successful_collection = datetime('now')
                WHERE city_slug = ?
            """, (source_config['platform'], city_slug, source_config['endpoint'],
                  inserted, city_slug))
            conn.commit()

            result['outcome'] = 'success'
            result['permits_inserted'] = inserted
            result['source'] = {'platform': source_config['platform'],
                                'endpoint': source_config['endpoint'][:100],
                                'match_rate': f'{rate:.0%}'}
        except Exception as e:
            result['outcome'] = 'insert_error'
            result['error'] = str(e)

        result['attempts'] = attempts
        return result

    # Nothing worked
    result['attempts'] = attempts
    result['outcome'] = 'no_source_found'
    try:
        conn.execute("UPDATE prod_cities SET health_status = 'no_source_found' WHERE city_slug = ?", (city_slug,))
        conn.commit()
    except Exception:
        pass
    return result
