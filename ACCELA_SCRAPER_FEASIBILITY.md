# Accela Scraper Feasibility Analysis
**Date:** March 28, 2026
**Cities:** Dallas, Detroit, Charlotte, Reno

---

## Executive Summary

**Verdict: Feasible. Worth building. Recommend 12-hour collection cycle.**

An Accela scraper would unlock 4 major US cities that have zero open data API coverage. The search results page already provides all the fields PermitGrab needs — no need to click into individual permit detail pages. A "Download results" CSV export button exists that could reduce scraping to 2-3 HTTP requests per city. Estimated total scrape time for all 4 cities: **2-5 minutes**.

---

## What I Found (Dallas Deep-Dive)

### The Portal
- **URL:** `aca-prod.accela.com/DALLASTX`
- **Platform:** ASP.NET WebForms (classic postback, NOT a REST API)
- **Public access:** Yes — search works without login under Building > Search Applications
- **Search form:** Date range + Record Type + address fields
- **Results:** Paginated HTML table, 10 records per page

### Data Volume (Dallas)
- **2 days of data (03/27-03/28) returned 100+ records**
- That's roughly **50-75 new permits per day** for Dallas
- With 6-hour collection, that's ~12-20 permits per scrape window

### Fields Available in Search Results (no detail page needed!)

| Field | PermitGrab Mapping | Available? |
|-------|-------------------|------------|
| Date | issued_date | Yes |
| Record Number | permit_number | Yes |
| Record Type | permit_type | Yes |
| Address | address (full with ZIP) | Yes |
| Description | description | Yes |
| Project Name | project_name | Yes |
| Expiration Date | expiration_date | Yes |
| Status | status | Yes |
| Short Notes | (bonus field) | Yes |

**This is everything PermitGrab needs from the list view alone.** No need to crawl into 50+ detail pages per scrape.

### Detail Page (bonus, not required)
- Adds: Work Location map, "More Details" expandable section
- Contractor name sometimes appears in the Project Name column on the list view
- Not worth the extra requests for V1 of the scraper

### The CSV Export (Game Changer)
- **"Download results" link exists** on the search results page
- Server-side ASP.NET control: `btnExport`
- If this exports all results matching the search (not just page 1), it reduces the entire scrape to:
  1. POST search form with date range → get results page
  2. POST export button → download CSV
  3. Parse CSV → done
- **This needs to be tested** — it might only export the current page (10 records) or it might export all 100+

---

## Technical Architecture

### How the Search Works
```
1. GET  CapHome.aspx?module=Building  → loads form with __VIEWSTATE (257 KB!)
2. POST CapHome.aspx                  → submit search with date params + ViewState
3. Response: full HTML page with results table
4. POST CapHome.aspx (page 2 click)   → ViewState postback for next page
```

### Key Challenge: ViewState
- Every request carries **257 KB of ViewState data** (ASP.NET encrypted state blob)
- This means you can't just POST arbitrary params — you need a valid ViewState from a prior GET
- Session cookies are required (ASP.NET_SessionId, etc.)
- **This rules out simple `requests` + BeautifulSoup for a reliable solution**

### Bot Detection
- **DataDog RUM** is loaded (browser monitoring)
- **WalkMe** is loaded (user analytics)
- No visible CAPTCHA or explicit bot blocking
- But aggressive scraping could trigger rate limits on Accela's infrastructure

---

## Scraping Approach Options

### Option A: Playwright (Headless Browser) — RECOMMENDED
```
Pros:
- Handles ViewState/cookies/JS automatically
- Can click the CSV export button natively
- Resilient to HTML changes
- Works identically for all 4 Accela cities

Cons:
- Requires Playwright + Chromium (~150 MB dependency)
- Slower than raw HTTP (real browser overhead)
- Needs headless Chromium on Render (possible, uses more RAM)

Time per city: ~30-60 seconds
```

### Option B: requests + ViewState Management
```
Pros:
- Lightweight, no browser dependency
- Faster per-request

Cons:
- Must extract __VIEWSTATE, __EVENTVALIDATION, __VIEWSTATEGENERATOR
- 257KB ViewState must be round-tripped correctly
- ASP.NET WebForms is notoriously fragile to scrape this way
- One server-side change breaks everything
- Hard to maintain across 4 different city configurations

Time per city: ~15-30 seconds IF it works (fragile)
```

### Option C: Hybrid (requests for search, Playwright only if needed)
```
Start with Option B, fall back to Option A on failure.
More complex but could be faster for the happy path.
Not recommended for V1 — unnecessary complexity.
```

**Recommendation: Option A (Playwright)**. The ViewState management in Option B is a maintenance nightmare. Playwright "just works" and the 30-60 second overhead per city is negligible on a 12-hour cycle.

---

## Timing & Frequency Analysis

