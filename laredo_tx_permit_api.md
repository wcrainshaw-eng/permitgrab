# Laredo, Texas - Permit Data API Documentation

**City:** Laredo, Texas  
**Last Updated:** May 30, 2025  
**Data Coverage:** 2007 - Present (2025)  
**Status:** ✅ VERIFIED WORKING

## Summary
Laredo provides building permit data through CSV files and a CKAN API. The data includes **345,219 records** covering permits from 2007 through May 2025. No 2026 data is available yet. The city does not appear to have public ArcGIS FeatureServer endpoints for permits.

---

## 1. CSV Data Endpoints (RECOMMENDED)

### Building Permits CSV
**URL:** `https://www.openlaredo.com/data/BuildingPermits.csv`

**Records:** 345,220 records  
**Data Range:** 2007 - May 30, 2025  
**Format:** CSV  
**Update Frequency:** Regular updates through CKAN datastore

**Field Names (23 fields):**
1. Permit
2. Structure
3. Type
4. sequence
5. issue date
6. issue month
7. permit fee
8. plan check
9. total
10. valuation
11. sq ft
12. Valuation (True)
13. LargeProj
14. Permit Description
15. Permit-Type
16. Permit Group
17. fiscal_year
18. CalendarYear
19. Match
20. Fiscal Period
21. RevenueCategory
22. Units
23. Buildings

**Sample 2025 Data:**
```csv
25-00006239,000 000,FPLT,0,5/30/2025,5,100,0,100,0,0,0,0,FIRE PLAT REVEIW,FIRE,FIRE,FY 2024-2025,2025,25-00006239FPLT00,8,FIRE INSPECTION FEES,,
```

### Building Inspections CSV
**URL:** `https://www.openlaredo.com/data/BuildingInspections.csv`

**Format:** CSV  
**Data Range:** 2015 - May 30, 2025  
**Includes:** Permit addresses, inspection dates, status, and inspector information

**Field Names (18 fields):**
1. PermitNumber
2. Address
3. GEOID
4. PermitCode
5. PermitSequence
6. PermitType
7. InspectionCode
8. InspectionSequence
9. Inspection Type
10. InspectionDate
11. InspectionStatus
12. Inspector
13. GroupType
14. Month
15. Year
16. Year (Fiscal)
17. Fiscal Period
18. JOINSEQ

**Sample 2025 Data:**
```csv
25-00001805,3807 TAHOE DR,985-27002-300,701,0,EL-RESIDENTIAL,202,2,EL-SERVICE INSPECTION,5/30/2025,APPROVED,AG,ELECTRICAL,5,2025,FY 2024-2025,8,25-0000180570100
```

### Permits Issued Report (Excel)
**URL:** `https://www.openlaredo.com/data/PermitsIssuedReports.xlsx`

**Format:** Excel (.xlsx)  
**Description:** Includes permit addresses, valuations, names  
**Note:** Contains address information linked to permits

---

## 2. CKAN API Endpoints

### Base URL
`https://data.openlaredo.com/api/3/action/`

### API Documentation
Official CKAN API Docs: https://docs.ckan.org/en/2.9/api/

### Dataset Metadata
**Endpoint:** `package_show`  
**Example:**
```bash
curl "https://data.openlaredo.com/api/3/action/package_show?id=building-permits"
```

**Response Fields:**
- Dataset ID: `b7cdb7e5-abc6-41ec-b577-9efdeca43180`
- Author: Andres Castaneda (acastaneda@ci.laredo.tx.us)
- Maintainer: Miguel Hernandez (mhernande2@ci.laredo.tx.us)
- Description: Building permits since Naviline inception in 2007

### Datastore Search
**Endpoint:** `datastore_search`  
**Resource ID:** `7f70bf47-7c3d-4913-864f-f5557563cbd2`

**Example Query:**
```bash
curl "https://data.openlaredo.com/api/3/action/datastore_search?resource_id=7f70bf47-7c3d-4913-864f-f5557563cbd2&limit=10"
```

**Sample Response Structure:**
```json
{
  "success": true,
  "result": {
    "total": 345219,
    "records": [
      {
        "_id": 1,
        "Permit": "07-00000442",
        "Structure": "000 000",
        "Type": "500",
        "issue date": "1/2/2008",
        "CalendarYear": "2008",
        "Permit Description": "RES DRIVEWAY & SIDEWALK",
        "Permit-Type": "Right-of-Way",
        "valuation": "$117,533.00",
        ...
      }
    ]
  }
}
```

