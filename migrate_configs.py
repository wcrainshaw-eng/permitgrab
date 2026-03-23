#!/usr/bin/env python3
"""
PermitGrab V12.54 — Migrate Configs
Moves every entry in CITY_REGISTRY and BULK_SOURCES from city_configs.py
into the city_sources SQLite table.

Run on Render shell after seed_us_cities.py and seed_us_counties.py:
  python3 migrate_configs.py
"""

import json
import db as permitdb


def main():
    print("=" * 60)
    print("PermitGrab V12.54 — Migrate Configs")
    print("=" * 60)

    # Initialize database
    permitdb.init_db()
    conn = permitdb.get_connection()

    # Import city_configs
    try:
        from city_configs import CITY_REGISTRY, BULK_SOURCES
    except ImportError as e:
        print(f"[Migrate] ERROR: Could not import city_configs: {e}")
        return

    city_count = 0
    bulk_count = 0
    skipped = 0

    # Migrate CITY_REGISTRY
    print(f"[Migrate] Processing {len(CITY_REGISTRY)} entries from CITY_REGISTRY...")

    for key, config in CITY_REGISTRY.items():
        if not config.get('active', False):
            continue

        try:
            # Build field_map JSON
            field_map = config.get('field_map', {})
            if isinstance(field_map, dict):
                field_map_json = json.dumps(field_map)
            else:
                field_map_json = '{}'

            conn.execute("""
                INSERT INTO city_sources (
                    source_key, name, state, platform, mode, endpoint, dataset_id,
                    field_map, date_field, city_field, limit_per_page, status,
                    discovery_score, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
                ON CONFLICT(source_key) DO UPDATE SET
                    name=excluded.name, endpoint=excluded.endpoint,
                    field_map=excluded.field_map, date_field=excluded.date_field,
                    status=excluded.status, discovery_score=excluded.discovery_score,
                    updated_at=datetime('now')
            """, (
                key,
                config.get('name', key),
                config.get('state', ''),
                config.get('platform', 'socrata'),
                'city',
                config.get('endpoint', ''),
                config.get('dataset_id'),
                field_map_json,
                config.get('date_field'),
                None,  # city_field is None for individual cities
                config.get('limit', 2000),
                'active',
                100,  # High score for manually configured sources
            ))
            city_count += 1
        except Exception as e:
            print(f"[Migrate] Error migrating {key}: {e}")
            skipped += 1

    conn.commit()
    print(f"[Migrate] Migrated {city_count} city sources")

    # Migrate BULK_SOURCES
    print(f"[Migrate] Processing {len(BULK_SOURCES)} entries from BULK_SOURCES...")

    for key, config in BULK_SOURCES.items():
        if not config.get('active', False):
            continue

        try:
            # Build field_map JSON
            field_map = config.get('field_map', {})
            if isinstance(field_map, dict):
                field_map_json = json.dumps(field_map)
            else:
                field_map_json = '{}'

            conn.execute("""
                INSERT INTO city_sources (
                    source_key, name, state, platform, mode, endpoint, dataset_id,
                    field_map, date_field, city_field, limit_per_page, status,
                    discovery_score, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
                ON CONFLICT(source_key) DO UPDATE SET
                    name=excluded.name, endpoint=excluded.endpoint,
                    field_map=excluded.field_map, date_field=excluded.date_field,
                    city_field=excluded.city_field,
                    status=excluded.status, discovery_score=excluded.discovery_score,
                    updated_at=datetime('now')
            """, (
                key,
                config.get('name', key),
                config.get('state', ''),
                config.get('platform', 'socrata'),
                'bulk',
                config.get('endpoint', ''),
                config.get('dataset_id'),
                field_map_json,
                config.get('date_field'),
                config.get('city_field'),
                config.get('limit', 50000),
                'active',
                100,  # High score for manually configured sources
            ))
            bulk_count += 1
        except Exception as e:
            print(f"[Migrate] Error migrating bulk {key}: {e}")
            skipped += 1

    conn.commit()
    print(f"[Migrate] Migrated {bulk_count} bulk sources")

    # Verify
    total = conn.execute("SELECT COUNT(*) as cnt FROM city_sources").fetchone()['cnt']
    active = conn.execute("SELECT COUNT(*) as cnt FROM city_sources WHERE status='active'").fetchone()['cnt']
    city_mode = conn.execute("SELECT COUNT(*) as cnt FROM city_sources WHERE mode='city'").fetchone()['cnt']
    bulk_mode = conn.execute("SELECT COUNT(*) as cnt FROM city_sources WHERE mode='bulk'").fetchone()['cnt']

    print("=" * 60)
    print("SUMMARY:")
    print(f"  City sources migrated: {city_count}")
    print(f"  Bulk sources migrated: {bulk_count}")
    print(f"  Skipped (errors): {skipped}")
    print(f"  Total in city_sources: {total}")
    print(f"  Active: {active}")
    print(f"  Mode=city: {city_mode}")
    print(f"  Mode=bulk: {bulk_mode}")
    print("=" * 60)

    # Show sample data
    print("\n[Migrate] Sample city sources:")
    samples = conn.execute("SELECT source_key, name, state, platform FROM city_sources WHERE mode='city' LIMIT 5").fetchall()
    for s in samples:
        print(f"  - {s['source_key']}: {s['name']}, {s['state']} ({s['platform']})")

    print("\n[Migrate] Sample bulk sources:")
    samples = conn.execute("SELECT source_key, name, state, platform FROM city_sources WHERE mode='bulk' LIMIT 5").fetchall()
    for s in samples:
        print(f"  - {s['source_key']}: {s['name']}, {s['state']} ({s['platform']})")


if __name__ == '__main__':
    main()
