# City Queue — Pre-Vetted Cities for Fast Onboarding

Check this file BEFORE researching any new city. "Ready to Wire" cities
have confirmed endpoints — just add the config. Cities WITHOUT contractor
data are STILL VIABLE: solar companies want owner/address data from permits.
Show "No contractor data available" on the city page when contractor_name
is absent. "Dead Ends" are cities with NO working permit API at all.

## Ready to Wire (confirmed endpoint + contractor field)
<!-- Format: - CityName ST: platform resource_id, contractor_field: fieldname, tested: date -->
- Greensboro NC: arcgis MapServer gis.greensboro-nc.gov OpenData_HRES_DS/2 BI_Permits — was already in CITY_REGISTRY as key "greensboro" but field_map mapped Contractor→contact_name (typo) and zip→Zoning (wrong). V342 fixed both. V340's duplicate "greensboro_nc" entry was reverted.
- Asheville NC: ALREADY correctly wired at "asheville_nc" → slug "asheville". Endpoint gis.ashevillenc.gov AccelaPermitsView/2 fresh through 2026-05-26 with real businesses (8MSOLAR LLC, LEDFORD ELECTRIC, AMERICAN AIR HEATING & COOLING). 64,383 records, 1,719 already collected. Uses contact_name slot but V180 fallback handles it. NC has no bulk state license DB so phones are DDG-only.
- Raleigh NC: Socrata, resource_id: building-permits, contractor_field: contractor_company_name. Researched 2026-04-25. **V361 wiring probe 2026-04-26: "raleigh" already in CITY_REGISTRY at services.arcgis.com/v400IkDOw1ad7Yad ArcGIS (180-day window) with rich contractor fields. Socrata federated search for "raleigh building permits" returned 0 results. The existing ArcGIS endpoint is the canonical one — re-confirm intent or provide an alternate Socrata host/resource id.**
- Virginia Beach VA: ArcGIS Hub, dataset_id: 15292e05..., contractor_field: contractor_name. Researched 2026-04-25. **V361 wiring probe 2026-04-26: dataset_id is truncated; can't be resolved as-is. Socrata federated search returned only Maryland Beach Buffer datasets. Need full ArcGIS Hub URL or feature service URL to wire.**
- Tulsa OK: Socrata, resource_id: okc-permits, contractor_field: primary_contractor. NOTE: was in Dead Ends (V339 only found StoryMap via ArcGIS search — Socrata dataset found via manual research). Researched 2026-04-25. **V361 wiring probe 2026-04-26: "okc-permits" prefix suggests Oklahoma City not Tulsa. Socrata federated search for "tulsa permits" returned 0 results. Likely a copy/paste error — confirm whether the dataset is actually OKC-wide or Tulsa-specific.**
- Omaha NE: CivicData, resource_id: blds-data, contractor_field: contractor_license. NOTE: was in Dead Ends (V340 found 0 ArcGIS results — CivicData platform not searched). Researched 2026-04-25. **V361 wiring probe 2026-04-26: "CivicData" platform isn't in our supported list (we handle socrata/arcgis/carto/ckan/accela). Need a concrete REST endpoint URL to wire — e.g. https://opendata-omaha.opendata.civicdata.com/resource/blds-data.json or similar.**
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
- Louisville KY: ArcGIS Hub louisville-metro-opendata-lojic.hub.arcgis.com. Permits: Active Construction Permits + Historical All Permits. Violations: Building Code Permit Enforcement Cases + Property Maintenance Inspection Violations (30-day rolling, daily updates). Contractor field unconfirmed. Top-30 city. BLITZ 2026-04-26.
- Atlanta GA: ArcGIS Hub building permits + code enforcement. Previous probe (V341) got ECONNREFUSED + TLS cert invalid — may have been fixed since. Worth a re-probe.
- New Orleans LA: Socrata open data portal, has "Code Enforcement Active Pipeline." Permit contractor field unconfirmed.
- Detroit MI: ArcGIS-based, tracks blighted properties + permits. Previous probe said "not a Socrata portal" but ArcGIS may work. Worth a re-probe.
- Pittsburgh PA: WPRDC CKAN data.wprdc.org. Permits: pli-permits (2019+). Violations: pittsburgh-pli-violations-report (PLI/DOMI/ES, 2015-present, daily). ALSO has "Licensed Businesses, Contractors & Trades" dataset — potential contractor name source! OKFN census confirms open permit data. BLITZ 2026-04-26.
- Montgomery County MD: OKFN census entry exists. Open data portal likely has permits. Worth checking.
- Norfolk VA: Socrata data.norfolk.gov. Permits: fahm-yuh4 (daily updates). Violations: mxtv-99gh (Neighborhood Quality Code Enforcement Cases) + agip-sqwc (Violations). Previous dead-end entry was ArcGIS-only probe — Socrata not checked. Contractor field unconfirmed — need SSH test. BLITZ 2026-04-26.
- Cincinnati OH: Socrata data.cincinnati-oh.gov. Permits: thvx-5mem (Building Permits Combo, 2014+, daily refresh). Violations: cncm-znd6 (Code Enforcement, daily refresh). Contractor field unconfirmed. BLITZ 2026-04-26.
- Kansas City MO: Socrata data.kcmo.org. Permits: building permits dashboard (resource ID TBD). Violations: mnjv-uy2z (Code Violations). Previous dead-end said "11 months stale" — re-check freshness, may have been refreshed since. BLITZ 2026-04-26.
- Richmond VA: Socrata data.richmondgov.com. Permits: on portal (resource ID TBD). Violations: needs investigation. Contractor field unconfirmed. BLITZ 2026-04-26.
- Chattanooga TN: Socrata chattadata.org. Permits: 764y-vxm2 (All Permit Data, BLDS format). Violations: TBD — search chattadata.org for code enforcement. Contractor field unconfirmed. BLITZ 2026-04-26.
- Syracuse NY: ArcGIS data-syr.opendata.arcgis.com. Permits: on portal. Violations: Code Violations dataset (2cc4e180fc6540fbb4fc6fafde311d7b). Contractor field unconfirmed. BLITZ 2026-04-26.
- Lexington KY: CivicData civicdata.com/organization/lexington-ky + ArcGIS data.lexingtonky.gov. Permits: CivicData building permits + AgencyCounter. Violations: TBD. Accela portal at aca-prod.accela.com/LEXKY. Contractor field unconfirmed. BLITZ 2026-04-26.
- Fort Worth TX: Socrata data.fortworthtexas.gov. Permits: qy5k-jz7m (building permits). Violations: spnu-bq4u (Code Violations, updated 3x daily!). Previously marked dead-end for "no contractor field" — re-check; even without contractor, viable as no-contractor city. BLITZ 2026-04-26.
- Milwaukee WI: CKAN data.milwaukee.gov. Permits: buildingpermits dataset (monthly CSV). Violations: Accela aca-prod.accela.com/MILWAUKEE enforcement. Previously investigated — zero profiles. Re-check with CKAN endpoint. BLITZ 2026-04-26.
- St Louis MO: Own portal stlouis-mo.gov/data. Permits: building permits via API/Web Service. Violations: not found in initial search. Contractor field unconfirmed. BLITZ 2026-04-26.
- Boise ID: ArcGIS city-of-boise.opendata.arcgis.com. Permits: New Residential Permits dataset. Violations: TBD. Contractor field unconfirmed. BLITZ 2026-04-26.
- Corpus Christi TX: ArcGIS Hub gis-corpus.opendata.arcgis.com. Permits: may have building permits under GIS datasets. Dynamic Portal for permit apps. Contractor field unconfirmed. BLITZ 2026-04-26.
- Honolulu HI: Socrata data.honolulu.gov. Permits: 3fr8-2hnx (Building Permits, 2010-2016 — STALE, need to check for newer dataset). Violations: TBD. Previous dead-end entry was wrong ("no open data portal") — they have Socrata. BLITZ 2026-04-26.
- Greensboro NC violations: Already wired for permits. Violations: Code Compliance All Violations dataset at data.greensboro-nc.gov (2011-present). Add violations config. BLITZ 2026-04-26.
- Raleigh NC violations: Already wired for permits. Violations: check data-ral.opendata.arcgis.com Public Safety section for code enforcement. BLITZ 2026-04-26.

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
- Las Vegas NV violations: ECONNREFUSED on opendata portal
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
- Hialeah FL violations: confirmed permanent dead-end after 3rd probe — no public ArcGIS or sitemap-discoverable code-enforcement feed exists. 108 phones (largest non-ad-ready phone count) but the violations leg is unsolvable from our side; needs Hialeah IT to publish a feed.
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
- Anchorage AK: Open data policy exists but no specific building permits dataset confirmed on portal (blitz 2026-04-26)
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
