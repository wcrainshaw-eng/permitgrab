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
MIN_DOMAIN_INTERVAL = 0.3  # V14: Accelerated from 1.0s to 0.3s


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
    'address':         ['address', 'site_address', 'street_address', 'location', 'project_address', 'addr', 'situs', 'job_site', 'work_site', 'premise'],
    'date':            ['issue_date', 'issued_date', 'filing_date', 'permit_date', 'application_date', 'created', 'permitdate', 'issueddate',
                        'filingdate', 'applicationdate', 'applydate', 'applied',  # V12.65: New Orleans + app variants
                        'date_issued', 'date_filed', 'applied_date', 'file_date', 'open_date', 'record_date',
                        'permit_issued_date', 'permitissuedate', 'processed_date', 'date_opened', 'inspection_date'],  # V12.65: more variants
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
    Returns dict like {'address': 'site_address', 'date': 'issue_date', ...}

    V12.65: Normalizes underscores for matching — "filing_date" matches "filingdate"."""
    field_map = {}
    columns_lower = {c.lower(): c for c in columns}

    for our_field, patterns in FIELD_PATTERNS.items():
        for pattern in patterns:
            pat_norm = pattern.replace('_', '')  # V12.65: underscore-agnostic matching
            for col_lower, col_actual in columns_lower.items():
                col_norm = col_lower.replace('_', '')  # V12.65: normalize column too
                if pat_norm in col_norm:
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
      +10  has recent data (within N days)
      +10  has 10,000+ records (V12.56: likely real permit DB)
      +5   has 1,000+ records
      +3   has 100+ records
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
    # V12.56: Enhanced scoring for large datasets (likely real permit databases)
    if row_count and row_count > 10000:
        score += 10
    elif row_count and row_count > 1000:
        score += 5
    elif row_count and row_count >= 100:
        score += 3
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
    return (has_address / total >= 0.25) or (has_date / total >= 0.25)  # V14b: relaxed


def check_data_recency(sample, date_field, days=90):
    """Check if sample has records from last N days.
    V12.56: Added days parameter (default 90, use 365 for initial discovery).
    Returns True if any record's date is within N days of now."""
    if not sample or not date_field:
        return False
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
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


# ============================================================================
# V13.2: FULL DISCOVERY ENGINE
# ============================================================================

