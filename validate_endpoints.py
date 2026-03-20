#!/usr/bin/env python3
"""
PermitGrab - Endpoint Validator
V12.30: Validates all active city configs and categorizes issues.

Usage:
  python validate_endpoints.py              # Full validation
  python validate_endpoints.py --quick      # Just check HTTP status
  python validate_endpoints.py --fix        # Generate fix suggestions
"""

import requests
import json
import os
import sys
import time
import argparse
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

# Import city configs
sys.path.insert(0, os.path.dirname(__file__))
from city_configs import CITY_REGISTRY, get_active_cities, get_city_config

# Output directory
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(DATA_DIR, exist_ok=True)

# Request settings
TIMEOUT = 10
MAX_WORKERS = 10  # Parallel requests

# Session with proper headers
SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "PermitGrab/1.0 (endpoint validator; contact@permitgrab.com)",
    "Accept": "application/json",
})

# Common field name patterns for auto-mapping
FIELD_PATTERNS = {
    'permit_number': ['permit', 'permit_no', 'permit_num', 'permitnumber', 'permit_id', 'permnum', 'application'],
    'address': ['address', 'location', 'site_address', 'street', 'property_address', 'situs'],
    'estimated_cost': ['cost', 'value', 'valuation', 'amount', 'job_value', 'project_value', 'estimated'],
    'filing_date': ['date', 'issue_date', 'issued', 'filed', 'created', 'applied', 'permit_date'],
    'description': ['desc', 'description', 'work_desc', 'scope', 'project_desc', 'work_type'],
    'contractor_name': ['contractor', 'applicant', 'builder', 'owner', 'contact', 'company'],
    'status': ['status', 'state', 'permit_status', 'disposition'],
}


def validate_endpoint(city_key):
    """Validate a single city endpoint. Returns validation result dict."""
    config = get_city_config(city_key)
    if not config:
        return {'city': city_key, 'status': 'NO_CONFIG', 'error': 'City config not found'}

    endpoint = config.get('endpoint', '')
    if not endpoint or not endpoint.startswith('http'):
        return {
            'city': city_key,
            'name': config.get('name', city_key),
            'status': 'NO_ENDPOINT',
            'error': 'No valid endpoint URL'
        }

    platform = config.get('platform', 'unknown')
    field_map = config.get('field_map', {})

    result = {
        'city': city_key,
        'name': config.get('name', city_key),
        'platform': platform,
        'endpoint': endpoint[:100] + '...' if len(endpoint) > 100 else endpoint,
        'expected_fields': list(field_map.values())[:10],
    }

    try:
        # Build request based on platform
        if platform == 'socrata':
            params = {'$limit': 5}
        elif platform == 'arcgis':
            params = {'where': '1=1', 'outFields': '*', 'resultRecordCount': 5, 'f': 'json'}
        elif platform == 'ckan':
            params = {'limit': 5}
        else:
            params = {'limit': 5}

        resp = SESSION.get(endpoint, params=params, timeout=TIMEOUT)
        result['http_status'] = resp.status_code

        if resp.status_code != 200:
            result['status'] = 'DEAD_URL'
            result['error'] = f'HTTP {resp.status_code}'
            return result

        # Check content type
        content_type = resp.headers.get('content-type', '')
        if 'json' not in content_type.lower() and 'text/plain' not in content_type.lower():
            result['status'] = 'NOT_JSON'
            result['error'] = f'Content-Type: {content_type[:50]}'
            return result

        # Parse JSON
        try:
            data = resp.json()
        except json.JSONDecodeError as e:
            result['status'] = 'INVALID_JSON'
            result['error'] = str(e)[:100]
            return result

        # Extract records based on platform
        if platform == 'arcgis':
            if 'error' in data:
                result['status'] = 'API_ERROR'
                result['error'] = data['error'].get('message', 'Unknown ArcGIS error')[:100]
                return result
            records = [f.get('attributes', f) for f in data.get('features', [])]
        elif platform == 'ckan':
            if data.get('success') and 'result' in data:
                records = data['result'].get('records', [])
            else:
                records = []
        else:  # socrata and others
            records = data if isinstance(data, list) else []

        result['record_count'] = len(records)

        if len(records) == 0:
            result['status'] = 'EMPTY'
            result['error'] = 'No records returned'
            return result

        # Get actual field names from first record
        actual_fields = list(records[0].keys()) if records else []
        result['actual_fields'] = actual_fields[:20]

        # Check if expected fields exist
        missing_fields = []
        for our_field, api_field in field_map.items():
            if api_field and api_field not in actual_fields:
                # Case-insensitive check
                if not any(f.lower() == api_field.lower() for f in actual_fields):
                    missing_fields.append(f'{our_field}={api_field}')

        if missing_fields:
            result['status'] = 'WRONG_FIELDS'
            result['missing_fields'] = missing_fields
            result['suggested_map'] = auto_map_fields(actual_fields)
            return result

        # Check if we actually get meaningful data
        sample = records[0]
        has_permit_id = any(sample.get(field_map.get(f)) for f in ['permit_number'])
        has_address = any(sample.get(field_map.get(f)) for f in ['address'])

        if has_permit_id or has_address:
            result['status'] = 'WORKING'
            result['sample_permit'] = str(sample.get(field_map.get('permit_number', ''), ''))[:50]
            result['sample_address'] = str(sample.get(field_map.get('address', ''), ''))[:50]
        else:
            result['status'] = 'WORKING_PARTIAL'
            result['note'] = 'Returns data but key fields empty'

        return result

    except requests.exceptions.Timeout:
        result['status'] = 'TIMEOUT'
        result['error'] = f'Request timed out after {TIMEOUT}s'
        return result
    except requests.exceptions.ConnectionError as e:
        result['status'] = 'CONNECTION_ERROR'
        result['error'] = str(e)[:100]
        return result
    except Exception as e:
        result['status'] = 'ERROR'
        result['error'] = f'{type(e).__name__}: {str(e)[:100]}'
        return result


