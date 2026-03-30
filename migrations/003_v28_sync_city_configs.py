#!/usr/bin/env python3
"""
MIGRATION 003: V28 - Sync all active cities from city_configs.py to prod_cities table.

This ensures all V26/V27 cities added to city_configs.py are registered
in prod_cities for collection.

Usage:
    python migrations/003_v28_sync_city_configs.py
"""

import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db as permitdb
from city_configs import CITY_REGISTRY, BULK_SOURCES


def slugify(text):
    """Convert text to URL-safe slug."""
    return text.lower().replace(' ', '-').replace(',', '').replace('.', '')


def sync_cities():
    """Sync all active cities from CITY_REGISTRY to prod_cities."""
    synced = 0
    skipped = 0
    errors = []

    print("=" * 60)
    print("V28 MIGRATION: Sync city_configs.py -> prod_cities")
    print("=" * 60)

    for city_key, config in CITY_REGISTRY.items():
        if not config.get('active', False):
            skipped += 1
            continue

        city_name = config.get('name', '')
        state = config.get('state', '')
        platform = config.get('platform', '')
        slug = config.get('slug', slugify(city_name))

        if not city_name or not state:
            errors.append(f"{city_key}: Missing name or state")
            continue

        try:
            permitdb.upsert_prod_city(
                city=city_name,
                state=state,
                city_slug=slug,
                source_type=platform,
                source_id=city_key,
                source_scope='city',
                status='active',
                added_by='v28_migration',
                notes=f"V28: Synced from city_configs.py"
            )
            synced += 1
        except Exception as e:
            errors.append(f"{city_key}: {str(e)}")

    print(f"\nCITY_REGISTRY Results:")
    print(f"  Synced: {synced}")
    print(f"  Skipped (inactive): {skipped}")
    print(f"  Errors: {len(errors)}")

    return synced, skipped, errors


def sync_bulk_sources():
    """Sync all active bulk sources to prod_cities."""
    synced = 0
    skipped = 0
    errors = []

    for source_key, config in BULK_SOURCES.items():
        if not config.get('active', False):
            skipped += 1
            continue

        name = config.get('name', source_key)
        state = config.get('state', '')
        platform = config.get('platform', '')
        scope = config.get('mode', 'bulk')

        # Determine scope type
        if 'county' in source_key.lower() or 'County' in name:
            source_scope = 'county'
        elif 'state' in source_key.lower():
            source_scope = 'state'
        else:
            source_scope = 'bulk'

        slug = slugify(f"{name}-{state}") if state else slugify(name)

        try:
            permitdb.upsert_prod_city(
                city=name,
                state=state,
                city_slug=slug,
                source_type=platform,
                source_id=source_key,
                source_scope=source_scope,
                status='active',
                added_by='v28_migration',
                notes=f"V28: Bulk source synced from city_configs.py"
            )
            synced += 1
        except Exception as e:
            errors.append(f"{source_key}: {str(e)}")

    print(f"\nBULK_SOURCES Results:")
    print(f"  Synced: {synced}")
    print(f"  Skipped (inactive): {skipped}")
    print(f"  Errors: {len(errors)}")

    return synced, skipped, errors


def main():
    # Initialize database connection
    permitdb.init_db()

    # Sync cities
    city_synced, city_skipped, city_errors = sync_cities()

    # Sync bulk sources
    bulk_synced, bulk_skipped, bulk_errors = sync_bulk_sources()

    # Summary
    print("\n" + "=" * 60)
    print("MIGRATION COMPLETE")
    print("=" * 60)
    print(f"Total synced: {city_synced + bulk_synced}")
    print(f"Total skipped: {city_skipped + bulk_skipped}")

    all_errors = city_errors + bulk_errors
    if all_errors:
        print(f"\nErrors ({len(all_errors)}):")
        for e in all_errors[:10]:  # Show first 10
            print(f"  - {e}")
        if len(all_errors) > 10:
            print(f"  ... and {len(all_errors) - 10} more")

    # Verification query
    conn = permitdb.get_connection()
    total = conn.execute("SELECT COUNT(*) as cnt FROM prod_cities").fetchone()['cnt']
    active = conn.execute("SELECT COUNT(*) as cnt FROM prod_cities WHERE status='active'").fetchone()['cnt']

    print(f"\nprod_cities table now has:")
    print(f"  Total entries: {total}")
    print(f"  Active: {active}")


if __name__ == '__main__':
    main()
