# PermitGrab Full UAT Report — V15 through V17b
**Date:** March 27, 2026
**Tester:** Claude (Cowork)
**Scope:** Full site UAT after V15 (sources), V16 (city explosion), V17 (auto-pipeline), V17b (discovery acceleration)

---

## PASS — What's Working

### Homepage
- Loads correctly: 511,653 active permits, 870+ cities, $34.6B total project value, 33,408 high-value leads
- Logged-in state shows user name, Dashboard, Early Intel, Analytics Pro in nav
- Logged-out state shows Log In / Sign Up buttons

### Login/Logout
- Login form loads at /login with autofill
- Login succeeds → redirects to homepage with user nav
- User dropdown works: My Leads, Account, Billing, Log Out
- Logout succeeds → returns to logged-out homepage

### Dashboard (authenticated)
- Shows 511,653 permits with filters (City, Trade, Value Tier, Status, Lead Quality)
- Market Leaders sidebar with real contractor data
- Leads by Trade breakdown (General Construction 2,444, HVAC 2,369)
- Real permit data with addresses, contractor names, project values

### City Pages (working examples)
- **Orlando, FL** (/permits/orlando-fl): 8,255+ permits, $1.0B value — data looks clean
- **San Antonio, TX** (/permits/san-antonio): 2,406+ permits, 735 this month — clean addresses (Vance Jackson Rd, Crestridge Dr, Hildebrand Ave)
- **Chicago** (/permits/chicago): loads correctly
- **Louisville, KY** (/permits/louisville): 1,780+ permits, $107M value

### SEO
- Title tags: correct format "City, ST Building Permits & Contractor Leads | PermitGrab"
- Meta descriptions: present and descriptive
- Canonical URLs: correct
- OG tags: present
- Schema.org Dataset structured data: present
- Robots: `index, follow` for cities WITH permits
- Robots: `noindex, follow` for cities with 0 permits (Paterson NJ) — correct behavior

### Sitemap
- /sitemap.xml loads: 2,185 total URLs, 2,090 permit-related
- State pages present (texas, california, maryland, etc.)
- City pages present

### Collection Pipeline
- Render logs show active collection at 11:57 AM
- Testing cities individually (San Marcos, Celina, Princeton, Prosper, Anna)
- Bulk source fetches running (Albany County ArcGIS, Montgomery County MD)
- Collection is processing all prod_cities (not truncated)

### Discovery Engine (V17b)
- Discovery thread running on Render
- ArcGIS bulk scan completed at 12:06 PM: scanned 2,656 unique items, 110 skipped (already known)
- V17b accelerated discovery completed in 100.7s
- V17 pipeline complete — no new sources this cycle (expected since most already discovered)
- Next cycle expected ~4 hours later

---

## BUGS — Issues Found

### BUG 1: Slug Format Inconsistency (HIGH — affects SEO & user navigation)
**Description:** City slugs are inconsistent between CITY_REGISTRY entries (no state suffix) and V16 bulk-harvest entries (with state suffix). Most cities (2,067 of 2,087) use the no-suffix format, but ~20 use `-state` suffix.

**Impact:** Users and search engines guessing `/permits/city-state` format will get 404s for most cities. The intuitive URL format doesn't work.

**Examples:**
- `/permits/san-antonio-tx` → 404 (actual: `/permits/san-antonio`)
- `/permits/louisville-ky` → 404 (actual: `/permits/louisville`)
- `/permits/mckinney-tx` → 404 (McKinney exists but at different slug)
- `/permits/miami-fl` → 404 (data is from Miami-Dade County bulk)

**Fix:** Either standardize ALL slugs to `city-state` format, OR add redirect logic so `/permits/city-state` automatically redirects to `/permits/city` when the `-state` version doesn't exist but the bare version does. The `city-state` format is better for SEO (disambiguates Portland OR vs Portland ME) and should be the canonical format.

---

### BUG 2: Newark NJ Shows Wrong-State Permit Data (HIGH — data integrity)
**Description:** The Newark, NJ city page displays permits with state codes "CT" and "CL" and addresses that are clearly NOT in Newark (e.g., "CATTLEMANS CREEK RD W", "BEAR CLAW CT", "DODGE CITY TRL", "EDGEMON WAY"). "CL" isn't even a valid US state abbreviation.

