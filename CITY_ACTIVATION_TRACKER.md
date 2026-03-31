# City Activation Tracker
Last updated: 2026-03-31

Working down inactive cities by population. For each city: test every portal, either activate or mark exhausted.

## EXHAUSTED — No viable data source found

### 1. Houston, TX (pop 2,304,580) — EXHAUSTED
**Portals tried:**
- CKAN (data.houstontx.gov): Only "Residential Building Permits by Month and Year" — aggregated monthly XLS, NOT individual permits
- ArcGIS Hub (cohgis-mycity.opendata.arcgis.com): Zero permit datasets
- ArcGIS Hub (houston-mycity.opendata.arcgis.com): Zero permit datasets
- ArcGIS Hub (geohub.houstontx.gov): Zero permit datasets
- Texas State Socrata (data.texas.gov): Zero Houston permit datasets
- Houston Permitting Center (houstonpermittingcenter.org): Proprietary iPermits/ILMS/ProjectDox system. No API. Sold Permits Search page exists but is HTML-only, no programmatic access.
- Harris County (epermits.harriscountytx.gov): Requires 10-digit project number, no bulk query API
- NNMD.org: HTML page only, no API

**Verdict:** Houston does not publish individual building permit data through any open data platform. Their system is proprietary (iPermits/ILMS). Only aggregate monthly counts available. Cannot be activated.

### 2. San Diego, CA (pop 1,386,932) — EXHAUSTED
**Portals tried:**
- City of San Diego Open Data (data.sandiego.gov): Custom Jekyll-based portal, NOT Socrata. Has "Development Permits" dataset with individual records BUT data is CSV-only download from seshat.datasd.org. No API — requires new "csv" platform type to support. Data updated 2026-03-27 (fresh!) but no programmatic query endpoint.
- San Diego County Socrata (data.sandiegocounty.gov/resource/dyzh-7eat): Endpoint alive, 236K records, BUT most recent data is from 2023-12-05 — over 2 years stale. Fails 30-day freshness gate.
- ArcGIS (sdgis-sandag.opendata.arcgis.com): Regional planning hub, no individual city permits

**Verdict:** City has fresh individual permit data but only as bulk CSV download (not API-queryable). County Socrata endpoint is 2+ years stale. Would need a new "csv" platform type in the collector to support. Cannot be activated with current infrastructure.

### 3. Jacksonville, FL (pop 949,611) — EXHAUSTED
**Portals tried:**
- ArcGIS Hub (coj-coj.opendata.arcgis.com): Zero Jacksonville-specific building permit datasets. Search returned results from other cities only.
- COJ GIS (maps.coj.net/arcgis): CORS-blocked, server exists but no permit layers found
- COJ Open Data (data.coj.net): CORS-blocked, could not verify if exists
- Florida State GeoData (geodata.floridagio.gov): No Jacksonville permit data
- JaxEPICS (jaxepics.coj.net): Proprietary permitting system, no public API. Launched as replacement for old system.
- Old ArcGIS endpoint (services1.arcgis.com/sSwFpTv9KLqJnHQx): Dead/removed

**Verdict:** Jacksonville uses proprietary JaxEPICS system with no open data API. No Socrata, ArcGIS, or CKAN portal publishes individual building permits. Cannot be activated.

### 4. El Paso, TX (pop 678,815) — ACCELA CONFIGURED, NEEDS DEPLOY
**Portals tried:**
- Old Socrata (data.elpasotexas.gov): DNS dead. Domain no longer resolves. Server-side test confirmed: "Failed to resolve hostname."
- ArcGIS Hub (opendata.elpasotexas.gov): New ArcGIS open data portal exists but has zero building permit datasets
- Texas State Socrata (data.texas.gov): No El Paso permit datasets
- Accela (aca-prod.accela.com/ELPASO): LIVE! Confirmed working. Modules: Building, Licenses, Planning, City, Traffic Control, etc.

**Action taken:**
- Added `el_paso_tx` to accela_scraper.py ACCELA_CONFIGS (agency=ELPASO, module=Building)
- Updated city_configs.py: changed platform from socrata→accela, updated endpoint
- Cannot activate until code is pushed and deployed to Render (Accela scraper runs server-side)

**Status:** Config ready. Needs push → deploy → test → activate.

