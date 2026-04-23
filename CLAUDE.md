# CLAUDE.md — PermitGrab Autonomous Agent Instructions

## NORTH STAR

PermitGrab sells contractor lead lists to home service companies. A contractor pulls a permit → we capture their name and phone → we sell that lead for $149/mo. The product is a web app at permitgrab.com deployed on Render.

**The goal: 10+ ad-ready cities for Google Ads launch.**

A city is "ad-ready" when it has ALL THREE:
1. **Profiles** (>100 contractor_profiles with real business names)
2. **Phones** (>50 profiles with phone numbers)
3. **Violations** (>0 code enforcement violation records)

**Current state (2026-04-22):**
- Confirmed YES: Chicago (3,494 phones), Phoenix (1,079), San Antonio (3,828), San Jose (95 phones, 3,428 violations)
- NYC: 466 phones (was 4,362 — check if data issue)
- 6+ FL cities BLOCKED on FL DBPR import (column position bug, V244d fix pending)
- ~98 cities actively collecting permits

**You are autonomous. Don't stop to ask permission. Fix things, test things, deploy things. If something breaks, debug and fix it. Write clean PRs with descriptive titles.**

---

## ARCHITECTURE

### Data Flow
```
External APIs (Socrata/ArcGIS/Accela/CKAN)
    → collector daemon (runs every ~30min)
    → permits table (raw permit records)
    → contractor_profiles (deduplicated by business name per city)
    → enrichment (DDG web search adds phone numbers)
    → product pages at permitgrab.com/permits/{city-slug}
```

### Key Tables
- **prod_cities**: What the daemon collects. `status='active'` means collecting. Has source_id, source_type.
- **permits**: Raw permit data. Key columns: source_city_key, contractor_name, date, address, permit_type
- **contractor_profiles**: Deduplicated contractors. Key columns: source_city_key, business_name, phone, trade_category
- **violations**: Code enforcement violations. Key columns: source_city_key, case_number, date, address, description
- **scraper_runs**: Collection logs. status, error_message, permits_found, permits_inserted, run_started_at

### Slug Format
- prod_cities.city_slug and permits.source_city_key use hyphen format: `miami-dade-county`, `orlando-fl`, `buffalo-ny`
- prod_cities.source_id uses underscore format: `miami_dade_county`, `orlando_fl`
- ALWAYS query the DB for exact slug before assuming: `SELECT city_slug FROM prod_cities WHERE city_slug LIKE '%miami%'`

### Config Chain
The daemon checks in order: prod_cities → city_sources → CITY_REGISTRY dict in city_configs.py. For new cities, add to CITY_REGISTRY in city_configs.py.

### Daemon
- Does NOT auto-start on deploy. Must call: `POST /api/admin/start-collectors`
- Runs as background thread in Flask process
- Pauses during imports via IMPORT_IN_PROGRESS flag
- Collects all active cities every ~30min

---

## ADMIN API

**Base URL:** https://permitgrab.com
**Auth Header:** `X-Admin-Key: 122f635f639857bd9296150ba2e64419`

### Endpoints
| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | /api/admin/health | NO | Daemon status, error counts, fresh city count |
| POST | /api/admin/query | YES | Run SELECT queries. Body: `{"sql":"..."}` |
| POST | /api/admin/force-collection | YES | Trigger full collection cycle |
| POST | /api/admin/test-and-backfill | YES | Test a city endpoint + backfill data |
| POST | /api/admin/license-import | YES | Trigger state license import. Body: `{"state":"FL"}` |
| POST | /api/admin/start-collectors | YES | Start the daemon thread |

**CRITICAL:** The query endpoint is SELECT ONLY. It rejects UPDATE/INSERT/DELETE. It also blocks column names containing "INSERT" or "CREATE" — use aliases.

