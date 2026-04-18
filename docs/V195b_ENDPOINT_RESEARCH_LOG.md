
## Chunk 2 — 15 Additional Cities Researched (V195c)

| # | City | Pop | Result | Detail |
|---|---|---|---|---|
| 1 | Jacksonville, FL | 1,010K | NO API | JAXEPICS system, no public API |
| 2 | Corpus Christi, TX | 317K | NO API | ArcGIS has 31 services but 0 permit-related |
| 3 | St. Louis, MO | 280K | NO API | Data portal HTML, no SODA endpoint |
| 4 | Boise, ID | 238K | ACCELA | Already in ACCELA_CONFIGS (agency=BOISE) |
| 5 | Huntsville, AL | 230K | NO API | Dashboard only |
| 6 | San Bernardino, CA | 225K | NO API | Accela/EZOP, no public API |
| 7 | Des Moines, IA | 213K | NO API | DSMUSA data hub, no permit dataset |
| 8 | Grand Prairie, TX | 207K | NO API | Web reports only |
| 9 | Amarillo, TX | 204K | NO API | MGO software, no API |
| 10 | Oxnard, CA | 201K | NO API | Ventura County Accela (already configured) |
| 11 | Chattanooga, TN | 191K | NO API | ChattaData portal exists, endpoint returns HTML not JSON |
| 12 | Akron, OH | 190K | NO API | Cityworks portal |
| 13 | Glendale, CA | 188K | NO API | Web portal only |
| 14 | Clarksville, TN | 186K | NO API | GovWell portal |
| 15 | Modesto, CA | 221K | NO API | eTRAKiT system |

**Tested but not viable**: Chattanooga (SODA endpoint returned HTML), Corpus Christi (ArcGIS 0 permit services), St. Louis (HTML portal page).

## Coverage Ceiling Analysis

45 cities researched across V195b + V195c. **0 new viable endpoints found.**

The remaining unconfigured cities overwhelmingly use proprietary permitting platforms:
- EnerGov (Overland Park KS, etc.)
- eTRAKiT (Coral Springs FL, Modesto CA, Westminster CO)
- Tyler/iWorQ (various)
- CityView/GovWell (Clarksville TN)
- MGO (Amarillo TX)
- Custom CSS portals (Des Moines, Cedar Rapids)

Cities that DO have public APIs (Socrata, ArcGIS FeatureServer, CKAN) are almost all already configured in CITY_REGISTRY from prior onboarding rounds (V12-V88). The research confirms the coverage ceiling is real at ~250 cities with current public API sources.
