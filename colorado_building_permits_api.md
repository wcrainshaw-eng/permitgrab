# Colorado Building Permits API - Working Endpoint

## Summary
**WORKING API FOUND**: Aurora, Colorado (neighboring city to Colorado Springs)
- **API Type**: ArcGIS REST Services (MapServer)
- **2025-2026 Data**: CONFIRMED (23,212 records)
- **Most Recent Data**: March 27, 2026
- **Status**: Fully functional and tested

## Aurora, Colorado Building Permits API

### Base Endpoint
```
https://ags.auroragov.org/aurora/rest/services/OpenData/MapServer
```

### Building Permits Layer
- **Layer ID**: 44
- **Layer Name**: Building Permits
- **Geometry Type**: Point (esriGeometryPoint)
- **Spatial Reference**: WKID 102654 (NAD 1983 StatePlane Colorado Central FIPS 0502 Feet)
- **Max Record Count**: 2000 records per query

### Full Layer Metadata Endpoint
```
https://ags.auroragov.org/aurora/rest/services/OpenData/MapServer/44?f=json
```

### Query Endpoint
```
https://ags.auroragov.org/aurora/rest/services/OpenData/MapServer/44/query
```

## Field Names and Types

| Field Name | Type | Length | Description |
|------------|------|--------|-------------|
| OBJECTID | esriFieldTypeOID | - | Object ID (unique) |
| FolderRSN | esriFieldTypeInteger | - | Folder reference number |
| Permit_ | esriFieldTypeString | 18 | Permit number (e.g., "26-2614704-000-00") |
| InDate | esriFieldTypeDate | 8 | Intake/Application date (epoch milliseconds) |
| FolderType | esriFieldTypeString | 4 | Folder type code |
| FolderDesc | esriFieldTypeString | 80 | Folder description (e.g., "Counter Permit") |
| FolderGroupDesc | esriFieldTypeString | 80 | Folder group description (e.g., "Building") |
| SubDesc | esriFieldTypeString | 80 | Sub-description/permit type (e.g., "Roofing-RT2") |
| FolderDescription | esriFieldTypeString | large | Detailed work description |
| FolderCondition | esriFieldTypeString | large | Permit conditions and requirements |
| IssueDate | esriFieldTypeDate | 8 | Date permit was issued (epoch milliseconds) |
| valuation | esriFieldTypeString | 2000 | Project valuation in dollars |
| PropertyRSN | esriFieldTypeInteger | - | Property reference number |
| PropX | esriFieldTypeDouble | - | X coordinate (State Plane) |
| PropY | esriFieldTypeDouble | - | Y coordinate (State Plane) |
| Address | esriFieldTypeString | 91 | Property address |
| GlobalID | esriFieldTypeGlobalID | 38 | Global unique identifier (GUID) |
| PropertyRoll | esriFieldTypeString | 50 | Property roll number |

## Sample Queries

### Query 1: Recent 2026 Permits (Last 5)
```bash
curl -s -G "https://ags.auroragov.org/aurora/rest/services/OpenData/MapServer/44/query" \
  --data-urlencode "where=IssueDate >= timestamp '2026-01-01 00:00:00'" \
  --data-urlencode "outFields=Permit_,IssueDate,FolderDesc,SubDesc,valuation,Address" \
  --data-urlencode "returnGeometry=false" \
  --data-urlencode "resultRecordCount=5" \
  --data-urlencode "orderByFields=IssueDate DESC" \
  --data-urlencode "f=json"
```

### Query 2: All Fields for Recent Permits
```bash
curl -s -G "https://ags.auroragov.org/aurora/rest/services/OpenData/MapServer/44/query" \
  --data-urlencode "where=IssueDate >= timestamp '2025-01-01 00:00:00'" \
  --data-urlencode "outFields=*" \
  --data-urlencode "returnGeometry=true" \
  --data-urlencode "resultRecordCount=10" \
  --data-urlencode "orderByFields=IssueDate DESC" \
  --data-urlencode "f=json"
```

### Query 3: Count Total 2025-2026 Permits
```bash
curl -s -G "https://ags.auroragov.org/aurora/rest/services/OpenData/MapServer/44/query" \
  --data-urlencode "where=IssueDate >= timestamp '2025-01-01 00:00:00' AND IssueDate <= timestamp '2026-12-31 23:59:59'" \
  --data-urlencode "returnCountOnly=true" \
  --data-urlencode "f=json"
```

### Query 4: Permits by Type (e.g., Roofing)
```bash
curl -s -G "https://ags.auroragov.org/aurora/rest/services/OpenData/MapServer/44/query" \
  --data-urlencode "where=SubDesc LIKE '%Roofing%' AND IssueDate >= timestamp '2026-01-01 00:00:00'" \
  --data-urlencode "outFields=Permit_,IssueDate,SubDesc,valuation,Address" \
  --data-urlencode "returnGeometry=false" \
  --data-urlencode "resultRecordCount=10" \
  --data-urlencode "f=json"
```