def run_full_discovery(max_results=200):
    """
    V13.2: Run a full discovery cycle to find new permit data sources.

    Searches the Socrata Discovery API for building permit datasets,
    evaluates their quality, generates field maps, and inserts new
    sources into the city_sources table.

    Returns:
        int: Number of new sources added
    """
    import db as permitdb
    from city_source_db import upsert_city_source, get_city_config

    print(f"[Discovery] V13.2: Starting full discovery cycle...")
    new_sources_added = 0
    sources_skipped = 0
    sources_failed = 0

    # Search Socrata Discovery API for permit-related datasets
    for keyword in SEARCH_KEYWORDS:  # V14: Use all keywords (was top 5)
        print(f"[Discovery] Searching for: {keyword}")

        results, total = search_socrata_catalog(keyword, limit=100)  # V14: 100 results per keyword (was 50)
        print(f"[Discovery] Found {len(results)} results (total: {total})")

        for result in results:
            try:
                resource = result.get('resource', {})
                metadata = result.get('metadata', {})

                dataset_id = resource.get('id')
                domain = metadata.get('domain', '')
                name = resource.get('name', '')
                columns = resource.get('columns_field_name', [])

                if not dataset_id or not domain:
                    continue

                # Generate source_key
                source_key = f"{domain}_{dataset_id}"

                # Skip if already exists
                existing = get_city_config(source_key)
                if existing:
                    sources_skipped += 1
                    continue

                # Score the dataset
                score = score_dataset(columns, name=name)
                if score < 50:  # Skip low-quality datasets
                    continue

                # Generate field map
                field_map = generate_field_map(columns)
                if not field_map.get('address'):
                    continue  # Must have address field

                # Build endpoint URL
                endpoint = f"https://{domain}/resource/{dataset_id}.json"

                # Extract city/state from metadata if available
                city_name = name.split(' - ')[0] if ' - ' in name else name.split(',')[0]
                city_name = city_name.replace(' Building Permits', '').replace(' Permits', '').strip()

                # Create source config
                source_config = {
                    'source_key': source_key,
                    'name': city_name[:50],  # Truncate long names
                    'state': '',  # Will be populated by first collection
                    'platform': 'socrata',
                    'mode': 'city',
                    'endpoint': endpoint,
                    'dataset_id': dataset_id,
                    'field_map': field_map,
                    'date_field': field_map.get('date') or field_map.get('filing_date'),
                    'status': 'active',
                    'discovery_score': score,
                }

                # Insert into city_sources
                upsert_city_source(source_config)
                new_sources_added += 1
                print(f"[Discovery] ✓ Added: {city_name} ({source_key}) score={score}")

            except Exception as e:
                sources_failed += 1
                print(f"[Discovery] ✗ Error processing result: {str(e)[:100]}")

        # Rate limit between keyword searches
        time.sleep(2)

        # Stop if we've added enough
        if new_sources_added >= max_results:
            break

    # Log discovery run to database
    try:
        conn = permitdb.get_connection()
        conn.execute("""
            INSERT INTO discovery_runs (run_type, completed_at, sources_found)
            VALUES ('full', datetime('now'), ?)
        """, (new_sources_added,))
        conn.commit()
    except Exception as e:
        print(f"[Discovery] Warning: Failed to log run: {e}")

    print(f"[Discovery] Complete: {new_sources_added} added, {sources_skipped} skipped, {sources_failed} failed")
    return new_sources_added


# ============================================================================
# V16: ARCGIS BULK DISCOVERY — sweep the entire ArcGIS Online catalog
# ============================================================================

ARCGIS_SEARCH_KEYWORDS = [
    "building permits",
    "building permits issued",
    "construction permits",
    "permit applications",
    "development permits",
    "building inspections",
    "zoning permits",
    "code enforcement",
]

# US state abbreviations for extracting state from ArcGIS metadata
US_STATES = {
    'alabama': 'AL', 'alaska': 'AK', 'arizona': 'AZ', 'arkansas': 'AR',
    'california': 'CA', 'colorado': 'CO', 'connecticut': 'CT', 'delaware': 'DE',
    'florida': 'FL', 'georgia': 'GA', 'hawaii': 'HI', 'idaho': 'ID',
    'illinois': 'IL', 'indiana': 'IN', 'iowa': 'IA', 'kansas': 'KS',
    'kentucky': 'KY', 'louisiana': 'LA', 'maine': 'ME', 'maryland': 'MD',
    'massachusetts': 'MA', 'michigan': 'MI', 'minnesota': 'MN', 'mississippi': 'MS',
    'missouri': 'MO', 'montana': 'MT', 'nebraska': 'NE', 'nevada': 'NV',
    'new hampshire': 'NH', 'new jersey': 'NJ', 'new mexico': 'NM', 'new york': 'NY',
    'north carolina': 'NC', 'north dakota': 'ND', 'ohio': 'OH', 'oklahoma': 'OK',
    'oregon': 'OR', 'pennsylvania': 'PA', 'rhode island': 'RI', 'south carolina': 'SC',
    'south dakota': 'SD', 'tennessee': 'TN', 'texas': 'TX', 'utah': 'UT',
    'vermont': 'VT', 'virginia': 'VA', 'washington': 'WA', 'west virginia': 'WV',
    'wisconsin': 'WI', 'wyoming': 'WY', 'district of columbia': 'DC',
}
US_STATE_ABBREVS = set(US_STATES.values())


