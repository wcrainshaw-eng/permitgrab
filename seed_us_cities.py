#!/usr/bin/env python3
"""
PermitGrab V12.54 — Seed US Cities
Downloads Census population data and inserts ~19,500 cities into us_cities table.
Matches existing active cities from city_configs.py and marks them as active.

Run on Render shell after V12.54a deploy:
  python3 seed_us_cities.py
"""

import csv
import io
import os
import re
import urllib.request
import db as permitdb

# Census Bureau CSV with city populations
# sub-est2024.csv contains incorporated places
CENSUS_URL = "https://www2.census.gov/programs-surveys/popest/datasets/2020-2024/cities/totals/sub-est2024.csv"

# Fallback: SimpleMaps free data
SIMPLEMAPS_URL = "https://simplemaps.com/static/data/us-cities-demo/uscities.csv"


def slugify(text):
    """Convert text to URL-safe slug."""
    text = text.lower().strip()
    text = re.sub(r'[^a-z0-9\s-]', '', text)
    text = re.sub(r'[\s]+', '-', text)
    text = re.sub(r'-+', '-', text)
    return text.strip('-')


def download_csv(url):
    """Download CSV data from URL."""
    print(f"[Seed] Downloading: {url}")
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'PermitGrab/1.0'})
        with urllib.request.urlopen(req, timeout=60) as response:
            return response.read().decode('utf-8', errors='ignore')
    except Exception as e:
        print(f"[Seed] Download failed: {e}")
        return None


def parse_census_csv(csv_data):
    """Parse Census sub-est CSV format.
    Returns list of dicts with: city_name, state, county, county_fips, population, lat, lng
    """
    cities = []
    reader = csv.DictReader(io.StringIO(csv_data))

    for row in reader:
        # Census file has STATE, NAME, POPESTIMATE2024, etc.
        # NAME format: "City name city, State" or "Town name town, State"
        name_raw = row.get('NAME', '')
        state = row.get('STNAME', '')
        pop_str = row.get('POPESTIMATE2024') or row.get('POPESTIMATE2023') or row.get('POPESTIMATE', '0')

        # Skip state-level rows and non-incorporated places
        if not name_raw or not state:
            continue

        # Clean city name - remove ", State" suffix and type suffixes
        city_name = name_raw
        if ',' in city_name:
            city_name = city_name.split(',')[0].strip()

        # Remove common suffixes like "city", "town", "village", "CDP"
        for suffix in [' city', ' town', ' village', ' CDP', ' borough', ' municipality']:
            if city_name.lower().endswith(suffix):
                city_name = city_name[:-len(suffix)].strip()

        try:
            population = int(pop_str.replace(',', ''))
        except (ValueError, TypeError):
            population = 0

        # Get state abbreviation
        state_abbrev = get_state_abbrev(state)
        if not state_abbrev:
            continue

        # Get county info if available
        county = row.get('COUNTY', '') or row.get('CTYNAME', '')
        county_fips = row.get('COUNTY') or ''
        state_fips = row.get('STATE') or ''
        if state_fips and county_fips:
            full_fips = f"{state_fips.zfill(2)}{county_fips.zfill(3)}"
        else:
            full_fips = ''

        # Get coordinates if available
        lat = row.get('LATITUDE') or row.get('INTPTLAT') or None
        lng = row.get('LONGITUDE') or row.get('INTPTLONG') or None

        try:
            lat = float(lat) if lat else None
            lng = float(lng) if lng else None
        except (ValueError, TypeError):
            lat, lng = None, None

        slug = f"{slugify(city_name)}-{state_abbrev.lower()}"

        cities.append({
            'city_name': city_name,
            'state': state_abbrev,
            'county': county.replace(' County', '').strip() if county else None,
            'county_fips': full_fips if full_fips else None,
            'population': population,
            'latitude': lat,
            'longitude': lng,
            'slug': slug,
        })

    return cities


