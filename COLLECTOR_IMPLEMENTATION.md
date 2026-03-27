# PermitGrab Collector Redesign - Implementation Status

**Date:** March 27, 2026

This document tracks the implementation of the collector redesign as specified in `COLLECTOR_REDESIGN.txt`.

---

## Phase 1 Status: FOUNDATION

### Day 1-2 Tasks

| Task | Status | Notes |
|------|--------|-------|
| Export source inventory | **DONE** | `source_inventory.json`, `source_inventory_summary.csv` |
| Create `prod_cities` table | **DONE** | `migrations/001_prod_cities_and_scraper_runs.sql` |
| Create `scraper_runs` table | **DONE** | `migrations/001_prod_cities_and_scraper_runs.sql` |
| Seed 17 fresh cities | **DONE** | `migrations/002_seed_fresh_cities.sql` |

### Source Inventory Summary

From `export_source_inventory.py`:

```
CITY_REGISTRY:    555 total (311 active, 244 inactive)
BULK_SOURCES:      25 total (15 active, 10 inactive)
COMBINED:         580 total (326 active, 254 inactive)
```

### High-Value Inactive Sources

These 10 bulk sources are inactive but could cover many cities:

1. `miami_dade_county` (FL) - 34 cities
2. `dallas_tx_bulk` (TX) - all Dallas zip codes
3. `fort_worth_tx_bulk` (TX) - Pop ~978K
4. `orlando_fl_bulk` (FL) - metro area
5. `mesa_az_bulk` (AZ) - 34 fields
6. `san_diego_county_v2` (CA) - public endpoint
7. `corona_ca_bulk` (CA)
8. `norfolk_va_bulk` (VA)
9. `austin_tx_datahub_bulk` (TX) - possibly duplicate
10. `pierce_county_wa_socrata_bulk` (WA)

---

## Remaining Implementation Steps

### Day 3-4: State/County Discovery

**TODO:** Run automated checks on all 50 states and top counties.

For each state:
1. Check Socrata API: `https://api.us.socrata.com/api/catalog/v1?domains=data.{state}.gov&categories=Building`
2. Check if there's a statewide permit dataset
3. If found, test with the 5-step protocol
4. Add passing cities to `prod_cities`

Priority counties to verify:
- Montgomery County MD (configured, degrading)
- Miami-Dade County FL (inactive, 34 cities)
- San Diego County CA (configured, no data)

### Day 5: Site Integration

**DONE:** Modified `server.py` to use `prod_cities` with fallback to heuristics.

#### 1. Replace `get_cities_with_data()`

Current (heuristic-based):
```python
def get_cities_with_data():
    # Uses KNOWN_OK_CITIES, VALID_STATES, etc.
    ...
```

New (table-based):
```python
def get_cities_with_data():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT city, state, city_slug, total_permits, last_permit_date
        FROM prod_cities
        WHERE status = 'active'
        ORDER BY state, city
    ''')
    return cursor.fetchall()
```

#### 2. Replace city count

Current:
```python
city_count = "850+"  # Hardcoded or heuristic
```

New:
```python
def get_active_city_count():
    cursor.execute('SELECT COUNT(*) FROM prod_cities WHERE status = "active"')
    return cursor.fetchone()[0]
```

#### 3. Handle non-prod city pages

For cities NOT in `prod_cities`:
- Show "coming soon" message
- Add `<meta name="robots" content="noindex">`
- Capture email for notification when data available

---

## Collector.py Changes

### Current Architecture (to be replaced)

```python
def collect_refresh():
    # Runs every 6 hours via background thread
    # Iterates CITY_REGISTRY + BULK_SOURCES
    # Uses failure_tracker to skip cities with 10+ failures
    # No per-city logging
```

### New Architecture

```python
def collect_refresh():
    """
    New collection loop using prod_cities.
    """
    conn = get_db_connection()

    # Get active cities from prod_cities
    cities = conn.execute('''
        SELECT city_slug, source_id, source_type
        FROM prod_cities
        WHERE status = 'active'
        ORDER BY last_collection ASC  -- Oldest first
    ''').fetchall()

    for city in cities:
        start_time = time.time()

        try:
            # Get config from CITY_REGISTRY or BULK_SOURCES
            config = get_source_config(city['source_id'])

            # Run collection
            permits = collect_city(config)

            # Insert permits
            inserted = insert_permits(permits, city['city_slug'])

            # Log success
            log_scraper_run(
                city_slug=city['city_slug'],
                status='success' if inserted > 0 else 'no_new',
                permits_found=len(permits),
                permits_inserted=inserted,
                duration_ms=int((time.time() - start_time) * 1000)
            )

            # Update prod_cities
            update_prod_city(city['city_slug'],
                last_collection=datetime.now(),
                consecutive_failures=0
            )

        except Exception as e:
            # Log error
            log_scraper_run(
                city_slug=city['city_slug'],
                status='error',
                error_message=str(e),
                duration_ms=int((time.time() - start_time) * 1000)
            )

            # Increment failure counter
            failures = increment_failures(city['city_slug'])

            # Pause if 3+ consecutive failures
            if failures >= 3:
                update_prod_city(city['city_slug'], status='paused')

            # Continue to next city (don't abort!)
            continue
```