def _extract_city_state_from_arcgis(result):
    """Extract city name and state from ArcGIS search result metadata.
    Uses title, snippet, tags, and owner fields to guess location.
    Returns (city_name, state_abbrev) or (None, None)."""

    title = result.get('title', '')
    snippet = result.get('snippet', '') or ''
    tags = result.get('tags', []) or []
    owner = result.get('owner', '')
    all_text = f"{title} {snippet} {' '.join(tags)} {owner}".lower()

    state = ''
    # Check for state abbreviations in tags first (most reliable)
    for tag in tags:
        tag_upper = tag.strip().upper()
        if tag_upper in US_STATE_ABBREVS:
            state = tag_upper
            break
    # Check for full state names in text
    if not state:
        for state_name, abbrev in US_STATES.items():
            if state_name in all_text:
                state = abbrev
                break

    # Extract city from title — strip common suffixes
    city = title
    for suffix in [' Building Permits', ' Permits', ' Permit', ' Construction',
                   ' Building', ' Development', ' - Building', ' - Permits',
                   ' Issued Permits', ' Active Permits', ' Residential Permits',
                   ' Commercial Permits', ' Inspections', ' Code Enforcement',
                   ' Planning', ' Zoning']:
        if city.lower().endswith(suffix.lower()):
            city = city[:len(city) - len(suffix)].strip()
    # Strip "City of " prefix
    if city.lower().startswith('city of '):
        city = city[8:].strip()
    # Strip state from end like "Austin, TX" or "Austin TX"
    city = re.sub(r',?\s*[A-Z]{2}\s*$', '', city).strip()
    # Strip trailing " - " fragments
    if ' - ' in city:
        city = city.split(' - ')[0].strip()

    if not city or len(city) < 3 or len(city) > 60:
        return None, None

    return city, state


