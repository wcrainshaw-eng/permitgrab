# Albuquerque, NM Building Permits API - Working Endpoint

## API Type: ArcGIS REST Services (FeatureServer)

## Working Endpoint URL

**Base Service URL:**
```
https://coageo.cabq.gov/cabqgeo/rest/services/agis/City_Building_Permits/FeatureServer/0
```

**Query Endpoint:**
```
https://coageo.cabq.gov/cabqgeo/rest/services/agis/City_Building_Permits/FeatureServer/0/query
```

## API Status: WORKING ✓

- Connection: Successful
- Authentication: None required (public API)
- Max Records per Request: 50,000
- Supported Output Formats: JSON, GeoJSON, PBF

## Data Availability Issue: No 2025/2026 Data

**Important Note:** The API is functional and working, but the dataset is currently outdated:
- Total Records: 43,661 permits
- Date Range: 2009 to April 12, 2024
- **Latest Data:** April 12, 2024
- **Status:** Does NOT contain 2025 or 2026 data

The City of Albuquerque launched a new permitting system (ABQ-PLAN) in January 2025 to replace the old POSSE system. The public API dataset has not been updated since April 2024, likely due to the system migration. You may need to contact the City's GIS or Planning Department to inquire about when the public API will be updated with 2025+ data.

## Field Names (21 fields)

| Field Name | Type | Description |
|------------|------|-------------|
| OBJECTID | OID | Unique object identifier |
| PermitNumber | String | Permit number (e.g., BP-2024-13415) |
| DateIssued | Date | Date the permit was issued |
| DateEntered | Date | Date the permit was entered into system |
| CalculatedAddress | String | Geocoded address |
| FreeFormAddress | String | Additional address information |
| GeneralCategory | String | Commercial or Residential |
| TypeofWork | String | Type of work (Alterations, New Buildings, etc.) |
| TypeofStructure | String | Structure type (Single Family, Apartment, Commercial, etc.) |
| Valuation | Double | Project valuation in dollars |
| SquareFootage | Double | Square footage of project |
| NumberofUnits | Integer | Number of units (for multi-family) |
| Owner | String | Property owner name |
| Applicant | String | Permit applicant name |
| Contractor | String | Contractor name |
| DataSource | String | Source system (KIVA or POSSE) |
| AGISLandUseUpdate | String | Land use update status |
| WorkDescription | String | Description of work |
| created_date | Date | Record creation date in GIS |
| last_edited_date | Date | Record last edited date in GIS |
| GlobalID | GlobalID | Global unique identifier |

## Sample Data (Most Recent as of April 2024)

### Permit 1
```json
{
  "PermitNumber": "BP-2024-13415",
  "DateIssued": "2024-04-12",
  "CalculatedAddress": "3916 ORTIZ CT NE",
  "FreeFormAddress": "Unit A",
  "GeneralCategory": "Residential",
  "TypeofWork": "Alterations",
  "TypeofStructure": "Apartment",
  "Valuation": 500.0,
  "SquareFootage": 768.0,
  "NumberofUnits": null,
  "Owner": "FIDEL",
  "Applicant": "FIDEL CASTRO",
  "Contractor": "C&L HANDY LLC",
  "DataSource": "POSSE",
  "WorkDescription": "REPAIR DRYWALL INTERIOR DOORS LIKE FOR LIKE LES THAN 15%"
}
```

### Permit 2
```json
{
  "PermitNumber": "BP-2023-50126",
  "DateIssued": "2024-04-12",
  "CalculatedAddress": "2425 RIDGECREST DR SE",
  "GeneralCategory": "Commercial",
  "TypeofWork": "Alterations",
  "TypeofStructure": "Commercial",
  "Valuation": 440864.0,
  "SquareFootage": 55636.0,
  "NumberofUnits": null,
  "Owner": "OSWALDO AMAYA",
  "Applicant": "OSWALDO AMAYA",
  "Contractor": "BRADBURY STAMM CONSTRUCTION INC., ARICHITECT",
  "DataSource": "POSSE",
  "WorkDescription": "COMMERCIAL – DEMOLITION – BUILDINGS: 14 (1ST & 2ND LEVELS) & 14A (1ST LEVEL) / LOVELACE BIOMEDICAL RESEARCH INSTITUTE"
}
```

### Permit 3
```json
{
  "PermitNumber": "BP-2024-13411",
  "DateIssued": "2024-04-12",
  "CalculatedAddress": "6020 PYRENEES CT NW",
  "GeneralCategory": "Residential",
  "TypeofWork": "Alterations",
  "TypeofStructure": "Single Family",
  "Valuation": 400.0,
  "SquareFootage": 2097.0,
  "NumberofUnits": null,
  "Owner": "FIDEL",
  "Applicant": "FIDEL CASTRO",
  "Contractor": "C&L HANDY LLC",
  "DataSource": "POSSE",
  "WorkDescription": "REPLACE CEMENT BOAR 44 SQ FT LESS THEN 15% LIKE FOR LIKE NO PLUMBING ALTERATIONS"
}
```

