# PermitGrab City Scaling Plan

## Current State (March 2026)

| Metric | Count |
|--------|-------|
| Active CITY_REGISTRY entries | ~289 |
| Inactive CITY_REGISTRY entries | ~278 |
| Active BULK_SOURCES | 22 |
| Inactive BULK_SOURCES | 4 |
| Active prod_cities | ~290 |
| Distinct cities in permits table | ~2,034 |
| States with some coverage | 50 |

The ~290 active cities are the ones we're currently pulling from. The 2,034 in the permits table are historical — many came from bulk sources but aren't individually tracked. **Only actively pulled cities should be counted in marketing and stats** (fixed in V31).

---

## Scaling Strategy (Ranked by Leverage)

### 1. Reactivate Inactive Sources (~278 potential cities)

**What:** 278 CITY_REGISTRY entries are marked `active: False`. Many were disabled due to temporary API errors, URL changes, or parsing failures that may have been fixed upstream.

**How:**
- Build a `retest_inactive.py` script that:
  1. Iterates over inactive entries in CITY_REGISTRY
  2. Hits each endpoint with a test query (last 7 days)
  3. If permits come back, flips `active: True` and adds to prod_cities
  4. Logs which ones are truly dead vs temporarily down
- Run as a scheduled weekly job
- Estimated yield: 50-100 reactivated cities (based on typical API churn)

**Effort:** Low — the collection infrastructure already handles these configs.

---

### 2. Expand Bulk Sources (Highest volume per effort)

**What:** County and state-level data portals often cover dozens or hundreds of cities in a single API. We have 22 active bulk sources but only 4 inactive. There are far more available.

**How:**
- Target the top 20 states by construction activity that DON'T have a bulk source yet
- Priority states: FL, NY, IL, OH, PA, MI, NJ, VA, NC, GA (check which already have bulk)
- Search Socrata Discovery API for `"building permit"` filtered by state
- Search ArcGIS Online for state/county GIS portals with permit layers
- Each new bulk source can add 20-200 cities instantly

**Effort:** Medium — requires field mapping per source, but `auto_discover.py` already does the searching.

---

### 3. Accelerate Auto-Discovery Pipeline

**What:** `auto_discover.py` already searches Socrata + ArcGIS in parallel. The `activate_pending_cities()` flow tests discovered sources and flips them to active if permits come back.

**How:**
- Run `run_accelerated_discovery()` on a weekly schedule (not just manually)
- Expand `SEARCH_KEYWORDS` and `ARCGIS_SEARCH_KEYWORDS` to catch more datasets
- Add new platform connectors beyond Socrata/ArcGIS:
  - **Accela** (used by hundreds of cities for permit management)
  - **Tyler Technologies / EnerGov** (another major permit platform)
  - **OpenGov / ViewPoint** portals
  - **CityView** systems
- Lower the `min_records` threshold for auto-activation to catch smaller cities

**Effort:** Medium-High — new platform connectors require reverse-engineering each API.

---

### 4. State-Level Aggregators

**What:** Some states aggregate permit data from all jurisdictions into a single portal.

**Known opportunities:**
- **Connecticut**: CT Data Portal has statewide building permits
- **Massachusetts**: MassGIS / Mass.gov data portal
- **Oregon**: Oregon.gov open data
- **Minnesota**: MN Geospatial Commons
- **Colorado**: Already have some; expand coverage
- **Washington**: Already have some; check for statewide dataset

**How:** Each state aggregator = one integration, potentially 50+ cities.

**Effort:** Low per city covered — high leverage.

---

### 5. Partnership / Data Feeds

**What:** Approach permit data aggregators or commercial providers for bulk data licensing.

**Options:**
- **BuildZoom** API (if they offer data partnerships)
- **Construction Monitor** feeds
- **Dodge Construction Network** (expensive but comprehensive)
- **Local government associations** (NLC, ICMA) — some facilitate data sharing

**Effort:** High (business development), but could unlock 1,000+ cities overnight.

---

## Recommended Execution Order

| Phase | Action | Timeline | Expected New Cities |
|-------|--------|----------|-------------------|
| 1 | Retest 278 inactive sources | Week 1 | +50-100 |
| 2 | Add 5-10 new bulk sources (top states) | Weeks 2-3 | +200-500 |
| 3 | Schedule auto-discovery weekly | Week 1 | +20-50/month ongoing |
| 4 | Research state aggregators (top 5) | Weeks 3-4 | +100-250 |
| 5 | Explore data partnerships | Month 2+ | +500-1,000 |

**Conservative 90-day target:** 500-700 actively collected cities (up from ~290)
**Aggressive 90-day target:** 800-1,000 actively collected cities

---

## Quick Wins (This Week)

1. **Run inactive source retest** — zero new code needed if we just iterate CITY_REGISTRY inactive entries and try collecting
2. **Schedule `run_accelerated_discovery()`** as a weekly Render cron job
3. **Search Socrata for top-10 missing states** — manual check, add configs for any hits
4. **Check the 4 inactive BULK_SOURCES** — may just need URL updates
