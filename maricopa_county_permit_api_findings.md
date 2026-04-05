# Maricopa County, Arizona - Bulk Permit Data API Research

## Date: March 29, 2026

## Summary
Research into county-level and city-level APIs for building permit data covering Phoenix, Scottsdale, Tempe, Mesa, Chandler, Glendale, and other Maricopa County cities.

---

## BEST OPTION: Mesa, Arizona - SODA API

### Working URL (City of Mesa Building Permits)
**API Endpoint:** `https://citydata.mesaaz.gov/resource/dzpk-hxfb.json`

**Public View:** `https://data.mesaaz.gov/resource/m2kk-w2hz.json`

**Dataset Page:** https://data.mesaaz.gov/Development-Services/Building-Permits-Commercial-Residential-and-Signs-/m2kk-w2hz

### Data Verification (TESTED & CONFIRMED - March 29, 2026)
- API is WORKING and accessible
- Contains 2025 and 2026 data (confirmed with live query)
- Latest permit: PMT26-05179 issued March 27, 2026
- Total records: 153,774
- Data published weekly, changes daily
- No API key required
- CORS enabled (Access-Control-Allow-Origin: *)

### Field Names (Verified from Live API - 37 fields)
```
applicant
balance
contractor_address
contractor_city
contractor_name
contractor_state
contractor_zip
council_district
description_of_work
finaled_date
finaled_month
finaled_year
fiscal_year
fiscalyear
icc_value
issued_date
issued_month
issued_year
job_value
latitude (available on some records)
longitude (available on some records)
location (GeoJSON point - available on some records)
new_residential_permit
opened_date
parcel_number
permit_module
permit_number
permit_type
permit_year
property_address
rowid
status
street_direction
street_name
street_number
street_type (available on some records)
total_fee_assessed
total_fee_invoiced
total_square_feet
total_valuation
update_date
```

**Note:** Not all fields are populated for every record. Newer permits tend to have more complete data including lat/long coordinates.

### Example Queries
```
# Get recent permits
https://citydata.mesaaz.gov/resource/dzpk-hxfb.json?$limit=100&$order=issued_date DESC

# Filter by year
https://citydata.mesaaz.gov/resource/dzpk-hxfb.json?$where=issued_year=2026&$limit=1000

# Filter by date range
https://citydata.mesaaz.gov/resource/dzpk-hxfb.json?$where=issued_date >= '2026-01-01' AND issued_date <= '2026-12-31'

# Get specific permit types
https://citydata.mesaaz.gov/resource/dzpk-hxfb.json?permit_type=Residential

# CSV Download
https://data.mesaaz.gov/api/views/dzpk-hxfb/rows.csv?accessType=DOWNLOAD
```

### API Documentation
- SODA API: https://dev.socrata.com/
- OData endpoints also available
- Access-Control-Allow-Origin: * (CORS enabled)

---

## Alternative Sources

### 1. Maricopa County (Unincorporated Areas Only)
**Note:** Maricopa County permits only cover unincorporated areas, NOT Phoenix, Scottsdale, etc.

- **Permit Center:** https://www.maricopa.gov/6003/Maricopa-Countys-Permit-Center
- **Historical Permit Viewer:** https://apps.pnd.maricopa.gov/PermitViewer (1999-June 2024)
- **System:** Accela-based (transitioned to Permit Center in June 2024)
- **GIS Services:** https://gis.mcassessor.maricopa.gov/arcgis/rest/services
- **Open Data:** https://data-maricopa.opendata.arcgis.com/

**Issue:** No bulk API found for active permits. Data access appears to be through web portal only.

### 2. City of Phoenix
- **Open Data Portal:** https://www.phoenixopendata.com/dataset/phoenix-az-building-permit-data
- **ArcGIS Hub:** https://mapping-phoenix.opendata.arcgis.com/
- **PDD Online Search:** https://apps-secure.phoenix.gov/pdd/search/permits