### Per-City Scrape Time (Playwright approach)
| Step | Time |
|------|------|
| Launch browser, navigate to portal | ~5 sec |
| Load search form | ~3 sec |
| Set date range, submit search | ~5 sec |
| Wait for results | ~3-5 sec |
| Click CSV export (if all-results) | ~5-10 sec |
| OR paginate 5-8 pages | ~20-40 sec |
| Parse results | ~1 sec |
| **Total per city** | **~30-60 sec** |

### All 4 Cities
| Frequency | Total Time | Feasible? |
|-----------|-----------|-----------|
| Every 6 hours | ~2-4 min | Yes, but tight on Render free tier |
| Every 12 hours | ~2-4 min | Yes, comfortable |
| Every 24 hours | ~2-4 min | Yes, but stale data |

### Comparison to Current Collectors
- Socrata/ArcGIS/CKAN: **1-3 seconds per city** (single API call with JSON response)
- Accela scraper: **30-60 seconds per city** (10-20x slower)
- But still only 2-4 minutes total, which is fine on a 12-hour cycle

### Frequency Recommendation: **Every 12 hours**
- Dallas generates ~50-75 permits/day → ~25-37 permits per 12h window
- That's plenty fresh for contractor leads
- Gives breathing room for retries if Accela is slow
- Won't stress Accela's servers (they notice aggressive automated access)
- Could drop to 6h later if needed — the scrape itself is fast enough

---

## Implementation Plan

### New Code Needed

**1. `fetch_accela()` in collector.py** (~100-150 lines)
```python
async def fetch_accela(config, days_back=1):
    """
    Scrape permits from Accela Citizen Access portal.
    Uses Playwright headless browser to handle ASP.NET WebForms.

    Config needs:
      - agency_code: e.g., "DALLASTX", "DETROIT", "CHARLOTTE", "RENO"
      - module: e.g., "Building" (could be "Permits" for Detroit)
      - base_url: e.g., "aca-prod.accela.com"
    """
    # 1. Launch headless Chromium
    # 2. Navigate to search page
    # 3. Set date range (today - days_back)
    # 4. Submit search
    # 5. Try CSV export OR paginate and parse HTML
    # 6. Normalize to PermitGrab format
    # 7. Return list of permit dicts
```

**2. City configs in city_configs.py**
```python
"dallas": {
    "platform": "accela",
    "agency_code": "DALLASTX",
    "module": "Building",
    "field_map": {
        "permit_number": "Record Number",
        "permit_type": "Record Type",
        "address": "Address",
        "description": "Description",
        "issued_date": "Date",
        "status": "Status"
    }
}
```

**3. Dependencies**
- `playwright` (pip install playwright)
- `playwright install chromium` (one-time setup on Render)
- These add ~150MB to the deploy

### Confirmed Agency Codes
| City | Agency Code | URL | Module |
|------|------------|-----|--------|
| Dallas | DALLASTX | aca-prod.accela.com/DALLASTX | Building |
| Detroit | DETROIT | aca-prod.accela.com/DETROIT | Permits |
| Charlotte | CHARLOTTE | aca-prod.accela.com/CHARLOTTE | Building (needs verification) |
| Reno | RENO | aca-prod.accela.com/RENO | Building (needs verification) |

---

## Risks & Mitigations

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| Accela blocks automated access | Medium | Use realistic User-Agent, add random delays (2-5s between actions), run from consistent IP |
| ViewState/form changes break scraper | Low | Playwright handles this natively; only breaks if they redesign the whole portal |
| CSV export only exports current page | Medium | Fall back to pagination (adds ~30 sec per city) |
| Render can't run headless Chromium | Low | Render supports it; may need to bump to paid tier for RAM |
| Rate limiting | Medium | 12h frequency + delays between cities keeps volume very low |
| Different Accela configs per city | Certain | Each city has slightly different module names/form fields; need per-city testing |

---

## Estimated Development Effort

| Task | Time |
|------|------|
| Build `fetch_accela()` with Playwright | 4-6 hours |
| Test & configure all 4 cities | 2-3 hours |
| Add Playwright to Render deploy | 1 hour |
| Integration with existing collector loop | 1-2 hours |
| Error handling, retries, logging | 1-2 hours |
| **Total** | **~1.5-2 days** |

---

## Bottom Line

Building an Accela scraper is **absolutely worth it**. You're looking at ~2 days of dev work to unlock 4 major US cities (Dallas #9, Detroit #27, Charlotte #16, Reno #86 by population) that currently have zero permit data coverage. The scraper adds ~2-4 minutes to the collection cycle running every 12 hours, which is nothing. Playwright makes the ASP.NET ViewState nightmare a non-issue, and the "Download results" CSV export could make it even faster.

The main decision point is whether to invest the time now (while Code is grinding through the 1,898 city audit) or queue it for after the audit is complete.
