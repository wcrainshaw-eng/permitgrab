---
name: health-monitor
description: Production health monitoring, daemon management, error pattern detection, and automatic remediation for PermitGrab. Use for daily health checks and incident response.
type: skill
---

# Health Monitor & Self-Healing Skill

You are monitoring PermitGrab's production health and automatically fixing issues. The system should run 24/7 without human intervention. If something breaks, fix it — don't just report it.

## ENDPOINTS

| Endpoint | Auth | Method | Purpose |
|----------|------|--------|---------|
| /api/admin/health | NO | GET | Daemon status, error counts, fresh cities |
| /api/admin/query | YES | POST | Run SELECT queries |
| /api/admin/start-collectors | YES | POST | Start/restart daemon |
| /api/admin/force-collection | YES | POST | Trigger full collection cycle |
| /api/admin/license-import | YES | POST | Trigger state license import |

**Auth header**: `X-Admin-Key: 122f635f639857bd9296150ba2e64419`
**Base URL**: `https://permitgrab.com`

## HEALTH CHECK SEQUENCE

### 1. Hit the Health Endpoint
```bash
curl -sS https://permitgrab.com/api/admin/health
```

Expected response:
```json
{
  "status": "healthy",
  "daemon_running": true,
  "errors_last_24h": <number>,
  "fresh_city_count": <number>,
  "collections_last_24h": <number>
}
```

### Automatic responses:

| Condition | Action |
|-----------|--------|
| `daemon_running: false` | Restart immediately: `POST /api/admin/start-collectors` |
| `errors_last_24h > 100` | Investigate error patterns (see step 2) |
| `fresh_city_count < 80` | Check for stale cities (see step 3) |
| `status != "healthy"` | Full diagnostic — something is fundamentally wrong |
| Health endpoint returns 5xx | Render may have restarted. Wait 2min, retry. If persistent, check Render logs |
| Health endpoint timeout | Service may be OOMing or overloaded. Check memory usage |

### 2. Error Pattern Analysis
```sql
SELECT error_message, COUNT(*) as cnt, 
  COUNT(DISTINCT source_city_key) as cities_affected,
  MAX(run_started_at) as most_recent
FROM scraper_runs
WHERE status = 'error' 
  AND run_started_at > NOW() - INTERVAL '24 hours'
GROUP BY error_message
ORDER BY cnt DESC LIMIT 15
```

#### Common error patterns and fixes:

| Error Pattern | Cause | Auto-Fix |
|---------------|-------|----------|
| `timeout` / `timed out` | Source API slow | Transient — ignore unless >20 cities |
| `404` / `Not Found` | Endpoint moved or dataset deleted | Check endpoint via SSH, deactivate city if confirmed dead |
| `500` / `Internal Server Error` | Source API broken | Transient — retry next cycle |
| `SSL` / `certificate` | SSL cert expired on source | Cannot fix — wait for source to fix |
| `rate limit` / `429` | Too many requests | Increase delay between requests in collector |
| `JSON decode error` | API returning HTML (auth wall, error page) | Test endpoint manually via SSH |
| `column .* does not exist` | Source API changed schema | Update field_map in city_configs.py |
| `OOM` / `Killed` | Memory limit exceeded | Check for memory leaks, reduce batch sizes |
| `ConnectionPool` / `pool` | DB connection exhaustion | Check for connection leaks, restart |

### 3. Stale City Check
```sql
SELECT p.source_city_key, MAX(p.date) as newest,
  CURRENT_DATE - MAX(p.date)::date as days_stale
FROM permits p
INNER JOIN prod_cities pc ON p.source_city_key = pc.city_slug
WHERE pc.status = 'active'
GROUP BY p.source_city_key
HAVING MAX(p.date) < CURRENT_DATE - INTERVAL '7 days'
ORDER BY newest ASC LIMIT 20
```