### 5. Las Vegas, NV (pop 641,903) — EXHAUSTED
**Portals tried:**
- ArcGIS MapServer (mapdata.lasvegasnevada.gov/clvgis/rest/services/DevelopmentServices/BuildingPermits/MapServer/0): 768K records but ALL 37 fields are esriFieldTypeString (including dates/valuations). Date-based WHERE clauses fail with error 400. ENTERED_YR field contains "BUILDING" (garbage, not years). ISSUE_YR contains 4-digit years but newest is "2022" — zero records for 2023/2024/2025/2026. Data last updated ~Feb 2022. Fails 30-day freshness gate by 4+ years.
- ArcGIS FeatureServer (same server): Only has ObjectId field, no permit data fields exposed.
- ArcGIS Open Data Hub (opendataportal-lasvegas.opendata.arcgis.com): Zero building permit datasets. Search returned empty.
- Accela (aca-prod.accela.com/LASVEGASNV): 404 — agency does not exist
- Accela (aca-prod.accela.com/CLV): 404 — agency does not exist
- Clark County ArcGIS Hub (opendataportal-clarkcountynv.opendata.arcgis.com): Blank page, no building permit datasets
- No Socrata portal found for City of Las Vegas or Clark County

**Verdict:** Las Vegas MapServer has historical permit data but it's 4+ years stale (last updated Feb 2022), all fields are strings making date queries impossible, and no other open data portal exists. No Accela, no Socrata, no CKAN. Cannot be activated.

---

## ACTIVATED — Successfully tested, backfilled, and configured

(none yet this session)

---

## CSV DATA AVAILABLE — Needs new "csv" platform type

### 6. Albuquerque, NM (pop 564,559) — CSV AVAILABLE, NEEDS PLATFORM SUPPORT
**Portals tried:**
- ArcGIS FeatureServer (coageo.cabq.gov/cabqgeo/rest/services/agis/City_Building_Permits/FeatureServer/0): Works, 43,661 records, BUT newest data is April 12, 2024. Fails 30-day freshness gate by ~12 months.
- data.cabq.gov (Apache file directory, NOT Socrata): Found `business/buildingpermits/` directory with CSV files.
  - **BuildingPermitsCABQ-en-us.csv** (19MB): Updated daily (last-modified: 2026-03-31). Columns: ApplicationPermitNumber, SiteNumber, SiteStreet, SiteStreetType, SiteStreetDirectional, SiteZip, PlanCheckValuation, TypeofWork, Lot, Block, Subdivision, Description, TotalSquareFeet, OwnerName, ContractorName, NumberOfUnits, IssueDate, Status. Sorted by contractor name. Issue dates found up to **2026-01-29**. ~2 months from today but file regenerated daily.
- Accela (aca-prod.accela.com/CABQ): 404 — agency does not exist
- Accela (aca-prod.accela.com/ABQ): 404 — agency does not exist
- Tyler EnerGov / ABQ-PLAN (cityofalbuquerquenm-energovweb.tylerhost.net): Redirected from old POSSE system. REST API exists at `/apps/selfservice/api/energov/permits/search` but returns "Cannot find tenant information" — requires session/tenant auth. Cannot be called server-side without reverse-engineering auth.
- No Socrata portal
- No CKAN portal
- Bernalillo County: bernco.gov/bernco-view/building-permits-zoning/ — informational page only, no API

**Verdict:** Albuquerque publishes daily-updated CSV with individual building permits at `data.cabq.gov/business/buildingpermits/BuildingPermitsCABQ-en-us.csv`. Data is fresh (Jan 2026 issue dates). Good field mapping available. BUT requires a new "csv" platform type in the collector — download CSV, parse, filter by date, normalize. Same situation as San Diego. **Not a dead end — just needs CSV platform support built.**