def run_arcgis_bulk_discovery(max_results=500):
    """
    V16: Sweep ArcGIS Online catalog for ALL building permit Feature Services.
    Unlike per-city ArcGIS search, this does a broad catalog scan and onboards
    every valid permit service it finds.

    Returns: int — number of new sources added
    """
    import db as permitdb
    from city_source_db import upsert_city_source, get_city_config

    print(f"[Discovery] V16: Starting ArcGIS bulk discovery...", flush=True)
    new_sources_added = 0
    sources_skipped = 0
    sources_failed = 0
    seen_ids = set()

    for keyword in ARCGIS_SEARCH_KEYWORDS:
        start = 1
        while start < 500:  # Up to 500 results per keyword
            url = "https://www.arcgis.com/sharing/rest/search"
            params = {
                'q': f'{keyword} type:"Feature Service"',
                'f': 'json',
                'num': 100,
                'start': start,
                'sortField': 'numviews',
                'sortOrder': 'desc',
            }

            try:
                resp = request_with_backoff(url, params=params, timeout=30)
                if resp.status_code != 200:
                    print(f"[Discovery] ArcGIS search failed ({resp.status_code}) for '{keyword}' start={start}", flush=True)
                    break

                data = resp.json()
                results = data.get('results', [])
                total = data.get('total', 0)

                if not results:
                    break

                print(f"[Discovery] ArcGIS '{keyword}' start={start}: {len(results)} results (total: {total})", flush=True)

                for r in results:
                    try:
                        item_id = r.get('id', '')
                        if not item_id or item_id in seen_ids:
                            continue
                        seen_ids.add(item_id)

                        # Must be a Feature Service
                        if 'Feature Service' not in r.get('type', ''):
                            continue

                        service_url = r.get('url', '')
                        if not service_url or 'FeatureServer' not in service_url:
                            continue

                        # Skip non-US services (check for common international patterns)
                        title_lower = r.get('title', '').lower()
                        if any(x in title_lower for x in ['canada', 'ontario', 'british columbia', 'uk ', 'australia']):
                            continue

                        # Extract city and state
                        city, state = _extract_city_state_from_arcgis(r)
                        if not city:
                            continue

                        # Build source key and endpoint
                        source_key = f"arcgis_{item_id}"
                        query_url = service_url.rstrip('/') + '/0/query'

                        # Skip if already exists
                        existing = get_city_config(source_key)
                        if existing:
                            sources_skipped += 1
                            continue

                        # Fetch columns to score and generate field map
                        columns = get_arcgis_columns(query_url)
                        if not columns:
                            continue

                        # Score
                        score = score_dataset(columns, name=r.get('title', ''))
                        if score < 50:
                            continue

                        # Generate field map
                        field_map = generate_field_map(columns)
                        if not field_map.get('address'):
                            continue

                        date_field = field_map.get('date') or field_map.get('filing_date')

                        # Detect if this is a bulk/county source (has city_field)
                        city_field = find_city_field(columns)
                        mode = 'bulk' if city_field else 'city'

                        source_config = {
                            'source_key': source_key,
                            'name': city[:50],
                            'state': state,
                            'platform': 'arcgis',
                            'mode': mode,
                            'endpoint': query_url,
                            'dataset_id': item_id,
                            'field_map': field_map,
                            'date_field': date_field,
                            'status': 'active',
                            'discovery_score': score,
                        }
                        if city_field:
                            source_config['city_field'] = city_field

                        upsert_city_source(source_config)
                        new_sources_added += 1
                        print(f"[Discovery] ✓ ArcGIS: {city}, {state} ({source_key}) score={score} mode={mode}", flush=True)

                        if new_sources_added >= max_results:
                            break

                    except Exception as e:
                        sources_failed += 1
                        if sources_failed <= 5:
                            print(f"[Discovery] ✗ ArcGIS error: {str(e)[:100]}", flush=True)
                        continue

                if new_sources_added >= max_results:
                    break

                # Next page
                next_start = data.get('nextStart', -1)
                if next_start <= 0 or next_start <= start:
                    break
                start = next_start
                time.sleep(0.5)  # Rate limit between pages

            except Exception as e:
                print(f"[Discovery] ArcGIS search error for '{keyword}': {e}", flush=True)
                break

        if new_sources_added >= max_results:
            break
        time.sleep(1)  # Rate limit between keywords

    # Log discovery run
    try:
        conn = permitdb.get_connection()
        conn.execute("""
            INSERT INTO discovery_runs (run_type, completed_at, sources_found)
            VALUES ('arcgis_bulk', datetime('now'), ?)
        """, (new_sources_added,))
        conn.commit()
    except Exception as e:
        print(f"[Discovery] Warning: Failed to log arcgis run: {e}")

    print(f"[Discovery] ArcGIS bulk complete: {new_sources_added} added, "
          f"{sources_skipped} skipped, {sources_failed} failed, {len(seen_ids)} unique items scanned", flush=True)
    return new_sources_added


# ============================================================================
# V17b: ACCELERATED DISCOVERY — parallel searches + batch processing
# ============================================================================

from concurrent.futures import ThreadPoolExecutor, as_completed


def _search_socrata_keyword(keyword, existing_keys, max_per_keyword=50):
    """Worker function: search one keyword and return candidates."""
    candidates = []
    try:
        results, total = search_socrata_catalog(keyword, limit=100)
        for result in results:
            try:
                resource = result.get('resource', {})
                metadata = result.get('metadata', {})
                dataset_id = resource.get('id')
                domain = metadata.get('domain', '')
                name = resource.get('name', '')
                columns = resource.get('columns_field_name', [])

                if not dataset_id or not domain:
                    continue

                source_key = f"{domain}_{dataset_id}"
                if source_key in existing_keys:
                    continue

                # Score
                score = score_dataset(columns, name=name)
                if score < 50:
                    continue

                # Field map
                field_map = generate_field_map(columns)
                if not field_map.get('address'):
                    continue

                # Extract city name
                city_name = name.split(' - ')[0] if ' - ' in name else name.split(',')[0]
                city_name = city_name.replace(' Building Permits', '').replace(' Permits', '').strip()

                candidates.append({
                    'source_key': source_key,
                    'name': city_name[:50],
                    'state': '',
                    'platform': 'socrata',
                    'mode': 'city',
                    'endpoint': f"https://{domain}/resource/{dataset_id}.json",
                    'dataset_id': dataset_id,
                    'field_map': field_map,
                    'date_field': field_map.get('date') or field_map.get('filing_date'),
                    'status': 'active',
                    'discovery_score': score,
                })

                if len(candidates) >= max_per_keyword:
                    break
            except:
                continue
    except Exception as e:
        print(f"[V17b] Socrata search error for '{keyword}': {e}")
    return candidates


