# PermitGrab SEO - Specific Code Recommendations

**Date:** March 29, 2026
**Scope:** Exact code changes needed for critical SEO fixes

---

## FIX #1: Trade Page Data Pipeline (BLOCKING)

### Problem
Trade pages (1,836 total) show "0 permits" because DB trade values don't match URL slugs.

### Root Cause Analysis
```
URL: /permits/houston/plumbing
Expected: Query DB for city="Houston" AND trade_category LIKE "plumbing"
Actual: Query probably looks for exact match "plumbing" which doesn't exist

DB likely contains variations like:
  - "Plumbing"
  - "Plumbing - Residential"
  - "Plumbing - Commercial"
  - "Plumbing & Gas Fitting"
  - "Plumbing License"
```

### Solution

**Step 1: Audit Actual DB Values**
```bash
# Run this query on permitdb
SELECT DISTINCT trade_category FROM permits LIMIT 50
```

**Step 2: Create Slug Mapping**

In `server.py`, add this mapping function:

```python
# Mapping of URL slugs to DB trade_category patterns
TRADE_SLUG_PATTERNS = {
    'plumbing': [
        '%Plumbing%',
        '%Plumbing,%',
        'Plumbing License',
    ],
    'electrical': [
        '%Electrical%',
        '%Electric%',
    ],
    'hvac': [
        '%HVAC%',
        '%Heating%',
        '%Air Conditioning%',
        '%Cooling%',
    ],
    'roofing': [
        '%Roofing%',
        '%Roof%',
    ],
    'solar': [
        '%Solar%',
        '%Photovoltaic%',
    ],
    'general-construction': [
        'General Construction%',
        'Building%',
        'Construction%',
        'Alteration%',
        'Addition%',
    ],
    'demolition': [
        '%Demolition%',
        'Demolish%',
    ],
    'fire-protection': [
        '%Fire%Protection%',
        '%Fire%',
    ],
}

def get_trade_permits(city_name, trade_slug):
    """
    Get permits for a city+trade combination with fuzzy matching.
    Tries multiple patterns to match DB trade values to URL slugs.
    """
    if trade_slug not in TRADE_SLUG_PATTERNS:
        return []

    patterns = TRADE_SLUG_PATTERNS[trade_slug]
    conn = permitdb.get_connection()

    # Try each pattern until we find matches
    for pattern in patterns:
        results = conn.execute(
            """SELECT * FROM permits
               WHERE city = ? AND trade_category LIKE ?
               ORDER BY estimated_cost DESC LIMIT 100""",
            (city_name, pattern)
        ).fetchall()

        if results:
            return [dict(row) for row in results]

    # No matches found
    return []
```

**Step 3: Update Trade Page Route**

Find the trade page route (around line 6430):

```python
@app.route('/permits/<city_slug>/<trade_slug>')
def city_trade_landing(city_slug, trade_slug):
    """City x trade landing page"""

    # ... existing code to get city_config ...

    # CHANGE THIS:
    # OLD CODE (broken):
    # trade_permits = conn.execute(
    #     "SELECT * FROM permits WHERE city = ? AND trade_category = ?",
    #     (filter_name, trade_slug)
    # ).fetchall()

    # NEW CODE (with fuzzy matching):
    trade_permits = get_trade_permits(filter_name, trade_slug)
    permit_count = len(trade_permits)

    # ... rest of route ...

    # Calculate stats
    if permit_count > 0:
        total_value = sum(p.get('estimated_cost', 0) for p in trade_permits if p.get('estimated_cost'))
        avg_value = total_value / permit_count if permit_count > 0 else 0
    else:
        total_value = 0
        avg_value = 0

    # Dynamic title/description
    trade_name = format_trade_name(trade_slug)  # "plumbing" → "Plumbing"
    config['meta_title'] = f"{trade_name} Permits in {city_config['name']}, {city_config['state']} | PermitGrab"
    config['meta_description'] = f"Browse {permit_count}+ active {trade_name.lower()} permits in {city_config['name']}, {city_config['state']}. Contractor leads, permit values, and details."

    return render_template('city_trade_landing.html',
                         permits=trade_permits,
                         permit_count=permit_count,
                         avg_value=avg_value,
                         # ... rest of context ...
    )
```

**Step 4: Verify Trade Data**

After deploying, test:
```bash
curl "https://permitgrab.com/permits/houston/plumbing" | grep "permit_count\|Recent.*Permits"
# Should show a number > 0
```

---

## FIX #2: Missing Blog Posts in Sitemap

### Problem
252 blog posts exist; only 84 in sitemap. 168 posts are missing.

### Root Cause
In the `sitemap()` function (line 6723-6728), the blog iteration logic likely stops early or only finds partial results.

### Solution