### 7. Fresno, CA (pop 542,107) — EXHAUSTED
**Portals tried:**
- Accela (aca-prod.accela.com/FRESNO): Redirects to FusionAuth login (login.accela.com). Requires authenticated Accela account. No public search. This is the city's "FAASTER" permitting system.
- Accela alternate (lmsaca.fresno.gov/CitizenAccess): Redirect page back to aca-prod.accela.com/FRESNO login
- Socrata (data.fresno.gov): DNS dead. Domain does not resolve. No Socrata portal.
- ArcGIS Hub (gis-cityoffresno.hub.arcgis.com): Shell site, search returns empty
- ArcGIS Hub (city-of-fresno-gis-hub-cityoffresno.hub.arcgis.com): Official hub per gis4u.fresno.gov. Search for "permit" returns "Please try again" — zero results
- ArcGIS Hub (gis-cofgis.opendata.arcgis.com): "This page may have been moved or deleted"
- ArcGIS Hub (hub-cofgis.opendata.arcgis.com): API returns 400
- ArcGIS Hub (fresno-cofgis.opendata.arcgis.com): API returns 400
- ArcGIS Hosted Services (services1.arcgis.com/wMR0BjLPvsWmvJnl): Zero permit/building FeatureServers
- gis1.fresno.gov: CORS-blocked from cross-origin
- gis4u.fresno.gov/downloads/: Only 1 dataset (FAX Transit GTFS). No permit data.
- data.ca.gov (California state CKAN): No Fresno building permit datasets
- Fresno County Citizens Portal (permitportal.fresnocountyca.gov): Returns error page. Proprietary system requiring registration.
- Fresno County Accela (aca-prod.accela.com/FRESNOCOUNTY): CORS-blocked
- Fresno County Socrata (data.fresnocountyca.gov): CORS-blocked
- No CKAN portal found

**Verdict:** Fresno uses Accela (FAASTER) which requires FusionAuth login — no public API or search. All 5 ArcGIS hub subdomains are either empty, dead, or error. No Socrata, no CKAN, no CSV downloads, no open FeatureServers. Fresno County portal is also proprietary/broken. Cannot be activated.

### 8. Omaha, NE (pop 486,051) — ACCELA CONFIGURED, NEEDS DEPLOY
**Portals tried:**
- ArcGIS Hub (planning-omaha.hub.arcgis.com): Zero Omaha-specific datasets. Hub is a shell with random non-local datasets.
- ArcGIS Hub (data-dogis.opendata.arcgis.com): DCGIS Open Data Portal. Zero building permit datasets. Only has non-Omaha datasets tagged "permit" from other states.
- DOGIS ArcGIS Server (gis.dogis.org/arcgis): Only 1 service: Accela_Dynamic/MapServer — reference map layers (parcels, inspector areas, zoning). NOT permit records.
- Socrata: No Socrata portal exists for City of Omaha or Douglas County. data.cityofomaha.org is dead (old discover_endpoints.py reference).
- permits.cityofomaha.org: City permit portal. Links to omahapermits.com which redirects to Accela.
- Accela (aca-prod.accela.com/OMAHA): LIVE! Confirmed working. Modules: Permits, Licenses, Planning, Rentals, Fire, Public Works, Enforcement. Public search available without login.
- apps.dcgis.org/findpermits: "FindWhoIssuesMyPermits" — jurisdiction lookup tool, not a data source.
- Nebraska State Open Data (nebraska.gov/government/open-data/): No city-level building permit datasets.
- NebraskaMap (nebraskamap.gov): State GIS portal, no building permits.

**Config status:**
- city_configs.py line 1401: `omaha` entry exists, platform=accela, agency_code=OMAHA, active=False
- accela_scraper.py line 65: `omaha` entry exists, module=Permits, tab_name=Permits
- Both configs look correct. Just needs Playwright deploy to test and activate.

**Status:** Config ready. Needs push → deploy → test → activate. Same situation as El Paso.

### 9. Colorado Springs, CO (pop 478,221) — ACCELA CONFIGURED, NEEDS DEPLOY
**Portals tried:**
- Socrata (data.coloradosprings.gov): Tyler Data & Insights portal. Only 16 datasets (budget, sidewalks, trees, airport traffic). Zero permit datasets. Federated catalog misleadingly shows other cities' permits (Orlando, Chicago) but none are local.
- ArcGIS Hub (data-cos-gis.hub.arcgis.com): This is City of Scottsdale, NOT Colorado Springs. "COS" = Scottsdale.
- ArcGIS Server (gis.coloradosprings.gov/arcgis): Accela folder has only reference layers (AddressesParcels, CustomACAWebmap, AccelaScripting). Planning folder has PlanDevTracker_PRO with "Planning_Applications" layer (NOT building permits). No permit FeatureServers.
- ArcGIS Online (coloradosprings.maps.arcgis.com): Zero building permit FeatureServers from COS org.
- Colorado State Open Data (data.colorado.gov, geodata.colorado.gov): No city-level building permit datasets.
- Accela (aca-prod.accela.com/COSPRINGS): LIVE! Confirmed working. Modules: Building, Police Records, Stormwater, Planning, Public Works, Neighborhood Services, Fire.
- CKAN: No CKAN portal found.

