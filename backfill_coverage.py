#!/usr/bin/env python3
"""
PermitGrab V12.54 — Backfill Coverage
For existing bulk sources, identifies all cities they cover and marks
those us_cities rows as covered_by_county.

Run on Render shell after migrate_configs.py:
  python3 backfill_coverage.py
"""

import json
import re
import requests
import db as permitdb

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "PermitGrab/1.0 (permit lead aggregator)",
    "Accept": "application/json",
})


def slugify(text):
    """Convert text to URL-safe slug."""
    text = text.lower().strip()
    text = re.sub(r'[^a-z0-9\s-]', '', text)
    text = re.sub(r'[\s]+', '-', text)
    text = re.sub(r'-+', '-', text)
    return text.strip('-')


def normalize_city_name(name):
    """Normalize a city name for matching."""
    if not name:
        return ''
    name = name.strip().lower()
    # Remove common suffixes
    for suffix in [' city', ' town', ' village', ' borough', ' township']:
        if name.endswith(suffix):
            name = name[:-len(suffix)].strip()
    return name


def fetch_distinct_cities_socrata(endpoint, city_field, limit=1000):
    """Fetch distinct city names from a Socrata bulk source."""
    try:
        url = f"{endpoint}?$select=distinct {city_field}&$limit={limit}"
        resp = SESSION.get(url, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            cities = []
            for row in data:
                city_val = row.get(city_field, '')
                if city_val and isinstance(city_val, str):
                    cities.append(city_val.strip())
            return cities
    except Exception as e:
        print(f"[Backfill] Error fetching cities: {e}")
    return []


def fetch_distinct_cities_arcgis(endpoint, city_field, limit=1000):
    """Fetch distinct city names from an ArcGIS bulk source."""
    try:
        # Use outStatistics to get distinct values
        params = {
            'where': '1=1',
            'outFields': city_field,
            'returnDistinctValues': 'true',
            'resultRecordCount': limit,
            'f': 'json',
        }
        resp = SESSION.get(endpoint, params=params, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            features = data.get('features', [])
            cities = []
            for f in features:
                city_val = f.get('attributes', {}).get(city_field, '')
                if city_val and isinstance(city_val, str):
                    cities.append(city_val.strip())
            return cities
    except Exception as e:
        print(f"[Backfill] Error fetching ArcGIS cities: {e}")
    return []


def main():
    print("=" * 60)
    print("PermitGrab V12.54 — Backfill Coverage")
    print("=" * 60)

    # Initialize database
    permitdb.init_db()
    conn = permitdb.get_connection()

    # Get all active bulk sources with city_field
    bulk_sources = conn.execute("""
        SELECT * FROM city_sources
        WHERE mode='bulk' AND status='active' AND city_field IS NOT NULL
    """).fetchall()

    print(f"[Backfill] Found {len(bulk_sources)} active bulk sources with city_field")

    total_matched = 0

    for row in bulk_sources:
        source = dict(row)
        source_key = source['source_key']
        endpoint = source['endpoint']
        city_field = source['city_field']
        state = source.get('state', '')
        platform = source.get('platform', 'socrata')

        print(f"\n[Backfill] Processing: {source_key}")
        print(f"           Endpoint: {endpoint}")
        print(f"           City field: {city_field}")
        print(f"           State: {state}")

        # Fetch distinct cities from the source
        if platform == 'socrata':
            cities_in_source = fetch_distinct_cities_socrata(endpoint, city_field)
        elif platform == 'arcgis':
            cities_in_source = fetch_distinct_cities_arcgis(endpoint, city_field)
        else:
            print(f"           Unsupported platform: {platform}, skipping")
            continue

        print(f"           Found {len(cities_in_source)} distinct cities in source")

        if not cities_in_source:
            continue

        # Match against us_cities
        matched_slugs = []

        for city_name in cities_in_source:
            normalized = normalize_city_name(city_name)
            if not normalized:
                continue

            # Try exact match first
            if state:
                row = conn.execute("""
                    SELECT slug FROM us_cities
                    WHERE LOWER(city_name) = ? AND state = ?
                """, (normalized, state)).fetchone()
            else:
                row = conn.execute("""
                    SELECT slug FROM us_cities
                    WHERE LOWER(city_name) = ?
                """, (normalized,)).fetchone()

            if row:
                matched_slugs.append(row['slug'])
            else:
                # Try contains match for compound city names
                if state:
                    row = conn.execute("""
                        SELECT slug FROM us_cities
                        WHERE (LOWER(city_name) LIKE ? OR ? LIKE '%' || LOWER(city_name) || '%')
                        AND state = ?
                        LIMIT 1
                    """, (f"%{normalized}%", normalized, state)).fetchone()
                else:
                    row = conn.execute("""
                        SELECT slug FROM us_cities
                        WHERE LOWER(city_name) LIKE ?
                        LIMIT 1
                    """, (f"%{normalized}%",)).fetchone()

                if row:
                    matched_slugs.append(row['slug'])

        matched_slugs = list(set(matched_slugs))  # Dedupe
        print(f"           Matched {len(matched_slugs)} cities in us_cities")

        # Update us_cities to mark as covered
        updated = 0
        for slug in matched_slugs:
            cursor = conn.execute("""
                UPDATE us_cities
                SET status = 'covered_by_county', covered_by_source = ?
                WHERE slug = ? AND status IN ('not_started', 'no_data_available')
            """, (source_key, slug))
            updated += cursor.rowcount

        conn.commit()
        print(f"           Updated {updated} cities to covered_by_county")
        total_matched += updated

        # Update city_sources with covers_cities
        if matched_slugs:
            conn.execute("""
                UPDATE city_sources
                SET covers_cities = ?, updated_at = datetime('now')
                WHERE source_key = ?
            """, (json.dumps(matched_slugs[:100]), source_key))  # Limit to 100 for JSON size
            conn.commit()

    # Summary
    covered = conn.execute("SELECT COUNT(*) as cnt FROM us_cities WHERE status='covered_by_county'").fetchone()['cnt']
    not_started = conn.execute("SELECT COUNT(*) as cnt FROM us_cities WHERE status='not_started'").fetchone()['cnt']
    active = conn.execute("SELECT COUNT(*) as cnt FROM us_cities WHERE status='active'").fetchone()['cnt']

    print("\n" + "=" * 60)
    print("SUMMARY:")
    print(f"  Cities marked covered_by_county this run: {total_matched}")
    print(f"  Total covered_by_county: {covered}")
    print(f"  Total active (direct source): {active}")
    print(f"  Total not_started (remaining to search): {not_started}")
    print("=" * 60)


if __name__ == '__main__':
    main()
