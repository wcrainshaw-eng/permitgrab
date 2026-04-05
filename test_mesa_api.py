#!/usr/bin/env python3
"""
Test script for Mesa, AZ Building Permits API
City of Mesa - SODA API
"""

import requests
import json
from datetime import datetime

# API Configuration
BASE_URL = "https://citydata.mesaaz.gov/resource/dzpk-hxfb.json"
PUBLIC_URL = "https://data.mesaaz.gov/resource/m2kk-w2hz.json"

# Headers to mimic browser request
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
    'Accept': 'application/json',
}

def test_basic_query():
    """Test basic API access"""
    print("Testing basic API access...")
    params = {
        '$limit': 5,
        '$order': 'issued_date DESC'
    }

    try:
        response = requests.get(BASE_URL, params=params, headers=HEADERS, timeout=30)
        print(f"Status Code: {response.status_code}")

        if response.status_code == 200:
            data = response.json()
            print(f"Records returned: {len(data)}")
            if data:
                print("\nSample Record:")
                print(json.dumps(data[0], indent=2))
            return True
        else:
            print(f"Error: {response.text}")
            return False
    except Exception as e:
        print(f"Exception: {e}")
        return False

def test_2026_data():
    """Test for 2026 data availability"""
    print("\n\nTesting for 2026 data...")
    params = {
        '$where': "issued_date >= '2026-01-01'",
        '$limit': 5,
        '$order': 'issued_date DESC'
    }

    try:
        response = requests.get(BASE_URL, params=params, headers=HEADERS, timeout=30)
        print(f"Status Code: {response.status_code}")

        if response.status_code == 200:
            data = response.json()
            print(f"2026 Records found: {len(data)}")
            if data:
                print("\nSample 2026 Record:")
                record = data[0]
                print(f"Permit Number: {record.get('permit_number')}")
                print(f"Issued Date: {record.get('issued_date')}")
                print(f"Address: {record.get('property_address')}")
                print(f"Type: {record.get('permit_type')}")
            return True
        else:
            print(f"Error: {response.text}")
            return False
    except Exception as e:
        print(f"Exception: {e}")
        return False

def test_field_names():
    """Get and display all available field names"""
    print("\n\nGetting field names from actual data...")
    params = {'$limit': 1}

    try:
        response = requests.get(BASE_URL, params=params, headers=HEADERS, timeout=30)
        if response.status_code == 200:
            data = response.json()
            if data:
                fields = list(data[0].keys())
                print(f"Total fields: {len(fields)}")
                print("\nAvailable fields:")
                for field in sorted(fields):
                    print(f"  - {field}")
                return True
        else:
            print(f"Error: {response.text}")
            return False
    except Exception as e:
        print(f"Exception: {e}")
        return False

def test_count():
    """Test getting record count"""
    print("\n\nGetting total record count...")
    # SODA API count endpoint
    count_url = BASE_URL.replace('.json', '')

    try:
        # Try to get count using $select=count(*)
        params = {
            '$select': 'count(*) as count'
        }
        response = requests.get(BASE_URL, params=params, headers=HEADERS, timeout=30)

        if response.status_code == 200:
            data = response.json()
            if data:
                print(f"Total records: {data[0].get('count', 'Unknown')}")
            return True
        else:
            print(f"Count query not supported, status: {response.status_code}")
            return False
    except Exception as e:
        print(f"Exception: {e}")
        return False

def test_public_endpoint():
    """Test the public-facing endpoint"""
    print("\n\nTesting public endpoint...")
    params = {
        '$limit': 3,
        '$order': 'issued_date DESC'
    }

    try:
        response = requests.get(PUBLIC_URL, params=params, headers=HEADERS, timeout=30)
        print(f"Status Code: {response.status_code}")

        if response.status_code == 200:
            data = response.json()
            print(f"Records returned: {len(data)}")
            return True
        else:
            print(f"Error: {response.text}")
            return False
    except Exception as e:
        print(f"Exception: {e}")
        return False

if __name__ == "__main__":
    print("=" * 60)
    print("Mesa, AZ Building Permits API Test")
    print(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    results = {
        'basic_query': test_basic_query(),
        'public_endpoint': test_public_endpoint(),
        '2026_data': test_2026_data(),
        'field_names': test_field_names(),
        'record_count': test_count()
    }

    print("\n" + "=" * 60)
    print("Test Summary:")
    print("=" * 60)
    for test_name, result in results.items():
        status = "PASS" if result else "FAIL"
        print(f"{test_name}: {status}")

    print("\n" + "=" * 60)
    print("API Documentation:")
    print("=" * 60)
    print(f"Base URL: {BASE_URL}")
    print(f"Public URL: {PUBLIC_URL}")
    print("SODA API Docs: https://dev.socrata.com/")
    print("\nExample queries:")
    print("  - All records (limited): ?$limit=1000")
    print("  - Recent permits: ?$order=issued_date DESC&$limit=100")
    print("  - Filter by year: ?$where=issued_year=2026")
    print("  - Filter by type: ?permit_type=Residential")
    print("  - Date range: ?$where=issued_date>='2026-01-01' AND issued_date<='2026-03-31'")
