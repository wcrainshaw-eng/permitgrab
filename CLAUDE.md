# CLAUDE.md — PermitGrab Autonomous Agent Instructions

## NORTH STAR

PermitGrab sells contractor lead lists to home service companies. A contractor pulls a permit → we capture their name and phone → we sell that lead for $149/mo. The product is a web app at permitgrab.com deployed on Render.

**The goal: 10+ ad-ready cities for Google Ads launch.**

A city is "ad-ready" when it has ALL THREE:
1. **Profiles** (>100 contractor_profiles with real business names)
2. **Phones** (>50 profiles with phone numbers)
3. **Violations** (>0 code enforcement violation records)

**Current state (2026-04-23, post-V250 merges):**
- **Ad-ready (6):** Chicago (3,494 phones), NYC (466), Phoenix (1,079), San Jose (95), Miami-Dade (245), **Orlando** (57 — new this cycle, FL DBPR 2nd-import lift)
- **Near-miss — phones gap:** Cape Coral (44, need 6), Fort Lauderdale (22), Columbus (12), Buffalo (15)
- **Near-miss — violation gap:** Hialeah (88 phones, 0 violations — confirmed dead-end below), San Antonio (3,828 phones, no violations — dead-end)
- **FL DBPR 2nd import landed:** Miami-Dade 181→245, Hialeah 64→88, Orlando 39→57, Cape Coral 24→44, Fort Lauderdale 17→22
- **Visual UAT green** (61/0, V248 Puppeteer) post-V247/V248/V249/V250-P0/P1D deploys
- ~98 cities actively collecting permits

### Known dead ends for new-city onboarding (don't re-research)
- **Washington DC** (maps2.dcgis.dc.gov FeatureServer 18): PERMIT_APPLICANT field is individual names (e.g. "KENNETH BEECHNER"), not business names. 14,187 total permits, only 50% have any applicant, and those aren't contractors. Owner field also individuals. No separate contractor field.
- **Boston** (data.boston.gov CKAN, resource 6ddcd912-32a0-43df-9908-63574f8c7e77): 722k permits, daily updates, rich fields (address/zip/declared_valuation/status/worktype). But `applicant` is individual licensee names ("Iliya Iliev", "paul roper", "HomeWorks Energy" occasional business) same pattern as DC. Municipal permit-pull convention: licensed individual files, business name not captured at the permit record. Would need a MA state licensing DB join (license_enrichment.py pattern) to map individual → business — deferred.
- **Louisville Active Permits** — ~~previously frozen 2019~~ **RE-VERIFIED 2026-04-24: LIVE AGAIN.** newest ISSUE_DATE = 2026-04-20. services1.arcgis.com/79kfd2K6fskCAkyg/active_construction_permits/0 is publishing current data with CONTRACTOR field populated ("HOWELL CONSTRUCTION", "HDDS INC"). **BUT:** Louisville KY permits are currently stored under slug `louisville-co` (Colorado) — 4,362 permits, 576 with contractor, 360 profiles, 0 phones. Slug routing bug needs fixing before this city can ship.
- **Baltimore Permits** (egisdata.baltimorecity.gov FS/3): No contractor field at all. Fields are OBJECTID, CaseNumber, Description, Address, BLOCKLOT, ExistingUse, ProposedUse, Cost. Owner/applicant not exposed.
- **Oklahoma City** (data.okc.gov): Blocked by Incapsula WAF — can't probe programmatically.
- **Hialeah violations:** No public ArcGIS/Socrata feed. Miami-Dade county violations return 0 Hialeah addresses (CCVIOL_gdb is unincorporated-only).

