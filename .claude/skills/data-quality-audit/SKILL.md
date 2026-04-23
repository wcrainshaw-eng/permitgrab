---
name: data-quality-audit
description: Comprehensive data quality audit for PermitGrab. Detects garbage profiles, stale data, duplicates, phone regressions, broken pipelines, and field mapping errors. Run daily or before any deploy.
type: skill
---

# Data Quality Audit Skill

You are auditing PermitGrab's production data for quality issues. This audit must be thorough — bad data directly impacts revenue because contractors see garbage on city pages and won't pay $149/mo.

## ADMIN API
- **Query endpoint**: `POST https://permitgrab.com/api/admin/query`
- **Auth**: `X-Admin-Key: 122f635f639857bd9296150ba2e64419`
- **Body**: `{"sql": "SELECT ..."}`
- **CRITICAL**: SELECT only. No UPDATE/INSERT/DELETE. Column names can't contain "INSERT" or "CREATE" — use aliases.

## AUDIT SEQUENCE

Run ALL of these queries. Don't stop at the first issue — complete the full audit, then prioritize fixes.

### 1. GARBAGE PROFILE DETECTION

```sql
-- Numeric-only names (license numbers mapped as names)
SELECT source_city_key, business_name, COUNT(*) as cnt 
FROM contractor_profiles
WHERE business_name ~ '^[0-9]+$'
GROUP BY source_city_key, business_name
ORDER BY cnt DESC LIMIT 30

-- Single/double character names
SELECT source_city_key, COUNT(*) as garbage
FROM contractor_profiles
WHERE LENGTH(business_name) < 3
GROUP BY source_city_key
HAVING COUNT(*) > 5
ORDER BY garbage DESC

-- Common junk values
SELECT source_city_key, business_name, COUNT(*) as cnt
FROM contractor_profiles
WHERE UPPER(business_name) IN ('N/A', 'NA', 'NONE', 'TEST', 'OWNER', 'SELF', 
  'HOMEOWNER', 'UNKNOWN', 'TBD', 'PENDING', 'VARIOUS', 'SEE PLANS', 'SAME',
  'NOT APPLICABLE', 'NO CONTRACTOR', 'SELF WORK', 'OWNER BUILDER')
GROUP BY source_city_key, business_name
ORDER BY cnt DESC LIMIT 30

-- Names that are just numbers with dashes (permit/license numbers)
SELECT source_city_key, business_name, COUNT(*) as cnt
FROM contractor_profiles
WHERE business_name ~ '^[0-9]+-?[0-9]*$'
  AND LENGTH(business_name) > 3
GROUP BY source_city_key, business_name
ORDER BY cnt DESC LIMIT 20
```

**Action if found**: Check the city's field_map — contractor_name is likely mapped to the wrong source field. Fix the field_map in city_configs.py.

### 2. DUPLICATE PROFILE DETECTION

```sql
-- Exact duplicates
SELECT source_city_key, business_name, COUNT(*) as dupes
FROM contractor_profiles
GROUP BY source_city_key, business_name
HAVING COUNT(*) > 1
ORDER BY dupes DESC LIMIT 30

-- Near-duplicates (trailing whitespace, case variations)
SELECT source_city_key, 
  TRIM(UPPER(business_name)) as normalized,
  COUNT(*) as variants
FROM contractor_profiles
GROUP BY source_city_key, TRIM(UPPER(business_name))
HAVING COUNT(*) > 2
ORDER BY variants DESC LIMIT 20
```

**Action if found**: Check if the dedup logic in profile generation handles these cases. May need to add TRIM/UPPER normalization.

### 3. STALE DATA DETECTION

```sql
-- Active cities with no new permits in 7+ days
SELECT p.source_city_key, MAX(p.date) as newest_permit,
  COUNT(*) as total_permits
FROM permits p
INNER JOIN prod_cities pc ON p.source_city_key = pc.city_slug
WHERE pc.status = 'active'
GROUP BY p.source_city_key
HAVING MAX(p.date) < CURRENT_DATE - INTERVAL '7 days'
ORDER BY newest_permit ASC

-- Recently broken collections (errors in last 24h)
SELECT source_city_key, status, error_message, 
  COUNT(*) as error_count,
  MAX(run_started_at) as last_error
FROM scraper_runs
WHERE status = 'error' 
  AND run_started_at > NOW() - INTERVAL '24 hours'
GROUP BY source_city_key, status, error_message
ORDER BY error_count DESC LIMIT 20
```

**Action if found**: 
- If the source portal is still active (test via SSH), check for field_map or API changes
- If the source portal is dead, mark city as inactive and note in tracking
- If errors are transient (timeouts, 5xx), these usually self-resolve

### 4. PHONE COUNT REGRESSION CHECK

```sql
-- Current phone counts for ad-ready cities
SELECT source_city_key, 
  COUNT(*) as total_profiles,
  SUM(CASE WHEN phone IS NOT NULL AND phone != '' THEN 1 ELSE 0 END) as with_phone,
  ROUND(100.0 * SUM(CASE WHEN phone IS NOT NULL AND phone != '' THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0), 1) as phone_pct
FROM contractor_profiles
WHERE source_city_key IN (
  'chicago-il', 'new-york-city', 'phoenix-az', 'san-antonio-tx', 
  'san-jose-ca', 'miami-dade-county'
)
GROUP BY source_city_key
ORDER BY with_phone DESC
```

