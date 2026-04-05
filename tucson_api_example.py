"""
Tucson/Pima County Building Permit API Example
Demonstrates how to fetch and process permit data from City of Tucson ArcGIS REST services
"""

import requests
import json
from datetime import datetime
from typing import List, Dict, Optional

class TucsonPermitAPI:
    """Client for accessing Tucson building permit data"""

    BASE_URL = "https://mapdata.tucsonaz.gov/arcgis/rest/services/PublicMaps/PermitsCode/MapServer"

    # Layer IDs
    COMMERCIAL_LAYER = 81
    RESIDENTIAL_LAYER = 85
    MULTIFAMILY_LAYER = 84

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'PermitGrab/1.0'
        })

    def query_permits(
        self,
        layer_id: int,
        where_clause: str = "1=1",
        out_fields: str = "*",
        return_geometry: bool = True,
        order_by: Optional[str] = None,
        limit: Optional[int] = None
    ) -> Dict:
        """
        Query permits from a specific layer

        Args:
            layer_id: Layer ID (81=Commercial, 85=Residential, 84=Multi-Family)
            where_clause: SQL WHERE clause (e.g., "APPLYDATE >= date'2025-01-01'")
            out_fields: Comma-separated field list or "*" for all
            return_geometry: Include geometry in response
            order_by: Field to sort by (e.g., "APPLYDATE DESC")
            limit: Maximum number of records to return

        Returns:
            Dict containing API response with features array
        """
        url = f"{self.BASE_URL}/{layer_id}/query"

        params = {
            'where': where_clause,
            'outFields': out_fields,
            'returnGeometry': 'true' if return_geometry else 'false',
            'f': 'json'
        }

        if order_by:
            params['orderByFields'] = order_by

        if limit:
            params['resultRecordCount'] = limit

        response = self.session.get(url, params=params)
        response.raise_for_status()

        return response.json()

    def get_recent_permits(
        self,
        layer_id: int,
        days: int = 30,
        limit: Optional[int] = None
    ) -> List[Dict]:
        """
        Get permits from the last N days

        Args:
            layer_id: Layer ID
            days: Number of days to look back
            limit: Maximum number of records

        Returns:
            List of permit records
        """
        # Calculate date N days ago
        from datetime import timedelta
        cutoff_date = datetime.now() - timedelta(days=days)
        date_str = cutoff_date.strftime('%Y-%m-%d')

        where_clause = f"APPLYDATE >= date'{date_str}'"

        result = self.query_permits(
            layer_id=layer_id,
            where_clause=where_clause,
            order_by="APPLYDATE DESC",
            limit=limit
        )

        return result.get('features', [])

    def get_permit_count(self, layer_id: int, where_clause: str = "1=1") -> int:
        """
        Get count of permits matching criteria

        Args:
            layer_id: Layer ID
            where_clause: SQL WHERE clause

        Returns:
            Count of matching records
        """
        url = f"{self.BASE_URL}/{layer_id}/query"

        params = {
            'where': where_clause,
            'returnCountOnly': 'true',
            'f': 'json'
        }

        response = self.session.get(url, params=params)
        response.raise_for_status()

        return response.json().get('count', 0)

    @staticmethod
    def convert_timestamp(ms_timestamp: int) -> str:
        """Convert UNIX millisecond timestamp to ISO date string"""
        if ms_timestamp:
            return datetime.fromtimestamp(ms_timestamp / 1000).strftime('%Y-%m-%d')
        return None

    @staticmethod
    def parse_permit(feature: Dict) -> Dict:
        """
        Parse permit feature into clean dictionary

        Args:
            feature: Feature object from API response

        Returns:
            Cleaned permit data dictionary
        """
        attrs = feature.get('attributes', {})
        geometry = feature.get('geometry', {})

        # Convert timestamps
        apply_date = TucsonPermitAPI.convert_timestamp(attrs.get('APPLYDATE'))
        issue_date = TucsonPermitAPI.convert_timestamp(attrs.get('ISSUEDATE'))
        expire_date = TucsonPermitAPI.convert_timestamp(attrs.get('EXPIREDATE'))
        complete_date = TucsonPermitAPI.convert_timestamp(attrs.get('COMPLETEDATE'))

        return {
            'permit_number': attrs.get('NUMBER'),
            'address': attrs.get('ADDRESS'),
            'unit_or_suite': attrs.get('UNITORSUITE'),
            'parcel': attrs.get('PARCEL'),
            'status': attrs.get('STATUS'),
            'type': attrs.get('TYPE'),
            'work_class': attrs.get('WORKCLASS'),
            'structure_type': attrs.get('StructureType'),
            'apply_date': apply_date,
            'issue_date': issue_date,
            'expire_date': expire_date,
            'complete_date': complete_date,
            'value': attrs.get('VALUE'),
            'square_feet': attrs.get('SQUAREFEET'),
            'project_name': attrs.get('PROJECTNAME'),
            'description': attrs.get('DESCRIPTION'),
            'latitude': attrs.get('LAT'),
            'longitude': attrs.get('LON'),
            'ward': attrs.get('WARD'),
            'css_url': attrs.get('CSS_URL'),
            'pro_url': attrs.get('PRO_URL'),
            'active': attrs.get('ACTIVE')
        }


