# Rochester, New York (Monroe County) Permit Data API Research

**Research Date:** March 29, 2026
**Location:** Rochester, NY (Monroe County)

## Summary

Rochester, NY uses the Accela permitting system for building permits, but the city's ArcGIS FeatureServer services for permit data are currently **OFFLINE/NOT STARTED**. The primary open data portal is DataROC, but active permit APIs were not found to be operational during testing.

---

## Key Findings

### 1. City of Rochester - Accela System
**Portal:** https://aca-prod.accela.com/ROCHESTER/Cap/CapHome.aspx?module=Building&TabName=HOME

- Rochester uses Accela Citizen Access for building permit applications and searches
- Web interface for searching permits is available
- No public REST API access confirmed
- All roofing, plumbing, and electrical permits must be submitted online

### 2. DataROC - City of Rochester Open Data Portal
**Portal:** https://data.cityofrochester.gov/
**ArcGIS Hub:** https://dataroc-rochesterny.hub.arcgis.com/

- Official open data portal for the City of Rochester
- Features over 100 datasets
- Datasets include GIS data, property information, and various city services

### 3. City of Rochester ArcGIS Server
**Base URL:** https://maps.cityofrochester.gov/server/rest/services

#### Status: SERVICES CURRENTLY OFFLINE

The following permit-related services were identified but returned "Service not started" errors:

**Business Permits Service (OFFLINE):**
- URL: `https://maps.cityofrochester.gov/server/rest/services/NBD/Business_Permits/FeatureServer`
- Status: Error 500 - "Service NBD/Business_Permits/MapServer not started"

**Business Permits Open Data (OFFLINE):**
- URL: `https://maps.cityofrochester.gov/server/rest/services/Open_Data/Business_Permits_Open_Data/FeatureServer`
- Status: Error 500 - "Service Open_Data/Business_Permits_Open_Data/MapServer not started"

**Code Enforcement Open Cases (OFFLINE):**
- URL: `https://maps.cityofrochester.gov/server/rest/services/Open_Data/Code_Enforcement_Open_Cases_Open_Data/FeatureServer`
- Status: Error 500 - "Service not started"
- Fields would include: PERMIT_CASE, CASE_OPEN_DATE, LAST_TICKET_DATE, VACANT_DATE, ADDRESS, SBL

---

## Working Services Found

### Planning Projects Open Data (WORKING)
**URL:** `https://maps.cityofrochester.gov/server/rest/services/Open_Data/Planning_Projects_Open_Data/FeatureServer/0`

**Status:** OPERATIONAL
**Max Records:** 2000 per query
**Spatial Reference:** WKID 102717 (NAD 1983 StatePlane New York West)

**Field Names:**
- OBJECTID (OID)
- PROJECTNAME (String, 100)
- PROJECTDESCRIPTION (String, 1000)
- WEBADDRESS (String, 200)
- CONTACTNAME (String, 50)
- ORGANIZATION (String, 100)
- FUNDINGSOURCE (String, 100)
- CONTACTEMAIL (String, 100)
- PROJECTYEAR (String, 20)
- PROJECTSTATUS (String, 500)
- CITYLEAD (String, 10)
- ACTIVE (String, 20) - Yes/No
- COLOR (String, 50) - Red/Green/Blue/Yellow
- LinkText (String, 50)
- Shape__Area (Double)
- Shape__Length (Double)

**Example Query:**
```
https://maps.cityofrochester.gov/server/rest/services/Open_Data/Planning_Projects_Open_Data/FeatureServer/0/query?where=1=1&outFields=*&f=json
```

**Note:** This dataset contains planning projects, not building permits. No 2025/2026 project data was found in testing.

---

## Monroe County GIS Services

**Base URL:** https://maps.monroecounty.gov/server/rest/services
**GIS Hub:** https://gishub-monroegis.hub.arcgis.com/

**Status:** Server is online but no building permit services were found