**Impact:** Completely wrong data displayed for a major city. Destroys user trust if a contractor in Newark sees Texas-looking addresses.

**Likely Cause:** The NJ statewide bulk source may be mapping a county abbreviation or permit-type field into the state column, OR the city-field matching is pulling in permits from a wrong source.

**Fix:** Investigate which source is feeding Newark NJ permits. Check the field mapping — the "CT"/"CL" values likely come from a `permit_type` or `county` field being mapped to `state`.

---

### BUG 3: Cities with 0 Permits Are Active in prod_cities (MEDIUM)
**Description:** Paterson, NJ shows "0+ Active Permits" and "0+ Permits This Month" but is still listed as an active city on the cities page.

**Impact:** Empty pages hurt SEO (even with noindex) and user experience — clicking through to a city with zero data is a dead end.

**Fix:** Either:
- Don't display cities with 0 permits on the cities browse page
- Or set a minimum permit threshold (e.g., 5+ permits) for a city to appear in listings
- The page itself correctly uses `noindex` so Google won't index it, but it's still reachable from the cities page

---

### BUG 4: Duplicate City Entries — Fort Lauderdale (MEDIUM)
**Description:** Florida shows both "Fort Lauderdale" (124 permits) and "Ft Lauderdale" (32 permits) — same city, different name formats from different data sources.

**Impact:** Split data and duplicate pages for the same city. User confusion.

**Fix:** Normalize city names during collection/harvest. Map common abbreviations: "Ft" → "Fort", "St" → "Saint", "Mt" → "Mount", etc. Merge permit data under the canonical name.

---

### BUG 5: Garbage City Name — "Archived_Buildings" in DC (LOW)
**Description:** DC section shows "Archived_Buildings" with 10 permits. This is clearly a database field name leaking through as a city name, not an actual city.

**Fix:** Add data validation during harvest to filter out entries where city names contain underscores, are all-caps database field names, or match known garbage patterns. Delete this entry from prod_cities.

---

### BUG 6: /api/health Endpoint Missing (LOW)
**Description:** GET /api/health returns 404. This was specified in V16 Step 7 (Task C) but was not implemented.

**Impact:** No automated way to monitor if collection is running. Can't easily check last_collection_run timestamp.

**Fix:** Implement the endpoint as specified:
```json
{
  "status": "ok",
  "last_collection": "2026-03-27T14:30:00",
  "cities_active": 870,
  "collection_interval_hours": 6,
  "next_expected": "2026-03-27T20:30:00"
}
```

---

## OBSERVATIONS (not bugs, but worth noting)

1. **Discovery found 0 new sources this cycle** — expected since V17b just ran. The ArcGIS scan covered 2,656 items and skipped 110 already-known. Next cycle in ~4 hours.

2. **Sitemap is large** (2,185 URLs) — may want to split into multiple sitemaps if it grows past 5,000 URLs (Google's recommendation is max 50,000 per sitemap but smaller is better for crawl efficiency).

3. **"Not listed on permit" values** — Most permits on city pages show "Not listed on permit" for the value field. This is correct behavior when the source data doesn't include project values, but it means the "$34.6B total project value" on the homepage is concentrated in a subset of cities.

4. **Collection timing** — Logs show collection actively running at 11:57 AM with bulk source fetches completing in seconds. The 6-hour cycle appears to handle 870+ cities without issues.

---

## PRIORITY FIX ORDER

1. **BUG 2** (Newark wrong data) — Data integrity is #1. Wrong data = lost trust.
2. **BUG 1** (Slug inconsistency) — Affects every user who types a URL and most SEO value.
3. **BUG 4** (Fort Lauderdale duplicate) — Data quality, probably affects other cities too.
4. **BUG 3** (0-permit cities visible) — Easy fix, improves UX.
5. **BUG 5** (Archived_Buildings) — Quick delete.
6. **BUG 6** (/api/health) — Nice to have for monitoring.
