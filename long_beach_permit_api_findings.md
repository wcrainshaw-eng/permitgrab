# Long Beach, California - Permit Data API Research

**Date:** 2026-03-29
**Searched for:** Working permit data API endpoints with 2025/2026 data

## Summary

After comprehensive research, **no publicly accessible REST API endpoint with individual building permit records** was found for Long Beach, California that contains current 2025/2026 permit data.

---

## Resources Found

### 1. Long Beach ArcGIS Services
**Organization ID:** yCArG7wGXGyWLqav
**Base URL:** https://services6.arcgis.com/yCArG7wGXGyWLqav/arcgis/rest/services

#### Available Building/Permit Related Services:
- **Bldg_Permits_5th_Cycle_RHNA_2020** (FeatureServer)
  - URL: https://services6.arcgis.com/yCArG7wGXGyWLqav/ArcGIS/rest/services/Bldg_Permits_5th_Cycle_RHNA_2020/FeatureServer
  - Fields: Year, Building_Type, VLI, LI, MI, MR, Total, Affordable, Total_Affordable, ObjectId
  - **Status:** Aggregated housing data by year, not individual permits

- **Building_Inspection_Residential** (FeatureServer)
  - URL: https://services6.arcgis.com/yCArG7wGXGyWLqav/ArcGIS/rest/services/Building_Inspection_Residential/FeatureServer
  - Fields: OBJECTID, DISTRICT, INSPECTOR, GlobalID, created_user, created_date, etc.
  - **Status:** Inspection district boundaries, not permit records

- **Building_Inspection_Commercial** (FeatureServer)
  - URL: https://services6.arcgis.com/yCArG7wGXGyWLqav/ArcGIS/rest/services/Building_Inspection_Commercial/FeatureServer
  - **Status:** Inspection district boundaries

- **Building_Inspection_Electrical** (FeatureServer)
  - URL: https://services6.arcgis.com/yCArG7wGXGyWLqav/ArcGIS/rest/services/Building_Inspection_Electrical/FeatureServer
  - **Status:** Inspection district boundaries

### 2. Long Beach GIS Server
**Base URL:** https://gis.longbeach.gov/fed/rest/services
**Version:** 10.91

#### Discovered Folders:
- Permitting (folder exists)
  - **Status:** Token Required - not publicly accessible

### 3. Long Beach Open Data Portal
**Portal URLs:**
- https://data.longbeach.gov/
- https://maps.longbeach.gov/

**API Consoles:**
- https://data.longbeach.gov/api/v1/console
- https://data.longbeach.gov/api/explore/v2.1/console

**Search Results:** No permit datasets found when searching for "permit" in the API

**Available Datasets:** 13 total datasets available, none appear to be building permits

### 4. Long Beach Permit System
**Portal:** https://permitslicenses.longbeach.gov/
**Description:** City of Long Beach's online permit application system
**Status:** Web-based portal for permit applications, no public API documented

### 5. LA County Data
**LA County Open Data:** https://data.lacounty.gov/

**Construction Permits Dataset (2000-2018):**
- Dataset ID: bkfq-69wz
- URL: https://data.lacounty.gov/Public-Works/2000-2018-Construction-Permits/bkfq-69wz
- **Status:** Data only through 2018, tested API endpoint did not return data
- **Coverage:** Unincorporated LA County areas (Long Beach is incorporated, likely not included)

---

## Incorrect Endpoints Found During Research

### Virginia Beach Service (Not Long Beach!)
**URL:** https://services2.arcgis.com/CyVvlIiUfRBmMQuu/arcgis/rest/services/Building_Permits_Applications_view/FeatureServer

**Note:** This service appeared in searches but returns Virginia Beach, VA data, NOT Long Beach, CA.

#### Sample Fields:
- PermitNumber, CreatedBy, PermitType, ConstructionType, WorkType
- ApplicationDate, IssueDate, FinalDate, Status, WorkDesc
- GPIN, StreetAddress, AddressUnit, City, State, Zip

**Test Results:** Contains 2025 data with City="Virginia Beach", State="VA"

---

## Alternative Access Methods

### 1. Building Permit Records Search
**URL:** https://www.longbeach.gov/lbcd/building/permit-center/building-permit-records/
**Description:** Web-based search for building permit records by address
**Type:** Manual lookup, no API

### 2. Permit Status Inquiry
**URL:** https://www.longbeach.gov/lbcd/building/permit-center/status-inquiry/
**Description:** Permit status/permit history lookup
**Format:** Project Number, Project Description, Inspection Final dates
**Type:** Web interface only

### 3. RecordsLB
**URL:** https://www.longbeach.gov/openlb/recordslb
**Description:** Search historical/archival construction documents
**Type:** Address-based query system

---

## Recommendations

1. **Contact City Directly:** Reach out to Long Beach Development Services or GIS department to inquire about API access to permit data

2. **Check for Updates:** The open data portal (data.longbeach.gov) only has 13 datasets currently - monitor for future permit dataset additions

3. **ArcGIS Hub:** Check maps.longbeach.gov periodically for new datasets being published

4. **Alternative Sources:**
   - HUD has county-level residential construction permit data
   - State of California may have aggregated permit data

---

## Technical Details

### Long Beach ArcGIS Organization
- **Portal:** longbeachca.maps.arcgis.com
- **Custom Domain:** maps.longbeach.gov
- **Data Portal:** datalb.longbeach.gov
- **Organization ID:** yCArG7wGXGyWLqav
- **Total Services:** 155+ services available
- **Max Records:** 2000 per query (typical for their services)

### Working Query Example (Inspection Districts)
```
https://services6.arcgis.com/yCArG7wGXGyWLqav/ArcGIS/rest/services/Building_Inspection_Residential/FeatureServer/0/query?where=1=1&outFields=*&f=json
```

---

## Conclusion

Long Beach, California does not currently provide a publicly accessible REST API endpoint with individual building permit records and current 2025/2026 data. The city has:

- A permit application portal (permitslicenses.longbeach.gov) - web interface only
- Various GIS services - but limited to inspection districts and aggregated housing data
- An open data portal - but no permit datasets published
- A secured GIS server with a "Permitting" folder - requires authentication

**Status:** No working API endpoint available for individual permit records with 2025/2026 dates.
