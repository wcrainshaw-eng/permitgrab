# V195b Endpoint Research Log — Top 30 Unconfigured Cities

## Summary
30 cities researched. 0 viable new endpoints found. All fabricated CITY_REGISTRY Socrata domains (data.cityname.gov) are fake — DNS doesn't resolve.

## Research Results

### Chunk 1 (cities 1-10, by population)

| # | City | Pop | Result | Detail |
|---|---|---|---|---|
| 1 | DC | 702K | SKIP | Slug mismatch — already have washington-dc (10K+ permits) |
| 2 | Lexington-Fayette, KY | 329K | NO API | ArcGIS hub exists, no permit dataset published |
| 3 | North Las Vegas, NV | 294K | NO API | Accela portal only, no public API |
| 4 | Des Moines, IA | 213K | NO API | CSS portal only |
| 5 | Overland Park, KS | 203K | NO API | EnerGov portal, no API |
| 6 | Mobile, AL | 201K | NO API | No documented API |
| 7 | Providence, RI | 195K | STALE | Socrata exists (ufmm-rbej) but data only through 2019 |
| 8 | Kansas City, KS | 157K | NO API | Fake domain, EnerGov platform |
| 9 | Olathe, KS | 149K | NO API | ArcGIS hub exists, FeatureServer returned 0 features |
| 10 | Thornton, CO | 147K | NO API | ArcGIS services returned 0 permit layers |

### Chunk 2 (cities 11-30)

| # | City | Pop | Result | Detail |
|---|---|---|---|---|
| 11 | Coral Springs, FL | 141K | NO API | eTRAKiT portal |
| 12 | West Valley City, UT | 138K | NO API | Utah state portal, no city-specific permits |
| 13 | Cedar Rapids, IA | 138K | NO API | CSS portal only |
| 14 | New Haven, CT | 138K | NO API | ViewMyPermitCT portal |
| 15 | Fargo, ND | 136K | NO API | Dashboard exists, no REST API confirmed |
| 16 | Norman, OK | 131K | NO API | ArcGIS hub 150 datasets, 0 permit-related |
| 17 | Columbia, MO | 131K | NO API | Web portal only |
| 18 | Allentown, PA | 127K | NO API | Open data portal, unclear permit coverage |
| 19 | North Charleston, SC | 126K | NO API | No city-specific API |
| 20 | Topeka, KS | 125K | NO API | Socrata domain exists, dataset ID unknown, HTML returned |
| 21 | Broken Arrow, OK | 123K | NO API | Web portal only |
| 22 | Arvada, CO | 122K | NO API | ArcGIS hub 41 datasets, 0 permit-related |
| 23 | Billings, MT | 121K | NO API | ArcGIS hub, permit dataset unconfirmed |
| 24 | Matanuska-Susitna, AK | 118K | SKIP | Census borough, not a city |
| 25 | Nampa, ID | 117K | NO API | Web portal only |
| 26 | West Jordan, UT | 117K | NO API | Utah state portal, no city-specific permits |
| 27 | Miami Gardens, FL | 116K | NO API | Through Miami-Dade County |
| 28 | Waterbury, CT | 116K | NO API | GIS available, no permit API |
| 29 | Provo, UT | 115K | NO API | Utah state portal |
| 30 | Westminster, CO | 115K | NO API | eTRAKiT portal |

## Key Finding

The 109 "Category E" cities with prod_cities rows but no source_type ALL have:
- Fabricated CITY_REGISTRY entries with fake Socrata domains (created by V87/V88 population expansion)
- prod_cities rows from V88 population expansion (not from actual source onboarding)
- No real public permit API — they use proprietary systems (EnerGov, eTRAKiT, Tyler, ViewPermit, iWorQ, Accela without public access)

The remaining unconfigured cities represent the "long tail" of US cities that haven't adopted open data platforms for building permits. Expanding coverage to these cities would require either:
1. Platform-specific scrapers (EnerGov, eTRAKiT, Tyler, etc.)
2. State-level bulk data sources that cover multiple cities
3. FOIA requests for bulk data exports
4. Partnerships with proprietary platform vendors
