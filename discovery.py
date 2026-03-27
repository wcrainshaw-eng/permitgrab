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


def main():
    parser = argparse.ArgumentParser(description='V15 Source Discovery Engine')
    parser.add_argument('--states', action='store_true', help='Check all 50 states')
    parser.add_argument('--state', type=str, help='Check specific state (e.g., TX)')
    parser.add_argument('--counties', action='store_true', help='Check top 200 counties')
    parser.add_argument('--cities', action='store_true', help='Check cities by population')
    parser.add_argument('--test', type=str, help='Test specific domain/dataset (domain:id)')

    args = parser.parse_args()

    if args.states:
        run_state_discovery()
    elif args.state:
        run_state_discovery([args.state.upper()])
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
