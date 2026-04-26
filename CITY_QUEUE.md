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
- San Francisco CA: Socrata DataSF, has building permits SODA API. No contractor field but has address+date+permit_type.
- Washington DC: ArcGIS Hub, has current-year permits. PERMIT_APPLICANT is individual names (not businesses) — still useful for address/owner matching.
- Boston MA: Socrata (Analyze Boston), has building permits + code violations. Applicant is individual licensee names — still useful for address/owner.
- Baltimore MD: Socrata (Open Baltimore), has SODA API for housing code + building citations. No contractor field but has address data.
- Denver CO: Socrata open data portal, has construction permits with address data. No contractor field. No state licensing DB either.
- Tucson AZ: ArcGIS gis.tucsonaz.gov PDSD_ResidentialBldg MapServer/85, 31-field schema with address+project data. No contractor/applicant field.
- Lincoln NE: ArcGIS gis.lincoln.ne.gov Residential_New_Construction_Permits MS/0, 53 fields with address data. No contractor field.
- Tempe AZ: has permit API (V314 confirmed data exists). No usable contractor field.
- Madison WI: has permit API. No contractor field exposed.
- Aurora CO: has permit API. No contractor field in source.

## Needs Investigation (promising but unverified)
<!-- Cities with known open data portals but endpoint not yet SSH-tested -->
<!-- Tallahassee resolved in V343: switched endpoint from TLC_OverlayPermitsActive_D_WM/0
     (stuck commercials 2018-2021) to TLC_OverlayPermitsActiveTrends_D_WM/2
     (Single Family Last 1 Year, updated nightly). Real ContractorPhone inline.
     FL DBPR import will lift license-only contractors. -->
- Louisville KY: ArcGIS Hub, has building permits + Property Maintenance Inspections API. Contractor field unconfirmed. Top-30 city.
- Atlanta GA: ArcGIS Hub building permits + code enforcement. Previous probe (V341) got ECONNREFUSED + TLS cert invalid — may have been fixed since. Worth a re-probe.
- New Orleans LA: Socrata open data portal, has "Code Enforcement Active Pipeline." Permit contractor field unconfirmed.
- Detroit MI: ArcGIS-based, tracks blighted properties + permits. Previous probe said "not a Socrata portal" but ArcGIS may work. Worth a re-probe.
- Pittsburgh PA: WPRDC (data.wprdc.org) CKAN + ArcGIS Hub (pghgishub-pittsburghpa.opendata.arcgis.com). PLI Permits dataset back to 2019. Contractor field unconfirmed. OKFN census lists as having open permit data.
- Montgomery County MD: OKFN census entry exists. Open data portal likely has permits. Worth checking.

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
- Honolulu HI: no open data portal for building permits
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
- Kansas City MO: contractor field exists but data 11 months stale
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
- Norfolk VA: 0 results in ArcGIS federated search (V340 probed 2026-04-25)
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
- Redmond OR (V350 probed 2026-04-25): services2.arcgis.com B0h69gkZPiRSTUFu Accela_Permits/0 has 19 fields but NONE are contractor/applicant/owner/business — only TAXLOT, APP_NUMBER, STATUS, PERMIT_TYPE, ADDRESS, OPENED, DESCRIPTION, PROJECT_NAME. Dead despite the Accela_ prefix.

## Monitoring for New Cities

The daemon checks CITY_QUEUE.md as part of the autonomous loop. When
starting Phase 2 (GROW THE DATA), ALWAYS check this file first:
1. Process all "Ready to Wire" cities before researching new ones
2. Never investigate cities in the "Dead Ends" list
3. After investigating a city, add it to the appropriate section
