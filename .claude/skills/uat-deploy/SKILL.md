---
name: uat-deploy
description: Pre-deploy validation and post-deploy verification for PermitGrab. Run before every merge and after every deploy. Catches regressions before they reach production.
type: skill
---

# UAT & Deploy Skill

You are running PermitGrab's quality gate. NO code reaches production without passing this checklist. If any P0 check fails, block the deploy and fix it first.

## PRE-DEPLOY CHECKS (run before merging any PR)

### 1. Code Quality
- [ ] No syntax errors in changed files
- [ ] No hardcoded credentials (except the admin key which is already in CLAUDE.md)
- [ ] No `print()` statements left from debugging (use `logger` instead)
- [ ] No TODO/FIXME comments added without a ticket reference
- [ ] Changes are backward-compatible with existing data

### 2. Field Map Validation (if city_configs.py changed)
For every new or modified city config:
```python
# Verify these fields exist:
assert 'source_type' in config
assert 'field_map' in config
assert 'contractor_name' in config['field_map']  # MANDATORY
assert 'date' in config['field_map']  # MANDATORY
assert 'address' in config['field_map']  # MANDATORY

# If ArcGIS MapServer:
if config.get('source_type') == 'arcgis_mapserver':
    assert config.get('return_geometry') == False  # MANDATORY
```

### 3. Endpoint Liveness (if adding new cities)
Test each new endpoint via SSH:
```bash
RENDER_SSH="srv-d6s1tvsr85hc73em9ch0@ssh.oregon.render.com"
ssh -T $RENDER_SSH 'curl -s "ENDPOINT_URL" | head -c 500'
```
Verify: returns valid JSON, has recent dates, has contractor names.

### 4. Import Safety (if license_enrichment.py changed)
- Memory usage must stay under ~200MB peak (Render limit is 512MB)
- Imports must not block collection for >10 minutes
- CSV column positions must match actual file layout

## POST-DEPLOY CHECKS (run after every deploy to main)

### 1. Service Health (P0 — blocks everything)
```bash
# Must return 200 with daemon_running:true
curl -sS https://permitgrab.com/api/admin/health
```
If this fails: service didn't start properly. Check Render logs.

### 2. Restart Daemon (REQUIRED after every deploy)
```bash
curl -sS -X POST https://permitgrab.com/api/admin/start-collectors \
  -H "X-Admin-Key: 122f635f639857bd9296150ba2e64419"
```
Wait 5 minutes, then re-check health.

### 3. City Pages Load (P0)
```bash
for slug in chicago-il new-york-city phoenix-az san-antonio-tx miami-dade-county san-jose-ca; do
  status=$(curl -s -o /dev/null -w "%{http_code}" "https://permitgrab.com/permits/$slug")
  echo "$slug: $status"
done
```
ALL must return 200. Any 404/500 = regression = rollback.

### 4. Phone Count Regression (P1)
```sql
SELECT source_city_key,
  COUNT(*) as profiles,
  SUM(CASE WHEN phone IS NOT NULL AND phone != '' THEN 1 ELSE 0 END) as phones
FROM contractor_profiles
WHERE source_city_key IN ('chicago-il','new-york-city','phoenix-az','san-antonio-tx','san-jose-ca','miami-dade-county')
GROUP BY source_city_key
ORDER BY phones DESC
```

Compare against known minimums:
| City | Min Phones |
|------|-----------|
| chicago-il | 3,000 |
| san-antonio-tx | 3,500 |
| phoenix-az | 1,000 |
| new-york-city | 400 |
| miami-dade-county | 150 |
| san-jose-ca | 80 |

If any city dropped >20% from its minimum: P0 regression. Investigate immediately.

### 5. Data Freshness (P1)
```sql
SELECT source_city_key, MAX(date) as newest
FROM permits
WHERE source_city_key IN ('chicago-il','new-york-city','phoenix-az','san-antonio-tx','miami-dade-county','san-jose-ca')
GROUP BY source_city_key
```
No ad-ready city should have permits older than 7 days.

### 6. Garbage Data Check (P1)
```sql
SELECT source_city_key, COUNT(*) as garbage
FROM contractor_profiles
WHERE business_name ~ '^[0-9]+$' OR LENGTH(business_name) < 3
GROUP BY source_city_key
HAVING COUNT(*) > 10
ORDER BY garbage DESC
```
If new garbage appeared after deploy: field_map regression.

### 7. Collection Running (P2)
```sql
SELECT COUNT(*) as runs, 
  SUM(permits_inserted) as inserted_count,
  MAX(run_started_at) as latest
FROM scraper_runs
WHERE run_started_at > NOW() - INTERVAL '10 minutes'
```
Should have >0 runs after daemon restart.

### 8. Payment Flow (P2)
```bash
# Pricing page loads
curl -s -o /dev/null -w "%{http_code}" https://permitgrab.com/pricing
```

### 9. Homepage & Blog (P3)
```bash
curl -s -o /dev/null -w "%{http_code}" https://permitgrab.com/
curl -s -o /dev/null -w "%{http_code}" https://permitgrab.com/blog
```

## SEVERITY AND RESPONSE

| Result | Action |
|--------|--------|
| All P0 pass | Deploy is GOOD. Continue. |
| P0 fails | ROLLBACK immediately. `git revert HEAD && git push` |
| P1 fails | Investigate. If regression from THIS deploy, fix in hotfix PR. |
| P2 fails | Note and fix in next cycle. |
| P3 fails | Track for later. |

## OUTPUT FORMAT

```
## UAT Report — [DATE TIME]
### Deploy: [commit hash / PR number]

### Result: [PASS / FAIL]

### P0 Checks
- [ ] Health endpoint: [PASS/FAIL]
- [ ] Daemon restart: [PASS/FAIL]  
- [ ] City pages: [PASS/FAIL] — [details]

### P1 Checks
- [ ] Phone counts: [PASS/FAIL] — [details]
- [ ] Data freshness: [PASS/FAIL] — [details]
- [ ] Garbage data: [PASS/FAIL] — [details]

### P2 Checks
- [ ] Collection running: [PASS/FAIL]
- [ ] Pricing page: [PASS/FAIL]

### P3 Checks
- [ ] Homepage: [PASS/FAIL]
- [ ] Blog: [PASS/FAIL]

### Issues Found
[list any failures and remediation taken]
```