**Available Service Folders:**
- Base_Layers, Base_Map, BaseLayers
- BOE, CAD_911, Census_Data
- City_MCWA_Water, Cityworks_Test
- DES, DES_Fiber, DES_Sewer_Assets, DES_Sewer_UN
- DOT, Education, EOC, Facilities, FAM
- Flood_Layers, Geocoding_Tools, Hosted
- Imagery, Infrastructure, LiDAR
- Misc_Temporary_Layers, Monuments, Municipal_Assets
- Network_Analysis, NetworkFleet, Parks
- Pesticide_App, **Planning**, Public_Safety
- Pure_Waters_Assets, SAP, SnowmobileTrails
- Solid_Waste, Stormwater, Test_Table
- Transportation, Treatment_Plant_Assets
- Utilities, Weights_and_Measures, WRRF, Zoo

**Planning Folder Services:**
- Planning/CDBG_Low_Mod_Upper_Quartile (FeatureServer/MapServer)
- Planning/CTST_Intersections (FeatureServer/MapServer)
- Planning/DRC_Review_Area (MapServer)
- Planning/LMISD_Upper_Quartile (MapServer)
- Planning/Planning_DRC_Review_Area (MapServer)

**No building permit services were identified in Monroe County's ArcGIS Server**

---

## Other Data Sources

### HUD - Residential Construction Permits by County
**Portal:** https://hudgis-hud.opendata.arcgis.com/datasets/HUD::residential-construction-permits-by-county/about

- County-level residential construction permit data
- Data from Census Bureau's Building Permits Survey
- Annual data from 1980 to present
- Includes Monroe County, NY data
- Final permits only (not preliminary permits)

**Note:** The exact FeatureServer URL was not accessible during testing. Visit the HUD portal for API endpoints.

### New York State Open Data
**Portal:** https://data.ny.gov/

- Statewide open data portal
- Developer portal with API access
- Building permit datasets for some NY cities (Buffalo, Albany)
- No specific Rochester/Monroe County building permit dataset found

**API Format:** `https://data.ny.gov/resource/[dataset-id].json`

---

## Recommendations

### For Current Permit Data (2025/2026):

1. **Accela Citizen Access Portal** (Web Interface Only)
   - URL: https://aca-prod.accela.com/ROCHESTER/Cap/CapHome.aspx?module=Building&TabName=HOME
   - Manual search capability
   - No confirmed public API access

2. **Contact City of Rochester GIS Team**
   - Email: opendata@cityofrochester.gov
   - Website: https://www.cityofrochester.gov/departments/it/geographic-information-system-gis-maps
   - Request activation of Business Permits FeatureServer
   - Request API access to building permit data

3. **DataROC Data Request**
   - URL: https://data.cityofrochester.gov/pages/225135048db4469996b4729d5c4b2009
   - Submit data release request form
   - Response within 2-3 business days

4. **BuildingBlocks Tool** (Property-Level Data)
   - URL: https://www.cityofrochester.gov/departments/neighborhood-and-business-development/buildingblocks
   - Interactive map-based queries
   - Includes code enforcement data
   - Works best on Chrome or Firefox

### For Historical/County-Level Data:

1. **HUD SOCDS Building Permits Database**
   - URL: https://socds.huduser.gov/permits/
   - County-level aggregated data
   - Historical annual data available

2. **Monroe County GIS Department**
   - Location: 7th floor, Monroe County City Place Building, 50 W. Main St., Rochester
   - Phone: Contact via main county line
   - GIS Hub: https://gishub-monroegis.hub.arcgis.com/

---

## Technical Details

### Working API Example (Planning Projects)

**Endpoint:**
```
https://maps.cityofrochester.gov/server/rest/services/Open_Data/Planning_Projects_Open_Data/FeatureServer/0/query
```

**Query All Records:**
```
?where=1=1&outFields=*&f=json
```

**Query by Year:**
```
?where=PROJECTYEAR='2024'&outFields=*&f=json
```

**Query Active Projects:**
```
?where=ACTIVE='Yes'&outFields=*&f=json
```

**Return Formats:** JSON, GeoJSON, CSV, Shapefile, FileGDB, SQLite

---

## Conclusion

**Current Status:** No working building permit API endpoints were found for Rochester, NY (Monroe County) that return 2025/2026 permit data.

**Primary Issue:** The Business Permits and Code Enforcement FeatureServer services exist in the city's ArcGIS infrastructure but are currently stopped/offline.

**Next Steps:**
1. Contact City of Rochester GIS/IT department to request service activation
2. Use Accela web portal for manual permit searches
3. Submit formal data request through DataROC portal
4. Consider HUD data for county-level residential permit statistics

**Last Verified:** March 29, 2026
