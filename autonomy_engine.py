"""
PermitGrab V12.56 — Autonomy Engine (Discovery Overhaul)
The daemon thread. Processes counties first, then cities.
Uses single-pass pipeline: search -> validate -> pull 6 months -> done.

CRITICAL: Each city/county is fully processed in ONE function call.
          No "pending" state. No separate validation cron. No onboarding queue.

V12.56 OVERHAUL:
  - STATE_PORTALS: Search state-level Socrata domains first
  - Expanded search keywords (building inspection, code enforcement, etc.)
  - Relaxed domain filter: accept ALL .gov/.us domains
  - Loosened recency gate: 365 days for discovery (was 90)
  - Enhanced scoring bonus for large datasets (10k+ records)
"""

import time
import json
import math
import re
import sys
import traceback
from datetime import datetime, timedelta
import db as permitdb
from city_source_db import (
    get_next_unsearched_county, get_next_unsearched_city,
    update_county_status, update_city_status, upsert_city_source,
    mark_county_cities_covered, increment_search_attempts,
    record_collection, count_unsearched_counties, count_unsearched_cities,
    log_discovery_run, update_source_status,
)
from auto_discover import (
    search_socrata_catalog, search_socrata_domain, search_arcgis,
    search_arcgis_hub, try_common_domains, generate_field_map,
    find_city_field, score_dataset, fetch_sample, validate_sample,
    check_data_recency, auto_fix_field_map, get_socrata_columns,
    get_arcgis_columns, SEARCH_KEYWORDS,
)
# V12.54b: Import from collector for trade classification and fetch functions
from collector import classify_trade, score_value, fetch_socrata, fetch_arcgis


def parse_address_value(val):
    """V12.55c/V12.57: Parse Socrata location fields and GeoJSON points.
    Handles:
      - Socrata location: {'latitude': '39.23', 'longitude': '-77.27', 'human_address': '{"address": "123 MAIN ST", ...}'}
      - GeoJSON Point: {'type': 'Point', 'coordinates': [-117.55, 33.83]}
      - String representations of either format
    Returns the street address, or empty string if only coordinates.
    """
    if not val:
        return ''
    if isinstance(val, dict):
        # V12.57: Handle GeoJSON Point objects
        if val.get('type') == 'Point' and val.get('coordinates'):
            return ''

        human = val.get('human_address', '')
        if human:
            try:
                if isinstance(human, str):
                    human = json.loads(human)
                if isinstance(human, dict):
                    parts = []
                    if human.get('address'):
                        parts.append(human['address'].strip())
                    return ' '.join(parts) if parts else str(val)
            except (json.JSONDecodeError, TypeError):
                pass
        if val.get('address'):
            return str(val['address']).strip()
        lat = val.get('latitude') or val.get('lat')
        lng = val.get('longitude') or val.get('lng') or val.get('lon')
        if lat and lng:
            return ''  # V12.57: Don't show coords as address
        return ''
    s = str(val).strip()
    # V12.57: Handle stringified GeoJSON Point
    if s.startswith('{') and "'type': 'Point'" in s:
        return ''
    if s.startswith('{') and ('human_address' in s or 'latitude' in s):
        try:
            import ast
            parsed = ast.literal_eval(s)
            return parse_address_value(parsed)
        except (ValueError, SyntaxError):
            try:
                parsed = json.loads(s.replace("'", '"'))
                return parse_address_value(parsed)
            except (json.JSONDecodeError, ValueError):
                pass
    return s


def slugify(text):
    """Convert text to URL-safe slug."""
    text = text.lower().strip()
    text = re.sub(r'[^a-z0-9\s-]', '', text)
    text = re.sub(r'[\s]+', '-', text)
    text = re.sub(r'-+', '-', text)
    return text.strip('-')


def create_source_key(name, state, mode='city'):
    """Generate a unique source_key from name + state."""
    slug = slugify(name)
    return f"{slug}-{state.lower()}" if mode == 'city' else f"{slug}-{state.lower()}-bulk"


def throttle(peak_hours=True):
    """Rate limit between searches.
    5 seconds during peak (7 AM - 11 PM ET), 1 second off-peak."""
    try:
        import pytz
        et = pytz.timezone('America/New_York')
        hour = datetime.now(et).hour
    except ImportError:
        hour = datetime.now().hour
    if 7 <= hour <= 23:
        time.sleep(5)
    else:
        time.sleep(1)


# US state abbreviation -> full name mapping for domain matching
US_STATES = {
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
    'WI': 'wisconsin', 'WY': 'wyoming', 'DC': 'districtofcolumbia',
}

# Country TLDs that are NOT US (to reject foreign government portals)
FOREIGN_TLDS = {'.ca', '.uk', '.au', '.nz', '.za', '.in', '.eu', '.de', '.fr', '.jp', '.cn', '.br', '.mx', '.it', '.es', '.nl', '.se', '.no', '.dk', '.fi', '.ie', '.at', '.ch', '.be', '.pt', '.pl', '.cz', '.hu', '.ro', '.bg', '.hr', '.sk', '.si', '.lt', '.lv', '.ee'}