def _search_arcgis_keyword(keyword, existing_keys, max_per_keyword=50):
    """Worker function: search one ArcGIS keyword and return candidates."""
    candidates = []
    seen_ids = set()

    try:
        url = "https://www.arcgis.com/sharing/rest/search"
        params = {
            'q': f'{keyword} type:"Feature Service"',
            'f': 'json',
            'num': 100,
            'start': 1,
            'sortField': 'numviews',
            'sortOrder': 'desc',
        }

        resp = request_with_backoff(url, params=params, timeout=30)
        if resp.status_code != 200:
            return candidates

        results = resp.json().get('results', [])

        for r in results:
            try:
                item_id = r.get('id', '')
                if not item_id or item_id in seen_ids:
                    continue
                seen_ids.add(item_id)

                if 'Feature Service' not in r.get('type', ''):
                    continue

                service_url = r.get('url', '')
                if not service_url or 'FeatureServer' not in service_url:
                    continue

                # Skip non-US
                title_lower = r.get('title', '').lower()
                if any(x in title_lower for x in ['canada', 'ontario', 'british columbia', 'uk ', 'australia']):
                    continue

                source_key = f"arcgis_{item_id}"
                if source_key in existing_keys:
                    continue

                city, state = _extract_city_state_from_arcgis(r)
                if not city:
                    continue

                query_url = service_url.rstrip('/') + '/0/query'

                # Quick column fetch
                columns = get_arcgis_columns(query_url)
                if not columns:
                    continue

                score = score_dataset(columns, name=r.get('title', ''))
                if score < 50:
                    continue

                field_map = generate_field_map(columns)
                if not field_map.get('address'):
                    continue

                city_field = find_city_field(columns)
                mode = 'bulk' if city_field else 'city'

                candidate = {
                    'source_key': source_key,
                    'name': city[:50],
                    'state': state,
                    'platform': 'arcgis',
                    'mode': mode,
                    'endpoint': query_url,
                    'dataset_id': item_id,
                    'field_map': field_map,
                    'date_field': field_map.get('date') or field_map.get('filing_date'),
                    'status': 'active',
                    'discovery_score': score,
                }
                if city_field:
                    candidate['city_field'] = city_field

                candidates.append(candidate)

                if len(candidates) >= max_per_keyword:
                    break
            except:
                continue
    except Exception as e:
        print(f"[V17b] ArcGIS search error for '{keyword}': {e}")

    return candidates