**Config status:**
- city_configs.py line 7154: `colorado_springs` entry exists, platform=accela, agency_code=COSPRINGS, module=Building, active=False
- accela_scraper.py line 133: `colorado_springs` entry exists, module=Building, tab_name=Building
- Both configs look correct. Needs Playwright deploy to test.

**Status:** Config ready. Needs push → deploy → test → activate.

### 10. Long Beach, CA (pop 467,354) — EXHAUSTED
**Portals tried:**
- Opendatasoft (data.longbeach.gov): Official open data portal. Only 14 datasets total — Strategic Vision, Employee Demographics, Service Requests, Crime, Animal Shelter, etc. ZERO building permit datasets.
- Existing city_configs.py entry (line 1537): Socrata platform with dataset 6yaw-2i7d — INVALID. data.longbeach.gov is Opendatasoft, NOT Socrata. Correctly marked as "fabricated Socrata domain."
- city_tester.py reference (a5g6-fczp as "long_beach_new"): Also invalid — Socrata resource ID on non-Socrata portal.
- Accela (aca-prod.accela.com/LONGBEACH): 404 — agency does not exist
- Accela (aca-prod.accela.com/CLB): 404 — agency does not exist
- Accela (aca-prod.accela.com/COLB): 404 — agency does not exist
- ArcGIS Hub (longbeach.maps.arcgis.com): Portal exists but search returns only federated results from other cities (Calgary, San Marcos TX, Tempe, etc.). Zero Long Beach-specific building permit datasets.
- ArcGIS Server (gis.longbeach.gov/arcgis/rest/services): 404 — server does not exist
- LA County Socrata (data.lacounty.gov): No Long Beach building permit datasets. LA County permits cover unincorporated areas only; Long Beach is incorporated and handles its own permitting.
- RecordsLB (longbeach.gov/openlb/recordslb → citydocs.longbeach.gov/WebLink8): Laserfiche WebLink document management system. HTML address search only, no API.
- Online Permitting (longbeach.gov/lbcd/building/permit-center/online/): Proprietary "Customer Portal" with PIN-based contractor access. No commercial permitting software identified (not Accela, not EnerGov, not Tyler).
- No Socrata portal (data.longbeach.gov is Opendatasoft)
- No CKAN portal
- California state data (data.ca.gov): No Long Beach building permit datasets

**Verdict:** Long Beach uses a proprietary Customer Portal for online permitting and Laserfiche WebLink for records. data.longbeach.gov is Opendatasoft with zero permit datasets. No Accela (3 slugs tried), no ArcGIS FeatureServers, no Socrata, no CKAN, no county-level data. Cannot be activated.

---

### 11. Tulsa, OK (pop 413,066) — EXHAUSTED (re-verified 2026-03-31)
**Portals tried (original V26 + re-verification):**
- Socrata (data.tulsaok.gov): Dead. Socrata discovery API returns zero datasets for this domain. DNS may still resolve but no Socrata instance.
- ArcGIS Hub (gis2-cityoftulsa.opendata.arcgis.com): Alive, but search for "permit" returns zero results. Still no building permit datasets.
- Tyler EnerGov (tulsaok-energovweb.tylerhost.net/apps/selfservice): Self-service portal alive ("SelfService Public Site"). REST API endpoints tested: `/api/energov/search/search` (POST) returns 500 "An error has occurred"; `/api/energov/permit/search` and `/api/energov/search/permits` both return 404. Web-only, no public API, exports capped at 1000 records per V26 notes.
- Accela (aca-prod.accela.com/TULSA): 404 — agency does not exist
- Open Tulsa (cityoftulsa.org/opentulsa): City open data initiative page. Links to ArcGIS Hub (above). No new permit datasets added.
- University of Tulsa blog (July 2025): Confirms researchers had to manually aggregate data from the self-service portal — no API available.
- City was expected to launch online permit map by late 2025 — no evidence this has happened as of March 2026.
- No CKAN portal
- No new Socrata portal
- Oklahoma state open data: No Tulsa-specific building permit datasets

