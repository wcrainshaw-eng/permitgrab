# PermitGrab: City Data Discovery Playbook

## Goal
Cover the top 2,000 US cities by population with real-time building permit data.

## Current State (V17h — March 2026)
- **~250 cities configured** in `city_configs.py`
- **~25-30 cities actively pulling data** (the rest have dead/fabricated endpoints)
- Working sources: NYC, Chicago, LA, Austin, Seattle, SF, Atlanta, Nashville, Denver, Portland, DC, Baltimore, Boston, Philly, Minneapolis, Orlando, Las Vegas, Mesa, and others

## How City Data Discovery Works

### The 4 Platform Types
1. **Socrata (SODA API)** — `domain/resource/{dataset_id}.json`
   - Used by: NYC, Chicago, SF, Austin, Orlando, Fort Worth
   - Discovery: Socrata Catalog API `api.us.socrata.com/api/catalog/v1?q=building+permit&domains={domain}`
   - Test: `GET https://{domain}/resource/{dataset_id}.json?$limit=2`
   - Date format: ISO 8601 strings (`2024-01-15T00:00:00.000`)

2. **ArcGIS (FeatureServer/MapServer)** — `{host}/arcgis/rest/services/{name}/FeatureServer/{layer}/query`
   - Used by: DC, Portland, Baltimore, Denver, Minneapolis, Las Vegas, Mesa
   - Discovery: ArcGIS Hub search or direct REST endpoint browse
   - Test: `GET {endpoint}?where=1=1&outFields=*&resultRecordCount=2&f=json`
   - Date format: Usually epoch milliseconds (e.g., `1548633600000`)

3. **CKAN (datastore_search)** — `{domain}/api/3/action/datastore_search`
   - Used by: Boston
   - Discovery: `{domain}/api/3/action/package_search?q=building+permit`
   - Test: `GET {domain}/api/3/action/datastore_search?resource_id={id}&limit=2`
   - Date format: Varies

4. **CARTO (SQL API)** — `{domain}/api/v2/sql?q=SELECT...`
   - Used by: Philadelphia
   - Discovery: Manual — CARTO portals are usually custom
   - Test: `GET {domain}/api/v2/sql?q=SELECT * FROM {table} LIMIT 2&format=json`

### The Discovery Process

#### Step 1: Identify the city's open data portal
Search for `"{city name}" open data portal building permits` or check these common patterns:
- `data.{city}.gov` (Socrata)
- `{city}.data.socrata.com` (Socrata)
- `opendata.{city}.gov` (ArcGIS Hub or CKAN)
- `{city}-{state}.opendata.arcgis.com` (ArcGIS Hub)
- `gis.{city}.gov/arcgis/rest/services` (ArcGIS direct)

#### Step 2: Find the building permit dataset
- **Socrata**: Use the catalog API or browse the portal
- **ArcGIS Hub**: Search the hub page for "permit" or "building"
- **ArcGIS Direct**: Browse `/arcgis/rest/services` directory, look for `Permits`, `Building`, `Development` services
- **CKAN**: Use package_search API

#### Step 3: Get the API endpoint and test it
- Fetch 2-3 records to confirm data format
- Map fields to our standard schema (see below)
- Check if date field is ISO or epoch

#### Step 4: Wire into city_configs.py
- Add entry with proper field_map
- Set `date_format: "epoch"` if dates are milliseconds
- Set `active: True`
- Push and let the autonomy engine bootstrap it

### Standard Field Map Schema
```python
"field_map": {
    "permit_number": "",    # Required — unique permit ID
    "permit_type": "",      # e.g., "Building", "Electrical", "Plumbing"
    "work_type": "",        # e.g., "New Construction", "Renovation"
    "address": "",          # Required — street address
    "zip": "",              # Zip code
    "owner_name": "",       # Property owner
    "contact_name": "",     # Contractor/applicant — HIGH VALUE
    "contact_phone": "",    # Contractor phone — HIGHEST VALUE
    "filing_date": "",      # Required — issue or filing date
    "status": "",           # Permit status
    "estimated_cost": "",   # Project valuation
    "description": "",      # Work description
}
```

## Automated Discovery Script

`discover_endpoints.py` automates Steps 2-3 for known portal domains. Run on Render shell:
```bash
python3 discover_endpoints.py
```

