# PermitGrab Implementation Spec: City Dropdown + Market Leaders Fix

**Date:** March 22, 2026
**Version:** V12.55
**Status:** Ready to implement

---

## Issue 1: City Dropdown Needs State Grouping

### Problem

The city filter dropdown on the dashboard (`/` route) lists 610+ cities alphabetically with no state context. Users can't find their city quickly and cities with the same name in different states are indistinguishable.

### Current Implementation

**HTML** — `templates/dashboard.html` line 1452:
```html
<select id="filter-city">
  <option value="">All Cities</option>
</select>
```

**JavaScript** — `templates/dashboard.html` line 1838-1848:
```javascript
function populateFilters() {
  const cities = [...new Set(allPermits.map(p => p.city))].sort();
  const citySelect = document.getElementById('filter-city');
  const alertCity = document.getElementById('alert-city');
  cities.forEach(c => {
    citySelect.add(new Option(c, c));
    alertCity.add(new Option(c, c));
  });
}
```

Cities come from `allPermits` (loaded via `/api/permits?per_page=10000`). Each permit has both `city` and `state` fields in the `permits` SQLite table.

**Alert signup dropdown** — same template, line 1584: `<select id="alert-city">` is populated identically.

**Default city logic** — lines 1857-1860: if user has a `defaultCity` from onboarding, it's pre-selected via `citySelect.value = defaultCity`. This must continue working after the fix.

### Fix: Prefix Cities with State Abbreviation

Change `populateFilters()` to build `"STATE - City"` labels sorted by state first, then city. The `value` attribute stays as the raw city name so all downstream filtering still works.

**Replace lines 1838-1848 in `templates/dashboard.html` with:**

```javascript
function populateFilters() {
  // Build city-state pairs from permits
  const cityStateMap = new Map();
  allPermits.forEach(p => {
    if (p.city && !cityStateMap.has(p.city)) {
      cityStateMap.set(p.city, p.state || '');
    }
  });

  // Sort by state abbreviation first, then city name
  const cityEntries = [...cityStateMap.entries()].sort((a, b) => {
    const stateCompare = (a[1] || 'ZZ').localeCompare(b[1] || 'ZZ');
    if (stateCompare !== 0) return stateCompare;
    return a[0].localeCompare(b[0]);
  });

  const citySelect = document.getElementById('filter-city');
  const alertCity = document.getElementById('alert-city');
  cityEntries.forEach(([city, state]) => {
    const label = state ? `${state} - ${city}` : city;
    citySelect.add(new Option(label, city));
    alertCity.add(new Option(label, city));
  });

  // (rest of populateFilters continues unchanged below — trades, statuses, defaults)
```

**Why this works:**

- `value` stays as the raw city name (`"Atlanta"`) so `applyFilters()`, `loadMarketLeaders()`, and all `/api/` calls remain unchanged
- Display label becomes `"GA - Atlanta"`, `"TX - Dallas"`, etc.
- Sorted by state then city, so all Georgia cities cluster together
- `defaultCity` matching still works since it compares against `value`, not display text
- Both the filter dropdown AND the alert signup dropdown get the same treatment

**Other dropdowns affected (check if they need the same fix):**

- The alert signup dropdown `#alert-city` at line 1584 — YES, already handled above
- Any other `<select>` elements that show cities — search for `filter-city` and `alert-city` in the template to confirm no others exist

### Testing

1. Load dashboard, open city dropdown — cities should show as "AL - Birmingham", "AZ - Phoenix", etc.
2. Select a city — permits should filter correctly (value is still raw city name)
3. Check that Market Leaders updates when city filter changes
4. Check CSV export still works with city filter applied
5. Test with a user who has `defaultCity` set from onboarding — should auto-select correctly
6. Check the alert signup dropdown shows same format

---

## Issue 2: Market Leaders Showing "NONE" as #1 Contractor

### Problem

The Market Leaders sidebar shows `"NONE / State Lic. / ID: 113204..."` as the #1 contractor with 234 permits and $18.4M in value. This is not a Python `None` bug — it's a real `contact_name` value in the database where the source data literally contains "NONE" as part of the contractor field (e.g., `"NONE / State Lic. / ID: 113204..."`).

### Root Cause

**Server-side filter** — `server.py` lines 2675-2677:
```python
name = p.get('contact_name', '').strip()
if not name or name.lower() in ('n/a', 'unknown', 'none', ''):
    continue
```

This checks for exact matches only. The value `"NONE / State Lic. / ID: 113204..."` lowercased is `"none / state lic. / id: 113204..."` — this does NOT equal `"none"`, so the filter doesn't catch it.

There are likely other garbage patterns in `contact_name` across the 119K permits:

