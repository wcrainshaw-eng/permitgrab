#!/usr/bin/env python3
"""
PermitGrab - County/City Bulk Source Discovery with STRICT Recency Filter
V12.34: Searches ALL Socrata portals and ArcGIS hubs for current permit datasets.

Usage:
  python discover_counties.py                    # Full discovery
  python discover_counties.py --socrata-only     # Socrata only
  python discover_counties.py --arcgis-only      # ArcGIS only
  python discover_counties.py --min-cities 5     # Require 5+ cities
"""

import requests
import json
import os
import sys
import time
import argparse
from datetime import datetime, timedelta
from collections import defaultdict

# Search queries - specific enough to find permit data
SOCRATA_SEARCH_QUERIES = [
    "building permits issued",
    "issued building permits",
    "building permit applications",
    "construction permits issued",
    "residential building permits",
    "commercial building permits",
    "permit applications filed",
    "building permits",
    "construction permits",
]

# ArcGIS hubs for top counties
ARCGIS_HUBS = [
    ("Miami-Dade County, FL", "gis-mdc.opendata.arcgis.com"),
    ("Los Angeles County, CA", "data-lacounty.opendata.arcgis.com"),
    ("Franklin County, OH", "data-columbus.opendata.arcgis.com"),
    ("Harris County, TX", "geo-harriscounty.opendata.arcgis.com"),
    ("Cuyahoga County, OH", "data-cuyahoga.opendata.arcgis.com"),
    ("Clark County, NV", "opendataportal-lasvegas.opendata.arcgis.com"),
    ("Wake County, NC", "data-wake.opendata.arcgis.com"),
    ("Hillsborough County, FL", "city-tampa.opendata.arcgis.com"),
    ("Wayne County, MI", "data-detroitmi.hub.arcgis.com"),
    ("Denver County, CO", "opendata-geospatialdenver.hub.arcgis.com"),
    ("Maricopa County, AZ", "gis-maricopa.opendata.arcgis.com"),
    ("Cook County, IL", "datacatalog.cookcountyil.gov"),
    ("King County, WA", "gis-kingcounty.opendata.arcgis.com"),
    ("Dallas County, TX", "data-dallascityhall.opendata.arcgis.com"),
    ("Orange County, CA", "data-ocgis.opendata.arcgis.com"),
    ("San Bernardino County, CA", "open-sbcounty.opendata.arcgis.com"),
    ("Riverside County, CA", "gis1-countyofriverside.opendata.arcgis.com"),
    ("Tarrant County, TX", "data-fortworthtexas.opendata.arcgis.com"),
    ("Bexar County, TX", "opendata-cosagis.opendata.arcgis.com"),
    ("Broward County, FL", "gis-broward.opendata.arcgis.com"),
]

# Fields that indicate permit data
PERMIT_FIELD_INDICATORS = [
    'permit', 'license', 'application', 'app_no', 'permit_no',
    'address', 'location', 'street', 'site_address',
    'contractor', 'applicant', 'owner', 'builder',
    'cost', 'value', 'valuation', 'job_value', 'estimated',
    'description', 'work_type', 'type', 'permit_type', 'work_desc',
    'issued', 'filed', 'submitted', 'date',
]

# Fields that indicate city/municipality
CITY_FIELD_INDICATORS = [
    'city', 'municipality', 'muniname', 'town', 'jurisdiction',
    'community', 'locale', 'muni', 'city_name', 'incorporated',
    'unincorporated', 'place', 'township',
]

# Recency cutoff
RECENCY_DAYS = 90

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "PermitGrab/1.0 (county discovery; contact@permitgrab.com)",
    "Accept": "application/json",
})


def parse_date(date_str):
    """Parse various date formats from APIs."""
    if not date_str:
        return None

    # Handle ISO format with timezone
    if isinstance(date_str, str):
        # Strip timezone suffix
        date_str = date_str.split('T')[0] if 'T' in date_str else date_str
        date_str = date_str.split(' ')[0]  # Strip time if present

        for fmt in ['%Y-%m-%d', '%m/%d/%Y', '%Y/%m/%d', '%d-%m-%Y']:
            try:
                return datetime.strptime(date_str, fmt)
            except ValueError:
                continue

    return None


def is_current_dataset(records, date_fields):
    """Check if dataset has records from last 90 days."""
    cutoff = datetime.now() - timedelta(days=RECENCY_DAYS)

    for record in records:
        for field in date_fields:
            if field in record:
                date = parse_date(record[field])
                if date and date > cutoff:
                    return True, date

    return False, None


def has_permit_fields(record):
    """Check if record looks like a building permit."""
    fields = set(k.lower() for k in record.keys())

    matches = 0
    matched_fields = []
    for indicator in PERMIT_FIELD_INDICATORS:
        for f in fields:
            if indicator in f:
                matches += 1
                matched_fields.append(f)
                break

    return matches >= 3, matched_fields


