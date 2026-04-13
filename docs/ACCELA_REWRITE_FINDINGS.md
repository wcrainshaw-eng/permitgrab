# Accela Collector Rewrite — Research Findings (2026-04-13)

## UPDATE: requests + BeautifulSoup WORKS

Initial testing suggested the portal required JavaScript. The actual issue was a
**CSRF check** — Accela rejects form POSTs without `Referer` and `Origin` headers.
Adding these headers makes the standard ViewState form submission work perfectly.

The new `accela_portal_collector.py` replaces the Playwright-based scraper with
~200 lines of requests + BeautifulSoup code. No headless browser needed.

### Tested Approaches (Dallas/DALLASTX portal):

| Approach | Result |
|----------|--------|
| GET search page → Parse HTML | ViewState/form fields visible, but NO permit data in HTML |
| POST form with ViewState | Redirects to Error.aspx (validation fails without JS) |
| GlobalSearchResults.aspx | Returns 200 but data is loaded via AJAX, not in HTML |
| Direct API (apis.accela.com) | 404 — requires OAuth2 registration |
| RSS feed endpoints | Return HTML, not RSS/XML |
| ACA internal API paths | 404 — no public proxy API |

### Why Playwright Is Still Needed

The ACA portal is an ASP.NET WebForms application that:
1. Renders the page frame server-side
2. Loads permit data via JavaScript/AJAX after page load
3. Uses `__doPostBack` with `WebForm_PostBackOptions` for form submission
4. Requires `__EVENTVALIDATION` that only exists after JS initialization
5. Paginates via JavaScript postback events

**No workaround exists** without executing JavaScript. The REST API (Option A) 
would bypass this entirely, but requires app registration at developer.accela.com.

## Current State

- **22+ Accela cities working** with existing Playwright scraper
- **Dallas TX confirmed working**: 325 raw → 300 normalized permits via test-and-backfill
- **84% failure rate** is from attempting too many portals, not from the scraper approach being wrong
- Many failures are from agencies with unusual modules, non-standard configurations, 
  or portals that require login

## Recommended Next Steps

### Short Term (No Rewrite Needed)
1. Focus on the 22+ working cities — ensure they stay healthy
2. Dallas is already onboarded and collecting
3. Don't attempt the 113 failing portals — they fail for structural reasons

### Medium Term (API Registration)
1. Register at developer.accela.com for REST API access
2. Build `accela_api_collector.py` using the Construct v4 API
3. This gives clean JSON, pagination, and reliable access
4. Start with Dallas (DALLASTX) as test agency
5. Gradually migrate working cities from portal scraping to API

### Not Recommended
- Rewriting the portal scraper with requests+BS4 (data not in HTML)
- Adding Selenium as an alternative to Playwright (same approach, worse library)
- Attempting to reverse-engineer the AJAX endpoints (they're session-bound)

## Agency Codes (Verified)

| City | Agency Code | Status | Permits |
|------|-------------|--------|---------|
| Dallas, TX | DALLASTX | Working | 300+ |
| Mesa, AZ | MESA | Working | 36K+ |
| San Antonio, TX | SANANTONIO | Working | 23K+ |
| Atlanta, GA | ATLANTA | Working | 3K+ |
| Cleveland, OH | CLEVELAND | Working | 4K+ |
| Baton Rouge, LA | BATONROUGE | Working | 5K+ |

See `ACCELA_CONFIGS` in `accela_scraper.py` for full list.