### Testing Endpoints from Code
**SSH into Render** for all external endpoint testing. Code's sandbox proxy blocks outbound HTTPS:
```bash
RENDER_SSH="srv-d6s1tvsr85hc73em9ch0@ssh.oregon.render.com"
ssh -T $RENDER_SSH 'curl -s "https://example.com/resource/id.json?\$limit=3"'
```
Never trust `curl` results from the local Code sandbox. They go through a proxy that returns 403 for everything.

---

## CITY ONBOARDING PIPELINE

**NEVER use auto-discovery. NEVER use catalog APIs. NEVER use state-level datasets. One city at a time, manual research.**

### Step 1: Research
Google "[city name] open data portal building permits" and find the API:
- **Socrata**: Look for `data.[city].gov/resource/[id].json` URLs
- **ArcGIS**: Look for `*/arcgis/rest/services/*/FeatureServer/*` or `MapServer/*` URLs
- **CKAN**: Look for `data.[city].gov/api/3/action/datastore_search` URLs
- **Accela**: Accela ACA portals scrape HTML — only add if the grid has contractor columns

### Step 2: Test the Endpoint (via SSH)
```bash
# Socrata
ssh -T $RENDER_SSH 'curl -s "https://data.example.gov/resource/XXXX.json?\$limit=3&\$order=date_field+DESC"'

# ArcGIS FeatureServer
ssh -T $RENDER_SSH 'curl -s "https://example.com/arcgis/rest/services/.../FeatureServer/0/query?where=1%3D1&outFields=*&resultRecordCount=3&orderByFields=date_field+DESC&f=json"'

# ArcGIS MapServer (MUST include returnGeometry=false)
ssh -T $RENDER_SSH 'curl -s "https://example.com/arcgis/rest/services/.../MapServer/0/query?where=1%3D1&outFields=*&returnGeometry=false&resultRecordCount=3&orderByFields=date_field+DESC&f=json"'
```

### Step 3: CHECK FRESHNESS (CRITICAL)
**Record count means NOTHING. Check the newest record date.**
- If newest record is >30 days old → SKIP, endpoint is dead
- If newest record is from this week → PROCEED
- 300K records from 2015 = worthless. 50 records from yesterday = gold.

### Step 4: Check for Contractor Names
The response MUST have a field containing real business names (not license numbers, not "N/A", not blank). Without contractor names → no profiles → city is useless.

### Step 5: Add Configuration
Add to CITY_REGISTRY in city_configs.py with:
- Correct slug (query DB first)
- source_type matching the platform
- field_map mapping source fields to standard fields (contractor_name, date, address, permit_type, etc.)
- date_field for freshness queries

### Step 6: Backfill + Verify
After deploy, trigger collection and verify:
```sql
SELECT source_city_key, COUNT(*) as permits, COUNT(contractor_name) as with_name, MAX(date) as newest
FROM permits WHERE source_city_key = 'new-city-slug'
GROUP BY source_city_key
```
If permits > 0 AND with_name > 0 AND newest is recent → success.

---

## DATA QUALITY RULES

### What to Check For (run these regularly)
```sql
-- Garbage profiles (numeric-only names, single characters, common junk)
SELECT source_city_key, business_name, COUNT(*) FROM contractor_profiles
WHERE business_name ~ '^[0-9]+$'
   OR LENGTH(business_name) < 3
   OR business_name IN ('N/A', 'NA', 'NONE', 'TEST', 'OWNER', 'SELF')
GROUP BY source_city_key, business_name
ORDER BY COUNT(*) DESC LIMIT 20

-- Duplicate profiles
SELECT source_city_key, business_name, COUNT(*) as dupes
FROM contractor_profiles
GROUP BY source_city_key, business_name
HAVING COUNT(*) > 1
ORDER BY dupes DESC LIMIT 20

-- Cities with permits but no profiles (broken contractor_name extraction)
SELECT p.source_city_key, COUNT(*) as permits, COUNT(p.contractor_name) as with_name
FROM permits p
LEFT JOIN contractor_profiles cp ON p.source_city_key = cp.source_city_key
WHERE cp.source_city_key IS NULL
GROUP BY p.source_city_key
HAVING COUNT(*) > 100
ORDER BY permits DESC

-- Stale cities (active but no new data in 7+ days)
SELECT p.source_city_key, MAX(p.date) as newest
FROM permits p
INNER JOIN prod_cities pc ON p.source_city_key = pc.city_slug
WHERE pc.status = 'active'
GROUP BY p.source_city_key
HAVING MAX(p.date) < date('now', '-7 days')
ORDER BY newest ASC
```

