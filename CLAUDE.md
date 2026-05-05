# CLAUDE.md — PermitGrab Autonomous Agent Instructions

## NORTH STAR

PermitGrab sells contractor lead lists to home service companies. A contractor pulls a permit → we capture their name and phone → we sell that lead for $149/mo. The product is a web app at permitgrab.com deployed on Render.

**The goal: 10+ ad-ready cities for Google Ads launch.**

A city is "ad-ready" when it has ALL THREE:
1. **Profiles** (>100 contractor_profiles with real business names)
2. **Phones** (>50 profiles with phone numbers)
3. **Violations** (>0 code enforcement violation records)

**Current state (2026-04-29, post-V474 assessor expansion):**
- **17 COMPLETE cities** (have permits + violations + >500 owners):
  Fort Worth (97K owners), Miami (82K), Phoenix (79K), Cincinnati (79K), Chicago (72K), Nashville (71K), Cleveland (60K), Austin (55K), Philadelphia (55K), **Mesa (38K — V474 win)**, Raleigh (20K), **Scottsdale (17K — V474 win)**, NYC (13K), Columbus (5K), San Antonio (5K), Buffalo (2K), **Orlando (531 — V474 win)**.
- **V474 session adds (~851K new property_owners rows):**
  - fl_statewide: 134K rows (Orlando + Jacksonville + St Pete + Hialeah + Tampa + Cape Coral + Fort Lauderdale via single source)
  - washoe_reno: 178K, pima_tucson: 156K, dane_madison: 186K
  - maricopa_secondary: 164K (filtered to JURISDICTION IN Mesa/Glendale/Tempe/Scottsdale/etc — split off the original 'maricopa' source which OBJECTID-ordered into Phoenix-only territory)
  - collin_plano: 33K (NCTCOG Collin CAD parcel feed via services2.arcgis.com/5aVZxf6eblRfH5Yb)
- **Need violations (DEAD per V474 sweep):** Madison (195K owners), Reno (183K), Tucson (161K), Indianapolis (96K), Portland (54K), Tampa (40K), Minneapolis (29K), Tempe (25K), Las Vegas (7.5K), Boston (3.6K), Jacksonville (1.1K), Dallas (925) — all probed, no live REST violations source.
- **Need owners (potential next-cycle wins):** Houston (3.4K permits + 83K violations + 0 owners; HCAD is HTML-only via REST), Charlotte (540p + 8K v + 0 — Mecklenburg owner data only on polaris HTML portal), LA (34K p + 2.8K v), New Orleans, Pittsburgh, Denver, Henderson (Clark assessor wired but tag mismatch?), Anaheim, San Jose, Hialeah (Miami-Dade tagged 'Miami').
**Older snapshot below from 2026-04-26, post-V437 retag + 18 assessor-source imports:**
- **Ad-ready (13):** Chicago (3,498), Miami-Dade (3,980), Phoenix (1,080), San Antonio (3,830), NYC (791), LA (591), Nashville (73), Cleveland (148), Henderson (362), Buffalo (85), Orlando (57), Anaheim (242), San Jose (112) — phones in parens; all 13 have >100 profiles + >50 phones + >0 violations
- **Property owners pipeline live (~947K rows across 18 assessor sources).** Top owner counts: Miami-Dade 81K, Phoenix 79K, Broward 77K, Clark/LV 73K, Cook/Chicago 72K, Davidson/Nashville 71K, Cuyahoga/Cleveland 60K, Erie/Buffalo 59K, Travis/Austin 55K, Philadelphia 55K, Wake/Raleigh 54K, Onondaga/Syracuse 54K, Multnomah/Portland 54K, Hillsborough/Tampa 53K, Hennepin/Minneapolis 29K, NYC PLUTO 5K, Bexar/SA 5K, Lee/Cape Coral 4K
- **Near-miss watch:** Philadelphia 1,345 profiles / 12 phones (PA has no bulk DB → DDG-only path); Minneapolis/Hialeah/Las Vegas all gated by violations (known dead-ends per below)
- **Periodic state license re-imports are the highest-leverage hidden lever.** Re-running this session yielded LA +564 phones, NYC +317, Buffalo +16, Minneapolis +7, Hialeah +18 (FL). Schedule monthly state-import refresh in autonomous loop.
- **Older snapshot below from 2026-04-23, post-V250 merges:**
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

### V258 TODO: CKAN violation collector support — DONE in V322
- ✅ V322 added is_ckan branch to collect_violations_from_endpoint and VIOLATION_SOURCES['pittsburgh'] entry. WPRDC `70c06278-...` (573K records) now eligible. Pittsburgh phones still gated on PA-no-bulk-DB (DDG-only).

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

### V321/V322 broken-extraction sweep 2026-04-25
Audited all cities in DB with permits>1000 + profiles<100 + fresh data. Findings (slug pre-flight + schema probe):
- **Newport Beach CA** (newport_beach_ca, ArcGIS DashBuildingPermits): 1,679 permits, schema 33 fields, NONE contractor-related. Dead.
- **Fayetteville NC** (fayetteville_nc, ArcGIS SPA_Dashboard_Permits): 1,386 permits, 25-field schema with no contractor field. Dead.
- **Richmond VA + Palmdale CA** — both Accela platform, known dead-end pattern (HTML grid, no contractor column).
- **austin_tx, tempe_az_arcgis, seattle_wa**: prod_cities source_ids that are ORPHANED — no matching CITY_REGISTRY key. Daemon's `collect_single_city` falls through to 'not_found'. Tempe/Seattle/Austin are also independently structurally dead (no contractor in source). Not worth fixing the slug routing for these.
- **dallas** (registry key, dallas ArcGIS T_BU_Permits_FY2023_24): 14,336 permits with 13,570 contractor names + phone/email, BUT newest ISSUE_DATE is 2023-12-29 (frozen 16 months). Could be backfilled as historical contractor reference but stale-by-our-rules.

