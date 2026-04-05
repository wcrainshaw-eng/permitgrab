# PermitGrab SEO - Critical Fixes Checklist

**Quick Reference for High-Impact Improvements**
**Last Updated:** March 29, 2026

---

## CRITICAL ISSUES (Fix This Week)

### 🔴 ISSUE #1: Trade Pages Show 0 Permits (Blocks 1,836 Pages)
- **Status:** BLOCKING ALL TRADE PAGE VALUE
- **Location:** `/permits/houston/plumbing` and all similar pages
- **Problem:** DB trade values don't match URL slugs; no fuzzy matching
- **Impact:** 1,836 pages appear as doorway/thin-content pages
- **Fix Effort:** 2-4 hours
- **Code Location:** `server.py` → trade page route handler
- **Action Items:**
  - [ ] Audit DB: `SELECT DISTINCT trade_category FROM permits LIMIT 50`
  - [ ] Create slug-to-db mapping (e.g., "plumbing" → "Plumbing%", "Plumbing - Residential")
  - [ ] Add LIKE-based matching in trade query
  - [ ] Test: Verify `houston/plumbing` shows real permits
  - [ ] Verify permit count matches across pages

---

### 🔴 ISSUE #2: 168 Blog Posts Missing from Sitemap
- **Status:** CRITICAL DISCOVERY GAP
- **Problem:** Blog index shows 252 posts; sitemap has only 84
- **Impact:** 168 blog posts not crawled efficiently; lost organic traffic
- **Fix Effort:** 1 hour
- **Code Location:** `server.py` → `sitemap()` function (line 6723-6728)
- **Action Items:**
  - [ ] Identify why blog directory iteration stops at 84 posts
  - [ ] Check file count: `ls -1 blog/*.md | wc -l`
  - [ ] Add debug logging: `print(f"Blog posts found: {count}")`
  - [ ] Verify all 252 posts are added to XML
  - [ ] Test sitemap size increased from 2,078 → 2,246 URLs

---

### 🔴 ISSUE #3: H1 Missing Space Before "&" (All 848 City Pages)
- **Status:** AFFECTS EVERY CITY PAGE
- **Problem:** Renders "Houston, TX Building Permits& Contractor Leads" (no space)
- **Impact:** Minor ranking signal; accessibility issue
- **Fix Effort:** 5 minutes
- **Code Location:** `templates/city_landing.html` line ~837
- **Current:** `<h1>{{ city_name }}, {{ city_state }} Building Permits<br> & <span>Contractor Leads</span></h1>`
- **Fix:** Add space before `&`
  ```html
  <h1>{{ city_name }}, {{ city_state }} Building Permits & <span>Contractor Leads</span></h1>
  ```
- **Action Items:**
  - [ ] Update template
  - [ ] Test one city page (check HTML source)
  - [ ] Resubmit in Search Console

---

### 🟡 ISSUE #4: Blog Posts Missing Publication Date Meta Tags
- **Status:** FRESHNESS SIGNAL LOSS
- **Problem:** No `<meta property="article:published_time">` tags
- **Impact:** ~10% reduction in freshness signals
- **Fix Effort:** 15 minutes
- **Code Location:** `templates/blog_post.html` → `<head>` section
- **Add:**
  ```html
  <meta property="article:published_time" content="{{ published_date }}">
  <meta property="article:modified_time" content="{{ modified_date }}">
  <meta property="article:author" content="PermitGrab">
  ```
- **Also ensure** Article schema has:
  ```json
  "datePublished": "{{ published_date | isoformat }}",
  "dateModified": "{{ modified_date | isoformat }}"
  ```
- **Action Items:**
  - [ ] Add meta tags to blog template
  - [ ] Verify Article schema has dates
  - [ ] Test one blog post

---

### 🟡 ISSUE #5: /map Page Meta Description Stale
- **Status:** MINOR, QUICK FIX
- **Problem:** Says "140+ US cities" (should be "840+")
- **Fix Effort:** 1 minute
- **Code Location:** `server.py` → `/map` route
- **Current:** `Browse building permits from 140+ US cities...`
- **Change to:** `Browse building permits from 840+ US cities...`
- **Action Items:**
  - [ ] Find /map route in server.py
  - [ ] Update hardcoded city count
  - [ ] Test page

---

## HIGH-PRIORITY IMPROVEMENTS (Next 2 Weeks)

### 🟠 IMPROVEMENT #1: Expand Trade Page Content to 800+ Words
- **Current:** ~370 words
- **Target:** 800-1,200 words
- **Impact:** +20-30% ranking improvement
- **Effort:** 8-16 hours (1-2 hours per page template)
- **Add to `/templates/city_trade_landing.html`:**
  - [ ] "About [Trade] Permits in [City]" section (3 paragraphs)
  - [ ] Local licensing requirements (state-specific)
  - [ ] Average project values (pull from DB)
  - [ ] Processing timeline
  - [ ] Common permit types
  - [ ] Links to trade-specific blog posts
  - [ ] CTA: "Get [Trade] Leads"
- **New Schemas:** Add Dataset + WebPage (currently only FAQPage)

---

### 🟠 IMPROVEMENT #2: Expand Top 20 City Blog Posts to 1,500+ Words
- **Current:** 883 words (Houston example)
- **Target:** 1,500-2,500 words
- **Top 20 Cities:** NYC, LA, Chicago, Houston, Phoenix, Philadelphia, San Antonio, San Diego, Dallas, San Jose, Austin, Jacksonville, Fort Worth, Columbus, Charlotte, San Francisco, Indianapolis, Seattle, Denver, Boston
- **Impact:** +25-40% traffic increase
- **Add:** Permit fees, processing times, neighborhoods, trade subsections, licensing info
- **Effort:** 20-30 hours total
- **Action Items:**
  - [ ] Identify top 20 cities by permit volume
  - [ ] Create content expansion plan
  - [ ] Add internal links to trade pages (currently missing)

