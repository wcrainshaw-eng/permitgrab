#!/usr/bin/env python3
"""
PermitGrab V12.54 — Seed US Counties
Inserts ~3,143 US counties into us_counties table.
Calculates cities_in_county, population, and priority from us_cities data.
Pre-populates portal_domain from known state portals and ArcGIS hubs.

Run on Render shell after seed_us_cities.py:
  python3 seed_us_counties.py
"""

import math
import db as permitdb

# Known state-level Socrata portals (from discover_states.py)
STATE_PORTALS = {
    'AL': None,
    'AK': None,
    'AZ': 'data.az.gov',
    'AR': None,
    'CA': 'data.ca.gov',
    'CO': 'data.colorado.gov',
    'CT': 'data.ct.gov',
    'DE': 'data.delaware.gov',
    'FL': 'data.florida.gov',
    'GA': None,
    'HI': 'data.hawaii.gov',
    'ID': None,
    'IL': 'data.illinois.gov',
    'IN': 'data.in.gov',
    'IA': 'data.iowa.gov',
    'KS': None,
    'KY': 'data.ky.gov',
    'LA': 'data.la.gov',
    'ME': 'data.maine.gov',
    'MD': 'data.maryland.gov',
    'MA': 'data.mass.gov',
    'MI': 'data.michigan.gov',
    'MN': 'data.mn.gov',
    'MS': None,
    'MO': 'data.mo.gov',
    'MT': None,
    'NE': None,
    'NV': 'data.nv.gov',
    'NH': None,
    'NJ': 'data.nj.gov',
    'NM': None,
    'NY': 'data.ny.gov',
    'NC': None,
    'ND': None,
    'OH': None,
    'OK': 'data.ok.gov',
    'OR': 'data.oregon.gov',
    'PA': 'data.pa.gov',
    'RI': 'data.ri.gov',
    'SC': 'data.sc.gov',
    'SD': None,
    'TN': None,
    'TX': 'data.texas.gov',
    'UT': 'opendata.utah.gov',
    'VT': 'data.vermont.gov',
    'VA': 'data.virginia.gov',
    'WA': 'data.wa.gov',
    'WV': None,
    'WI': None,
    'WY': None,
    'DC': 'opendata.dc.gov',
}