# V12.56: Known state-level Socrata portals — search these first for better hit rate
STATE_PORTALS = {
    'TX': ['data.texas.gov'],
    'CA': ['data.ca.gov', 'data.lacity.org'],
    'NY': ['data.ny.gov', 'data.cityofnewyork.us'],
    'FL': ['data.florida.gov'],
    'WA': ['data.wa.gov'],
    'CO': ['data.colorado.gov'],
    'IL': ['data.illinois.gov', 'data.cityofchicago.org'],
    'MD': ['data.maryland.gov', 'data.montgomerycountymd.gov'],
    'VA': ['data.virginia.gov'],
    'PA': ['data.pa.gov'],
    'OH': ['data.ohio.gov'],
    'GA': ['data.georgia.gov'],
    'NC': ['data.nc.gov'],
    'MI': ['data.michigan.gov'],
    'NJ': ['data.nj.gov'],
    'MA': ['data.mass.gov'],
    'AZ': ['data.az.gov'],
    'MN': ['data.mn.gov'],
    'MO': ['data.mo.gov'],
    'WI': ['data.wi.gov'],
    'OR': ['data.oregon.gov'],
    'SC': ['data.sc.gov'],
    'KY': ['data.ky.gov'],
    'LA': ['data.la.gov'],
    'OK': ['data.ok.gov'],
    'CT': ['data.ct.gov'],
    'UT': ['opendata.utah.gov'],
    'NV': ['data.nv.gov'],
    'NM': ['data.nm.gov'],
    'KS': ['data.ks.gov'],
    'NE': ['data.ne.gov'],
    'IA': ['data.iowa.gov'],
}


def is_domain_relevant(domain, county_name, state_abbrev):
    """V12.60: Check if a Socrata domain plausibly belongs to the target county/state.

    STRICTER than V12.56: Instead of accepting ALL .gov/.us domains, we now
    reject domains that clearly belong to a DIFFERENT city/state.
    e.g., data.cityofnewyork.us is rejected when searching for Kalamazoo MI.
    """
    if not domain:
        return False

    # V12.62: Reject domains containing other US state abbreviations
    # e.g., "data.montgomerycountymd.gov" contains "md" for Maryland
    US_STATE_ABBREVS = {
        'al','ak','az','ar','ca','co','ct','de','fl','ga','hi','id','il','in',
        'ia','ks','ky','la','me','md','ma','mi','mn','ms','mo','mt','ne','nv',
        'nh','nj','nm','ny','nc','nd','oh','ok','or','pa','ri','sc','sd','tn',
        'tx','ut','vt','va','wa','wv','wi','wy','dc'
    }
    if state_abbrev:
        domain_check = domain.lower()
        target_abbrev = state_abbrev.lower()
        # Check if domain contains a different state abbreviation at a boundary
        import re
        for abbrev in US_STATE_ABBREVS:
            if abbrev != target_abbrev:
                # Check for state abbrev at domain boundary (after dot, before dot, or at end)
                if re.search(rf'(?:^|[.\-/]){abbrev}(?:[.\-/]|gov|$)', domain_check):
                    return False

    domain_lower = domain.lower()

    # Reject foreign TLDs (e.g. data.edmonton.ca is Canadian)
    for tld in FOREIGN_TLDS:
        if domain_lower.endswith(tld):
            return False

    # Accept ArcGIS Hub domains (geography checked later via city_field)
    if 'arcgis' in domain_lower or 'hub.arcgis.com' in domain_lower:
        return True

    # Accept: domain contains county/city name (e.g. data.kingcounty.gov)
    county_slug = county_name.lower().replace(' ', '').replace('-', '')
    domain_slug = domain_lower.replace('.', '').replace('-', '')
    if county_slug in domain_slug:
        return True

    # Accept: domain contains target state name or abbreviation
    state_name = US_STATES.get(state_abbrev.upper(), '')
    state_lower = state_abbrev.lower()
    if state_name and state_name in domain_slug:
        return True
    # Check 2-letter abbreviation in domain parts (e.g., data.wa.gov)
    domain_parts = domain_lower.split('.')
    if state_lower in domain_parts:
        return True

    # V12.60: REJECT domains that contain a DIFFERENT city or state name.
    # This prevents data.cityofnewyork.us from being used for Kalamazoo MI.
    _OTHER_CITY_MARKERS = [
        'newyork', 'losangeles', 'lacity', 'chicago', 'houston', 'phoenix',
        'philadelphia', 'sanantonio', 'sandiego', 'dallas', 'austin',
        'jacksonville', 'sanfrancisco', 'sfgov', 'columbus', 'charlotte',
        'indianapolis', 'seattle', 'denver', 'boston', 'nashville',
        'detroit', 'portland', 'memphis', 'louisville', 'baltimore',
        'milwaukee', 'albuquerque', 'tucson', 'fresno', 'sacramento',
        'mesa', 'kansascity', 'atlanta', 'omaha', 'miami', 'tulsa',
        'minneapolis', 'stpaul', 'pittsburgh', 'stlouis', 'cincinnati',
        'tampa', 'oakland', 'raleigh', 'arlington', 'anaheim',
        'honolulu', 'santaana', 'riverside', 'stockton', 'irvine',
    ]
    for marker in _OTHER_CITY_MARKERS:
        if marker in domain_slug and marker != county_slug:
            return False

    # Accept generic .gov and .us domains that don't belong to another city
    if domain_lower.endswith('.gov') or domain_lower.endswith('.us'):
        return True

    # Reject everything else
    return False


def _parse_cost(value):
    """Parse a cost value to float. Handles strings like '$1,234.56'."""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.replace('$', '').replace(',', '').strip()
        try:
            return float(cleaned)
        except (ValueError, TypeError):
            return 0.0
    return 0.0