- `"NONE / State Lic. / ID: ..."` — source includes license IDs as contractor name
- `"N/A - ..."` or `"UNKNOWN - ..."` with suffixes
- `"OWNER"` / `"OWNER/BUILDER"` — self-permitted, not a real contractor
- `"VARIOUS"` / `"MULTIPLE"` / `"TBD"` / `"TBA"` / `"PENDING"`
- `"SEE PLANS"` / `"NOT PROVIDED"` / `"NOT APPLICABLE"`
- Very short names like single characters or numbers

### Fix: Improve the Contractor Name Filter

**Replace lines 2672-2677 in `server.py` with:**

```python
    # Aggregate by contractor
    JUNK_NAMES = {'n/a', 'unknown', 'none', 'na', 'tbd', 'tba', 'pending',
                  'various', 'multiple', 'owner', 'owner/builder', 'self',
                  'homeowner', 'not provided', 'not applicable', 'see plans',
                  'not listed', 'not available', 'exempt', '---', '--', '-'}

    contractors = {}
    for p in permits:
        name = (p.get('contact_name') or '').strip()
        if not name:
            continue
        name_lower = name.lower()

        # Skip exact junk matches
        if name_lower in JUNK_NAMES:
            continue

        # Skip names that START with common junk prefixes
        if name_lower.startswith(('none ', 'n/a ', 'unknown ', 'tbd ', 'owner ')):
            continue

        # Skip very short names (likely data artifacts)
        if len(name) < 3:
            continue
```

**Why `(p.get('contact_name') or '').strip()`:**

The current code `p.get('contact_name', '').strip()` has a subtle bug: if the key exists with value `None` (SQL NULL), `.get()` returns `None` (not the default `''`), and `None.strip()` raises `AttributeError`. The `or ''` pattern handles this correctly. This path is currently saved because `query_permits()` returns dicts from SQLite where NULLs become Python `None` but the key still exists.

### Additional Consideration: Database Cleanup

There may be a significant number of permits with junk contractor names. To understand the scope, run this on the Render shell:

```python
python3 -c "
import sqlite3
conn = sqlite3.connect('/var/data/permitgrab.db')
c = conn.cursor()
c.execute('''
    SELECT contact_name, COUNT(*) as cnt
    FROM permits
    WHERE contact_name IS NOT NULL AND contact_name != ''
    GROUP BY LOWER(contact_name)
    ORDER BY cnt DESC
    LIMIT 30
''')
for r in c.fetchall():
    print(f'  {r[1]:>6}  {r[0][:80]}')
conn.close()
"
```

This will reveal the top 30 contractor names by frequency. If there are other garbage patterns beyond what the filter catches, add them to `JUNK_NAMES`.

**Do NOT modify the database itself** — the filter should happen at query time in the API endpoint so the raw data stays intact.

### Frontend: No Changes Needed

The `renderMarketLeaders()` function at lines 2844-2865 in `dashboard.html` just displays whatever the API returns. Once the server filters correctly, the frontend will be fine.

### Testing

1. Load dashboard — Market Leaders should NOT show any "NONE" or junk entries
2. The #1 contractor should be a real company name
3. Filter by a specific city — market leaders should update with real contractors
4. Check `/api/contractors/top` directly — no junk names in the JSON response
5. Verify no `AttributeError` crashes by checking that permits with NULL `contact_name` are handled

---

## Bonus: Edmonton Data in Permits Table

The screenshot showed a permit from "Edmonton, AB" as the first result on the dashboard (from the `king-wa-bulk` bad source). We already deleted that source from `city_sources`, but the permits it pulled may still be in the `permits` table.

**Cleanup command for Render shell:**

```python
python3 -c "
import sqlite3
conn = sqlite3.connect('/var/data/permitgrab.db')
c = conn.cursor()
# Check for non-US data
c.execute(\"SELECT city, state, COUNT(*) FROM permits WHERE state NOT IN ('AL','AK','AZ','AR','CA','CO','CT','DE','FL','GA','HI','ID','IL','IN','IA','KS','KY','LA','ME','MD','MA','MI','MN','MS','MO','MT','NE','NV','NH','NJ','NM','NY','NC','ND','OH','OK','OR','PA','RI','SC','SD','TN','TX','UT','VT','VA','WA','WV','WI','WY','DC') OR state IS NULL GROUP BY city, state ORDER BY COUNT(*) DESC\")
for r in c.fetchall():
    print(f'  {r[0]}, {r[1]}: {r[2]} permits')
conn.close()
"
```

If Edmonton/AB or other non-US entries appear, delete them:

```python
c.execute("DELETE FROM permits WHERE state = 'AB'")  # Alberta, Canada
# Add more as needed based on the query above
```

---

## Files to Modify

| File | Change |
|------|--------|
| `templates/dashboard.html` | Lines 1838-1848: Replace `populateFilters()` city population logic |
| `server.py` | Lines 2672-2677: Replace contractor name filtering in `api_top_contractors()` |

## Estimated Effort

Both fixes are < 20 lines of code changes each in well-defined locations. No database migrations needed. No new dependencies.