# Known county-level ArcGIS hubs (from discover_counties.py)
ARCGIS_HUBS = {
    # Florida counties
    'Miami-Dade, FL': 'gis-mdc.opendata.arcgis.com',
    'Broward, FL': 'opendata.broward.org',
    'Palm Beach, FL': 'pbcgis.com',
    'Hillsborough, FL': 'open-hillsboroughcounty.hub.arcgis.com',
    'Orange, FL': 'data-ocfl.opendata.arcgis.com',
    'Pinellas, FL': 'egis-pinellas.opendata.arcgis.com',
    'Duval, FL': 'www.coj.net',
    'Lee, FL': 'gis-lee-county.hub.arcgis.com',
    'Polk, FL': 'polk-county.data.socrata.com',
    'Brevard, FL': 'gis-brevardcounty.opendata.arcgis.com',
    # California counties
    'Los Angeles, CA': 'data.lacounty.gov',
    'San Diego, CA': 'data.sandiego.gov',
    'Orange, CA': 'data-ocpw.opendata.arcgis.com',
    'Riverside, CA': 'gis1-countyofriverside.opendata.arcgis.com',
    'San Bernardino, CA': 'open.sbcounty.gov',
    'Santa Clara, CA': 'data.sccgov.org',
    'Alameda, CA': 'data.acgov.org',
    'Sacramento, CA': 'data.saccounty.net',
    'Contra Costa, CA': 'data.contracosta.ca.gov',
    'Fresno, CA': 'gis-fresnocounty.hub.arcgis.com',
    'San Francisco, CA': 'data.sfgov.org',
    'Kern, CA': 'data.kerncounty.com',
    'Ventura, CA': 'data.ventura.org',
    'San Mateo, CA': 'data.smcgov.org',
    'San Joaquin, CA': 'sjmap-sjcgis.opendata.arcgis.com',
    # Texas counties
    'Harris, TX': 'pdata.hcad.org',
    'Dallas, TX': 'www.dallascounty.org',
    'Tarrant, TX': 'data.tarrantcounty.com',
    'Bexar, TX': 'data.sanantonio.gov',
    'Travis, TX': 'data.traviscountytx.gov',
    'Collin, TX': 'gis.collincountytx.gov',
    'Hidalgo, TX': 'opendata.hidalgocounty.us',
    'El Paso, TX': 'data-elpaso.opendata.arcgis.com',
    'Fort Bend, TX': 'gis-fbctx.opendata.arcgis.com',
    'Denton, TX': 'gis-dentoncounty.opendata.arcgis.com',
    # Arizona
    'Maricopa, AZ': 'geodata-maricopa.opendata.arcgis.com',
    'Pima, AZ': 'gisdata.pima.gov',
    # Nevada
    'Clark, NV': 'opengis-clarkcounty.opendata.arcgis.com',
    # Georgia
    'Fulton, GA': 'gis-fultoncountyga.opendata.arcgis.com',
    'Gwinnett, GA': 'opendata.gwinnettcounty.com',
    'Cobb, GA': 'opendata.cobbcounty.org',
    'DeKalb, GA': 'gis-dekalbcounty.opendata.arcgis.com',
    # North Carolina
    'Mecklenburg, NC': 'data.charlottenc.gov',
    'Wake, NC': 'data-wake.opendata.arcgis.com',
    'Guilford, NC': 'data-guilford.opendata.arcgis.com',
    'Forsyth, NC': 'data-forsyth.opendata.arcgis.com',
    # Virginia
    'Fairfax, VA': 'data-fairfaxcountygis.opendata.arcgis.com',
    'Virginia Beach, VA': 'data.vbgov.com',
    # Maryland
    'Montgomery, MD': 'data.montgomerycountymd.gov',
    'Prince Georges, MD': 'gis-pgcdata.opendata.arcgis.com',
    'Baltimore, MD': 'data.baltimorecountymd.gov',
    'Anne Arundel, MD': 'data.aacounty.org',
    # Colorado
    'Denver, CO': 'www.denvergov.org',
    'Arapahoe, CO': 'data-arapahoe.opendata.arcgis.com',
    'Jefferson, CO': 'data-jeffco.opendata.arcgis.com',
    'Adams, CO': 'opendata-adamscounty.hub.arcgis.com',
    'El Paso, CO': 'data-elpasoco.opendata.arcgis.com',
    'Douglas, CO': 'gis-douglas.opendata.arcgis.com',
    # Pennsylvania
    'Philadelphia, PA': 'opendataphilly.org',
    'Allegheny, PA': 'data.alleghenycounty.us',
    'Montgomery, PA': 'data-montcopa.opendata.arcgis.com',
    # Ohio
    'Franklin, OH': 'opendata.columbus.gov',
    'Cuyahoga, OH': 'data-cuyahoga.opendata.arcgis.com',
    'Hamilton, OH': 'data.cincinnati-oh.gov',
    # Michigan
    'Wayne, MI': 'data.waynecounty.com',
    'Oakland, MI': 'accessoakland.oakgov.com',
    'Macomb, MI': 'opendata-macombgov.hub.arcgis.com',
    # Washington
    'King, WA': 'data.kingcounty.gov',
    'Pierce, WA': 'gisdata.piercecowa.opendata.arcgis.com',
    'Snohomish, WA': 'snoco-gis.opendata.arcgis.com',
    # Oregon
    'Multnomah, OR': 'gis-pdx.opendata.arcgis.com',
    'Washington, OR': 'geo-washcoweb.opendata.arcgis.com',
    'Clackamas, OR': 'data-clackamas.opendata.arcgis.com',
    # Massachusetts
    'Middlesex, MA': None,  # No unified county portal
    'Suffolk, MA': 'data.boston.gov',
    'Worcester, MA': None,
    # New York
    'Kings (Brooklyn), NY': 'data.cityofnewyork.us',
    'Queens, NY': 'data.cityofnewyork.us',
    'New York (Manhattan), NY': 'data.cityofnewyork.us',
    'Bronx, NY': 'data.cityofnewyork.us',
    'Richmond (Staten Island), NY': 'data.cityofnewyork.us',
    'Nassau, NY': 'data.nassaucountyny.gov',
    'Westchester, NY': 'westchestergis.opendata.arcgis.com',
    'Erie, NY': 'data2.erie.gov',
    # Illinois
    'Cook, IL': 'datacatalog.cookcountyil.gov',
    'DuPage, IL': 'gis.dupageco.org',
    'Lake, IL': 'data-lakecountyil.opendata.arcgis.com',
    'Will, IL': 'gis-willcounty.hub.arcgis.com',
    # New Jersey (state-level NJ covers all, but some have county data too)
    'Bergen, NJ': None,
    'Middlesex, NJ': None,
    'Essex, NJ': None,
    'Hudson, NJ': None,
    # Tennessee
    'Shelby, TN': 'data.memphistn.gov',
    'Davidson, TN': 'data.nashville.gov',
    'Knox, TN': 'knoxgis.maps.arcgis.com',
    # Indiana
    'Marion, IN': 'data.indy.gov',
    'Lake, IN': None,
    # Missouri
    'St. Louis, MO': 'data.stlouis-mo.gov',
    'Jackson, MO': None,
    # Wisconsin
    'Milwaukee, WI': 'data.milwaukee.gov',
    'Dane, WI': 'data-countyofdane.opendata.arcgis.com',
    # Minnesota
    'Hennepin, MN': 'gis-hennepin.opendata.arcgis.com',
    'Ramsey, MN': 'gis.ramseycounty.us',
    # Utah
    'Salt Lake, UT': 'opendata.gis.utah.gov',
}