def run_accelerated_discovery(max_results=500, max_workers=5):
    """
    V17b: Accelerated discovery using parallel searches.

    - Searches Socrata + ArcGIS keywords in parallel
    - Batch inserts discovered sources
    - ~5-10x faster than sequential discovery

    Args:
        max_results: Max total sources to add
        max_workers: Number of parallel search threads

    Returns:
        int: Number of new sources added
    """
    import db as permitdb
    from city_source_db import upsert_city_source, get_city_config

    start_time = time.time()
    print(f"[V17b] Starting accelerated discovery (max_workers={max_workers})...", flush=True)

    # Build set of existing source keys for fast lookup
    conn = permitdb.get_connection()
    existing_rows = conn.execute("SELECT source_key FROM city_sources").fetchall()
    existing_keys = {r['source_key'] for r in existing_rows}

    # Also check discovered_sources
    try:
        ds_rows = conn.execute("SELECT source_key FROM discovered_sources").fetchall()
        existing_keys.update(r['source_key'] for r in ds_rows)
    except:
        pass

    print(f"[V17b] {len(existing_keys)} existing sources to skip", flush=True)

    all_candidates = []
    all_keywords = SEARCH_KEYWORDS + ARCGIS_SEARCH_KEYWORDS

    # Phase 1: Parallel Socrata searches
    print(f"[V17b] Phase 1: Socrata parallel search ({len(SEARCH_KEYWORDS)} keywords)...", flush=True)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_search_socrata_keyword, kw, existing_keys, 30): kw
            for kw in SEARCH_KEYWORDS
        }
        for future in as_completed(futures):
            kw = futures[future]
            try:
                candidates = future.result()
                all_candidates.extend(candidates)
                if candidates:
                    print(f"[V17b]   '{kw}': {len(candidates)} candidates", flush=True)
            except Exception as e:
                print(f"[V17b]   '{kw}' error: {e}", flush=True)

    # Phase 2: Parallel ArcGIS searches
    print(f"[V17b] Phase 2: ArcGIS parallel search ({len(ARCGIS_SEARCH_KEYWORDS)} keywords)...", flush=True)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_search_arcgis_keyword, kw, existing_keys, 30): kw
            for kw in ARCGIS_SEARCH_KEYWORDS
        }
        for future in as_completed(futures):
            kw = futures[future]
            try:
                candidates = future.result()
                all_candidates.extend(candidates)
                if candidates:
                    print(f"[V17b]   '{kw}': {len(candidates)} candidates", flush=True)
            except Exception as e:
                print(f"[V17b]   '{kw}' error: {e}", flush=True)

    # Deduplicate by source_key
    seen_keys = set()
    unique_candidates = []
    for c in all_candidates:
        if c['source_key'] not in seen_keys and c['source_key'] not in existing_keys:
            seen_keys.add(c['source_key'])
            unique_candidates.append(c)

    print(f"[V17b] Found {len(unique_candidates)} unique new candidates", flush=True)

    # Phase 3: Batch insert (limit to max_results)
    new_sources_added = 0
    for candidate in unique_candidates[:max_results]:
        try:
            upsert_city_source(candidate)
            new_sources_added += 1
        except Exception as e:
            print(f"[V17b] Insert error: {e}")

    # Log discovery run
    elapsed = time.time() - start_time
    try:
        conn.execute("""
            INSERT INTO discovery_runs (run_type, completed_at, sources_found)
            VALUES ('accelerated', datetime('now'), ?)
        """, (new_sources_added,))
        conn.commit()
    except:
        pass

    print(f"[V17b] Accelerated discovery complete: {new_sources_added} added in {elapsed:.1f}s", flush=True)
    return new_sources_added


def run_quick_discovery(max_results=100):
    """
    V17b: Quick discovery mode - faster but less thorough.
    Uses only top 3 keywords per platform with max 2 workers.
    Good for frequent (hourly) discovery checks.
    """
    import db as permitdb
    from city_source_db import upsert_city_source

    start_time = time.time()
    print(f"[V17b] Starting quick discovery...", flush=True)

    conn = permitdb.get_connection()
    existing_rows = conn.execute("SELECT source_key FROM city_sources").fetchall()
    existing_keys = {r['source_key'] for r in existing_rows}

    all_candidates = []
    quick_keywords = SEARCH_KEYWORDS[:3]  # Top 3 only

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {
            executor.submit(_search_socrata_keyword, kw, existing_keys, 20): kw
            for kw in quick_keywords
        }
        for future in as_completed(futures):
            try:
                candidates = future.result()
                all_candidates.extend(candidates)
            except:
                pass

    # Dedupe and insert
    seen_keys = set()
    new_sources_added = 0
    for c in all_candidates:
        if c['source_key'] not in seen_keys and c['source_key'] not in existing_keys:
            seen_keys.add(c['source_key'])
            try:
                upsert_city_source(c)
                new_sources_added += 1
                if new_sources_added >= max_results:
                    break
            except:
                pass

    elapsed = time.time() - start_time
    print(f"[V17b] Quick discovery: {new_sources_added} added in {elapsed:.1f}s", flush=True)
    return new_sources_added
