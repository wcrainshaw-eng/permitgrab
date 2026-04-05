# LA County Bulk Permit Data API

## Working API Endpoint

**Base URL:**
```
https://services.arcgis.com/RmCCgQtiZLDCtblq/arcgis/rest/services/EPIC-LA_Case_History_view/FeatureServer/0
```

## Verification Status

- **Data Currency:** VERIFIED - Contains 2025 and 2026 data (tested March 29, 2026)
- **Latest Permits:** March 26, 2026
- **API Status:** WORKING - Successfully tested with multiple queries
- **Data Format:** ArcGIS FeatureServer (JSON)

## Coverage

### Geographic Areas
This API covers **LA County EPIC-LA (Electronic Permitting and Inspections)** cases including:

**Primary Coverage:**
- Unincorporated Los Angeles County areas
- Multiple county supervisorial districts (SD-1, SD-2, SD-5, etc.)

**Unincorporated Communities Include:**
- Hacienda Heights
- La Crescenta
- Altadena
- Ladera Heights
- East Los Angeles
- And 120+ other unincorporated areas

**Also Includes Some Contract Cities:**
- Santa Clarita (verified in data)
- Torrance (verified in data)
- Pomona (verified in data)
- Long Beach (verified in data)
- And approximately 15 other participating incorporated cities

**Note:** Major cities like Glendale use their own separate permitting systems and are NOT included in this dataset.

### Permit Types
- Building Permits (Residential & Commercial)
- Electrical Permits
- Mechanical Permits (HVAC, Plumbing)
- Site Plan Reviews
- Additions/Alterations
- New Construction
- Repairs/Replacements

## Field Names (39 Total)

### Core Identification Fields
- `OBJECTID` - Unique object identifier
- `CASENUMBER` - Permit case number (e.g., "UNC-BLDR260326003120")
- `CASENAME` - Case name
- `PROJECTNAME` - Project name
- `PROJECT_NAME` - Project name (alternate)
- `MODULENAME` - Module name (typically "PermitManagement")

### Date Fields
- `APPLY_DATE` - Application date (Unix timestamp)
- `ISSUANCE_DATE` - Permit issuance date
- `COMPLETE_DATE` - Completion date
- `EXPIRE_DATE` - Expiration date
- `LAST_INSPECTION_DATE` - Last inspection date

### Status & Classification
- `STATUS` - Permit status (e.g., "Issued", "New", "Finaled", "Approved")
- `WORKCLASS_NAME` - Work classification (e.g., "Simple", "Addition/Alteration", "New")
- `DESCRIPTION` - Permit description
- `SPATIALTYPE` - Spatial type

### Location Fields
- `MAIN_ADDRESS` - Full address (up to 2,000 chars)
- `MAIN_AIN` - Assessor Identification Number (parcel ID)
- `DISTRICT_DISPLAY` - Display name for district
- `SUP_DIST` - Supervisorial District (e.g., "SD-1", "SD-5")
- `COUNTYWIDE_STAT_AREA` - Statistical area code
- `PW_ADDRESS_TYPE` - Address type

### Project Details
- `PERMIT_VALUATION` - Valuation amount (double)
- `NEW_DWELLING_UNITS` - Number of new dwelling units (integer)
- `AFFORDABLE_HOUSING` - Affordable housing flag (0 or 1)
- `ACCESSORY_DWELLING_UNIT` - ADU flag (0 or 1)
- `JUNIOR_ADU` - Junior ADU indicator
- `STYLE_CATEGORY` - Style category
- `STRUCT_TYPE_DISP` - Structure type display

### Disaster Recovery Fields
- `DISASTER_LOSS` - Disaster loss indicator
- `DISASTER_TYPE` - Type of disaster
- `REBUILD_PROGRESS` - Rebuild progress status
- `REBUILD_APP_RECEIVED` - Rebuild application received
- `ZONING_REV_CLEARED` - Zoning review cleared
- `BUILD_PLAN_REV_PROC` - Building plan review process
- `BUILD_PLAN_APPROVED` - Building plan approved
- `BUILD_PERMIT_ISSUED` - Building permit issued
- `REBUILD_IN_CONS` - Rebuild in construction
- `CONS_COMPLETED` - Construction completed

### Statistical Fields
- `STAT_CLASS` - Statistical classification

## Example API Queries

### 1. Get Recent 2026 Permits
```
https://services.arcgis.com/RmCCgQtiZLDCtblq/arcgis/rest/services/EPIC-LA_Case_History_view/FeatureServer/0/query?where=APPLY_DATE>='2026-01-01'&outFields=*&returnGeometry=false&resultRecordCount=100&orderByFields=APPLY_DATE DESC&f=json
```