def process_county(county):
    """SINGLE-PASS: search -> validate -> pull 6 months -> mark cities -> done.

    Args: county dict with keys: county_name, state, fips, portal_domain, etc.
    Returns: dict with keys: found, valid, permits, cities_covered, source_key
    """
    fips = county['fips']
    name = county['county_name']
    state = county['state']
    update_county_status(fips, 'searching')

    print(f"[Autonomy] Searching: {name} County, {state}...", flush=True)

    # 1. Search for a bulk permit dataset
    candidates = []

    # V12.56: Search state-level portals first (higher hit rate)
    state_portals = STATE_PORTALS.get(state, [])
    for portal in state_portals:
        for keyword in ['building permits', 'permits', 'construction']:
            results = search_socrata_domain(portal, f"{name} {keyword}")
            for r in results:
                resource = r.get('resource', {})
                resource_id = resource.get('id', '')
                columns = resource.get('columns_field_name', [])
                if resource_id:
                    candidates.append({
                        'name': resource.get('name', ''),
                        'endpoint': f"https://{portal}/resource/{resource_id}.json",
                        'platform': 'socrata',
                        'dataset_id': resource_id,
                        'columns': columns,
                        'domain': portal,
                    })
            time.sleep(0.3)

    # V12.56: Search Socrata catalog with expanded keywords
    for keyword in ['building permits', 'construction permits', 'permits',
                    'building inspection', 'code enforcement', 'development permits']:
        results, _ = search_socrata_catalog(f"{name} {state} {keyword}", limit=20)
        for r in results:
            resource = r.get('resource', {})
            metadata = r.get('metadata', {})
            domain = metadata.get('domain', '')
            resource_id = resource.get('id', '')
            columns = resource.get('columns_field_name', [])
            if resource_id and columns:
                candidates.append({
                    'name': resource.get('name', ''),
                    'endpoint': f"https://{domain}/resource/{resource_id}.json",
                    'platform': 'socrata',
                    'dataset_id': resource_id,
                    'columns': columns,
                    'domain': domain,
                })
        time.sleep(0.5)

    # Search specific portal domain if known
    if county.get('portal_domain'):
        domain = county['portal_domain']
        for keyword in ['building permits', 'permits', 'construction', 'inspections']:
            results = search_socrata_domain(domain, keyword)
            for r in results:
                resource = r.get('resource', {})
                resource_id = resource.get('id', '')
                columns = resource.get('columns_field_name', [])
                if resource_id:
                    candidates.append({
                        'name': resource.get('name', ''),
                        'endpoint': f"https://{domain}/resource/{resource_id}.json",
                        'platform': 'socrata',
                        'dataset_id': resource_id,
                        'columns': columns,
                        'domain': domain,
                    })
            time.sleep(0.5)

    # Search ArcGIS
    arcgis_results = search_arcgis(name, state)
    for r in arcgis_results:
        columns = get_arcgis_columns(r['endpoint'])
        r['columns'] = columns
        candidates.append(r)
        time.sleep(0.3)

    # V12.54d: Filter out foreign/irrelevant domains BEFORE expensive validation
    relevant_candidates = []
    for c in candidates:
        if c.get('platform') == 'socrata' and not is_domain_relevant(c.get('domain', ''), name, state):
            continue  # Skip foreign/irrelevant Socrata domains
        relevant_candidates.append(c)

    if len(relevant_candidates) < len(candidates):
        print(f"[Autonomy] {name}, {state}: filtered {len(candidates) - len(relevant_candidates)} irrelevant domains, {len(relevant_candidates)} remain", flush=True)

    # Filter: must have a city_field (since this is a county/bulk source)
    bulk_candidates = []
    for c in relevant_candidates:
        if not c.get('columns'):
            # Try to fetch columns
            if c['platform'] == 'socrata':
                c['columns'] = get_socrata_columns(c['endpoint'])
            elif c['platform'] == 'arcgis':
                c['columns'] = get_arcgis_columns(c['endpoint'])
            time.sleep(0.3)

        city_field = find_city_field(c.get('columns', []))
        if city_field:
            c['city_field'] = city_field
            bulk_candidates.append(c)

    if not bulk_candidates:
        print(f"[Autonomy] {name}, {state}: NO bulk candidates (had {len(relevant_candidates)} relevant, none with city_field)", flush=True)
        update_county_status(fips, 'no_data', 'no_bulk_dataset_with_city_field')
        return {'found': False}

    # 2. Score and pick best — REQUIRE recent data
    # V12.60 FIX: Skip datasets we've already tried (dedup by dataset_id)
    import db as _permitdb
    _dedup_conn = _permitdb.get_connection()
    _existing_dataset_ids = set()
    for _row in _dedup_conn.execute(
        "SELECT DISTINCT dataset_id FROM city_sources WHERE dataset_id IS NOT NULL"
    ).fetchall():
        _existing_dataset_ids.add(_row[0])

    _before_dedup = len(bulk_candidates)
    bulk_candidates = [c for c in bulk_candidates
                       if not c.get('dataset_id') or c['dataset_id'] not in _existing_dataset_ids]
    if len(bulk_candidates) < _before_dedup:
        print(f"[Autonomy] {name}, {state}: deduped {_before_dedup - len(bulk_candidates)} "
              f"already-known datasets, {len(bulk_candidates)} remain", flush=True)

    if not bulk_candidates:
        print(f"[Autonomy] {name}, {state}: ALL candidates already known — skipping", flush=True)
        update_county_status(fips, 'no_data', 'all_datasets_already_known')
        return {'found': False}

    for c in bulk_candidates:
        sample = fetch_sample(c['endpoint'], c['platform'], limit=10)
        c['sample'] = sample
        date_key = generate_field_map(c['columns']).get('date')
        has_recent = check_data_recency(sample, date_key, days=365)  # V12.56: 365 days for discovery
        c['has_recent'] = has_recent
        c['score'] = score_dataset(c['columns'], c.get('name', ''), has_recent)

        # V12.55b/V12.57: Verify dataset actually contains data for THIS county
        # by checking if the county name appears in city_field values
        if sample and c.get('city_field'):
            county_lower = name.lower().replace(' county', '').strip()
            domain_lower = c.get('domain', '').lower()
            city_vals = set()
            for rec in sample:
                val = str(rec.get(c['city_field'], '')).lower().strip()
                if val:
                    city_vals.add(val)

            # V12.57: Skip penalty if domain clearly belongs to this county
            # e.g., datacatalog.cookcountyil.gov contains "cook" for Cook County
            domain_matches_county = county_lower in domain_lower.replace('.', '').replace('-', '')

            # V12.57: Check if domain is a state portal for the correct state
            state_portals = STATE_PORTALS.get(state, [])
            is_state_portal = any(portal in domain_lower for portal in state_portals)

            if city_vals and not any(county_lower in v or v in county_lower for v in city_vals):
                if domain_matches_county:
                    # Domain contains county name — trust it, no penalty
                    print(f"[Autonomy] {name}, {state}: domain {domain_lower} matches county, skipping city_field penalty", flush=True)
                elif is_state_portal:
                    # State portal — light penalty (might serve multiple counties)
                    c['score'] = max(0, c['score'] - 10)
                    print(f"[Autonomy] {name}, {state}: state portal {domain_lower}, light penalty (-10) "
                          f"— city_field values {list(city_vals)[:5]}", flush=True)
                else:
                    # Unknown domain with mismatched city values — full penalty
                    c['score'] = max(0, c['score'] - 40)
                    print(f"[Autonomy] {name}, {state}: penalized {c.get('domain','')}/{c.get('dataset_id','')[:8]} "
                          f"— city_field values {list(city_vals)[:5]} don't match county", flush=True)
        time.sleep(0.3)

    bulk_candidates.sort(key=lambda x: x['score'], reverse=True)
    best = bulk_candidates[0]

    if best['score'] < 60:
        print(f"[Autonomy] {name}, {state}: best score {best['score']} < 60 ({best.get('name', '')[:50]})", flush=True)
        update_county_status(fips, 'no_data', f"best_score_{best['score']}")
        return {'found': False, 'best_score': best['score']}

    # V12.55b: Hard gate — must have data from last 90 days to be worth onboarding
    if not best.get('has_recent'):
        print(f"[Autonomy] {name}, {state}: best dataset has NO recent data ({best.get('name', '')[:50]})", flush=True)
        update_county_status(fips, 'no_data', 'no_recent_data')
        return {'found': True, 'valid': False}

    # 3. Validate: generate field map, test on sample
    field_map = generate_field_map(best['columns'])
    sample = best.get('sample') or fetch_sample(best['endpoint'], best['platform'], limit=10)

    if not validate_sample(sample, field_map):
        field_map = auto_fix_field_map(sample)
        if not field_map or not validate_sample(sample, field_map):
            print(f"[Autonomy] {name}, {state}: validation FAILED — score={best['score']}, "
                  f"address={field_map.get('address') if field_map else 'None'}, "
                  f"date={field_map.get('date') if field_map else 'None'}, "
                  f"domain={best.get('domain','')}, name={best.get('name','')[:50]}", flush=True)
            update_county_status(fips, 'no_data', 'validation_failed')
            return {'found': True, 'valid': False}

    # Find the date_field from the field_map
    date_field = field_map.get('date') or field_map.get('filing_date')

    # 4. Create city_sources row
    source_key = create_source_key(name, state, mode='bulk')
    upsert_city_source({
        'source_key': source_key,
        'name': best.get('name', f"{name} County Permits"),
        'state': state,
        'platform': best['platform'],
        'mode': 'bulk',
        'endpoint': best['endpoint'],
        'dataset_id': best.get('dataset_id'),
        'field_map': field_map,
        'date_field': date_field,
        'city_field': best.get('city_field'),
        'status': 'active',
        'discovery_score': best['score'],
    })

    # 5. PULL 6 MONTHS RIGHT NOW (V12.54b: use collector.py fetch functions)
    permits_raw = []
    try:
        config = {
            'endpoint': best['endpoint'],
            'date_field': date_field,
            'limit': 50000 if best['platform'] == 'socrata' else 2000,
            'field_map': field_map,
        }
        if best['platform'] == 'socrata':
            permits_raw = fetch_socrata(config, days_back=180)
        elif best['platform'] == 'arcgis':
            permits_raw = fetch_arcgis(config, days_back=180)
    except Exception as e:
        print(f"[Autonomy] Fetch error for {name}: {e}", flush=True)

    # Normalize permits
    normalized = []
    normalize_errors = type('', (), {})()  # V12.62: simple object for error tracking
    for raw in permits_raw:
        try:
            # V12.55c: Parse Socrata location objects into clean addresses
            raw_address = raw.get(field_map.get('address', ''), '')
            clean_address = parse_address_value(raw_address)
            # Also try to extract zip from location object if not in field_map
            raw_zip = raw.get(field_map.get('zip', ''), '')
            if not raw_zip and isinstance(raw_address, dict):
                human = raw_address.get('human_address', '')
                if isinstance(human, str):
                    try:
                        human = json.loads(human)
                    except (json.JSONDecodeError, TypeError):
                        human = {}
                if isinstance(human, dict):
                    raw_zip = human.get('zip', '')
            permit = {
                'permit_number': raw.get(field_map.get('permit_number', ''), ''),
                'city': raw.get(best.get('city_field', ''), name),
                'state': state,
                'address': clean_address,
                'zip': raw_zip if isinstance(raw_zip, str) else str(raw_zip),
                'permit_type': raw.get(field_map.get('permit_type', ''), ''),
                'description': raw.get(field_map.get('description', ''), ''),
                'estimated_cost': _parse_cost(raw.get(field_map.get('estimated_cost', ''), 0)),
                'status': raw.get(field_map.get('status', ''), ''),
                'filing_date': raw.get(date_field, ''),
                'date': raw.get(date_field, ''),
                'contractor_name': raw.get(field_map.get('contractor_name', ''), ''),
                'owner_name': raw.get(field_map.get('owner_name', ''), ''),
            }
            # V12.54b: Classify trade and value tier (same as collector.py)
            permit['trade_category'] = classify_trade(
                f"{permit.get('description', '')} {permit.get('permit_type', '')}")
            permit['value_tier'] = score_value(permit.get('estimated_cost', 0))
            # Must have permit_number or address to be useful
            if permit.get('permit_number') or permit.get('address'):
                # Generate permit_number if missing
                if not permit['permit_number']:
                    permit['permit_number'] = f"AUTO-{hash(str(raw)) % 10000000}"
                normalized.append(permit)
        except Exception as e:
            if not hasattr(normalize_errors, 'logged'):
                print(f"[Autonomy] Normalization error (first of batch): {e}", flush=True)
                normalize_errors.logged = True
            continue

    # Upsert to SQLite — DATA IS NOW LIVE ON THE SITE
    if normalized:
        new_count, updated_count = permitdb.upsert_permits(normalized, source_city_key=source_key)
        print(f"[Autonomy] {name}, {state}: {len(normalized)} permits loaded ({new_count} new, {updated_count} updated)", flush=True)

        # 6. Mark all cities in this county as covered
        cities_covered = mark_county_cities_covered(fips, source_key)
        update_county_status(fips, 'has_data')
        record_collection(source_key, len(normalized))
    else:
        print(f"[Autonomy] {name}, {state}: 0 permits after normalization — marking no_data", flush=True)
        update_county_status(fips, 'no_data', 'zero_permits_after_fetch')
        # Deactivate the source since it produced nothing
        update_source_status(source_key, 'inactive', 'zero_permits')
        cities_covered = 0

    return {
        'found': True,
        'valid': True,
        'permits': len(normalized),
        'cities_covered': cities_covered,
        'source_key': source_key,
    }


