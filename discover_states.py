#!/usr/bin/env python3
"""
PermitGrab - State-Level Bulk Source Discovery
V12.33: Searches all 50 state Socrata portals for building permit datasets.

Usage:
  python discover_states.py              # Search all states
  python discover_states.py --state NJ   # Search one state
  python discover_states.py --test       # Test discovered datasets
"""

import requests
import json
import os
import sys
import time
import argparse
from datetime import datetime

# State portal domains - Socrata open data portals
STATE_PORTALS = {
    'AL': ['data.alabama.gov'],
    'AK': ['data.alaska.gov'],
    'AZ': ['data.arizona.gov', 'data.az.gov'],
    'AR': ['data.arkansas.gov'],
    'CA': ['data.ca.gov', 'data.california.gov'],
    'CO': ['data.colorado.gov'],
    'CT': ['data.ct.gov', 'data.connecticut.gov'],
    'DE': ['data.delaware.gov'],
    'FL': ['data.florida.gov', 'open.florida.gov'],
    'GA': ['data.georgia.gov'],
    'HI': ['data.hawaii.gov'],
    'ID': ['data.idaho.gov'],
    'IL': ['data.illinois.gov'],
    'IN': ['data.indiana.gov'],
    'IA': ['data.iowa.gov', 'mydata.iowa.gov'],
    'KS': ['data.kansas.gov'],
    'KY': ['data.kentucky.gov', 'data.ky.gov'],
    'LA': ['data.louisiana.gov', 'data.la.gov'],
    'ME': ['data.maine.gov'],
    'MD': ['data.maryland.gov', 'opendata.maryland.gov'],
    'MA': ['data.mass.gov', 'data.massachusetts.gov'],
    'MI': ['data.michigan.gov'],
    'MN': ['data.minnesota.gov', 'gisdata.mn.gov'],
    'MS': ['data.mississippi.gov'],
    'MO': ['data.missouri.gov', 'data.mo.gov'],
    'MT': ['data.montana.gov'],
    'NE': ['data.nebraska.gov'],
    'NV': ['data.nevada.gov', 'data.nv.gov'],
    'NH': ['data.newhampshire.gov', 'data.nh.gov'],
    'NJ': ['data.nj.gov'],  # CONFIRMED WORKING
    'NM': ['data.newmexico.gov', 'data.nm.gov'],
    'NY': ['data.ny.gov', 'data.newyork.gov'],
    'NC': ['data.nc.gov', 'data.northcarolina.gov'],
    'ND': ['data.northdakota.gov', 'data.nd.gov'],
    'OH': ['data.ohio.gov'],
    'OK': ['data.oklahoma.gov', 'data.ok.gov'],
    'OR': ['data.oregon.gov'],
    'PA': ['data.pa.gov', 'data.pennsylvania.gov'],
    'RI': ['data.rhodeisland.gov', 'data.ri.gov'],
    'SC': ['data.sc.gov', 'data.southcarolina.gov'],
    'SD': ['data.southdakota.gov', 'data.sd.gov'],
    'TN': ['data.tennessee.gov', 'data.tn.gov'],
    'TX': ['data.texas.gov'],
    'UT': ['data.utah.gov', 'opendata.utah.gov'],
    'VT': ['data.vermont.gov'],
    'VA': ['data.virginia.gov'],
    'WA': ['data.wa.gov', 'data.washington.gov'],
    'WV': ['data.wv.gov', 'data.westvirginia.gov'],
    'WI': ['data.wisconsin.gov', 'data.wi.gov'],
    'WY': ['data.wyoming.gov', 'data.wyo.gov'],
}

# Keywords to search for building permit datasets
PERMIT_KEYWORDS = [
    'building permits',
    'construction permits',
    'permit applications',
    'building permit',
    'uniform construction code',
]

# Required fields for a usable bulk source
REQUIRED_PATTERNS = {
    'permit_number': ['permit', 'permitno', 'permit_number', 'permit_id', 'application'],
    'city_field': ['city', 'municipality', 'muniname', 'town', 'jurisdiction', 'locale'],
    'date_field': ['date', 'issued', 'filed', 'created', 'permitdate'],
}

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "PermitGrab/1.0 (state discovery; contact@permitgrab.com)",
    "Accept": "application/json",
})


