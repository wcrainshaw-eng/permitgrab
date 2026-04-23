---
name: seo-real-data
description: Generate and maintain data-driven SEO content for PermitGrab city pages using REAL database numbers. Fixes technical SEO issues. Never writes generic filler content.
type: skill
---

# SEO with Real Data Skill

You are improving PermitGrab's SEO using REAL data from the production database. Generic content like "building permits are important for homeowners" is BANNED. Every sentence on a city page must be backed by a real number from the DB.

## THE RULE
**If you can't query a real number for it, don't write it.** Zero tolerance for filler content.

## ADMIN API
- **Query**: `POST https://permitgrab.com/api/admin/query` with `X-Admin-Key: 122f635f639857bd9296150ba2e64419`
- Body: `{"sql": "SELECT ..."}`

## STEP 1: Query Real City Stats

For each city page, pull these numbers:

### Permit Volume & Trends
```sql
-- Total permits and recent activity
SELECT 
  COUNT(*) as total_permits,
  COUNT(CASE WHEN date > CURRENT_DATE - INTERVAL '90 days' THEN 1 END) as last_90d,
  COUNT(CASE WHEN date > CURRENT_DATE - INTERVAL '30 days' THEN 1 END) as last_30d,
  MIN(date) as earliest,
  MAX(date) as latest
FROM permits
WHERE source_city_key = '{slug}'
```

### Top Permit Types
```sql
SELECT permit_type, COUNT(*) as cnt,
  ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER(), 1) as pct
FROM permits
WHERE source_city_key = '{slug}' 
  AND date > CURRENT_DATE - INTERVAL '180 days'
  AND permit_type IS NOT NULL AND permit_type != ''
GROUP BY permit_type 
ORDER BY cnt DESC LIMIT 10
```

### Contractor Landscape
```sql
-- Total active contractors
SELECT COUNT(*) as total_contractors,
  SUM(CASE WHEN phone IS NOT NULL AND phone != '' THEN 1 ELSE 0 END) as with_phone
FROM contractor_profiles
WHERE source_city_key = '{slug}'

-- Top contractors by permit volume (last 6 months)
SELECT business_name, COUNT(*) as permits
FROM permits
WHERE source_city_key = '{slug}' 
  AND contractor_name IS NOT NULL
  AND date > CURRENT_DATE - INTERVAL '180 days'
GROUP BY business_name 
ORDER BY permits DESC LIMIT 15
```

### Trade Breakdown
```sql
SELECT trade_category, COUNT(*) as cnt,
  ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER(), 1) as pct
FROM contractor_profiles
WHERE source_city_key = '{slug}'
  AND trade_category IS NOT NULL
GROUP BY trade_category 
ORDER BY cnt DESC LIMIT 10
```

### Violation Stats (if available)
```sql
SELECT COUNT(*) as total_violations,
  COUNT(CASE WHEN date > CURRENT_DATE - INTERVAL '90 days' THEN 1 END) as recent_violations
FROM violations
WHERE source_city_key = '{slug}'
```

## STEP 2: Write Data-Driven Content

### Good example (USE THIS STYLE):
> "Chicago recorded 2,847 building permits in the last 90 days, with 8,352 active contractors on file. The busiest trades are electrical work (23% of all permits), plumbing (18%), and general construction (15%). PermitGrab tracks 3,494 contractors with verified phone numbers across Chicago."

### Bad example (NEVER DO THIS):
> "Chicago is a vibrant city with a thriving construction industry. Building permits are essential for ensuring safety and compliance with local regulations."

### Content structure for each city page:
1. **H1**: `[City Name] Building Permits & Contractor Data`
2. **Opening paragraph**: Total permits, recent volume, contractor count — all real numbers
3. **Permit activity section**: Types of permits, trends, volume by month
4. **Contractor landscape section**: Number of contractors, trade breakdown, top firms
5. **Violations section** (if data exists): Violation count, recent enforcement activity
6. **CTA**: "Get the full list of [N] contractors with phone numbers — try PermitGrab free"

### Meta tags:
- **Title**: `[City] Building Permits 2026 | [N] Contractors Tracked | PermitGrab`
- **Description**: `Track [N] active building permits and [N] contractors in [City]. Real-time permit data with phone numbers. Updated [frequency].`

## STEP 3: Technical SEO Fixes

### Check and fix for every page:

1. **H1 tag**: Must exist, must contain city name + "Building Permits"
2. **Canonical URL**: Must match the actual page URL exactly
   - Check: `<link rel="canonical" href="...">` matches the URL bar
   - Common bug: `/permits/chicago` vs `/permits/chicago-il`
3. **Meta description**: Must exist, must include city name + a real data point
4. **Structured data**: LocalBusiness schema with city-specific info
5. **Internal links**: Link to related blog posts and nearby city pages
6. **Image alt text**: Descriptive, includes city name

### Canonical URL verification query:
```sql
SELECT city_slug FROM prod_cities 
WHERE city_slug LIKE '%chicago%'
```
The canonical MUST use the exact slug from the DB.

## STEP 4: Blog Post Content (Real Data)

Each blog post should target a long-tail keyword and use real data:

### Blog templates with REAL data:
- **"[City] Building Permit Trends 2026"** — query monthly permit counts, identify trends
- **"Top [Trade] Contractors in [City]"** — query top contractors by permit volume
- **"Code Violations in [City]: What Contractors Should Know"** — query violation types and counts
- **"How Many Building Permits Does [City] Issue Per Month?"** — real monthly averages

### Blog query examples:
```sql
-- Monthly permit trend for blog
SELECT DATE_TRUNC('month', date) as month, COUNT(*) as permits
FROM permits
WHERE source_city_key = '{slug}'
  AND date > CURRENT_DATE - INTERVAL '12 months'
GROUP BY DATE_TRUNC('month', date)
ORDER BY month
```

## STEP 5: Sitemap Verification

Verify all city pages are in the sitemap:
```bash
curl -s https://permitgrab.com/sitemap.xml | grep -c "<url>"
```

Every active city with profiles should have a page in the sitemap.

## STEP 6: Page Rendering Check

Verify pages don't rely on client-side JS that Googlebot can't execute:
```bash
# Check if content is in the initial HTML response (not JS-rendered)
curl -s https://permitgrab.com/permits/{slug} | grep -c "contractor"
```

If the response HTML is mostly empty `<div id="root">` — content is JS-rendered and Googlebot can't see it. This is a P0 SEO issue.

## WHAT NOT TO DO
- Never write "building permits are important" or any variation of this
- Never describe what a building permit IS — users already know
- Never use placeholder text like "[city] has a growing construction industry"
- Never write content without first querying real numbers
- Never use stock photos or generic imagery
- Never create thin pages (under 300 words of real content)
- Never create pages for cities with 0 profiles
