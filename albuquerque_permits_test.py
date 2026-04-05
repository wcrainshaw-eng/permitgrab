#!/usr/bin/env python3
"""
Albuquerque, NM Building Permits API Test
Tests the ArcGIS REST API endpoint for City of Albuquerque building permits
"""

import requests
import json
from datetime import datetime

# API Configuration
BASE_URL = "https://coageo.cabq.gov/cabqgeo/rest/services/agis/City_Building_Permits/FeatureServer/0"

def test_api_connection():
    """Test basic API connectivity and get service info"""
    print("=" * 80)
    print("ALBUQUERQUE BUILDING PERMITS API TEST")
    print("=" * 80)
    
    url = f"{BASE_URL}?f=json"
    response = requests.get(url)
    
    if response.status_code == 200:
        data = response.json()
        print("\n✓ API Connection: SUCCESS")
        print(f"Service Name: {data.get('name')}")
        print(f"Max Record Count: {data.get('maxRecordCount')}")
        print(f"Supports Statistics: {data.get('supportsStatistics')}")
        return True
    else:
        print(f"\n✗ API Connection: FAILED (Status {response.status_code})")
        return False

def get_field_names():
    """Get all field names from the service"""
    print("\n" + "=" * 80)
    print("FIELD NAMES")
    print("=" * 80)
    
    url = f"{BASE_URL}?f=json"
    response = requests.get(url)
    data = response.json()
    
    fields = data.get('fields', [])
    print(f"\nTotal Fields: {len(fields)}\n")
    
    for field in fields:
        field_type = field['type'].replace('esriFieldType', '')
        print(f"  • {field['name']:<25} ({field_type})")
    
    return [f['name'] for f in fields]

def get_date_range():
    """Get the date range of available data"""
    print("\n" + "=" * 80)
    print("DATA AVAILABILITY")
    print("=" * 80)
    
    # Get max date
    stats_query = {
        'where': '1=1',
        'outStatistics': json.dumps([{
            'statisticType': 'max',
            'onStatisticField': 'DateIssued',
            'outStatisticFieldName': 'max_date'
        }]),
        'f': 'json'
    }
    
    response = requests.get(f"{BASE_URL}/query", params=stats_query)
    data = response.json()
    
    max_timestamp = data['features'][0]['attributes']['max_date']
    max_date = datetime.fromtimestamp(max_timestamp / 1000)
    
    # Get count
    count_query = {'where': '1=1', 'returnCountOnly': 'true', 'f': 'json'}
    response = requests.get(f"{BASE_URL}/query", params=count_query)
    count_data = response.json()
    
    print(f"\nTotal Permits: {count_data['count']:,}")
    print(f"Latest Data: {max_date.strftime('%Y-%m-%d')}")
    print(f"\nNote: Data currently extends from 2009 to April 2024")
    print("      (May not include 2025/2026 data yet)")

def get_sample_data():
    """Get sample permit records"""
    print("\n" + "=" * 80)
    print("SAMPLE DATA (3 Most Recent Permits)")
    print("=" * 80)
    
    query_params = {
        'where': '1=1',
        'outFields': '*',
        'orderByFields': 'DateIssued DESC',
        'resultRecordCount': 3,
        'f': 'json'
    }
    
    response = requests.get(f"{BASE_URL}/query", params=query_params)
    data = response.json()
    
    for i, feature in enumerate(data['features'], 1):
        attrs = feature['attributes']
        date_issued = datetime.fromtimestamp(attrs['DateIssued'] / 1000)
        
        print(f"\n--- Permit {i} ---")
        print(f"Permit Number:    {attrs.get('PermitNumber', 'N/A')}")
        print(f"Date Issued:      {date_issued.strftime('%Y-%m-%d')}")
        print(f"Address:          {attrs.get('CalculatedAddress', 'N/A')}")
        print(f"Type of Work:     {attrs.get('TypeofWork', 'N/A')}")
        print(f"Type of Structure: {attrs.get('TypeofStructure', 'N/A')}")
        print(f"Category:         {attrs.get('GeneralCategory', 'N/A')}")
        print(f"Valuation:        ${attrs.get('Valuation', 0):,.2f}")
        print(f"Square Footage:   {attrs.get('SquareFootage', 'N/A')}")
        print(f"Owner:            {attrs.get('Owner', 'N/A')}")
        print(f"Contractor:       {attrs.get('Contractor', 'N/A')}")
        print(f"Description:      {attrs.get('WorkDescription', 'N/A')[:100]}...")
    
    return data

def example_queries():
    """Show example API query patterns"""
    print("\n" + "=" * 80)
    print("EXAMPLE API QUERIES")
    print("=" * 80)
    
    examples = [
        {
            'title': 'Get all permits from 2024',
            'url': f"{BASE_URL}/query?where=DateIssued>=timestamp'2024-01-01'&outFields=*&f=json"
        },
        {
            'title': 'Get residential permits only',
            'url': f"{BASE_URL}/query?where=GeneralCategory='Residential'&outFields=*&f=json"
        },
        {
            'title': 'Get permits with valuation over $100,000',
            'url': f"{BASE_URL}/query?where=Valuation>100000&outFields=*&f=json"
        },
        {
            'title': 'Get permits by address (wildcard search)',
            'url': f"{BASE_URL}/query?where=CalculatedAddress LIKE '%CENTRAL%'&outFields=*&f=json"
        },
        {
            'title': 'Get new building permits only',
            'url': f"{BASE_URL}/query?where=TypeofWork='New Buildings'&outFields=*&f=json"
        }
    ]
    
    for example in examples:
        print(f"\n{example['title']}:")
        print(f"  {example['url']}")

if __name__ == '__main__':
    try:
        # Run all tests
        if test_api_connection():
            get_field_names()
            get_date_range()
            get_sample_data()
            example_queries()
            
            print("\n" + "=" * 80)
            print("API ENDPOINT SUMMARY")
            print("=" * 80)
            print(f"\nBase URL: {BASE_URL}")
            print(f"Query Endpoint: {BASE_URL}/query")
            print("\nSupported Formats: JSON, GeoJSON, PBF")
            print("Max Records per Request: 50,000")
            print("Authentication: None required (public API)")
            print("\n" + "=" * 80)
    
    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()