def parse_simplemaps_csv(csv_data):
    """Parse SimpleMaps uscities.csv format as fallback."""
    cities = []
    reader = csv.DictReader(io.StringIO(csv_data))

    for row in reader:
        city_name = row.get('city', '')
        state = row.get('state_id', '')
        county = row.get('county_name', '')
        county_fips = row.get('county_fips', '')
        pop_str = row.get('population', '0')
        lat = row.get('lat')
        lng = row.get('lng')

        if not city_name or not state:
            continue

        try:
            population = int(float(pop_str))
        except (ValueError, TypeError):
            population = 0

        try:
            lat = float(lat) if lat else None
            lng = float(lng) if lng else None
        except (ValueError, TypeError):
            lat, lng = None, None

        slug = f"{slugify(city_name)}-{state.lower()}"

        cities.append({
            'city_name': city_name,
            'state': state,
            'county': county.replace(' County', '').strip() if county else None,
            'county_fips': county_fips if county_fips else None,
            'population': population,
            'latitude': lat,
            'longitude': lng,
            'slug': slug,
        })

    return cities


def get_state_abbrev(state_name):
    """Convert full state name to 2-letter abbreviation."""
    states = {
        'Alabama': 'AL', 'Alaska': 'AK', 'Arizona': 'AZ', 'Arkansas': 'AR',
        'California': 'CA', 'Colorado': 'CO', 'Connecticut': 'CT', 'Delaware': 'DE',
        'Florida': 'FL', 'Georgia': 'GA', 'Hawaii': 'HI', 'Idaho': 'ID',
        'Illinois': 'IL', 'Indiana': 'IN', 'Iowa': 'IA', 'Kansas': 'KS',
        'Kentucky': 'KY', 'Louisiana': 'LA', 'Maine': 'ME', 'Maryland': 'MD',
        'Massachusetts': 'MA', 'Michigan': 'MI', 'Minnesota': 'MN', 'Mississippi': 'MS',
        'Missouri': 'MO', 'Montana': 'MT', 'Nebraska': 'NE', 'Nevada': 'NV',
        'New Hampshire': 'NH', 'New Jersey': 'NJ', 'New Mexico': 'NM', 'New York': 'NY',
        'North Carolina': 'NC', 'North Dakota': 'ND', 'Ohio': 'OH', 'Oklahoma': 'OK',
        'Oregon': 'OR', 'Pennsylvania': 'PA', 'Rhode Island': 'RI', 'South Carolina': 'SC',
        'South Dakota': 'SD', 'Tennessee': 'TN', 'Texas': 'TX', 'Utah': 'UT',
        'Vermont': 'VT', 'Virginia': 'VA', 'Washington': 'WA', 'West Virginia': 'WV',
        'Wisconsin': 'WI', 'Wyoming': 'WY', 'District of Columbia': 'DC',
        'Puerto Rico': 'PR',
    }
    return states.get(state_name, '')


def insert_cities(cities):
    """Insert cities into us_cities table."""
    conn = permitdb.get_connection()
    inserted = 0
    skipped = 0

    for city in cities:
        try:
            conn.execute("""
                INSERT OR IGNORE INTO us_cities (
                    city_name, state, county, county_fips, population,
                    latitude, longitude, slug, status, priority
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'not_started', 99999)
            """, (
                city['city_name'], city['state'], city['county'],
                city['county_fips'], city['population'],
                city['latitude'], city['longitude'], city['slug']
            ))
            if conn.execute("SELECT changes()").fetchone()[0] > 0:
                inserted += 1
            else:
                skipped += 1
        except Exception as e:
            print(f"[Seed] Error inserting {city['city_name']}: {e}")
            skipped += 1

    conn.commit()
    return inserted, skipped