**Issue:** Dataset last updated March 2023. No current API found. Data is from HUD SOCDS database.

### 3. City of Tempe
- **Data Catalog:** https://data.tempe.gov/datasets/tempegov::permits-issued-by-building-safety
- **GIS Services:** https://gis.tempe.gov/arcgis/rest/services/
- **ArcGIS Hub:** https://data-tempegov.opendata.arcgis.com/

**Services Found:**
- Encroachment Permits: `https://gis.tempe.gov/arcgis/rest/services/encroachment_permits/FeatureServer`
- Building permits data exists but specific FeatureServer endpoint not located

### 4. City of Scottsdale
- **GIS Open Data:** https://data-cos-gis.opendata.arcgis.com/
- **Building Permit Search:** https://eservices.scottsdaleaz.gov/bldgresources/buildingpermit
- **Permit Dataset:** https://data-cos-gis.opendata.arcgis.com/datasets/68ba7b38073f4cd4aee38d2b59afcaf4_12/about

**Note:** Scottsdale transitioned to SPUR portal in January 2026. Building permits data available but specific API endpoint not confirmed.

### 5. City of Chandler
- **Open Data Hub:** https://share-open-data-changis.hub.arcgis.com/
- **System:** Electronic Plan Review (integrated with permitting software)

**Issue:** Dataset exists but specific API endpoint not located.

### 6. City of Glendale
- **Open Data Hub:** https://glendaleaz-cog-gis.hub.arcgis.com/search
- **GIS Portal:** https://gis.glendaleaz.com/
- **OpenData Site:** https://opendata.glendaleaz.com/

**Issue:** Datasets available but specific building permits API endpoint not located.

### 7. MAG (Maricopa Association of Governments)
- **Open Data:** https://geodata-azmag.opendata.arcgis.com/
- **GIS Services:** https://geo.azmag.gov/arcgis/rest/services/

**Services Found:**
- Building Age/Footprints: `https://geo.azmag.gov/arcgis/rest/services/real_estate/Maricopa_Pinal_Building_Age/MapServer`

**Note:** MAG provides regional planning data but does not aggregate building permits from all member cities into a single API.

---

## Recommendations

### For Mesa Coverage
Use the **Mesa SODA API** (documented above) - fully functional with 2025/2026 data.

### For Multi-City Coverage
No single regional API exists. Options:
1. **Individual city APIs:** Query each city's open data portal separately
2. **Maricopa County GIS:** Contact directly for potential bulk data access
3. **MAG:** Contact for potential regional aggregation (they're exploring building permits integration)
4. **Commercial services:** BuildChek, PermitFlow, etc. aggregate this data commercially

### Next Steps
1. Test Mesa API with actual queries (may require API key or user agent)
2. Contact Scottsdale, Chandler, Tempe, Glendale GIS departments for API access
3. Contact MAG about regional permit data aggregation
4. Check if Phoenix has updated their dataset beyond 2023

---

## Technical Notes

- Most cities use either Accela or similar permitting systems
- ArcGIS FeatureServer endpoints typically follow pattern: `https://[domain]/arcgis/rest/services/[folder]/[service]/FeatureServer/[layer]`
- SODA API uses pattern: `https://[domain]/resource/[dataset-id].json`
- Many services may require authentication or API tokens for bulk access
- CORS is typically enabled for public datasets

---

## Sources
- [Maricopa County GIS](https://data-maricopa.opendata.arcgis.com/)
- [Mesa Open Data](https://data.mesaaz.gov/)
- [Phoenix Open Data](https://www.phoenixopendata.com/)
- [Tempe Data Catalog](https://data.tempe.gov/)
- [Scottsdale GIS](https://data-cos-gis.opendata.arcgis.com/)
- [MAG Open Data](https://geodata-azmag.opendata.arcgis.com/)
- [Socrata SODA API Documentation](https://dev.socrata.com/)
