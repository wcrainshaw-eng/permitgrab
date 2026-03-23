"""
PermitGrab V12.54b — Auto Discover
Search Socrata catalog, ArcGIS hubs, and CKAN for permit datasets.
Score datasets. Generate field maps. Pure search + scoring functions.

V12.54b: Added per-domain rate limiting and exponential backoff on 429.
"""

import requests
import json
import time
import os
import re
import threading
from datetime import datetime, timedelta
from urllib.parse import urlparse

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "PermitGrab/1.0 (permit lead aggregator; contact@permitgrab.com)",
    "Accept": "application/json",
})
SOCRATA_APP_TOKEN = os.environ.get("SOCRATA_APP_TOKEN", "")
if SOCRATA_APP_TOKEN:
    SESSION.headers["X-App-Token"] = SOCRATA_APP_TOKEN

# V12.54b: Per-domain rate limiting
_domain_last_request = {}
_global_lock = threading.Lock()
MIN_DOMAIN_INTERVAL = 1.0  # seconds between requests to same domain


def rate_limit_domain(url):
    """Ensure at least MIN_DOMAIN_INTERVAL seconds between requests
    to the same domain. Thread-safe."""
    domain = urlparse(url).netloc
    with _global_lock:
        last = _domain_last_request.get(domain, 0)
        now = time.time()
        wait = MIN_DOMAIN_INTERVAL - (now - last)
        if wait > 0:
            time.sleep(wait)
        _domain_last_request[domain] = time.time()


def request_with_backoff(url, params=None, timeout=15, max_retries=3):
    """GET with per-domain rate limiting and exponential backoff on 429/5xx."""
    rate_limit_domain(url)
    for attempt in range(max_retries):
        try:
            resp = SESSION.get(url, params=params, timeout=timeout)
            if resp.status_code == 429:
                wait = min(60, (2 ** attempt) * 5)
                print(f"[Discovery] 429 from {urlparse(url).netloc}, waiting {wait}s")
                time.sleep(wait)
                continue
            if resp.status_code >= 500:
                time.sleep(2 ** attempt)
                continue
            return resp
        except Exception:
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise
    return resp  # Return last response even if it was an error


# Keywords to search for (use ALL of them)
SEARCH_KEYWORDS = [
    "building permits",
    "building permits issued",
    "construction permits",
    "development permits",
    "permit applications",
    "building applications",
    "building inspections",
    "planning permits",
    "zoning permits",
]

# Field matching patterns (case-insensitive contains match)
FIELD_PATTERNS = {
    'address':         ['address', 'site_address', 'street_address', 'location', 'project_address'],
    'date':            ['issue_date', 'issued_date', 'filing_date', 'permit_date', 'application_date', 'created', 'permitdate', 'issueddate'],
    'permit_number':   ['permit_number', 'permit_no', 'permit_id', 'application_number', 'record_id', 'app_no', 'permitno'],
    'estimated_cost':  ['estimated_cost', 'job_value', 'valuation', 'construction_value', 'project_value', 'cost', 'constcost', 'declaredvaluation', 'jobvalue'],
    'permit_type':     ['permit_type', 'work_type', 'permit_class', 'record_type', 'type', 'permittypedesc'],
    'contractor_name': ['contractor', 'contractor_name', 'builder', 'applicant', 'applicant_name', 'contractorname'],
    'owner_name':      ['owner', 'owner_name', 'property_owner', 'ownername'],
    'description':     ['description', 'work_description', 'work_desc', 'projectdescription'],
    'status':          ['status', 'permit_status', 'status_desc', 'permitstatusdesc'],
    'zip':             ['zip', 'zip_code', 'postal_code'],
}

# City field patterns (for bulk/county sources)
CITY_FIELD_PATTERNS = ['city', 'municipality', 'muniname', 'town', 'jurisdiction',
                        'community', 'muni', 'city_name', 'city1', 'site_city']


def search_socrata_catalog(query, limit=100, offset=0):
    """Search ALL Socrata portals via the Discovery API.
    Returns (results_list, total_count).
    This is a FREE API — no key required for basic search."""
    url = "https://api.us.socrata.com/api/catalog/v1"
    params = {'q': query, 'limit': limit, 'offset': offset, 'only': 'datasets'}
    try:
        resp = request_with_backoff(url, params=params, timeout=30)
        if resp.status_code != 200:
            return [], 0
        data = resp.json()
        return data.get('results', []), data.get('resultSetSize', 0)
    except Exception as e:
        print(f"[Discovery] Socrata search error: {e}")
        return [], 0


