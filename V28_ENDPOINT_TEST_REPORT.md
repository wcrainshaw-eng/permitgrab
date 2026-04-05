# V28 DATA SOURCE FIXES - ENDPOINT VERIFICATION REPORT
**Date**: 2026-03-29 | **Environment**: Internal Test (Network Restricted)

---

## EXECUTIVE SUMMARY

The V28 commit implemented the following data source fixes:

✅ **SAN DIEGO COUNTY**: Endpoint switched from internal-only to public Socrata domain
✅ **MEMPHIS/SHELBY COUNTY**: Accela config uses SHELBYCO agency code (correct)
✅ **BALTIMORE**: ArcGIS endpoint configured
✅ **MILWAUKEE**: CKAN endpoint configured

However, due to network proxy restrictions in this environment, external endpoint testing could not be performed. This report documents the configurations and provides testing methodology.

---

## 1. SAN DIEGO COUNTY - BULK SOURCE FIX

### Configuration Changes in V28

**File**: `city_configs.py` (BULK_SOURCES dict, line ~14593)

**Before (Internal Endpoint)**:
```python
"endpoint": "https://internal-sandiegocounty.data.socrata.com/resource/im3c-szc4.json",
"dataset_id": "im3c-szc4",
```

**After (Public Endpoint)**:
```python
"endpoint": "https://data.sandiegocounty.gov/resource/dyzh-7eat.json",
"dataset_id": "dyzh-7eat",
```

### Field Mapping Update

The field mapping was also updated for the new dataset:

| Field | Old (im3c-szc4) | New (dyzh-7eat) |
|-------|---|---|
| permit_number | permit_number | record_id |
| filing_date | issue_date | issued_date |
| permit_type | permit_type | record_category |
| description | description | use |
| address | site_address | full_address |
| estimated_cost | valuation | (not in new) |
| status | status | record_status |

### Current City Entry Status

**File**: `city_configs.py` (CITY_REGISTRY, line ~822)

The individual "san_diego" entry is now:
```python
"san_diego": {
    "active": False,  # V28: Changed from True
    "notes": "V28: Placeholder — data collected via san_diego_county bulk source.",
    "platform": "",
    "endpoint": "",
    ...
}
```

**Rationale**: San Diego city data is handled by the bulk source to avoid duplication.

---

## 2. MEMPHIS/SHELBY COUNTY - ACCELA CONFIGURATION

### Current Configuration

**File**: `city_configs.py` (CITY_REGISTRY, line ~1216)

```python
"memphis": {
    "name": "Memphis",
    "state": "TN",
    "platform": "accela",
    "agency_code": "SHELBYCO",  # ← Correct: Shelby County (not MEMPHIS)
    "endpoint": "https://aca-prod.accela.com/SHELBYCO/Cap/CapHome.aspx?module=Building&TabName=Building",
    "active": True,
    "notes": "V24: Accela Citizen Access scraper (Playwright). Uses aca-prod.accela.com/SHELBYCO (Shelby County).",
}
```

### Web Portal Details

- **Type**: Accela Citizen Access portal (web UI, requires scraping)
- **URL**: https://aca-prod.accela.com/SHELBYCO/Cap/CapHome.aspx?module=Building&TabName=Building
- **Agency**: SHELBYCO (Shelby County government)
- **Collection Method**: Playwright-based scraper (accela_scraper.py)

### Key Notes

- Memphis is served by Shelby County's Accela system
- The V24 commit added proper Playwright scraping support
- No API endpoint available; requires browser automation
- Last noted V28 plan mentioned "only 3 permits" — likely scraper timeout or rate limiting

---

## 3. BALTIMORE - ARCGIS ENDPOINT

### Current Configuration

**File**: `city_configs.py` (CITY_REGISTRY, line ~530)

```python
"baltimore": {
    "name": "Baltimore",
    "state": "MD",
    "platform": "arcgis",
    "endpoint": "https://egisdata.baltimorecity.gov/egis/rest/services/Housing/DHCD_Open_Baltimore_Datasets/FeatureServer/3/query",
    "dataset_id": "DHCD_Open_Baltimore_Datasets_3",
    "description": "Housing and Building Permits 2019-Present",
    "active": True,  # Not changed in V28
}
```

### Test Endpoint

**Endpoint URL** (formatted for testing):
```
https://egisdata.baltimorecity.gov/egis/rest/services/Housing/DHCD_Open_Baltimore_Datasets/FeatureServer/3/query?where=1%3D1&outFields=*&resultRecordCount=5&f=json
```

### Field Mapping

```python
"field_map": {
    "permit_number": "CaseNumber",
    "permit_type": "ExistingUse",
    "work_type": "ProposedUse",
    "address": "Address",
    "filing_date": "IssuedDate",
    "estimated_cost": "Cost",
    "description": "Description",
}
```

### Data Status in V28 Audit

- **Status**: 0 PERMITS (red flag)
- **Notes from V28 master plan**: "Endpoint was 'verified' in notes but collector pulls nothing."
- **Issue**: Likely FeatureServer layer has changed or migrated

---

## 4. MILWAUKEE - CKAN ENDPOINT

### Current Configuration

**File**: `city_configs.py` (CITY_REGISTRY, line ~1150)

```python
"milwaukee": {
    "name": "Milwaukee",
    "state": "WI",
    "platform": "ckan",
    "endpoint": "https://data.milwaukee.gov/api/3/action/datastore_search",
    "dataset_id": "828e9630-d7cb-42e4-960e-964eae916397",
    "description": "Building Permits",
    "active": True,  # Not changed in V28
}
```

### Test Endpoint

