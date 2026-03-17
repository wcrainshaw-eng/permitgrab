# PermitGrab Site Review — 20 Improvements for SEO Readiness

**Reviewed:** March 16, 2026
**Site:** permitgrab.com
**Perspective:** What would a contractor experience on their first visit?

---

## CRITICAL (Broken — Fix Before SEO Hits)

### 1. /pricing is a 404
The "Pricing" link in the main nav points to `/#pricing` — but there's no pricing section on the homepage. And `/pricing` as a direct URL returns a raw, unstyled "Not Found" page. This is the single most important page for conversion. Every SEO landing page has a "See Pricing" button that goes nowhere.

**Fix:** Create a real `/pricing` route with a proper pricing page showing the $149/mo Professional plan, a free tier comparison, feature breakdown, and a Stripe checkout button.

### 2. Sign Up is a dead link
The orange "Sign Up" button in the nav bar points to `/#` — it does nothing. Same for "Log In." A contractor who's interested literally cannot create an account. `/signup` and `/register` both return 404.

**Fix:** Build actual `/signup` and `/login` routes with email+password registration, or at minimum redirect Sign Up to the Stripe checkout page so they can subscribe directly.

### 3. Log In is a dead link
Same issue — "Log In" points to `/#`. Returning users can't access their account.

**Fix:** Build a login page. If auth isn't ready yet, at minimum hide the Log In button so it doesn't look broken.

### 4. Get Alerts nav link is dead
"Get Alerts" in the nav points to `/#`. The sidebar has a "Get Permit Alerts" form with email/city/trade fields and a "Start Free Alerts" button, but the nav link itself goes nowhere.

**Fix:** Either create a `/get-alerts` landing page, or make the nav link scroll to the sidebar alert form.

---

## HIGH PRIORITY (UX Issues That Hurt Conversion)

### 5. No pagination — all 2,000 permits on one page
The dashboard loads all 2,000 permits in a single scrolling page with no pagination, no "load more," and no page breaks. This makes the page slow and overwhelming. A contractor doesn't want to scroll through 2,000 cards.

**Fix:** Add pagination (25-50 per page) with page numbers or infinite scroll with lazy loading.

### 6. Every single lead shows "100 pts" — no score differentiation
Every visible permit card shows the same orange "100 pts" lead score. If every lead is 100, the scoring system adds zero value. Contractors can't tell which leads are actually hot.

**Fix:** Implement real lead scoring based on recency, project value, trade match, permit stage, and contact info completeness. Scores should range from ~30 to 100 with visible variation.

### 7. Every permit shows "General Construction" trade badge
Scrolling through the dashboard, nearly every card has the green "General Construction" badge. The trade classification isn't differentiating — a plumbing permit should say Plumbing, an electrical permit should say Electrical, etc.

**Fix:** Improve the trade classification logic. Use keyword matching on the description field (e.g., "HVAC," "plumbing," "electrical," "roofing") to assign more specific trade badges. This is critical for the trade filter to be useful.

### 8. The 404 pages are raw/unstyled
When you hit a missing page, you get a plain white page with black text "Not Found" — no nav, no branding, no link back to the dashboard. This looks unprofessional and like the site is half-built.

**Fix:** Create a styled 404 page that matches the site design, includes the nav bar, and has a "Back to Dashboard" link.

### 9. No footer on the main dashboard
The city landing pages (/permits/austin) have a proper footer with Cities, Company links, and branding. But the main dashboard at `/` has no footer at all — it just ends with the last permit card.

**Fix:** Add the same footer to the dashboard and all app pages for consistency and SEO (internal links in footer help crawlers).

---

## MEDIUM PRIORITY (Polish for Professional Feel)

### 10. Hero stats are static/hardcoded
The hero says "2,000 Active Permits, 44 Metro Areas, $444M Total Project Value, 712 High-Value Leads." These look hardcoded. The "44 Metro Areas" is the cities in config, not cities with actual data. Only ~8-10 cities are confirmed working with real data.

**Fix:** Make stats dynamic — pull from actual data. Or at minimum, say "10+ Metro Areas" to be honest. Inflated numbers erode trust when a contractor filters by their city and sees nothing.

### 11. Cities dropdown lists 44 cities but most have no data
The Cities dropdown in the nav lists every city in the config (Albuquerque through Washington DC), but the sidebar shows only 8 cities with actual permit counts (NYC 611, LA 432, Chicago 352, Austin 165, Seattle 115, Denver 113, SF 110, Portland 102). A contractor clicking "Honolulu" or "Detroit" will likely see zero permits.

**Fix:** Only show cities in the dropdown that have actual permit data. Or add "(coming soon)" labels to cities without data so contractors aren't disappointed.