def process_city(city):
    """SINGLE-PASS for individual cities: search -> validate -> pull -> done.

    Args: city dict with keys: city_name, state, slug, etc.
    Returns: dict with keys: found, valid, permits
    """
    slug = city['slug']
    name = city['city_name']
    state = city['state']
    update_city_status(slug, 'searching')

    print(f"[Autonomy] Searching: {name}, {state}...", flush=True)

    # 1. Search
    candidates = []

    # V12.56: Socrata catalog search with expanded keywords
    for keyword in ['building permits', 'construction permits', 'permits',
                    'building inspection', 'development permits']:
        results, _ = search_socrata_catalog(f"{name} {state} {keyword}", limit=10)
        for r in results:
            resource = r.get('resource', {})
            metadata = r.get('metadata', {})
            domain = metadata.get('domain', '')
            resource_id = resource.get('id', '')
            columns = resource.get('columns_field_name', [])
            # V12.54d: Skip foreign/irrelevant domains early
            if resource_id and is_domain_relevant(domain, name, state):
                candidates.append({
                    'name': resource.get('name', ''),
                    'endpoint': f"https://{domain}/resource/{resource_id}.json",
                    'platform': 'socrata',
                    'dataset_id': resource_id,
                    'columns': columns,
                    'domain': domain,
                })
        time.sleep(0.5)

    # Try common domain patterns
    domain_candidates = try_common_domains(name, state)
    candidates.extend(domain_candidates)

    # ArcGIS
    arcgis_results = search_arcgis(name, state)
    for r in arcgis_results:
        columns = get_arcgis_columns(r['endpoint'])
        r['columns'] = columns
        candidates.append(r)
        time.sleep(0.3)

    if not candidates:
        update_city_status(slug, 'no_data_available', 'no_candidates_found')
        increment_search_attempts(slug)
        return {'found': False}

    # 2. Score
    # V12.60 FIX: Skip datasets we've already tried (dedup by dataset_id)
    _dedup_conn = permitdb.get_connection()
    _existing_ids = set(row[0] for row in _dedup_conn.execute(
        "SELECT DISTINCT dataset_id FROM city_sources WHERE dataset_id IS NOT NULL"
    ).fetchall())
    _pre = len(candidates)
    candidates = [c for c in candidates
                  if not c.get('dataset_id') or c['dataset_id'] not in _existing_ids]
    if len(candidates) < _pre:
        print(f"[Autonomy] {name}, {state}: deduped {_pre - len(candidates)} known datasets", flush=True)
    if not candidates:
        update_city_status(slug, 'no_data_available', 'all_datasets_already_known')
        increment_search_attempts(slug)
        return {'found': False}

    for c in candidates:
        if not c.get('columns'):
            if c['platform'] == 'socrata':
                c['columns'] = get_socrata_columns(c['endpoint'])
            elif c['platform'] == 'arcgis':
                c['columns'] = get_arcgis_columns(c['endpoint'])
            time.sleep(0.3)

        sample = fetch_sample(c['endpoint'], c['platform'], limit=10)
        c['sample'] = sample
        fm = generate_field_map(c.get('columns', []))
        has_recent = check_data_recency(sample, fm.get('date'), days=365)  # V12.56: 365 days for discovery
        c['score'] = score_dataset(c.get('columns', []), c.get('name', ''), has_recent)

    candidates.sort(key=lambda x: x.get('score', 0), reverse=True)
    best = candidates[0]

    if best.get('score', 0) < 60:
        update_city_status(slug, 'no_data_available', f"best_score_{best.get('score', 0)}")
        increment_search_attempts(slug)
        return {'found': False, 'best_score': best.get('score', 0)}

    # 3. Validate
    field_map = generate_field_map(best.get('columns', []))
    sample = best.get('sample') or fetch_sample(best['endpoint'], best['platform'], limit=10)

    if not validate_sample(sample, field_map):
        field_map = auto_fix_field_map(sample)
        if not field_map or not validate_sample(sample, field_map):
            update_city_status(slug, 'rejected', 'validation_failed')
            increment_search_attempts(slug)
            return {'found': True, 'valid': False}

    date_field = field_map.get('date') or field_map.get('filing_date')

    # 4. Create source + PULL 6 MONTHS (V12.54b: use collector.py fetch functions)
    source_key = slug
    upsert_city_source({
        'source_key': source_key,
        'name': name,
        'state': state,
        'platform': best['platform'],
        'mode': 'city',
        'endpoint': best['endpoint'],
        'dataset_id': best.get('dataset_id'),
        'field_map': field_map,
        'date_field': date_field,
        'status': 'active',
        'discovery_score': best.get('score', 0),
    })

    permits_raw = []
    try:
        config = {
            'endpoint': best['endpoint'],
            'date_field': date_field,
            'limit': 50000 if best['platform'] == 'socrata' else 2000,
            'field_map': field_map,
        }
        if best['platform'] == 'socrata':
            permits_raw = fetch_socrata(config, days_back=180)
        elif best['platform'] == 'arcgis':
            permits_raw = fetch_arcgis(config, days_back=180)
    except Exception as e:
        print(f"[Autonomy] Fetch error for {name}: {e}", flush=True)

    # Normalize
    normalized = []
    normalize_errors = type('', (), {})()  # V12.62: simple object for error tracking
    for raw in permits_raw:
        try:
            # V12.55c: Parse Socrata location objects into clean addresses
            raw_address = raw.get(field_map.get('address', ''), '')
            clean_address = parse_address_value(raw_address)
            raw_zip = raw.get(field_map.get('zip', ''), '')
            if not raw_zip and isinstance(raw_address, dict):
                human = raw_address.get('human_address', '')
                if isinstance(human, str):
                    try:
                        human = json.loads(human)
                    except (json.JSONDecodeError, TypeError):
                        human = {}
                if isinstance(human, dict):
                    raw_zip = human.get('zip', '')
            permit = {
                'permit_number': raw.get(field_map.get('permit_number', ''), ''),
                'city': name,
                'state': state,
                'address': clean_address,
                'zip': raw_zip if isinstance(raw_zip, str) else str(raw_zip),
                'permit_type': raw.get(field_map.get('permit_type', ''), ''),
                'description': raw.get(field_map.get('description', ''), ''),
                'estimated_cost': _parse_cost(raw.get(field_map.get('estimated_cost', ''), 0)),
                'status': raw.get(field_map.get('status', ''), ''),
                'filing_date': raw.get(date_field, ''),
                'date': raw.get(date_field, ''),
                'contractor_name': raw.get(field_map.get('contractor_name', ''), ''),
                'owner_name': raw.get(field_map.get('owner_name', ''), ''),
            }
            # V12.54b: Classify trade and value tier (same as collector.py)
            permit['trade_category'] = classify_trade(
                f"{permit.get('description', '')} {permit.get('permit_type', '')}")
            permit['value_tier'] = score_value(permit.get('estimated_cost', 0))
            if permit.get('permit_number') or permit.get('address'):
                if not permit['permit_number']:
                    permit['permit_number'] = f"AUTO-{hash(str(raw)) % 10000000}"
                normalized.append(permit)
        except Exception as e:
            if not hasattr(normalize_errors, 'logged'):
                print(f"[Autonomy] Normalization error (first of batch): {e}", flush=True)
                normalize_errors.logged = True
            continue

    if normalized:
        new_count, updated_count = permitdb.upsert_permits(normalized, source_city_key=source_key)
        print(f"[Autonomy] {name}, {state}: {len(normalized)} permits loaded", flush=True)

    # Quality gate: must have at least 10 permits
    if len(normalized) < 10:
        update_city_status(slug, 'rejected', f"only_{len(normalized)}_permits")
        update_source_status(source_key, 'rejected', f"only_{len(normalized)}_permits")
        return {'found': True, 'valid': True, 'permits': len(normalized), 'rejected': True}

    update_city_status(slug, 'active')
    record_collection(source_key, len(normalized))

    return {'found': True, 'valid': True, 'permits': len(normalized)}