### V317/V318/V319 dead-ends 2026-04-24 (new-city hunt continued)
- **Boulder CO**: maps.bouldercolorado.gov ArcGIS root has 14 folders but only `pds/ExportPermitWebMap` (geoprocessor, not data), `plan/ROWPermit2` (right-of-way only — sidewalk/utility cuts), and `cv/CommuterPermitNetworkLayer` / `ParkingPermitsMap` (commuter/parking). No building permits feed exposed.
- **Honolulu HI**: data.honolulu.gov has 8 permit datasets but newest (4vab-c87q "Building Permits Jan 2005-June 30, 2025") is 10 months stale. Other datasets frozen 2016-2019. Dead by freshness.
- **Madison WI, Saint Paul MN, Lansing MI, Mobile AL, Lexington KY, Richmond VA, Portland OR**: federated Socrata search returns 0 hits matching their domain. Madison ArcGIS (maps.cityofmadison.com) root has no relevant services. Lansing's gis.lansingmi.gov times out from Render.

### V319/V320/V321 — recurring CITY_REGISTRY ↔ prod_cities slug trap
2026-04-24: shipped V319 (Miami `CompanyName → contractor_name`) + V320 (Arlington TX `NameofBusiness → contractor_name`) + V321 (rename `miami_fl` → `miami_fl_arcgis2`). The big lesson: when adding a contractor mapping, **always confirm prod_cities.source_id matches the CITY_REGISTRY key you're patching**. Same trap hit Cambridge (cambridge vs cambridge_ma at V315→V316) and Miami (miami_fl vs miami_fl_arcgis2 at V319→V321). Fix-before-ship check:
```sql
SELECT city_slug, source_id FROM prod_cities WHERE city LIKE '%<CITY>%';
```
Then `grep -n '"<source_id>":' city_configs.py` — if the key isn't there, the daemon's `collect_single_city` falls through to 'not_found' and silently never collects (visible in scraper_runs as `status=error` with no error_message).

Also: after a contractor field_map fix, the daemon takes one full collection cycle (~30 min) AND a worker module reload to apply. Force-collect via `/api/admin/force-collection` will pull fresh records under the new field_map only after the gunicorn worker restarts. Multiple rapid PRs queue Render deploys; expect lag.

### V315 wins + dead-ends 2026-04-24 (new-city hunt)
- **Cambridge MA** ✅ ad-ready candidate. Already in CITY_REGISTRY at qu2z-8suj (Building Permits: Addition/Alteration), 613 permits / 389 profiles. Old field_map mapped applicant_name → contact_name only — all 389 profiles were individuals. Source ALSO has firm_name (real businesses: "Long Home LLC", "Vining Construction", "Rogers Insulation Specialists Co Inc", "advanced green insulation", "HomeWorks"). 425 of 432 2026 records have firm_name populated (98.4%). V315 adds `firm_name → contractor_name` + license_number mapping. Profile consolidation happens after next worker restart picks up the new module import.
- **Aurora CO** dead. ags.auroragov.org/aurora/rest/services/OpenData/MapServer/156 (Building Permits 6 Months) has 7,546 fresh records but 43-field schema has ZERO contractor/applicant/business/company/firm fields. Same DC/Boston pattern. Has a usable violations feed at MapServer/161 (3,484, 6mo) but we don't onboard violation-only cities.
- **Pasadena CA** dead. data.cityofpasadena.net Permit_Activity + Active_Building_Permits_view feeds (services2.arcgis.com/zNjnZafDYCAJAbN0) are fresh (modified 2026-04-24) but expose only address/case_number/description/parcel — no contractor field. Same DC/Boston pattern.
- **Frisco TX, Cary NC**: standard ArcGIS Hub DCAT URLs (data-friscotx, data-townofcary, geohub variants) all 404. Need deeper research — possibly behind Accela/Viewpoint or proprietary portals.

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
- Runs as background thread **inside the Flask process** (the only service)
- Pauses during imports via IMPORT_IN_PROGRESS flag
- Collects all active cities every ~30min

### Tampa Accela P1 — RESOLVED in V476
The long-standing P1 ("Accela scraper has no contractor column") is
fixed via a new hybrid platform `accela_arcgis_hybrid`:
1. Pull permit list from `arcgis.tampagov.net Planning/PermitsAll`
   (each row carries a `URL` linking to the Accela CapDetail page).
2. Per permit, fetch the Accela detail HTML and parse
   "Licensed Professional:" via regex.
3. 100% yield on 15 sample probes (DOMAIN HOMES INC, HOOTER
   CONSTRUCTION, JP CONSTRUCTION INC, etc.).
The same pattern can wire Memphis or any other city with this
ArcGIS-index + Accela-detail combo. See
`accela_portal_collector.parse_accela_licensed_professional` and
`fetch_accela_arcgis_hybrid`.

