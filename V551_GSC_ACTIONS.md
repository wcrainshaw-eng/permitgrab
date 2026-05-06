# V551 — GSC Relaunch Actions

**Captured:** 2026-05-06 ~14:25 UTC
**Trigger:** V544 indexation contract is finally real (V546c fix made
the sitemap actually serve the 35 Pass cities). Google's 2-7 day
re-evaluation cycle starts NOW.

## Sitemap state (verified)

`https://permitgrab.com/sitemap.xml` index → 5 sub-sitemaps:
- sitemap-pages.xml
- sitemap-cities.xml ← **35 city URLs** (was 1,713 pre-V546c)
- sitemap-states.xml (45 state hubs)
- sitemap-trades.xml
- sitemap-blog.xml

`/sitemap-cities.xml` confirmed serving the V546-era Pass set:
new-york-city, chicago-il, new-orleans, cincinnati-oh, cambridge-ma,
washington-dc, nashville, miami-dade-county, raleigh, sacramento-ca,
+ 25 more.

## Wes action checklist (most via Chrome MCP — GSC API not in Code's sandbox)

### 1. Resubmit sitemap to Google Search Console

**Chrome MCP path:**
1. Open https://search.google.com/search-console
2. Property: permitgrab.com
3. Sidebar → Sitemaps
4. Click "Add a new sitemap"
5. Enter: `sitemap.xml`
6. Click Submit

**Expected GSC response:** "Sitemap submitted successfully" + a
"Discovered URLs" count of ~80 (35 cities + 45 states + a few
blog/page URLs). If GSC reports 1,000+, V546c didn't actually take
effect — re-check the live sitemap.

### 2. Resubmit to Bing Webmaster Tools

1. Open https://www.bing.com/webmasters
2. Property: permitgrab.com
3. Sitemaps → Submit Sitemap
4. Enter: `https://permitgrab.com/sitemap.xml`

### 3. URL Inspection requests for the 35 Pass cities

For each of the 35 Pass cities (full list below), use GSC URL
Inspection → "Request Indexing":

```
https://permitgrab.com/permits/new-york-state/new-york-city
https://permitgrab.com/permits/illinois/chicago-il
https://permitgrab.com/permits/california/los-angeles
https://permitgrab.com/permits/pennsylvania/philadelphia
https://permitgrab.com/permits/district-of-columbia/washington-dc
https://permitgrab.com/permits/arizona/phoenix-az
https://permitgrab.com/permits/nevada/las-vegas
https://permitgrab.com/permits/colorado/denver-co
https://permitgrab.com/permits/florida/miami
https://permitgrab.com/permits/florida/miami-dade-county
https://permitgrab.com/permits/california/san-jose
https://permitgrab.com/permits/north-carolina/charlotte
https://permitgrab.com/permits/ohio/columbus
https://permitgrab.com/permits/texas/fort-worth
https://permitgrab.com/permits/california/sacramento-ca
https://permitgrab.com/permits/california/sacramento-county
https://permitgrab.com/permits/ohio/cincinnati-oh
https://permitgrab.com/permits/ohio/cleveland-oh
https://permitgrab.com/permits/pennsylvania/pittsburgh
https://permitgrab.com/permits/north-carolina/raleigh
https://permitgrab.com/permits/tennessee/nashville
https://permitgrab.com/permits/louisiana/new-orleans
https://permitgrab.com/permits/florida/orlando-fl
https://permitgrab.com/permits/new-mexico/bernalillo-county
https://permitgrab.com/permits/washington/everett
https://permitgrab.com/permits/colorado/fort-collins-co
https://permitgrab.com/permits/texas/sugar-land
https://permitgrab.com/permits/massachusetts/cambridge-ma
```

(Pull the live list via `curl /sitemap-cities.xml | grep -oE
'<loc>[^<]+'` — count is currently 35 but will grow as enrichment
imports + V547i deep-fetch lift more cities to Pass.)

### 4. Reconcile conditional noindex emissions

V544 already unified the city-page noindex gate onto
`city_health.is_sellable_city`. Verify by spot-checking 5 of the 35
Pass cities — fetch each `/permits/<state>/<city>` URL and assert
`<meta name="robots" content="index, follow">` is present (NOT
`noindex`). Already covered by:
- `tests/test_city_health.py:test_v544_*`
- `tests/test_routes.py:test_v257_city_page_prod_rich_pages_are_indexed`

If a Pass city emits noindex in production, V544 has regressed.

### 5. Audit `<link rel="canonical">` on Pass pages

Every Pass page must have `<link rel="canonical" href="<exact URL>">`
matching the live URL precisely. Common bug classes:
- trailing slash mismatch (`/permits/.../atlanta/` vs `/permits/.../atlanta`)
- http/https mismatch
- query-string leak

Quick spot-check:
```bash
for url in $(curl -sS https://permitgrab.com/sitemap-cities.xml | grep -oE 'https://permitgrab\.com/permits/[^/<]+/[^<]+' | head -5); do
  canonical=$(curl -sS "$url" | grep -oE '<link rel="canonical"[^>]*href="[^"]+"' | head -1)
  echo "$url → $canonical"
done
```

Each output line should show URL and canonical matching exactly.

### 6. Internal-link audit (V547d-2 deferred)

Crawl the sitemap, build the internal-link graph, identify Pass
pages with 0 inbound links from elsewhere on the site. Add
contextual links from homepage + blog index + nearest related
city. Out of scope for this turn; ship V547d-2 after fleet stabilizes.

### 7. 7-day GSC checkpoint

Schedule a Chrome MCP task for 2026-05-13: re-pull URL Inspection
for the 35 Pass cities. Acceptance:
- ≥20 of 35 should be "Indexed" by then
- 0 should be "Excluded by noindex" (V544 contract)
- Any "Discovered – currently not indexed" → investigate per-page

Document the check-in result in `V551_GSC_7DAY_CHECKPOINT.md`.

## What's done in this commit

- Documented the action checklist (this file)
- Provided exact URLs for URL Inspection requests
- Documented the sitemap state at the time of writing (35 Pass URLs)

## What's NOT done (Wes-driven, requires GSC UI / Chrome MCP)

- Items 1-3: GSC + Bing sitemap resubmission, URL Inspection requests
- Item 7: 7-day re-pull (schedule for 2026-05-13)

## Code-side follow-up (V547d-2, deferred)

- Internal-link audit script — orphan-page identification + auto-link
  injection from homepage + blog
- Canonical assertion test in tests/test_routes.py

— Captured 2026-05-06 ~14:25 UTC
