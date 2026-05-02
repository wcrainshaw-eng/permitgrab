# City Queue — Pre-Vetted Cities for Fast Onboarding

Check this file BEFORE researching any new city. "Ready to Wire" cities
have confirmed endpoints — just add the config. Cities WITHOUT contractor
data are STILL VIABLE: solar companies want owner/address data from permits.
Show "No contractor data available" on the city page when contractor_name
is absent. "Dead Ends" are cities with NO working permit API at all.

## Property-Owner Sources Wired (V428–V484, 2026-05-01)

35 owner sources now in `assessor_collector.ASSESSOR_SOURCES` (was 33 pre-V484).
Trigger via `POST /api/admin/collect-assessor-data {"source":"<key>"}`. After
the import, run `POST /api/admin/fix-property-owner-cities` to retag rows
where the source feed populates suburb names instead of the metro slug.

| Source key | Metro / cities |
|---|---|
| nyc_pluto (pre-V428) | New York City |
| maricopa | Phoenix (+ Mesa/Scottsdale/Tempe metro) |
| maricopa_secondary | Mesa/Glendale/Tempe/Scottsdale (V474 split) |
| cook_chicago | Chicago + Cook suburbs |
| miami_dade | Miami-Dade + Hialeah |
| miami_dade_hialeah | Hialeah-specific subset |
| davidson_nashville | Nashville |
| cuyahoga_cleveland | Cleveland |
| philadelphia_opa | Philadelphia (Carto SQL platform) |
| hennepin_minneapolis | Minneapolis (concat-list address) |
| bexar (pre-V428) | San Antonio |
| clark_lasvegas | Las Vegas |
| clark_henderson | Henderson NV |
| travis_austin | Austin |
| erie_buffalo | Buffalo |
| onondaga_syracuse | Syracuse |
| hamilton_cincinnati | Cincinnati |
| wake_raleigh | Raleigh + Cary + Apex |
| broward_ftlauderdale | Fort Lauderdale + Broward suburbs |
| lee_capecoral | Cape Coral + Fort Myers |
| multnomah_portland | Portland OR |
| hillsborough_tampa | Tampa (parked — Tampa permits dead pending Accela parser fix) |
| dc_vacant_blighted (pre-V428) | DC vacant/blighted properties |
| dane_madison | Madison WI |
| franklin_columbus | Columbus OH |
| marion_indianapolis | Indianapolis |
| pima_tucson | Tucson |
| santa_clara_sj | San Jose |
| tarrant_fortworth | Fort Worth |
| washoe_reno | Reno |
| collin_plano | Plano TX |
| fl_statewide | FL DOR NAL — Cape Coral, Orlando, Jacksonville, Tampa, etc. |
| orleans_new_orleans (V483b) | New Orleans LA |
| **duval_jacksonville** (V484) | Jacksonville FL — daily-fresh, 405K parcels w/ full mailing addr |
| **spokane_spokane** (V484) | Spokane WA — nightly, 138K parcels |
| **mecklenburg_charlotte** (V485) | Charlotte NC — daily, ~250K Charlotte parcels with mailing addr |
| **hcad_houston** (V485) | Houston TX — ~500K Houston-city parcels (Appraised_value_COH filter) |
| **wayne_detroit** (V486) | Detroit MI — 378K parcels, daily, full mailing addr (NEW METRO) |
| **lucas_toledo** (V486) | Toledo OH — 204K parcels, weekly (NEW METRO) |
| **fulton_atlanta** (V486) | Atlanta core + Sandy Springs / Roswell — 372K parcels (NEW METRO) |
| **dekalb_atlanta** (V486) | Decatur / Brookhaven / Tucker / Doraville — 245K (NEW METRO) |
| **stlouis_county_mo** (V486) | St. Louis County suburbs — 401K (NEW METRO) |
| **saint_louis_city** (V487) | City of St. Louis MO — 129K (independent jurisdiction, pairs w/ county) |
| **hamilton_chattanooga** (V487) | Chattanooga TN — 166K (NEW METRO) |
| **anchorage_moa** (V487) | Anchorage AK — 99K, daily refresh (NEW METRO, owners-only) |

**Confirmed structurally suppressed** (no public owner data — would need
commercial source like DataTree/FirstAmerican/ATTOM): all California metros
(LA, OC/Anaheim, San Diego, San Francisco, San Jose has Santa Clara wired
but suppressed at name level), Louisville KY (Jefferson PVA hides owner
names from public REST — V484 confirmed), Seattle WA (King County
Property layer omits owner field — V484 confirmed), Pittsburgh PA
(Allegheny classifies but doesn't name), Orlando FL (OCPA web-search only;
covered by fl_statewide statewide), Monroe NY/Rochester (not in NYS public
dataset), Charleston SC (no public REST with owner field — V484 probed).

**STATEWIDE OWNER SUPPRESSION** (V487 environmental findings — don't probe
counties in these states for the owner pillar):
- **New Jersey** statewide — Daniel's Law (P.L. 2020 c.125) explicitly
  redacts OWNER_NAME from all hosted parcel feeds. Bergen County confirmed.
  Don't probe Hudson, Essex, Middlesex, Monmouth, Camden, etc.
- **Utah** statewide — UCA 63F-1-506 keeps owner names off the LIR
  sharing program. Salt Lake County confirmed. All UT counties affected.
- **California** counties (general pattern) — LA, San Diego, Bay Area,
  Sacramento all suppress owner names in public REST per county-assessor
  policy. Treat all CA counties as suppressed for owners. (CA city-level
  permit data is still wireable where the city publishes it — Sacramento
  V486 is the working example.)
- **New York non-NYC** — NY ORPTS-sourced county feeds strip OWNER fields.
  Westchester confirmed. Only NYC PLUTO survives for the NY owner pillar.

## Ready to Wire (confirmed endpoint + contractor field)
<!-- Format: - CityName ST: platform resource_id, contractor_field: fieldname, tested: date -->
- Greensboro NC: arcgis MapServer gis.greensboro-nc.gov OpenData_HRES_DS/2 BI_Permits — was already in CITY_REGISTRY as key "greensboro" but field_map mapped Contractor→contact_name (typo) and zip→Zoning (wrong). V342 fixed both. V340's duplicate "greensboro_nc" entry was reverted.
- Asheville NC: ALREADY correctly wired at "asheville_nc" → slug "asheville". Endpoint gis.ashevillenc.gov AccelaPermitsView/2 fresh through 2026-05-26 with real businesses (8MSOLAR LLC, LEDFORD ELECTRIC, AMERICAN AIR HEATING & COOLING). 64,383 records, 1,719 already collected. Uses contact_name slot but V180 fallback handles it. NC has no bulk state license DB so phones are DDG-only.
- Raleigh NC: Socrata, resource_id: building-permits, contractor_field: contractor_company_name. Researched 2026-04-25. **V361 wiring probe 2026-04-26: "raleigh" already in CITY_REGISTRY at services.arcgis.com/v400IkDOw1ad7Yad ArcGIS (180-day window) with rich contractor fields. Socrata federated search for "raleigh building permits" returned 0 results. The existing ArcGIS endpoint is the canonical one — re-confirm intent or provide an alternate Socrata host/resource id.**
- Virginia Beach VA: ArcGIS Hub, dataset_id: 15292e05..., contractor_field: contractor_name. Researched 2026-04-25. **V361 wiring probe 2026-04-26: dataset_id is truncated; can't be resolved as-is. Socrata federated search returned only Maryland Beach Buffer datasets. Need full ArcGIS Hub URL or feature service URL to wire.**
- Tulsa OK: Socrata, resource_id: okc-permits, contractor_field: primary_contractor. NOTE: was in Dead Ends (V339 only found StoryMap via ArcGIS search — Socrata dataset found via manual research). Researched 2026-04-25. **V361 wiring probe 2026-04-26: "okc-permits" prefix suggests Oklahoma City not Tulsa. Socrata federated search for "tulsa permits" returned 0 results. Likely a copy/paste error — confirm whether the dataset is actually OKC-wide or Tulsa-specific.** **V429 re-probe 2026-04-27: gis2-cityoftulsa.opendata.arcgis.com DCAT returned 103KB feed but zero permit/construction/violation/building matches. Tulsa really doesn't publish a permit dataset to ArcGIS Hub. → STAYS dead.**
- Omaha NE: CivicData, resource_id: blds-data, contractor_field: contractor_license. NOTE: was in Dead Ends (V340 found 0 ArcGIS results — CivicData platform not searched). Researched 2026-04-25. **V361 wiring probe 2026-04-26: "CivicData" platform isn't in our supported list (we handle socrata/arcgis/carto/ckan/accela). Need a concrete REST endpoint URL to wire — e.g. https://opendata-omaha.opendata.civicdata.com/resource/blds-data.json or similar.** **V429 probe 2026-04-27: opendata-omaha.opendata.civicdata.com hostname does not resolve (DNS NXDOMAIN). Even if we found a working Omaha CivicData URL, our collector doesn't support the CivicData platform — would need a new platform handler. → STAYS in Dead Ends pending platform support.**
- Salt Lake City UT: Socrata, resource_id: 5gsj-w587, contractor_field: contractor_name. Researched 2026-04-25. **V361 wiring probe 2026-04-26: resource_id 5gsj-w587 returns 404 on opendata.utah.gov + data.slc.gov; Socrata federated catalog returns 0 results. Need correct host domain or alternate resource_id.** **V362 wiring probe with corrected id 3eji-gn2j: dataset exists on opendata.utah.gov but applicant_name is INDIVIDUAL names (Andrew Carey, Rebecca Delis) not businesses; newest record 2023-10-26 (18 months stale). Fails both freshness + contractor rules.**
- Virginia Beach VA: V362 wiring probe 2026-04-26 — found at services2.arcgis.com/CyVvlIiUfRBmMQuu Building_Permits/0. Schema has PermitNumber/Type/ApplicationDat/IssueDate/address/GPIN but ZERO contractor/applicant/business fields. Newest ApplicationDat 2026-03-13 (44 days stale, just past the <30 day rule). Candidate for no-contractor wiring once freshness improves; mark as Part-C-eligible.
- Tulsa OK: V362 wiring probe 2026-04-26 — gis2-cityoftulsa.opendata.arcgis.com Hub returns federated cross-org results (Naperville, Leon County, Durham), no Tulsa-specific permit dataset. Tulsa doesn't publish on this Hub.