### Remove Failure Tracker

Delete or disable the file-based failure tracker:
```python
# city_failures.json - DELETE
# failure_tracker.py - DELETE or deprecate
```

---

## Admin Dashboard

### New Route: `/admin/collector-health`

```python
@app.route('/admin/collector-health')
@require_admin
def collector_health():
    conn = get_db_connection()

    # Get health status for all cities
    cities = conn.execute('''
        SELECT * FROM city_health_status
        ORDER BY
            CASE health_color
                WHEN 'RED' THEN 1
                WHEN 'YELLOW' THEN 2
                ELSE 3
            END,
            days_since_data DESC
    ''').fetchall()

    # Get today's collection summary
    summary = conn.execute('''
        SELECT * FROM daily_collection_summary
        WHERE run_date = DATE('now')
    ''').fetchone()

    return render_template('admin/collector_health.html',
        cities=cities,
        summary=summary
    )
```

---

## Files Modified/Created

### New Files
- `migrations/001_prod_cities_and_scraper_runs.sql`
- `migrations/002_seed_fresh_cities.sql`
- `source_inventory.json`
- `source_inventory_summary.csv`
- `export_source_inventory.py`
- `COLLECTOR_IMPLEMENTATION.md` (this file)

### Files to Modify
- `server.py` - Replace heuristics with `prod_cities` queries
- `collector.py` - New collection loop with per-city logging
- `templates/base.html` - Dynamic city count
- `templates/city.html` - Handle non-prod cities

### Files to Delete/Deprecate
- `city_failures.json` (failure tracker)
- `KNOWN_OK_CITIES` constant (if exists)
- `VALID_STATES` constant (if exists)

---

## Migration Commands

Run on production database (Render):

```bash
# 1. Create tables
sqlite3 data/permitgrab.db < migrations/001_prod_cities_and_scraper_runs.sql

# 2. Seed fresh cities
sqlite3 data/permitgrab.db < migrations/002_seed_fresh_cities.sql

# 3. Verify
sqlite3 data/permitgrab.db "SELECT COUNT(*) FROM prod_cities WHERE status='active';"
# Expected: 17
```

---

## Phase 2+ Tasks

### Week 2: Expand
- [ ] Check remaining top 200 counties
- [ ] Check Tier 1 cities (500k+ population)
- [ ] Reactivate `miami_dade_county` bulk source
- [ ] Fix NJ statewide source
- [x] Build `/admin/collector-health` dashboard **DONE**

### Week 3+: Scale
- [ ] Continue city-level discovery
- [ ] Backfill missing data
- [ ] Add collection failure alerts
- [ ] Complete discovery engine run (all 30k cities)

---

## Success Metrics

| Week | Target | Metric |
|------|--------|--------|
| 1 | 50-100 verified cities | `SELECT COUNT(*) FROM prod_cities WHERE status='active'` |
| 2 | 150-250 verified cities | Same |
| 4 | Discovery complete | All 30k cities checked, final prod list |

---

## Current State Summary

**As of March 27, 2026:**

- Database schema: **DONE** (added to `db.py` init_db())
- Fresh city seeds: **Ready** (17 cities in migration SQL)
- Source inventory: **Exported** (580 sources, 326 active)
- Site integration: **DONE** (server.py updated)
  - `get_cities_with_data()` uses prod_cities with fallback
  - `get_total_city_count_auto()` uses prod_cities with fallback
  - City pages check prod_cities for coming soon/noindex
- Admin dashboard: **DONE** (`/admin/collector-health` route)
- Collector rewrite: **Not started**
- State/county discovery: **Not started**

**What's Implemented:**
1. `db.py` - Added `prod_cities` and `scraper_runs` tables to init_db()
2. `db.py` - Added helper functions:
   - `get_prod_cities()` - Get all prod cities
   - `get_prod_city_count()` - Count active cities
   - `is_prod_city()` - Check if city slug exists
   - `get_city_health_status()` - Health dashboard data
   - `upsert_prod_city()` - Insert/update city
   - `update_prod_city_collection()` - Update after collection run
   - `log_scraper_run()` - Log collection runs
   - `get_daily_collection_summary()` - Daily stats
   - `get_recent_scraper_runs()` - Recent run history
   - `prod_cities_table_exists()` - Check if table has data
3. `server.py` - Updated to use prod_cities with fallback to heuristics
4. `server.py` - Added `/admin/collector-health` dashboard route

**Next immediate step:**
1. Deploy to production (git push)
2. Database will auto-create tables on startup (init_db)
3. Run seed migration to add 17 fresh cities
4. Verify at `/admin/collector-health`
