---
name: competitive-intel
description: Research competitors, identify feature gaps, and build what they have that we don't
type: skill
---

# Competitive Intelligence Skill

## When to Use
- Weekly during the autonomous loop (Phase 4)
- Before building any new feature (check if competitors already do it)
- When prioritizing what to build next

## Competitors

| Competitor | URL | What They Do | Our Advantage |
|-----------|-----|-------------|---------------|
| BuildZoom | buildzoom.com | Contractor profiles + project history | We have permit-level data they don't |
| ConstructConnect | constructconnect.com | Project leads from permits | They're $500+/mo, we're $149 |
| Dodge Construction | construction.com | Commercial project tracking | We focus on residential/small commercial |
| Canopy | canopy.com | Permit analytics for real estate | We have phone numbers for direct outreach |

## Research Procedure

### Step 1: Check competitor pages via SSH
```bash
RENDER_SSH="srv-d6s1tvsr85hc73em9ch0@ssh.oregon.render.com"

# Check what data fields a competitor shows
ssh -T $RENDER_SSH 'curl -s "https://buildzoom.com/contractor/example" | grep -oP "class=\"[^\"]*\"" | sort -u | head -30'

# Check their city pages
ssh -T $RENDER_SSH 'curl -s "https://buildzoom.com/city/chicago" | grep -oP "<h[1-3][^>]*>.*?</h[1-3]>" | head -10'
```

### Step 2: Identify feature gaps
For each competitor page, note:
- Data fields they show that we don't
- Filters or search capabilities
- Visual elements (maps, charts, timelines)
- SEO elements (schema markup, meta descriptions, content depth)

### Step 3: Check if we have the data
```sql
-- What fields do we actually have in our permits table?
SELECT column_name FROM information_schema.columns WHERE table_name = 'permits'

-- Do we have permit values?
SELECT source_city_key, COUNT(*) as has_value
FROM permits WHERE permit_value IS NOT NULL AND permit_value > 0
GROUP BY source_city_key ORDER BY has_value DESC LIMIT 10

-- Do we have detailed permit types?
SELECT DISTINCT permit_type FROM permits WHERE source_city_key = 'chicago-il' LIMIT 20
```

### Step 4: Build the feature
If we have the data, build it. If we don't, note what data source we'd need.

## Feature Gap Checklist
After researching, update this checklist:
- [ ] Permit dollar values on city pages
- [ ] Date range filtering
- [ ] Trade/category filtering
- [ ] Individual contractor detail pages
- [ ] CSV/Excel export for subscribers
- [ ] Email alert digests
- [ ] Violation cross-references on contractor profiles
- [ ] Neighborhood/zip code breakdown
- [ ] Permit trend charts (volume over time)
- [ ] "Similar contractors" recommendations