def auto_map_fields(actual_fields):
    """Suggest field mappings based on common patterns."""
    suggested = {}
    actual_lower = {f.lower(): f for f in actual_fields}

    for our_field, patterns in FIELD_PATTERNS.items():
        for pattern in patterns:
            # Check for exact match or substring match
            for field_lower, field_actual in actual_lower.items():
                if pattern in field_lower:
                    suggested[our_field] = field_actual
                    break
            if our_field in suggested:
                break

    return suggested


def discover_socrata_endpoint(city_name, domain_hint=None):
    """Use Socrata Discovery API to find permit datasets for a city."""
    search_url = "https://api.us.socrata.com/api/catalog/v1"

    params = {
        'q': f'building permits {city_name}',
        'limit': 5,
    }
    if domain_hint:
        params['domains'] = domain_hint

    try:
        resp = SESSION.get(search_url, params=params, timeout=15)
        if resp.status_code != 200:
            return None

        data = resp.json()
        results = data.get('results', [])

        for r in results:
            resource = r.get('resource', {})
            name = resource.get('name', '').lower()
            # Look for permit-related datasets
            if 'permit' in name and ('building' in name or 'construction' in name):
                return {
                    'name': resource.get('name'),
                    'id': resource.get('id'),
                    'domain': r.get('metadata', {}).get('domain'),
                    'link': r.get('link'),
                    'columns': resource.get('columns_field_name', [])[:15],
                }

        return None
    except Exception:
        return None