**Expected minimums** (update these as they grow):
| City | Min Phones | Last Known |
|------|-----------|------------|
| chicago-il | 3,000 | 3,494 |
| san-antonio-tx | 3,500 | 3,828 |
| phoenix-az | 1,000 | 1,079 |
| new-york-city | 400 | 466 |
| miami-dade-county | 150 | 188 |
| san-jose-ca | 80 | 95 |

**Action if regression**: Check if a reimport or garbage cleanup ran. If phones dropped >20%, investigate immediately.

### 5. PERMITS WITHOUT PROFILES (Broken Pipeline)

```sql
-- Cities with many permits but zero profiles
SELECT p.source_city_key, 
  COUNT(DISTINCT p.id) as permit_count,
  COUNT(DISTINCT p.contractor_name) as unique_contractors,
  COUNT(DISTINCT cp.id) as profile_count
FROM permits p
LEFT JOIN contractor_profiles cp ON p.source_city_key = cp.source_city_key
WHERE p.contractor_name IS NOT NULL 
  AND p.contractor_name != ''
  AND LENGTH(p.contractor_name) > 2
GROUP BY p.source_city_key
HAVING COUNT(DISTINCT cp.id) = 0 AND COUNT(DISTINCT p.contractor_name) > 10
ORDER BY unique_contractors DESC LIMIT 20
```

**Action if found**: Profile generation may not be running for these cities, or dedup logic is filtering everything out. Check if the city's contractor names are real business names.

### 6. AD-READY CITY HEALTH CHECK

For each city that's supposed to be ad-ready, verify ALL THREE criteria:

```sql
-- Full ad-ready audit
SELECT 
  cp.source_city_key,
  COUNT(DISTINCT cp.id) as profiles,
  SUM(CASE WHEN cp.phone IS NOT NULL AND cp.phone != '' THEN 1 ELSE 0 END) as phones,
  (SELECT COUNT(*) FROM violations v WHERE v.source_city_key = cp.source_city_key) as violation_count,
  CASE 
    WHEN COUNT(DISTINCT cp.id) >= 100 
      AND SUM(CASE WHEN cp.phone IS NOT NULL AND cp.phone != '' THEN 1 ELSE 0 END) >= 50
      AND (SELECT COUNT(*) FROM violations v WHERE v.source_city_key = cp.source_city_key) > 0
    THEN 'AD-READY'
    ELSE 'NOT READY'
  END as status_check
FROM contractor_profiles cp
WHERE cp.source_city_key IN (
  'chicago-il', 'new-york-city', 'phoenix-az', 'san-antonio-tx',
  'san-jose-ca', 'miami-dade-county'
)
GROUP BY cp.source_city_key
ORDER BY phones DESC
```

### 7. NEAR-MISS CITIES (Close to Ad-Ready)

```sql
-- Cities with 20-49 phones (close to threshold)
SELECT source_city_key,
  COUNT(*) as profiles,
  SUM(CASE WHEN phone IS NOT NULL AND phone != '' THEN 1 ELSE 0 END) as phones
FROM contractor_profiles
GROUP BY source_city_key
HAVING SUM(CASE WHEN phone IS NOT NULL AND phone != '' THEN 1 ELSE 0 END) BETWEEN 20 AND 49
ORDER BY phones DESC
```

### 8. COLLECTION HEALTH

```sql
-- Daemon collection stats
SELECT 
  COUNT(DISTINCT source_city_key) as cities_collected,
  SUM(permits_found) as total_found,
  SUM(permits_inserted) as total_inserted,
  SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) as errors,
  MIN(run_started_at) as oldest_run,
  MAX(run_started_at) as newest_run
FROM scraper_runs
WHERE run_started_at > NOW() - INTERVAL '24 hours'
```

## SEVERITY CLASSIFICATION

After running all queries, classify findings:

| Severity | Criteria | Response |
|----------|----------|----------|
| P0 | Ad-ready city lost phone/profile data, broken collection for >24h | Fix immediately in same session |
| P1 | Garbage data on customer-visible pages, phone regression >10% | Fix in next deploy |
| P2 | Stale non-ad-ready city, minor duplicates | Fix when convenient |
| P3 | Cosmetic issues, low-volume cities with sparse data | Track but don't block |

## OUTPUT FORMAT

Produce a structured report:

```
## Data Quality Audit — [DATE]

### P0 Issues (fix now)
- [none / list]

### P1 Issues (fix next deploy)
- [list]

### P2 Issues (fix when convenient)
- [list]

### Ad-Ready City Status
| City | Profiles | Phones | Violations | Status |
|------|----------|--------|------------|--------|
| ... | ... | ... | ... | ... |

### Near-Miss Cities
| City | Phones | Gap to 50 |
|------|--------|-----------|
| ... | ... | ... |

### Collection Health
- Daemon: [running/stopped]
- Cities collected (24h): [N]
- Errors (24h): [N]
- Fresh cities: [N]
```
