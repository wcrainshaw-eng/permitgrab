# City Queue — Pre-Vetted Cities for Fast Onboarding

Check this file BEFORE researching any new city. "Ready to Wire" cities
have confirmed endpoints with contractor names — just add the config.
"Dead Ends" cities should never be investigated again.

## Ready to Wire (confirmed endpoint + contractor field)
<!-- Add cities here after SSH-testing confirms contractor_name field works -->
<!-- Format: - CityName ST: platform resource_id, contractor_field: fieldname, tested: date -->
- Greensboro NC: arcgis MapServer gis.greensboro-nc.gov OpenData_HRES_DS/2 BI_Permits, contractor_field: Contractor, tested: 2026-04-25 — wired in V340

## Needs Investigation (promising but unverified)
<!-- Cities with known open data portals but contractor field unconfirmed -->

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
- Tulsa OK: ArcGIS search returns only a StoryMap citing 2023-2024 permits, no queryable feature service (V339 probed 2026-04-25)
- Omaha NE: ArcGIS search returns 0 building-permit feature services for Omaha or Douglas County NE (V339 probed 2026-04-25)
- Jacksonville FL: ArcGIS portal returns only Jacksonville OREGON UGB; data.coj.net not indexed in Socrata federated catalog (V339 probed 2026-04-25)
- Reno NV: 0 ArcGIS Reno-specific results, only StoryMap reference (V340 probed 2026-04-25)
- Toledo OH: ArcGIS results are for Toledo SPAIN, no Toledo OH building permit feed (V340 probed 2026-04-25)
- Norfolk VA: 0 results in ArcGIS federated search (V340 probed 2026-04-25)
- Riverside CA: only Riverside COUNTY permits surface (PLUSActivities_PD), no city-of-Riverside building permit feed (V340 probed 2026-04-25)
- Glendale AZ: no AZ-specific result; Glendale CA has a permits-by-walkshed analytical layer but no permit-record feed (V340 probed 2026-04-25)
- Lubbock TX: only a performance-metrics dashboard, no queryable feature service (V340 probed 2026-04-25)

## Monitoring for New Cities

The daemon checks CITY_QUEUE.md as part of the autonomous loop. When
starting Phase 2 (GROW THE DATA), ALWAYS check this file first:
1. Process all "Ready to Wire" cities before researching new ones
2. Never investigate cities in the "Dead Ends" list
3. After investigating a city, add it to the appropriate section