**Verdict:** Tulsa uses Tyler EnerGov self-service portal with no public API. Socrata is dead, ArcGIS Hub has no permit data, no Accela. The expected late-2025 permit map has not materialized. Cannot be activated without a Tyler EnerGov scraper.

## ACCELA CONFIGURED — Needs Deploy & Test

### 12. Wichita, KS (pop 397,532) — ACCELA CONFIGURED, NEEDS DEPLOY
**Portals tried:**
- Socrata (data.wichita.gov): Dead/fabricated. Socrata discovery API returns 0 results. Domain unreachable (Failed to fetch).
- opendata.wichita.gov: Domain responds (opaque/no-cors) but no open data portal with permit datasets found.
- ArcGIS Hub: Zero results for "wichita building permit" on hub.arcgis.com search.
- ArcGIS OpenData: Zero results filtered for Wichita.
- GIS server (gis.wichita.gov/arcgis): Unreachable/error.
- Sedgwick County Socrata (data.sedgwickcounty.org): Zero permit datasets.
- CKAN: No CKAN portal found.
- **Accela (aca-prod.accela.com/WICHITA): LIVE!** Title "Accela Citizen Access". Two modules: Engineering (labeled "Permitting" — handles building permits) and Licenses (labeled "Cement Test and Bond Registrations"). Engineering module is the target.

**Config changes made:**
- accela_scraper.py: Added `wichita` to ACCELA_CONFIGS (agency_code=WICHITA, module=Engineering, tab_name=Engineering)
- city_configs.py: Updated from fabricated Socrata to platform=accela, endpoint=aca-prod.accela.com/WICHITA, agency_code=WICHITA, active=False

**Verdict:** Accela is the only viable source. Needs code push to GitHub → auto-deploy → test scraper → backfill → activate.

---

## SERVER-BLOCKED — Data Exists But Inaccessible From Render

### 13. Tampa, FL (pop 384,959) — SERVER-BLOCKED
**Portals tried:**
- ArcGIS self-hosted (arcgis.tampagov.net/arcgis/rest/services/Planning/PermitsAll/FeatureServer/0): DATA IS FRESH — 2,514 records, newest created 2026-03-24. Has CREATEDDATE, LASTUPDATE, RECORD_ID, RECORDTYPE, ADDRESS, PROJECTSTATUS, PROJECTDESCRIPTION. **BUT returns 403 Forbidden from Render server.** Browser can access it fine — server blocks non-browser requests (likely Referer/Origin/IP check).
- ArcGIS Online (services.arcgis.com/apTfC6SUmnNfnxuF): Has Res_Comm_Permits and TTCPermitsV4 services but both return "Invalid URL" errors.
- Socrata (data.tampagov.net): Domain unreachable. Zero results in discovery API.
- ArcGIS Hub: Zero results for Tampa permits.
- Accela (aca-prod.accela.com): Tried TAMPA, COT, TAMPAGOV, TAMPAFL, CITYOFTAMPA — all 404/error. Data shows `CREATED: "Accela"` indicating Tampa uses Accela internally but no public citizen access portal.
- Accela custom domains (aca.tampagov.net, permits.tampagov.net, etc.): All unreachable.
- Hillsborough County Socrata: Zero permit datasets.
- Hillsborough County ArcGIS: No permit services found.
- Hillsborough County Accela (HILLSBOROUGHFL): Error/unreachable.
- CKAN: No portal found.
- data.tampagov.net: Domain unreachable.

**Config changes made:**
- city_configs.py: Fixed date_field from OBJECTID to CREATEDDATE, added RECORDTYPE and filing_date mappings. Config is correct IF server-access issue can be resolved.

**Verdict:** Fresh data exists on self-hosted ArcGIS but server blocks Render. No alternative portals. Needs proxy solution or custom User-Agent/Referer headers to bypass 403. Config improvements saved for when access is resolved.

---

## IN PROGRESS

(working next city below)