### Known dead ends for phone enrichment (don't re-invest effort)
- **Cape Coral contractor_name_raw is dominated by homeowner names** ("OWNER BUILDER", "PEDRO CHAVEZ", "WADE WILLIAMSON"), not business names. Only 44 profiles have phones out of 1,751, and those matched DBPR by coincidence (licensed individuals who share a homeowner's name). Conclusion: Cape Coral permit data has no real contractor names — DBPR can't lift phones meaningfully. Compare Miami-Dade contractor_name_raw which is real businesses ("STRADA SERVICES INC", "ARNOLD J ELECTRIC INC"). Cape Coral is structurally a poor fit for the $149/mo lead product regardless of enrichment investment.

### V258 structural dead ends — permits collect, but no contractor field in the source API
- **Los Angeles** (data.lacity.org): CURRENT pi9x-tg5x has no contractor field. Probed 3 alternates 2026-04-24 — hbkd-qubn (Electrical Permits), xnhu-aczu (LA BUILD PERMITS), 6q2s-9pnn (Building Permits Feb'15-Present) — all have `contractors_business_name` BUT all datasets frozen at 2023-05-19. LA city data portal stopped publishing permits ~May 2023 across all 30 permit datasets. Dead.
- **San Francisco** (data.sfgov.org i98e-djp9): 11,254 permits, 0 profiles. Only `application_submission_method` is remotely contractor-shaped. Building Inspection Commission dataset doesn't expose applicant identity.
- **Seattle** (data.seattle.gov ht3q-kdvx): 1,159 permits, 30 profiles. Zero contractor-like fields in the Socrata response.
- **Denver** (denvergov.org opendata): 3,409 permits, 505 profiles (some extraction working, source unclear), 0 violations, 0 phones. CO has no state licensing DB, DDG-only path.
- **Sacramento** (data.cityofsacramento.org): CKAN portal returns 404 for package_search — no public data portal detected 2026-04-24.
- **Fresno** (fresno.gov): WAF 403 blocks programmatic probes.
- **Oakland** (data.oaklandca.gov): catalog API returned Chicago's ydr8-5enu dataset (mirroring Socrata globally, not city-specific data) — no local permits available.
- **Dallas** (www.dallasopendata.com e7gq-4sah): HAS `contractor` field with name+address+phone inline (e.g. "RELIANT HEATING A/C ... (817) 616-0620"). BUT newest permit is 2020-08-29 (zero permits starting with 2025/2026). Socrata metadata says "daily automated updates, updated 2024-01-10" but data is frozen. Probed 2026-04-24. Dead.
- **Charlotte NC** (data.charlottenc.gov): ArcGIS Hub DCAT feed contains no building/construction permit datasets — only transportation, stormwater, emergency services. Dead.
- **Indianapolis IN** (data.indy.gov): Open-data catalog has 47 datasets (boundaries, addresses, transportation) — no permits. Dead.
- **San Diego CA**: seshat.datasd.org blocks programmatic requests with 403; maps.sandiego.gov ArcGIS root ECONNREFUSED. Double-confirmed dead via two paths.
- **Kansas City MO** (data.kcmo.org): ntw8-aacc Permits-CPD Dataset has `contractorcompanyname` with real businesses ("Permit Service Inc.", "Garrison Plumbing") but newest is 2025-05-09 (~11 months stale). Companion 6h9j-mu65 ("CPD Permits Status Change") freezes at 2024-10-10 and has no contractor field. Dead.
- **Atlanta GA** (opendata.atlantaga.gov): DCAT feed ECONNREFUSED, search API returns TLS cert-name-invalid, ArcGIS hub returns empty JS-rendered pages. No accessible API path found 2026-04-24.
- **Detroit MI** (data.detroitmi.gov): Not indexed at api.us.socrata.com federated catalog (404) — host isn't a Socrata portal.
- **Las Vegas NV** (opendata.lasvegasnevada.gov): ECONNREFUSED on search path.
- **Nashville TN** (data.nashville.gov): Socrata catalog API returns 404; portal browse page requires JS rendering — can't enumerate datasets via WebFetch.
- **Memphis TN**: 0 matches in ArcGIS Hub search. Already known dead via Accela (CLAUDE.md P1 — no contractor column in grid).
- **Tampa FL**: 0 Tampa-specific datasets in ArcGIS Hub. Accela dead-end noted.
- **Sacramento CA** (SACOG): Only a yearly housing-units summary at services.sacog.org — no individual-permit dataset with contractor info.
- **Memphis TN, Tampa FL, Madison WI, Cincinnati OH, Colorado Springs CO, Birmingham AL, Huntsville AL, Chesapeake VA, Long Beach CA, Stockton CA, Virginia Beach VA** — all probed 2026-04-24 via ArcGIS Hub and/or direct endpoints. None expose a city-level building-permit feed with a contractor-name field. Top 100 by population is effectively exhausted via open portals at this point; remaining cities ship permit data through paid vendors (Accela, Viewpoint), state licensing DBs, or not at all.
- **Naperville IL** (`Building_Permit_Contractors` FeatureServer, services1.arcgis.com/rXJ6QApc2sOtl1Pd): rich schema with `Contractor_Name`, `Business_Phone`, `Email`, `CONTRACTOR_ADDRESS`, `Contractor_Type`. BUT newest ISSUEDATE is 2024-02-02 — 14 months stale. Dataset was apparently a one-time snapshot. Dead by freshness rule.
- **Bend OR** (`Permits_and_Contractors_Table`): six contractor-typed columns (ContractorName, GeneralContractorName, ElectricalContractorName, etc.), but first 2 sample records have all fields null. Would need further sampling — low population priority.

### V258 new-city wins 2026-04-24
- **Philadelphia** — already in CITY_REGISTRY, phl.carto.com, fresh 2026-04-22. 1,253 real-business profiles, 11 phones, 7,270 violations. DDG enrichment fired (job `da6d8fd1613e`). No bulk PA phone source — DDG-only.
- **Henderson NV** — PR #157 migrated from dead Socrata (fpc9-568j) to ArcGIS OpenDevPermits/2 at maps.cityofhenderson.com. Fresh (2026-04-18) with `OWNER` (business names like "JOHNNY RIBEIRO BUILDER, LLC") + `BUSINESSPHONE` (inline — "7022927679"). Phones come with the permit → skip enrichment entirely.
- **Cleveland OH** — PR #158 migrated from stale Building_Permits/0 (frozen 2025-04-14) to Project_Records/0 at services3.arcgis.com. Fresh (2026-04-19) with `APPLICANT_BUSINESS` populated ("Fischer & Associates Architects Inc.", "Northedge Steel LLC"). Violations source (CCVIOL) already wired.

### V258 TODO: CKAN violation collector support
- Pittsburgh has a fresh violations feed at WPRDC: resource_id `70c06278-92c5-4040-ab28-17671866f81c` ("Pittsburgh PLI/DOMI/ES Violations Report", updated 2026-04-23 daily). Fields: `casefile_number`, `investigation_date`, `address`, `investigation_findings`, `case_file_type`, `status`. BUT `violation_collector.collect_violations_from_endpoint` only handles Socrata SODA + Carto + ArcGIS. CKAN's `datastore_search_sql` endpoint needs a 3rd-platform branch (~30 lines).
- Pittsburgh also needs phones (PA no-bulk, DDG-only). Wire CKAN violations + find an alternate phone approach to make Pittsburgh a real candidate.

### V258 activation bugs to fix
- **Louisville KY** routing: current config slug `louisville` + Kentucky state yields `louisville-co` (Colorado) at DB write time. 4,362 permits/360 profiles with KY business names are mis-attributed. Need a slug-routing fix so these become discoverable under a `louisville-ky` slug. Re-verified data is LIVE as of 2026-04-20.
- **Worcester MA** collector: source has `Contractor_Name` populated (probed: "Aparicio Kitchen Designs INC", "IVAN PITTAMIGLIO BENITEZ") but all 6,041 stored permits have `contractor_name=""`. Field_map is correct; ArcGIS collector path is dropping the field. Root cause TBD.

### V258 already-configured — activation pending
- **Philadelphia** (phl.carto.com/permits, platform=carto): 11,875 permits (newest 2026-04-22 FRESH), 1,253 real-business profiles ("Hormigon LLC", "Ricco Construction Corp"), 11 phones, 7,270 violations (newest 2026-04-22 FRESH). Only gap: phone count. DDG enrichment fired job `da6d8fd1613e` 2026-04-24. PA has no bulk state license DB → DDG-only.
- **Pittsburgh** (data.wprdc.org CKAN, dataset f4d1177a-f597-4c32-8cbf-7885f56253f6): 2,045 permits (newest 2026-04-10), 2,002 have `contractor_name` with real businesses ("Phillips Heating & Air Conditioning, Inc.", "Johnson Controls Fire Protection LP"), but contractor_profiles=0 because profile-build step never ran for this slug. TODO: `POST /api/admin/refresh-profiles?city=pittsburgh` after current enrichment jobs drain the sqlite write lock — then fire enrichment on the newly-created profiles.

**New Orleans — partial.** PR #150 switched `new_orleans_la` endpoint from `nbcf-m6c2` (metadata-only) to `rcm3-fn58` (has `contractors` field). 3,489 historical profiles exist. Sample of recent profiles (2026-04-24) shows mixed individual licensee names ("Christopher Jake Laborde", "Joan Brooks") and real businesses ("LGD Lawn, LLC", "Legacy Construction and Design LLC") — roughly 40-50% business-suffixed. DDG hit rate on first 5 profiles of async enrichment was 0/5 not_found. Louisiana has no bulk state license DB (paid-only). Not a structural dead-end but conversion will be slow; plan for 20-40 phones after a full enrichment pass, not 50+.

**Takeaway:** All 6 California cities in V258 Tier 1-3 except the already-live ones lack a usable contractor-field permit API. CSLB name-matching is wired but there are no names to match. California's municipal permit data is fundamentally a metadata-only story across major cities.

### V289 new-city win 2026-04-24
- **Anaheim CA** — config existed in CITY_REGISTRY but endpoint was dead (`gis.anaheim.net/server`). V289 migrated to `services3.arcgis.com/hPs600I3X0RTaaaq/arcgis/rest/services/Accela_Building_Permits/FeatureServer/0`. Probed: 189K total, 1,580 in last 90 days, **8,553 records have `contractorsphone` populated inline** (field_map now has `contact_phone: contractorsphone` — no DDG needed). Violations wired from same org: `CodeEnforcementCasesPublic/FeatureServer/0` (168K cases, daily refresh, top record 2026-04-24 "Unpermitted Construction"). Awaiting backfill verification.

### V290 dead-ends confirmed 2026-04-24 (Top 101-200 hunt)
- **Durham NC** (`services2.arcgis.com/G5vR3cOjh6g2Ed8E/arcgis/rest/services/Permits/FeatureServer/13`): daily refresh, but 24-field schema exposes only INSPECTOR names (BLDG_FNAME/BLDG_LNAME paired with BLDG_INSP). No contractor/applicant/business field anywhere across the 4 permit layers (Building/Electrical/Mechanical/Plumbing).
- **Boise ID** (`services1.arcgis.com/WHM6qC35aMtyAAlN/arcgis/rest/services/Housing_OpenData/FeatureServer/0`): 26-field schema is pure property-address + status. No contractor field.
- **Irving TX** (data-cityofirving DCAT): 14 datasets with "permit" in name but newest permit dataset is ~1yr stale ("Feb 15 2022 Through Present" mod=2025-03-12). Code Violations datasets frozen at 2021 data. Dead by freshness.
- **Gilbert AZ** (data.gilbertaz.gov): Single "Permits" dataset modified 2023-01-26. 3+ years stale. Dead.
- **Tyler TX** (services5.arcgis.com/RmXXW3PwBZGOxlSe): `CONTRACTOR_NAME` field exists with real businesses ("BAILEY ELECTRIC") but zero records with ISSUED >= 2026-01-01. Dataset metadata was updated but record volume frozen at 2021. Dead by freshness.
- **Fremont/Irvine/Chula Vista/Bakersfield/Anaheim-alt/Plano/Garland/Arlington TX/Winston-Salem/Fayetteville NC/Chandler AZ/Peoria AZ/Fort Wayne IN/Spokane WA/Moreno Valley/Oxnard/Fontana/Huntington Beach/Oceanside/Santa Clarita/Overland Park KS/Newport News VA/Jersey City NJ/Tacoma WA/SLC UT/Knoxville TN/Providence RI/Grand Rapids MI/Akron OH/Des Moines IA/Anchorage AK/St Louis/Milwaukee/Baltimore/Wichita** — tried ArcGIS Hub DCAT + direct Socrata catalogs across ~40 host patterns each. None expose a permit feed with a contractor-name field via OPEN portals. Either on Accela/Viewpoint (paid scraping), or state-licensing-DB only, or no digital portal.

### V315 dead-end confirmed 2026-04-24 (Aurora CO)
- **Aurora CO** (ags.auroragov.org/aurora/rest/services/OpenData/MapServer/156): Building Permits 6 Months feed has 7,546 records (fresh) but the 43-field schema exposes ZERO contractor / applicant / business / company / firm fields. Just FolderRSN/Type/Desc, property/address fields, dates, valuation. Same structural pattern as DC/Boston — public permit feed without licensee identity. Has a usable violations feed at MapServer/161 (3,484 records, 6mo) but we don't onboard violation-only cities. Dead for contractor product.

### V314 dead-end confirmed 2026-04-24 (Tempe AZ)
- **Tempe AZ** (services.arcgis.com/lQySeXwbBg53XWDi/.../building_permits/FeatureServer/0): schema exposes ContractorCompanyName + ContractorPhone + ContractorEmail + ContractorLicNum, BUT Tempe stopped populating these fields years ago. Histogram by year (probed 2026-04-24): 2022=0, 2023=1, 2024=1, 2025=5, 2026=0 records with ContractorPhone. The 913 historical records with phones are all pre-2022. V314 wired the field_map correctly but it's a no-op for current data. Don't re-attempt — Tempe is structurally dead for the contractor lead product despite a "rich" schema.

### V290 crisis + recovery (site down ~35 min)
- **V287 Sentry integration** caused gunicorn worker hang: module-level `import sentry_sdk` + `FlaskIntegration` deadlocked the single Render worker. Site returned 502/HTTP 000 from 16:27Z to ~17:05Z even though SENTRY_DSN env var was unset (defensive gating didn't help).
- **V288 revert PR** #192 was opened but `gh pr merge --squash` produced an EMPTY commit — GitHub's squash detected the revert as redundant with the targeted V287 and dropped the payload. `git show --numstat e8faa05` returns zero files.
- **V290** #194 directly deletes the Sentry block from server.py + sentry-sdk[flask] from requirements.txt, bypassing the revert-squash path. Fix confirmed locally with grep.
- **Lesson:** When reverting a merged PR via squash-merge, the GitHub squash engine can silently drop the revert payload. Prefer `git revert` → push → merge without `--squash`, or use a direct-edit PR when under duress.

**You are autonomous. Don't stop to ask permission. Fix things, test things, deploy things. If something breaks, debug and fix it. Write clean PRs with descriptive titles.**

---

## ARCHITECTURE

### Data Flow
```
External APIs (Socrata/ArcGIS/Accela/CKAN)
    → collector daemon (runs every ~30min)
    → permits table (raw permit records)
    → contractor_profiles (deduplicated by business name per city)
    → enrichment (DDG web search adds phone numbers)
    → product pages at permitgrab.com/permits/{city-slug}
```

### Key Tables
- **prod_cities**: What the daemon collects. `status='active'` means collecting. Has source_id, source_type.
- **permits**: Raw permit data. Key columns: source_city_key, contractor_name, date, address, permit_type
- **contractor_profiles**: Deduplicated contractors. Key columns: source_city_key, business_name, phone, trade_category
- **violations**: Code enforcement violations. Key columns: source_city_key, case_number, date, address, description
- **scraper_runs**: Collection logs. status, error_message, permits_found, permits_inserted, run_started_at

### Slug Format
- prod_cities.city_slug and permits.source_city_key use hyphen format: `miami-dade-county`, `orlando-fl`, `buffalo-ny`
- prod_cities.source_id uses underscore format: `miami_dade_county`, `orlando_fl`
- ALWAYS query the DB for exact slug before assuming: `SELECT city_slug FROM prod_cities WHERE city_slug LIKE '%miami%'`

### Config Chain
The daemon checks in order: prod_cities → city_sources → CITY_REGISTRY dict in city_configs.py. For new cities, add to CITY_REGISTRY in city_configs.py.

### Daemon
- Does NOT auto-start on deploy. Must call: `POST /api/admin/start-collectors`
- Runs as background thread in Flask process
- Pauses during imports via IMPORT_IN_PROGRESS flag
- Collects all active cities every ~30min

---

## ADMIN API

**Base URL:** https://permitgrab.com
**Auth Header:** `X-Admin-Key: 122f635f639857bd9296150ba2e64419`

### Endpoints
| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | /api/admin/health | NO | Daemon status, error counts, fresh city count |
| POST | /api/admin/query | YES | Run SELECT queries. Body: `{"sql":"..."}` |
| POST | /api/admin/force-collection | YES | Trigger full collection cycle |
| POST | /api/admin/test-and-backfill | YES | Test a city endpoint + backfill data |
| POST | /api/admin/license-import | YES | Trigger state license import. Body: `{"state":"FL"}` |
| POST | /api/admin/start-collectors | YES | Start the daemon thread |

**CRITICAL:** The query endpoint is SELECT ONLY. It rejects UPDATE/INSERT/DELETE. It also blocks column names containing "INSERT" or "CREATE" — use aliases.

### Testing Endpoints from Code
**SSH into Render** for all external endpoint testing. Code's sandbox proxy blocks outbound HTTPS:
```bash
RENDER_SSH="srv-d6s1tvsr85hc73em9ch0@ssh.oregon.render.com"
ssh -T $RENDER_SSH 'curl -s "https://example.com/resource/id.json?\$limit=3"'
```
Never trust `curl` results from the local Code sandbox. They go through a proxy that returns 403 for everything.

---

## CITY ONBOARDING PIPELINE

**NEVER use auto-discovery. NEVER use catalog APIs. NEVER use state-level datasets. One city at a time, manual research.**

### Step 1: Research
Google "[city name] open data portal building permits" and find the API:
- **Socrata**: Look for `data.[city].gov/resource/[id].json` URLs
- **ArcGIS**: Look for `*/arcgis/rest/services/*/FeatureServer/*` or `MapServer/*` URLs
- **CKAN**: Look for `data.[city].gov/api/3/action/datastore_search` URLs
- **Accela**: Accela ACA portals scrape HTML — only add if the grid has contractor columns

### Step 2: Test the Endpoint (via SSH)
```bash
# Socrata
ssh -T $RENDER_SSH 'curl -s "https://data.example.gov/resource/XXXX.json?\$limit=3&\$order=date_field+DESC"'

# ArcGIS FeatureServer
ssh -T $RENDER_SSH 'curl -s "https://example.com/arcgis/rest/services/.../FeatureServer/0/query?where=1%3D1&outFields=*&resultRecordCount=3&orderByFields=date_field+DESC&f=json"'

# ArcGIS MapServer (MUST include returnGeometry=false)
ssh -T $RENDER_SSH 'curl -s "https://example.com/arcgis/rest/services/.../MapServer/0/query?where=1%3D1&outFields=*&returnGeometry=false&resultRecordCount=3&orderByFields=date_field+DESC&f=json"'
```

### Step 3: CHECK FRESHNESS (CRITICAL)
**Record count means NOTHING. Check the newest record date.**
- If newest record is >30 days old → SKIP, endpoint is dead
- If newest record is from this week → PROCEED
- 300K records from 2015 = worthless. 50 records from yesterday = gold.

### Step 4: Check for Contractor Names
The response MUST have a field containing real business names (not license numbers, not "N/A", not blank). Without contractor names → no profiles → city is useless.

### Step 5: Add Configuration
Add to CITY_REGISTRY in city_configs.py with:
- Correct slug (query DB first)
- source_type matching the platform
- field_map mapping source fields to standard fields (contractor_name, date, address, permit_type, etc.)
- date_field for freshness queries

### Step 6: Backfill + Verify
After deploy, trigger collection and verify:
```sql
SELECT source_city_key, COUNT(*) as permits, COUNT(contractor_name) as with_name, MAX(date) as newest
FROM permits WHERE source_city_key = 'new-city-slug'
GROUP BY source_city_key
```
If permits > 0 AND with_name > 0 AND newest is recent → success.

---

## DATA QUALITY RULES

### What to Check For (run these regularly)
```sql
-- Garbage profiles (numeric-only names, single characters, common junk)
SELECT source_city_key, business_name, COUNT(*) FROM contractor_profiles
WHERE business_name ~ '^[0-9]+$'
   OR LENGTH(business_name) < 3
   OR business_name IN ('N/A', 'NA', 'NONE', 'TEST', 'OWNER', 'SELF')
GROUP BY source_city_key, business_name
ORDER BY COUNT(*) DESC LIMIT 20

-- Duplicate profiles
SELECT source_city_key, business_name, COUNT(*) as dupes
FROM contractor_profiles
GROUP BY source_city_key, business_name
HAVING COUNT(*) > 1
ORDER BY dupes DESC LIMIT 20

-- Cities with permits but no profiles (broken contractor_name extraction)
SELECT p.source_city_key, COUNT(*) as permits, COUNT(p.contractor_name) as with_name
FROM permits p
LEFT JOIN contractor_profiles cp ON p.source_city_key = cp.source_city_key
WHERE cp.source_city_key IS NULL
GROUP BY p.source_city_key
HAVING COUNT(*) > 100
ORDER BY permits DESC

-- Stale cities (active but no new data in 7+ days)
SELECT p.source_city_key, MAX(p.date) as newest
FROM permits p
INNER JOIN prod_cities pc ON p.source_city_key = pc.city_slug
WHERE pc.status = 'active'
GROUP BY p.source_city_key
HAVING MAX(p.date) < date('now', '-7 days')
ORDER BY newest ASC
```

### What Breaks (Common Failure Modes)
1. **field_map wrong**: contractor_name maps to wrong source field → permits collected with NULL contractor → 0 profiles. Fix: check raw API response, find the right field, update field_map.
2. **Column position mismatch**: CSV imports (FL DBPR) use positional columns. If CSV layout changes → wrong data in every field. Fix: download CSV, check actual headers, align column lists.
3. **Slug mismatch**: Using `buffalo` instead of `buffalo-ny` → data goes nowhere. Fix: always query DB for exact slug.
4. **Date field wrong**: Freshness queries return nothing → city appears stale. Fix: check which field the API actually uses for dates.
5. **MapServer without returnGeometry=false**: Returns 400 errors or huge GeoJSON responses. Fix: add returnGeometry=false to all MapServer queries.

---

## PHONE ENRICHMENT

### State License Imports (highest-yield source)
Only 4 states have phone numbers in bulk downloads:
| State | Source | Format | Status |
|-------|--------|--------|--------|
| FL DBPR | myfloridalicense.com CSV (3 files: applicants, certified, registered) | Positional CSV | BROKEN — column positions misaligned. V244d fix pending. |
| MN DLI | Socrata at data.mn.gov | SODA API | Working. +154 phones for Minneapolis |
| NY DOL | Socrata at data.ny.gov | SODA API | Working. +9 phones for Buffalo (51min lock bug fixed) |
| WA L&I | Socrata m8qx-ubtq at data.wa.gov | SODA API | Working. +4 phones for Seattle |
| CA CSLB | CSV download from cslb.ca.gov | CSV (no phone field) | Matches by name only. +95 for San Jose |
| AZ ROC | CSV at roc.az.gov/posting-list | CSV (no phone field) | 57K records. No phone in bulk. Individual search has phones but no bulk export |

**States with NO usable bulk phone data:** IL, OH, PA, TN, NV, LA (paid only)

### DDG Web Search Enrichment
The enrichment module uses DuckDuckGo + selectolax to search for contractor phone numbers. Typical yield: 1-5% of profiles get phones. This is the fallback when state license DBs don't have phones.

### The #1 Enrichment Blocker
FL DBPR covers 6+ cities (Miami-Dade, Orlando, Tampa, Hialeah, St. Petersburg, Cape Coral, Fort Lauderdale, Jacksonville). Getting this single import working adds hundreds of phones to each city. This is the highest-leverage fix in the entire system.

---

## VIOLATION SOURCES

### Active (configured and collecting)
| City | Type | Endpoint/Resource |
|------|------|-------------------|
| NYC HPD | Socrata | wvxf-dwi5 at data.cityofnewyork.us |
| NYC DOB | Socrata | 3h2n-5cm9 at data.cityofnewyork.us |
| Chicago | Socrata | 22u3-xenr at data.cityofchicago.org |
| Austin | Socrata | 6wtj-zbtb at data.austintexas.gov |
| LA (open) | Socrata | u82d-eh7z at data.lacity.org |
| LA (closed) | Socrata | rken-a55j at data.lacity.org |
| Orlando | Socrata | k6e8-nw6w at data.cityoforlando.net |
| St. Pete | Socrata | tmdq-gg7f at stat.stpete.org |
| Mesa | Socrata | hgf6-yenu at data.mesaaz.gov |
| Miami-Dade | ArcGIS | services.arcgis.com/8Pc9XBTAsYuxx9Ny/.../CCVIOL_gdb/FeatureServer/0 |
| Phoenix | ArcGIS MapServer | maps.phoenix.gov/pub/rest/services/Public/NSD_Property_Maintenance/MapServer/0 |
| Cape Coral | ArcGIS MapServer | capeims.capecoral.gov/.../OpenData/MapServer/5 |
| Cleveland | ArcGIS FeatureServer | services3.arcgis.com/dty2kHktVXHrqO8i/... |
| Fort Lauderdale | ArcGIS MapServer | gis.fortlauderdale.gov/arcgis/rest/services/... |
| Denver | ArcGIS FeatureServer TABLE | services1.arcgis.com/zdB7qR0BtYrg0Xpl/.../ODC_service_requests_311/FeatureServer/66 (V259) — Agency=CPD + Case_Summary whitelist |
| San Antonio | ArcGIS FeatureServer | services.arcgis.com/g1fRTDLeMgspWrYp/.../311_All_Service_Calls/FeatureServer/0 (V259) — ReasonName=Code Enforcement + TypeName whitelist |

### Known dead ends for violations
San Diego (pre-2018 archive, live only via Accela scrape), Dallas (Socrata archives frozen 2018, no live ArcGIS feature service), Houston (CKAN Excel-only), Minneapolis (Tableau dashboard only — no REST feed), Henderson NV (ComDevServices/0 has CE Violations but ~80 building-relevant of 871; rest is STVR/parking/camping nuisance — low lead-gen value, skip until needed), **Las Vegas NV** (V313 2026-04-24: data.lasvegasnevada.gov TCP-times-out from Render egress; api.us.socrata.com federated search returns 0 LV datasets — LV not on Socrata at all; Clark County maps.clarkcountynv.gov has a CodeEnforcement folder but it lists empty; PW/Complaints8 is development-services-review workflow, not code enforcement; ArcGIS Hub at opendata-lasvegas.opendata.arcgis.com has a UI but DCAT 404 + search API 401. 380 phones on LV are an ad-ready candidate but violations gap is structurally unsolvable.)

---

## SEO — REAL DATA, NOT GENERIC

### Current State (Critical)
- Only 2 out of 3,115 pages indexed by Google
- Zero organic traffic for any target keyword
- 67 blog posts exist, only 1 indexed
- Technical issues: canonical mismatches, missing H1 tags on some pages

### What Good SEO Looks Like for PermitGrab
Every city page MUST have content that is:
1. **Specific to that city** — real permit counts, real contractor counts, real trade breakdowns
2. **Useful** — not generic "building permits are important" filler
3. **Data-driven** — pull actual numbers from the DB

### How to Generate Real City Content
For each ad-ready city page, query the DB and include real stats:
```sql
-- Real permit stats for a city
SELECT permit_type, COUNT(*) as cnt FROM permits
WHERE source_city_key = '{slug}' AND date > date('now', '-90 days')
GROUP BY permit_type ORDER BY cnt DESC LIMIT 10

-- Top contractors in the city
SELECT business_name, COUNT(*) as permits FROM permits
WHERE source_city_key = '{slug}' AND contractor_name IS NOT NULL
AND date > date('now', '-180 days')
GROUP BY business_name ORDER BY permits DESC LIMIT 20

-- Trade breakdown
SELECT trade_category, COUNT(*) as cnt FROM contractor_profiles
WHERE source_city_key = '{slug}'
GROUP BY trade_category ORDER BY cnt DESC LIMIT 10
```

Use these real numbers in page content: "Chicago had 2,847 building permits filed in the last 90 days across 8,430 active contractors. The most active trades are electrical (23%), plumbing (18%), and general construction (15%)."

### Technical SEO Checklist
- Every page needs H1 with city name + "Building Permits"
- Canonical URL must match the actual URL exactly (no /chicago vs /chicago-il mismatch)
- Meta description must include city name + real data point
- Structured data (LocalBusiness schema) for each city page
- Sitemap must be submitted to Google Search Console
- Blog posts should target "[city] building permits 2026" long-tail keywords
- Internal linking between city pages and related blog posts

### What Code CAN Do for SEO
- Fix technical issues (canonicals, H1s, meta tags, structured data)
- Generate data-driven content using real DB numbers
- Verify pages render correctly (check for client-side JS rendering issues that block Googlebot)
- Submit sitemap via the sitemap.xml endpoint

### What Code CANNOT Do for SEO
- Access Google Search Console (no programmatic API available from Code)
- Build backlinks
- The scheduled Cowork task checks GSC via Chrome — that covers monitoring

---

## UAT TESTING PROCEDURES

### Before Every Deploy, Verify:

1. **Health check**: `GET /api/admin/health` returns `{"status":"healthy","daemon_running":true}`

2. **City pages load**: For each ad-ready city, check `GET /permits/{slug}` returns 200 with real data:
   ```bash
   for slug in chicago-il new-york-city phoenix-az san-antonio-tx miami-dade-county; do
     status=$(curl -s -o /dev/null -w "%{http_code}" "https://permitgrab.com/permits/$slug")
     echo "$slug: $status"
   done
   ```

3. **Data freshness**: No ad-ready city should have permits older than 7 days:
   ```sql
   SELECT source_city_key, MAX(date) as newest FROM permits
   WHERE source_city_key IN ('chicago-il','new-york-city','phoenix-az','san-antonio-tx','miami-dade-county')
   GROUP BY source_city_key
   ```

4. **Phone counts haven't regressed**:
   ```sql
   SELECT source_city_key, COUNT(*) as profiles,
     SUM(CASE WHEN phone IS NOT NULL AND phone != '' THEN 1 ELSE 0 END) as phones
   FROM contractor_profiles
   WHERE source_city_key IN ('chicago-il','new-york-city','phoenix-az','san-antonio-tx')
   GROUP BY source_city_key ORDER BY phones DESC
   ```

5. **No garbage data**:
   ```sql
   SELECT source_city_key, COUNT(*) FROM contractor_profiles
   WHERE business_name ~ '^[0-9]+$' OR LENGTH(business_name) < 3
   GROUP BY source_city_key HAVING COUNT(*) > 10
   ```

6. **Pricing page works**: `GET /pricing` returns 200, Stripe checkout loads

7. **No 500 errors in recent logs**: Check Render dashboard or `scraper_runs` for errors

### After Every Deploy:
1. Restart the daemon: `POST /api/admin/start-collectors`
2. Wait 5 minutes, then check health
3. Verify collection is happening: check `scraper_runs` for new entries
4. If FL DBPR was changed, trigger: `POST /api/admin/license-import {"state":"FL"}`

---

## KNOWN BUGS & CURRENT BLOCKERS

### P0: FL DBPR Column Position Mismatch (V244d)
- Import runs but matches 0 records. applicants_phone_indexed=2 out of ~100K.
- Root cause: STATE_CONFIGS['FL'] applicant_columns (13 fields) and licensee_columns (20 fields) don't match actual CSV layout from myfloridalicense.com
- Fix: SSH in, `head -3` each CSV, count actual columns, update the column lists
- Impact: Unlocks 6+ FL cities for phone enrichment

### P1: Tampa/Memphis Accela Scraper
- Both have permits but 0 profiles because contractor_name is NULL
- Root cause: accela_portal_collector.py scrapes HTML search grid, but the grid has NO contractor column
- Fix: Need new scraper feature to follow each permit detail link and parse contractor info from detail page
- Impact: Tampa alone is 1,778 permits

### P2: NYC Phone Count Dropped
- NYC had 4,362 phones, now showing 466. Investigate whether data was lost or re-imported.

### P3: SEO — Not Indexed
- 2/3,115 pages indexed. Need to fix technical SEO issues and wait for Googlebot.

---

## HARD-WON RULES (NEVER VIOLATE)

1. **Test endpoints via SSH, never local curl.** Code's sandbox proxy blocks everything.
2. **Check freshness, not record count.** 300K stale records = worthless.
3. **One city at a time, sequential.** No parallel discovery, no bulk operations.
4. **No auto-discovery.** No Socrata Discovery API, no ArcGIS Hub search, no state-level portals. Manual google-test-add only.
5. **No state-level datasets.** NJ failed 20+ times. State portals never work.
6. **Check DB before investigating platforms.** Query permits/profiles/scraper_runs FIRST.
7. **Full preflight before any code change.** Audit the full pipeline before writing a fix. Never discover problems one at a time.
8. **Fix immediately, don't just report.** If you find a bug, fix it in the same PR.
9. **MapServer queries MUST include returnGeometry=false.**
10. **The daemon must be restarted after every deploy** via POST /api/admin/start-collectors.
11. **Auto-merge your own PRs.** You are the reviewer, the merger, and the deployer. After `gh pr create`, immediately squash-merge with `gh pr merge --squash --delete-branch`. Never leave a PR open waiting for someone to click a button. If a merge conflicts, rebase and retry — don't stop.

---

## AUTONOMOUS OPERATION — NEVER STOP

You are not a task runner. You are the engineering team. When you finish something, you
start the next thing. When you hit a wall, you pivot. When everything is green, you BUILD.

### THE LOOP (run continuously, never exit)

#### Phase 1: KEEP THE LIGHTS ON (every session)
1. Health check → fix anything broken
2. Visual UAT → `npm run test:uat` → fix any failures
3. Data quality audit → fix garbage, stale data, regressions
4. Daemon check → restart if stopped, investigate if erroring

#### Phase 2: GROW THE DATA (every session)
5. Phone enrichment → check near-miss cities, trigger imports if needed
6. City expansion → pick next city from top 100 by population, research → test → add
7. Violation expansion → find sources for cities that have profiles but no violations
8. Backfill any cities with <6 months of data

#### Phase 3: MAKE THE PRODUCT BETTER (every session)
9. SEO with real data → update 1-2 city pages with fresh DB numbers
10. Fix any UI/UX issues found during visual UAT
11. Check for template bugs → test rendering of different city page states
12. Performance → check page load times, optimize if >3s

#### Phase 4: COMPETITIVE INTELLIGENCE (weekly)
13. Research competitors and build features they have that we don't:

**Known competitors to monitor:**
- BuildZoom (buildzoom.com) — contractor profiles, project history, ratings
- ConstructConnect (constructconnect.com) — permit data + project leads
- Dodge Construction Network — commercial project leads
- PermitUsNow — permit expediting but also has data
- Canopy (canopy.com) — permit analytics for real estate
- Reonomy — commercial property data with permits

**What to check:**
- What data do they show on city/contractor pages that we don't?
- Do they have filters we're missing? (by trade, date range, permit value)
- Do they show permit VALUES (dollar amounts)? We should too where available.
- Do they have maps? Project timelines? Contractor ratings?
- What SEO keywords are they ranking for that we should target?

**How to research (via SSH):**
```bash
ssh -T $RENDER_SSH 'curl -s "https://buildzoom.com/contractor/..." | head -200'
```
Parse their HTML to see what data fields they display. Then check if we have
that data in our permits/profiles tables — if yes, add it to our pages.

#### Phase 5: BUILD NEW FEATURES (when Phases 1-4 are green)
14. Feature ideas to implement (pick the highest-impact one):

**Tier 1 — High impact, definitely doable:**
- [ ] Permit value display — many permits have dollar amounts in the data, show them
- [ ] Date range filter — let users filter by last 30/60/90/180 days
- [ ] Trade category filter — filter contractors by electrical/plumbing/general/etc
- [ ] Contractor detail pages — /contractor/{id} with permit history, phone, violations
- [ ] CSV export — let paying users download their lead list as CSV
- [ ] Email alerts — "New permits filed in [city] this week" digest emails
- [ ] Violation cross-reference — show which contractors have code violations

**Tier 2 — Medium impact, needs research:**
- [ ] Permit value trends — "Average permit value in Chicago up 12% this quarter"
- [ ] Contractor growth signals — "This contractor pulled 3x more permits this month"
- [ ] Geographic clustering — show permit activity by zip code or neighborhood
- [ ] Competitor comparison pages — "PermitGrab vs BuildZoom: more data, better price"

**Tier 3 — Future, needs new data:**
- [ ] Property owner data (from county assessor records)
- [ ] Contractor reviews/ratings aggregation
- [ ] Project photos from permit inspections
- [ ] Insurance/bonding status from state databases

**Before building any feature:**
1. Check if the data for it already exists in our DB
2. Design the feature (template changes, new routes, queries needed)
3. Build it on a branch
4. Run visual UAT to make sure it doesn't break existing pages
5. Merge and deploy
6. Run visual UAT again post-deploy

### WHEN YOU HIT A WALL
If you can't make progress on one phase, SKIP IT and move to the next phase.
Never stop and wait. There is ALWAYS something to do:

| Wall | Pivot to |
|------|----------|
| Can't find a new city's data portal | Research violation sources for existing cities |
| Endpoint is dead/stale | Move to the next city on the list |
| Enrichment source has no phones | Try DDG web search for that city's contractors |
| Feature needs frontend JS you can't test | Build the backend/API part, note frontend TODO |
| SSH is down | Run data quality audits via admin API |
| Import is running (locked) | Work on SEO content or templates |
| Everything is green | Build a new feature from the feature list |
| Context getting long | Commit your work, write a summary in the PR, start fresh next session |

### NEVER DO THESE
- Never output "waiting for instructions" or "tasks complete"
- Never stop after one task — chain to the next immediately
- Never re-investigate a city you already know about (check CLAUDE.md first)
- Never skip visual UAT after a deploy
- Never write generic SEO content — real numbers or nothing
- Never ask Wes what to do next — decide yourself based on impact

### PRIORITY ORDER (when choosing what to work on)
1. P0 bugs (anything broken, invisible, or erroring)
2. Phone enrichment for near-miss cities (biggest conversion impact)
3. New city onboarding (grows the product)
4. Feature development (differentiates from competitors)
5. SEO improvements (drives organic traffic)
6. Code quality / refactoring (only if it unblocks something)

### SESSION STARTUP CHECKLIST
Every new session, before doing anything:
1. Read CLAUDE.md (you're doing this now)
2. Check git log for what happened in the last session
3. Run health check
4. Run `npm run test:uat`
5. Check scraper_runs for errors in the last 24h
6. Pick the highest-impact work from the phases above
7. GO

---

## SKILLS SYSTEM

You have seven skills installed in `.claude/skills/`. Read the relevant SKILL.md BEFORE performing
that type of work. Skills contain battle-tested procedures, exact SQL queries, and failure recovery.

### Available Skills

| Skill | When to Use | Location |
|-------|-------------|----------|
| city-onboarding | Adding ANY new city | .claude/skills/city-onboarding/SKILL.md |
| data-quality-audit | Daily quality checks, before deploys | .claude/skills/data-quality-audit/SKILL.md |
| seo-real-data | Writing/updating city page content | .claude/skills/seo-real-data/SKILL.md |
| enrichment-pipeline | Improving phone coverage | .claude/skills/enrichment-pipeline/SKILL.md |
| health-monitor | Health checks, incident response | .claude/skills/health-monitor/SKILL.md |
| uat-deploy | Pre-deploy validation, post-deploy checks | .claude/skills/uat-deploy/SKILL.md |
| competitive-intel | Weekly competitor research, feature-gap analysis | .claude/skills/competitive-intel/SKILL.md |

### How to Use Skills

1. Before starting a task, identify which skill applies
2. Read the SKILL.md file to get the exact procedure
3. Follow the procedure step by step — don't skip steps
4. Use the output format specified in the skill for reporting

### Autonomous Loop with Skills

Each cycle of the autonomous loop should use skills:

1. **HEALTH CHECK** → Read `health-monitor/SKILL.md`, follow the health check sequence
2. **DATA QUALITY** → Read `data-quality-audit/SKILL.md`, run the full audit
3. **ENRICHMENT** → Read `enrichment-pipeline/SKILL.md`, check phone coverage, trigger imports
4. **CITY EXPANSION** → Read `city-onboarding/SKILL.md`, onboard the next city
5. **SEO** → Read `seo-real-data/SKILL.md`, update city pages with real numbers
6. **UAT** → Read `uat-deploy/SKILL.md`, run the full checklist after any change

---

## RENDER DEPLOYMENT

- **Service**: permitgrab (web service)
- **SSH**: `ssh srv-d6s1tvsr85hc73em9ch0@ssh.oregon.render.com`
- **Memory limit**: 512MB (import jobs MUST stay under ~200MB peak)
- **Auto-deploy**: Pushes to main trigger automatic deploy
- **Logs**: Available via Render dashboard or SSH

---

## FILE STRUCTURE (key files)

- `server.py` — Flask app, admin API routes, daemon startup
- `collector.py` — Permit collection daemon, _collect_all_inner()
- `city_configs.py` — CITY_REGISTRY dict, STATE_CONFIGS, field_maps
- `license_enrichment.py` — State license imports (FL DBPR, MN DLI, etc.)
- `accela_portal_collector.py` — Accela HTML scraper
- `templates/` — Jinja2 templates for city pages, homepage, blog

---

## WHAT SUCCESS LOOKS LIKE

You wake up and:
- 15+ cities have real permit data from this week
- Each city page shows real contractor names, real permit types, real numbers
- Phone coverage is 20%+ for ad-ready cities
- No stale data, no garbage profiles, no broken pages
- New cities are being added automatically
- Google is indexing pages and organic traffic is growing
- Pricing page works, Stripe checkout works
- The site looks like a real product someone would pay $149/mo for