### What Breaks (Common Failure Modes)
1. **field_map wrong**: contractor_name maps to wrong source field → permits collected with NULL contractor → 0 profiles. Fix: check raw API response, find the right field, update field_map.
2. **Column position mismatch**: CSV imports (FL DBPR) use positional columns. If CSV layout changes → wrong data in every field. Fix: download CSV, check actual headers, align column lists.
3. **Slug mismatch**: Using `buffalo` instead of `buffalo-ny` → data goes nowhere. Fix: always query DB for exact slug.
4. **Date field wrong**: Freshness queries return nothing → city appears stale. Fix: check which field the API actually uses for dates.
5. **MapServer without returnGeometry=false**: Returns 400 errors or huge GeoJSON responses. Fix: add returnGeometry=false to all MapServer queries.

---

## PHONE ENRICHMENT

### State License Imports (highest-yield source)
Only 4 states have phone numbers in bulk downloads:
| State | Source | Format | Status |
|-------|--------|--------|--------|
| FL DBPR | myfloridalicense.com CSV (3 files: applicants, certified, registered) | Positional CSV | BROKEN — column positions misaligned. V244d fix pending. |
| MN DLI | Socrata at data.mn.gov | SODA API | Working. +154 phones for Minneapolis |
| NY DOL | Socrata at data.ny.gov | SODA API | Working. +9 phones for Buffalo (51min lock bug fixed) |
| WA L&I | Socrata m8qx-ubtq at data.wa.gov | SODA API | Working. +4 phones for Seattle |
| CA CSLB | CSV download from cslb.ca.gov | CSV (no phone field) | Matches by name only. +95 for San Jose |
| AZ ROC | CSV at roc.az.gov/posting-list | CSV (no phone field) | 57K records. No phone in bulk. Individual search has phones but no bulk export |

**States with NO usable bulk phone data:** IL, OH, PA, TN, NV, LA (paid only)

### DDG Web Search Enrichment
The enrichment module uses DuckDuckGo + selectolax to search for contractor phone numbers. Typical yield: 1-5% of profiles get phones. This is the fallback when state license DBs don't have phones.

### The #1 Enrichment Blocker
FL DBPR covers 6+ cities (Miami-Dade, Orlando, Tampa, Hialeah, St. Petersburg, Cape Coral, Fort Lauderdale, Jacksonville). Getting this single import working adds hundreds of phones to each city. This is the highest-leverage fix in the entire system.

---

## VIOLATION SOURCES

### Active (configured and collecting)
| City | Type | Endpoint/Resource |
|------|------|-------------------|
| NYC HPD | Socrata | wvxf-dwi5 at data.cityofnewyork.us |
| NYC DOB | Socrata | 3h2n-5cm9 at data.cityofnewyork.us |
| Chicago | Socrata | 22u3-xenr at data.cityofchicago.org |
| Austin | Socrata | 6wtj-zbtb at data.austintexas.gov |
| LA (open) | Socrata | u82d-eh7z at data.lacity.org |
| LA (closed) | Socrata | rken-a55j at data.lacity.org |
| Orlando | Socrata | k6e8-nw6w at data.cityoforlando.net |
| St. Pete | Socrata | tmdq-gg7f at stat.stpete.org |
| Mesa | Socrata | hgf6-yenu at data.mesaaz.gov |
| Miami-Dade | ArcGIS | services.arcgis.com/8Pc9XBTAsYuxx9Ny/.../CCVIOL_gdb/FeatureServer/0 |
| Phoenix | ArcGIS MapServer | maps.phoenix.gov/pub/rest/services/Public/NSD_Property_Maintenance/MapServer/0 |
| Cape Coral | ArcGIS MapServer | capeims.capecoral.gov/.../OpenData/MapServer/5 |
| Cleveland | ArcGIS FeatureServer | services3.arcgis.com/dty2kHktVXHrqO8i/... |
| Fort Lauderdale | ArcGIS MapServer | gis.fortlauderdale.gov/arcgis/rest/services/... |