**Auto-response for stale cities:**
1. If <3 cities stale: likely transient API issues, check next cycle
2. If 3-10 cities stale: check if daemon is actually running (may have crashed)
3. If >10 cities stale: something systemic is wrong — full diagnostic needed

### 4. Collection Throughput
```sql
SELECT 
  DATE_TRUNC('hour', run_started_at) as hour,
  COUNT(*) as runs,
  SUM(permits_found) as found,
  SUM(permits_inserted) as inserted_val,
  SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) as errors
FROM scraper_runs
WHERE run_started_at > NOW() - INTERVAL '24 hours'
GROUP BY DATE_TRUNC('hour', run_started_at)
ORDER BY hour DESC LIMIT 24
```

### 5. Page Response Check
Verify key pages return 200:
```bash
for slug in chicago-il new-york-city phoenix-az san-antonio-tx miami-dade-county san-jose-ca; do
  status=$(curl -s -o /dev/null -w "%{http_code}" "https://permitgrab.com/permits/$slug")
  echo "$slug: $status"
done

# Check critical non-city pages
for page in "" pricing blog; do
  status=$(curl -s -o /dev/null -w "%{http_code}" "https://permitgrab.com/$page")
  echo "/$page: $status"
done
```

### 6. Memory & Performance (if SSH available)
```bash
RENDER_SSH="srv-d6s1tvsr85hc73em9ch0@ssh.oregon.render.com"
ssh -T $RENDER_SSH 'ps aux | head -5'
ssh -T $RENDER_SSH 'free -m'
```

## SELF-HEALING PLAYBOOKS

### Playbook: Daemon Stopped
1. Restart: `POST /api/admin/start-collectors`
2. Wait 2 minutes
3. Check health again
4. If still stopped: check for crash in recent scraper_runs errors
5. If crash found: identify the city/error, deactivate problematic city, restart daemon

### Playbook: High Error Rate (>100/24h)
1. Query error patterns (step 2 above)
2. If all errors are from 1-2 cities: likely those source APIs are down. No action needed.
3. If errors are spread across many cities: check if daemon is cycling properly
4. If errors contain "pool" or "connection": DB connection issue, may need restart

### Playbook: Memory Pressure
1. Check if an import is running (FL DBPR is the usual suspect)
2. If import is running: wait for completion, it's designed to stay under 200MB
3. If no import: check for memory leaks in long-running daemon thread
4. Nuclear option: restart the service via deploy

### Playbook: After Deploy
Every deploy requires:
1. Wait for deploy to complete (~2-3 min after push)
2. Hit health endpoint to verify service is up
3. Restart daemon: `POST /api/admin/start-collectors`
4. Wait 5 minutes
5. Check scraper_runs for new entries
6. Verify no regressions in phone counts

## INCIDENT SEVERITY

| Level | Criteria | Response Time |
|-------|----------|---------------|
| SEV1 | Site down, health returns 5xx | Immediate — restart, check Render |
| SEV2 | Daemon stopped, no collections in 2+ hours | Immediate — restart daemon |
| SEV3 | Ad-ready city data stale >48h | Within 1 hour — check source, fix collection |
| SEV4 | High error rate but site functional | Within 4 hours — analyze patterns |
| SEV5 | Non-ad-ready city issues | Next cycle — low priority |

## OUTPUT FORMAT

```
## Health Report — [DATE TIME]

### Status: [HEALTHY / DEGRADED / DOWN]

### Daemon
- Running: [yes/no]
- Collections (24h): [N]
- Errors (24h): [N]
- Fresh cities: [N]

### Issues Found
- [P0/P1/P2]: [description] — [action taken / action needed]

### Auto-Remediation Log
- [timestamp]: [action taken] — [result]

### Ad-Ready Cities
| City | Profiles | Phones | Violations | Page Status |
|------|----------|--------|------------|-------------|
| ... | ... | ... | ... | [200/404/500] |
```