It tests candidate endpoints, uses Socrata Catalog API, and outputs suggested `city_configs.py` entries.

## Criteria for Prioritizing Cities

### Tier 1 (Top 50 by population — highest ROI)
These cities have the most contractors and the most permit volume. Focus here first.

**Currently active:** NYC, Chicago, LA, Austin, Seattle, SF, Atlanta, Nashville, Denver, Portland, DC, Baltimore, Boston, Philly, Minneapolis, Orlando, Las Vegas

**Next targets (confirmed portals exist but endpoint needs work):**
- Fort Worth — Socrata `data.fortworthtexas.gov` (Development Permits dataset `quz7-xnsy` found via catalog)
- Detroit — Socrata `data.detroitmi.gov` (domain responds but dataset IDs need fresh discovery)
- Columbus — Unknown platform (guessed Socrata domain doesn't exist)
- Charlotte — Unknown platform (Mecklenburg County GIS didn't respond)
- Indianapolis — Unknown platform (guessed ArcGIS didn't respond)
- Jacksonville — Unknown platform (guessed Socrata domain doesn't exist)
- San Jose — Unknown platform (guessed Socrata domain doesn't exist)

**Hard targets (no public API found):**
- Sacramento, Memphis, Louisville, Milwaukee, Tucson, Cleveland, Tampa, Virginia Beach, Albuquerque, Omaha, Fresno, Long Beach, St. Louis, Oklahoma City

### Tier 2 (Cities 51-200 by population)
Mid-size cities. Many use county-level portals or proprietary permitting software (Accela, Tyler Technologies) with no public API.

### Tier 3 (Cities 201-2000 by population)
Smaller cities. Very few have open data portals. Best approach:
- **State-level bulk sources** (like NJ statewide permits) that cover hundreds of cities at once
- **County-level ArcGIS portals** that aggregate multiple cities
- **ArcGIS Hub bulk discovery** — many counties publish permit layers

## Scaling Strategy for 2,000 Cities

### Phase 1: Fix broken top-50 endpoints (current work)
- Run `discover_endpoints.py` on Render
- Browser-verify the ~7 "next target" cities above
- Wire confirmed endpoints
- **Expected yield: 5-10 more cities**

### Phase 2: State-level bulk sources
Many states aggregate permit data. One state source = dozens of cities.
- **New Jersey**: Already have `nj_statewide` (covers ~100+ NJ cities)
- **California**: Cal OSHA or HCD may have statewide data
- **Texas**: TDLR has statewide contractor data
- **Florida**: May have statewide building department data
- **Research needed**: Check each state's open data portal for statewide permit aggregations

### Phase 3: County-level ArcGIS Hub discovery
Counties often publish GIS layers that include permits for all cities in the county. One county source = 5-30 cities.
- Use ArcGIS Hub search: `https://hub.arcgis.com/search?q=building+permit&collection=Dataset`
- Filter by state/county
- Target counties containing multiple top-2000 cities

### Phase 4: Automated portal discovery at scale
Build a script that:
1. Takes a list of top 2,000 cities by population
2. For each city, tries common open data portal domain patterns
3. If a portal responds, searches for permit datasets via API
4. Tests discovered endpoints automatically
5. Outputs ready-to-wire city_configs entries

This is essentially a beefed-up version of `discover_endpoints.py` with a city population database and domain-guessing logic.

### Phase 5: Manual research for remaining cities
For cities with no discoverable API:
- Check if the city uses Accela (common proprietary system) — some expose a citizen portal with scrapeable data
- Check if the county has an ArcGIS layer that includes the city
- Check if a state-level source covers the city
- Accept that some cities simply don't publish permit data publicly

## Key Metrics
- **Coverage rate**: What % of top-N cities have active data sources?
- **Freshness**: How recent is the latest permit in each city?
- **Richness**: Does the source include contractor names/phones? (highest value for lead gen)
- **Volume**: How many permits per week does each city generate?

## Files
- `city_configs.py` — All city endpoint configurations
- `discover_endpoints.py` — Automated endpoint discovery script
- `server.py` — Main application with autonomy engine
- `DISCOVERY_PLAYBOOK.md` — This file