### Known dead ends for violations
San Antonio, San Diego, Dallas, Houston, Henderson NV, Minneapolis (Tableau only), Denver

---

## SEO — REAL DATA, NOT GENERIC

### Current State (Critical)
- Only 2 out of 3,115 pages indexed by Google
- Zero organic traffic for any target keyword
- 67 blog posts exist, only 1 indexed
- Technical issues: canonical mismatches, missing H1 tags on some pages

### What Good SEO Looks Like for PermitGrab
Every city page MUST have content that is:
1. **Specific to that city** — real permit counts, real contractor counts, real trade breakdowns
2. **Useful** — not generic "building permits are important" filler
3. **Data-driven** — pull actual numbers from the DB

### How to Generate Real City Content
For each ad-ready city page, query the DB and include real stats:
```sql
-- Real permit stats for a city
SELECT permit_type, COUNT(*) as cnt FROM permits
WHERE source_city_key = '{slug}' AND date > date('now', '-90 days')
GROUP BY permit_type ORDER BY cnt DESC LIMIT 10

-- Top contractors in the city
SELECT business_name, COUNT(*) as permits FROM permits
WHERE source_city_key = '{slug}' AND contractor_name IS NOT NULL
AND date > date('now', '-180 days')
GROUP BY business_name ORDER BY permits DESC LIMIT 20

-- Trade breakdown
SELECT trade_category, COUNT(*) as cnt FROM contractor_profiles
WHERE source_city_key = '{slug}'
GROUP BY trade_category ORDER BY cnt DESC LIMIT 10
```

Use these real numbers in page content: "Chicago had 2,847 building permits filed in the last 90 days across 8,430 active contractors. The most active trades are electrical (23%), plumbing (18%), and general construction (15%)."

### Technical SEO Checklist
- Every page needs H1 with city name + "Building Permits"
- Canonical URL must match the actual URL exactly (no /chicago vs /chicago-il mismatch)
- Meta description must include city name + real data point
- Structured data (LocalBusiness schema) for each city page
- Sitemap must be submitted to Google Search Console
- Blog posts should target "[city] building permits 2026" long-tail keywords
- Internal linking between city pages and related blog posts

### What Code CAN Do for SEO
- Fix technical issues (canonicals, H1s, meta tags, structured data)
- Generate data-driven content using real DB numbers
- Verify pages render correctly (check for client-side JS rendering issues that block Googlebot)
- Submit sitemap via the sitemap.xml endpoint

### What Code CANNOT Do for SEO
- Access Google Search Console (no programmatic API available from Code)
- Build backlinks
- The scheduled Cowork task checks GSC via Chrome — that covers monitoring

---

## UAT TESTING PROCEDURES

### Before Every Deploy, Verify:

1. **Health check**: `GET /api/admin/health` returns `{"status":"healthy","daemon_running":true}`

2. **City pages load**: For each ad-ready city, check `GET /permits/{slug}` returns 200 with real data:
   ```bash
   for slug in chicago-il new-york-city phoenix-az san-antonio-tx miami-dade-county; do
     status=$(curl -s -o /dev/null -w "%{http_code}" "https://permitgrab.com/permits/$slug")
     echo "$slug: $status"
   done
   ```

3. **Data freshness**: No ad-ready city should have permits older than 7 days:
   ```sql
   SELECT source_city_key, MAX(date) as newest FROM permits
   WHERE source_city_key IN ('chicago-il','new-york-city','phoenix-az','san-antonio-tx','miami-dade-county')
   GROUP BY source_city_key
   ```

