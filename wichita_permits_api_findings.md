# Wichita, Kansas Building Permits API Research

## Date: 2026-03-29

## Summary
After extensive research, **no publicly accessible API with 2025/2026 building permit data was found for Wichita, Kansas**. However, a working HUD API was found that provides residential construction permits data for Sedgwick County (which includes Wichita) through 2022.

---

## WORKING API: HUD Residential Construction Permits (County-Level)

### API Details
- **Type**: ArcGIS REST FeatureServer
- **Endpoint**: `https://services.arcgis.com/VTyQ9soqVukalItT/arcgis/rest/services/Residential_Construction_Permits_by_County/FeatureServer/24`
- **Data Coverage**: 1980 - 2022
- **Geographic Level**: County (Sedgwick County, KS includes Wichita)
- **Update Frequency**: Annual (published in May of following year)
- **Data Type**: Residential construction permits only (final permits, not preliminary)

### Field Names (Sample)
- `NAME` - County name (e.g., "Sedgwick")
- `STATE_NAME` - State name (e.g., "Kansas")
- `STUSAB` - State abbreviation (e.g., "KS")
- `GEOID` - Geographic identifier (e.g., "20173")
- `ALL_PERMITS_[YEAR]` - Total permits for that year
- `SINGLE_FAMILY_PERMITS_[YEAR]` - Single family permits
- `ALL_MULTIFAMILY_PERMITS_[YEAR]` - Multifamily permits
- `MULTIFAMILY_PERMITS_2_UNITS_[YEAR]` - 2-unit permits
- `MULTIFAMILY_PERMITS_3_4_UNITS_[YEAR]` - 3-4 unit permits
- `MULTIFAMILY_PERMITS_5_OR_MORE_UNITS_[YEAR]` - 5+ unit permits

### Sample Query
```bash
curl "https://services.arcgis.com/VTyQ9soqVukalItT/arcgis/rest/services/Residential_Construction_Permits_by_County/FeatureServer/24/query?where=NAME%3D%27Sedgwick%27+AND+STUSAB%3D%27KS%27&outFields=NAME,STATE_NAME,GEOID,ALL_PERMITS_2022,SINGLE_FAMILY_PERMITS_2022,ALL_MULTIFAMILY_PERMITS_2022,ALL_PERMITS_2021,ALL_PERMITS_2020&returnGeometry=false&f=json"
```

### Sample Data (Sedgwick County, KS - Includes Wichita)
```json
{
  "NAME": "Sedgwick",
  "STATE_NAME": "Kansas",
  "GEOID": "20173",
  "ALL_PERMITS_2022": 2590,
  "SINGLE_FAMILY_PERMITS_2022": 1387,
  "ALL_MULTIFAMILY_PERMITS_2022": 1203,
  "ALL_PERMITS_2021": 2124,
  "ALL_PERMITS_2020": 1637
}
```

### Limitations
- ❌ Only residential permits (no commercial building permits)
- ❌ County-level aggregation only (not individual permit records)
- ❌ Data only through 2022 (no 2023, 2024, 2025, or 2026 data)
- ❌ No individual permit details (address, permit number, valuation, etc.)

### Source
- **Dataset**: HUD State of the Cities Data Systems (SOCDS)
- **Provider**: U.S. Department of Housing and Urban Development, U.S. Census Bureau
- **Portal**: https://hudgis-hud.opendata.arcgis.com/datasets/residential-construction-permits-by-county
- **Full Database**: https://socds.huduser.gov/permits/

---

## Other Resources Investigated (No Working APIs Found)

### 1. City of Wichita Open Data Portal
- **URL**: https://data-cityofwichita.hub.arcgis.com/
- **Platform**: ArcGIS Hub
- **Status**: No building permits dataset found
- **Available Data**: Zoning, parcels, flood hazard areas, council districts, etc.
- **Contact**: gis@wichita.gov

### 2. Sedgwick County GIS Services
- **URL**: https://gismaps.sedgwickcounty.org/arcgis/rest/services/
- **Platform**: ArcGIS REST Services
- **Status**: Various map services available, but no building permits FeatureServer found
- **Available Services**: Tax data, parcels, zoning, election districts, census data, etc.
- **Contact**: 316-660-9290

### 3. MABCD Portal (Metropolitan Area Building and Construction Department)
- **URL**: https://mabcdportal.sedgwickcounty.org/
- **Platform**: Web portal (not API)
- **Status**: Interactive web portal for permit lookup, applications, and inspections
- **Functionality**: Search permits by number, applicant name, or address
- **API Access**: None found (web portal only)
- **Contact**: 316-660-1840 or MABCD@sedgwick.gov

### 4. Accela Citizen Access
- **URL**: https://aca-prod.accela.com/WICHITA/
- **Platform**: Accela (permit management system)
- **Status**: Web portal only, no public API access found
- **Note**: Accela does have a developer API (Construct API) but requires developer registration and agency-specific credentials

### 5. Kansas City, MO Open Data (Comparison)
- **URL**: https://data.kcmo.org/
- **Platform**: Socrata/Tyler
- **Status**: Tested but requires authentication
- **Dataset ID**: ue52-x8g8
- **Note**: This is Kansas City, Missouri, not Wichita, Kansas

---

## Recommendations

### For 2025/2026 Data
Since no API with 2025/2026 data was found, consider these alternatives:

1. **Contact MABCD directly** (316-660-1840) to request:
   - Bulk data export of building permits
   - API access to their permit system
   - Data sharing agreement for programmatic access

2. **Monitor HUD's dataset** - Check https://hudgis-hud.opendata.arcgis.com/ for updates
   - Data is typically published in May of the following year
   - 2023 data should be available, 2024 may be coming soon

3. **Census Bureau Building Permits Survey** - https://www.census.gov/construction/bps/
   - More detailed monthly data available
   - May have more recent data than HUD's aggregated dataset

4. **Contact City of Wichita GIS** (gis@wichita.gov) to request:
   - Publishing building permits to their open data portal
   - Creating a FeatureServer for permit data

### For Historical/Residential Data
Use the HUD API documented above for:
- County-level residential permit trends
- Annual residential construction statistics
- Historical analysis (1980-2022)

---

## Technical Notes

### ArcGIS REST API Query Parameters
- `where` - SQL-like WHERE clause for filtering
- `outFields` - Comma-separated list of field names (or * for all)
- `returnGeometry` - true/false to include geometry
- `f` - Format (json, geojson, pjson, etc.)
- `resultRecordCount` - Limit number of results
- `orderByFields` - Sort results

### Example Python Usage
```python
import requests

url = "https://services.arcgis.com/VTyQ9soqVukalItT/arcgis/rest/services/Residential_Construction_Permits_by_County/FeatureServer/24/query"
params = {
    "where": "NAME='Sedgwick' AND STUSAB='KS'",
    "outFields": "NAME,STATE_NAME,ALL_PERMITS_2022,SINGLE_FAMILY_PERMITS_2022",
    "returnGeometry": "false",
    "f": "json"
}
response = requests.get(url, params=params)
data = response.json()
print(data['features'][0]['attributes'])
```

---

## Conclusion

**The only working API found is the HUD Residential Construction Permits by County FeatureServer**, which provides annual residential permit counts for Sedgwick County, Kansas (which includes Wichita) through 2022. This data is aggregated at the county level and does not include individual permit records, commercial permits, or data for 2025/2026.

For more detailed or recent building permit data, direct contact with Sedgwick County MABCD or City of Wichita GIS is recommended to inquire about data export options or API development plans.
