# V547d — GSC Indexation Relaunch (Pillar 4)

**Computed:** 2026-05-06 ~13:30 UTC
**Trigger:** V544 silent breakage repaired by V546c — sitemap-cities.xml
now actually returns the 35 (≈ Wes's 38) Pass cities, not the full 1,713
fleet. Google's 2-7 day re-evaluation cycle starts NOW.

## Sitemap state (verified 13:30 UTC)

`https://permitgrab.com/sitemap.xml` is the index, with 5 sub-sitemaps:
- sitemap-pages.xml
- sitemap-cities.xml ← now 35 city URLs (was 1,713 pre-V546c)
- sitemap-states.xml (45)
- sitemap-trades.xml
- sitemap-blog.xml

## The 35 Pass cities currently in sitemap-cities.xml (sample 10)

| URL |
|-----|
| /permits/new-york-state/new-york-city |
| /permits/illinois/chicago-il |
| /permits/louisiana/new-orleans |
| /permits/ohio/cincinnati-oh |
| /permits/massachusetts/cambridge-ma |
| /permits/district-of-columbia/washington-dc |
| /permits/tennessee/nashville |
| /permits/florida/miami-dade-county |
| /permits/north-carolina/raleigh |
| /permits/california/sacramento-ca |

(Full list of 35 derivable from the live sitemap; this is the V546-era
Pass set, expected to grow to 50-80+ as enrichment imports land.)

## Action checklist (Wes-driven, GSC API not available from Code)

| # | Action | Owner | Status |
|---|--------|-------|--------|
| 1 | Resubmit sitemap.xml to Google Search Console (Sitemaps API or Chrome MCP) | Wes | Pending |
| 2 | Resubmit to Bing Webmaster Tools | Wes | Pending |
| 3 | URL Inspection request via GSC API for each of the 35 Pass cities — explicit "recrawl this" signal | Wes via Chrome MCP | Pending |
| 4 | Reconcile any conditional noindex emissions against city_health.is_sellable_city — Pass cities should NEVER emit noindex | Code (V544 already did this; verify) | Likely done |
| 5 | Audit `<link rel="canonical">` on the 35 Pass city pages — must match actual URL precisely (no trailing slash, no http/https mismatch, no query-string leak) | Code | Pending |
| 6 | Internal linking — count inbound links per Pass page, fill 0-inbound pages from homepage + blog index | Code (V547d-2) | Pending |
| 7 | Schedule a 7-day GSC URL Inspection re-pull for the 35 Pass cities | Wes via cron / Chrome MCP | Pending |

## V544 reconciliation — items 4 + 5 above

**Item 4 (noindex emissions):** V544 already unified the 3 gates onto
`city_health.is_sellable_city`. Need to verify no other noindex paths
exist for Pass cities. The conditional emissions Wes mentioned
("8 cases" from V542 partial) live in:
- `templates/city_landing.html` — uses `{{ robots_directive }}` from
  context, set by `routes/city_pages.py:state_city_landing` (V544
  unified)
- `templates/city_paused.html` — emits `noindex, follow` always
  (correct: paused cities shouldn't be indexed)
- `server.py:6886` city_trade_landing — V544 unified
- All other templates use static `<meta robots="...">` based on page
  type (login/dashboard = noindex by design)

The remaining audit task is making sure the dynamic templates don't
leak noindex to Pass cities. V544 PR3 `is_sellable_city()` is now
the gate (post-V546c fix). Once Render's next /api/admin/health probe
shows 0 daemons on web (V547a) and stable cycle from worker, the gate
is fully wired.

**Item 5 (canonical audit):** Need to spot-check 5 of the 35 Pass
cities — fetch each page, parse `<link rel="canonical">`, compare to
actual URL. Deferred to a follow-up V547d-canonical pass when the
worker is stable.

## Schedule

- **T+0 (now):** sitemap-cities.xml is live with 35 URLs.
- **T+1d (2026-05-07):** Google's first scheduled crawl after sitemap
  fetch should re-evaluate at least a few of the 35.
- **T+2-7d (by 2026-05-13):** majority of the 35 should be indexed
  if they have substantial content + no other technical blockers.
- **T+7d:** Wes pulls GSC URL Inspection for all 35 — checkpoint.

## V547d-2 (deferred internal-linking work)

Crawl https://permitgrab.com/sitemap.xml, build a graph of
internal links between Pass city pages + homepage + blog. Identify
Pass pages with 0 inbound links from elsewhere on the site. Add
contextual links from homepage + blog index + nearest related city.
Ship as own commit with regression tests after Pillar 1+2 are
fully stable. Not blocking for V547 first-day milestones.

— Computed 2026-05-06 ~13:30 UTC