### Saint Petersburg — DEAD via current REST sources (V476 audit)
- Old config endpoint `egis.stpete.org/.../ServicesDSD/AllPermitApplicaitons/MapServer/0` is **404**.
- Replacement `ServicesDSD/PermitsExternal/MapServer/0` is **frozen 2019-2021** AND its CONTRACTOR field is blank on every record (only OWNER populated).
- `ServicesDOTS/PermitsResidential/MapServer/{0..3}` has only OWNER + a CLICKGOVLINK to `actiononline.stpete.org/Click2GovBP/` — same Click2Gov detail-page scrape pattern as Tampa, but a DIFFERENT (Tyler/CivicAccess) HTML structure. Would need its own parser.
- The 2,537 existing `saint-petersburg` profiles are mostly **homeowner names** ("ZWINGE, KIRK T *", "ZIMMERMAN, JOSEPH V *") — they came from CONTRACTOR field that was actually populated with owner names. FL DBPR can't match these because DBPR licensees are businesses.
Verdict: Saint Pete needs a Click2Gov detail-page scraper to recover real contractors. Documented as research dead-end without that engineering work.

### Jacksonville FL — DEAD via current REST sources (V476 audit)
- Existing config points to `services2.arcgis.com/CyVvlIiUfRBmMQuu/.../Building_Permits_Applications_view/FeatureServer/0` — but **the data is for Virginia Beach VA** (sample shows City=Virginia Beach, State=VA). Wrong-source bug.
- Hosts `data.coj.net`, `maps.coj.net`, `gis.coj.net`, `aca.coj.net`, `data.jacksonville.gov` all fail DNS or 404 from Render egress.
- ArcGIS Hub returns 0 hits for Jacksonville/Duval permits.
- `www.coj.net` HTML has no embedded ArcGIS URLs.
Verdict: no public REST permit feed found 2026-04-30. The 27 stored Jacksonville permits are stragglers from the wrong-source config. Documented dead-end until a real source emerges.

### Email digest scheduler (V475 — don't repeat the V473b miss)
The web process spawns THREE daemon threads via `start_collectors()`:
`scheduled_collection`, `enrichment_daemon`, **`email_scheduler`**.
V471 PR4 deleted all three; V473b corrective restored only the first
two — `email_scheduler` was missed and daily digests stopped firing on
2026-04-30. V475 added it back. If you ever neuter `start_collectors()`
again, restore all three or daily digests die silently.

The `/api/admin/start-collectors?force=1` endpoint resets the
`_collector_started` flag even when scheduled_collection is alive
(also V475) — this is what lets you spawn newly-added daemon threads
without a Render restart. Without `force=1`, the endpoint short-circuits
on "already_running" and `start_collectors()` body never runs.

Verify post-deploy: `GET /api/admin/digest/status` should show
`thread_alive: true` + a recent `last_heartbeat`. If thread_alive=false,
hit `POST /api/admin/start-collectors?force=1` and check
`/api/admin/debug/threads` for an `email_scheduler` entry.

### V515 — digest dup-fire guard via digest_log (2026-05-05)
On 2026-05-05 a single subscriber received two daily digests 27 min
apart (11:02 UTC then 11:29 UTC). Email A: "50 new permits in Atlanta".
Email B: "no new permits today, here are the most recent". Same
subscriber, same underlying permits — just two renders with two
different templates because the second fire happened AFTER the first
fire had advanced subscribers.last_digest_sent_at, which made the
"new since last digest" filter return zero records and the V22
fallback engaged.

Root cause was the dup-fire itself, not the templates. Worker A
inserted digest_log row 36 at 11:02 UTC and updated
system_state.digest_last_success in the same txn, then died before
all per-subscriber timestamps persisted. Worker B booted; the V276
bootstrap that re-seeds in-memory `last_digest_date` from
system_state.digest_last_success failed in some race path
(thread-spawn-before-bootstrap, WAL contention, silent exception),
so Worker B's `last_digest_date` was None and the 7AM ET gate fired
again at 11:29 UTC.

V515 fix: query digest_log directly at the top of the digest fire
path (server.py:8855). digest_log is durable and is written in the
same txn as system_state, so it's the ground-truth dedup source
regardless of which worker we're in or whether bootstrap saw it.
Also bumped email_scheduler thread startup sleep 180s → 240s so a
respawning worker gives any in-flight digest 60s extra to commit
its digest_log row.

**Lesson:** in-memory dedup counters bootstrapped from "system_state-
on-thread-start" can race with worker spawn vs. concurrent INSERTs.
Durable dedup must query the durable table directly inline at the
fire decision point, not read a once-at-startup cached value.

### V510-V513 — wrong-tenant data + Phase 3 Accela skip (2026-05-05)

