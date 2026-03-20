#!/usr/bin/env python3
"""
PermitGrab - Deactivate Broken Individual City Configs
V12.34: Cleans up city_configs.py based on endpoint_validation.json results.

Usage:
  python deactivate_broken.py                    # Preview changes
  python deactivate_broken.py --apply            # Apply changes
  python deactivate_broken.py --validation FILE  # Use custom validation file
"""

import json
import os
import sys
import argparse
import re
from datetime import datetime

# Categories of broken endpoints to deactivate
DEACTIVATE_CATEGORIES = [
    'dead_url',      # 404, connection refused, DNS errors
    'timeout',       # Request timeouts
    'error',         # Server errors, auth errors
    'no_records',    # Endpoint works but returns no data
]

# Categories where auto-fix might help (don't deactivate yet)
FIXABLE_CATEGORIES = [
    'wrong_fields',  # Field names changed - try auto-fix first
]


def load_validation_results(filepath):
    """Load endpoint validation results."""
    if not os.path.exists(filepath):
        print(f"Error: Validation file not found: {filepath}")
        sys.exit(1)

    with open(filepath, 'r') as f:
        return json.load(f)


def get_cities_to_deactivate(validation):
    """Get list of cities that should be deactivated."""
    to_deactivate = {}

    for category in DEACTIVATE_CATEGORIES:
        cities = validation.get(category, [])
        for city in cities:
            if isinstance(city, dict):
                slug = city.get('slug', city.get('city', ''))
                reason = city.get('error', city.get('reason', category))
            else:
                slug = city
                reason = category

            if slug:
                to_deactivate[slug] = {
                    'reason': reason,
                    'category': category,
                }

    return to_deactivate


def preview_deactivations(to_deactivate, validation):
    """Show what would be deactivated."""
    print("\n" + "=" * 60)
    print("CITIES TO DEACTIVATE")
    print("=" * 60)

    by_category = {}
    for slug, info in to_deactivate.items():
        cat = info['category']
        if cat not in by_category:
            by_category[cat] = []
        by_category[cat].append(slug)

    for category, cities in sorted(by_category.items()):
        print(f"\n{category.upper()}: {len(cities)} cities")
        for city in sorted(cities)[:20]:
            print(f"  - {city}")
        if len(cities) > 20:
            print(f"  ... and {len(cities) - 20} more")

    # Show fixable cities
    print("\n" + "-" * 40)
    print("FIXABLE (not deactivating):")
    print("-" * 40)

    for category in FIXABLE_CATEGORIES:
        cities = validation.get(category, [])
        print(f"\n{category.upper()}: {len(cities)} cities")
        print("  These may be recoverable with field_map fixes.")

    # Show working cities
    working = validation.get('working', [])
    print(f"\nWORKING: {len(working)} cities")

    return len(to_deactivate)


def update_city_configs(to_deactivate, dry_run=True):
    """Update city_configs.py to deactivate broken cities."""
    config_path = 'city_configs.py'

    if not os.path.exists(config_path):
        print(f"Error: {config_path} not found")
        return False

    with open(config_path, 'r') as f:
        content = f.read()

    changes_made = 0
    new_content = content

    for slug, info in to_deactivate.items():
        # Pattern to match the city config entry
        # Look for "slug": { ... "active": True ... }
        pattern = rf'("{slug}":\s*\{{[^}}]*)"active":\s*True'

        if re.search(pattern, new_content, re.DOTALL):
            # Replace active: True with active: False and add note
            replacement = rf'\1"active": False  # V12.34: Deactivated ({info["category"]})'
            new_content = re.sub(pattern, replacement, new_content, flags=re.DOTALL)
            changes_made += 1

            if changes_made <= 10:  # Show first 10
                print(f"  Deactivating: {slug} ({info['category']})")

    if changes_made > 10:
        print(f"  ... and {changes_made - 10} more")

    print(f"\nTotal configs to deactivate: {changes_made}")

    if dry_run:
        print("\n[DRY RUN] No changes written. Use --apply to apply changes.")
        return True

    # Write changes
    if changes_made > 0:
        # Backup original
        backup_path = f'city_configs.py.backup.{datetime.now().strftime("%Y%m%d_%H%M%S")}'
        with open(backup_path, 'w') as f:
            f.write(content)
        print(f"Backup saved to: {backup_path}")

        # Write updated config
        with open(config_path, 'w') as f:
            f.write(new_content)
        print(f"Updated: {config_path}")

        return True

    print("No changes needed.")
    return True


def generate_deactivation_report(to_deactivate, validation):
    """Generate a detailed report of deactivations."""
    report = {
        'generated_at': datetime.now().isoformat(),
        'total_deactivated': len(to_deactivate),
        'by_category': {},
        'deactivated_cities': to_deactivate,
        'working_count': len(validation.get('working', [])),
        'fixable_count': sum(len(validation.get(c, [])) for c in FIXABLE_CATEGORIES),
    }

    for slug, info in to_deactivate.items():
        cat = info['category']
        if cat not in report['by_category']:
            report['by_category'][cat] = 0
        report['by_category'][cat] += 1

    return report


def main():
    parser = argparse.ArgumentParser(description='Deactivate broken city configs')
    parser.add_argument('--apply', action='store_true', help='Apply changes (default is dry run)')
    parser.add_argument('--validation', default='data/endpoint_validation.json',
                        help='Path to validation results')
    parser.add_argument('--report', default='data/deactivation_report.json',
                        help='Path for deactivation report')
    args = parser.parse_args()

    print("=" * 60)
    print("PermitGrab Config Cleanup V12.34")
    print("=" * 60)

    # Load validation results
    print(f"\nLoading validation results from: {args.validation}")
    validation = load_validation_results(args.validation)

    # Get cities to deactivate
    to_deactivate = get_cities_to_deactivate(validation)

    if not to_deactivate:
        print("\nNo cities to deactivate based on validation results.")
        return

    # Preview changes
    count = preview_deactivations(to_deactivate, validation)

    # Update configs
    print("\n" + "=" * 60)
    print("UPDATING city_configs.py")
    print("=" * 60)

    success = update_city_configs(to_deactivate, dry_run=not args.apply)

    # Save report
    if success:
        report = generate_deactivation_report(to_deactivate, validation)

        os.makedirs(os.path.dirname(args.report) or '.', exist_ok=True)
        with open(args.report, 'w') as f:
            json.dump(report, f, indent=2)

        print(f"\nReport saved to: {args.report}")

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Cities deactivated: {len(to_deactivate)}")
    print(f"Cities still working: {len(validation.get('working', []))}")
    print(f"Cities potentially fixable: {sum(len(validation.get(c, [])) for c in FIXABLE_CATEGORIES)}")

    remaining = len(validation.get('working', [])) + sum(len(validation.get(c, [])) for c in FIXABLE_CATEGORIES)
    print(f"\nEstimated active configs after cleanup: ~{remaining}")


if __name__ == '__main__':
    main()