## Sample 2026 Data (Tested March 27, 2026)

```json
{
  "Permit": "26-2614704-000-00",
  "Issued": "2026-03-27 11:10:14",
  "Type": "Counter Permit - Roofing-RT2",
  "Valuation": "$7000",
  "Address": "19541 E LASALLE PL"
}

{
  "Permit": "26-2614303-000-00",
  "Issued": "2026-03-27 10:44:49",
  "Type": "Counter Permit - Mechanical Permit",
  "Valuation": "$21400.00",
  "Address": "3838 S FRASER ST"
}

{
  "Permit": "26-2614073-000-00",
  "Issued": "2026-03-27 10:41:12",
  "Type": "Public Improvement Permit - Private Development",
  "Valuation": "$2000",
  "Address": "15892 E ALAMEDA PKWY"
}
```

## URL-Encoded Query Examples

### Browser-friendly URL (recent 5 permits):
```
https://ags.auroragov.org/aurora/rest/services/OpenData/MapServer/44/query?where=IssueDate%20%3E%3D%20timestamp%20%272026-01-01%2000%3A00%3A00%27&outFields=Permit_%2CIssueDate%2CFolderDesc%2CSubDesc%2Cvaluation%2CAddress&returnGeometry=false&resultRecordCount=5&orderByFields=IssueDate%20DESC&f=json
```

## Query Parameters Reference

| Parameter | Description | Example Values |
|-----------|-------------|----------------|
| where | SQL WHERE clause | `IssueDate >= timestamp '2026-01-01 00:00:00'` |
| outFields | Comma-separated field list | `Permit_,Address` or `*` for all |
| returnGeometry | Include spatial coordinates | `true` or `false` |
| returnCountOnly | Return only count of records | `true` or `false` |
| resultRecordCount | Max records to return | `5`, `100`, `2000` (max) |
| orderByFields | Sort order | `IssueDate DESC`, `Address ASC` |
| f | Output format | `json`, `geojson`, `pjson` |

## Date Field Format
- Dates are stored as epoch time in milliseconds
- Example: `1774627814000` = March 27, 2026 11:10:14
- Query format: `timestamp 'YYYY-MM-DD HH:MM:SS'`

## Additional Resources

### Aurora GIS Open Data Portal
- **Portal**: https://data-auroraco.opendata.arcgis.com/
- **Building Permits (1 Month)**: https://data-auroraco.opendata.arcgis.com/datasets/AuroraCo::building-permits-1-month
- **Building Permits (6 Months)**: https://data-auroraco.opendata.arcgis.com/datasets/building-permits-6-months

### Other Aurora Layers in OpenData MapServer
- Layer 6: Certificate of Occupancy
- Layer 156: Building Permits 6 Months
- Layer 157: Building Permits 1 Month
- Full list: https://ags.auroragov.org/aurora/rest/services/OpenData/MapServer?f=json

## Colorado Springs Status

### Current Situation
Colorado Springs building permits are managed by the Pikes Peak Regional Building Department (PPRBD) and use the Accela system. While they have an ArcGIS REST services endpoint, the building permits are not publicly exposed via a queryable API endpoint.

### Colorado Springs Resources
- **Accela Portal**: https://aca-prod.accela.com/COSPRINGS/Default.aspx
- **PPRBD Website**: https://www.pprbd.org/
- **GIS Services**: https://gis.coloradosprings.gov/arcgis/rest/services
- **Accela Folder**: https://gis.coloradosprings.gov/arcgis/rest/services/Accela (contains only address/parcel layers)

### 2025 Colorado Springs Building Statistics
- 2,811 single-family home permits (down from 2,854 in 2024)
- 400 commercial project permits (31% increase from 2024)
- 2,700+ apartment units permitted
- $3.7 billion in total construction (8% increase over 2024)

## Alternative Data Sources

### 1. State-level Socrata API (Historical Data Only)
- **Endpoint**: https://data.colorado.gov/resource/v4as-sthd.json
- **Data Range**: 2010-2022 (aggregated counts only, not individual permits)
- **Status**: No 2025-2026 data available

### 2. Federal Reserve Economic Data (FRED)
- **New Housing Units (Colorado Springs MSA)**: https://fred.stlouisfed.org/series/COLO808BPPRIV
- **Status**: Aggregated statistics, not detailed permit records

## Testing and Verification

### Status: CONFIRMED WORKING
- Tested: March 29, 2026
- Total 2025-2026 records: 23,212
- Most recent permit: March 27, 2026
- API Response: Successful (200 OK)
- Data Quality: Complete with all fields populated

### Recommended Use
This Aurora, Colorado API is suitable for:
- Real-time building permit tracking
- Construction market analysis
- Residential and commercial development monitoring
- Integration with permit tracking applications
- Regional Colorado construction data (Aurora is ~70 miles from Colorado Springs)