def search_socrata_domain(domain, query):
    """Search a specific Socrata domain for datasets.
    Returns list of dataset metadata dicts."""
    url = f"https://{domain}/api/catalog/v1"
    params = {'q': query, 'limit': 50}
    try:
        resp = request_with_backoff(url, params=params, timeout=15)
        if resp.status_code != 200:
            return []
        return resp.json().get('results', [])
    except Exception:
        return []


def search_arcgis(name, state):
    """Search ArcGIS Online for permit feature services.
    Returns list of candidate dicts with endpoint, columns, name."""
    url = "https://www.arcgis.com/sharing/rest/search"
    params = {
        'q': f'{name} {state} building permits type:Feature Service',
        'f': 'json',
        'num': 20,
    }
    try:
        resp = request_with_backoff(url, params=params, timeout=15)
        if resp.status_code != 200:
            return []
        results = resp.json().get('results', [])
        candidates = []
        for r in results:
            if 'Feature Service' in r.get('type', ''):
                # Build endpoint URL
                service_url = r.get('url', '')
                if service_url and 'FeatureServer' in service_url:
                    query_url = service_url.rstrip('/') + '/0/query'
                    candidates.append({
                        'name': r.get('title', ''),
                        'endpoint': query_url,
                        'platform': 'arcgis',
                        'dataset_id': r.get('id', ''),
                    })
        return candidates
    except Exception:
        return []


def search_arcgis_hub(hub_domain, query='building permits'):
    """Search a specific ArcGIS Hub for datasets."""
    url = f"https://{hub_domain}/api/v3/search"
    params = {'q': query, 'per_page': 20}
    try:
        resp = request_with_backoff(url, params=params, timeout=15)
        if resp.status_code != 200:
            return []
        items = resp.json().get('data', [])
        candidates = []
        for item in items:
            attrs = item.get('attributes', {})
            item_url = attrs.get('url', '')
            if 'FeatureServer' in item_url:
                layer_url = item_url + '/0' if not item_url.endswith('/0') else item_url
                candidates.append({
                    'name': attrs.get('name', ''),
                    'endpoint': f"{layer_url}/query",
                    'platform': 'arcgis',
                    'dataset_id': item.get('id', ''),
                })
        return candidates
    except Exception:
        return []


def try_common_domains(city_name, state):
    """Try common Socrata portal domain patterns for a city.
    Many cities use data.cityof{name}.gov or similar."""
    city_slug = city_name.lower().replace(' ', '')
    domains = [
        f"data.cityof{city_slug}.us",
        f"data.cityof{city_slug}.gov",
        f"data.{city_slug}.gov",
        f"data.{city_slug}{state.lower()}.gov",
        f"opendata.{city_slug}.gov",
        f"data.{city_slug}.us",
    ]
    candidates = []
    for domain in domains:
        results = search_socrata_domain(domain, 'building permits')
        if results:
            for r in results:
                resource = r.get('resource', {})
                metadata = r.get('metadata', {})
                candidates.append({
                    'name': resource.get('name', ''),
                    'endpoint': f"https://{metadata.get('domain', domain)}/resource/{resource.get('id', '')}.json",
                    'platform': 'socrata',
                    'dataset_id': resource.get('id', ''),
                    'columns': resource.get('columns_field_name', []),
                })
        time.sleep(0.5)  # Rate limit between domain attempts
    return candidates