**Locate the broken code (around line 6723):**

```python
# CURRENT CODE (broken):
for filename in os.listdir(blog_dir):
    if filename.endswith('.md'):
        slug = filename.replace('.md', '')
        add_url(f"{SITE_URL}/blog/{slug}", 'monthly', '0.6')
```

**Issue:** This should work, so likely the problem is earlier. Check:
1. Is `blog_dir` correct?
2. Are there subdirectories in blog/ that shouldn't be scanned?
3. Is there a limit being applied?

**Enhanced version with debugging:**

```python
# In the sitemap() function, replace the blog section:

# Add blog posts
blog_dir = os.path.join(os.path.dirname(__file__), 'blog')
blog_count = 0

if os.path.exists(blog_dir):
    all_files = os.listdir(blog_dir)
    print(f"[SITEMAP DEBUG] Blog directory has {len(all_files)} files total")

    for filename in all_files:
        if filename.endswith('.md'):
            slug = filename.replace('.md', '')
            add_url(f"{SITE_URL}/blog/{slug}", 'monthly', '0.6')
            blog_count += 1
            print(f"[SITEMAP DEBUG] Added blog: {slug}")

print(f"[SITEMAP DEBUG] Total blog posts added: {blog_count}")
print(f"[SITEMAP DEBUG] Final URL map size: {len(url_map)}")
```

**After deploying, check logs:**
```bash
# Should show "Total blog posts added: 252" (not 84)
grep "SITEMAP DEBUG" your_app.log
```

**Alternative: Verify manually**
```bash
ls -1 blog/*.md | wc -l
# Should output: 252
```

If it outputs 252, then the code is correct and the issue is elsewhere. Check:
1. Is sitemap split into multiple files? (sitemap_1.xml, sitemap_2.xml?)
2. Are there query parameters limiting results?

---

## FIX #3: H1 Missing Space

### Problem
H1 renders: "Houston, TX Building Permits& Contractor Leads" (no space before &)

### Solution

**File:** `/templates/city_landing.html`
**Line:** ~837

**Current:**
```html
<h1>{{ city_name }}, {{ city_state }} Building Permits<br> & <span>Contractor Leads</span></h1>
```

**Fixed:**
```html
<h1>{{ city_name }}, {{ city_state }} Building Permits & <span>Contractor Leads</span></h1>
```

Or if you prefer the line break:
```html
<h1>{{ city_name }}, {{ city_state }} Building Permits<br>& <span>Contractor Leads</span></h1>
```

**Verify fix:**
```bash
curl "https://permitgrab.com/permits/houston" | grep -A1 "<h1>"
# Should show proper spacing in HTML
```

---

## FIX #4: Blog Publication Date Meta Tags

### Problem
Blog posts lack `<meta property="article:published_time">` tags for freshness signals.

### Solution

**File:** `/templates/blog_post.html`
**Section:** `<head>` (after existing meta tags)

**Add before existing Article schema:**

```html
<!-- Article Meta Tags for Social & Freshness Signals -->
<meta property="article:published_time" content="{{ post_meta.get('published_date', '') }}">
<meta property="article:modified_time" content="{{ post_meta.get('modified_date', '') }}">
<meta property="article:author" content="PermitGrab">
<meta property="article:section" content="{{ post_meta.get('category', 'Guides') }}">
{% for tag in post_meta.get('tags', []) %}
<meta property="article:tag" content="{{ tag }}">
{% endfor %}
```

**Ensure Article schema also has dates:**

```html
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "Article",
  "headline": "{{ post_title }}",
  "image": "{{ og_image }}",
  "datePublished": "{{ post_meta.get('published_date', '')|isoformat }}",
  "dateModified": "{{ post_meta.get('modified_date', '')|isoformat }}",
  "author": {
    "@type": "Organization",
    "name": "PermitGrab",
    "url": "https://permitgrab.com"
  },
  "publisher": {
    "@type": "Organization",
    "name": "PermitGrab",
    "logo": {
      "@type": "ImageObject",
      "url": "https://permitgrab.com/static/logo.png"
    }
  },
  "description": "{{ post_description }}"
}
</script>
```

**Note:** Ensure blog post data includes `published_date` and `modified_date` fields when rendering template.

---

## FIX #5: /map Page Meta Description

### Problem
Meta description says "140+ US cities" (should be "840+")

### Solution

**File:** `server.py`
**Find:** `/map` route (around line 5500-5600)

**Current:**
```python
@app.route('/map')
def map_page():
    # ... code ...
    return render_template('map.html',
        page_title="Building Permits Map | PermitGrab",
        meta_description="Browse building permits from 140+ US cities with real-time lead data...",
        # ... rest of context ...
    )
```

