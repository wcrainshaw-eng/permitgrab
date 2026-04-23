---
name: city-onboarding
description: Autonomous pipeline for researching, testing, configuring, and verifying new city permit data sources. Use when adding ANY new city to PermitGrab.
type: skill
---

# City Onboarding Skill

You are onboarding a new city to PermitGrab's permit collection system. Follow this pipeline EXACTLY. Do NOT skip steps. Do NOT proceed if a step fails — log the failure and move to the next city candidate.

## HARD RULES
- ONE city at a time. No parallel discovery. No bulk operations.
- No auto-discovery APIs (no Socrata Discovery API, no ArcGIS Hub search, no state-level datasets)
- Test via SSH ONLY: `ssh -T srv-d6s1tvsr85hc73em9ch0@ssh.oregon.render.com 'curl -s ...'`
- Local curl is BLOCKED by sandbox proxy. Never trust local curl results.
- Check freshness (newest record date), NOT record count. 300K stale records = worthless.
- MapServer queries MUST include `returnGeometry=false`

## STEP 1: Pick the Next City

Query the database for the next candidate:
```sql
SELECT city, state, population 
FROM us_cities 
WHERE population > 100000 
AND city_slug NOT IN (SELECT city_slug FROM prod_cities WHERE status='active')
ORDER BY population DESC 
LIMIT 10
```
Pick the largest city not yet onboarded. Skip cities known to be dead ends (check CLAUDE.md "Known dead ends" sections).

## STEP 2: Research the Data Source

Search Google for: `"[city name] [state]" open data portal building permits`

Look for these platform patterns:
| Platform | URL Pattern | Priority |
|----------|-------------|----------|
| Socrata | `data.[city].gov/resource/[id].json` | HIGH — best supported |
| ArcGIS FeatureServer | `*/arcgis/rest/services/*/FeatureServer/*` | MEDIUM |
| ArcGIS MapServer | `*/arcgis/rest/services/*/MapServer/*` | MEDIUM (needs returnGeometry=false) |
| CKAN | `data.[city].gov/api/3/action/datastore_search` | MEDIUM |
| Accela | `aca.[city].gov` or `citizenaccess.com` | LOW — HTML scraping only |

