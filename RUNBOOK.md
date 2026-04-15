# Runbook

## Deploy went bad — roll back

1. Render dashboard → Events → find last green deploy
2. Click "Rollback" on that deploy's row
3. Verify /api/health shows old version
4. File a note about what went wrong
5. Add a test that would have caught it

## Memory is climbing

1. Check /api/diagnostics → memory_mb and threads
2. Check logs for `heartbeat` lines — plot memory over time
3. If threads growing: something is spawning and not reaping
4. If memory growing: something is accumulating. Common causes:
   - List accumulation in collector (fixed in V166)
   - requests.Session not closing
   - SQLite connection leak (check open_files in diagnostics)

## Instance failing health checks

1. Check: is /healthz responding? (should be instant, no DB)
2. If /healthz is slow, gunicorn is blocked
3. Common cause: synchronous DB writes from collector
4. V166b caps cities per cycle to 50 to prevent this

## Add a city

1. Google "<city name> open data permits"
2. Test the endpoint:
   ```
   curl "https://data.example.com/resource/abc-123.json?$limit=1&$order=issue_date DESC"
   ```
3. Add to city_sources via admin execute:
   ```sql
   INSERT INTO city_sources (source_key, name, state, platform, endpoint, date_field, status)
   VALUES ('newcity_xy', 'New City', 'XY', 'socrata',
           'https://data.example.com/resource/abc-123.json', 'issue_date', 'active');
   ```
4. Trigger collection: POST /api/admin/test-city-collection
5. Verify: /permits/new-city-xy shows data

## Known Limitations

- Collector runs in the same process as the web server. Admin API
  queries can time out during active collection cycles (~60s gaps).
  Workaround: run admin SQL during the 5-10 min gaps between cycles,
  or wait for the cycle to finish (visible in /api/diagnostics
  permits_last_collect_at). Proper fix is splitting the collector
  into a Render Background Worker (see V167 Phase 3 spec); deferred
  until admin ops become a regular pain point.

## Verified Flows

### Sign up → trial → paid (verified 2026-04-15)
- New user gets 14-day trial (User.trial_started_at set on signup)
- Day 15: trial expires (User.trial_end_date < now)
- /upgrade → Stripe Checkout → webhook flips status to 'active'
- Test card: 4242 4242 4242 4242 in test mode
- Stripe env vars: STRIPE_SECRET_KEY, STRIPE_PRICE_ID, STRIPE_WEBHOOK_SECRET

### Daily alert emails (verified 2026-04-15)
- User creates saved search via /api/saved-searches
- Worker runs send_daily_alerts() at 7 AM ET (or admin trigger)
- Matching permits from last 24h rendered to email template
- Unsubscribe via /unsubscribe/<search_id>

## One-Off Operations

### Recalc data_freshness across all cities
Run POST /api/admin/recalc-freshness with admin key, or from Render Shell:
```bash
python3 -c "
import os, psycopg2
conn = psycopg2.connect(os.environ['DATABASE_URL'])
cur = conn.cursor()
cur.execute(\"SET statement_timeout = '600000'\")
cur.execute(\"UPDATE prod_cities SET data_freshness = CASE WHEN substring(newest_permit_date,1,10)::date >= CURRENT_DATE - INTERVAL '7 days' THEN 'fresh' WHEN substring(newest_permit_date,1,10)::date >= CURRENT_DATE - INTERVAL '30 days' THEN 'aging' ELSE 'stale' END WHERE source_type IS NOT NULL AND newest_permit_date IS NOT NULL AND newest_permit_date ~ '^[0-9]{4}'\")
print(cur.rowcount)
conn.commit()
"
```

## Something broke after a push

1. Check GitHub Actions — was CI green?
2. Check Render events — did deploy succeed?
3. Check /api/diagnostics — does the service respond?
4. Roll back while investigating.
5. Add a test that would have caught the issue.