def search_socrata_catalog(domain, keyword):
    """Search Socrata discovery API for datasets on a domain."""
    search_url = "https://api.us.socrata.com/api/catalog/v1"
    params = {
        'q': keyword,
        'domains': domain,
        'limit': 10,
    }
    try:
        resp = SESSION.get(search_url, params=params, timeout=15)
        if resp.status_code != 200:
            return []
        data = resp.json()
        return data.get('results', [])
    except Exception as e:
        print(f"    Search error for {domain}: {e}")
        return []


def analyze_dataset(result):
    """Analyze a dataset result to check if it has individual permit records."""
    resource = result.get('resource', {})
    metadata = result.get('metadata', {})

    info = {
        'name': resource.get('name', ''),
        'id': resource.get('id', ''),
        'domain': metadata.get('domain', ''),
        'link': result.get('link', ''),
        'columns': resource.get('columns_field_name', []),
        'description': resource.get('description', '')[:200],
        'updated_at': resource.get('updatedAt', ''),
    }

    # Check for required fields
    columns_lower = [c.lower() for c in info['columns']]

    # Must have a city/municipality field to be useful as bulk source
    has_city = any(
        any(p in col for p in REQUIRED_PATTERNS['city_field'])
        for col in columns_lower
    )

    # Must have permit number
    has_permit = any(
        any(p in col for p in REQUIRED_PATTERNS['permit_number'])
        for col in columns_lower
    )

    # Must have date field
    has_date = any(
        any(p in col for p in REQUIRED_PATTERNS['date_field'])
        for col in columns_lower
    )

    info['has_city_field'] = has_city
    info['has_permit_field'] = has_permit
    info['has_date_field'] = has_date
    info['is_bulk_candidate'] = has_city and has_permit

    # Identify the actual field names
    if has_city:
        for col in info['columns']:
            if any(p in col.lower() for p in REQUIRED_PATTERNS['city_field']):
                info['city_field_name'] = col
                break

    return info


def test_dataset(info):
    """Fetch a sample record to verify the dataset works."""
    if not info.get('link'):
        return info

    try:
        # Build Socrata API URL
        endpoint = info['link']
        if not endpoint.endswith('.json'):
            # Convert HTML link to API endpoint
            domain = info.get('domain', '')
            dataset_id = info.get('id', '')
            endpoint = f"https://{domain}/resource/{dataset_id}.json"

        resp = SESSION.get(endpoint, params={'$limit': 5}, timeout=15)
        if resp.status_code != 200:
            info['test_status'] = f'HTTP {resp.status_code}'
            return info

        records = resp.json()
        if not records or not isinstance(records, list):
            info['test_status'] = 'No records'
            return info

        info['test_status'] = 'OK'
        info['sample_record'] = records[0] if records else {}
        info['record_count_sample'] = len(records)

        # Count unique cities in sample
        city_field = info.get('city_field_name', '')
        if city_field and records:
            cities = set(r.get(city_field, '') for r in records if r.get(city_field))
            info['unique_cities_in_sample'] = len(cities)
            info['sample_cities'] = list(cities)[:10]

    except Exception as e:
        info['test_status'] = f'Error: {str(e)[:50]}'

    return info


def discover_state(state_abbrev):
    """Search a state's portals for building permit datasets."""
    print(f"\n{'='*60}")
    print(f"Searching {state_abbrev}...")
    print('='*60)

    portals = STATE_PORTALS.get(state_abbrev, [])
    if not portals:
        print(f"  No known portals for {state_abbrev}")
        return []

    candidates = []

    for domain in portals:
        print(f"\n  Portal: {domain}")

        for keyword in PERMIT_KEYWORDS:
            print(f"    Searching: '{keyword}'...")
            results = search_socrata_catalog(domain, keyword)

            for result in results:
                info = analyze_dataset(result)

                if info['is_bulk_candidate']:
                    print(f"    FOUND: {info['name']}")
                    print(f"      ID: {info['id']}")
                    print(f"      City field: {info.get('city_field_name', 'N/A')}")
                    print(f"      Columns: {info['columns'][:10]}")
                    candidates.append(info)

            time.sleep(0.5)  # Rate limit

    # Deduplicate by dataset ID
    seen_ids = set()
    unique_candidates = []
    for c in candidates:
        if c['id'] not in seen_ids:
            seen_ids.add(c['id'])
            unique_candidates.append(c)

    print(f"\n  Found {len(unique_candidates)} unique bulk source candidates")
    return unique_candidates