### What to look for in the dataset:
1. **Building permits** (not business licenses, not zoning applications)
2. **A contractor/applicant name field** with real business names (not license #s, not "N/A")
3. **A date field** for freshness queries
4. **Address field** for location

## STEP 3: Test the Endpoint via SSH

### Socrata:
```bash
RENDER_SSH="srv-d6s1tvsr85hc73em9ch0@ssh.oregon.render.com"
ssh -T $RENDER_SSH 'curl -s "https://data.DOMAIN.gov/resource/DATASET_ID.json?\$limit=3&\$order=date_field+DESC"'
```

### ArcGIS FeatureServer:
```bash
ssh -T $RENDER_SSH 'curl -s "https://DOMAIN/arcgis/rest/services/.../FeatureServer/0/query?where=1%3D1&outFields=*&resultRecordCount=3&orderByFields=date_field+DESC&f=json"'
```

### ArcGIS MapServer (MUST include returnGeometry=false):
```bash
ssh -T $RENDER_SSH 'curl -s "https://DOMAIN/arcgis/rest/services/.../MapServer/0/query?where=1%3D1&outFields=*&returnGeometry=false&resultRecordCount=3&orderByFields=date_field+DESC&f=json"'
```

### CKAN:
```bash
ssh -T $RENDER_SSH 'curl -s "https://data.DOMAIN.gov/api/3/action/datastore_search?resource_id=RESOURCE_ID&limit=3&sort=date_field+desc"'
```

## STEP 4: Validate the Response

Check these three things IN ORDER:

### 4a. FRESHNESS (most important)
Find the newest record date. If the newest record is:
- **This week** → PROCEED
- **This month** → Acceptable, PROCEED with caution
- **>30 days old** → STOP. Endpoint is dead/stale. Skip this city.

### 4b. CONTRACTOR NAMES
The response MUST have a field containing real business names. Check the actual values:
- Real names: "ABC Plumbing LLC", "Smith Electric Co" → GOOD
- License numbers: "C-12345", "HIC.123456" → BAD (no profiles will be created)
- Empty/null/generic: "", null, "N/A", "OWNER", "SELF" → BAD
- If >50% of records have no contractor name → skip unless the field just needs a different mapping

### 4c. RECORD VOLUME
- At least 100 records in the last 6 months → Good
- <100 records → City is too small or data is too sparse. Consider skipping.

## STEP 5: Check the Slug

ALWAYS query the DB for the exact slug before adding config:
```sql
SELECT city_slug, source_id FROM prod_cities WHERE city_slug LIKE '%cityname%'
```

If the city already exists with a different slug or inactive status, reactivate rather than creating duplicate.

## STEP 6: Build the Field Map

Map the API's field names to PermitGrab's standard fields:

| Standard Field | Description | Required? |
|---------------|-------------|-----------|
| contractor_name | Business name of contractor/applicant | YES — city is useless without this |
| date | Permit date (issued, filed, or applied) | YES — needed for freshness |
| address | Property address | YES |
| permit_type | Type/description of work | Recommended |
| permit_number | Unique permit ID | Recommended |
| status | Permit status | Optional |
| description | Work description | Optional |

### Common gotchas:
- Some APIs split address into street/city/zip — concatenate in field_map
- Date fields may be `permit_issued_date`, `applied_date`, `filed_date`, etc. — pick the most recent/active one
- Contractor may be under `applicant_name`, `contractor_business_name`, `company_name`, etc.
- ArcGIS often uses ALL_CAPS field names

## STEP 7: Add to CITY_REGISTRY

Add the configuration to `city_configs.py` CITY_REGISTRY dict:

```python
'city-slug': {
    'source_type': 'socrata',  # or 'arcgis_feature', 'arcgis_mapserver', 'ckan'
    'source_id': 'city_name',  # underscore format
    'domain': 'data.domain.gov',
    'resource_id': 'xxxx-xxxx',
    'field_map': {
        'contractor_name': 'api_contractor_field',
        'date': 'api_date_field',
        'address': 'api_address_field',
        'permit_type': 'api_type_field',
        'permit_number': 'api_number_field',
    },
    'date_field': 'api_date_field',  # for freshness queries
    'city': 'City Name',
    'state': 'ST',
},
```

For ArcGIS MapServer, add: `'return_geometry': False`

## STEP 8: Deploy and Verify

1. Commit and push to main (triggers auto-deploy on Render)
2. Wait for deploy to complete (~2-3 minutes)
3. Restart daemon: `POST /api/admin/start-collectors`
4. Wait 5 minutes for first collection
5. Verify with DB query:

```sql
SELECT source_city_key, COUNT(*) as permits, 
  COUNT(contractor_name) as with_name, 
  MAX(date) as newest
FROM permits WHERE source_city_key = 'new-city-slug'
GROUP BY source_city_key
```

### Success criteria:
- permits > 0
- with_name > 0 (if 0, field_map is wrong — go back to step 6)
- newest is from this week

## STEP 9: Backfill Historical Data

Trigger a backfill to get 6 months of history:
```
POST /api/admin/test-and-backfill
Body: {"city_slug": "new-city-slug"}
```

Re-verify counts after backfill completes.

## STEP 10: Check Profile Generation

After the daemon's next cycle, verify profiles were created:
```sql
SELECT source_city_key, COUNT(*) as profiles,
  SUM(CASE WHEN phone IS NOT NULL AND phone != '' THEN 1 ELSE 0 END) as phones
FROM contractor_profiles
WHERE source_city_key = 'new-city-slug'
GROUP BY source_city_key
```

If profiles = 0 but permits > 0 with contractor names, there's a dedup/profile generation issue. Check the profile generation logic.

## FAILURE MODES & RECOVERY

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| 0 permits collected | Wrong domain/resource_id, endpoint down | Re-test endpoint via SSH |
| Permits but 0 with contractor name | field_map points to wrong field | Check raw API response, fix field_map |
| Permits but 0 profiles | contractor_name is junk (numbers, "N/A") | Skip city or find better field |
| Stale data (newest >7d old) | Endpoint stopped updating | Check source portal manually |
| 400/500 errors in scraper_runs | Bad query params, auth required | Check error_message in scraper_runs |
| MapServer returns huge GeoJSON | Missing returnGeometry=false | Add to config |

## OUTPUT
After completing onboarding, update the relevant tracking and log the result:
- City name, slug, source type, platform
- Permit count, contractor name coverage %, newest date
- Whether violations are available (search for code enforcement data on same portal)
- Any issues encountered