def bootstrap_existing_sources():
    """V12.60: One-time collection for pre-seeded sources that have never been collected.
    These are city_sources entries (like NYC, Chicago, etc.) that were loaded
    with valid endpoints and high scores but never had permits collected."""
    conn = permitdb.get_connection()
    uncollected = conn.execute("""
        SELECT source_key, name, state, platform, endpoint, date_field,
               field_map, city_field, mode, discovery_score
        FROM city_sources
        WHERE status = 'active'
          AND total_permits_collected = 0
          AND endpoint IS NOT NULL
          AND endpoint != ''
          AND discovery_score >= 60
        ORDER BY discovery_score DESC
        LIMIT 20
    """).fetchall()

    if not uncollected:
        print("[Autonomy] Bootstrap: no uncollected sources to bootstrap", flush=True)
        return

    print(f"[Autonomy] Bootstrap: found {len(uncollected)} uncollected sources, collecting...", flush=True)
    total_loaded = 0

    for row in uncollected:
        source = dict(row)
        source_key = source['source_key']
        name = source['name']
        state = source['state']

        # V12.61: Re-discover field_map from live API instead of trusting stored values
        # The pre-seeded field_maps are often wrong (e.g., "work_permit" mapped as permit_number
        # when it's actually a status field, or date_field referencing a non-existent column)
        try:
            sample = fetch_sample(source['endpoint'], source['platform'], limit=10)
        except Exception as e:
            print(f"[Autonomy] Bootstrap: {name} sample fetch error: {e}", flush=True)
            # Mark dead endpoints as inactive
            if '404' in str(e) or '403' in str(e) or '401' in str(e):
                update_source_status(source_key, 'inactive', f'dead_endpoint_{str(e)[:50]}')
                print(f"[Autonomy] Bootstrap: {name} — dead endpoint, marked inactive", flush=True)
            continue

        if not sample:
            print(f"[Autonomy] Bootstrap: {name} — empty sample, skipping", flush=True)
            continue

        # Re-discover field_map from the actual column names in the sample
        sample_columns = list(sample[0].keys()) if sample else []
        field_map = generate_field_map(sample_columns)
        date_field = field_map.get('date') or field_map.get('filing_date')

        if not date_field:
            print(f"[Autonomy] Bootstrap: {name} — no date field found in columns: "
                  f"{sample_columns[:10]}", flush=True)
            continue

        if not field_map.get('address') and not field_map.get('permit_number'):
            print(f"[Autonomy] Bootstrap: {name} — no address or permit_number in field_map: "
                  f"{field_map}", flush=True)
            continue

        print(f"[Autonomy] Bootstrap: {name} — re-discovered field_map: "
              f"address={field_map.get('address')}, date={date_field}, "
              f"permit_number={field_map.get('permit_number')}", flush=True)

        try:
            config = {
                'endpoint': source['endpoint'],
                'date_field': date_field,
                'limit': 50000 if source['platform'] == 'socrata' else 2000,
                'field_map': field_map,
            }
            if source['platform'] == 'socrata':
                permits_raw = fetch_socrata(config, days_back=180)
            elif source['platform'] == 'arcgis':
                permits_raw = fetch_arcgis(config, days_back=180)
            else:
                continue
        except Exception as e:
            print(f"[Autonomy] Bootstrap: {name} fetch error: {e}", flush=True)
            continue

        # Normalize
        normalized = []
        normalize_errors = type('', (), {})()  # V12.62: simple object for error tracking
        for raw in permits_raw:
            try:
                raw_address = raw.get(field_map.get('address', ''), '')
                clean_address = parse_address_value(raw_address)
                permit = {
                    'permit_number': raw.get(field_map.get('permit_number', ''), ''),
                    'city': raw.get(source.get('city_field', ''), name) if source.get('city_field') else name,
                    'state': state,
                    'address': clean_address,
                    'zip': str(raw.get(field_map.get('zip', ''), '')),
                    'permit_type': raw.get(field_map.get('permit_type', ''), ''),
                    'description': raw.get(field_map.get('description', ''), ''),
                    'estimated_cost': _parse_cost(raw.get(field_map.get('estimated_cost', ''), 0)),
                    'status': raw.get(field_map.get('status', ''), ''),
                    'filing_date': raw.get(date_field, ''),
                    'date': raw.get(date_field, ''),
                    'contractor_name': raw.get(field_map.get('contractor_name', ''), ''),
                    'owner_name': raw.get(field_map.get('owner_name', ''), ''),
                }
                permit['trade_category'] = classify_trade(
                    f"{permit.get('description', '')} {permit.get('permit_type', '')}")
                permit['value_tier'] = score_value(permit.get('estimated_cost', 0))
                if permit.get('permit_number') or permit.get('address'):
                    if not permit['permit_number']:
                        permit['permit_number'] = f"AUTO-{hash(str(raw)) % 10000000}"
                    normalized.append(permit)
            except Exception as e:
                if not hasattr(normalize_errors, 'logged'):
                    print(f"[Autonomy] Normalization error (first of batch): {e}", flush=True)
                    normalize_errors.logged = True
                continue

        if normalized:
            new_count, updated_count = permitdb.upsert_permits(normalized, source_city_key=source_key)
            record_collection(source_key, len(normalized))
            total_loaded += len(normalized)
            print(f"[Autonomy] Bootstrap: {name}, {state}: {len(normalized)} permits "
                  f"({new_count} new, {updated_count} updated)", flush=True)
            # V12.61: Update stored field_map and date_field since re-discovery worked
            _conn = permitdb.get_connection()
            _conn.execute(
                "UPDATE city_sources SET field_map=?, date_field=? WHERE source_key=?",
                (json.dumps(field_map), date_field, source_key)
            )
            _conn.commit()
        else:
            print(f"[Autonomy] Bootstrap: {name}, {state}: 0 permits after normalization "
                  f"(field_map={field_map})", flush=True)
            # Don't deactivate — log field_map for debugging

        time.sleep(2)  # Be gentle on APIs

    print(f"[Autonomy] Bootstrap complete: {total_loaded} total permits loaded", flush=True)