---

### 🟠 IMPROVEMENT #3: Add Missing Schema Markup (3 pages × 2 hours)
- **Homepage:** Add SoftwareApplication schema
  ```json
  {
    "@type": "SoftwareApplication",
    "name": "PermitGrab",
    "applicationCategory": "BusinessApplication",
    "operatingSystem": "Web",
    "description": "Real-time building permit leads for contractors..."
  }
  ```
- **Contractors Page:** Add CollectionPage schema
- **Get-Alerts Page:** Add WebPage schema
- **Effort:** 1-2 hours total
- **Action Items:**
  - [ ] Add SoftwareApplication to homepage
  - [ ] Add CollectionPage to /contractors
  - [ ] Add WebPage to /get-alerts

---

### 🟠 IMPROVEMENT #4: Add State Hub FAQPage Schema & Content
- **Current:** Only CollectionPage schema
- **Add:** FAQPage schema + FAQ content + "Fastest Growing Cities"
- **Impact:** Eligible for "People Also Ask" features
- **Effort:** 4-6 hours
- **FAQ Examples:**
  - "How many Texas cities have active permits?"
  - "What's the average construction project value in Texas?"
  - "Which Texas city has the most building permits?"
- **New DB Functions Needed:**
  - `get_fastest_growing_cities(state)` - Month-over-month growth
  - `get_top_contractors_by_state(state)` - By permit volume
- **Action Items:**
  - [ ] Create DB functions
  - [ ] Add FAQ section to state_landing.html
  - [ ] Add FAQPage schema
  - [ ] Add "Fastest Growing Cities" section

---

### 🟠 IMPROVEMENT #5: Add Blog → Trade Page Internal Links
- **Current:** Blog links to city pages only
- **Add:** Links from blog to trade pages
- **Example:** "Houston Building Permits" blog → plumbing/electrical/roofing pages
- **Effort:** 2-3 hours
- **Impact:** Better topic clustering, +3-5% ranking boost
- **Action Items:**
  - [ ] Add contextual links in blog post bodies
  - [ ] Verify at least 3 trade links per city blog post

---

## MEDIUM-PRIORITY TASKS (Next 4 Weeks)

### 🟡 TASK #1: Add "Top Neighborhoods" to City Pages
- **Current:** Not shown
- **Add:** Top 5 zip codes by permit count
- **DB Query:** `SELECT zip_code, COUNT(*) FROM permits WHERE city=? GROUP BY zip_code ORDER BY COUNT(*) DESC LIMIT 5`
- **Effort:** 2-3 hours
- **Impact:** +5% unique content per city

---

### 🟡 TASK #2: Paginate /blog Index (Currently 252 Posts on One Page)
- **Current:** All posts on /blog page
- **Target:** 20 posts/page with pagination
- **Add:** rel="next"/rel="prev" tags
- **Add:** Category filters (city guides, trade guides, market reports)
- **Effort:** 4-6 hours
- **Impact:** Better crawl efficiency, faster load time

---

### 🟡 TASK #3: Add Related Content Footer Sections
- **Current:** Some pages have; inconsistent
- **Target:** All pages (city, trade, blog) have 3-4 related links
- **Effort:** 2-3 hours
- **Impact:** Better internal linking authority

---

## QUICK WINS (30 min total)

- [ ] Fix H1 space issue (5 min)
- [ ] Update /map meta description (1 min)
- [ ] Add blog date meta tags (15 min)
- [ ] Add footer "Building:" garbage filter (5 min)
- [ ] Check city count micro-inconsistency (5 min)

---

## ESTIMATED IMPACT

### After CRITICAL Fixes (This Week):
- Indexed pages: 900 → 1,100 (+22%)
- Organic impressions: +15-20%
- Trade page penalty removed

### After HIGH-PRIORITY Fixes (2 Weeks):
- Indexed pages: 1,100 → 2,300 (+109%)
- Blog content strength: +3x average word count
- Estimated organic traffic: +50%

### After MEDIUM-PRIORITY Fixes (4 Weeks):
- Keyword coverage: 900 → 3,500+ keywords
- Estimated organic traffic: +75-100%

---

## DEPENDENCIES

**Fix Order (Critical):**
1. **Trade page data pipeline** ← Everything else depends on this
2. **Blog sitemap gap** ← Blocks 168 posts
3. **H1 space issue** ← Quick, high-visibility fix
4. **Blog date meta tags** ← Freshness signals
5. **Content expansion** ← Can work in parallel

---

## TESTING CHECKLIST

After each fix, verify:
- [ ] Page renders correctly (browser + mobile)
- [ ] HTML validates (no broken tags)
- [ ] Meta tags appear in page source
- [ ] Structured data validates (schema.org validator)
- [ ] Internal links not broken
- [ ] Sitemap updated (if applicable)

---

## GOOGLE SEARCH CONSOLE ACTIONS

After deploying fixes:
1. [ ] Submit updated sitemap
2. [ ] Request indexing for trade pages
3. [ ] Check coverage report for newly indexed pages
4. [ ] Monitor indexation in next 48 hours

---

**Priority: CRITICAL → HIGH → MEDIUM**
**Start with CRITICAL issues to unblock SEO growth**
