#!/usr/bin/env python3
"""
V73: Comprehensive city endpoint diagnosis.
Tests each active city's endpoint and categorizes issues.
"""

import requests
import json
import time
import sys
from datetime import datetime
from city_configs import CITY_REGISTRY

TIMEOUT = 15

def test_socrata(key, config):
    """Test Socrata endpoint."""
    endpoint = config.get('endpoint', '')
    date_field = config.get('date_field', '')

    if not endpoint:
        return 'DEAD', 'No endpoint configured', None

    try:
        # Socrata SODA API format
        url = f"{endpoint}?$limit=1"
        resp = requests.get(url, timeout=TIMEOUT)

        if resp.status_code == 404:
            return 'DEAD', '404 Not Found - endpoint moved or deleted', None
        elif resp.status_code == 403:
            return 'DEAD', '403 Forbidden - access denied', None
        elif resp.status_code != 200:
            return 'DEAD', f'HTTP {resp.status_code}', None

        try:
            data = resp.json()
        except:
            return 'DEAD', 'Invalid JSON response', None

        if not data or len(data) == 0:
            return 'DEAD', 'Empty dataset - no records returned', None

        sample = data[0]
        fields = list(sample.keys())

        # Check date_field
        if date_field and date_field not in sample:
            return 'FIXABLE', f'date_field "{date_field}" not in response. Available: {fields[:8]}', fields

        return 'WORKING', f'{len(fields)} fields, has data', fields

    except requests.exceptions.Timeout:
        return 'DEAD', 'Timeout (>15s)', None
    except requests.exceptions.ConnectionError as e:
        return 'DEAD', f'Connection error: {str(e)[:50]}', None
    except Exception as e:
        return 'DEAD', f'Error: {str(e)[:50]}', None


def test_arcgis(key, config):
    """Test ArcGIS REST endpoint."""
    endpoint = config.get('endpoint', '')

    if not endpoint:
        return 'DEAD', 'No endpoint configured', None

    try:
        # Build proper ArcGIS query URL
        if '/query' in endpoint:
            url = endpoint
            if '?' not in url:
                url += '?where=1=1&outFields=*&resultRecordCount=1&f=json'
            elif 'f=json' not in url:
                url += '&f=json'
        else:
            url = f"{endpoint}/query?where=1=1&outFields=*&resultRecordCount=1&f=json"

        resp = requests.get(url, timeout=TIMEOUT)

        if resp.status_code != 200:
            return 'DEAD', f'HTTP {resp.status_code}', None

        try:
            data = resp.json()
        except:
            return 'DEAD', 'Invalid JSON response', None

        if 'error' in data:
            err_msg = data['error'].get('message', str(data['error']))[:60]
            return 'DEAD', f'API error: {err_msg}', None

        if 'features' not in data:
            return 'DEAD', 'No features array in response', None

        if len(data['features']) == 0:
            return 'DEAD', 'Empty features - no records', None

        # Get field names
        fields = [f['name'] for f in data.get('fields', [])]
        if not fields and data['features']:
            fields = list(data['features'][0].get('attributes', {}).keys())

        return 'WORKING', f'{len(fields)} fields, has data', fields

    except requests.exceptions.Timeout:
        return 'DEAD', 'Timeout (>15s)', None
    except Exception as e:
        return 'DEAD', f'Error: {str(e)[:50]}', None


def test_ckan(key, config):
    """Test CKAN datastore endpoint."""
    endpoint = config.get('endpoint', '')
    dataset_id = config.get('dataset_id', '')

    if not endpoint:
        return 'DEAD', 'No endpoint configured', None

    try:
        # CKAN datastore_search requires resource_id
        if 'resource_id=' not in endpoint and dataset_id:
            url = f"{endpoint}?resource_id={dataset_id}&limit=1"
        else:
            url = f"{endpoint}?limit=1" if '?' not in endpoint else endpoint

        resp = requests.get(url, timeout=TIMEOUT)

        if resp.status_code != 200:
            return 'DEAD', f'HTTP {resp.status_code}', None

        try:
            data = resp.json()
        except:
            return 'DEAD', 'Invalid JSON response', None

        if not data.get('success', False):
            err = data.get('error', {})
            return 'DEAD', f'CKAN error: {err}', None

        records = data.get('result', {}).get('records', [])
        if not records:
            return 'DEAD', 'Empty records', None

        fields = list(records[0].keys())
        return 'WORKING', f'{len(fields)} fields, has data', fields

    except requests.exceptions.Timeout:
        return 'DEAD', 'Timeout (>15s)', None
    except Exception as e:
        return 'DEAD', f'Error: {str(e)[:50]}', None


