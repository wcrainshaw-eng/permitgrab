
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

## V196 — SSH-Verified Re-Test Results

**Key finding: local curl was NOT proxy-blocked.** SSH testing via Render produced the SAME results as local testing. The fabricated endpoints really are fake.

### SSH verification (tested from Render server):

| Endpoint | SSH Result | Verdict |
|---|---|---|
| **Providence RI** (data.providenceri.gov/resource/ufmm-rbej) | ✅ S=200, JSON, 2 records | WORKS but data from 2019 (stale) |
| Norman OK ArcGIS hub | ✅ S=200, 150 datasets | Hub alive, 0 permit datasets |
| Topeka KS (data.topeka.org) | S=200, HTML | City website, NOT Socrata — fabricated resource ID |
| Lexington KY (data.lexingtonky.gov) | S=200, HTML | City website, NOT Socrata — fabricated resource ID. Real path: ACCELA (LEXKY, already configured) |
| Fargo ND (data.fargond.gov) | S=200, HTML | City website, NOT Socrata — fabricated resource ID |
| Des Moines (data.dsm.city) | S=200, HTML | City website, NOT Socrata — fabricated resource ID |
| St. Louis (data.stlouis-mo.gov) | S=200, HTML | Custom CMS, not Socrata |
| Chattanooga (chattadata.org) | S=200, HTML | Pantheon "No Site Detected" — portal DOWN |
| Allentown PA (data.allentownpa.gov) | DNS FAIL | Domain doesn't exist — fabricated |
| Billings MT (data.billingsmt.gov) | DNS FAIL | Domain doesn't exist — fabricated |
| **Chicago (control test)** | ✅ S=200, JSON, 2 records | Known-working endpoint confirmed SSH testing works |

### Conclusion

The V87/V88 population expansion generated CITY_REGISTRY entries with fabricated Socrata URLs. The fabrication pattern: `data.{cityname}.gov/resource/{random-4chars}.json`. Most city domains (data.topeka.org, data.fargond.gov, etc.) are real city websites but DON'T have Socrata SODA APIs. The resource IDs are random strings that don't correspond to real datasets.

**Coverage ceiling confirmed at ~250 cities with genuine public APIs.**
