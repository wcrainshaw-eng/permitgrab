# SPEC: Discovery Engine Overhaul — Get Past 119,020 Permits

**Version**: V12.56
**Date**: 2026-03-23
**Status**: Ready for implementation
**Priority**: Critical — the engine has searched 481 counties and onboarded ZERO new cities

---

## Problem Statement

The autonomy engine (`autonomy_engine.py`) has been running since V12.54 but has not added a single new permit to the database. The count has been stuck at 119,020 for days.

**Current county breakdown (from production DB):**
- `not_started`: 2,488
- `no_data`: 481 (searched, found nothing viable)
- `has_data`: 153 (ALL pre-existing, `source_key: None` — none from the engine)
- `covered_by_state`: 21
- `searching`: 1

The engine processes counties, searches Socrata + ArcGIS, but every candidate gets rejected by one of these gates:
1. Domain relevance filter (`is_domain_relevant()`)
2. Score threshold (< 60)
3. Recency gate (no data in last 90 days)
4. Validation failure (< 50% of sample has address + date)
5. Zero permits after normalization

---

## Root Cause Analysis

### Why Socrata-only discovery fails for most counties

Socrata hosts maybe 200-300 active permit datasets across the entire US. There are ~3,144 counties. **Most counties don't publish permit data on Socrata.** The engine searches Socrata and ArcGIS, but the search strategies are too narrow.

### Specific issues in the current pipeline