def main():
    """Example usage"""

    api = TucsonPermitAPI()

    # Example 1: Get count of 2025+ residential permits
    print("Example 1: Count 2025+ residential permits")
    count = api.get_permit_count(
        layer_id=api.RESIDENTIAL_LAYER,
        where_clause="APPLYDATE >= date'2025-01-01'"
    )
    print(f"Total residential permits from 2025+: {count}\n")

    # Example 2: Get recent commercial permits
    print("Example 2: Get last 5 commercial permits")
    recent = api.get_recent_permits(
        layer_id=api.COMMERCIAL_LAYER,
        days=90,
        limit=5
    )

    for feature in recent:
        permit = api.parse_permit(feature)
        print(f"Permit: {permit['permit_number']}")
        print(f"  Address: {permit['address']}")
        print(f"  Status: {permit['status']}")
        print(f"  Applied: {permit['apply_date']}")
        print(f"  Value: ${permit['value']:,.2f}")
        print(f"  Description: {permit['description'][:100]}...")
        print()

    # Example 3: Get high-value permits
    print("\nExample 3: Get high-value permits (>$1M)")
    result = api.query_permits(
        layer_id=api.COMMERCIAL_LAYER,
        where_clause="VALUE > 1000000 AND APPLYDATE >= date'2025-01-01'",
        order_by="VALUE DESC",
        limit=3
    )

    for feature in result.get('features', []):
        permit = api.parse_permit(feature)
        print(f"Permit: {permit['permit_number']}")
        print(f"  Address: {permit['address']}")
        print(f"  Value: ${permit['value']:,.2f}")
        print(f"  Type: {permit['type']}")
        print()

    # Example 4: Search by address
    print("\nExample 4: Search by address pattern")
    result = api.query_permits(
        layer_id=api.RESIDENTIAL_LAYER,
        where_clause="ADDRESS LIKE '%E BROADWAY%' AND APPLYDATE >= date'2025-01-01'",
        limit=3
    )

    print(f"Found {len(result.get('features', []))} permits on E BROADWAY")
    for feature in result.get('features', []):
        permit = api.parse_permit(feature)
        print(f"  - {permit['permit_number']}: {permit['address']}")

    # Example 5: Get 2026 data specifically
    print("\n\nExample 5: Verify 2026 data availability")
    count_2026 = api.get_permit_count(
        layer_id=api.RESIDENTIAL_LAYER,
        where_clause="APPLYDATE >= date'2026-01-01'"
    )
    print(f"Total 2026 residential permits: {count_2026}")

    if count_2026 > 0:
        result = api.query_permits(
            layer_id=api.RESIDENTIAL_LAYER,
            where_clause="APPLYDATE >= date'2026-01-01'",
            order_by="APPLYDATE DESC",
            limit=3
        )

        print("Recent 2026 permits:")
        for feature in result.get('features', []):
            permit = api.parse_permit(feature)
            print(f"  - {permit['permit_number']} ({permit['apply_date']}): {permit['address']}")


if __name__ == '__main__':
    main()
