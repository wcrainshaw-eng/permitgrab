# MASTER UAT — PermitGrab Acceptance Anchor

**This is THE UAT doc.** Every prior UAT artifact in the repo is consolidated here.
When new findings come in, append to the "Open Findings" log and update the relevant
checklist. Do not start a fresh UAT_*.md file. This is the anchor.

**Last consolidated:** 2026-05-03 from `UAT_VISUAL_REPORT_2026-04-27.md`,
`CODE_V422_UAT_BUGS.txt`, `CODE_V440_UAT_BUGS.txt`, `CODE_V488_UAT_FIXES.txt`,
`docs/V171_UAT_FINDINGS.md`, `tests/test_visual_uat.py`, `tests/conversion-uat.js`,
`tests/visual-uat.js`, `tests/visual_uat_live.py`, plus 14 historical UAT branches
in git refs (v223 → v488).

**Maintainer rule:** Every PR that touches templates, /pricing, /signup, /start-checkout,
/get-alerts, the digest, the webhook handler, or any of the routes in the
"Pre-Deploy Gates" section MUST tick the relevant checkbox in this doc and
record the verification artifact (screenshot path, curl response, or test ID).

---

## TABLE OF CONTENTS

1. [Pre-Deploy Gates (must pass before every deploy)](#pre-deploy-gates)
2. [Persona Checklist — Logged-Out](#persona-logged-out)
3. [Persona Checklist — Free / Digest-Only](#persona-free)
4. [Persona Checklist — Pro Paid](#persona-pro)
5. [Flow Checklist (signup, paid signup, alerts, export, digest)](#flow-checklist)
6. [Page-by-Page Checklist](#page-by-page)
7. [Branding Consistency Checklist](#branding)
8. [Mobile Checklist](#mobile)
9. [SEO + Indexing Checklist](#seo-indexing)
10. [Data Quality Gates](#data-quality)
11. [Open Findings (current)](#open-findings)
12. [Closed Findings (historical, do-not-regress)](#closed-findings)
13. [Recurring Regressions (the ones that keep coming back)](#recurring-regressions)
14. [How to Run a Full UAT](#how-to-run)

---

<a name="pre-deploy-gates"></a>
## PRE-DEPLOY GATES

These run before every Render deploy. Failures here are blockers — do not ship.

- [ ] **Health endpoint returns healthy.** `GET /api/admin/health` →
      `daemon_running: true`, `status: healthy`. Self-heal triggered = warning.
- [ ] **Digest scheduler thread alive.** `GET /api/admin/digest/status` →
      `daemon_alive: true`, `thread_alive: true`. (V475 regression watchpoint.)
- [ ] **Pricing button → city capture → Stripe.** Sign up an incognito test
      account, click "Start Free Trial". Must land on `/select-cities`, not
      Stripe. (V494 watchpoint — see Flow Checklist for full path.)
- [ ] **Webhook handler writes subscribers row.** After test Stripe checkout
      completes (use Stripe test card 4242 4242 4242 4242), confirm a row
      exists in `subscribers` with `digest_cities != '[]'` and `active = 1`.
- [ ] **AdsBot fetches city pages in <2 s.** `time curl -s
      https://permitgrab.com/permits/<state>/<city> -o /dev/null` ≤ 2 s
      for buffalo-ny, cleveland-oh, miami-dade-county, phoenix-az.
      (V492 watchpoint — see "Destination not working" history.)
- [ ] **`/permits/<slug>` 301-redirects to `/permits/<state>/<slug>`.**
      Manual canonical check for chicago-il, miami-dade-county, phoenix-az.
      (V485 B1 watchpoint — without this, sitemap publishes both forms and
      Google indexes neither.)
- [ ] **Visual UAT screenshot run passes.** `npm run test:uat` (or the
      Playwright skill) — screenshots match baseline within 5% for the 8
      anchor pages. (See `tests/visual-uat.js` + `tests/visual_uat_live.py`.)
- [ ] **No 500s in last 1 h scraper_runs.**
      `SELECT COUNT(*) FROM scraper_runs WHERE status='error' AND
       run_started_at > datetime('now','-1 hour')` returns 0.
- [ ] **Phone counts haven't regressed on ad-ready cities.**
      Snapshot Buffalo, Cleveland, Miami-Dade, Phoenix, NYC, Henderson,
      San Antonio. None should drop more than 5% deploy-over-deploy.
- [ ] **Sitemap publishes the canonical URL only.** Spot-check
      `https://permitgrab.com/sitemap.xml | grep miami-dade-county` —
      should be exactly one entry, the `/permits/florida/...` form.

---

<a name="persona-logged-out"></a>
## PERSONA — LOGGED-OUT VISITOR

Tests run from incognito / private window with no cookies.

### Navigation
- [ ] Homepage loads in < 3 s above the fold
- [ ] Hero stat bar shows real numbers (not `--` or `0`)
- [ ] How It Works section renders 3 steps with icons
- [ ] City grid renders, every linked city resolves (no 404 on click)
- [ ] Nav: Home, Cities▾, Contractors, Blog, Pricing, Get Alerts, Log In, Sign Up
- [ ] Cities dropdown opens on click, lists ad-ready cities first

### Content
- [ ] City page → unified data table renders rows
- [ ] City page → pagination + filter dropdown work
- [ ] City page → phone numbers shown as masked / "Sign up to reveal"
- [ ] Contractors page → leaderboard sorts by last_active by default
      *(historical regression — see Closed Findings m3)*
- [ ] Blog index → posts visible, click-through works
- [ ] Pricing page → renders dedicated `/pricing` (not redirect to home)
      *(historical regression — see Closed Findings m1)*

### Footer
- [ ] TOP CITIES list = ad-ready cities first
      *(historical regression — see CODE_V422_UAT_BUGS Phase 3)*
- [ ] All footer links resolve (no 404)
- [ ] © year is current

### Conversion buttons
- [ ] "Sign Up" → /signup
- [ ] "Start Free Trial" → /select-cities (V494) → /start-checkout → Stripe
- [ ] "Get Alerts" → /get-alerts (free digest funnel)

---

<a name="persona-free"></a>
## PERSONA — FREE / DIGEST-ONLY

Logged in with a free account that signed up via /get-alerts (no card).

- [ ] Nav shows "Get Alerts" link with checkmark or active state
- [ ] City pages still show masked phones with "Upgrade to Pro" CTA
- [ ] Analytics page is gated or shows free-tier preview
- [ ] CSV export prompts upgrade (don't allow export)
- [ ] /my-leads either gated OR shows empty state explaining Pro feature
- [ ] Email digest fires daily at 11:00 ET if user is in `subscribers` table
      with `active = 1` and `digest_cities != '[]'`

---

<a name="persona-pro"></a>
## PERSONA — PRO PAID

Logged in with a Pro subscription (test via Stripe test card 4242).

### Core paid value
- [ ] All phone numbers visible (no masking) on city pages
- [ ] Click any permit row → expanded detail shows:
  - [ ] Full permit details (untruncated type, description, value, date)
  - [ ] Contractor name + phone
  - [ ] Trade category
  - [ ] "Save as Lead" button
  - [ ] Link to contractor's full profile
  *(C2 from UAT_VISUAL_REPORT_2026-04-27 — biggest paid-value gap)*
- [ ] CSV export works, downloads file with phone column populated
- [ ] /my-leads renders saved leads, status filter works
- [ ] /analytics defaults to a city with data (not blank "All Cities")
      *(M1 historical regression)*
- [ ] /intel dashboard renders, phone % capped at 100%
      *(M3 historical regression — Miami-Dade was showing 127%)*

### Upgrade pathway
- [ ] No "Upgrade" prompts shown to existing Pro users
- [ ] Account page shows subscription status, next billing date
- [ ] Cancel link in account → Stripe billing portal

---

<a name="flow-checklist"></a>
## FLOW CHECKLIST

### Free digest signup (`/get-alerts`)
1. [ ] Land on /get-alerts (logged-out)
2. [ ] Form has email, name, city dropdown, trade dropdown, optional zip
3. [ ] City dropdown lists ≥ 200 cities, capped (not 600+ which used to OOM)
      *(V309 watchpoint)*
4. [ ] Submit → 201 response from /api/subscribe
5. [ ] `subscribers` row created with `digest_cities` populated
6. [ ] Welcome email sent (if SMTP configured)
7. [ ] User can log in immediately (no password set, sent via magic link)

### Paid signup — POST-V494 (the way it should work)
1. [ ] /pricing → click "Start Free Trial"
2. [ ] Logged-out: redirect to /signup?next=/select-cities?plan=pro
3. [ ] /signup → email + password → submit
4. [ ] Land on **/select-cities** (NEW V494 step) — not Stripe
5. [ ] Form has multi-select city dropdown grouped by state
6. [ ] Form rejects empty submission (must pick ≥ 1 city)
7. [ ] Submit → writes `subscribers` row with `active = 0`, plan = pro,
       digest_cities = [selected]
8. [ ] Stash session.pending_cities, redirect to /start-checkout
9. [ ] /start-checkout reads session OR existing subscribers row, has
       cities, creates Stripe Checkout Session
10. [ ] Stripe Checkout — pay with test card
11. [ ] Webhook fires checkout.session.completed
12. [ ] Webhook flips subscribers.active = 0 → 1, sets plan = pro
13. [ ] Redirect to /success then /dashboard
14. [ ] First digest fires next morning at 11:00 ET with the chosen cities

### Paid signup — PRE-V494 (current broken state — Higgins/Meyer hit this)
- ❌ /pricing → "Start Free Trial" → /start-checkout → Stripe
- ❌ Webhook updates users.plan = pro but DOES NOT write subscribers row
- ❌ User never sees /select-cities or /get-alerts
- ❌ `digest_cities` is NULL → digest scheduler skips them
- ❌ Customer pays $149/mo and receives nothing

This is the V494 P0 fix. Once V494 ships, walk this flow end-to-end with a
test card BEFORE marking the deploy green.

### Email digest (daily 11:00 ET)
1. [ ] worker.email_scheduler thread alive (`/api/admin/digest/status`)
2. [ ] At 11:00 ET, scheduler queries `subscribers WHERE active=1`
3. [ ] For each, queries permits filed in last 24 h matching digest_cities
4. [ ] Renders templates/emails/digest.html with data
5. [ ] Sends via SMTP
6. [ ] Writes scraper_runs (or digest_log) row with status='sent', sent_count
7. [ ] Failed sends logged with error_message and retried next day
- *(V475 watchpoint — start_collectors() must spawn email_scheduler thread)*

### CSV export (Pro only)
1. [ ] /permits/<state>/<city> → "Export" button visible (Pro only)
2. [ ] Click → POST /api/export/<city>
3. [ ] Returns CSV with columns: address, permit_type, contractor_name,
      phone, trade_category, filing_date
4. [ ] Phone column populated for rows where contractor_profiles.phone exists
5. [ ] File downloads with filename `permits_{city}_{date}.csv`
6. [ ] Free user clicking export → upgrade prompt (do not return CSV)
- *(V313 watchpoint)*

### Login + magic link
1. [ ] /login → email + password → submit
2. [ ] Wrong password → error message, no enumeration leak
3. [ ] Magic link button → email sent with one-time link
4. [ ] Magic link click → logs user in, redirects to /dashboard
5. [ ] Session persists across page reloads
6. [ ] Logout link clears session

---

<a name="page-by-page"></a>
## PAGE-BY-PAGE CHECKLIST

Anchor pages — must pass on every deploy.

### / (Homepage)
- [ ] Hero stat bar real numbers
- [ ] No JS console errors *(V247/V422 P0 — renderPermits null guard)*
- [ ] Footer top cities = ad-ready first

### /pricing
- [ ] Renders dedicated page, not homepage redirect *(m1 regression)*
- [ ] 3 plan cards: Starter / Pro / Enterprise (or current set)
- [ ] CTA buttons → /select-cities?plan=X (V494)
- [ ] FAQ section text matches actual product (no references to dead plans)

### /signup
- [ ] 14-day Pro trial banner
- [ ] Email + password + name fields
- [ ] TOS + Privacy links work
- [ ] If logged-in user lands here → redirect to /dashboard *(V375 fix)*

### /select-cities (V494, new)
- [ ] Renders only after /signup
- [ ] Multi-select city list, ad-ready first
- [ ] Per-plan city limits enforced (Starter: 1, Pro: 5, Enterprise: unlimited)
- [ ] Form submission writes pending subscribers row
- [ ] Redirect to /start-checkout?plan=X

### /start-checkout
- [ ] Logged-out → /signup
- [ ] Logged-in already-paid → /dashboard?already_subscribed=1
- [ ] Logged-in free without cities in session → /select-cities (V494 gate)
- [ ] Logged-in free with cities → Stripe Checkout

### /get-alerts (free digest)
- [ ] Form caps city dropdown at 200 *(V309 watchpoint)*
- [ ] Submit → 201 from /api/subscribe
- [ ] Email format validation rejects junk *(V218 T5B watchpoint)*

### /permits/<state>/<city>
- [ ] Loads in < 2 s *(V492 watchpoint — AdsBot timeout)*
- [ ] H1 contains "<City> Building Permits" + state
- [ ] Canonical = `/permits/<state>/<city>` *(V485 B1)*
- [ ] Unified records table has rows
- [ ] Owner section renders if property_owners has data
- [ ] Violations section renders if violations has data
- [ ] FAQ JSON-LD present *(V474 Section B+C)*
- [ ] No 502s (V493 daemon dependency)

### /permits/<slug> (legacy 1-segment)
- [ ] 301-redirects to canonical 2-segment form *(V485 B1)*

### /cities
- [ ] Hero text readable (not white-on-white) *(C1 — historical regression)*
- [ ] City cards link to canonical city pages

### /contractors
- [ ] Leaderboard sorted by last_active descending *(m3)*
- [ ] Total Value column hides "Not listed on permit" rows OR shows them gracefully

### /analytics (Pro)
- [ ] Defaults to user.preferred_city, not blank *(M1 — historical)*
- [ ] Charts render with no duplicate Y-axis labels *(m2)*

### /intel (Pro)
- [ ] Phone % capped at 100% *(M3 — historical)*
- [ ] All ad-ready cities listed with current data

### /my-leads (Pro)
- [ ] Empty state OR test-data state both render gracefully *(m4)*
- [ ] No bare "," when contractor_name is null *(m4)*

### /account (logged-in)
- [ ] Profile fields editable
- [ ] Subscription status visible (Active/Cancelled/Past Due)
- [ ] Alert preferences editable (cities, trades, frequency)

### /blog/<slug>
- [ ] All city × persona posts render (no 404s)
      *(V486 P1 historical — 10 posts returned 404 because the dispatcher
      branch was lost)*

### /sitemap.xml
- [ ] Publishes ONLY canonical 2-segment city URLs *(V485 B1)*
- [ ] All blog posts present
- [ ] All persona pages present

### /robots.txt
- [ ] Allows AdsBot-Google (full crawl)
- [ ] Disallows /api/, /admin/, /success, /start-checkout

---

<a name="branding"></a>
## BRANDING CONSISTENCY

| Element | Pass criteria |
|---------|---------------|
| Logo | "PermitGrab" wordmark, same treatment every page |
| Primary color | #4F46E5 (or current brand blue) — buttons, links, hero |
| Background | White (#FFFFFF) — not light gray |
| Text body | Dark slate, ≥ 4.5:1 contrast on white |
| Nav (logged-out) | Home / Cities▾ / Contractors / Blog / Pricing / Get Alerts / Log In / Sign Up |
| Nav (logged-in) | Home / Cities▾ / Contractors / Analytics / Pricing / Get Alerts / [user]▾ |
| Footer | TOP CITIES / COMPANY / RESOURCES columns, © 2026 |
| Button primary | Blue filled, white text |
| Button secondary | White filled, blue border |
| Heading font | Same family across pages |

---

<a name="mobile"></a>
## MOBILE CHECKLIST (≤ 380px viewport)

- [ ] No horizontal scroll on any page
- [ ] Hamburger menu works, opens drawer
- [ ] All CTAs reachable (sticky bottom or in-line)
- [ ] Tables responsive — horizontal scroll within container, not page
- [ ] Pricing cards stack vertically, no overflow
- [ ] Hero text readable (font-size ≥ 16px body, ≥ 24px H1)
- [ ] Touch targets ≥ 44px tall

---

<a name="seo-indexing"></a>
## SEO + INDEXING

- [ ] Each city page has H1 = "<City> Building Permits & Contractor Leads"
- [ ] Each city page meta_description includes real numbers (permit count,
      contractor count) — not generic boilerplate
- [ ] Every page has exactly one canonical URL
- [ ] Each city page has LocalBusiness JSON-LD
- [ ] Each city page has FAQ JSON-LD with ≥ 5 questions *(V474 B+C)*
- [ ] Sitemap submits to Google Search Console
- [ ] No `<meta name="robots" content="noindex">` on pages we want indexed
- [ ] All anchor pages return HTTP 200 to AdsBot user-agent

**Current SEO state (CLAUDE.md North Star):** 2 of 3,115 pages indexed. The
canonical-conflict (V485 B1) was the root cause; expect indexing to climb
after V485 + V493 ship and Google recrawls.

---

<a name="data-quality"></a>
## DATA QUALITY GATES

Run these queries before marking a deploy clean.

### Garbage profiles
```sql
SELECT source_city_key, COUNT(*) FROM contractor_profiles
WHERE business_name ~ '^[0-9]+$'
   OR LENGTH(business_name) < 3
   OR business_name IN ('N/A','NA','NONE','TEST','OWNER','SELF')
GROUP BY source_city_key
HAVING COUNT(*) > 10
ORDER BY COUNT(*) DESC;
```
- [ ] Zero rows = pass

### Stale ad-ready cities
```sql
SELECT source_city_key, MAX(date) AS newest
FROM permits
WHERE source_city_key IN ('chicago-il','new-york-city','phoenix-az',
  'san-antonio-tx','miami-dade-county','buffalo-ny','cleveland-oh',
  'henderson','nashville')
GROUP BY source_city_key;
```
- [ ] All cities `newest >= date('now','-7 days')`

### Phone count regression
- [ ] Snapshot before deploy
- [ ] Re-query after deploy
- [ ] No city loses > 5% phones

### Subscribers vs paid users mismatch *(V494 watchpoint)*
```sql
-- Subscribers with active digest
SELECT COUNT(*) FROM subscribers WHERE active = 1
  AND digest_cities IS NOT NULL AND digest_cities != '[]';

-- Compare against Stripe Dashboard's count of active subscriptions.
-- Mismatch = the V494 bug is recurring. Investigate stripe_webhook_events
-- vs subscribers table immediately.
```

---

<a name="open-findings"></a>
## OPEN FINDINGS

### O1 — V494 trial signup skips city capture (P0 FOUNDATIONAL bug)
- **Found:** 2026-05-03
- **Source:** The signup → checkout → digest pipeline has never
  worked end-to-end for any organic user since launch. Stripe has
  3 customers (Gomes, Meyer, Higgins). All 3 entered card info, all
  3 hit this bug. Gomes was hand-rescued by Wes manually inserting
  his subscribers row with a guessed city. Meyer (trial ends ~May 15)
  and Higgins (trial ends ~May 16) are still broken — they will
  cancel before being charged unless their cities get captured and
  the digest reaches them.
- **Impact:** Every paid customer the platform has ever had was
  hand-rescued by Wes. The funnel was never wired to the subscribers
  table. With organic signups starting to come in (3 in 5 days),
  this stops being theoretical and becomes the dominant retention
  blocker.
- **Fix:** `CODE_V494_PAID_FLOW_NO_CITY_CAPTURE.txt` — gate /start-checkout
  on /select-cities; webhook activates pending subscribers row.
- **Status:** Instruction file written, awaiting push + deploy.
- **Recovery (URGENT — 14-day trial clock is ticking):**
  - Email Higgins + Meyer TONIGHT via `POST /api/forgot-password`
  - Ask which cities they want
  - Backfill subscribers row so tomorrow's 11:00 ET digest reaches them
  - At least 12-13 days of digest delivery before Stripe charges
    them — buys time to retain

### O2 — Daemon outage (P0)
- **Found:** 2026-05-03
- **Source:** /api/admin/health → daemon_running: false; digest
  scheduler thread_alive: false; no collection in 24+ hr.
  POST /api/admin/start-collectors returns "noop".
- **Impact:** Production data is stale; email digest dead; ads risk
  Destination Not Working disapproval cascade.
- **Fix:** `CODE_V493_DAEMON_OUTAGE_FIX.txt` — remove the V481
  WORKER_MODE no-op gates that point at a worker service that was
  never deployed.
- **Status:** Instruction file written, awaiting push + deploy.

### O3 — Buffalo "Destination not working" (P1) — CLOSED 2026-05-04
- **Found:** 2026-05-03 after Buffalo ad save in Google Ads UI
- **Source:** Google Ads policy finding on /permits/buffalo-ny.
  Same root cause as V485 B2 (which fixed it for persona pages):
  multi-COUNT aggregate queries time out under AdsBot crawl.
- **Fix shipped:** V492 (commit `4cd4571`) — three-layer city-stats
  cache (process-mem → system_state → live compute fallback) in
  `routes/city_stats_cache.py`, wired into `state_city_landing()`.
  Cache-Control header `public, max-age=300, s-maxage=600,
  stale-if-error=86400` so AdsBot's re-crawl hits the CDN edge
  cache. End-of-cycle refresh in scheduled_collection + startup
  warm (limit=20) keeps the system_state row pre-populated.
- **Verification 2026-05-04 15:32 UTC:**
  - 39 city_stats:* rows in system_state (target: ≥8)
  - Buffalo cold-render 3.12s, warm rerender 0.94s (was 30s+)
  - 8 priority cities all 200 OK in 1.24-2.22s
  - Cache hit on un-warmed city (fairfax-va, mill-valley-ca)
    auto-populates system_state row via the live-compute path
- **Watchpoint:** Pre-Deploy Gates → after any city_pages.py
  change, time the slowest priority city: `time curl -sko/dev/null
  https://permitgrab.com/permits/<state>/<slug>` should be <5s.
  If >10s, AdsBot will fail it.

### O4 — 7 ad groups still at zero impressions
- **Found:** 2026-05-03
- **Source:** Google Ads UI — Buffalo + Cleveland refreshed and saved
  but Henderson, San Jose, LA, Nashville, Phoenix, San Antonio, and
  Buyer Intent National all still have generic copy and Poor strength.
  Henderson also has a wrong Final URL (set to /permits/miami-dade-county).
- **Fix:** `AD_COPY_REFRESH_2026-05-03.md` — full headline + URL specs.
  Apply via Google Ads Editor desktop app (avoids the 2FA wall that
  blocked the web UI flow).
- **Status:** Spec written.

### O5 — FL DBPR column position misalignment (P0 enrichment blocker)
- **Found:** Pre-V244d, ongoing
- **Source:** CLAUDE.md — STATE_CONFIGS['FL'] applicant_columns and
  licensee_columns don't match actual myfloridalicense.com CSV layout.
  Import runs but matches 0 records → 6+ FL cities can't get phones.
- **Fix:** SSH into Render, head -3 each CSV, count actual columns,
  update STATE_CONFIGS['FL'].
- **Status:** Open. Highest leverage hidden fix in the system.

### O6 — Phone % over 100% on Intel dashboard
- **Found:** 2026-04-27 (UAT_VISUAL_REPORT M3)
- **Source:** Miami-Dade 127%, LA 145% on /intel.
- **Fix:** Cap at 100% in template OR fix the calculation (likely
  cross-city duplicates in numerator).
- **Status:** Open.

### O7 — Click-to-expand on city pages shows only address (P1)
- **Found:** 2026-04-27 (UAT_VISUAL_REPORT C2)
- **Source:** Pro user clicks a permit row, expanded section shows
  only `📍 [ADDRESS]` — no phone, no contractor, no Save Lead.
- **Impact:** Biggest paid-value gap. Pro users pay $149/mo expecting
  contractor phone numbers on each row.
- **Fix:** Extend the row-click AJAX to include contractor profile
  fetch; render full detail card.
- **Status:** Open.

### O8 — Cities hero text invisible (low)
- **Found:** 2026-04-27 (UAT_VISUAL_REPORT C1)
- **Source:** /cities — white text on white background, "Browse 447+
  Cities" heading unreadable.
- **Fix:** Add blue background OR change text color (5-min CSS).
- **Status:** Open.

### O9 — Analytics empty on default "All Cities" (M1)
- **Status:** Open.

### O10 — Dashboard nav goes to homepage (M2)
- **Status:** Open. Either build a real /dashboard or rename nav link
  to "Home".

### O11 — gtag `purchase` event never fires (P1 — kills attribution)
- **Found:** 2026-05-03 via GA4 Traffic Acquisition for May 1-2
- **Source:** Total Revenue = $0.00 across all channels in GA4
  Traffic Acquisition report. Stripe processed $298 (Higgins +
  Meyer × $149). GA4 saw 1 Key Event total for the 2-day window
  matching 2 Stripe purchases.
- **Impact:**
  - We can't tell which channel converted Higgins vs Meyer
    (worse: every future paid signup has the same blind spot)
  - Google Ads bid optimization is starved of conversion signal
    — Smart Bidding can't optimize without revenue events
  - GA4 attribution reports show 0 revenue forever
- **Root cause likely:** `templates/partials/analytics.html` only
  fires gtag('config', ...) and ad-side conversions but doesn't
  fire `purchase` event on `/success` page render. The Stripe
  webhook fires server-side AFTER redirect, and the gtag needs to
  fire client-side ON the success page.
- **Fix:** Add to /success template:
    `gtag('event', 'purchase', {transaction_id: '{{ session_id }}',
     value: 149.00, currency: 'USD', items: [{item_name: 'pro_monthly'}]});`
  - Pull session_id from query params (`?session_id={CHECKOUT_SESSION_ID}`)
  - Re-check the success_url config in /start-checkout — it
    already passes session_id (server.py uses
    `success_url=f'{SITE_URL}/success?session_id={CHECKOUT_SESSION_ID}'`)
- **Status:** Open. Add to next CODE_V### file.
- **Watchpoint:** Pre-Deploy Gates → GA4 Total Revenue should be
  non-zero within 48 hr of any paid signup.

### O12 — Paid Search engagement 14s (CPC traffic bounces)
- **Found:** 2026-05-03 via GA4 Traffic Acquisition for May 1-2
- **Source:** google / cpc — 18 sessions, 14s avg engagement,
  104 events but 0 Key Events. Compare: google / organic at 3m 06s
  avg engagement, 1 Key Event from 5 sessions.
- **Impact:** $26+ in May 1-2 ad spend (CPC × clicks) generated
  ZERO tracked conversions. Even accounting for the gtag purchase
  bug, the 14s engagement is genuinely bounce-tier — most CPC
  visitors aren't even reading the landing page.
- **Root cause:** Paired with O3 (Buffalo "Destination not
  working"), O4 (Henderson URL pointing at miami-dade-county),
  and the broader landing-page-quality issues from O7 (row click
  expansion empty). When AdsBot or a real visitor lands on a
  city page that's slow OR points at the wrong city OR has empty
  detail expansions, they leave fast.
- **Fix:** Ship V492 (city-stats cache), V494 (paid signup flow),
  and AD_COPY_REFRESH (Henderson URL fix). Then re-check this
  number — if engagement < 30s on cpc after those land, the
  problem is the landing page itself, not the ads.
- **Status:** Open. Recheck after V492+V494 deploy.
- **Watchpoint:** Pre-Deploy Gates → google / cpc avg engagement
  ≥ 30s on the most recent 7-day window.

### O14 — Manual digest trigger + password reset CONFIRMED WORKING (status note)
- **Verified:** 2026-05-03 by Cowork
- **Digest:** `POST /api/admin/digest/trigger {"email":"<addr>"}` →
  status=sent, logged digest_log id=35, result=50 permits.
  Bare body `{}` triggers full all-subscribers send.
- **Password reset:** 4 endpoints all wired:
  - `GET /forgot-password` (200) — request form
  - `POST /api/forgot-password` — validates email, generates token,
    1-hour expiry, sends `/reset-password/<token>` link via SMTP
  - `GET /reset-password/<token>` — validates token state
  - `POST /api/reset-password` — accepts password (≥8 chars), marks
    token used
- **Onboarding hand-off:** after reset/login, user hits /onboarding
  which captures cities + trades — exactly the V494 city-prefs gap
  recovery path for paid customers who bypassed /select-cities.
- **Watchpoint:** Pre-Deploy Gates → both endpoints must return 200
  to a fresh test cycle on every deploy. If either breaks, paid
  customer recovery is blocked.

### O13 — chatgpt.com / referral showing in GA4 (positive signal)
- **Found:** 2026-05-03 — 1 referral session from chatgpt.com
  visible in GA4 Traffic Acquisition for May 1-2.
- **Impact:** Brand-discovery signal — ChatGPT mentioned
  PermitGrab to someone, who clicked through. Worth tracking
  whether this is recurring traffic.
- **Fix:** None needed — log only. Add a UAT watchpoint to spot
  it growing or disappearing.
- **Status:** Open (informational, not a bug).
- **Watchpoint:** SEO + Indexing checklist → check GA4 Traffic
  Acquisition for chatgpt.com / referral monthly. If it grows,
  ChatGPT is citing PermitGrab as a building-permit source —
  worth doubling down on the FAQ + structured data signals
  that LLMs cite from.

### O15 — Cloudflare Email Routing on permitgrab.com (P1 customer reply path)
- **Found:** 2026-05-04 (set up by Wes after the alerts@ bounce-back)
- **Source:** Cloudflare Email Routing forwards every address
  @permitgrab.com to wcrainshaw@gmail.com:
  - catch-all → wcrainshaw@gmail.com
  - sales@permitgrab.com → wcrainshaw@gmail.com
  - wes@permitgrab.com → wcrainshaw@gmail.com
  - alerts@permitgrab.com → wcrainshaw@gmail.com
- **Impact if it breaks:** every customer reply, every digest reply,
  every Stripe/Resend/Cloudflare alert silently disappears. The
  product looks abandoned from the outside.
- **Fix:** none — passive infra. If MX records get clobbered or a
  route gets toggled off in CF, re-enable in
  Cloudflare → Email → Email Routing.
- **Status:** Open (monitor only).
- **Watchpoint:** Pre-Deploy Gates → run
  `POST /api/admin/email-test {"to":"wcrainshaw@gmail.com",
  "kind":"sales"}` weekly. If it doesn't land in gmail within 60s,
  the route is broken — fix in CF dashboard before next deploy.

### O16 — send_sales_email() must be the ONLY outreach path (P1 brand consistency)
- **Found:** 2026-05-04 (V495 Phase 3 bonus)
- **Source:** `email_alerts.send_sales_email()` (V495) sends
  FROM sales@permitgrab.com with Reply-To: wcrainshaw@gmail.com,
  so the customer sees a brand sender and replies route back to
  Wes's inbox via Cloudflare Email Routing (O15).
- **Impact if regressed:** if recovery / outreach emails (Higgins,
  Meyer, Gomes, future cold reactivation) go through the regular
  `send_email()` (FROM noreply@permitgrab.com) the FROM looks like
  a no-reply system mailer and reply rate drops to ~0%.
- **Fix:** every V494 manual-subscriber outreach, every Higgins/
  Meyer recovery email, every cold/win-back template MUST call
  `send_sales_email()` not `send_email()`. The `/api/admin/email-test`
  endpoint takes `"kind":"sales"` to verify the path stays alive.
- **Status:** Open (regression watch).
- **Watchpoint:** Pre-Deploy Gates → grep before any deploy:
  `grep -n "send_email\b" routes/admin.py templates/emails/sales*`
  in any sales-context call sites. They should be calling
  `send_sales_email`, not `send_email`.

### O17 — DMARC DNS record on _dmarc.permitgrab.com (P2 deliverability)
- **Found:** 2026-05-04
- **Source:** Wes added a TXT record at _dmarc.permitgrab.com:
  `v=DMARC1; p=none; rua=mailto:wcrainshaw@gmail.com; pct=100;`
  Starts in monitor mode (`p=none`) — receivers report on auth
  results without rejecting any mail.
- **Impact:** Without DMARC, Gmail/Outlook spam-filter mail
  FROM @permitgrab.com aggressively (especially after the brand
  was previously sending via SendGrid with no published policy).
  Once the rua= reports are clean for 2 weeks, escalate to
  `p=quarantine` so spoofed senders get junked instead of inbox'd.
- **Fix:** none right now (record is live in monitor mode). Schedule
  a 2-week follow-up to (a) review the rua= aggregate reports
  for unauthorized senders, and (b) bump policy to
  `p=quarantine; rua=mailto:wcrainshaw@gmail.com; pct=100;`.
- **Status:** Open (in monitor mode until ~2026-05-18).
- **Watchpoint:** Pre-Deploy Gates → check
  `dig +short TXT _dmarc.permitgrab.com` returns the record on
  every deploy (in case Cloudflare DNS gets clobbered). Watch the
  rua= mailbox monthly for unauthorized SPF/DKIM failures —
  if a 3rd party (e.g. a re-tooled ESP) starts sending FROM
  @permitgrab.com without DKIM alignment, the report flags it
  before quarantine bites.

---

<a name="closed-findings"></a>
## CLOSED FINDINGS — DO-NOT-REGRESS

These were fixed but historically come back. Add a watchpoint check to
the relevant section above when one regresses.

| ID | Issue | Fixed in | Watchpoint |
|----|-------|----------|------------|
| V247 | Homepage `renderPermits()` null TypeError | V247 | Pre-deploy gate, /  |
| V218 T5B | /api/subscribe accepted "notanemail" | V218 | /get-alerts flow |
| V309 | /get-alerts dropdown OOM with 600+ cities | V309 | /get-alerts flow |
| V313 | Anonymous click "Export" → /signup redirect | V313 | CSV export flow |
| V322 | Pittsburgh CKAN violation collector | V322 | Data quality gate |
| V326 | Saint Pete dead Socrata; Fort Lauderdale frozen 2019 | V326 | Data freshness gate |
| V375 | "Start Free Trial" inconsistent for logged-in vs out | V375 | /start-checkout flow |
| V414 | --preload removed from Dockerfile | V421 | Pre-deploy gate health |
| V443 | Zombie daemon `already_running` forever | V443 | start-collectors flow |
| V474 | City pages had generic SEO meta | V474 B+C | /permits/* page checklist |
| V475 | Email scheduler thread missed in start_collectors | V475 | Pre-deploy gate digest |
| V476 | Tampa Accela had no contractor column | V476 | Tampa-specific |
| V477 | /permits/arizona/phoenix showed 0 records | V477 | /permits/* page checklist |
| V481 | start_collectors → noop on web (V471 PR4 redux) | V493 | Pre-deploy gate health |
| V485 B1 | Sitemap published both /permits/<slug> and /permits/<state>/<slug> | V485 | SEO + Indexing |
| V485 B2 | Persona pages timed out under AdsBot crawl | V485 + V488 | Pre-deploy gate AdsBot |
| V487 | V486 Part C blog posts 404'd (dispatcher lost) | V488 | /blog/<slug> page |
| V488 | persona stats cached in-memory only (lost on restart) | V488 IRONCLAD | Pre-deploy gate |
| V490 | charlotte_meck KeyError: 'name' boot crash | 4091085 hotfix | Health endpoint |

---

<a name="recurring-regressions"></a>
## RECURRING REGRESSIONS

These have come back more than once. They are signals that the underlying
architecture is fragile. Watch for them after every deploy.

### R1: WORKER_MODE no-op on web service (V471 PR4 / V481)
The web process IS the daemon — render.yaml's `permitgrab-worker`
service was never actually created on Render. Anyone who adds a
`if WORKER_MODE != '1': return` guard breaks production. CLAUDE.md
ARCHITECTURE GROUND TRUTH explicitly documents this. If you find a
PR adding such a guard, REJECT it.

### R2: Sitemap dual-canonicalization (V485 B1)
Older code published both /permits/<slug> and /permits/<state>/<slug>.
Each form self-canonicalized. Google saw ~716 URLs (358 cities × 2).
Fix is to publish ONLY the 2-segment form. If sitemap.xml ever shows
both forms again → indexing tanks.

### R3: City page aggregate-query timeouts (V485 B2 → V488 → V492)
Multi-COUNT against 2.5M-row tables under AdsBot crawl times out and
Google flags "Destination not working." Each new source pushing more
rows into the count tables raises the risk. The fix is the IRONCLAD
system_state cache — extend it to any new aggregate that lands on a
public page.

### R4: City slug routing (Cambridge V315/V316, Miami V319/V321,
Louisville known-broken)
prod_cities.source_id ≠ CITY_REGISTRY key → daemon's
collect_single_city falls through to 'not_found' silently. Fix-before-
ship check is in CLAUDE.md ("Always confirm prod_cities.source_id
matches the CITY_REGISTRY key you're patching").

### R5: Free vs paid funnel divergence (V494) + plan data drift
Two related bugs in the same area:

(a) **Funnel split**: /get-alerts captures cities; /start-checkout
doesn't. Free users become subscribers; paid users become Users
without subscribers rows. The V494 fix unifies the two funnels but
if anyone re-splits them the bug returns.

(b) **Plan field drift**: Stripe webhook updates `users.plan` (Postgres)
but doesn't update `subscribers.plan` (SQLite). Result: a user who
first signs up via /get-alerts (plan=free in subscribers), then
later upgrades to Stripe trial, keeps showing plan=free in subscribers
forever. Doesn't break digest delivery (active=1 + digest_cities
still work) but breaks reporting and any downstream filter that
looks at subscribers.plan to identify paying customers.

The V494 webhook patch fixes both: writes `subscribers.plan = pro`
for any existing row matching customer email, AND activates any
pending row written by /select-cities.

Watchpoint query for any future deploy:
```sql
-- Should always be 0. If non-zero, the webhook isn't syncing plan.
SELECT COUNT(*) FROM subscribers s
WHERE s.email IN (
  SELECT email FROM stripe_synced_customers  -- or via Stripe API
)
AND s.plan NOT IN ('pro', 'trialing', 'professional', 'enterprise');
```

### R6: AdsBot crawl latency
After every new template change or new aggregate query on a public
page, AdsBot's crawl can time out. Add stale-if-error Cache-Control
to any new `/permits/*` or `/leads/*` route from day one.

### R7: 2FA wall on Google Ads after 2-3 saves
The web UI starts demanding "Confirm it's you" after a couple of ad
saves in a session. Use Google Ads Editor desktop app for batch
edits — uses an API token, no session-cookie 2FA.

---

<a name="how-to-run"></a>
## HOW TO RUN A FULL UAT

### Quick smoke (pre-deploy, ~10 min)
1. Hit Pre-Deploy Gates section, run all bullets.
2. Run Persona Logged-Out section in incognito.
3. Sign up a test account via the V494 flow with Stripe test card.
4. Check Open Findings — has the deploy you're about to ship
   addressed any of them? Move them to Closed if so.

### Full UAT (weekly, ~60 min)
1. All Pre-Deploy Gates.
2. All three persona checklists end-to-end.
3. Every Page-by-Page anchor.
4. All five Flow Checklist flows.
5. Run Mobile Checklist on a real phone or 380px viewport.
6. Run SEO + Indexing automated checks.
7. Run Data Quality SQL gates.
8. Cross-reference Stripe subscriber count vs `subscribers` table
   row count *(V494 watchpoint)*.
9. Check Open Findings — anything age > 30 days? Force a decision:
   fix, defer with explicit reason, or close as won't-fix.

### Tooling
- **Visual regression:** `tests/visual-uat.js` (Puppeteer) and
  `tests/visual_uat_live.py`. The Playwright skill (now installed,
  see CLAUDE.md SKILLS SYSTEM) replaces the Puppeteer pipeline as of
  V491.
- **Conversion flow:** `tests/conversion-uat.js` walks the signup
  funnel.
- **Health probe:** `curl https://permitgrab.com/api/admin/health`
  scripted into UAT runner.
- **Persona browsers:** keep three Chrome profiles for logged-out,
  free, pro to avoid cookie cross-contamination.

### Recording findings
1. New finding → append to Open Findings section with ID Oxx.
2. When fixed → move row to Closed Findings table with the version
   that fixed it. ADD A WATCHPOINT to the appropriate Pre-Deploy /
   Page / Flow checklist so it can't regress silently.
3. If something keeps coming back → promote to Recurring Regressions.

### Adding a new check
1. Decide which section it belongs in.
2. Add as a checkbox with the same shape as adjacent items.
3. If it's a regression watchpoint, link back to the Closed Finding
   row in italics like `*(V494 watchpoint)*` so future testers know
   why the check exists.

---

## CHANGELOG

- **2026-05-03** — Initial consolidation. Merged
  `UAT_VISUAL_REPORT_2026-04-27.md` (10 findings → O5-O10),
  `CODE_V422_UAT_BUGS.txt` (Phase 1-3),
  `CODE_V440_UAT_BUGS.txt` (C1-C2 + M1-M3),
  `CODE_V488_UAT_FIXES.txt` (P0 daemon + P1 blog 404 + persona timeout),
  `docs/V171_UAT_FINDINGS.md`,
  14 historical UAT branch names from git refs (v223-v488).
  Added new findings: O1 V494 paid signup, O2 daemon outage, O3
  destination not working, O4 7 ad groups zero impressions.
  Established Pre-Deploy Gates as the single source of truth before
  ship. Locked Recurring Regressions section to call out R1-R7
  patterns that have come back more than once.