# States with bulk statewide coverage (from city_configs.py BULK_SOURCES)
STATEWIDE_BULK_STATES = ['NJ']


def main():
    print("=" * 60)
    print("PermitGrab V12.54 — Seed US Counties")
    print("=" * 60)

    # Initialize database
    permitdb.init_db()
    conn = permitdb.get_connection()

    # Check if us_cities has data
    city_count = conn.execute("SELECT COUNT(*) as cnt FROM us_cities").fetchone()['cnt']
    if city_count == 0:
        print("[Seed] ERROR: us_cities is empty. Run seed_us_cities.py first.")
        return

    print(f"[Seed] Found {city_count} cities in us_cities table")

    # Get distinct county + state combos from us_cities
    counties = conn.execute("""
        SELECT
            county,
            state,
            county_fips,
            COUNT(*) as cities_in_county,
            SUM(population) as total_population
        FROM us_cities
        WHERE county IS NOT NULL AND county != ''
        GROUP BY county_fips
    """).fetchall()

    print(f"[Seed] Found {len(counties)} distinct counties from us_cities")

    inserted = 0
    skipped = 0

    for county_row in counties:
        county_name = county_row['county']
        state = county_row['state']
        fips = county_row['county_fips']
        cities_in_county = county_row['cities_in_county']
        population = county_row['total_population'] or 0

        if not county_name or not state:
            skipped += 1
            continue

        # Generate slug-like fips if missing
        if not fips:
            fips = f"{state}-{county_name.lower().replace(' ', '-')}"

        # Calculate priority: cities_in_county * ln(population + 1)
        # Higher priority (lower number) = more valuable to search first
        if population > 0:
            priority = int(10000 - (cities_in_county * math.log(population + 1)))
        else:
            priority = 99999

        # Clamp to positive
        if priority < 1:
            priority = 1

        # Look up portal_domain
        portal_domain = None

        # Check state-level portals
        if STATE_PORTALS.get(state):
            portal_domain = STATE_PORTALS[state]

        # Check county-level ArcGIS hubs (overrides state portal)
        county_key = f"{county_name}, {state}"
        if ARCGIS_HUBS.get(county_key):
            portal_domain = ARCGIS_HUBS[county_key]

        # Determine initial status
        status = 'not_started'
        if state in STATEWIDE_BULK_STATES:
            status = 'covered_by_state'

        try:
            conn.execute("""
                INSERT OR IGNORE INTO us_counties (
                    county_name, state, fips, population, cities_in_county,
                    portal_domain, status, priority
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                county_name, state, fips, population, cities_in_county,
                portal_domain, status, priority
            ))
            if conn.execute("SELECT changes()").fetchone()[0] > 0:
                inserted += 1
            else:
                skipped += 1
        except Exception as e:
            print(f"[Seed] Error inserting {county_name}, {state}: {e}")
            skipped += 1

    conn.commit()
    print(f"[Seed] Inserted: {inserted}, Skipped (duplicates): {skipped}")

    # Re-calculate priorities based on ranking
    print("[Seed] Recalculating priorities...")
    rows = conn.execute("""
        SELECT id, cities_in_county, population
        FROM us_counties
        ORDER BY (cities_in_county * 1.0 * CASE WHEN population > 0 THEN population ELSE 1 END) DESC
    """).fetchall()

    for rank, row in enumerate(rows, start=1):
        conn.execute(
            "UPDATE us_counties SET priority = ? WHERE id = ?",
            (rank, row['id'])
        )

    conn.commit()
    print(f"[Seed] Priorities assigned by cities * population rank")

    # Mark existing bulk sources from city_configs.py
    try:
        from city_configs import BULK_SOURCES
        existing_bulk = 0
        for source_key, config in BULK_SOURCES.items():
            if not config.get('active', False):
                continue
            # Try to find matching county
            # Source keys often contain county name
            source_state = config.get('state', '')
            if source_state:
                # Mark any matching county as has_data
                cursor = conn.execute("""
                    UPDATE us_counties
                    SET status = 'has_data', source_key = ?
                    WHERE state = ? AND status NOT IN ('has_data', 'covered_by_state')
                    AND (
                        LOWER(county_name) LIKE ? OR
                        LOWER(?) LIKE '%' || LOWER(county_name) || '%'
                    )
                """, (source_key, source_state, f"%{source_key}%", source_key))
                existing_bulk += cursor.rowcount
        conn.commit()
        print(f"[Seed] Marked {existing_bulk} counties as has_data from BULK_SOURCES")
    except ImportError:
        print("[Seed] city_configs.py not found, skipping bulk source matching")

    # Summary
    total = conn.execute("SELECT COUNT(*) as cnt FROM us_counties").fetchone()['cnt']
    with_portal = conn.execute("SELECT COUNT(*) as cnt FROM us_counties WHERE portal_domain IS NOT NULL").fetchone()['cnt']
    not_started = conn.execute("SELECT COUNT(*) as cnt FROM us_counties WHERE status='not_started'").fetchone()['cnt']
    has_data = conn.execute("SELECT COUNT(*) as cnt FROM us_counties WHERE status='has_data'").fetchone()['cnt']
    covered_by_state = conn.execute("SELECT COUNT(*) as cnt FROM us_counties WHERE status='covered_by_state'").fetchone()['cnt']

    print("=" * 60)
    print("SUMMARY:")
    print(f"  Total counties: {total}")
    print(f"  With known portals: {with_portal}")
    print(f"  Not started (to be searched): {not_started}")
    print(f"  Has data (bulk source exists): {has_data}")
    print(f"  Covered by state-level source: {covered_by_state}")
    print("=" * 60)


if __name__ == '__main__':
    main()