def find_city_field(columns):
    """Find the city/municipality field in columns."""
    columns_lower = {c.lower(): c for c in columns}

    for indicator in CITY_FIELD_INDICATORS:
        for col_lower, col_actual in columns_lower.items():
            if indicator in col_lower:
                return col_actual

    return None


def find_date_field(columns):
    """Find the date field in columns."""
    columns_lower = {c.lower(): c for c in columns}

    date_indicators = ['issued', 'date', 'filed', 'created', 'submitted', 'applied']
    for indicator in date_indicators:
        for col_lower, col_actual in columns_lower.items():
            if indicator in col_lower:
                return col_actual

    return None


def count_unique_cities(endpoint, city_field, limit=1000):
    """Count unique cities in a dataset."""
    try:
        url = f"{endpoint}?$select=distinct {city_field}&$limit={limit}"
        resp = SESSION.get(url, timeout=15)
        if resp.status_code != 200:
            return 0, []

        data = resp.json()
        cities = [r.get(city_field) for r in data if r.get(city_field)]
        return len(cities), cities[:20]  # Return count and sample
    except Exception:
        return 0, []


def test_socrata_dataset(domain, resource_id, metadata):
    """Test a Socrata dataset for usability."""
    endpoint = f"https://{domain}/resource/{resource_id}.json"

    result = {
        'domain': domain,
        'resource_id': resource_id,
        'name': metadata.get('name', ''),
        'endpoint': endpoint,
        'usable': False,
        'reason': '',
    }

    try:
        # Get columns from metadata
        columns = metadata.get('columns', [])

        # Find city and date fields
        city_field = find_city_field(columns)
        date_field = find_date_field(columns)

        if not city_field:
            result['reason'] = 'No city/municipality field'
            return result

        result['city_field'] = city_field
        result['date_field'] = date_field

        # Fetch sample records sorted by date DESC if possible
        if date_field:
            url = f"{endpoint}?$order={date_field} DESC&$limit=10"
        else:
            url = f"{endpoint}?$limit=10"

        resp = SESSION.get(url, timeout=15)
        if resp.status_code != 200:
            result['reason'] = f'HTTP {resp.status_code}'
            return result

        records = resp.json()
        if not records:
            result['reason'] = 'No records'
            return result

        # Check if records look like permits
        has_permits, matched = has_permit_fields(records[0])
        if not has_permits:
            result['reason'] = 'Not permit data (missing key fields)'
            return result

        result['permit_fields'] = matched

        # Check recency
        date_fields = [f for f in records[0].keys() if any(d in f.lower() for d in ['date', 'issued', 'filed', 'created'])]
        is_current, latest = is_current_dataset(records, date_fields)

        if not is_current:
            result['reason'] = f'Stale data (no records in last {RECENCY_DAYS} days)'
            return result

        result['latest_date'] = latest.isoformat() if latest else None

        # Count unique cities
        city_count, sample_cities = count_unique_cities(endpoint, city_field)
        result['city_count'] = city_count
        result['sample_cities'] = sample_cities

        if city_count < 1:
            result['reason'] = 'No cities found'
            return result

        # Success!
        result['usable'] = True
        result['columns'] = columns
        result['sample_record'] = records[0]

    except Exception as e:
        result['reason'] = f'Error: {str(e)[:50]}'

    return result


def search_socrata_catalog(query, limit=100, offset=0):
    """Search ALL Socrata portals for datasets."""
    url = "https://api.us.socrata.com/api/catalog/v1"
    params = {
        'q': query,
        'limit': limit,
        'offset': offset,
    }

    try:
        resp = SESSION.get(url, params=params, timeout=30)
        if resp.status_code != 200:
            return [], 0

        data = resp.json()
        return data.get('results', []), data.get('resultSetSize', 0)
    except Exception as e:
        print(f"  Search error: {e}")
        return [], 0