4. **Phone counts haven't regressed**:
   ```sql
   SELECT source_city_key, COUNT(*) as profiles,
     SUM(CASE WHEN phone IS NOT NULL AND phone != '' THEN 1 ELSE 0 END) as phones
   FROM contractor_profiles
   WHERE source_city_key IN ('chicago-il','new-york-city','phoenix-az','san-antonio-tx')
   GROUP BY source_city_key ORDER BY phones DESC
   ```

5. **No garbage data**:
   ```sql
   SELECT source_city_key, COUNT(*) FROM contractor_profiles
   WHERE business_name ~ '^[0-9]+$' OR LENGTH(business_name) < 3
   GROUP BY source_city_key HAVING COUNT(*) > 10
   ```

6. **Pricing page works**: `GET /pricing` returns 200, Stripe checkout loads

7. **No 500 errors in recent logs**: Check Render dashboard or `scraper_runs` for errors

### After Every Deploy:
1. Restart the daemon: `POST /api/admin/start-collectors`
2. Wait 5 minutes, then check health
3. Verify collection is happening: check `scraper_runs` for new entries
4. If FL DBPR was changed, trigger: `POST /api/admin/license-import {"state":"FL"}`

---

## KNOWN BUGS & CURRENT BLOCKERS

### P0: FL DBPR Column Position Mismatch (V244d)
- Import runs but matches 0 records. applicants_phone_indexed=2 out of ~100K.
- Root cause: STATE_CONFIGS['FL'] applicant_columns (13 fields) and licensee_columns (20 fields) don't match actual CSV layout from myfloridalicense.com
- Fix: SSH in, `head -3` each CSV, count actual columns, update the column lists
- Impact: Unlocks 6+ FL cities for phone enrichment

### P1: Tampa/Memphis Accela Scraper
- Both have permits but 0 profiles because contractor_name is NULL
- Root cause: accela_portal_collector.py scrapes HTML search grid, but the grid has NO contractor column
- Fix: Need new scraper feature to follow each permit detail link and parse contractor info from detail page
- Impact: Tampa alone is 1,778 permits

### P2: NYC Phone Count Dropped
- NYC had 4,362 phones, now showing 466. Investigate whether data was lost or re-imported.

### P3: SEO — Not Indexed
- 2/3,115 pages indexed. Need to fix technical SEO issues and wait for Googlebot.

---

## HARD-WON RULES (NEVER VIOLATE)

1. **Test endpoints via SSH, never local curl.** Code's sandbox proxy blocks everything.
2. **Check freshness, not record count.** 300K stale records = worthless.
3. **One city at a time, sequential.** No parallel discovery, no bulk operations.
4. **No auto-discovery.** No Socrata Discovery API, no ArcGIS Hub search, no state-level portals. Manual google-test-add only.
5. **No state-level datasets.** NJ failed 20+ times. State portals never work.
6. **Check DB before investigating platforms.** Query permits/profiles/scraper_runs FIRST.
7. **Full preflight before any code change.** Audit the full pipeline before writing a fix. Never discover problems one at a time.
8. **Fix immediately, don't just report.** If you find a bug, fix it in the same PR.
9. **MapServer queries MUST include returnGeometry=false.**
10. **The daemon must be restarted after every deploy** via POST /api/admin/start-collectors.

---

## AUTONOMOUS OPERATION LOOP

When running autonomously, follow this cycle:

### 1. HEALTH CHECK (every run)
- Hit /api/admin/health
- Check daemon_running, errors_last_24h, fresh_city_count
- If daemon stopped → restart it
- If errors > 100 → investigate scraper_runs for patterns

### 2. DATA QUALITY AUDIT (daily)
- Run the garbage data queries above
- Check for stale cities
- Check phone counts haven't regressed
- Fix any issues found

### 3. FL DBPR FIX (until resolved)
- Check if FL cities have >50 phones each
- If not → the import is still broken → investigate and fix
- Once working, this is the single biggest win