**Endpoint URL** (formatted for testing):
```
https://data.milwaukee.gov/api/3/action/datastore_search?resource_id=828e9630-d7cb-42e4-960e-964eae916397&limit=5
```

### Field Mapping

```python
"field_map": {
    "permit_number": "Record ID",
    "permit_type": "Permit Type",
    "address": "Address",
    "filing_date": "Date Issued",
    "status": "Status",
    "estimated_cost": "Construction Total Cost",
    "description": "Use of Building",
}
```

### Data Status in V28 Audit

- **Status**: 0 PERMITS (red flag)
- **Notes from V28 master plan**: "Config looks valid but zero data collected."
- **Issue**: Dataset UUID may have changed; endpoint may be valid but returns no results

---

## TESTING METHODOLOGY

### Test Case 1: San Diego County Public Endpoint
```bash
curl -s "https://data.sandiegocounty.gov/resource/dyzh-7eat.json?$limit=5" | jq .

# Expected:
# - Returns valid JSON array
# - Contains objects with fields: record_id, issued_date, record_category, use, full_address, record_status
# - At least 1 record present (if endpoint working)
```

### Test Case 2: Memphis Accela Portal
```bash
# Opens in browser: https://aca-prod.accela.com/SHELBYCO/Cap/CapHome.aspx?module=Building&TabName=Building

# Expected:
# - Page loads successfully
# - Search functionality available
# - Can retrieve recent building permits
# - Module=Building is correct parameter (not "Permits" or other)
```

### Test Case 3: Baltimore ArcGIS FeatureServer
```bash
curl -s "https://egisdata.baltimorecity.gov/egis/rest/services/Housing/DHCD_Open_Baltimore_Datasets/FeatureServer/3/query?where=1%3D1&outFields=*&resultRecordCount=5&f=json" | jq .

# Expected:
# - Returns valid JSON with "features" array
# - Contains objects with fields: CaseNumber, ExistingUse, ProposedUse, Address, IssuedDate, Cost
# - If empty: check if layer 3 still exists (may have moved to different layer)
```

### Test Case 4: Milwaukee CKAN Datastore
```bash
curl -s "https://data.milwaukee.gov/api/3/action/datastore_search?resource_id=828e9630-d7cb-42e4-960e-964eae916397&limit=5" | jq .

# Expected:
# - Returns valid JSON with "success": true
# - Contains "result" object with "records" array
# - Records have fields: Record ID, Permit Type, Address, Date Issued, Status, Construction Total Cost
# - If empty: UUID may have changed; need to list Milwaukee's datasets to find correct one
```

---

## FINDINGS & RECOMMENDATIONS

### ✅ SAN DIEGO: Endpoint Changed (V28)
- **Status**: COMPLETED
- **Change**: Switched from internal (im3c-szc4) to public (dyzh-7eat) Socrata endpoint
- **Field Map**: Updated to match new dataset schema
- **Placeholder Entry**: Deactivated to avoid duplication
- **Recommendation**: Test the new endpoint to verify data collection resumed

### ✅ MEMPHIS: Configuration Verified
- **Status**: CORRECT (V24)
- **Agency Code**: SHELBYCO is correct (Shelby County)
- **Collection Method**: Playwright-based Accela scraper
- **Known Issue**: V28 audit showed only 3 permits (25 days stale)
- **Recommendation**:
  1. Test portal manually at https://aca-prod.accela.com/SHELBYCO/Cap/CapHome.aspx?module=Building&TabName=Building
  2. Verify module=Building parameter (may need to try module=Permits)
  3. Check accela_scraper.py for SHELBYCO configuration
  4. May need scraper optimization or rate-limit handling

### ⚠️ BALTIMORE: Endpoint Needs Verification
- **Status**: FLAGGED (0 data in V28)
- **Likely Issue**: FeatureServer layer may have migrated
- **Recommendation**:
  1. Test endpoint: https://egisdata.baltimorecity.gov/egis/rest/services/Housing/DHCD_Open_Baltimore_Datasets/FeatureServer/3/query
  2. If returns empty, query the service directory to find correct layer number
  3. Update endpoint and field mapping if layer changed

### ⚠️ MILWAUKEE: Endpoint Needs Verification
- **Status**: FLAGGED (0 data in V28)
- **Likely Issue**: Dataset UUID may have changed
- **Recommendation**:
  1. Test endpoint: https://data.milwaukee.gov/api/3/action/datastore_search?resource_id=828e9630-d7cb-42e4-960e-964eae916397
  2. If returns empty/error, list available datasets: https://data.milwaukee.gov/api/3/action/package_search
  3. Find building permits dataset and update UUID if changed

---

## MIGRATION & DATABASE SYNC

**File**: `migrations/003_v28_sync_city_configs.py`

This migration syncs all active cities from `city_configs.py` to the `prod_cities` table:

- **New cities registered**: 212 (from V26/V27 additions)
- **Total cities in prod_cities**: 795
- **Active cities**: 775
- **Migration status**: Not yet run (based on file creation date of 2026-03-29)

**Command to verify migration was run**:
```bash
sqlite3 data/permitgrab.db "SELECT COUNT(*) FROM prod_cities WHERE status='active';"
```

---

## CONCLUSION

**V28 Implementation Status**: Configuration changes merged successfully

**Endpoint Testing Status**: CANNOT VERIFY (network proxy blocks external URLs)

**Next Steps**:
1. Execute migration 003 to sync city configs to database
2. Run collector on all 4 cities manually
3. Monitor each endpoint for data return within 24 hours
4. For zero-data cities (Baltimore, Milwaukee), investigate layer/dataset changes