def generate_config(info, state_abbrev):
    """Generate a BULK_SOURCES config entry for a discovered dataset."""
    domain = info.get('domain', '')
    dataset_id = info.get('id', '')

    # Build field map from columns
    field_map = {}
    columns_lower = {c.lower(): c for c in info.get('columns', [])}

    # Map standard fields
    for our_field, patterns in REQUIRED_PATTERNS.items():
        for pattern in patterns:
            for col_lower, col_actual in columns_lower.items():
                if pattern in col_lower:
                    field_map[our_field.replace('_field', '')] = col_actual
                    break
            if our_field.replace('_field', '') in field_map:
                break

    # Add common cost fields
    for col_lower, col_actual in columns_lower.items():
        if any(p in col_lower for p in ['cost', 'value', 'valuation', 'amount']):
            field_map['estimated_cost'] = col_actual
            break

    config = {
        'name': f"{state_abbrev} Statewide",
        'state': state_abbrev,
        'platform': 'socrata',
        'mode': 'bulk',
        'endpoint': f"https://{domain}/resource/{dataset_id}.json",
        'dataset_id': dataset_id,
        'description': info.get('name', ''),
        'city_field': info.get('city_field_name', ''),
        'field_map': field_map,
        'date_field': field_map.get('date', 'permitdate'),
        'limit': 50000,
        'active': True,
        'notes': f"V12.33: Auto-discovered from {domain}",
    }

    return config


def main():
    parser = argparse.ArgumentParser(description='Discover state-level permit datasets')
    parser.add_argument('--state', help='Search a specific state (e.g., NJ)')
    parser.add_argument('--test', action='store_true', help='Test discovered datasets')
    parser.add_argument('--output', default='data/state_discoveries.json', help='Output file')
    args = parser.parse_args()

    print("=" * 60)
    print("PermitGrab State Discovery Tool V12.33")
    print("=" * 60)

    all_discoveries = {}

    if args.state:
        states = [args.state.upper()]
    else:
        states = list(STATE_PORTALS.keys())

    for state in states:
        candidates = discover_state(state)

        if args.test and candidates:
            print(f"\n  Testing {len(candidates)} candidates...")
            for i, c in enumerate(candidates):
                print(f"    [{i+1}/{len(candidates)}] Testing {c['name'][:40]}...")
                test_dataset(c)
                time.sleep(1)

        if candidates:
            all_discoveries[state] = candidates

    # Generate summary
    print("\n" + "=" * 60)
    print("DISCOVERY SUMMARY")
    print("=" * 60)

    total_candidates = sum(len(v) for v in all_discoveries.values())
    print(f"Total bulk source candidates: {total_candidates}")
    print(f"States with candidates: {len(all_discoveries)}")

    for state, candidates in sorted(all_discoveries.items()):
        print(f"\n  {state}: {len(candidates)} datasets")
        for c in candidates:
            status = c.get('test_status', 'not tested')
            cities = c.get('unique_cities_in_sample', '?')
            print(f"    - {c['name'][:50]} [{status}] ({cities} cities)")

    # Generate config suggestions
    print("\n" + "=" * 60)
    print("SUGGESTED CONFIGS")
    print("=" * 60)

    configs = []
    for state, candidates in all_discoveries.items():
        for c in candidates:
            if c.get('test_status') == 'OK':
                config = generate_config(c, state)
                configs.append(config)
                print(f"\n  {state}: {c['name']}")
                print(f"    endpoint: {config['endpoint']}")
                print(f"    city_field: {config['city_field']}")

    # Save results
    output_dir = os.path.dirname(args.output)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    results = {
        'discovered_at': datetime.now().isoformat(),
        'states_searched': len(states),
        'candidates_found': total_candidates,
        'discoveries': all_discoveries,
        'suggested_configs': configs,
    }

    with open(args.output, 'w') as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\n\nResults saved to: {args.output}")


if __name__ == '__main__':
    main()
