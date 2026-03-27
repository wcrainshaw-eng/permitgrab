"""
PermitGrab - V15 Source Discovery Engine
Discovers and tests building permit data sources.

Usage:
    python discovery.py --states        # Check all 50 states
    python discovery.py --counties      # Check top 200 counties
    python discovery.py --cities        # Check cities by population tier
    python discovery.py --test SOURCE   # Test a specific source
"""

import requests
import json
import time
import argparse
from datetime import datetime, timedelta
import db as permitdb

# US States
US_STATES = [
    'AL', 'AK', 'AZ', 'AR', 'CA', 'CO', 'CT', 'DE', 'FL', 'GA',
    'HI', 'ID', 'IL', 'IN', 'IA', 'KS', 'KY', 'LA', 'ME', 'MD',
    'MA', 'MI', 'MN', 'MS', 'MO', 'MT', 'NE', 'NV', 'NH', 'NJ',
    'NM', 'NY', 'NC', 'ND', 'OH', 'OK', 'OR', 'PA', 'RI', 'SC',
    'SD', 'TN', 'TX', 'UT', 'VT', 'VA', 'WA', 'WV', 'WI', 'WY', 'DC'
]

STATE_NAMES = {
    'AL': 'Alabama', 'AK': 'Alaska', 'AZ': 'Arizona', 'AR': 'Arkansas',
    'CA': 'California', 'CO': 'Colorado', 'CT': 'Connecticut', 'DE': 'Delaware',
    'FL': 'Florida', 'GA': 'Georgia', 'HI': 'Hawaii', 'ID': 'Idaho',
    'IL': 'Illinois', 'IN': 'Indiana', 'IA': 'Iowa', 'KS': 'Kansas',
    'KY': 'Kentucky', 'LA': 'Louisiana', 'ME': 'Maine', 'MD': 'Maryland',
    'MA': 'Massachusetts', 'MI': 'Michigan', 'MN': 'Minnesota', 'MS': 'Mississippi',
    'MO': 'Missouri', 'MT': 'Montana', 'NE': 'Nebraska', 'NV': 'Nevada',
    'NH': 'New Hampshire', 'NJ': 'New Jersey', 'NM': 'New Mexico', 'NY': 'New York',
    'NC': 'North Carolina', 'ND': 'North Dakota', 'OH': 'Ohio', 'OK': 'Oklahoma',
    'OR': 'Oregon', 'PA': 'Pennsylvania', 'RI': 'Rhode Island', 'SC': 'South Carolina',
    'SD': 'South Dakota', 'TN': 'Tennessee', 'TX': 'Texas', 'UT': 'Utah',
    'VT': 'Vermont', 'VA': 'Virginia', 'WA': 'Washington', 'WV': 'West Virginia',
    'WI': 'Wisconsin', 'WY': 'Wyoming', 'DC': 'District of Columbia'
}


