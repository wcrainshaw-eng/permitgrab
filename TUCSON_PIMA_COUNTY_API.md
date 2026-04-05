# Tucson/Pima County Building Permit API Documentation

## Overview
City of Tucson provides building permit data through ArcGIS REST services. The API is actively maintained and contains current 2025 and 2026 permit records.

**Base URL:** `https://mapdata.tucsonaz.gov/arcgis/rest/services/PublicMaps/PermitsCode/MapServer`

**Status:** VERIFIED WORKING (Tested March 29, 2026)

---

## API Endpoints

### 1. Commercial Building Permits
**Layer ID:** 81
**Layer Name:** PDSD_CommercialBldg
**URL:** `https://mapdata.tucsonaz.gov/arcgis/rest/services/PublicMaps/PermitsCode/MapServer/81`

**Record Count (2025+):** 1,624 permits

### 2. Residential Building Permits
**Layer ID:** 85
**Layer Name:** PDSD_ResidentialBldg
**URL:** `https://mapdata.tucsonaz.gov/arcgis/rest/services/PublicMaps/PermitsCode/MapServer/85`

**Record Count (2025+):** 5,792 permits

### 3. Multi-Family Building Permits
**Layer ID:** 84
**Layer Name:** PDSD_Multi_Family
**URL:** `https://mapdata.tucsonaz.gov/arcgis/rest/services/PublicMaps/PermitsCode/MapServer/84`

**Record Count (2025+):** 15 permits

**Additional Fields (Multi-Family only):**
- `DwellingUnits` (esriFieldTypeSmallInteger) - Number of dwelling units
- `DwellingBldgs` (esriFieldTypeSmallInteger) - Number of dwelling buildings

---

## Field Schema

All permit layers share the following core fields:

| Field Name | Data Type | Description |
|------------|-----------|-------------|
| OBJECTID | esriFieldTypeOID | Unique object identifier |
| ID | esriFieldTypeString | Permit UUID |
| MODULE | esriFieldTypeString | Module type (e.g., "PERMIT") |
| NUMBER | esriFieldTypeString | Permit number (e.g., "TC-RES-0326-01208") |
| ADDRESS | esriFieldTypeString | Street address |
| UNITORSUITE | esriFieldTypeString | Unit or suite number |
| PARCEL | esriFieldTypeString | Parcel number |
| STATUS | esriFieldTypeString | Permit status (e.g., "Issued", "Needs Resubmittal", "Fees Due") |
| TYPE | esriFieldTypeString | Permit type description |
| PREFIX | esriFieldTypeString | Permit prefix (e.g., "TC-RES", "TC-COM") |
| WORKCLASS | esriFieldTypeString | Work classification |
| CensusCode | esriFieldTypeString | Census code |
| StructureType | esriFieldTypeString | Structure type |
| APPLYDATE | esriFieldTypeDate | Date application submitted (UNIX timestamp ms) |
| ISSUEDATE | esriFieldTypeDate | Date permit issued (UNIX timestamp ms) |
| EXPIREDATE | esriFieldTypeDate | Permit expiration date (UNIX timestamp ms) |
| COMPLETEDATE | esriFieldTypeDate | Date work completed (UNIX timestamp ms) |
| VALUE | esriFieldTypeDouble | Project value in dollars |
| SQUAREFEET | esriFieldTypeDouble | Square footage |
| PROJECTNAME | esriFieldTypeString | Project name |
| LAT | esriFieldTypeDouble | Latitude coordinate |
| LON | esriFieldTypeDouble | Longitude coordinate |
| WARD | esriFieldTypeString | City ward (e.g., "Ward 6") |
| DESCRIPTION | esriFieldTypeString | Detailed work description |
| CSS_URL | esriFieldTypeString | Citizen self-service portal URL |
| ENGOV_URL | esriFieldTypeString | EnerGov portal URL |
| PRO_URL | esriFieldTypeString | Pro portal URL |
| SOURCE | esriFieldTypeString | Data source (e.g., "ENGV") |
| DATASOURCE | esriFieldTypeString | Dataset name |
| ACTIVE | esriFieldTypeString | Active status ("Yes"/"No") |
| Shape | esriFieldTypeGeometry | Geographic geometry |

**Note:** Commercial permits include a `CITY` field and `POSTALCODE` field not present in residential permits.

---

## Example Queries

### Query All 2025+ Permits (JSON)
```
https://mapdata.tucsonaz.gov/arcgis/rest/services/PublicMaps/PermitsCode/MapServer/85/query?where=APPLYDATE+%3E%3D+date%272025-01-01%27&outFields=*&f=json
```

### Query 2026+ Permits (Limited Fields)
```
https://mapdata.tucsonaz.gov/arcgis/rest/services/PublicMaps/PermitsCode/MapServer/85/query?where=APPLYDATE+%3E%3D+date%272026-01-01%27&outFields=NUMBER,ADDRESS,STATUS,TYPE,APPLYDATE,ISSUEDATE,VALUE,SQUAREFEET,DESCRIPTION&f=json
```