def run_validation(quick=False, discover=False):
    """Run validation on all active cities."""
    active_cities = get_active_cities()
    print(f"Validating {len(active_cities)} active cities...")
    print("=" * 60)

    results = {
        'validated_at': datetime.now().isoformat(),
        'total_cities': len(active_cities),
        'working': [],
        'working_partial': [],
        'wrong_fields': [],
        'empty': [],
        'dead_url': [],
        'timeout': [],
        'error': [],
        'no_endpoint': [],
    }

    # Run validations in parallel
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(validate_endpoint, city): city for city in active_cities}

        for i, future in enumerate(as_completed(futures)):
            city = futures[future]
            try:
                result = future.result()
                status = result.get('status', 'ERROR')

                # Categorize
                if status == 'WORKING':
                    results['working'].append(result)
                    symbol = '✓'
                elif status == 'WORKING_PARTIAL':
                    results['working_partial'].append(result)
                    symbol = '~'
                elif status == 'WRONG_FIELDS':
                    results['wrong_fields'].append(result)
                    symbol = '⚠'
                elif status == 'EMPTY':
                    results['empty'].append(result)
                    symbol = '○'
                elif status in ['DEAD_URL', 'NOT_JSON', 'INVALID_JSON', 'API_ERROR']:
                    results['dead_url'].append(result)
                    symbol = '✗'
                elif status == 'TIMEOUT':
                    results['timeout'].append(result)
                    symbol = '⏱'
                elif status == 'NO_ENDPOINT':
                    results['no_endpoint'].append(result)
                    symbol = '-'
                else:
                    results['error'].append(result)
                    symbol = '!'

                # Progress output
                if not quick or (i + 1) % 50 == 0:
                    print(f"  [{i+1}/{len(active_cities)}] {symbol} {result.get('name', city)}: {status}")

            except Exception as e:
                print(f"  [{i+1}/{len(active_cities)}] ! {city}: Exception - {e}")
                results['error'].append({'city': city, 'status': 'EXCEPTION', 'error': str(e)})

    # Summary
    print("\n" + "=" * 60)
    print("VALIDATION SUMMARY")
    print("=" * 60)
    print(f"  Working:        {len(results['working'])}")
    print(f"  Working (partial): {len(results['working_partial'])}")
    print(f"  Wrong fields:   {len(results['wrong_fields'])}")
    print(f"  Empty:          {len(results['empty'])}")
    print(f"  Dead URL:       {len(results['dead_url'])}")
    print(f"  Timeout:        {len(results['timeout'])}")
    print(f"  No endpoint:    {len(results['no_endpoint'])}")
    print(f"  Other errors:   {len(results['error'])}")

    # Discover new endpoints for dead URLs
    if discover and results['dead_url']:
        print("\n" + "=" * 60)
        print("DISCOVERING REPLACEMENTS FOR DEAD URLS...")
        print("=" * 60)

        results['discoveries'] = []
        for item in results['dead_url'][:20]:  # Limit to 20 to avoid rate limits
            city_name = item.get('name', item.get('city', ''))
            print(f"  Searching for: {city_name}...")
            discovery = discover_socrata_endpoint(city_name)
            if discovery:
                print(f"    Found: {discovery.get('name')}")
                results['discoveries'].append({
                    'city': item.get('city'),
                    'old_endpoint': item.get('endpoint'),
                    'discovery': discovery
                })
            time.sleep(0.5)  # Rate limit

    # Save results
    output_file = os.path.join(DATA_DIR, "endpoint_validation.json")
    with open(output_file, 'w') as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nResults saved to: {output_file}")

    return results


def generate_fixes(results):
    """Generate city_configs.py patches for wrong_fields cities."""
    fixes = []

    for item in results.get('wrong_fields', []):
        city = item.get('city')
        suggested = item.get('suggested_map', {})
        if suggested:
            fixes.append({
                'city': city,
                'action': 'UPDATE_FIELD_MAP',
                'suggested_field_map': suggested,
                'actual_fields': item.get('actual_fields', []),
            })

    for item in results.get('discoveries', []):
        city = item.get('city')
        discovery = item.get('discovery', {})
        if discovery:
            fixes.append({
                'city': city,
                'action': 'UPDATE_ENDPOINT',
                'new_endpoint': discovery.get('link'),
                'new_dataset_id': discovery.get('id'),
                'available_fields': discovery.get('columns', []),
            })

    # Save fixes
    fixes_file = os.path.join(DATA_DIR, "suggested_fixes.json")
    with open(fixes_file, 'w') as f:
        json.dump(fixes, f, indent=2)
    print(f"Suggested fixes saved to: {fixes_file}")

    return fixes


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Validate PermitGrab city endpoints')
    parser.add_argument('--quick', action='store_true', help='Quick mode - less verbose output')
    parser.add_argument('--discover', action='store_true', help='Discover new endpoints for dead URLs')
    parser.add_argument('--fix', action='store_true', help='Generate fix suggestions')
    args = parser.parse_args()

    results = run_validation(quick=args.quick, discover=args.discover)

    if args.fix:
        generate_fixes(results)