def run_autonomy_engine():
    """Main entry point. Runs as daemon thread in server.py."""
    # Wait for startup + initial collection
    print(f"[{datetime.now()}] V12.60: Autonomy engine waiting 10 minutes for startup...", flush=True)
    time.sleep(600)

    # V12.60: One-time bootstrap for pre-seeded sources
    try:
        bootstrap_existing_sources()
    except Exception as e:
        print(f"[Autonomy] Bootstrap error: {e}", flush=True)
        traceback.print_exc()

    while True:
        try:
            unsearched_counties = count_unsearched_counties()
            unsearched_cities = count_unsearched_cities()

            if unsearched_counties > 0 or unsearched_cities > 0:
                run_search_cycle()
            else:
                run_maintenance_cycle()
        except Exception as e:
            print(f"[Autonomy] Engine error: {e}", flush=True)
            traceback.print_exc()
            sys.stdout.flush()
            time.sleep(60)


def run_search_cycle():
    """Process counties first, then cities. Single-pass each.
    V12.54b: Caps at 50 successful onboards per cycle to protect server resources."""
    MAX_ONBOARDS_PER_CYCLE = 50
    onboard_count = 0

    stats = {
        'targets_searched': 0, 'sources_found': 0,
        'permits_loaded': 0, 'cities_activated': 0, 'errors': []
    }

    # Phase A: Counties
    county = get_next_unsearched_county()
    while county and onboard_count < MAX_ONBOARDS_PER_CYCLE:
        try:
            result = process_county(county)
            stats['targets_searched'] += 1
            if result.get('found') and result.get('valid'):
                stats['sources_found'] += 1
                stats['permits_loaded'] += result.get('permits', 0)
                stats['cities_activated'] += result.get('cities_covered', 0)
                if result.get('permits', 0) > 0:
                    onboard_count += 1
        except Exception as e:
            stats['errors'].append(f"county:{county.get('county_name','?')}: {str(e)[:100]}")
            print(f"[Autonomy] Error processing {county.get('county_name')}: {e}", flush=True)

        throttle()
        county = get_next_unsearched_county()

    # Phase B: Individual cities (only if county phase didn't exhaust the cap)
    if onboard_count < MAX_ONBOARDS_PER_CYCLE:
        city = get_next_unsearched_city()
        while city and onboard_count < MAX_ONBOARDS_PER_CYCLE:
            try:
                result = process_city(city)
                stats['targets_searched'] += 1
                if result.get('found') and result.get('valid') and not result.get('rejected'):
                    stats['sources_found'] += 1
                    stats['permits_loaded'] += result.get('permits', 0)
                    stats['cities_activated'] += 1
                    if result.get('permits', 0) > 0:
                        onboard_count += 1
            except Exception as e:
                stats['errors'].append(f"city:{city.get('city_name','?')}: {str(e)[:100]}")
                print(f"[Autonomy] Error processing {city.get('city_name')}: {e}", flush=True)

            throttle()
            city = get_next_unsearched_city()

    log_discovery_run('search', stats)
    print(f"[Autonomy] Search cycle complete: {stats['sources_found']} sources, "
          f"{stats['permits_loaded']} permits, {stats['cities_activated']} cities, "
          f"{onboard_count} onboards (cap: {MAX_ONBOARDS_PER_CYCLE})", flush=True)

    # Sleep until next cycle. If we hit the cap, come back in 6 hours.
    # If we didn't hit the cap (running low on targets), come back in 12 hours.
    if onboard_count >= MAX_ONBOARDS_PER_CYCLE:
        print(f"[Autonomy] Hit daily cap ({MAX_ONBOARDS_PER_CYCLE}). Sleeping 6 hours.", flush=True)
        time.sleep(21600)  # 6 hours
    else:
        print(f"[Autonomy] Targets exhausted for now. Sleeping 12 hours.", flush=True)
        time.sleep(43200)  # 12 hours


