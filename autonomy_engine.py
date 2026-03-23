"""
PermitGrab V12.54c — Autonomy Engine
The daemon thread. Processes counties first, then cities.
Uses single-pass pipeline: search -> validate -> pull 6 months -> done.

CRITICAL: Each city/county is fully processed in ONE function call.
          No "pending" state. No separate validation cron. No onboarding queue.

V12.54c FIXES:
  - flush=True on all [Autonomy] prints for Render visibility
  - traceback.print_exc() on daemon errors
  - See seed fix spec for us_counties data rebuild
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


def is_domain_relevant(domain, county_name, state_abbrev):
    """Check if a Socrata domain plausibly belongs to the target county/state.

    Returns True if domain looks like it could be from the right jurisdiction.
    This prevents onboarding Edmonton, CA datasets for King County, WA etc.
    """
    if not domain:
        return False

    domain_lower = domain.lower()

    # Reject foreign TLDs (e.g. data.edmonton.ca is Canadian)
    for tld in FOREIGN_TLDS:
        if domain_lower.endswith(tld):
            return False

    # Accept: domain contains county name (e.g. data.kingcounty.gov)
    county_slug = county_name.lower().replace(' ', '').replace('-', '')
    if county_slug in domain_lower.replace('.', '').replace('-', ''):
        return True

    # Accept: domain contains state name or abbreviation
    state_name = US_STATES.get(state_abbrev.upper(), '')
    state_lower = state_abbrev.lower()
    if state_name and state_name in domain_lower.replace('.', '').replace('-', ''):
        return True
    # Match state abbrev in domain parts (e.g. data.texas.gov, datahub.austintexas.gov)
    # But be careful: "in" matches too many things, "or" matches oregon but also other words
    if len(state_lower) > 2 or state_lower in ('tx', 'ca', 'ny', 'fl', 'il', 'pa', 'oh', 'wa', 'ma', 'nj', 'md', 'va', 'nc', 'az', 'co'):
        if f".{state_lower}." in f".{domain_lower}" or domain_lower.endswith(f".{state_lower}"):
            return True

    # Accept: any .gov or .us domain (US government portals are generally relevant)
    # These are still US-based even if not perfectly matching the county
    if domain_lower.endswith('.gov') or domain_lower.endswith('.us'):
        return True

    # Accept: .org domains (many transparency portals use .org)
    if domain_lower.endswith('.org'):
        return True

    # Reject everything else (foreign domains, commercial domains, etc.)
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

    # Search Socrata catalog with county name + state
    for keyword in ['building permits', 'construction permits']:
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
        for keyword in ['building permits', 'permits']:
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
        update_county_status(fips, 'no_data', 'no_bulk_dataset_with_city_field')
        return {'found': False}

    # 2. Score and pick best
    for c in bulk_candidates:
        sample = fetch_sample(c['endpoint'], c['platform'], limit=10)
        c['sample'] = sample
        has_recent = check_data_recency(sample, generate_field_map(c['columns']).get('date'))
        c['score'] = score_dataset(c['columns'], c.get('name', ''), has_recent)
    bulk_candidates.sort(key=lambda x: x['score'], reverse=True)
    best = bulk_candidates[0]

    if best['score'] < 60:
        update_county_status(fips, 'no_data', f"best_score_{best['score']}")
        return {'found': False, 'best_score': best['score']}

    # 3. Validate: generate field map, test on sample
    field_map = generate_field_map(best['columns'])
    sample = best.get('sample') or fetch_sample(best['endpoint'], best['platform'], limit=10)

    if not validate_sample(sample, field_map):
        field_map = auto_fix_field_map(sample)
        if not field_map or not validate_sample(sample, field_map):
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
    for raw in permits_raw:
        try:
            permit = {
                'permit_number': raw.get(field_map.get('permit_number', ''), ''),
                'city': raw.get(best.get('city_field', ''), name),
                'state': state,
                'address': raw.get(field_map.get('address', ''), ''),
                'zip': raw.get(field_map.get('zip', ''), ''),
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
                permit.get('description', ''), permit.get('permit_type', ''))
            permit['value_tier'] = score_value(permit.get('estimated_cost', 0))
            # Must have permit_number or address to be useful
            if permit.get('permit_number') or permit.get('address'):
                # Generate permit_number if missing
                if not permit['permit_number']:
                    permit['permit_number'] = f"AUTO-{hash(str(raw)) % 10000000}"
                normalized.append(permit)
        except Exception:
            continue

    # Upsert to SQLite — DATA IS NOW LIVE ON THE SITE
    if normalized:
        new_count, updated_count = permitdb.upsert_permits(normalized, source_city_key=source_key)
        print(f"[Autonomy] {name}, {state}: {len(normalized)} permits loaded ({new_count} new, {updated_count} updated)", flush=True)
    else:
        print(f"[Autonomy] {name}, {state}: 0 permits after normalization", flush=True)

    # 6. Mark all cities in this county as covered
    cities_covered = mark_county_cities_covered(fips, source_key)

    update_county_status(fips, 'has_data')
    record_collection(source_key, len(normalized))

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

    # Socrata catalog search
    for keyword in ['building permits', 'construction permits']:
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
        has_recent = check_data_recency(sample, fm.get('date'))
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
    for raw in permits_raw:
        try:
            permit = {
                'permit_number': raw.get(field_map.get('permit_number', ''), ''),
                'city': name,
                'state': state,
                'address': raw.get(field_map.get('address', ''), ''),
                'zip': raw.get(field_map.get('zip', ''), ''),
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
                permit.get('description', ''), permit.get('permit_type', ''))
            permit['value_tier'] = score_value(permit.get('estimated_cost', 0))
            if permit.get('permit_number') or permit.get('address'):
                if not permit['permit_number']:
                    permit['permit_number'] = f"AUTO-{hash(str(raw)) % 10000000}"
                normalized.append(permit)
        except Exception:
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


def run_autonomy_engine():
    """Main entry point. Runs as daemon thread in server.py."""
    # Wait for startup + initial collection
    print(f"[{datetime.now()}] V12.54: Autonomy engine waiting 10 minutes for startup...", flush=True)
    time.sleep(600)

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