## Example API Queries

### Get all permits from 2024
```
https://coageo.cabq.gov/cabqgeo/rest/services/agis/City_Building_Permits/FeatureServer/0/query?where=DateIssued>=timestamp'2024-01-01'&outFields=*&f=json
```

### Get residential permits only
```
https://coageo.cabq.gov/cabqgeo/rest/services/agis/City_Building_Permits/FeatureServer/0/query?where=GeneralCategory='Residential'&outFields=*&f=json
```

### Get permits with valuation over $100,000
```
https://coageo.cabq.gov/cabqgeo/rest/services/agis/City_Building_Permits/FeatureServer/0/query?where=Valuation>100000&outFields=*&f=json
```

### Get permits by address (wildcard search)
```
https://coageo.cabq.gov/cabqgeo/rest/services/agis/City_Building_Permits/FeatureServer/0/query?where=CalculatedAddress LIKE '%CENTRAL%'&outFields=*&f=json
```

### Get new building permits only
```
https://coageo.cabq.gov/cabqgeo/rest/services/agis/City_Building_Permits/FeatureServer/0/query?where=TypeofWork='New Buildings'&outFields=*&f=json
```

### Get count of permits in date range
```
https://coageo.cabq.gov/cabqgeo/rest/services/agis/City_Building_Permits/FeatureServer/0/query?where=DateIssued>=timestamp'2023-01-01' AND DateIssued<=timestamp'2023-12-31'&returnCountOnly=true&f=json
```

### Get statistics (max valuation, total permits)
```
https://coageo.cabq.gov/cabqgeo/rest/services/agis/City_Building_Permits/FeatureServer/0/query?where=1=1&outStatistics=[{"statisticType":"max","onStatisticField":"Valuation","outStatisticFieldName":"max_val"},{"statisticType":"count","onStatisticField":"PermitNumber","outStatisticFieldName":"total_permits"}]&f=json
```

## Python Usage Example

```python
import requests
from datetime import datetime

# Base endpoint
BASE_URL = "https://coageo.cabq.gov/cabqgeo/rest/services/agis/City_Building_Permits/FeatureServer/0/query"

# Query parameters
params = {
    'where': "DateIssued>=timestamp'2024-01-01'",
    'outFields': '*',
    'orderByFields': 'DateIssued DESC',
    'resultRecordCount': 10,
    'f': 'json'
}

# Make request
response = requests.get(BASE_URL, params=params)
data = response.json()

# Process results
for feature in data['features']:
    attrs = feature['attributes']
    date_issued = datetime.fromtimestamp(attrs['DateIssued'] / 1000)
    print(f"{attrs['PermitNumber']} - {date_issued.strftime('%Y-%m-%d')} - {attrs['CalculatedAddress']}")
```

## Alternative Data Sources

Since the public API lacks 2025/2026 data, consider these alternatives:

1. **ABQ-PLAN Portal** (https://www.cabq.gov/planning/abq-plan)
   - Web-based permit search system
   - Launched January 2025
   - May have more current data but no public API documented

2. **Building Safety Resource Page** (https://www.cabq.gov/planning/building-safety-division/building-safety-forms-reports-permit-searches)
   - Online permit search tool
   - No API access, web interface only

3. **Contact AGIS Division**
   - AGIS Division, Planning Department, City of Albuquerque
   - They maintain this dataset and may have updated data or API access

4. **Public Records Request**
   - File a formal public records request for current permit data
   - May be provided in CSV/Excel format

## Additional Resources

- ArcGIS Hub: https://hub.arcgis.com/maps/CABQ::city-building-permits/explore
- Open Data Portal: https://data-cabq.opendata.arcgis.com/
- ABQ Data Portal: https://www.cabq.gov/abq-data
- Service Metadata: https://coageo.cabq.gov/cabqgeo/rest/services/agis/City_Building_Permits/FeatureServer/0/metadata

## Test Script

A complete test script has been created at:
```
/Users/wescrainshaw/Documents/PermitGrab/albuquerque_permits_test.py
```

Run it with:
```bash
python3 albuquerque_permits_test.py
```

## Conclusion

The Albuquerque Building Permits ArcGIS REST API is fully functional and accessible, but currently does not contain 2025 or 2026 data. The dataset stops at April 12, 2024, likely due to the city's transition to the new ABQ-PLAN system in January 2025. You will need to contact the city's GIS or Planning Department to inquire about when the public API will be updated with current permit data.