def discover_socrata():
    """Discover permit datasets across ALL Socrata portals."""
    print("\n" + "=" * 60)
    print("SOCRATA DISCOVERY - Searching all portals")
    print("=" * 60)

    all_results = {}  # Keyed by resource_id to deduplicate

    for query in SOCRATA_SEARCH_QUERIES:
        print(f"\n  Searching: '{query}'...")

        offset = 0
        total = 0

        while True:
            results, result_count = search_socrata_catalog(query, limit=100, offset=offset)

            if offset == 0:
                total = result_count
                print(f"    Found {total} results")

            if not results:
                break

            for r in results:
                resource = r.get('resource', {})
                resource_id = resource.get('id', '')

                if resource_id in all_results:
                    continue  # Already tested

                # Quick pre-filter: skip if obviously not permits
                name = resource.get('name', '').lower()
                desc = resource.get('description', '').lower()

                skip_terms = ['well', 'oil', 'gas', 'highway', 'road', 'bridge',
                              'tax', 'budget', 'population', 'census', 'election',
                              'quarterly', 'annual report', 'summary', 'statistics']

                if any(term in name for term in skip_terms):
                    all_results[resource_id] = {'usable': False, 'reason': 'Skipped (name filter)'}
                    continue

                # Test the dataset
                metadata = r.get('metadata', {})
                domain = metadata.get('domain', '')

                columns = resource.get('columns_field_name', [])

                test_result = test_socrata_dataset(domain, resource_id, {
                    'name': resource.get('name', ''),
                    'columns': columns,
                })

                all_results[resource_id] = test_result

                if test_result['usable']:
                    print(f"    USABLE: {test_result['name'][:50]}")
                    print(f"      Domain: {domain}, Cities: {test_result.get('city_count', '?')}")

                time.sleep(0.3)  # Rate limit

            offset += 100
            if offset >= min(total, 500):  # Cap at 500 per query
                break

        time.sleep(1)  # Rate limit between queries

    return all_results


def test_arcgis_hub(name, domain):
    """Test an ArcGIS hub for permit datasets."""
    print(f"\n  Testing: {name} ({domain})")

    results = []

    # Search the hub
    search_url = f"https://{domain}/api/v3/search"
    try:
        params = {'q': 'building permits', 'per_page': 20}
        resp = SESSION.get(search_url, params=params, timeout=15)

        if resp.status_code != 200:
            print(f"    Search failed: HTTP {resp.status_code}")
            return results

        data = resp.json()
        items = data.get('data', [])

        for item in items:
            attrs = item.get('attributes', {})
            item_name = attrs.get('name', '')
            item_url = attrs.get('url', '')

            # Skip if not a FeatureServer
            if 'FeatureServer' not in item_url and 'MapServer' not in item_url:
                continue

            print(f"    Found: {item_name[:50]}")

            # Try to query the service
            try:
                # Get layer info
                layer_url = item_url + '/0' if not item_url.endswith('/0') else item_url
                query_url = f"{layer_url}/query"

                params = {
                    'where': '1=1',
                    'outFields': '*',
                    'resultRecordCount': 10,
                    'f': 'json',
                }

                resp = SESSION.get(query_url, params=params, timeout=15)
                if resp.status_code != 200:
                    continue

                data = resp.json()
                features = data.get('features', [])

                if not features:
                    continue

                # Check if it's permit data
                record = features[0].get('attributes', {})
                has_permits, matched = has_permit_fields(record)

                if not has_permits:
                    continue

                # Find city field
                columns = list(record.keys())
                city_field = find_city_field(columns)

                results.append({
                    'name': item_name,
                    'hub': domain,
                    'endpoint': query_url,
                    'usable': True,
                    'city_field': city_field,
                    'columns': columns,
                    'permit_fields': matched,
                    'platform': 'arcgis',
                })

                print(f"      USABLE: {len(matched)} permit fields, city_field={city_field}")

            except Exception as e:
                print(f"      Query error: {e}")

    except Exception as e:
        print(f"    Hub error: {e}")

    return results


def discover_arcgis():
    """Discover permit datasets on ArcGIS hubs."""
    print("\n" + "=" * 60)
    print("ARCGIS DISCOVERY - Searching county hubs")
    print("=" * 60)

    all_results = []

    for name, domain in ARCGIS_HUBS:
        results = test_arcgis_hub(name, domain)
        all_results.extend(results)
        time.sleep(1)

    return all_results


def generate_bulk_config(result, platform='socrata'):
    """Generate a BULK_SOURCES config entry."""
    if platform == 'socrata':
        # Derive a source key from domain
        domain = result.get('domain', '')
        parts = domain.replace('.gov', '').replace('.org', '').replace('data.', '').replace('opendata.', '').split('.')
        source_key = '_'.join(parts) + '_bulk'

        # Build field map
        columns_lower = {c.lower(): c for c in result.get('columns', [])}
        field_map = {}

        # Map standard fields
        mappings = {
            'permit_number': ['permit', 'permitno', 'permit_number', 'permit_id', 'application', 'app_no'],
            'date': ['issued', 'date', 'filed', 'created', 'permitdate', 'issue_date'],
            'address': ['address', 'site_address', 'location', 'street_address'],
            'description': ['description', 'work_description', 'work_desc', 'permit_type', 'type'],
            'estimated_cost': ['cost', 'value', 'valuation', 'job_value', 'estimated_cost'],
            'contractor': ['contractor', 'contractor_name', 'builder', 'applicant'],
            'owner': ['owner', 'owner_name', 'property_owner'],
        }

        for our_field, patterns in mappings.items():
            for pattern in patterns:
                for col_lower, col_actual in columns_lower.items():
                    if pattern in col_lower:
                        field_map[our_field] = col_actual
                        break
                if our_field in field_map:
                    break

        config = {
            'name': result.get('name', source_key)[:50],
            'source_key': source_key,
            'state': None,  # Needs manual assignment
            'platform': 'socrata',
            'mode': 'bulk',
            'endpoint': result.get('endpoint'),
            'dataset_id': result.get('resource_id'),
            'city_field': result.get('city_field'),
            'field_map': field_map,
            'date_field': result.get('date_field') or field_map.get('date', 'date'),
            'limit': 50000,
            'active': True,
            'city_count': result.get('city_count', 0),
            'latest_date': result.get('latest_date'),
            'notes': f"V12.34: Auto-discovered from {domain}",
        }

        return config

    return None