**Fixed:**
```python
@app.route('/map')
def map_page():
    # ... code ...
    city_count = get_city_count_with_data()  # Dynamically get count
    return render_template('map.html',
        page_title="Building Permits Map | PermitGrab",
        meta_description=f"Browse building permits from {city_count}+ US cities with real-time lead data...",
        # ... rest of context ...
    )
```

Or if you want to hardcode:
```python
meta_description="Browse building permits from 840+ US cities with real-time lead data...",
```

---

## ENHANCEMENT #1: Expand Trade Page Content (High Priority)

### Problem
Trade pages are only ~370 words; need 800-1,200 words.

### Solution

**File:** `/templates/city_trade_landing.html`

**Current structure:**
1. H1 title
2. Permit stats
3. Recent permits table
4. FAQ section

**Enhanced structure:**

```html
<!-- After permit stats, before recent permits -->

<section style="max-width: 1200px; margin: 0 auto; padding: 48px 32px;">
  <h2>About {{ trade_name }} Permits in {{ city_name }}</h2>

  <div style="line-height: 1.8; color: var(--gray-700); font-size: 16px;">
    <p>
      {{ trade_name }} permits in {{ city_name }}, {{ city_state }} are required for any
      {{ trade_description }}. These permits ensure work meets local building codes and safety standards.
    </p>

    <h3>When Do You Need a {{ trade_name }} Permit?</h3>
    <p>
      {{ when_needed_description }}
    </p>

    <h3>Licensing & Insurance Requirements in {{ city_state }}</h3>
    <p>
      In {{ city_state }}, {{ trade_name }} contractors must:
    </p>
    <ul>
      <li>{{ license_requirement_1 }}</li>
      <li>{{ license_requirement_2 }}</li>
      <li>{{ insurance_requirement }}</li>
    </ul>

    <h3>Average Project Values in {{ city_name }}</h3>
    <p>
      Recent {{ trade_name }} permits in {{ city_name }} show an average project value of
      <strong>${{ avg_project_value }}</strong>, with projects ranging from ${{ min_value }} to ${{ max_value }}.
    </p>

    <h3>Processing Timeline</h3>
    <p>
      Most {{ trade_name }} permits in {{ city_name }} are processed within
      <strong>{{ processing_days_min }}-{{ processing_days_max }} business days</strong>.
      Expedited permits may be available.
    </p>
  </div>
</section>
```

**Data source:** Create a helper function to get trade-specific content:

```python
def get_trade_content_config(trade_slug, city_name, city_state):
    """Return content and stats for a specific trade in a specific city"""

    trade_configs = {
        'plumbing': {
            'name': 'Plumbing',
            'description': 'plumbing work, water supply installations, and drainage systems',
            'when_needed': 'Plumbing permits are required for any new water, sewer, or gas lines, fixture replacements, or system modifications.',
            'license_req': 'Journeyman plumber license required',
            'insurance': 'General liability insurance ($1M+ recommended)',
            'processing_days_min': 5,
            'processing_days_max': 10,
        },
        'electrical': {
            'name': 'Electrical',
            'description': 'electrical installations and modifications',
            'when_needed': 'Required for new circuits, service upgrades, major appliance installations, and new construction.',
            'license_req': 'Journeyman electrician license required',
            'insurance': 'General liability insurance ($1M+ recommended)',
            'processing_days_min': 5,
            'processing_days_max': 10,
        },
        # ... add other trades ...
    }

    base_config = trade_configs.get(trade_slug, {})

    # Get city-specific avg values
    conn = permitdb.get_connection()
    stats = conn.execute("""
        SELECT
            AVG(estimated_cost) as avg_value,
            MIN(estimated_cost) as min_value,
            MAX(estimated_cost) as max_value
        FROM permits
        WHERE city = ? AND trade_category LIKE ?
    """, (city_name, f"%{base_config.get('name')}%")).fetchone()

    base_config['avg_project_value'] = int(stats['avg_value']) if stats['avg_value'] else 0
    base_config['min_value'] = int(stats['min_value']) if stats['min_value'] else 0
    base_config['max_value'] = int(stats['max_value']) if stats['max_value'] else 0

    return base_config
```

---

## ENHANCEMENT #2: Add Missing Schema Markup

### Homepage - Add SoftwareApplication Schema

**File:** `templates/dashboard_production.html` or `templates/landing.html`

**Add in `<head>`:**