## Ready to Wire (NO contractor field — address/owner data only)
<!-- These cities have working permit APIs with addresses and dates but NO contractor name field.
     Still valuable for solar/investor buyers who want property owner info.
     Template should show "No contractor data available" message.
     Owner enrichment via county assessor matching makes these cities monetizable. -->
- San Francisco CA: Socrata DataSF, has building permits SODA API. No contractor field but has address+date+permit_type. **V363/V364 wiring probe 2026-04-26: data.sfgov.org/resource/i98e-djp9.json works; newest permit_creation_date 2025-09-15 (~7 months stale). Fails freshness rule.**
- Washington DC: ArcGIS Hub, has current-year permits. PERMIT_APPLICANT is individual names (not businesses) — still useful for address/owner matching. **V364 probe 2026-04-26: opendata.dc.gov dataset URL 404; needs corrected feature service URL.**
- Boston MA: Socrata (Analyze Boston), has building permits + code violations. Applicant is individual licensee names — still useful for address/owner. **V364 wiring confirmed 2026-04-26: ALREADY in CITY_REGISTRY at boston/CKAN/6ddcd912-32a0-43df-9908-63574f8c7e77 with correct field_map. CKAN endpoint reachable from local probe (newest issued_date 2026-04-24 — fresh!) but Render reports last_error="http_unknown" and 0 permits in DB despite last_collection 2026-04-26. Likely Render egress IP / CDN issue. Needs SSH-side debug.**
- Baltimore MD: Socrata (Open Baltimore), has SODA API for housing code + building citations. No contractor field but has address data. **V364 probe 2026-04-26: data.baltimorecity.gov resource fauu-ji8n redirects to hub.arcgis.com/legacy — Baltimore has migrated off Socrata; needs new ArcGIS Hub URL.**
- Denver CO: Socrata open data portal, has construction permits with address data. No contractor field. No state licensing DB either. **V364 probe 2026-04-26: data.denvergov.org redirects to denvergov.org/opendata — needs current resource path.**
- Tucson AZ: ArcGIS gis.tucsonaz.gov PDSD_ResidentialBldg MapServer/85, 31-field schema with address+project data. No contractor/applicant field. **V363 confirmed 2026-04-26: ALREADY wired in CITY_REGISTRY at "tucson"/mapdata.tucsonaz.gov path with 2,751 permits already collected. Probe of gis.tucsonaz.gov path showed newest 2026-04-21 (5 days fresh). V362 Part A's no-contractor template fallback handles the missing contractor section.**
- Lincoln NE: ArcGIS gis.lincoln.ne.gov Residential_New_Construction_Permits MS/0, 53 fields with address data. No contractor field. **V363 wired 2026-04-26 (PR #268): migrated from Accela HTML scraper to ArcGIS. Newest SD_APP_DD 2026-04-10 (16 days fresh). V362 Part A handles missing contractor section.**
- Tempe AZ: has permit API (V314 confirmed data exists). No usable contractor field.
- Madison WI: has permit API. No contractor field exposed.
- Aurora CO: has permit API. No contractor field in source.

## Needs Investigation (promising but unverified)
<!-- Cities with known open data portals but endpoint not yet SSH-tested -->
<!-- Tallahassee resolved in V343: switched endpoint from TLC_OverlayPermitsActive_D_WM/0
     (stuck commercials 2018-2021) to TLC_OverlayPermitsActiveTrends_D_WM/2
     (Single Family Last 1 Year, updated nightly). Real ContractorPhone inline.
     FL DBPR import will lift license-only contractors. -->
- Louisville KY: ArcGIS Hub louisville-metro-opendata-lojic.hub.arcgis.com. Permits: Active Construction Permits + Historical All Permits. Violations: Building Code Permit Enforcement Cases + Property Maintenance Inspection Violations (30-day rolling, daily updates). Contractor field unconfirmed. Top-30 city. BLITZ 2026-04-26. **V420 RESOLVED 2026-04-26: ArcGIS Active_Construction_Permits FeatureServer/0 confirmed live with CONTRACTOR field; CITY_REGISTRY "louisville" entry slug fixed from "louisville" → "louisville-ky" (was hitting louisville-co Colorado). Existing 4,362 KY permits orphaned under "louisville-co" — needs follow-up SQL relink.**
- Atlanta GA: ArcGIS Hub building permits + code enforcement. Previous probe (V341) got ECONNREFUSED + TLS cert invalid — may have been fixed since. Worth a re-probe. **V421 probe 2026-04-26: opendata.atlantaga.gov returns SSL CERTIFICATE_VERIFY_FAILED Hostname mismatch — cert issue still unfixed. → MOVE to Dead Ends.**
- New Orleans LA: Socrata open data portal, has "Code Enforcement Active Pipeline." Permit contractor field unconfirmed.
- Detroit MI: ArcGIS-based, tracks blighted properties + permits. Previous probe said "not a Socrata portal" but ArcGIS may work. Worth a re-probe. **V421 probe 2026-04-26: data.detroitmi.gov DCAT returns 1.3MB feed but ZERO active building permits — only "Right of Way Permits Historic" frozen 2021-10-13 + ACP/health/ZIP datasets. → MOVE to Dead Ends.**
- Pittsburgh PA: WPRDC CKAN data.wprdc.org. Permits: pli-permits (2019+). Violations: pittsburgh-pli-violations-report (PLI/DOMI/ES, 2015-present, daily). ALSO has "Licensed Businesses, Contractors & Trades" dataset — potential contractor name source! OKFN census confirms open permit data. BLITZ 2026-04-26.
- Montgomery County MD: OKFN census entry exists. Open data portal likely has permits. Worth checking.
- Norfolk VA: Socrata data.norfolk.gov. Permits: fahm-yuh4 (daily updates). Violations: mxtv-99gh (Neighborhood Quality Code Enforcement Cases) + agip-sqwc (Violations). Previous dead-end entry was ArcGIS-only probe — Socrata not checked. Contractor field unconfirmed — need SSH test. BLITZ 2026-04-26. **V417 probe 2026-04-26: fahm-yuh4 fresh through 2026-04-26 (15 fields: permit_number/address/gpin/tax_account/type/use_class/work_type/use_type/structure/status/application_date/total_balance/square_footage/code_edition/next_five_year_inspection). NO contractor/applicant/business/firm field. → MOVE to "Ready to Wire (NO contractor field)".**
- Cincinnati OH: Socrata data.cincinnati-oh.gov. Permits: thvx-5mem (Building Permits Combo, 2014+, daily refresh). Violations: cncm-znd6 (Code Enforcement, daily refresh). Contractor field unconfirmed. BLITZ 2026-04-26. **V417 probe 2026-04-26: actual Building Permits dataset is uhjb-xac9 (thvx-5mem is "Permits Combo Records" with no contractor data either). uhjb-xac9 fresh through 2026-04-26, 24-field schema (permitnum/description/applieddate/issueddate/originaladdress1/permitclass/statuscurrent/workclass/permittype/estprojectcostdec/units/pin/proposeduse/expiresdate/fee/link/neighborhood). NO contractor/applicant field. Companion vmk6-gy84 "Permits Contacts" has only clerk login IDs ("CBOSTWICK") not contractor names. → MOVE to "Ready to Wire (NO contractor field)".**
- Kansas City MO: Socrata data.kcmo.org. Permits: building permits dashboard (resource ID TBD). Violations: mnjv-uy2z (Code Violations). Previous dead-end said "11 months stale" — re-check freshness, may have been refreshed since. BLITZ 2026-04-26. **V420 re-probe 2026-04-26: catalog returned 10 permit datasets; freshest are 6h9j-mu65 + ntw8-aacc both modified 2025-06-13 (10+ months stale as of 2026-04-26). Still dead-by-freshness. → KEEP in Dead Ends.**
- Richmond VA: Socrata data.richmondgov.com. Permits: on portal (resource ID TBD). Violations: needs investigation. Contractor field unconfirmed. BLITZ 2026-04-26. **V424 RETRACTION 2026-04-27: V422's "ryhf-m453 confirmed live" was wrong. The dataset surfaced via Richmond's federated-catalog mirror but its actual `domain` is `data.cityoforlando.net` — it's an Orlando dataset (already wired in PermitGrab). Domain-restricted catalog query (?domains=data.richmondgov.com) returned zero permit datasets. Richmond's own portal has no native permits feed. Confirms the V363 dead-end note on line 171. → STAYS in Dead Ends.**
- Chattanooga TN: Socrata chattadata.org. Permits: 764y-vxm2 (All Permit Data, BLDS format). Violations: TBD — search chattadata.org for code enforcement. Contractor field unconfirmed. BLITZ 2026-04-26. **V417 probe 2026-04-26: chattadata.org returns Pantheon "No Site Detected" placeholder — domain is decommissioned. → MOVE to Dead Ends.**
- Syracuse NY: ArcGIS data-syr.opendata.arcgis.com. Permits: on portal. Violations: Code Violations dataset (2cc4e180fc6540fbb4fc6fafde311d7b). Contractor field unconfirmed. BLITZ 2026-04-26. **V421 probe 2026-04-26: DCAT shows "Permit Requests" cf857fb2f72a448c8ef712cc667768c4_0 last modified 2025-11-12 (5+ months stale = fails freshness rule). "Code Violations" 107745f070b049feb38273a7ab200487_0 IS fresh (2026-04-26) but we don't onboard violation-only cities. → MOVE permits to Dead Ends.**
- Lexington KY: CivicData civicdata.com/organization/lexington-ky + ArcGIS data.lexingtonky.gov. Permits: CivicData building permits + AgencyCounter. Violations: TBD. Accela portal at aca-prod.accela.com/LEXKY. Contractor field unconfirmed. BLITZ 2026-04-26. **V424 re-probe 2026-04-27: data.lexingtonky.gov DCAT (1.7MB feed) has ZERO datasets matching permit/construction/violation. Confirms V363 dead-end. CivicData remains unsupported by our collector. → STAYS in Dead Ends.**
- Fort Worth TX: Socrata data.fortworthtexas.gov. Permits: qy5k-jz7m (building permits). Violations: spnu-bq4u (Code Violations, updated 3x daily!). Previously marked dead-end for "no contractor field" — re-check; even without contractor, viable as no-contractor city. BLITZ 2026-04-26. **V417 probe 2026-04-26: qy5k-jz7m doesn't exist — federated Socrata search returns only quz7-xnsy (Development Permits, last updated 2025-07-17 = 9mo stale) and i6gk-um2v (Dashboard, frozen 2024-01-05). Fort Worth's Socrata permits feed has no fresh dataset. Violations spnu-bq4u not yet probed. → MOVE permits portion to Dead Ends; re-probe violations separately.**
- Milwaukee WI: CKAN data.milwaukee.gov. Permits: buildingpermits dataset (monthly CSV). Violations: Accela aca-prod.accela.com/MILWAUKEE enforcement. Previously investigated — zero profiles. Re-check with CKAN endpoint. BLITZ 2026-04-26. **V422 probe 2026-04-26: CKAN package "buildingpermits" id 9bada2e0-fad5-4545-8674-1b2c8c4e9f2f exists, metadata_modified 2026-04-26 (today). Self-described as "Update Frequency: Monthly" + CSV-only download. Borderline by freshness rule (monthly cadence, actual record dates not yet sampled). Defer pending CSV probe; if records are <30 days old, this is a no-contractor-data candidate (notes say "date, construction cost and location" only).**
- St Louis MO: Own portal stlouis-mo.gov/data. Permits: building permits via API/Web Service. Violations: not found in initial search. Contractor field unconfirmed. BLITZ 2026-04-26. **V422 probe 2026-04-26: data.stlouis-mo.gov returns CommonSpot HTML page (CMS, not a Socrata catalog or open-data API). No machine-readable endpoint at this path. → MOVE to Dead Ends.**
- Boise ID: ArcGIS city-of-boise.opendata.arcgis.com. Permits: New Residential Permits dataset. Violations: TBD. Contractor field unconfirmed. BLITZ 2026-04-26. **V426 probe 2026-04-27: city-of-boise.opendata.arcgis.com DCAT has 2 fresh datasets ("New Residential Permits" + "New Housing Construction and Demolitions" both modified today) at services1.arcgis.com/WHM6qC35aMtyAAlN/Housing_OpenData/FeatureServer/0+1. Schema confirms ZERO contractor/applicant/builder/business fields — only RecordID/PermitStatus/PropertyAddress/IssuedDate/Type. Confirms V290 dead-end by-no-contractor. Already in CITY_REGISTRY at the smaller PDS_BuildingPermits_HighImpact endpoint (139 records, also no contractor). → STAYS in Dead Ends for contractor leads. Could be re-wired as a no-contractor city if we expand the larger Housing_OpenData dataset.**
- Corpus Christi TX: ArcGIS Hub gis-corpus.opendata.arcgis.com. Permits: may have building permits under GIS datasets. Dynamic Portal for permit apps. Contractor field unconfirmed. BLITZ 2026-04-26. **V424 probe 2026-04-27: gis-corpus.opendata.arcgis.com DCAT feed has ZERO datasets matching permit/construction/violation. → MOVE to Dead Ends.**
- Honolulu HI: Socrata data.honolulu.gov. Permits: 3fr8-2hnx (Building Permits, 2010-2016 — STALE, need to check for newer dataset). Violations: TBD. Previous dead-end entry was wrong ("no open data portal") — they have Socrata. BLITZ 2026-04-26. **V426 probe 2026-04-27: federated Socrata catalog (api.us.socrata.com?domains=data.honolulu.gov) returned 6 building-permit datasets but ALL stale: ycwt-ujqt 2025-02 (frozen 2016), 3fr8-2hnx 2025-02 (frozen 2016), 4vab-c87q "Building Permits Jan 2005 - June 30 2025" with explicit June-2025 cutoff (= 10mo stale by 2026-04). Data publishing stopped mid-2025. → STAYS dead by freshness.**
- Greensboro NC violations: Already wired for permits. Violations: Code Compliance All Violations dataset at data.greensboro-nc.gov (2011-present). Add violations config. BLITZ 2026-04-26. **V428 re-confirmed 2026-04-27: gis.greensboro-nc.gov/.../OpenData_CC_DS/MapServer/3 schema is rich (CaseNumber/ViolationCode/ViolationDescription/IssuedDate/FullAddress) but newest IssuedDate is 2024-06-18 (~22mo stale). Hub modified-date 2026-04-20 reflects metadata edits, not records. Duplicates V363 dead-end note. → STAYS dead.**
- Raleigh NC violations: Already wired for permits. Violations: check data-ral.opendata.arcgis.com Public Safety section for code enforcement. BLITZ 2026-04-26. **V428 probe 2026-04-27: data-ral.opendata.arcgis.com DCAT (845KB) has only "Food Inspection Violations" (fresh, but not code-enforcement) + stale "Permitted Environmental Health Compliance Facilities" (2023-05). No building code-enforcement dataset. → MOVE to Dead Ends for violations.**

## Quick Wins — Tier Promotion Actions (V481/V484 Discovery Audit, 2026-05-01)

These are the highest-ROI actions to move existing cities from Tier 4 → Tier 5 (ad-ready).
Discovered during full DB audit of all 300 cities across all 5 pillars.

| # | City | Missing Pillar | Fix | Effort |
|---|------|---------------|-----|--------|
| 1 | ~~Miami-Dade County~~ | ~~Owners show 0~~ | **DONE V482**: 81,833 rows realigned via SQL UPDATE. Cache reflects. | DONE |
| 2 | ~~Hialeah FL~~ | ~~Violations~~ | **DEAD END (V483)**: CCVIOL_gdb has NO MUNICIPALITY field, unincorporated MDC only. Stays at 4/5. | N/A |
| 3 | Cape Coral FL | Owners | `fl_statewide` (FL DOR NAL) is wired and covers Lee County. Verify rows landing for Cape Coral after next worker cycle. | DB verify only |
| 4 | Orlando FL | Owners | `fl_statewide` covers Orange County. Same verify-only step as #3. | DB verify only |
| 5 | ~~New Orleans LA~~ | ~~Owners~~ | **DONE V483b**: `orleans_new_orleans` wired against gis.nola.gov MapServer/15. SSL bypass added in V483b cleanup. | DONE |
| 6 | Minneapolis MN | Violations | CONFIRMED DEAD — 311 data has no address field (V433d). Stays at 4/5. | N/A |
| 7 | LA (open/closed) | Owners | CONFIRMED DEAD — CA suppresses owner names in public data. Stays at 4/5. | N/A |
| 8 | Tucson AZ | Violations | **DONE V484**: ENGOV_CodeCases at mapdata.tucsonaz.gov (21K records, fresh today). Promotes Tucson from Tier 3 → 4. | DONE |
| 9 | Washington DC | Violations | **DONE V484**: 311 ServiceRequests/FeatureServer/21 filtered by `ORGANIZATIONACRONYM='DOB'` (164K county-wide → ~10s of thousands DOB-only). Promotes DC from Tier 3 → 4. Annual layer rotation needed every Jan. | DONE |
| 10 | Jacksonville FL | Owners | **DONE V484**: `duval_jacksonville` at maps.coj.net Parcels/MapServer/0 (405K daily-fresh, full mailing address segmentation). Promotes Jax from Tier 3 → 4. | DONE |
| 11 | Spokane WA | Owners | **DONE V484**: `spokane_spokane` at gismo.spokanecounty.org SCOUT/PropertyLookup (138K nightly). Promotes Spokane from Tier 3 → 4. | DONE |

**Open dead-ends (don't re-research):**
- Portland OR violations — no public REST for code enforcement (V484 confirmed)
- Tampa FL violations — Accela UI only (V484 confirmed)
- Tallahassee FL violations — only inspector-area polygons, no case feed (V484 confirmed)
- Charleston SC permits + violations — no public open-data hub (V484 confirmed)
- Des Moines IA permits + violations — no permit datasets in DCAT (V484 confirmed)
- Providence RI permits — Socrata data frozen at 2018 (V484 confirmed)
- Louisville KY owners — Jefferson PVA hides names from public REST (V484 confirmed)
- Seattle WA owners — King County Property layer omits owner field (V484 confirmed)
- Atlanta GA permits — CSV-only frozen 2024-08; owners now wired via fulton_atlanta + dekalb_atlanta (V486 re-confirmed permits dead)
- Memphis TN permits — no Accela hybrid path; Shelby owner data stale 2024 (V486 re-confirmed)
- Sedgwick County KS / Wichita owners — Imperva WAF blocks REST owner endpoint (V486)
- Knox County TN / Knoxville owners — NTLM 401 on kgis.org REST (V486 — internal-only)

**V486 reversal (was dead, now LIVE):**
- ~~Sacramento CA permits~~ — V258 probed SACOG (regional), not the city. Real permits with Contractor field at services5.arcgis.com/.../BldgPermitIssued_CurrentYear (6,089 records, fresh 2026-04-26). Now wired in V486.

**Deferred (needs UX decision, not a research blocker):**
- **Birmingham AL** (V484): no public permits feed. Owners-only via `jefferson_birmingham`
  (88K Birmingham parcels, nightly fresh). Onboarding requires inventing
  `no_permits_owner_only` platform pattern + decisions for owners-only city
  page UX. Source endpoint:
  `https://jccgis.jccal.org/server/rest/services/Basemap/Parcels/MapServer/0`.
- **Savannah GA** (V484): permits-only (commercial + residential FeatureServer
  layers at pub.sagis.org/.../BuildingPermit_FC, ~2.4K records, 14 days fresh).
  Status uncertain post-V484 deploy — Code reported "✓ savannah_ga (1,811
  residential)" but verify the commercial layer and city registry entry
  landed in city_registry_data.py.

### Full Tier Audit Results (2026-05-01, post-V484)

**Tier 5 (all 5 pillars — ad-ready): 12 cities**
Chicago, Phoenix, San Antonio, NYC, Cleveland, Austin, Cape Coral, Fort Lauderdale,
Mesa, Orlando, St. Petersburg, **Miami-Dade (V482 promotion via owner slug fix)**

**Tier 4 (4/5 pillars): 12 cities**
Hialeah (missing: violations — dead end),
LA (missing: owners — dead end), Minneapolis (missing: violations — dead end),
Nashville, **New Orleans (V483b owners landed → likely Tier 5 after next stats refresh)**,
Philadelphia, Portland OR (violations dead-end confirmed V484),
San Jose, **Tucson (V484 violations landed)**, **DC (V484 violations landed)**,
**Jacksonville (V484 owners landed)**, **Spokane (V484 owners landed)**

**Tier 3 (3/5 pillars): 12 cities**
Anaheim, Buffalo, Cincinnati, Denver, Henderson NV, Las Vegas,
Louisville (owners dead-end confirmed V484), Raleigh,
Seattle (owners dead-end confirmed V484), Syracuse,
Tallahassee (violations dead-end V484), Tampa (violations dead-end V484)

**Tier 2 (permits + some profiles): 59 cities**
**Tier 1 (permits only or fewer): 205 cities**

**Owners-only candidates (new metros, no public permit feeds available):**
- Birmingham AL — V484 deferred. 88K Birmingham parcels via jefferson_birmingham.
- Detroit MI — V486 wired. 378K parcels, daily fresh.
- Toledo OH — V486 wired. 204K parcels.
- Atlanta GA — V486 wired (Fulton + DeKalb). 617K combined Atlanta-metro parcels.
- St. Louis County MO — V486 wired. 401K parcels (suburbs only; St. Louis CITY is a separate jurisdiction not yet covered).

All five share the same UX requirement deferred from V484: a `no_permits_owner_only`
platform pattern + city-page template variant for owners-only metros. With 5
candidates plus Charleston (probed dead but might thaw), this is now a real
second product SKU rather than a one-off science project.

**Permits-only candidate (new metro):** Savannah GA — V484 deployed, verify post-cycle.

**V486 NEW PERMIT WIN:** Sacramento CA — V258 dead-end overturned. Live
endpoint at services5.arcgis.com/.../BldgPermitIssued_CurrentYear with real
Contractor field. CA CSLB enrichment will lift phones once the worker cycles.

See PermitGrab_City_Discovery_100.xlsx for full breakdown with per-city pillar data.

## Research Resources
<!-- Use these to find endpoints for any queued city -->
<!-- OKFN US City Open Data Census: http://us-cities.survey.okfn.org/dataset/construction-permits.html -->
<!-- Socrata Open Data Network: https://www.opendatanetwork.com/ -->
<!-- ArcGIS Hub Search: https://hub.arcgis.com/ -->
<!-- Accela CivicData: https://www.civicdata.com/ -->
<!-- Data.gov catalog: https://catalog.data.gov/dataset/?tags=permits -->


## Dead Ends (skip forever)
<!-- One line per city. Include why. -->
- Tempe AZ: no usable contractor field (V314 wasted)
- Aurora CO: no contractor field in source
- Boulder CO: no building permits feed, only ROW/parking permits
- Honolulu HI: ACTUALLY HAS Socrata portal data.honolulu.gov with building permits (3fr8-2hnx) but data is 2010-2016 only — STALE. MOVED TO NEEDS INVESTIGATION to check for newer dataset.
- Madison WI: no contractor field exposed
- St Paul MN: stale endpoint
- Lansing MI: no building permit API
- Las Vegas NV violations: ECONNREFUSED on opendata portal. **V431 re-probe 2026-04-27 per CODE_V428 Phase 2b: opendata.lasvegasnevada.gov returns DNS NXDOMAIN. The Socrata resource id u3ci-m9hj cannot be reached from Render egress. Same trap as the V313 finding in CLAUDE.md. → STAYS dead.**
- Pittsburgh PA property owners: **V433h probe 2026-04-27: WPRDC `property-assessments` (datastore 9a1c60bd-f9f7-4aba-aeb7-af8c3aaa44e5, modified 2026-04-01) has 87 fields with PARID + PROPERTYADDRESS + PROPERTYCITY + PROPERTYZIP + CHANGENOTICEADDRESS1/2/3/4 + OWNERCODE + OWNERDESC + LEGAL1/2/3 + sale prices + condition + style + ... but NO actual OWNER NAME field. OWNERCODE/OWNERDESC are owner-type classifications ("CORPORATION", "INDIVIDUAL") not names. Same suppression pattern as CA cities (LA, OC). PA Allegheny County deliberately omits owner identity in public publications. → STAYS dead. Pittsburgh stays at 4/5 if violations + profiles + permits are in.**
- Minneapolis MN violations: **V433d probe 2026-04-27 per CODE_V428 Phase 2c: opendata.minneapolismn.gov has "Public 311 2026" (modified 2026-04-26, fresh) at services.arcgis.com/afSMGVsC7QlRK1kZ/Public_311_2026/FeatureServer/0. 14-field schema with CASEID, SUBJECTNAME, REASONNAME, TYPENAME, OPENEDDATETIME, XCOORD, YCOORD — but NO address field. Violation rows need a textual address to join against permits.address; reverse-geocoding from X/Y is out of scope. → STAYS dead. Owners shipped via V433b Hennepin so Minneapolis sits at 4/5.**
- LA County property owners: **V432 probe 2026-04-27 per CODE_V428 Phase 1f: confirmed structurally unavailable. LA County deliberately omits owner identity from BOTH the public ArcGIS feed AND the official "Assessor Parcel Data Rolls 2021–Present" CSV bundle (item 785f54236d1644dc975a55af19b3dd70). Feature service `services.arcgis.com/RmCCgQtiZLDCtblq/Parcel_Data_2021_Table/FeatureServer/0` has 51 fields — all situs/value/use-code/year-built — and zero owner/taxpayer/holder/name field. CSV data dictionary preview confirms the same schema with no owner column. The directive's "if no ArcGIS, use CSV" branch was based on a wrong assumption — the CSV doesn't have owner names either. To wire LA owners we'd need a paid commercial source (DataTree, FirstAmerican, ATTOM Data) OR per-property scraping of `portal.assessor.lacounty.gov` (likely ToS violation). → MOVE to Dead Ends. LA stays at 4/5 unless commercial data is approved.**
- San Antonio TX violations: **V431 probe 2026-04-27 per CODE_V428 Phase 2a: services.arcgis.com/g1fRTDLeMgspWrYp (the COSA AGOL org) lists 35+ services but none are building code violations — matches are all zoning/COVID/ZIP/law-enforcement (different domain). data-cos-gis.opendata.arcgis.com Hub search for "code violations" returns 0 hits. AGOL global search for COSA-owned violation services returns 0. The directive's "Planning and Development Code Violations" dataset is not surfacing on either path. → Defer pending a more specific URL or org id.**
- Houston TX: XLSX only, no API
- San Francisco CA: no contractor field in permit data
- Seattle WA: no contractor field in Socrata dataset
- Denver CO: no state licensing DB, minimal profiles
- Sacramento CA: CKAN portal 404
- Fresno CA: WAF 403 blocks all probes
- Oakland CA: no local permits available
- Dallas TX: data frozen since 2020
- Charlotte NC: no building permit datasets
- Indianapolis IN: no permits in open data catalog
- San Diego CA: all endpoints blocked (403 + ECONNREFUSED)
- Kansas City MO: contractor field exists but data 11 months stale — MOVED TO NEEDS INVESTIGATION for re-check (blitz 2026-04-26)
- Atlanta GA: all endpoints broken (ECONNREFUSED, TLS cert invalid)
- Detroit MI: not a Socrata portal
- Memphis TN: Accela only, no contractor column in grid
- Tampa FL: Accela only, no contractor column in grid
- Baltimore MD: no contractor field in permit data
- Oklahoma City OK: blocked by Incapsula WAF
- Washington DC: PERMIT_APPLICANT is individual names, not businesses
- Boston MA: applicant is individual licensee names, not businesses
- Naperville IL: data 14 months stale
- All California major cities except Anaheim: no contractor field in permit APIs
- Albuquerque NM: coageo.cabq.gov City_Building_Permits FS/0 has Owner+Applicant+Contractor fields populated with real businesses (TOFEL CONSTRUCTION, BRADBURY STAMM, HOME DEPOT) but newest DateIssued is 2024-04-12 — frozen 2 years (V339 probed 2026-04-25)
- Tucson AZ: gis.tucsonaz.gov PDSD_ResidentialBldg MapServer/85 — 31-field schema has zero contractor/applicant/business field; only PROJECTNAME and DESCRIPTION (V339 probed 2026-04-25)
- El Paso TX: ArcGIS portal returns mining permits + traffic permits; no building-permit feature service surfaces a contractor field (V339 probed 2026-04-25)
- Lincoln NE: gis.lincoln.ne.gov Residential_New_Construction_Permits MS/0 has 53 fields but none contain contractor/applicant/business/builder data (V339 probed 2026-04-25)
- Tulsa OK: MOVED TO READY TO WIRE — Socrata dataset found (okc-permits, primary_contractor). V339 only searched ArcGIS.
- Omaha NE: MOVED TO READY TO WIRE — CivicData dataset found (blds-data, contractor_license). V340 only searched ArcGIS.
- Jacksonville FL: ArcGIS portal returns only Jacksonville OREGON UGB; data.coj.net not indexed in Socrata federated catalog (V339 probed 2026-04-25)
- Reno NV: 0 ArcGIS Reno-specific results, only StoryMap reference (V340 probed 2026-04-25)
- Toledo OH: ArcGIS results are for Toledo SPAIN, no Toledo OH building permit feed (V340 probed 2026-04-25)
- Norfolk VA: 0 results in ArcGIS federated search (V340 probed 2026-04-25) — MOVED TO NEEDS INVESTIGATION: Socrata portal data.norfolk.gov has permits (fahm-yuh4) + violations (mxtv-99gh). V340 only probed ArcGIS.
- Riverside CA: only Riverside COUNTY permits surface (PLUSActivities_PD), no city-of-Riverside building permit feed (V340 probed 2026-04-25)
- Glendale AZ: no AZ-specific result; Glendale CA has a permits-by-walkshed analytical layer but no permit-record feed (V340 probed 2026-04-25)
- Lubbock TX: only a performance-metrics dashboard, no queryable feature service (V340 probed 2026-04-25)
- Hampton VA: 0 ArcGIS results for Hampton-specific permits (V341 probed 2026-04-25)
- Augusta GA: 0 ArcGIS results, no Augusta-Richmond County permit feed (V341 probed 2026-04-25)
- Modesto CA: 0 ArcGIS results (V341 probed 2026-04-25)
- Pembroke Pines FL / South Florida: 0 ArcGIS results (V341 probed 2026-04-25)
- Hartford CT: 0 building-permit ArcGIS results, only environmental + Census layers (V341 probed 2026-04-25)
- Huntington Beach / Long Beach CA: only a Survey123 inspection request form, no permit feed (V341 probed 2026-04-25)
- Rochester NY: code enforcement + demolitions feeds exist but no building-permits feed; full permit data lives on a 3rd-party tolemi.com SaaS (V341 probed 2026-04-25)
- Wilmington NC / New Hanover County: 0 ArcGIS results (V343-followup probed 2026-04-25)
- Charleston SC / Charleston County: only "Windshield Survey" damage-assessment app, no permit feed (V343-followup probed 2026-04-25)
- Savannah GA / Chatham County: only CRS_GMC_Maps community-rating inventory, no permit feed (V343-followup probed 2026-04-25)
- Cary NC: 0 ArcGIS results (V343-followup probed 2026-04-25)
- High Point NC: only Permit Activity Dashboards (3 of them), no queryable feature service (V343-followup probed 2026-04-25)
- Chapel Hill NC: only Ephesus-Fordham renovation density tile-services frozen 2004-2017, no live permit feed (V343-followup probed 2026-04-25)
- Hialeah FL violations: confirmed permanent dead-end. V483 SSH-tested CCVIOL_gdb schema: fields are OBJECTID/CASE_NUM/CASE_DATE/CASE_STATUS/STAT_DESC/ADDRESS/FOLIO/PROBLEM/PROBLEM_DESC — NO MUNICIPALITY field. ADDRESS LIKE '%HIALEAH%' returns 0, ZIP filtering returns 0. CCVIOL_gdb covers unincorporated Miami-Dade County only, not incorporated municipalities like Hialeah. Hialeah would need their own code enforcement portal to publish a feed. Stays at 4/5.
- McKinney TX: services1.arcgis.com B8MwidgHpU2dWUmv UnderConstruction/0 has NameOfBusiness field but newest IssueDate is 2023-09-26 (frozen 2.5 years) and all sample NameOfBusiness values are blank (V345 probed 2026-04-25)
- Lewisville TX: 0 ArcGIS results (V345 probed 2026-04-25)
- Pearland TX: 0 ArcGIS results (V345 probed 2026-04-25)
- Round Rock TX: only Large Development Projects + Current Developments view (zoning/plats/annexation), no permit-record feed with contractor field (V345 probed 2026-04-25)
- Newport News VA: 0 ArcGIS results (V345 probed 2026-04-25)
- Knoxville TN: 0 ArcGIS results (V345 probed 2026-04-25)
- Surprise AZ: only "Active Projects" feature service with AZ-to-FL extent (not Surprise-specific) (V346 probed 2026-04-25)
- Goodyear AZ: 0 ArcGIS results (V346 probed 2026-04-25)
- Avondale AZ: 0 ArcGIS results (V346 probed 2026-04-25)
- Scottsdale AZ: only a Web Map ("Development Activity - Building Permits: Issued & Completed"), no underlying feature service URL exposed in catalog (V346 probed 2026-04-25)
- shovels.ai third-party datasets (V348 probed 2026-04-25): nationwide permit feeds at services5.arcgis.com/ygiShlCiglrHaijs/.../All_Permits_Started_during_4Q25 (965K permits) and per-trade nationwide feeds (electrical 220K, roofing 79K, new-construction 45K). LA-specific Esri Living Atlas dataset has gold schema (CONTRACTOR_NAME/PHONE/EMAIL/WEBSITE + APPLICANT_PHONE/EMAIL + OWNER_NAME/PHONE/EMAIL). DO NOT INGEST: shovels.ai is a paid SaaS competitor and ingesting their data raises ToS + competitive-IP concerns; the nationwide 4Q25 endpoint also returned 400 (likely access-restricted). Documented for awareness only — same logic as the no-state-portals rule (don't rely on data we don't control).
- Colorado Springs CO: Accela portal only (aca-prod.accela.com/COSPRINGS), no open data API for building permits (blitz 2026-04-26)
- Wichita KS: No open data portal, permits via MABCD portal only (no API), dead (blitz 2026-04-26)
- Reno NV violations: No code violations API found, permits also dead (confirmed blitz 2026-04-26)
- Anchorage AK: Open data policy exists but no specific building permits dataset confirmed on portal (blitz 2026-04-26). Re-confirmed via WebFetch (loop probe 2026-04-26): moa-muniorg.hub.arcgis.com DCAT has 41 datasets focused on geographic/zoning/watershed/property-tax — zero permits/building/construction/code. Permit lookup happens via separate bsd.muni.org HTML system, not a queryable feed.
- Aurora IL (loop probed 2026-04-26): opendata-cityofaurora.hub.arcgis.com DCAT has 47 datasets covering police, lead service line replacement, fire prevention metrics, business registration, crime stats, public art, downtown revitalization — none for permits/building/construction/code. (Distinct from Aurora CO which is documented as no-contractor V315.)
- Garland TX (loop probed 2026-04-26): both Hub paths broken — data-garland DCAT returns 500, open-data-garland-garland DCAT returns 404. Hub is misconfigured or migrating. Cannot query.
- Berkeley CA (loop probed 2026-04-26): gis.cityofberkeley.info Accela MapServer has 35 layers covering building outlines, parcels, zoning, hazard zones — but the actual permit records live behind the Accela citizen portal (HTML). Same dead-end pattern as Tampa/Memphis. Geographic-context only, no queryable permit feed.
- McAllen TX (loop probed 2026-04-26): no Hub at gis-mcallen.opendata.arcgis.com (DCAT 404). No public open-data portal detected.
- Olathe KS (loop probed 2026-04-26): no Hub at data-olathe.opendata.arcgis.com (DCAT 404). No public open-data portal detected.
- Stockton CA: data.stocktonca.gov exists but permits appear Accela-only, no open data API (blitz 2026-04-26)
- Redmond OR (V350 probed 2026-04-25): services2.arcgis.com B0h69gkZPiRSTUFu Accela_Permits/0 has 19 fields but NONE are contractor/applicant/owner/business — only TAXLOT, APP_NUMBER, STATUS, PERMIT_TYPE, ADDRESS, OPENED, DESCRIPTION, PROJECT_NAME. Dead despite the Accela_ prefix.
- Albuquerque NM (V368 probed 2026-04-26): coageo.cabq.gov/cabqgeo/rest/services/agis/City_Building_Permits/FeatureServer/0 has GOLD-LIKE schema — Owner + Applicant + Contractor fields populated with real businesses ("C&L HANDY LLC", "BRADBURY STAMM CONSTRUCTION INC."). BUT newest DateIssued is 2024-04-12 (~2 years stale); count of records with DateIssued >= 2026-01-01 = 0. Dataset frozen at April 2024 — fails freshness rule. Could be revived if City of Albuquerque resumes publishing.
- Bakersfield CA (V368 probed 2026-04-26): bakersfielddatalibrary-cob.opendata.arcgis.com Hub has no permit datasets in DCAT feed; gis.bakersfieldcity.us/webmaps/rest/services has only a Cadastre service — Planning folder has "MakingDowntown" only (story-map). Permits live behind Click2Gov HTML portal at bakeweb.ci.bakersfield.ca.us/Click2GovBP. No public REST API. Dead.
- Augusta GA (V368 probed 2026-04-26): geohub-augustagis.opendata.arcgis.com DCAT feed has 42 datasets (transit, planning case lookups, electoral, zoning, parcels, fire/crime) but NONE for permits, construction, or code. Dead.
- Laredo TX (loop probed 2026-04-26): data.openlaredo.com CKAN portal has Building Permits dataset (resource 7f70bf47-7c3d-4913-864f-f5557563cbd2) with CSV download at openlaredo.com/data/BuildingPermits.csv. Last updated 2023-10-06 — 18 months stale. Fails freshness rule.
- Salem OR (loop probed 2026-04-26): city-salem.opendata.arcgis.com Hub renders Structure Permits dashboard (item 1749afaae6294767bb4812c36d60ab40) but underlying FeatureServer URL is on a private ArcGIS Enterprise that doesn't expose sharing API metadata — item 1200f3f1-...-14f19e9a4517 returns "does not exist or is inaccessible" via arcgis.com/sharing/rest. Inconclusive without SSH-side probe of the Hub directly.
- Las Vegas NV: ALREADY WIRED in CITY_REGISTRY as "las_vegas" → slug "las-vegas" since V292 (services1.arcgis.com/F1v0ufATbBQScMtY OpenData_Building_Permits_/0). 426K total permits, APPLICANT field has real businesses, fresh through 2026-04-26 (loop confirmed). Pre-existing dead-end entry in CLAUDE.md V313 was for a different host (data.lasvegasnevada.gov / mapdata.lasvegasnevada.gov) — both routes resolve to the same data via different infra. No change needed.
- Rochester NY (loop probed 2026-04-26): data.cityofrochester.gov DataROC DCAT feed surveyed — only matching dataset is "Review" (Community Gardens with permits), modified 2022-07-14. No building/code-enforcement/violation datasets. Permits + code data live behind the closed BuildingBlocks tool + Civics property-management portal (no public API). Dead.
- Yonkers NY (loop probed 2026-04-26): only public ArcGIS asset is "Yonkers GIS Data Viewer" web map (item ac9ad150392742a8babc1145b7ed3df3) last modified 2013-07-17 (12 years stale). No active feature service exposed for permits. Dead.
- Akron OH (loop probed 2026-04-26): agis.akronohio.gov/server/rest/services lists 9 MapServers covering water, CSO notifications, AMI meters, recycling, tree-keeper — NO permit, building, or code-enforcement service. Permits behind their Plan Review portal HTML. Dead.
- Mobile AL (loop probed 2026-04-26): open-government-cityofmobile.hub.arcgis.com DCAT feed only has "Buildings" (footprints — polygons, not permits) and "ROW Permitting" (right-of-way / sidewalk / driveway). No building-permit feed. Dead.
- Modesto CA: already V341 dead (re-confirmed 2026-04-26 — eTRAKiT HTML portal only).
- Chattanooga TN (loop V363 re-probe 2026-04-26): chattadata.org/resource/764y-vxm2.json returns Pantheon "No site detected" error — site is decommissioned/misconfigured. Dead (V363 directive endpoint is gone).
- Kansas City MO ntw8-aacc (loop V363 re-probe 2026-04-26): newest record 2025-05-08 — ~12 months stale. Confirms V258 dead-end. Has real businesses ("Midwest Elevator", "KONE INC") but freshness gate fails. Dead.
- Lexington KY (loop V363 re-probe 2026-04-26): data.lexingtonky.gov DCAT feed has 9 datasets — watersheds, greenways, zoning, traffic, parks — none for permits / building / code. Dead.
- Boise ID (loop V363 re-probe 2026-04-26): opendata.cityofboise.org DCAT has "Boise Zoning Activities" + "Development Tracker Open Data" — both planning/zoning applications, NOT building permits. No contractor field. Dead for our use case.
- Honolulu HI 4vab-c87q (loop V363 re-probe 2026-04-26): "Building Permits Jan 1, 2005 through June 30, 2025" last_updated 2025-08-12 — frozen at June 2025. Dataset has explicit cutoff date. Dead.
- Richmond VA (loop V363 re-probe 2026-04-26): data.richmondgov.com federated catalog returns 0 Richmond-specific datasets (only Chicago/Calgary/Seattle/etc results). Portal isn't publishing permits via Socrata API. Dead via this path.
- Cincinnati OH thvx-5mem (loop V363 re-probe 2026-04-26): ACTUALLY FRESH — newest date_issued 2026-04-24. NO contractor field. WIRED IN V384 (Accela → Socrata migration). Joins no-contractor cohort.
- Norfolk VA fahm-yuh4 (loop V363 re-probe 2026-04-26): ACTUALLY FRESH — newest application_date 2026-04-22. NO contractor field. EXISTING V26 config had 5 wrong field names (ftpuser/permit_address/etc). FIELD_MAP FIXED IN V385.
- Syracuse NY (loop V363 re-probe 2026-04-26): item id 2cc4e180fc6540fbb4fc6fafde311d7b returns "Item does not exist or is inaccessible" via arcgis.com/sharing/rest. data-syr.opendata.arcgis.com Hub DCAT only has Assessment Final Roll datasets (2011, 2012). V363 directive's source for the Syracuse violations item ID was incorrect. Dead via this path.
- Greensboro NC violations (loop V363 Part A re-probe 2026-04-26): gis.greensboro-nc.gov OpenGateCity/OpenData_CC_DS/MapServer/3 (CC_All_Violations) — schema is rich (CaseNumber, ViolationCode, ViolationDescription, IssuedDate, CaseStatus, FullAddress, ResponsibleParty, ClearDate). BUT newest IssuedDate is 2024-06-18 (~22 months stale). The Hub modified-date refreshes (2026-04-20) reflect metadata edits, not new records. Dead by freshness.
- Atlanta GA (loop re-probe 2026-04-26): "All Building Permits 2019-2024" item 655f985f43cc40b4bf2ab7bc73d2169b is a STATIC CSV (type=CSV, owner=gpickren2) last modified 2024-08-08. No live FeatureServer behind it — Atlanta dropped its Accela export to a one-shot CSV. Department of City Planning Hub (dpcd-coaplangis.opendata.arcgis.com) DCAT has zero permit datasets. gis.atlantaga.gov/arcgis/rest 404s. Confirmed dead; V258 dead-end stands.
- Detroit MI (loop re-probe 2026-04-26): data.detroitmi.gov DCAT has 43 datasets (transportation, public safety, health, parcels, zones) but ZERO matching "permit / building / construction / violation". Confirmed dead.
- Sacramento CA (loop re-probe 2026-04-26): data.cityofsacramento.org DCAT has 32 datasets (parks, transportation, council, EV chargers, libraries) but ZERO permits/building/construction. Confirmed dead via this hub.
- Memphis TN (loop re-probe 2026-04-26): only Develop901 Accela portal (aca-prod.accela.com/SHELBYCO style); no public ArcGIS hub for Memphis-specific permits. Confirmed Accela dead-end (CLAUDE.md P1).
- Tampa FL (loop re-probe 2026-04-26): city-tampa.opendata.arcgis.com Hub has "Single Family Permits" item 3a025f4287f14e37b6a9dc55100031f1 (modified 2026-04-26 — fresh metadata), backed by arcgis.tampagov.net OpenData/Planning/MapServer/32. Schema (40 fields via Hub CSV download): X, Y, OBJECTID, RECORD_ID (BLD-XX format), TYPE, SUBTYPE, APPLICATION_TYPE, APPLICATION_STATUS, OPENED_DATE, ADDRESS, ZIP, NEIGHBORHOOD — NO CONTRACTOR/APPLICANT/BUSINESS field. Direct FeatureServer requests return 403 to WebFetch (likely IP whitelist on tampagov.net). Render egress should be tested separately — if Render can reach it, wirable as no-contractor city via V362 template. Defer to SSH-side test before wiring; current Tampa Accela config in CITY_REGISTRY remains the dead source meanwhile.
- New Orleans LA (loop re-probe 2026-04-26): ALREADY WIRED at "new_orleans" / slug "new-orleans" → data.nola.gov/resource/rcm3-fn58.json. Newest issuedate 2026-04-25 (1 day fresh). Schema confirmed: applicant + contractors fields both populated; field_map at line 396 already maps contractor_name → "contractors" (V258). Current contractor mix ~50/50 individuals vs LLCs ("MGI Construction LLC", "Pinnacle Exterior Construction LLC", "Greenbull Enterprises LLC"). Working as designed; no change needed.
- Spokane WA (loop re-probe 2026-04-26): ALREADY WIRED at "spokane" / slug "spokane" → services.spokanegis.org Permit_WM_Dynamic2/MapServer/0 (V33). Verified via parallel-discovery on data-spokane.opendata.arcgis.com Hub: services.arcgis.com/3PDwyTturHqnGCu0/.../Permit/FeatureServer/3 has 76,710 records with UpdatedDT > 2026-04-01 (very fresh) BUT OpenDate field is sentinel-dated (1956 placeholder), no contractor field. Existing V33 endpoint should still be working — prod admin verification pending. WA L&I license import lifts phones for any business names that match.
- Saint Paul MN (loop re-probe 2026-04-26): information.stpaul.gov ArcGIS Hub DCAT only has "Approved Building Permits Dashboard" modified 2022-02-08 (~4 years stale). Other items (Vacant Buildings, Ethos Criminal) frozen 2022-2023. Confirmed dead — V315/V317 stale-finding stands.
- Fremont CA (loop re-probe 2026-04-26): fremont-ca-open-data-cofgis.hub.arcgis.com DCAT items "Permits" and "Tree Permits" both modified 2022 (~3 years stale). "Development Activity" FeatureServer modified 2023-06-23. No live building-permit feed. Confirms V290 dead.
- Plano TX (loop re-probe 2026-04-26): data-planogis.opendata.arcgis.com Hub DCAT returns 500, search returns 404 — Hub is misconfigured. Confirmed dead via this path; V290 stale-finding stands.
- Worcester MA (loop re-probe 2026-04-26): existing config maps contractor_name → Contractor_Name but newest Permit_License_Issued_Date is 2025-09-09 (~7 months stale). Does have real businesses (Window World of Boston, RHODE ISLAND BLOWN-IN CELLULOSE INSULATION INC.) mixed with individuals. V315 win has gone stale — Worcester moved past freshness gate.
- Fayetteville AR (loop re-probe 2026-04-26): data-fayetteville-ar.opendata.arcgis.com Hub DCAT has only Master Address File + City Owned Taxed Parcels. No permits dataset. Dead.
- Tulsa OK (loop V362 re-probe 2026-04-26): gis-cityoftulsa hub DCAT returns 500; gis2-cityoftulsa hub has only "Working In Neighborhoods (Nuisances)" (no FeatureServer URL exposed) and zero permit datasets. V362 dead-finding stands.
- Long Beach CA (loop re-probe 2026-04-26): no Long Beach-specific Hub DCAT path responding; loop search misdirected to Fort Worth's services5.arcgis.com tenant (CFW_*). Long Beach permits not exposed publicly. V258 dead-finding stands.
- Fort Worth TX (loop V363 Part B re-probe 2026-04-26): ALREADY WIRED at "fort_worth" → CFW_Open_Data_Development_Permits_View/FeatureServer/0 (V23). Schema has Owner_Full_Name + Full_Street_Address + File_Date + Use_Type + JobValue but NO contractor field. 5,862 records with File_Date > 2026-04-01 (very fresh). V362 no-contractor template fallback applies. No change needed.
- **Portland OR violations** (V484 probed 2026-05-01): DCAT scan of gis-pdx.opendata.arcgis.com (355 datasets) returns zero violation/enforcement/code-cases. portlandmaps.com /api/permits/ returns 404, /api/ returns 403. BDS_Layers MapServer has "Non-Compliant Signs (Data From 1999)" only — historical. PortlandMaps Advanced UI surfaces enforcement cases via website but no public JSON. Skip permanently.
- **Tampa FL violations** (V484 probed 2026-05-01): arcgis.tampagov.net/.../CodeEnforcement folder returns `{"folders":[],"services":[]}` — placeholder only. AGOL search shows "Code Enforcement Inspector Area & Parcel Lookup" web map owned by PrivateInfo_TampaGIS (non-public). Public-facing enforcement is Accela Citizen Access only. Skip permanently.
- **Tallahassee FL violations** (V484 probed 2026-05-01): geodata-tlcgis.opendata.arcgis.com DCAT has zero case datasets. AGOL items "Private Property Code Enforcement Zones" and "Environmental Services Code Enforcement Zones" are inspector area POLYGONS, not actual cases. talgov-tlcgis.opendata.arcgis.com returns 0 datasets. maps.talgov.com/arcgis/rest 404. No public Tallahassee enforcement-case feed exists. Skip permanently.
- **Charleston SC permits + violations** (V484 probed 2026-05-01): Tyler EnerGov SelfService at egcss.charleston-sc.gov returns generic error on /api/energov/Search/search. data.charleston-sc.gov, opendata.charleston-sc.gov, sc-charleston.opendata.arcgis.com all NXDOMAIN. gis.charleston-sc.gov has External/PDI_Data (police only) and Service_Energov_Composite (geocoder, not permits). No active open-data hub. Skip permanently.
- **Charleston SC owners** (V484 probed 2026-05-01): Charleston County hub has Charleston_County_Addresses but no parcel-with-owner FeatureServer. License notes "SC Code 30-2-50 prohibits private use of address data." Likely paid only. Skip permanently.
- **Des Moines IA permits + violations** (V484 probed 2026-05-01): data.dsm.city DCAT (65 datasets) has only police citations + building footprints — no permits/violations/inspections. AGOL org City_Des_Moines has 320 items, none permit/violation. opendata.dsm.city, gis.dsm.city, polkcountygis.org, gis.polkcountyiowa.gov all NXDOMAIN. No Accela tenant. Skip permanently.
- **Des Moines IA owners** (V484 probed 2026-05-01): polkcountyiowa-policrh.hub.arcgis.com and gis-polkcountyiowa.opendata.arcgis.com return 0 datasets. Vanguard HTML assessor only — no public REST. Skip permanently.
- **Providence RI permits + violations** (V484 probed 2026-05-01): data.providenceri.gov Socrata catalog has 297 datasets but Permits dataset (`ufmm-rbej`) was last updated 2020-01-24 with content ending 2018. Special Event Permits 2021. Active Business Licenses 2021. No fresh permit/violation data. gis.providenceri.gov NXDOMAIN. Skip permanently. Note: 2025 Property Tax Roll (`6ub4-iebe`) is fresh through 2026-04-13 and may have owner names — owners-only candidate if needed.
- **Louisville KY owners** (V484 probed 2026-05-01): LOJIC ArcGIS at gis.lojic.org exposes PvaGis/CamaViewer/MapServer Layer 26 (Current Parcel Polygons) but schema has NO owner-name field — only `OBJECTID, REV_DATE, PARCELID, HISTORICPIN, BLOCK, LOT, LRSN, UNIT_COUNT, ...`. LojicSolutions/OpenDataDevelopment exposes only city-owned ~5K parcels (CUR_LASTNAME). Owner names are gated behind jeffersonpva.ky.gov interactive search (no REST). Skip permanently.
- **Seattle WA owners** (V484 probed 2026-05-01): gismaps.kingcounty.gov/.../KingCo_PropertyInfo/MapServer/2 (Parcels) is queryable but `PROP_NAME` is the property name (e.g. "ZYLA COTTAGES") not owner; `PAAUNIQUENAME` is null in real rows. Owner identity is missing from the public layer. King County Assessor publishes Real Property data only via Excel/CSV annual rolls or eRealProperty interactive site. Skip permanently.
- **Birmingham AL permits** (V484 probed 2026-05-01): data.birminghamal.gov CKAN has only "building-permits-and-valuations-2017" — frozen 2017, XLSX/CSV only. City uses Accela aca-prod.accela.com/birmingham/ (no public REST). gisweb.birminghamal.gov/arcgis/rest/services/accela/ exposes only basemap layers + geocoder. No Hub item for Birmingham permits. Owners are wirable via jefferson_birmingham (V484 deferred); no permits source available. Skip permits permanently.
- **Birmingham AL violations** (V484 probed 2026-05-01): gisweb.birminghamal.gov/.../housing_inspections/FeatureServer/0 has 65,699 records with rich schema (CASE_NO, INSPECTION_DATE, PROPERTY_ADDRESS, OWNER_FIRST_NAME, OWNER_ADDRESS, plus 100+ violation-detail fields) BUT max EditDate is 2025-09-08 — 235 days stale. ComplaintInsp/FeatureServer/0+1 max InspDate 2025-07-18 (287 days stale). Skip by freshness.
- **Savannah GA violations** (V484 probed 2026-05-01): pub.sagis.org has Savannah folder with 31 layers in SAVEnergov_Data but all are basemap/zone/inspection-area polygons (zoning, parcels, neighborhoods, fire-inspection-areas, trade-inspection-areas). No code-enforcement FeatureServer. PROD_Oneview311_Only2_Public has only 6 generic layers. Skip violations permanently.

## License-Enrichment Opportunities (separate platform — needs new collector path)
<!-- Sources that ENRICH existing-city contractor profiles with license validation /
     license number / license type, but require a different ingestion path than the
     existing STATE_CONFIGS-based license_enrichment.py. -->
- Pittsburgh WPRDC — "Licensed Businesses, Contractors & Trades" (loop probed 2026-04-26):
  data.wprdc.org CKAN datastore, three resources: Business Licenses
  (e88c10d5-541d-417f-aef6-25cc5637aeb1), Licensed Contractors
  (7e195511-5219-4d16-84f7-f34a2aedf5b4), Trade Licenses
  (51470435-a36f-4385-aee2-1c4766214a9a). 5,467 contractor records, fresh through
  2026-04-23. Schema: license_number / license_type_name / business_name /
  license_state (active/expired) / initial_issue_date / most_recent_issue_date /
  expiration_date. NO phone / email / address. Value: name-matching to validate
  Pittsburgh contractor licensure + populate license_number on contractor_profiles
  (matches CA CSLB pattern). Won't add phones (PA still DDG-only). Wiring needs
  new license_enrichment.py path — existing code expects Socrata/CSV; this is CKAN
  datastore_search. City-scoped (city_slugs=['pittsburgh']) under STATE_CONFIGS['PA']
  with a new fetch fn. Defer to focused PR.

## Monitoring for New Cities

The daemon checks CITY_QUEUE.md as part of the autonomous loop. When
starting Phase 2 (GROW THE DATA), ALWAYS check this file first:
1. Process all "Ready to Wire" cities before researching new ones
2. Never investigate cities in the "Dead Ends" list
3. After investigating a city, add it to the appropriate section
