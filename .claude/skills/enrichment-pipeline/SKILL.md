---
name: enrichment-pipeline
description: Phone number enrichment for contractor profiles via state license databases, DDG web search, and bulk CSV imports. Use when improving phone coverage for ad-ready or near-miss cities.
type: skill
---

# Enrichment Pipeline Skill

You are enriching PermitGrab contractor profiles with phone numbers. Phone numbers are what makes the product sellable — without phones, a city's data is just a list of names. Target: >50 phones per city for ad-ready status.

## ENRICHMENT SOURCES (ranked by yield)

### Tier 1: State License Database Imports (HIGHEST YIELD)
These are bulk imports that match contractor profiles by business name against state licensing databases.

| State | Source | Has Phones? | Status | Cities Covered |
|-------|--------|-------------|--------|----------------|
| FL DBPR | myfloridalicense.com CSV | YES | Working (V244d) | Miami-Dade, Orlando, Tampa, Hialeah, St Pete, Cape Coral, Fort Lauderdale, Jacksonville |
| MN DLI | data.mn.gov Socrata | YES | Working | Minneapolis |
| NY DOL | data.ny.gov Socrata | YES | Working | Buffalo, NYC suburbs |
| WA L&I | data.wa.gov m8qx-ubtq | YES | Working | Seattle |
| CA CSLB | cslb.ca.gov CSV | NO (name match only) | Working | San Jose, LA, SF |
| AZ ROC | roc.az.gov/posting-list | NO (bulk) | Partial | Phoenix, Mesa |

### Tier 2: DDG Web Search (FALLBACK)
The enrichment module searches DuckDuckGo for contractor business names + city, then parses phone numbers from results using selectolax. Typical yield: 1-5% of profiles get phones.

### Tier 3: County/City Specific Sources
Some cities have local licensing databases with phone numbers. Research on a per-city basis.

## STATE LICENSE IMPORT PROCEDURE

### Trigger an import:
```
POST https://permitgrab.com/api/admin/license-import
Body: {"state": "FL"}
Header: X-Admin-Key: 122f635f639857bd9296150ba2e64419
```

### Verify import results:
```sql
-- Check phone counts before/after for state's cities
SELECT source_city_key,
  COUNT(*) as profiles,
  SUM(CASE WHEN phone IS NOT NULL AND phone != '' THEN 1 ELSE 0 END) as phones
FROM contractor_profiles
WHERE source_city_key IN ('miami-dade-county', 'orlando-fl', 'tampa-fl', 'hialeah-fl')
GROUP BY source_city_key
ORDER BY phones DESC
```

### Memory limit warning:
Render has 512MB memory limit. FL DBPR imports 3 large CSVs. The V244 fix added streaming/chunking to stay under ~200MB peak. If the import OOMs, check the streaming logic in `license_enrichment.py`.

## DDG WEB SEARCH ENRICHMENT

The enrichment module runs as part of the daemon cycle. It:
1. Picks profiles without phone numbers
2. Searches DDG for `"{business_name}" {city} {state} phone`
3. Parses results with selectolax (lightweight HTML parser)
4. Extracts phone numbers matching standard patterns
5. Updates contractor_profiles with found phones

### Check enrichment progress:
```sql
-- Enrichment coverage by city
SELECT source_city_key,
  COUNT(*) as total,
  SUM(CASE WHEN phone IS NOT NULL AND phone != '' THEN 1 ELSE 0 END) as enriched,
  ROUND(100.0 * SUM(CASE WHEN phone IS NOT NULL AND phone != '' THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0), 1) as pct
FROM contractor_profiles
GROUP BY source_city_key
HAVING COUNT(*) > 50
ORDER BY enriched DESC
```

### DDG yield factors:
- **Name quality matters**: "ABC Plumbing LLC" yields better than "JOHN SMITH" (too generic)
- **City size matters**: Bigger cities have more web presence
- **Rate limiting**: DDG blocks aggressive scraping. The module has circuit breakers (pybreaker)
- **Typical yield**: 1-5% of profiles get phones per enrichment cycle

## FINDING NEW ENRICHMENT SOURCES

### For a new state, check:
1. **Google**: `"[state] contractor license lookup" site:.gov`
2. **State licensing board website**: Look for "verify a license" or "license search"
3. **Bulk data**: Look for CSV/Excel downloads or Socrata/CKAN APIs
4. **What matters**: The download MUST have business name + phone number. Without phone, it only helps with name-matching (CA CSLB pattern).

### States already investigated (don't re-research):
- IL: No bulk download, web lookup only
- OH: OCILB has no phone in download
- PA: No bulk download
- TN: No bulk download
- NV: Henderson contractor list has no phone
- LA: Paid database only ($200+)
- AZ: ROC bulk CSV has no phone field (individual search does, but no bulk)

## ENRICHMENT PRIORITY FRAMEWORK

### Priority 1: Near-miss cities (20-49 phones)
These are closest to the 50-phone ad-ready threshold. Every phone added here matters most.

```sql
SELECT source_city_key,
  SUM(CASE WHEN phone IS NOT NULL AND phone != '' THEN 1 ELSE 0 END) as phones,
  50 - SUM(CASE WHEN phone IS NOT NULL AND phone != '' THEN 1 ELSE 0 END) as gap
FROM contractor_profiles
GROUP BY source_city_key
HAVING SUM(CASE WHEN phone IS NOT NULL AND phone != '' THEN 1 ELSE 0 END) BETWEEN 20 AND 49
ORDER BY phones DESC
```

### Priority 2: Ad-ready cities (maintain/grow)
Keep phone counts stable. Watch for regressions after imports.

### Priority 3: New state license databases
Research and add new state license sources — this is the highest-yield enrichment per effort spent.

## COMMON ISSUES

| Issue | Symptom | Fix |
|-------|---------|-----|
| FL DBPR 0 matches | `applicants_phone_indexed=2` | Column positions wrong in STATE_CONFIGS — re-download CSV, count columns, fix |
| Import OOMs | Render kills process, partial data | Reduce chunk size, add streaming in license_enrichment.py |
| DDG blocks | `enrichment_errors` spike | Reduce rate, add backoff. pybreaker circuit breaker should handle this |
| Phone format wrong | Phones stored as "1234567890" not "(123) 456-7890" | Normalize in enrichment module |
| Duplicate phones | Same phone on multiple profiles | Check dedup logic — phone should be unique per city |
| Import lock | Import takes 51+ minutes, blocks collection | V241 fixed this with lock timeout. If recurs, check lock mechanism |

## OUTPUT
After enrichment work, report:
- Phone counts before/after for affected cities
- Which enrichment source was used
- Any new sources found/tested
- Near-miss cities and their gap to 50