### Get Record Count
```
https://mapdata.tucsonaz.gov/arcgis/rest/services/PublicMaps/PermitsCode/MapServer/85/query?where=APPLYDATE+%3E%3D+date%272025-01-01%27&returnCountOnly=true&f=json
```

### Query Parameters
- `where` - SQL WHERE clause (e.g., `APPLYDATE >= date'2025-01-01'`)
- `outFields` - Comma-separated field list or `*` for all fields
- `f` - Format: `json`, `geojson`, `html`, etc.
- `returnCountOnly` - Set to `true` to return only count
- `resultRecordCount` - Limit number of records returned
- `orderByFields` - Sort results (e.g., `APPLYDATE DESC`)

---

## Sample Record (2026 Data Verified)

```json
{
  "OBJECTID": 15,
  "ID": "0020c5a3-ffc5-4a3a-8312-3b568ebd497a",
  "MODULE": "PERMIT",
  "NUMBER": "TC-RES-0326-01208",
  "ADDRESS": "4237 E KILMER ST",
  "UNITORSUITE": "",
  "PARCEL": "126010290",
  "STATUS": "Issued",
  "TYPE": "Residential Building - One or Two Family",
  "PREFIX": "TC-RES",
  "WORKCLASS": "Residential Trade Permit",
  "CensusCode": "9999 Census Code N/A",
  "StructureType": "Single Family Residence",
  "APPLYDATE": 1773092843000,
  "ISSUEDATE": 1773273600000,
  "EXPIREDATE": 1804809600000,
  "COMPLETEDATE": null,
  "VALUE": 0.0,
  "SQUAREFEET": 0.0,
  "PROJECTNAME": "",
  "LAT": 32.22639354,
  "LON": -110.90191883,
  "WARD": "Ward 6",
  "DESCRIPTION": "Repair work - electrical riser needs to be raised so the main wire is 5' above the pergola. New riser with support straps plus new feeder wires,",
  "CSS_URL": "https://cityoftucsonaz-energovweb.tylerhost.net/apps/selfservice#/permit/0020c5a3-ffc5-4a3a-8312-3b568ebd497a",
  "ENGOV_URL": "https://cityoftucsonaz-energov.tylerhost.net/apps/managepermit/#/permit/0020c5a3-ffc5-4a3a-8312-3b568ebd497a/summary",
  "PRO_URL": "https://pro.tucsonaz.gov/activity_search/TC-RES-0326-01208",
  "SOURCE": "ENGV",
  "DATASOURCE": "PDSD_RESIDENTIALBLDG",
  "ACTIVE": "Yes"
}
```

**Date Conversion Example:**
- APPLYDATE: 1773092843000 = March 9, 2026
- ISSUEDATE: 1773273600000 = March 11, 2026

---

## Other Available Permit Layers

The MapServer contains 58+ layers including:

| Layer ID | Name | Description |
|----------|------|-------------|
| 82 | PDSD_DevelopmentPackage | Development packages |
| 86 | PDSD_Subdivision | Subdivision permits |
| 87 | PDSD_LotSplits | Lot split permits |
| 90 | PDSD_DesignModifications | Design modification permits |
| 91 | PDSD_DesignReviews | Design review permits |
| 92 | PDSD_MarijuanaUseAuth | Marijuana use authorizations |
| 93 | PDSD_SpecialExceptions | Special exception permits |
| 95 | PDSD_PeddlerPermits | Peddler permits |
| 97 | PDSD_Variance | Variance permits |
| 99 | PDSD_CertificatesofOccupancy | Certificates of occupancy |
| 101 | PDSD_SIGNS | Sign permits |
| 108 | Active Development_Building ROW Permits | Active ROW permits |
| 109 | Inactive Development_Building ROW Permits | Inactive ROW permits |

---

## Additional Resources

- **City of Tucson GIS:** https://www.tucsonaz.gov/gis
- **Tucson Open Data Portal:** https://gisdata.tucsonaz.gov/
- **Pima County Geospatial Data Portal:** https://gisopendata.pima.gov/
- **MapServer Root:** https://mapdata.tucsonaz.gov/arcgis/rest/services
- **Permit Portal (EnerGov):** https://permits.pima.gov

---

## Data Quality Notes

- Data is refreshed regularly (appears to be near real-time or daily)
- APPLYDATE is the most reliable field for filtering recent permits
- Some records may have null ISSUEDATE if not yet issued
- COMPLETEDATE is often null for active permits
- VALUE field may be 0 for minor permits/repairs
- Timestamps are in UNIX milliseconds (divide by 1000 for standard UNIX timestamps)

---

## Testing Results

Tested on March 29, 2026:
- Commercial permits: 1,624 records from 2025+
- Residential permits: 5,792 records from 2025+
- Multi-family permits: 15 records from 2025+
- 2026 data confirmed present in all layers
- API response time: < 2 seconds for most queries
- Supports JSON, GeoJSON, and other formats

**Status:** Production-ready and actively maintained