- **V510 SBC tenant fix.** The codebase had San Bernardino County wired
  to `aca-prod.accela.com/SBCO/`. **/SBCO/ is Santa Barbara County's
  portal, not San Bernardino.** Verified via tenant landing pages:
  /SBC/ → "County of San Bernardino", /SBCO/ → "Santa Barbara County
  Citizen Portal". Real San Bernardino tenant per ezpermits.sbcounty.gov
  redirect = /SBC/. Fixed agency_code in `city_registry_data.py`,
  `accela_configs.py`, and `routes/admin.py` defaults. **Lesson:** when
  picking an Accela agency_code, hit `aca-prod.accela.com/<CODE>/Welcome.aspx`
  and grep the body for `(County|City) of <name>` to confirm the tenant
  is who you think it is. Do not assume the code mirrors the slug
  (SBCO ≠ San Bernardino County; that pattern is "first letters of agency
  name" which collides constantly).

- **V511 retag endpoint.** `/api/admin/retag-permits` moves rows between
  source_city_keys (permits + contractor_profiles). Body:
  `{"from_slug": "...", "to_slug": "...", "dry_run": true}`. Used to
  rescue 1,966 misidentified Santa Barbara permits out of the
  san-bernardino-county slug into santa-barbara without losing the data.
  Default dry_run=true so an accidental call is a no-op.

- **V512 redeploy.** Empty commit pushed to recycle gunicorn after a
  WAL deadlock from too-aggressive sequential force-collect during a
  full-fleet sweep. **Lesson reinforced:** even `time.sleep(1.0)`
  between sequential force-collects can deadlock if a slow city holds
  a write txn long enough for the daemon's own writes to pile up. Use
  `time.sleep(3-5)` between force-collects in bulk-sweep scripts, and
  prefer `/api/admin/force-collection` (background full-cycle) for
  fleet-wide refresh.

- **V513 Phase 3 Accela-skip removal — multi-customer ingestion bug.**
  collector.py:4220-4222 explicitly skipped `platform=='accela'` cities
  in the Phase 3 catch-all loop, with a comment "requires browser
  automation". That comment was stale: V162/V163 (2026-04-13) rewrote
  `accela_portal_collector.py` from Playwright to requests+BS4, but the
  skip in Phase 3 was never removed. **Concrete impact:** ~41 Accela
  cities (chandler, bradenton, brownsville, kettering, palmdale, pharr,
  sparks, adams-county-co, berkeley-ca-accela, alameda-ca, etc.) had their
  last `scraper_runs` entry on 2026-04-14 04:08-04:15 UTC and ZERO runs
  in the 22 days since. That timestamp is exactly when Phase 2's
  stale-first sort first rotated them out of the top-75. Phase 3 was
  meant to be the catch-all — but it filtered them out every cycle.
  Force-collect on chandler today returned 100 fresh permits proving
  the requests+BS4 scraper works fine. **Lesson:** when removing a
  dependency or rewriting a code path, audit every existing skip /
  filter / `if platform == X` for staleness. The comment-as-policy
  pattern (`# Skip accela (requires browser automation)`) lies silently
  forever after the rewrite.

### V496 — bulk source_endpoint patch + daemon-stability rules (2026-05-04)
- **Bug class:** prod_cities.source_endpoint had drifted to NULL on
  ~210 active cities (including Chicago, NYC, LA, Phoenix, etc.). The
  daemon's primary path errored with `Invalid URL '': No scheme supplied`
  on every cycle; data was leaking in via a fragile fallback path. Fixed
  by `/api/admin/patch-source-endpoint` (V496) — populated source_endpoint
  from CITY_REGISTRY[source_id].endpoint for each. **Use this endpoint
  for any future "active row but missing config" rows that appear.**
- **DO NOT parallel-force-collect.** Each `/api/admin/force-collect`
  holds a SQLite write transaction. >2 concurrent (3 was tested 2026-05-04)
  plus the daemon's own writes will WAL-deadlock all gunicorn workers
  and the site goes 502 site-wide for 5-10+ minutes until a deploy
  recycles the workers. Use `/api/admin/force-collection` (background
  full-cycle) for bulk work, or sequential force-collect for small batches.
- **External healthbeat REQUIRED.** Render does not natively probe
  `/api/admin/health`, so the V493 IRONCLAD self-heal only fires when
  something else hits /health. Without an external pinger the daemon
  has died silently for 4+ hours undetected on multiple occasions
  (V481-style regressions keep recurring). The intended fix is
  `.github/workflows/healthbeat.yml` — a 5-minute cron that pings
  /health. **The OAuth token used by Code's gh CLI cannot push
  workflow files; Wes must commit and push that file with his
  PAT.** The file lives at .github/workflows/healthbeat.yml in the
  working tree until pushed.
- **WAL recovery procedure** if deadlock happens anyway:
  1. `git commit --allow-empty -m "redeploy"` then `git push` (forces
     Render to recycle gunicorn workers — usually only path that works)
  2. After deploy completes, `POST /api/admin/start-collectors?force=1`
  3. `POST /api/admin/wal-checkpoint` if WAL has bloated
  4. Verify daemon via `GET /api/admin/debug/threads` — look for the
     three named threads scheduled_collection / enrichment_daemon /
     email_scheduler.

### ARCHITECTURE GROUND TRUTH (don't repeat the V471 PR4 mistake)
**There is exactly ONE Render service:** `permitgrab` (Docker, Oregon).
The Flask web server **and** the collection daemon thread run **in the
same process**. There is no separate worker container.

`render.yaml` declares a `permitgrab-worker` Background Worker service
and `worker.py` exists in the repo, **but the service was never created
in the actual Render project**. Treat `worker.py` and the
`permitgrab-worker` block in render.yaml as dead code / aspirational —
running collection in a separate worker would be a future migration,
not the current state.

`WORKER_MODE` env var is **not set** on the live web service (verify
before assuming). Code paths gated on `WORKER_MODE` are no-ops in prod.

V471 PR4 (commit `0f5a7ef`, since reverted by PR #417 / commit
`2c91a2a`) neutered `start_collectors()` because it assumed the worker
was running. Don't make that mistake again — `start_collectors()` is
the only collection mechanism, and POSTing to `/api/admin/start-collectors`
is the only way to start it after a deploy.

If you ever need to verify the deployed services, check the Render
dashboard directly. Do NOT trust render.yaml.

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

### Step 3b: HARD GATE — Verify contractor names BEFORE writing any code
SSH into the endpoint and examine 5+ records. You MUST confirm ALL of these
before writing ANY city_configs.py code:
  ✓ A field exists with real business names (not license numbers, not codes)
  ✓ At least 3 of 5 sample records have non-empty contractor names
  ✓ Names look like real businesses ("Smith Plumbing LLC", not "EV1110")

If ANY check fails → log a one-line dead-end in DEAD_ENDS.md and MOVE ON.
Do NOT write any code. Do NOT create a PR. Do NOT modify city_configs.py.
The entire check takes under 60 seconds via SSH.

**Tempe, Aurora, Boulder, Las Vegas violations all had PRs written, merged,
and deployed before discovering the data was unusable. That's 30+ minutes
wasted per city. This gate prevents that.**

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

### Time Limits (ENFORCED)
- City endpoint research: 5 minutes MAX. If you can't find a working
  endpoint with contractor names in 5 minutes, log "needs research" and
  move to the next city.
- City config wiring: 5 minutes MAX. Copy a similar existing config,
  change the fields, deploy.
- Dead-end investigation: 2 minutes MAX. One SSH test. If it fails,
  log one line and move on.
- Total per city: 10 minutes. If a city isn't producing data in 10
  minutes, skip it.

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
| ~~St. Pete~~ | ~~Socrata tmdq-gg7f~~ | DEAD — V326 2026-04-25: stat.stpete.org redirects to www.stpete.org HTML homepage; Socrata datasets removed/migrated. Not in VIOLATION_SOURCES anyway. |
| Mesa | Socrata | hgf6-yenu at data.mesaaz.gov |
| Miami-Dade | ArcGIS | services.arcgis.com/8Pc9XBTAsYuxx9Ny/.../CCVIOL_gdb/FeatureServer/0 |
| Phoenix | ArcGIS MapServer | maps.phoenix.gov/pub/rest/services/Public/NSD_Property_Maintenance/MapServer/0 |
| Cape Coral | ArcGIS MapServer | capeims.capecoral.gov/.../OpenData/MapServer/5 |
| Cleveland | ArcGIS FeatureServer | services3.arcgis.com/dty2kHktVXHrqO8i/... |
| ~~Fort Lauderdale~~ | ~~ArcGIS MapServer~~ | DEAD-BY-FRESHNESS — V326 2026-04-25: gis.fortlauderdale.gov CodeCase MapServer/0 has 66,436 records but newest INITDATE is 2019-10-03 (6+ years stale). Configured but every collection cycle returns 0 inserts because the date filter excludes everything. |
| Pittsburgh | CKAN | data.wprdc.org resource 70c06278-... (V322) |
| Boston | CKAN | data.boston.gov resource 90ed3816-... (V324) |
| Denver | ArcGIS FeatureServer TABLE | services1.arcgis.com/zdB7qR0BtYrg0Xpl/.../ODC_service_requests_311/FeatureServer/66 (V259) — Agency=CPD + Case_Summary whitelist |
| San Antonio | ArcGIS FeatureServer | services.arcgis.com/g1fRTDLeMgspWrYp/.../311_All_Service_Calls/FeatureServer/0 (V259) — ReasonName=Code Enforcement + TypeName whitelist |
| Fort Worth | ArcGIS FeatureServer | services5.arcgis.com/3ddLCBXe1bRt7mzj/.../CFW_Open_Data_Code_Violations_Table_view/FeatureServer/0 — daily refresh, 6,453 rows newest 2026-04-28 |
| Charlotte | (already wired) | DB has 7,990 rows newest 2026-04-29 — DESPITE Charlotte being a permit dead-end (V290), violations are LIVE and fresh. Don't re-research. |
| Plano TX | (already wired) | DB has 6,208 rows newest 2026-04-29 — fresh, daily refresh. Don't re-research. |

### Known dead ends for violations
San Diego (16K rows in DB but newest 2018-01-09 — pre-2018 archive only, live only via Accela scrape), Dallas (Socrata archives frozen 2018, no live ArcGIS feature service), Houston (83K rows in DB but newest 2018-08-22 — CKAN Excel-only for current data), Minneapolis (Tableau dashboard only — no REST feed), Henderson NV (ComDevServices/0 has CE Violations but ~80 building-relevant of 871; rest is STVR/parking/camping nuisance — low lead-gen value, skip until needed), **Las Vegas NV** (V313 2026-04-24: data.lasvegasnevada.gov TCP-times-out from Render egress; api.us.socrata.com federated search returns 0 LV datasets — LV not on Socrata at all; Clark County maps.clarkcountynv.gov has a CodeEnforcement folder but it lists empty; PW/Complaints8 is development-services-review workflow, not code enforcement; ArcGIS Hub at opendata-lasvegas.opendata.arcgis.com has a UI but DCAT 404 + search API 401. 380 phones on LV are an ad-ready candidate but violations gap is structurally unsolvable.), **Indianapolis IN** (V474 2026-04-29: gis.indy.gov OpenData_NonSpatial/MapServer/1 newest OPEN_DATE 2024-02-26, services.arcgis.com/.../Indianapolis_Code_Enforcement_Violations_and_Investigations_Geocoded newest USER_OPEN_DATE 2021-10-27 — both frozen, ArcGIS Hub has nothing fresher), **Tucson AZ** (V474 2026-04-29: only ArcGIS Hub hit was services3.arcgis.com/.../WD_Code_Enfrcmnt_Invoices_June_2022_Feb_2024 — dataset name literally contains the date range; modified 2024-10 with no record-level updates), **Madison WI** (V474 2026-04-29: maps.cityofmadison.com OPEN_DATA + Public_Safety + OPEN_DATA_PLANNING all enumerated — no code enforcement / violations layer exposed; ArcGIS Hub returns 0 hits for "madison wisconsin code enforcement"), **Reno NV** (V474 2026-04-29: wcgisweb.washoecounty.us QAlert + OpenData + CSD_App folders all probed — no violations layer; ArcGIS Hub returns 0 hits), **Portland OR** (V474 2026-04-29: ArcGIS Hub returns 0 hits for "portland oregon code violations" — portlandmaps.com/api 404s on Public/PortlandPolice path), **Tampa FL** (V474 2026-04-29: ArcGIS Hub returns 0 hits for "tampa code enforcement violations").

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

### Visual UAT — Click Through as Three Personas (after EVERY template change)

**LOGGED-OUT visitor:**
  □ Homepage loads in < 5 seconds
  □ Click a city → city page loads with data in unified table
  □ Unified table has rows, pagination works, filter dropdown works
  □ Click "Pricing" → pricing card is CENTERED, not left-aligned
  □ Click "Sign Up" → signup form loads
  □ Nav "Cities" dropdown opens on click, shows real cities
  □ Footer links all work
  □ No duplicate sections anywhere on city page

**FREE user (test account):**
  □ Login works, nav shows username
  □ "Get Alerts" link visible in nav
  □ City page shows same unified table + phone gating
  □ Analytics page loads
  □ Phone numbers are masked until reveal

**PRO user (test pro account):**
  □ All phones visible (no masking)
  □ CSV export works
  □ Intel dashboard loads
  □ No "upgrade" prompts shown

**For ALL personas, check:**
  □ No elements visually misaligned (especially single-card layouts)
  □ No broken links (click every nav item)
  □ Page doesn't freeze or take > 5 seconds
  □ FAQ text matches actual product (no references to nonexistent plans)
  □ Mobile: hamburger menu works, no horizontal scroll

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
11. **City expansion is QUEUE-ONLY.** Never research or discover NEW cities to add. Only wire cities already in CITY_QUEUE.md "Ready to Wire" section (Wes populates this). Max 3 cities per cycle, 10-minute cap per city. However, you ARE allowed and EXPECTED to web-search for exact API endpoint URLs when wiring queue cities — Google "[city] open data building permits API", find the REST endpoint, SSH-test it. The ban is on city DISCOVERY, not on endpoint RESEARCH for queued cities.
12. **Enrichment before expansion.** Always run ALL enrichment imports (FL DBPR, MN DLI, NY DOL, WA L&I, CA CSLB) before wiring any new cities. Enriching existing cities toward ad-ready is higher ROI than adding new ones.
13. **Auto-merge your own PRs.** You are the reviewer, the merger, and the deployer. After `gh pr create`, immediately squash-merge with `gh pr merge --squash --delete-branch`. Never leave a PR open waiting for someone to click a button. If a merge conflicts, rebase and retry — don't stop.
14. **Check CITY_QUEUE.md first.** Before researching any new city, check if
    it's already in the queue as "Ready to Wire" — if so, skip research and
    just wire the config. If it's in "Dead Ends" — skip it entirely.
15. **Batch dead-end logging.** Do NOT make a separate commit for each dead-end
    city. Accumulate dead-ends during a work session and log them in ONE batch
    commit to DEAD_ENDS.md at the end. Never spend more than 60 seconds
    documenting a dead end.
16. **Never ship untested infrastructure.** New dependencies (Sentry, monitoring
    packages, etc.) MUST use guarded imports (try/except) and be tested locally
    before deploying. The V287 Sentry incident wasted 45 minutes and 3 commits.

17. **DON'T STOP ON DEPLOY-ROLLOVER 502s.** A 502 during a Render deploy lasts
    1-3 minutes and clears itself — it is NOT a reason to pause. Keep working.
    The wakeup is for verifying long-term health, not for blocking on transient
    edge errors. The user's worst frustration is "stop stopping" — when given
    a goal like "50 complete cities" or "finish this file", DO NOT report
    progress and ask if you should continue. Keep grinding until the goal is
    met or a real blocker appears (a deploy that fails after 15+ min, a SQL
    error that won't resolve with a retry, an irreducible dead-end that
    requires user input). Mid-deploy 502s, "single check" loops, scheduled
    wakeups, and intermediate progress milestones are NOT blockers. Don't
    summarize and ask. Don't ask "want me to continue?". Don't pause for
    permission to do the next thing on the user's stated list. Just do it.

18. **"DON'T STOP" IS DURABLE FOR THE WHOLE SESSION.** When Wes says
    "keep going", "don't stop", "go through it all", "execute fully",
    "finish everything" — that directive applies to the rest of the
    session, not just the next step. After every PR merge or task
    milestone, IMMEDIATELY pick up the next item on the explicit
    or implicit list. NEVER end a turn with "want me to continue
    on X?" or a status-summary-then-pause pattern. Wes can interrupt
    anytime to redirect — that's cheaper than letting momentum die.
    Real blockers that DO warrant a pause: missing credentials,
    irreversible destructive action that needs sign-off, or the user
    has explicitly contradicted the prior directive. End-of-task
    "should I do Y next?" is NOT a real blocker; pick the highest-
    impact next item and start. Only summarize when the user
    explicitly asks "where are we / status / summary".

---

## AUTONOMOUS OPERATION — NEVER STOP

You are not a task runner. You are the engineering team. When you finish something, you
start the next thing. When you hit a wall, you pivot. When everything is green, you BUILD.

### Work Priority: Fast Track First, Slow Track After

**FAST TRACK (5 min each, do these first):**
- Process cities from CITY_QUEUE.md "Ready to Wire" list
- Fix known broken field_maps (one-line changes)
- Run enrichment imports for cities that need phone refresh
- Apply exact bug fixes from instruction files

**SLOW TRACK (30+ min each, do after fast track is empty):**
- Research new cities (update CITY_QUEUE.md)
- Complex bug fixes requiring investigation
- New feature development
- Template changes + full visual UAT

Always empty the fast track before starting slow track work.

### THE LOOP (run continuously, never exit)

### 1. HEALTH CHECK (every cycle)
Hit /api/admin/health, verify daemon_running + status:healthy. Restart
collectors via POST /api/admin/start-collectors if daemon stopped.

### 2. DATA QUALITY AUDIT (every cycle)
Run garbage-profile and stale-city queries. Fix anything found before
moving on. Visual UAT (npm run test:uat) after every template change.

### 3. FL DBPR FIX (until resolved — P0)
The FL DBPR import is the #1 unblocked phone-enrichment lever. SSH in,
inspect actual myfloridalicense.com CSV column positions, update
STATE_CONFIGS['FL'] applicant_columns + licensee_columns to match.
Unlocks 6+ FL cities (Miami-Dade, Orlando, Tampa, Hialeah, St Pete,
Cape Coral, Fort Lauderdale, Jacksonville). Until fixed, FL DBPR
imports run but match 0 records.

### 4. ENRICHMENT IMPORTS (every cycle — highest ROI)
Run ALL state license imports every cycle. This is the fastest path to ad-ready.
- FL DBPR: `POST /api/admin/license-import {"state":"FL"}` — unlocks 6+ FL cities
- MN DLI: re-run for Minneapolis
- NY DOL: re-run for Buffalo + NYC
- WA L&I: re-run for Seattle
- CA CSLB: re-run for San Jose + Anaheim
- DDG web search enrichment: trigger for any city with >100 profiles but <50 phones
After each import, verify phone counts increased. If FL DBPR still fails, debug it — this is the #1 blocker.

### 5. TEMPLATE & BUG FIXES (check for instruction files)
- Check for CODE_V*.txt instruction files in the repo root
- Process them in version order (e.g., CODE_V360_UAT_BUGS.txt before V361)
- After fixing, run UAT to verify

### 6. CITY EXPANSION (queue-driven — CITY_QUEUE.md only)
Check CITY_QUEUE.md "Ready to Wire" section. If entries exist:
- Wire up to 3 cities per cycle
- 10-minute hard cap per city — if it takes longer, log as dead end and move on
- Follow the standard pipeline: add config → deploy → backfill → verify
- After wiring, move the entry from "Ready to Wire" to the appropriate section
If "Ready to Wire" is empty, SKIP this phase entirely. Do NOT research new cities.
Wes populates the queue — your job is to wire what's there.

### 7. VIOLATION EXPANSION (ongoing)
- For each city that has profiles but no violations, search for ArcGIS/Socrata code enforcement data
- Test, configure, deploy

### 8. SEO IMPROVEMENT (weekly)
- Query DB for real city stats
- Update city page content with real numbers
- Fix any technical SEO issues (canonicals, H1s, meta descriptions)
- Check that new pages are being added to sitemap

### 9. UAT (after every change)
- Run the full UAT checklist above
- Verify no regressions

### 10. PROPERTY OWNER PIPELINE (V276+)
- Check property_owners table has data for all 5 ad-ready cities
- For cities with assessor sources, verify monthly refresh is running
- Check address matching rates — should be >50% for each city
- Verify property owner section appears on city pages
- Check permit_alerts table for new signups

### 11. SEO CITY PLAYBOOK (V285+, after every deploy)
- Run the tier query from CODE_V285_SEO_CITY_PLAYBOOK.txt
- Check if any city crossed a gate (COLLECTING → PROFILES → ENRICHED → OWNERS → AD_READY)
- Run the corresponding SEO actions for cities that crossed a gate
- For top 5 ad cities, ALWAYS verify full treatment is in place (title, H1, meta, JSON-LD, FAQ, data content)
- Priority order: ad cities first, then near-ready (San Jose, DC, Minneapolis), then top 100 by population

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

### Externally-installed plugins (from Claude Plugin Marketplace, May 2026)

In addition to the project-local skills above, the following plugins are
installed at the user level. ALWAYS prefer these for tasks they cover —
they're more rigorous and battle-tested than ad-hoc approaches.

| Plugin | Source | When to use it |
|--------|--------|----------------|
| **frontend-design** (Anthropic official) | `anthropics/claude-code` marketplace | Any UI / template / CSS work. Forces a design framework before code (purpose, audience, aesthetic) so output isn't generic AI-aesthetic. Mandatory for city-page template changes, persona-page redesigns, pricing-card tweaks, signup form work. |
| **claude-seo** | `AgriciDaniel/claude-seo` marketplace | Run `/seo audit` before any SEO content change. 19 sub-skills cover canonicals, H1s, schema, sitemap, E-E-A-T, technical SEO, GEO/AEO, local SEO. Single entry point that attacks the "2/3,115 indexed" problem documented in the SEO section. |
| **claude-ads** | `AgriciDaniel/claude-ads` marketplace | Mandatory before ANY Google Ads change. 250+ checks across Google/Meta/YouTube/LinkedIn/TikTok/Microsoft/Apple. Two modes: CSV upload (no API) or live MCP. "Wasted Spend Audit" sub-skill identifies recoverable spend in 2 min. Use after every campaign update + weekly. |
| **playwright-skill** | `lackeyjb/playwright-skill` marketplace | Replaces the V248 Puppeteer UAT pipeline. Visual regression with screenshot baselines, viewport tests, console + network capture. Use for every template change BEFORE deploy. Closes the V247-V250 layout-regression hunt loop. |

**Triggers:**
- "design / UI / template / CSS / responsive / mobile" → frontend-design
- "SEO / canonical / sitemap / schema / meta / blog / indexing" → claude-seo (`/seo audit`)
- "ad / campaign / Google Ads / CPC / CTR / quality score / sitelink / callout" → claude-ads
- "UAT / visual / screenshot / regression / e2e test / accessibility / Lighthouse" → playwright-skill

**Install commands** (for re-install or new dev machine setup):
```
/plugin marketplace add anthropics/claude-code && /plugin install frontend-design@claude-code-plugins
/plugin marketplace add AgriciDaniel/claude-seo
/plugin marketplace add AgriciDaniel/claude-ads
/plugin marketplace add lackeyjb/playwright-skill
```

**Backlog plugins** (not yet installed, recommended next — install commands + triggers below so the trigger map is ready the moment they're added):

| Plugin | Source | Install command | When to use it |
|--------|--------|-----------------|----------------|
| **ui-ux-pro-max** | `nextlevelbuilder/ui-ux-pro-max-skill` | `/plugin marketplace add nextlevelbuilder/ui-ux-pro-max-skill && /plugin install ui-ux-pro-max@ui-ux-pro-max-skill` | Layer on top of frontend-design after a week of use. Design system generator, 161 palettes, 99 UX rules, 25 chart types. Use for: branded design system creation, choosing color palettes for new persona pages, picking font pairings for the V478 redesign, generating data-viz styles for the Analytics dashboard. |
| **accessibility-pro** | `CogappLabs/claude-plugins` (plugin: accessibility-pro) | `/plugin marketplace add CogappLabs/claude-plugins && /plugin install accessibility-pro@claude-plugins` | WCAG 2.1/2.2 audits via Playwright + axe-core. Critical for Google Ads Quality Score (Google scores landing-page accessibility into ad rank). Use after every persona-page or city-page template change. Mandatory before unpause spend campaigns. |
| **web-quality-skills** | `addyosmani/web-quality-skills` | clone or symlink `~/.claude/skills/` from repo (no marketplace yet) | Core Web Vitals (LCP/INP/CLS) by Google Chrome team's Addy Osmani. 150+ Lighthouse audits framework-agnostic. Pairs with claude-seo for technical-SEO depth. Use for city-page LCP work — /permits/* pages have stat tables that risk LCP regression. |
| **superseo-skills** | `inhouseseo/superseo-skills` | clone to `~/.claude/skills/` | Explicit anti-AI-slop ruleset from Koray Tuğberk + Lily Ray methodology. 11 skills incl page audits, link building, semantic gap analysis, E-E-A-T. Use for the 67-blog-post indexing gap (only 1 of 67 currently indexed — Google's been demoting generic AI content since the March 2024 Helpful Content Update). |

**Backlog plugin triggers** (auto-route once installed):
- "design system / palette / font pair / chart style" → ui-ux-pro-max
- "accessibility / a11y / WCAG / contrast / screen reader / keyboard nav" → accessibility-pro
- "Core Web Vitals / LCP / INP / CLS / Lighthouse / page speed" → web-quality-skills
- "indexing / E-E-A-T / semantic gap / blog audit / topical authority / anti-slop" → superseo-skills

**Other plugins worth evaluating later** (not yet researched in depth):
- `airowe/claude-a11y-skill` — alternative `/a11y` command with three audit modes
- `Community-Access/accessibility-agents` — 11 specialist agents that prevent inaccessible code at generation time
- `coreyhaines31/marketingskills` — broader GTM stack to layer on top of the marketing plugin
- Page CRO skill at mcpmarket.com — 7-dimension audit framework for landing pages

---

## RENDER DEPLOYMENT

- **Single service**: `permitgrab` (Docker, Oregon). Flask app + collection
  daemon thread share this process. No separate worker. (See ARCHITECTURE
  GROUND TRUTH above — the `permitgrab-worker` declaration in render.yaml
  was never deployed.)
- **SSH**: `ssh srv-d6s1tvsr85hc73em9ch0@ssh.oregon.render.com`
- **Memory plan**: Standard 2GB (was 512MB pre-V418)
- **Auto-deploy**: Pushes to main trigger automatic deploy
- **Logs**: Available via Render dashboard or SSH
- **Daemon restart after every deploy**: `curl -X POST -H "X-Admin-Key: ..."
  https://permitgrab.com/api/admin/start-collectors` — collection does NOT
  auto-start on deploy.

---

## FILE STRUCTURE (key files)

- `server.py` — Flask app + helpers + middleware (~8,800 lines after V471). Module load is zero-side-effects.
- `routes/admin.py|api.py|auth.py|city_pages.py|health.py|seo.py` — V471 PR2 blueprint split (210 routes lifted out of server.py)
- `models.py` — V471 PR2 step 1: SQLAlchemy User + SavedSearch
- `city_configs.py` — STATE_CONFIGS + helpers (~326 lines after V471 PR3)
- `city_registry_data.py` — V471 PR3: CITY_REGISTRY (1,467) + BULK_SOURCES (41) extracted from city_configs
- `collector.py` — Permit collection (`_collect_all_inner`, `collect_refresh`, `_fetch_permits_with_timeout` for the V470b 5-min wall-clock cap)
- `assessor_collector.py` — County assessor → property_owners pipeline
- `license_enrichment.py` — State license imports (FL DBPR, MN DLI, NY DOL, WA L&I, CA CSLB, AZ ROC)
- `accela_portal_collector.py` — Accela HTML scraper
- `worker.py` — **DEAD CODE** (declared in render.yaml as `permitgrab-worker` but the service was never created on Render). Don't rely on this.
- `templates/` — Jinja2 templates including admin/, blog/seo/, blog/faq/, emails/

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
