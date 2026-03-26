# Two Bugs to Fix

## Bug 1: City Dropdown Duplicates

**What's happening:** The Cities dropdown in the nav (and the /cities page) shows duplicate entries like "LITTLE ROCK" and "Little Rock", and shows "Orleans" instead of "New Orleans". Some entries also link to wrong slugs (e.g. `/permits/mesa-az` instead of `/permits/mesa`).

**Root cause:** `nav_cities` is populated by `get_cities_with_data()` (server.py line 1816), which calls `permitdb.get_cities_with_permits()` (db.py line 408). This does `SELECT DISTINCT city, state` from the permits table — and the DB has permits stored with inconsistent city names from different collectors (e.g., "LITTLE ROCK" from one source and "Little Rock" from another, or "Orleans" from a bulk source instead of "New Orleans").

**Where to fix:**

Option A (recommended): Fix `get_cities_with_data()` in server.py (line 1578) to normalize/deduplicate city names before returning. Group by lowercased city+state, pick the properly-capitalized version (title case), and merge permit counts.

Option B: Fix at the DB level — add a normalization step to the collector that title-cases city names before inserting.

**Also:** We already cleaned up `city_configs.py` locally to deactivate 34 duplicate configs (the file has uncommitted changes). Commit and push `city_configs.py` as part of this fix — it reduces active configs from 336 to 311 with zero duplicate city+state combos. The `/api/cities` endpoint currently returns the old 336 with 21 dupes because the cleanup hasn't been deployed yet.

**Nav template location:** `templates/partials/nav.html` — uses `{% for city in nav_cities[:10] %}` with `city.slug` and `city.name`.

---

## Bug 2: Raw Coordinate JSON Showing as Addresses

**What's happening:** On the Kensington, MD page (`/permits/kensington`) and likely other Montgomery County MD bulk-sourced cities, addresses display as raw JSON strings like:

```
{'latitude': '39.032907', 'longitude': '-77.101436', 'human_address': '{"address": "", "city": "", "state": "", "zip": ""}'}
```

**Root cause:** Montgomery County MD's Socrata dataset stores location data as Socrata location objects with lat/lng but **empty** human_address fields (address, city, state, zip are all `""`).

The `clean_address` Jinja filter (server.py line 52) calls `parse_address_value()` from collector.py (line 24), which correctly handles this format — it parses the string via `ast.literal_eval`, finds the empty human_address, and returns `''`.

BUT: The `clean_address` filter IS applied in the template on the deployed code (city_landing.html line 924), and the live site still shows coordinates. This means either:
1. The deployed version doesn't have the `clean_address` filter yet (likely — it was added in a recent session but may not have been pushed), OR
2. The data is coming through a different code path

**Where to fix:**

1. Make sure the `clean_address` template filter (server.py line 52) is deployed
2. Also apply `| clean_address` in ALL templates that display addresses:
   - `templates/city_landing.html` line 924 — already has it
   - `templates/city_trade_landing.html` — check and add if missing
   - `templates/dashboard.html` line 2166 — uses JavaScript `escapeHtml(p.display_address || p.address)`, needs JS-side cleaning
   - `templates/map.html` line 392 — uses JavaScript `${permit.address}`, needs JS-side cleaning
3. Ideally, also fix at **collection time** in collector.py: when `parse_address_value()` returns empty string for a Socrata location object, store `''` in the DB instead of the raw JSON string. This prevents the bad data from being stored in the first place.

**The format to handle:** Socrata location objects stored as Python dict strings with `'latitude'`, `'longitude'`, and `'human_address'` keys where human_address contains empty fields.