#### Issue 1: Search is too narrow
The engine only searches:
- Socrata catalog API with 2 keywords: "building permits" and "construction permits"
- 6 common domain patterns via `try_common_domains()` (most don't exist)
- ArcGIS Hub search

**Missing strategies:**
- No direct web scraping of county/city permit portals
- No OpenData.gov / CKAN catalog search
- No state-level permit aggregators (many states publish statewide data)
- No known-good domain database (manually curated list of portals)
- No Google Dataset Search integration

#### Issue 2: Recency gate is too strict
`check_data_recency()` requires data from the last 90 days. Many legitimate datasets update quarterly or semi-annually. A dataset with data from 4 months ago is still valuable but gets rejected.

#### Issue 3: The "type dict is not supported" error
The initial collection (`collector.py`)'s `normalize_permit()` and `normalize_permit_bulk()` were passing raw Socrata location dicts to SQLite. V12.55c patched this but the fix needs verification — this error means existing city sources with location fields are failing to collect new data on every cycle.

#### Issue 4: Edmonton/AB Canadian data still in permits table
The old `king-wa-bulk` source ingested Edmonton, AB (Canada) permits. The source was deleted but ~1,978 Edmonton permits remain in the `permits` table and show up at the top of "Best Leads" sorted by date.

---

## Implementation Plan

### Fix 1: Clean up Edmonton/AB permits (5 min, do first)

**File**: Run on Render shell (one-time)

```sql
DELETE FROM permits WHERE state = 'AB';
DELETE FROM permits WHERE city = 'Edmonton' AND state NOT IN (
  SELECT DISTINCT state FROM permits WHERE state LIKE '__' AND length(state) = 2
);
```

Or more targeted:
```python
# On Render shell:
python3 -c "
import sqlite3
conn = sqlite3.connect('/var/data/permitgrab.db')
deleted = conn.execute(\"DELETE FROM permits WHERE state = 'AB'\").rowcount
conn.commit()
print(f'Deleted {deleted} AB permits')
"
```

### Fix 2: Fix the "type dict is not supported" error (30 min)

**File**: `collector.py`, functions `normalize_permit()` and `normalize_permit_bulk()`

The V12.55c patch added `parse_address_value()` but the `get_field()` helper still does `str(raw_record.get(raw_key, ""))` which converts dicts to strings. The issue is that SQLite receives the raw dict *before* `str()` conversion in some code paths.

**What to do:**
1. In `normalize_permit()` (line ~735), find every place where `raw_record.get()` is used directly (not through `get_field()`) and ensure the value is stringified
2. In `normalize_permit_bulk()` (line ~530), same thing
3. Add a safety wrapper: before `permitdb.upsert_permits()` is called, iterate through each permit dict and ensure every value is a string or number (not a dict or list)

**Suggested safety net in `db.py` `upsert_permits()` function:**
```python
# Add at top of upsert_permits():
for permit in permits:
    for key, val in permit.items():
        if isinstance(val, (dict, list)):
            permit[key] = str(val)  # Safety: prevent SQLite binding errors
```

### Fix 3: Add state-level permit data sources (2-3 hours, highest impact)

**File**: New file `state_sources.py` or add to `autonomy_engine.py`

Many states publish statewide building permit data. These are goldmines — one source covers dozens or hundreds of counties.

**Known state-level Socrata datasets to add as seeds:**

| State | Domain | Dataset ID | Notes |
|-------|--------|-----------|-------|
| MD | data.montgomerycountymd.gov | Various | Montgomery County MD (already working, 11K+ permits) |
| TX | data.texas.gov | Search for "building permits" | State portal |
| CA | data.ca.gov | Search for "construction" | State portal |
| NY | data.ny.gov | Search for "building permits" | State portal |
| FL | myfloridalicense.com | May need scraper | DBPR contractor/permit data |
| WA | data.wa.gov | Search for "building" | State portal |
| CO | data.colorado.gov | Search for "permits" | State portal |
| IL | data.illinois.gov | Search for "building" | State portal |
| PA | data.pa.gov | Search | State portal |
| VA | data.virginia.gov | Search | State portal |

**Implementation approach:**
1. Create a `STATE_PORTALS` dict mapping state abbreviations to known Socrata domains
2. In `process_county()`, before the generic catalog search, check if the county's state has a known portal domain
3. Search that specific domain first with `search_socrata_domain()`
4. This dramatically increases hit rate because state portals often have county-level data

```python
STATE_PORTALS = {
    'TX': ['data.texas.gov'],
    'CA': ['data.ca.gov', 'data.lacity.org'],
    'NY': ['data.ny.gov', 'data.cityofnewyork.us'],
    'FL': ['data.florida.gov'],
    'WA': ['data.wa.gov'],
    'CO': ['data.colorado.gov'],
    'IL': ['data.illinois.gov', 'data.cityofchicago.org'],
    'MD': ['data.montgomerycountymd.gov', 'data.maryland.gov'],
    'VA': ['data.virginia.gov'],
    'PA': ['data.pa.gov'],
    'OH': ['data.ohio.gov'],
    'GA': ['data.georgia.gov'],
    'NC': ['data.nc.gov'],
    'MI': ['data.michigan.gov'],
    'NJ': ['data.nj.gov'],
    # Add more as discovered
}
```

### Fix 4: Loosen recency gate for initial discovery (30 min)

**File**: `autonomy_engine.py`, `process_county()` around the recency check

**Current**: Hard reject if no data in last 90 days
**Proposed**: Accept datasets with data in last 365 days for initial onboarding. The daily collection cycle will keep fetching fresh data going forward.

```python
# Change check_data_recency to accept 365 days for discovery
# In process_county(), around line 287:
has_recent = check_data_recency(sample, fm.get('date'), days=365)  # was 90
```

Also update `check_data_recency()` in `auto_discover.py` to accept a `days` parameter:
```python
def check_data_recency(sample, date_field, days=90):
    """Check if sample has records from last N days."""
    if not sample or not date_field:
        return False
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    # ... rest same
```

### Fix 5: Expand search keywords (15 min)

**File**: `autonomy_engine.py`, `process_county()` keyword list

**Current**: Only searches "building permits" and "construction permits"
**Add**: "permit", "building inspection", "code enforcement", "planning permits", "development permits"

```python
# In process_county():
for keyword in ['building permits', 'construction permits', 'permits',
                'building inspection', 'code enforcement', 'development permits']:
```

### Fix 6: Add bulk scoring bonus for datasets with many records (15 min)

**File**: `auto_discover.py`, `score_dataset()` function

Datasets with 10,000+ records are almost certainly real permit databases. Add a scoring bonus:

```python
# In score_dataset(), after existing scoring:
if record_count and record_count > 10000:
    score += 10
elif record_count and record_count > 1000:
    score += 5
```

### Fix 7: Relax domain filter for .gov domains (15 min)

**File**: `autonomy_engine.py`, `is_domain_relevant()`

**Current**: `.gov` domains must contain state name/abbreviation. This rejects legitimate portals like `data.cityofhenderson.com` or `hub.arcgis.com`.

**Proposed**: Accept all `.gov` and `.us` domains (they're government by definition). Only reject foreign TLDs and obvious mismatches.

```python
def is_domain_relevant(domain, name, state):
    # Reject foreign TLDs (keep this)
    if any(domain_lower.endswith(tld) for tld in FOREIGN_TLDS):
        return False
    # Accept all .gov and .us domains — they're government by definition
    if domain_lower.endswith('.gov') or domain_lower.endswith('.us'):
        return True
    # For .com/.org/.net — require some connection to the jurisdiction
    # ... existing logic for name/state matching
```

---

## Verification

After implementing fixes 1-7, monitor these metrics:

1. **Permit count**: Should start climbing past 119,020
2. **County status breakdown**: `has_data` should increase, `no_data` should decrease as counties get re-searched
3. **Render logs**: Look for `[Autonomy] X permits loaded (Y new, Z updated)` messages
4. **Source key populated**: New `has_data` counties should have non-null `source_key`

**To check from Render shell:**
```python
python3 -c "
import sqlite3
conn = sqlite3.connect('/var/data/permitgrab.db')
print('Total permits:', conn.execute('SELECT count(*) FROM permits').fetchone()[0])
for row in conn.execute('SELECT status, count(*) FROM us_counties GROUP BY status').fetchall():
    print(f'  {row[0]}: {row[1]}')
print('Counties with source_key:',
    conn.execute('SELECT count(*) FROM us_counties WHERE source_key IS NOT NULL').fetchone()[0])
"
```

---

## Also deploy (already committed)

- `0f39730` — V12.55c fix: `ast.literal_eval` for address cleanup on startup (not yet pushed)

Push with: `git push origin main`