### 4. CITY EXPANSION (ongoing)
- Pick the next largest US city by population that we don't have
- Research its permit data portal
- Test endpoint via SSH
- Add config, deploy, verify
- Target: work down the top 100 US cities by population

### 5. VIOLATION EXPANSION (ongoing)
- For each city that has profiles but no violations, search for ArcGIS/Socrata code enforcement data
- Test, configure, deploy

### 6. SEO IMPROVEMENT (weekly)
- Query DB for real city stats
- Update city page content with real numbers
- Fix any technical SEO issues (canonicals, H1s, meta descriptions)
- Check that new pages are being added to sitemap

### 7. UAT (after every change)
- Run the full UAT checklist above
- Verify no regressions

---

## SKILLS SYSTEM

You have six skills installed in `.claude/skills/`. Read the relevant SKILL.md BEFORE performing
that type of work. Skills contain battle-tested procedures, exact SQL queries, and failure recovery.

### Available Skills

| Skill | When to Use | Location |
|-------|-------------|----------|
| city-onboarding | Adding ANY new city | .claude/skills/city-onboarding/SKILL.md |
| data-quality-audit | Daily quality checks, before deploys | .claude/skills/data-quality-audit/SKILL.md |
| seo-real-data | Writing/updating city page content | .claude/skills/seo-real-data/SKILL.md |
| enrichment-pipeline | Improving phone coverage | .claude/skills/enrichment-pipeline/SKILL.md |
| health-monitor | Health checks, incident response | .claude/skills/health-monitor/SKILL.md |
| uat-deploy | Pre-deploy validation, post-deploy checks | .claude/skills/uat-deploy/SKILL.md |

### How to Use Skills

1. Before starting a task, identify which skill applies
2. Read the SKILL.md file to get the exact procedure
3. Follow the procedure step by step — don't skip steps
4. Use the output format specified in the skill for reporting

### Autonomous Loop with Skills

Each cycle of the autonomous loop should use skills:

1. **HEALTH CHECK** → Read `health-monitor/SKILL.md`, follow the health check sequence
2. **DATA QUALITY** → Read `data-quality-audit/SKILL.md`, run the full audit
3. **ENRICHMENT** → Read `enrichment-pipeline/SKILL.md`, check phone coverage, trigger imports
4. **CITY EXPANSION** → Read `city-onboarding/SKILL.md`, onboard the next city
5. **SEO** → Read `seo-real-data/SKILL.md`, update city pages with real numbers
6. **UAT** → Read `uat-deploy/SKILL.md`, run the full checklist after any change

---

## RENDER DEPLOYMENT

- **Service**: permitgrab (web service)
- **SSH**: `ssh srv-d6s1tvsr85hc73em9ch0@ssh.oregon.render.com`
- **Memory limit**: 512MB (import jobs MUST stay under ~200MB peak)
- **Auto-deploy**: Pushes to main trigger automatic deploy
- **Logs**: Available via Render dashboard or SSH

---

## FILE STRUCTURE (key files)

- `server.py` — Flask app, admin API routes, daemon startup
- `collector.py` — Permit collection daemon, _collect_all_inner()
- `city_configs.py` — CITY_REGISTRY dict, STATE_CONFIGS, field_maps
- `license_enrichment.py` — State license imports (FL DBPR, MN DLI, etc.)
- `accela_portal_collector.py` — Accela HTML scraper
- `templates/` — Jinja2 templates for city pages, homepage, blog

---

## WHAT SUCCESS LOOKS LIKE

You wake up and:
- 15+ cities have real permit data from this week
- Each city page shows real contractor names, real permit types, real numbers
- Phone coverage is 20%+ for ad-ready cities
- No stale data, no garbage profiles, no broken pages
- New cities are being added automatically
- Google is indexing pages and organic traffic is growing
- Pricing page works, Stripe checkout works
- The site looks like a real product someone would pay $149/mo for