def test_accela(key, config):
    """Accela needs Playwright - just verify config exists."""
    accela_key = config.get('_accela_city_key', '')
    endpoint = config.get('endpoint', '')

    if not accela_key and not endpoint:
        return 'FIXABLE', 'Missing _accela_city_key and endpoint', None

    # Try to verify the portal is reachable
    if endpoint:
        try:
            # Just check if the domain responds
            from urllib.parse import urlparse
            parsed = urlparse(endpoint)
            base = f"{parsed.scheme}://{parsed.netloc}"
            resp = requests.get(base, timeout=10, allow_redirects=True)
            if resp.status_code < 400:
                return 'WORKING', f'Portal reachable (Playwright scraper)', None
            else:
                return 'DEAD', f'Portal returned HTTP {resp.status_code}', None
        except:
            return 'WORKING', 'Accela config present (untestable via API)', None

    return 'WORKING', 'Accela config present', None


def main():
    print("=" * 70)
    print(f"V73 City Endpoint Diagnosis - {datetime.now()}")
    print("=" * 70)

    active = [(k, v) for k, v in CITY_REGISTRY.items() if v.get('active', False)]
    total = len(active)

    print(f"\nTesting {total} active cities...\n")

    results = {'WORKING': [], 'FIXABLE': [], 'DEAD': []}

    for i, (key, config) in enumerate(active):
        platform = config.get('platform', 'unknown')
        name = config.get('name', key)
        state = config.get('state', '')

        # Test based on platform
        if platform == 'socrata':
            status, detail, fields = test_socrata(key, config)
        elif platform == 'arcgis':
            status, detail, fields = test_arcgis(key, config)
        elif platform == 'ckan':
            status, detail, fields = test_ckan(key, config)
        elif platform == 'accela':
            status, detail, fields = test_accela(key, config)
        else:
            status, detail, fields = 'DEAD', f'Unknown platform: {platform}', None

        results[status].append({
            'key': key,
            'name': name,
            'state': state,
            'platform': platform,
            'detail': detail,
            'fields': fields,
            'endpoint': config.get('endpoint', '')[:80]
        })

        # Progress
        if (i + 1) % 100 == 0:
            print(f"  Progress: {i+1}/{total} ({len(results['WORKING'])} working, {len(results['FIXABLE'])} fixable, {len(results['DEAD'])} dead)")

        time.sleep(0.05)  # Be nice to APIs

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"WORKING: {len(results['WORKING'])}")
    print(f"FIXABLE: {len(results['FIXABLE'])}")
    print(f"DEAD:    {len(results['DEAD'])}")

    # Show DEAD cities
    if results['DEAD']:
        print("\n" + "=" * 70)
        print(f"DEAD CITIES ({len(results['DEAD'])}) - Need investigation")
        print("=" * 70)
        for item in sorted(results['DEAD'], key=lambda x: x['platform']):
            print(f"  [{item['platform']:8}] {item['key']}: {item['detail']}")

    # Show FIXABLE cities
    if results['FIXABLE']:
        print("\n" + "=" * 70)
        print(f"FIXABLE CITIES ({len(results['FIXABLE'])}) - Config issues")
        print("=" * 70)
        for item in sorted(results['FIXABLE'], key=lambda x: x['platform']):
            print(f"  [{item['platform']:8}] {item['key']}: {item['detail']}")

    # Save detailed results
    with open('city_diagnosis.json', 'w') as f:
        json.dump(results, f, indent=2)

    print(f"\nFull results saved to city_diagnosis.json")

    return results


if __name__ == '__main__':
    main()