def main():
    parser = argparse.ArgumentParser(description='Discover county/city permit datasets')
    parser.add_argument('--socrata-only', action='store_true', help='Search only Socrata')
    parser.add_argument('--arcgis-only', action='store_true', help='Search only ArcGIS')
    parser.add_argument('--min-cities', type=int, default=1, help='Minimum cities required')
    parser.add_argument('--output', default='data/county_discoveries.json', help='Output file')
    args = parser.parse_args()

    print("=" * 60)
    print("PermitGrab County/City Discovery V12.34")
    print(f"Recency filter: {RECENCY_DAYS} days")
    print(f"Min cities: {args.min_cities}")
    print("=" * 60)

    socrata_results = {}
    arcgis_results = []

    if not args.arcgis_only:
        socrata_results = discover_socrata()

    if not args.socrata_only:
        arcgis_results = discover_arcgis()

    # Filter usable results
    usable_socrata = [r for r in socrata_results.values()
                      if r.get('usable') and r.get('city_count', 0) >= args.min_cities]
    usable_arcgis = [r for r in arcgis_results if r.get('usable')]

    # Generate summary
    print("\n" + "=" * 60)
    print("DISCOVERY SUMMARY")
    print("=" * 60)

    print(f"\nSocrata: {len(usable_socrata)} usable datasets")
    print(f"ArcGIS: {len(usable_arcgis)} usable datasets")

    # Sort by city count
    usable_socrata.sort(key=lambda x: x.get('city_count', 0), reverse=True)

    print("\n" + "-" * 40)
    print("TOP SOCRATA DATASETS BY CITY COUNT:")
    print("-" * 40)

    configs = []
    for r in usable_socrata[:30]:
        config = generate_bulk_config(r)
        if config:
            configs.append(config)
            print(f"\n  {r['name'][:50]}")
            print(f"    Domain: {r['domain']}")
            print(f"    Cities: {r.get('city_count', '?')}")
            print(f"    City field: {r.get('city_field')}")
            print(f"    Latest: {r.get('latest_date', 'N/A')}")
            print(f"    Sample cities: {r.get('sample_cities', [])[:5]}")

    print("\n" + "-" * 40)
    print("ARCGIS DATASETS:")
    print("-" * 40)

    for r in usable_arcgis:
        print(f"\n  {r['name'][:50]}")
        print(f"    Hub: {r['hub']}")
        print(f"    City field: {r.get('city_field', 'N/A')}")

    # Calculate potential city coverage
    total_cities = sum(c.get('city_count', 0) for c in configs)
    print("\n" + "=" * 60)
    print(f"POTENTIAL CITY COVERAGE: {total_cities}+ cities")
    print("=" * 60)

    # Save results
    output_dir = os.path.dirname(args.output)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    results = {
        'discovered_at': datetime.now().isoformat(),
        'recency_filter_days': RECENCY_DAYS,
        'socrata_usable': len(usable_socrata),
        'arcgis_usable': len(usable_arcgis),
        'total_potential_cities': total_cities,
        'suggested_configs': configs,
        'socrata_results': usable_socrata,
        'arcgis_results': usable_arcgis,
    }

    with open(args.output, 'w') as f:
        json.dump(results, f, indent=2, default=str)

    print(f"\nResults saved to: {args.output}")

    # Print ready-to-paste configs
    print("\n" + "=" * 60)
    print("READY-TO-PASTE BULK_SOURCES CONFIGS:")
    print("=" * 60)

    for c in configs[:10]:
        print(f'''
    "{c['source_key']}": {{
        "name": "{c['name']}",
        "state": None,  # TODO: Set state
        "platform": "socrata",
        "mode": "bulk",
        "endpoint": "{c['endpoint']}",
        "dataset_id": "{c.get('dataset_id', '')}",
        "city_field": "{c['city_field']}",
        "field_map": {json.dumps(c['field_map'], indent=8)},
        "date_field": "{c['date_field']}",
        "limit": 50000,
        "active": True,
        "notes": "V12.34: Auto-discovered, {c['city_count']} cities",
    }},''')


if __name__ == '__main__':
    main()