### 2. Get Permits by Date Range
```
https://services.arcgis.com/RmCCgQtiZLDCtblq/arcgis/rest/services/EPIC-LA_Case_History_view/FeatureServer/0/query?where=APPLY_DATE BETWEEN '2025-01-01' AND '2025-12-31'&outFields=*&returnGeometry=false&f=json
```

### 3. Get Permits with High Valuation
```
https://services.arcgis.com/RmCCgQtiZLDCtblq/arcgis/rest/services/EPIC-LA_Case_History_view/FeatureServer/0/query?where=PERMIT_VALUATION>100000&outFields=CASENUMBER,MAIN_ADDRESS,PERMIT_VALUATION,APPLY_DATE&returnGeometry=false&resultRecordCount=100&f=json
```

### 4. Get New Construction Only
```
https://services.arcgis.com/RmCCgQtiZLDCtblq/arcgis/rest/services/EPIC-LA_Case_History_view/FeatureServer/0/query?where=WORKCLASS_NAME='New'&outFields=*&returnGeometry=false&f=json
```

### 5. Get Permits by Supervisorial District
```
https://services.arcgis.com/RmCCgQtiZLDCtblq/arcgis/rest/services/EPIC-LA_Case_History_view/FeatureServer/0/query?where=SUP_DIST='SD-5'&outFields=*&returnGeometry=false&f=json
```

### 6. Get All Fields Metadata
```
https://services.arcgis.com/RmCCgQtiZLDCtblq/arcgis/rest/services/EPIC-LA_Case_History_view/FeatureServer/0?f=json
```

## Query Parameters

Standard ArcGIS REST API parameters:
- `where` - SQL WHERE clause for filtering
- `outFields` - Comma-separated field names or `*` for all fields
- `returnGeometry` - `true` or `false` (use false for faster queries)
- `resultRecordCount` - Limit number of results (default/max may vary)
- `orderByFields` - Field name(s) with `ASC` or `DESC`
- `f` - Output format (`json`, `geojson`, `pjson`)

## Data Notes

1. **Date Format:** Dates are returned as Unix timestamps (milliseconds since epoch)
2. **Transfer Limit:** Large queries may have `exceededTransferLimit: true`, indicating more records exist
3. **Spatial Reference:** WKID 102645 / 2229 (California State Plane, Zone V, NAD83, US Feet)
4. **Geometry:** Point features (typically parcel centroids)
5. **Update Frequency:** Refreshed on business days
6. **Coverage Limitations:** Primarily unincorporated LA County + some contract cities (NOT all 88 incorporated cities)

## Additional Resources

- **EPIC-LA Portal:** https://epicla.lacounty.gov/
- **EPIC-LA Permit Finder:** https://experience.arcgis.com/experience/bae937de066d46e28d9259e81fddce34
- **LA County Open Data:** https://data.lacounty.gov/
- **LA County Enterprise GIS:** https://egis-lacounty.hub.arcgis.com/
- **Building Permit Viewer:** https://apps.gis.lacounty.gov/dpw/m/?viewer=bpv_wf5

## Alternative Data Sources

For comprehensive coverage of all LA County cities including Glendale, Long Beach, Pasadena, etc., consider:

1. **City-Specific Open Data Portals:**
   - City of LA: https://data.lacity.org (Socrata API)
   - Long Beach: https://data.longbeach.gov

2. **Commercial Bulk Data Providers:**
   - ATTOM Data: 300M+ permits from 2,000+ jurisdictions nationwide
   - BuildZoom: 350M+ permits, 25+ years of history
   - Shovels API: 2,000+ jurisdictions, standardized data

## Example Response

```json
{
  "features": [
    {
      "attributes": {
        "CASENUMBER": "UNC-BLDR260326003120",
        "APPLY_DATE": 1774512000000,
        "STATUS": "New",
        "MAIN_ADDRESS": " 4608 La Crescenta Avenue, La Crescenta CA 91214",
        "WORKCLASS_NAME": "Addition/Alteration",
        "MODULENAME": "PermitManagement",
        "SUP_DIST": "SD-5",
        "PERMIT_VALUATION": 0,
        "NEW_DWELLING_UNITS": 0
      }
    }
  ]
}
```

## Contact

For technical assistance:
- EPIC-LA Help: epiclahelp@lacounty.gov
- Building & Safety IT Support: BSD-ITSupport@dpw.lacounty.gov
- LA County Enterprise GIS: egis@isd.lacounty.gov