### Additional Resources
**Permits Issued Excel (via CKAN):**
```bash
curl "https://data.openlaredo.com/dataset/9f3751a0-98ca-4c32-85a3-521dac8eb12b/resource/61972510-7b8c-488a-9e88-b73b0112f496/download/bpod1e.xlsx" -o permits_issued.xlsx
```

---

## 3. Data Portal Information

### Open Data Portal (CKAN-based)
**URL:** https://data.openlaredo.com/dataset/building-permits  
**Platform:** CKAN  
**Organization:** Building Development Services

### ArcGIS Open Data Portal
**URL:** https://open-laredo.opendata.arcgis.com/  
**Organization ID:** `h9QEFLHkUI1SIRs7`  
**Note:** Historical datasets available (2014, 2017), but no public FeatureServer endpoints found for current permits

**Historical Datasets:**
- 2017 Building Permits: https://open-laredo.opendata.arcgis.com/datasets/2017-building-permits
- 2014 Building Permits: https://open-laredo.opendata.arcgis.com/datasets/od-2014-total-building-permits

---

## 4. Testing & Verification

### Test CSV Access
```bash
# Download first 10 lines
curl -s "https://www.openlaredo.com/data/BuildingPermits.csv" | head -n 10

# Check for 2025 data
curl -s "https://www.openlaredo.com/data/BuildingPermits.csv" | grep ",2025," | head -n 5

# Get total record count
curl -s "https://www.openlaredo.com/data/BuildingPermits.csv" | wc -l
```

### Test CKAN API
```bash
# Get dataset metadata
curl "https://data.openlaredo.com/api/3/action/package_show?id=building-permits"

# Search datastore (limit 5 records)
curl "https://data.openlaredo.com/api/3/action/datastore_search?resource_id=7f70bf47-7c3d-4913-864f-f5557563cbd2&limit=5"
```

### Verification Results
- ✅ CSV endpoint active and returning data
- ✅ 2025 data confirmed (latest: May 30, 2025)
- ❌ 2026 data not yet available
- ✅ CKAN API functional (345,219 records)
- ✅ Address data available in BuildingInspections.csv
- ❌ No public ArcGIS FeatureServer found

---

## 5. Key Permit Types & Categories

From the data, permits are categorized by:

**Permit Types:**
- Residential Construction
- Renovation & Additions
- Right-of-Way
- Electrical (EL-RESIDENTIAL, EL-COMMERCIAL)
- Mechanical (ML-COMMERCIAL)
- Plumbing (PL-COMMERCIAL)
- Reroof (RES-ROOF)
- Fire Inspections

**Revenue Categories:**
- BUILDING PERMIT
- RIGHT OF WAY PERMITS
- ELECTRICAL PERMITS
- MECHANICAL PERMITS
- PLUMBING PERMITS
- FIRE INSPECTION FEES

**Permit Groups:**
- NEW CONSTRUCTION
- PROFESSIONAL PERMITS (M,P,E)
- REROOF
- Renovation & Additions

---

## 6. Data Update Information

**Last Dataset Update:** September 25, 2024 (metadata)  
**Latest Data Record:** May 30, 2025  
**Fiscal Year:** FY 2024-2025  
**Update Schedule:** Regular updates via CKAN datastore

---

## 7. Contact Information

**Chief Data Officer:**  
Andres Castaneda  
Email: acastaneda@ci.laredo.tx.us

**Data Maintainer:**  
Miguel Hernandez  
Email: mhernande2@ci.laredo.tx.us

**Department:**  
Building Development Services  
City of Laredo, Texas

---

## 8. Notes & Limitations

1. **No ArcGIS FeatureServer:** While Laredo has an ArcGIS Open Data portal, no public REST FeatureServer endpoints were found for current permit data
2. **Historical GIS Data:** Only historical permit data (2014, 2017) available through ArcGIS portal
3. **CSV is Primary Source:** The CSV endpoints are the most reliable and up-to-date source
4. **Address Data:** Full address information is available in the BuildingInspections.csv file
5. **No 2026 Data:** As of this documentation, no 2026 permits have been issued
6. **Data Coverage:** Comprehensive data from 2007 to present (May 2025)

---

## Recommended Approach

For automated data collection:
1. **Primary Source:** Use CSV endpoint (`https://www.openlaredo.com/data/BuildingPermits.csv`)
2. **Address Mapping:** Use BuildingInspections.csv for permit addresses
3. **API Access:** Use CKAN API for filtered queries and metadata
4. **Update Frequency:** Check daily for new records
5. **Data Validation:** Monitor the CalendarYear field for 2026 data availability