### 12. Contractor names look auto-generated
The contractors page shows names like "Michelle Ramirez," "Ana Wilson," "Sandra Lopez" — these look randomly generated rather than pulled from real permit data. If a contractor recognizes these aren't real, it kills credibility.

**Fix:** If contractor names are synthesized/demo data, either label it clearly as sample data or remove the Contractors page until you have real data. Alternatively, pull actual contractor/owner names from the permit records.

### 13. Phone numbers appear auto-generated
Every permit card has a green phone button with a number like "(723) 785-6152" or "(639) 509-4291." Real permit data doesn't typically include phone numbers — these look generated. A contractor who calls and gets a wrong number will never come back.

**Fix:** Only show phone numbers if they come from actual permit data or a verified enrichment source. If enrichment isn't ready, show "Contact info available" as a Pro feature teaser instead of fake numbers.

### 14. Two different nav bars between dashboard and landing pages
The main dashboard nav has: Dashboard, Cities, Contractors, Early Intel, Analytics, Pricing, Get Alerts, Log In, Sign Up. The city landing pages have: Cities, Pricing, Features, Browse All Permits. This inconsistency is confusing.

**Fix:** Unify the nav bar across all pages. The dashboard nav should be the primary one, with landing pages using the same nav.

### 15. "Upgrade to see contact info" CTA doesn't link anywhere
Some permit cards show blurred contact info with "Upgrade to see contact info for all leads →" but since there's no signup/pricing flow, this is a dead end.

**Fix:** Link this CTA to the pricing page (once it exists) or directly to Stripe checkout.

---

## SEO-SPECIFIC IMPROVEMENTS

### 16. No meta descriptions on the dashboard
The main dashboard page at `/` likely has basic or missing meta tags. For SEO, the homepage needs a compelling meta title and description optimized for "construction permit leads" and "building permit leads for contractors."

**Fix:** Add proper `<title>`, `<meta description>`, Open Graph tags, and Schema.org markup (WebApplication or Dataset schema) to every page.

### 17. No FAQ schema on landing pages
The city landing pages have good content but no FAQ section with FAQPage structured data. Adding 3-4 FAQs per city (e.g., "How many building permits are filed in Austin per month?") would help win featured snippets.

**Fix:** Add FAQ sections with FAQPage JSON-LD schema to each city landing page (the programmatic SEO spec already covers this).

### 18. No sitemap.xml or robots.txt
There's likely no dynamic sitemap or robots.txt configured, which means Google has to discover pages by crawling links. With 44+ city pages and the upcoming trade×city pages, a sitemap is essential.

**Fix:** Implement the dynamic sitemap.xml from the SEO spec. Also add robots.txt allowing all crawlers but blocking /admin, /api, /my-leads.

### 19. City landing pages lack "Other Cities" cross-links
The Austin landing page has great content but doesn't link to other city pages at the bottom (before the footer). Internal cross-linking between city pages distributes SEO authority.

**Fix:** Add a "Browse Permits in Other Cities" section above the footer on each city landing page, linking to all other active cities.

### 20. No blog or content marketing foundation
There's no `/blog` route, no educational content, no "how-to" articles. Blog content targeting long-tail keywords like "how to find construction leads" or "what is a building permit" would drive organic traffic and build authority.

**Fix:** Implement the blog framework from the programmatic SEO spec with the 3 starter posts.

---

## BONUS: Quick Wins

- **Cold start warning:** Free Render tier has ~50 second cold start after inactivity. First-time visitors may see a loading screen or timeout. Consider upgrading to paid Render ($7/mo) or adding a health check ping to keep it warm.
- **"Code Violations" widget says "No code violations found"** — if there's no data, hide the widget entirely rather than showing an empty state that implies you track violations but found nothing.
- **Export CSV button is available to all users** — if CSV export is meant to be a paid feature, gate it behind the paywall.
- **"My Leads" nav link exists** but the saved leads experience needs signup/login to work, which is broken.
- **The "View Permit History" link on cards** — verify this actually works and shows meaningful history.

---

## PRIORITY ORDER FOR CLAUDE CODE

Paste these into Code in this order:

1. **Fix broken routes first** — `/pricing`, `/signup`, `/login` (or redirect Sign Up to Stripe)
2. **Add pagination** to dashboard (50 per page)
3. **Fix trade classification** so permits show real trade badges
4. **Implement real lead scoring** (not all 100s)
5. **Style the 404 page**
6. **Add footer to dashboard**
7. **Dynamic hero stats** (or honest numbers)
8. **Filter cities dropdown** to only show cities with data
9. **Then deploy the SEO batch** (programmatic pages, sitemap, blog)
10. **Then deploy the remaining batches** in order