def get_socrata_columns(endpoint):
    """Fetch column names from a Socrata endpoint by requesting 1 record."""
    try:
        url = f"{endpoint}?$limit=1"
        resp = request_with_backoff(url, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            if data and isinstance(data, list) and len(data) > 0:
                return list(data[0].keys())
    except Exception:
        pass
    return []


def get_arcgis_columns(endpoint):
    """Fetch column names from an ArcGIS query endpoint."""
    try:
        params = {'where': '1=1', 'outFields': '*', 'resultRecordCount': 1, 'f': 'json'}
        resp = request_with_backoff(endpoint, params=params, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            features = data.get('features', [])
            if features:
                return list(features[0].get('attributes', {}).keys())
    except Exception:
        pass
    return []


def match_field(column_name, field_patterns):
    """Check if a column name matches any pattern for a field type.
    Case-insensitive contains match."""
    col_lower = column_name.lower()
    for pattern in field_patterns:
        if pattern in col_lower:
            return True
    return False


def generate_field_map(columns):
    """Auto-generate field_map by matching column names to standard fields.
    Returns dict like {'address': 'site_address', 'date': 'issue_date', ...}"""
    field_map = {}
    columns_lower = {c.lower(): c for c in columns}

    for our_field, patterns in FIELD_PATTERNS.items():
        for pattern in patterns:
            for col_lower, col_actual in columns_lower.items():
                if pattern in col_lower:
                    field_map[our_field] = col_actual
                    break
            if our_field in field_map:
                break

    return field_map


def find_city_field(columns):
    """Find the city/municipality column in a bulk dataset."""
    columns_lower = {c.lower(): c for c in columns}
    for pattern in CITY_FIELD_PATTERNS:
        for col_lower, col_actual in columns_lower.items():
            if pattern in col_lower:
                return col_actual
    return None


def score_dataset(columns, name='', has_recent_data=False, row_count=0):
    """Score a dataset 0-100 based on field availability.
    Threshold: 60+ to proceed with validation and pull.

    Scoring:
      +20  has address field
      +20  has date field
      +15  has permit_number field
      +10  has estimated_cost field
      +10  has permit_type or description field
      +5   has contractor field
      +10  has recent data (within 90 days)
      +5   has 100+ records
      +5   name contains 'permit' or 'building'
      -10  NO recent data and no way to check
      -20  NO address field at all
    """
    score = 0
    columns_lower = [c.lower() for c in columns]

    has_address = any(any(p in c for p in FIELD_PATTERNS['address']) for c in columns_lower)
    has_date = any(any(p in c for p in FIELD_PATTERNS['date']) for c in columns_lower)
    has_permit_num = any(any(p in c for p in FIELD_PATTERNS['permit_number']) for c in columns_lower)
    has_cost = any(any(p in c for p in FIELD_PATTERNS['estimated_cost']) for c in columns_lower)
    has_type = any(any(p in c for p in FIELD_PATTERNS['permit_type'] + FIELD_PATTERNS['description']) for c in columns_lower)
    has_contractor = any(any(p in c for p in FIELD_PATTERNS['contractor_name']) for c in columns_lower)

    if has_address: score += 20
    else: score -= 20
    if has_date: score += 20
    if has_permit_num: score += 15
    if has_cost: score += 10
    if has_type: score += 10
    if has_contractor: score += 5
    if has_recent_data: score += 10
    else: score -= 10
    if row_count >= 100: score += 5
    if 'permit' in name.lower() or 'building' in name.lower(): score += 5

    return max(0, min(100, score))


def fetch_sample(endpoint, platform, limit=10):
    """Fetch a small sample of records from an endpoint.
    Returns list of dicts (raw records) or empty list on failure."""
    try:
        if platform == 'socrata':
            url = f"{endpoint}?$limit={limit}&$order=:id DESC"
            resp = request_with_backoff(url, timeout=15)
            if resp.status_code == 200:
                return resp.json()
        elif platform == 'arcgis':
            params = {
                'where': '1=1', 'outFields': '*',
                'resultRecordCount': limit, 'f': 'json',
                'orderByFields': 'OBJECTID DESC'
            }
            resp = request_with_backoff(endpoint, params=params, timeout=15)
            if resp.status_code == 200:
                features = resp.json().get('features', [])
                return [f.get('attributes', {}) for f in features]
    except Exception:
        pass
    return []


def validate_sample(sample, field_map):
    """Check that field_map extracts real data from sample records.
    Returns True if at least 50% of records have address AND date populated."""
    if not sample or not field_map:
        return False

    address_field = field_map.get('address')
    date_field = field_map.get('date') or field_map.get('filing_date')

    if not address_field or not date_field:
        return False

    has_address = 0
    has_date = 0
    for record in sample:
        if record.get(address_field):
            has_address += 1
        if record.get(date_field):
            has_date += 1

    total = len(sample)
    return (has_address / total >= 0.5) and (has_date / total >= 0.5)


def check_data_recency(sample, date_field):
    """Check if sample has records from last 90 days.
    Returns True if any record's date is within 90 days of now."""
    if not sample or not date_field:
        return False
    cutoff = (datetime.now() - timedelta(days=90)).isoformat()
    for record in sample:
        date_val = record.get(date_field, '')
        if isinstance(date_val, str) and date_val > cutoff:
            return True
    return False


def auto_fix_field_map(sample):
    """Try to auto-detect field map from actual sample data.
    Used when the initial generate_field_map() fails validation."""
    if not sample:
        return None
    columns = list(sample[0].keys())
    return generate_field_map(columns)