def search_socrata_catalog(domain=None, query="building permit"):
    """
    Search Socrata open data catalog for permit datasets.

    Args:
        domain: Specific domain to search (e.g., 'data.texas.gov')
        query: Search query

    Returns:
        List of dataset dicts with name, domain, id, etc.
    """
    base_url = "https://api.us.socrata.com/api/catalog/v1"
    params = {
        "q": query,
        "limit": 100,
        "only": "datasets",
    }
    if domain:
        params["domains"] = domain

    try:
        resp = requests.get(base_url, params=params, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            results = data.get('results', [])
            datasets = []
            for r in results:
                resource = r.get('resource', {})
                datasets.append({
                    'name': resource.get('name', ''),
                    'description': resource.get('description', ''),
                    'domain': r.get('metadata', {}).get('domain', ''),
                    'id': resource.get('id', ''),
                    'updatedAt': resource.get('updatedAt', ''),
                    'columns': resource.get('columns_name', []),
                })
            return datasets
    except Exception as e:
        print(f"  [ERROR] Socrata catalog search failed: {e}")
    return []


def test_socrata_endpoint(domain, dataset_id, date_field=None):
    """
    Test a Socrata endpoint using the 5-step protocol.

    Returns:
        dict with keys: reachable, has_permits, has_recent, parseable, sample_records
    """
    result = {
        'reachable': False,
        'has_permits': False,
        'has_recent': False,
        'parseable': False,
        'sample_records': [],
        'record_count': 0,
        'error': None,
    }

    endpoint = f"https://{domain}/resource/{dataset_id}.json"

    # Test 1: Reachable
    try:
        resp = requests.get(f"{endpoint}?$limit=5", timeout=15)
        if resp.status_code != 200:
            result['error'] = f"HTTP {resp.status_code}"
            return result
        result['reachable'] = True

        data = resp.json()
        if not data:
            result['error'] = "Empty response"
            return result

        result['sample_records'] = data[:3]

        # Test 2: Has permit data (check for common permit fields)
        sample = data[0] if data else {}
        permit_fields = ['permit', 'permit_number', 'permitno', 'permit_id', 'record_id', 'application']
        has_permit_field = any(f in str(sample.keys()).lower() for f in permit_fields)
        result['has_permits'] = has_permit_field

        # Test 3: Has recent data
        if date_field and date_field in sample:
            try:
                date_val = sample[date_field]
                if date_val:
                    # Try to parse and check if recent (last 90 days)
                    # Socrata dates can be ISO format
                    if 'T' in str(date_val):
                        date_val = str(date_val).split('T')[0]
                    recent_cutoff = (datetime.now() - timedelta(days=90)).strftime('%Y-%m-%d')
                    result['has_recent'] = str(date_val) >= recent_cutoff
            except:
                pass

        # Test 4: Parseable (has address-like field)
        address_fields = ['address', 'location', 'street', 'site_address', 'permit_address']
        has_address = any(f in str(sample.keys()).lower() for f in address_fields)
        result['parseable'] = has_address

        # Get total count
        count_resp = requests.get(f"{endpoint}?$select=count(*)", timeout=10)
        if count_resp.status_code == 200:
            count_data = count_resp.json()
            if count_data:
                result['record_count'] = int(count_data[0].get('count', 0))

    except Exception as e:
        result['error'] = str(e)[:100]

    return result


def discover_state_sources(state_code):
    """
    Discover permit data sources for a US state.

    Checks:
    1. data.{state}.gov Socrata portal
    2. Common state data portal patterns
    """
    state_name = STATE_NAMES.get(state_code, state_code)
    print(f"\n{'='*60}")
    print(f"Checking {state_name} ({state_code})")
    print('='*60)

    discoveries = []

    # Common state Socrata domains
    domains_to_check = [
        f"data.{state_code.lower()}.gov",
        f"opendata.{state_code.lower()}.gov",
    ]

    for domain in domains_to_check:
        print(f"  Searching {domain}...")
        datasets = search_socrata_catalog(domain=domain, query="building permit")

        if datasets:
            print(f"    Found {len(datasets)} potential datasets")
            for ds in datasets[:5]:  # Check top 5
                name = ds.get('name', 'Unknown')
                ds_id = ds.get('id', '')
                print(f"    - {name[:50]} ({ds_id})")

                # Test the endpoint
                test_result = test_socrata_endpoint(domain, ds_id)
                if test_result['reachable'] and test_result['has_permits']:
                    print(f"      ✓ PASS: {test_result['record_count']} records")
                    discoveries.append({
                        'state': state_code,
                        'name': name,
                        'domain': domain,
                        'dataset_id': ds_id,
                        'test_result': test_result,
                    })
                else:
                    print(f"      ✗ FAIL: {test_result.get('error', 'Not permit data')}")

        time.sleep(1)  # Rate limit

    return discoveries


def add_to_prod_cities(discovery):
    """Add a discovered source to prod_cities table."""
    try:
        city_slug = f"{discovery['state'].lower()}-statewide"

        permitdb.upsert_prod_city(
            city=f"{discovery['state']} Statewide",
            state=discovery['state'],
            city_slug=city_slug,
            source_type='socrata',
            source_id=f"{discovery['state'].lower()}_statewide",
            source_scope='state',
            status='pending',  # Needs manual review before activating
            added_by='discovery_engine',
            notes=f"Auto-discovered {discovery['name']} at {discovery['domain']}/{discovery['dataset_id']}"
        )
        print(f"  Added to prod_cities: {city_slug}")
        return True
    except Exception as e:
        print(f"  [ERROR] Failed to add to prod_cities: {e}")
        return False


def run_state_discovery(states=None):
    """Run discovery for all states or specified list."""
    if states is None:
        states = US_STATES

    print(f"\n{'#'*60}")
    print(f"# V15 STATE-LEVEL SOURCE DISCOVERY")
    print(f"# Checking {len(states)} states")
    print(f"{'#'*60}")

    all_discoveries = []

    for state in states:
        discoveries = discover_state_sources(state)
        all_discoveries.extend(discoveries)
        time.sleep(2)  # Rate limit between states

    print(f"\n{'='*60}")
    print(f"DISCOVERY COMPLETE")
    print(f"{'='*60}")
    print(f"States checked: {len(states)}")
    print(f"Sources discovered: {len(all_discoveries)}")

    if all_discoveries:
        print("\nDiscovered sources:")
        for d in all_discoveries:
            print(f"  - {d['state']}: {d['name']} ({d['dataset_id']})")

    return all_discoveries


def search_county_sources(county_name, state):
    """
    V16: Search for permit data sources for a specific county.

    Args:
        county_name: County name (e.g., "Los Angeles")
        state: State code (e.g., "CA")

    Returns:
        List of discovered sources
    """
    discoveries = []

    # Search Socrata catalog for county permit data
    query = f"{county_name} county building permit"
    datasets = search_socrata_catalog(query=query)

    for ds in datasets[:3]:  # Check top 3 matches
        name = ds.get('name', '')
        domain = ds.get('domain', '')
        ds_id = ds.get('id', '')

        # Filter: must contain county name or state
        name_lower = name.lower()
        if county_name.lower() not in name_lower and state.lower() not in domain.lower():
            continue

        # Test the endpoint
        result = test_socrata_endpoint(domain, ds_id)
        if result['reachable'] and result['has_permits']:
            discoveries.append({
                'county': county_name,
                'state': state,
                'name': name,
                'domain': domain,
                'dataset_id': ds_id,
                'record_count': result['record_count'],
            })

    return discoveries


def run_county_discovery(limit=50):
    """
    V16: Discover county-level permit data sources.

    Searches the Socrata catalog for county building permit datasets.
    """
    print(f"\n{'#'*60}")
    print(f"# V16 COUNTY-LEVEL SOURCE DISCOVERY")
    print(f"# Checking top {limit} counties by population")
    print(f"{'#'*60}")

    # Top US counties by population (simplified list)
    top_counties = [
        ("Los Angeles", "CA"), ("Cook", "IL"), ("Harris", "TX"), ("Maricopa", "AZ"),
        ("San Diego", "CA"), ("Orange", "CA"), ("Miami-Dade", "FL"), ("Dallas", "TX"),
        ("Kings", "NY"), ("Riverside", "CA"), ("Clark", "NV"), ("Queens", "NY"),
        ("San Bernardino", "CA"), ("King", "WA"), ("Tarrant", "TX"), ("Bexar", "TX"),
        ("Santa Clara", "CA"), ("Broward", "FL"), ("Wayne", "MI"), ("Alameda", "CA"),
        ("New York", "NY"), ("Middlesex", "MA"), ("Philadelphia", "PA"), ("Suffolk", "NY"),
        ("Sacramento", "CA"), ("Bronx", "NY"), ("Palm Beach", "FL"), ("Hillsborough", "FL"),
        ("Cuyahoga", "OH"), ("Franklin", "OH"), ("Hennepin", "MN"), ("Allegheny", "PA"),
        ("Orange", "FL"), ("Travis", "TX"), ("Pima", "AZ"), ("Oakland", "MI"),
        ("Contra Costa", "CA"), ("Montgomery", "MD"), ("Wake", "NC"), ("Fairfax", "VA"),
    ]

    all_discoveries = []
    checked = 0

    for county_name, state in top_counties[:limit]:
        checked += 1
        print(f"\n[{checked}/{min(limit, len(top_counties))}] {county_name} County, {state}...", flush=True)

        discoveries = search_county_sources(county_name, state)

        if discoveries:
            for d in discoveries:
                print(f"  ✓ FOUND: {d['name'][:50]} ({d['record_count']:,} records)")
            all_discoveries.extend(discoveries)
        else:
            print(f"  - No sources found")

        time.sleep(2)  # Rate limit

    print(f"\n{'='*60}")
    print(f"COUNTY DISCOVERY COMPLETE")
    print(f"{'='*60}")
    print(f"Counties checked: {checked}")
    print(f"Sources discovered: {len(all_discoveries)}")

    if all_discoveries:
        print("\nDiscovered sources:")
        for d in all_discoveries:
            print(f"  - {d['county']} County, {d['state']}: {d['name'][:40]} ({d['record_count']:,})")

    return all_discoveries


# ---------------------------------------------------------------------------
# V18: City-to-County Mapping for Alternate Source Search
# ---------------------------------------------------------------------------

CITY_TO_COUNTY = {
    'Atlanta': ('Fulton County', 'GA'),
    'Dallas': ('Dallas County', 'TX'),
    'Denver': ('Denver County', 'CO'),
    'Detroit': ('Wayne County', 'MI'),
    'Minneapolis': ('Hennepin County', 'MN'),
    'San Diego': ('San Diego County', 'CA'),
    'Phoenix': ('Maricopa County', 'AZ'),
    'Baltimore': ('Baltimore City', 'MD'),
    'Las Vegas': ('Clark County', 'NV'),
    'Salt Lake City': ('Salt Lake County', 'UT'),
    'Sacramento': ('Sacramento County', 'CA'),
    'Indianapolis': ('Marion County', 'IN'),
    'Milwaukee': ('Milwaukee County', 'WI'),
    'Oklahoma City': ('Oklahoma County', 'OK'),
    'San Jose': ('Santa Clara County', 'CA'),
    'San Francisco': ('San Francisco County', 'CA'),
    'Los Angeles': ('Los Angeles County', 'CA'),
    'Houston': ('Harris County', 'TX'),
    'Chicago': ('Cook County', 'IL'),
    'Philadelphia': ('Philadelphia County', 'PA'),
    'San Antonio': ('Bexar County', 'TX'),
    'Fort Worth': ('Tarrant County', 'TX'),
    'Austin': ('Travis County', 'TX'),
    'Columbus': ('Franklin County', 'OH'),
    'Charlotte': ('Mecklenburg County', 'NC'),
    'Seattle': ('King County', 'WA'),
    'Portland': ('Multnomah County', 'OR'),
    'Boston': ('Suffolk County', 'MA'),
    'Nashville': ('Davidson County', 'TN'),
    'Memphis': ('Shelby County', 'TN'),
    'Louisville': ('Jefferson County', 'KY'),
    'Jacksonville': ('Duval County', 'FL'),
    'Tampa': ('Hillsborough County', 'FL'),
    'Miami': ('Miami-Dade County', 'FL'),
    'Orlando': ('Orange County', 'FL'),
    'Raleigh': ('Wake County', 'NC'),
}


def find_alternate_source(city, state):
    """
    V18: Search for an alternate data source for a stale city.

    Searches:
    1. Socrata catalogs (city portal, state portal)
    2. ArcGIS Hub
    3. County-level sources (using CITY_TO_COUNTY mapping)

    Returns:
        dict with search results and any found candidates
    """
    print(f"\n[V18] Searching for alternate source: {city}, {state}")
    results = {
        'city': city,
        'state': state,
        'searched': [],
        'candidates': [],
        'best_match': None,
    }

    # Normalize city name for search
    city_lower = city.lower().replace(' ', '-')
    city_search = city.replace('-', ' ')

    # 1. Search Socrata for city-specific data
    search_queries = [
        f"building permit {city_search}",
        f"construction permit {city_search}",
        f"permit {city_search}",
    ]

    for query in search_queries:
        try:
            datasets = search_socrata_catalog(query=query)
            results['searched'].append(f"Socrata: {query}")

            for ds in datasets:
                # Check if dataset is from this state
                ds_state = ds.get('metadata', {}).get('domain_metadata', {}).get('state', '')
                domain = ds.get('domain', '')

                # Heuristic: check if domain or name contains city/state
                name_lower = ds.get('name', '').lower()
                if (city_lower in name_lower or
                    city_lower in domain or
                    state.lower() in domain):

                    # Test if this dataset has recent data
                    candidate = {
                        'source': 'socrata',
                        'name': ds.get('name'),
                        'domain': domain,
                        'dataset_id': ds.get('id'),
                        'endpoint': f"https://{domain}/resource/{ds.get('id')}.json",
                        'record_count': ds.get('resource', {}).get('records_total', 0),
                    }

                    # Quick test for recent data
                    test_result = test_socrata_endpoint(domain, ds.get('id'))
                    if test_result.get('status') == 'success':
                        candidate['has_recent_data'] = test_result.get('has_recent_data', False)
                        candidate['newest_date'] = test_result.get('newest_date')
                        candidate['field_map'] = test_result.get('field_map', {})

                        if test_result.get('has_recent_data'):
                            results['candidates'].append(candidate)
                            print(f"  [FOUND] {candidate['name'][:50]} - {candidate['newest_date']}")

        except Exception as e:
            print(f"  [ERROR] Socrata search failed: {e}")

        time.sleep(0.5)  # Rate limiting

    # 2. Search county-level source if city is in mapping
    if city in CITY_TO_COUNTY:
        county_name, county_state = CITY_TO_COUNTY[city]
        if county_state == state:
            results['searched'].append(f"County: {county_name}")
            print(f"  Checking county portal: {county_name}")

            # Search for county data
            county_query = f"building permit {county_name}"
            try:
                datasets = search_socrata_catalog(query=county_query)
                for ds in datasets:
                    domain = ds.get('domain', '')
                    name_lower = ds.get('name', '').lower()

                    if 'permit' in name_lower:
                        candidate = {
                            'source': 'socrata_county',
                            'name': ds.get('name'),
                            'domain': domain,
                            'dataset_id': ds.get('id'),
                            'endpoint': f"https://{domain}/resource/{ds.get('id')}.json",
                            'county': county_name,
                        }
                        results['candidates'].append(candidate)
                        print(f"  [COUNTY] {candidate['name'][:50]}")

            except Exception as e:
                print(f"  [ERROR] County search failed: {e}")

    # 3. Select best candidate
    if results['candidates']:
        # Prefer candidates with recent data
        recent_candidates = [c for c in results['candidates'] if c.get('has_recent_data')]
        if recent_candidates:
            results['best_match'] = recent_candidates[0]
        else:
            results['best_match'] = results['candidates'][0]

        print(f"\n  [BEST MATCH] {results['best_match']['name']}")

    return results


def main():
    parser = argparse.ArgumentParser(description='V16 Source Discovery Engine')
    parser.add_argument('--states', action='store_true', help='Check all 50 states')
    parser.add_argument('--state', type=str, help='Check specific state (e.g., TX)')
    parser.add_argument('--counties', action='store_true', help='Check top counties by population')
    parser.add_argument('--county-limit', type=int, default=50, help='Number of counties to check (default: 50)')
    parser.add_argument('--cities', action='store_true', help='Check cities by population (not yet implemented)')
    parser.add_argument('--test', type=str, help='Test specific domain/dataset (domain:id)')
    parser.add_argument('--find-alt', type=str, help='Find alternate source for city (city:state)')

    args = parser.parse_args()

    if args.find_alt:
        if ':' in args.find_alt:
            city, state = args.find_alt.rsplit(':', 1)
            print(f"Searching for alternate source for {city}, {state}...")
            result = find_alternate_source(city, state.upper())
            print(json.dumps(result, indent=2, default=str))
        else:
            print("Usage: --find-alt 'City Name:ST'")
    elif args.states:
        run_state_discovery()
    elif args.state:
        run_state_discovery([args.state.upper()])
    elif args.counties:
        run_county_discovery(limit=args.county_limit)
    elif args.cities:
        print("City-level discovery not yet implemented. Use --counties instead.")
    elif args.test:
        if ':' in args.test:
            domain, ds_id = args.test.split(':', 1)
            print(f"Testing {domain}/{ds_id}...")
            result = test_socrata_endpoint(domain, ds_id)
            print(json.dumps(result, indent=2, default=str))
        else:
            print("Usage: --test domain:dataset_id")
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