def calculate_priorities():
    """Set priority based on population rank (1 = largest)."""
    conn = permitdb.get_connection()

    # Get all cities ordered by population desc
    rows = conn.execute("""
        SELECT id, population FROM us_cities ORDER BY population DESC
    """).fetchall()

    print(f"[Seed] Calculating priorities for {len(rows)} cities...")

    for rank, row in enumerate(rows, start=1):
        conn.execute(
            "UPDATE us_cities SET priority = ? WHERE id = ?",
            (rank, row['id'])
        )

    conn.commit()
    print(f"[Seed] Priorities assigned")


def match_existing_cities():
    """Match existing active cities from city_configs.py and mark as active."""
    try:
        from city_configs import CITY_REGISTRY
    except ImportError:
        print("[Seed] city_configs.py not found, skipping match")
        return 0

    conn = permitdb.get_connection()
    matched = 0

    for city_key, config in CITY_REGISTRY.items():
        if not config.get('active', False):
            continue

        city_name = config.get('name', '')
        state = config.get('state', '')

        if not city_name or not state:
            continue

        # Try exact slug match first
        expected_slug = f"{slugify(city_name)}-{state.lower()}"
        row = conn.execute(
            "SELECT id FROM us_cities WHERE slug = ?", (expected_slug,)
        ).fetchone()

        if row:
            conn.execute(
                "UPDATE us_cities SET status = 'active' WHERE id = ?",
                (row['id'],)
            )
            matched += 1
        else:
            # Try fuzzy match on city name + state
            row = conn.execute(
                "SELECT id FROM us_cities WHERE LOWER(city_name) = LOWER(?) AND state = ?",
                (city_name, state)
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE us_cities SET status = 'active' WHERE id = ?",
                    (row['id'],)
                )
                matched += 1
            else:
                print(f"[Seed] No match found for: {city_name}, {state}")

    conn.commit()
    return matched


def main():
    print("=" * 60)
    print("PermitGrab V12.54 — Seed US Cities")
    print("=" * 60)

    # Initialize database
    permitdb.init_db()

    # Try Census data first
    csv_data = download_csv(CENSUS_URL)
    if csv_data:
        cities = parse_census_csv(csv_data)
        print(f"[Seed] Parsed {len(cities)} cities from Census data")
    else:
        # Fallback to SimpleMaps
        print("[Seed] Census download failed, trying SimpleMaps...")
        csv_data = download_csv(SIMPLEMAPS_URL)
        if csv_data:
            cities = parse_simplemaps_csv(csv_data)
            print(f"[Seed] Parsed {len(cities)} cities from SimpleMaps data")
        else:
            print("[Seed] ERROR: Could not download city data")
            return

    if not cities:
        print("[Seed] ERROR: No cities parsed")
        return

    # Deduplicate by slug
    seen_slugs = set()
    unique_cities = []
    for city in cities:
        if city['slug'] not in seen_slugs:
            seen_slugs.add(city['slug'])
            unique_cities.append(city)

    print(f"[Seed] {len(unique_cities)} unique cities after deduplication")

    # Insert
    inserted, skipped = insert_cities(unique_cities)
    print(f"[Seed] Inserted: {inserted}, Skipped (duplicates): {skipped}")

    # Calculate priorities
    calculate_priorities()

    # Match existing active cities
    matched = match_existing_cities()
    print(f"[Seed] Matched {matched} existing active cities from city_configs.py")

    # Summary
    conn = permitdb.get_connection()
    total = conn.execute("SELECT COUNT(*) as cnt FROM us_cities").fetchone()['cnt']
    active = conn.execute("SELECT COUNT(*) as cnt FROM us_cities WHERE status='active'").fetchone()['cnt']
    not_started = conn.execute("SELECT COUNT(*) as cnt FROM us_cities WHERE status='not_started'").fetchone()['cnt']

    print("=" * 60)
    print(f"SUMMARY:")
    print(f"  Total cities: {total}")
    print(f"  Active (already have data): {active}")
    print(f"  Not started (to be searched): {not_started}")
    print("=" * 60)


if __name__ == '__main__':
    main()