def run_maintenance_cycle():
    """Runs after all cities/counties have been searched at least once.
    Re-searches old no_data cities, triggers self-healing."""
    print(f"[Autonomy] Maintenance mode. Sleeping 6 hours before next check.", flush=True)

    # Re-search cities that had no data 90+ days ago
    conn = permitdb.get_connection()
    stale = conn.execute("""
        SELECT * FROM us_cities
        WHERE status='no_data_available'
        AND last_searched_at < datetime('now', '-90 days')
        ORDER BY priority ASC
        LIMIT 50
    """).fetchall()

    if stale:
        print(f"[Autonomy] Re-searching {len(stale)} cities that had no data 90+ days ago", flush=True)
        for row in stale:
            city = dict(row)
            update_city_status(city['slug'], 'not_started')  # Reset to trigger re-search

    # Re-search counties monthly
    stale_counties = conn.execute("""
        SELECT * FROM us_counties
        WHERE status='no_data'
        AND last_searched_at < datetime('now', '-30 days')
        ORDER BY priority ASC
        LIMIT 20
    """).fetchall()

    if stale_counties:
        print(f"[Autonomy] Re-searching {len(stale_counties)} counties monthly", flush=True)
        for row in stale_counties:
            county = dict(row)
            update_county_status(county['fips'], 'not_started')

    # Run self-healing
    try:
        from auto_heal import run_self_healing
        run_self_healing()
    except Exception as e:
        print(f"[Autonomy] Self-healing error: {e}", flush=True)

    time.sleep(21600)  # 6 hours