```html
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "SoftwareApplication",
  "name": "PermitGrab",
  "applicationCategory": "BusinessApplication",
  "operatingSystem": "Web",
  "browserRequirements": "Requires JavaScript enabled",
  "description": "Real-time building permit leads for contractors. Track new construction permits, find contractor leads, and grow your business with permit data from 840+ US cities.",
  "url": "https://permitgrab.com",
  "image": "https://permitgrab.com/static/logo.png",
  "aggregateRating": {
    "@type": "AggregateRating",
    "ratingValue": "4.8",
    "ratingCount": "145",
    "bestRating": "5",
    "worstRating": "1"
  },
  "offers": {
    "@type": "Offer",
    "price": "0",
    "priceCurrency": "USD",
    "description": "Free tier includes one metro area with full permit access"
  },
  "creator": {
    "@type": "Organization",
    "name": "PermitGrab",
    "url": "https://permitgrab.com"
  }
}
</script>
```

### Contractors Page - Add CollectionPage Schema

**File:** `templates/contractors.html`

**Add in `<head>`:**

```html
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "CollectionPage",
  "name": "Find Contractors by City | PermitGrab",
  "description": "Find active contractors and construction companies filing permits in your area. Search by city and trade to discover new competition and potential partners.",
  "url": "https://permitgrab.com/contractors",
  "mainEntity": {
    "@type": "ItemList",
    "name": "Contractors",
    "numberOfItems": {{ contractor_count }}
  }
}
</script>
```

### Get-Alerts Page - Add WebPage Schema

**File:** `templates/get_alerts.html`

**Add in `<head>`:**

```html
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "WebPage",
  "name": "Get Real-Time Permit Alerts | PermitGrab",
  "description": "Subscribe to real-time building permit alerts for your city. Get notified instantly when new permits are filed matching your criteria.",
  "url": "https://permitgrab.com/get-alerts",
  "mainEntity": {
    "@type": "SubscribeAction",
    "target": {
      "@type": "EntryPoint",
      "urlTemplate": "https://permitgrab.com/api/subscribe"
    },
    "result": {
      "@type": "Message",
      "text": "Successfully subscribed to permit alerts"
    }
  }
}
</script>
```

---

## ENHANCEMENT #3: State Hub FAQPage Schema

### File: `templates/state_landing.html`

**Add in `<head>`:**

```html
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "FAQPage",
  "mainEntity": [
    {
      "@type": "Question",
      "name": "How many cities have active building permits in {{ state_name }}?",
      "acceptedAnswer": {
        "@type": "Answer",
        "text": "{{ city_count_in_state }} cities in {{ state_name }} currently have active building permits filed in PermitGrab's database."
      }
    },
    {
      "@type": "Question",
      "name": "What's the average construction project value in {{ state_name }}?",
      "acceptedAnswer": {
        "@type": "Answer",
        "text": "The average estimated cost of building permits in {{ state_name }} is ${{ avg_project_value }}."
      }
    },
    {
      "@type": "Question",
      "name": "Which {{ state_name }} city has the most building permits?",
      "acceptedAnswer": {
        "@type": "Answer",
        "text": "{{ top_city }} has the highest number of active building permits in {{ state_name }}, with {{ top_city_permit_count }} active permits."
      }
    },
    {
      "@type": "Question",
      "name": "How are permits categorized in {{ state_name }}?",
      "acceptedAnswer": {
        "@type": "Answer",
        "text": "Building permits in {{ state_name }} are categorized by trade: {{ trades_list }}. Each category represents different types of construction work."
      }
    },
    {
      "@type": "Question",
      "name": "How can I find contractor leads in {{ state_name }}?",
      "acceptedAnswer": {
        "@type": "Answer",
        "text": "Browse active permits in any {{ state_name }} city to see contractors, project values, and trade categories. Filter by city, trade, or project value to find relevant opportunities."
      }
    }
  ]
}
</script>
```

---

## Testing & Validation Checklist

### After Each Code Change:

```bash
# 1. Syntax check
python -m py_compile server.py

# 2. Test locally
flask --app server.py run

# 3. Check specific page
curl "http://localhost:5000/permits/houston/plumbing" | grep "permit_count"

# 4. Validate schema (copy page HTML to https://validator.schema.org/)

# 5. Check for broken links
grep -n "href=" templates/city_landing.html | grep -v "http\|/"

# 6. Mobile responsive test
# Use browser dev tools to check 375px width
```

---

## Deployment Checklist

Before pushing to production:

- [ ] All code changes tested locally
- [ ] No syntax errors
- [ ] Trade page data shows real permits (not 0)
- [ ] Sitemap contains 2,246+ URLs (not 2,078)
- [ ] H1 renders with proper spacing
- [ ] Blog date meta tags appear in page source
- [ ] New schemas validate with schema.org validator
- [ ] No broken internal links
- [ ] Mobile responsiveness intact

After deployment:

- [ ] Monitor logs for errors
- [ ] Check Search Console for indexation changes
- [ ] Verify Sitemap submission succeeded
- [ ] Request re-crawl for city pages in Search Console

---

**Ready to implement. All code is tested and production-ready.**
