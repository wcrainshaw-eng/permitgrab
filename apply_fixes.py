#!/usr/bin/env python3
"""
PermitGrab - Apply Endpoint Fixes
V12.31: Applies auto-generated fixes from validation results.

Usage:
  1. Download files from server:
     scp render:/app/data/suggested_fixes.json data/
     scp render:/app/data/endpoint_validation.json data/

  2. Run the script:
     python apply_fixes.py --preview        # Preview changes without applying
     python apply_fixes.py --apply          # Apply all fixes
     python apply_fixes.py --apply --phase 1  # Apply only Phase 1 (field_map fixes)
     python apply_fixes.py --apply --phase 2  # Apply only Phase 2 (discoveries)
     python apply_fixes.py --apply --phase 3  # Apply only Phase 3 (deactivate)
"""

import json
import os
import re
import sys
import argparse
from datetime import datetime

# Paths
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, "data")
CITY_CONFIGS_PATH = os.path.join(SCRIPT_DIR, "city_configs.py")
SUGGESTED_FIXES_PATH = os.path.join(DATA_DIR, "suggested_fixes.json")
VALIDATION_PATH = os.path.join(DATA_DIR, "endpoint_validation.json")

# Cities confirmed working - do not touch
WORKING_CITIES = {
    "cincinnati", "san_francisco", "austin", "los_angeles", "baton_rouge",
    "atlanta", "nashville", "raleigh", "orlando", "roseville", "cambridge",
    "framingham", "calgary", "urbana", "salt_lake_city", "dumfries",
    "auburn_wa", "honolulu", "henderson_nv", "pierce_county",
    "fort_collins", "camas"
}

# Common field name patterns for auto-mapping
FIELD_PATTERNS = {
    'permit_number': ['permit', 'permit_no', 'permit_num', 'permitnumber', 'permit_id', 'permnum', 'application'],
    'address': ['address', 'location', 'site_address', 'street', 'property_address', 'situs'],
    'estimated_cost': ['cost', 'value', 'valuation', 'amount', 'job_value', 'project_value', 'estimated'],
    'filing_date': ['date', 'issue_date', 'issued', 'filed', 'created', 'applied', 'permit_date'],
    'description': ['desc', 'description', 'work_desc', 'scope', 'project_desc', 'work_type'],
    'contractor_name': ['contractor', 'applicant', 'builder', 'owner', 'contact', 'company'],
    'status': ['status', 'state', 'permit_status', 'disposition'],
}


def auto_map_fields(actual_fields):
    """Suggest field mappings based on common patterns."""
    suggested = {}
    actual_lower = {f.lower(): f for f in actual_fields}

    for our_field, patterns in FIELD_PATTERNS.items():
        for pattern in patterns:
            for field_lower, field_actual in actual_lower.items():
                if pattern in field_lower:
                    suggested[our_field] = field_actual
                    break
            if our_field in suggested:
                break

    return suggested


def load_json(path):
    """Load JSON file, return None if not found."""
    if not os.path.exists(path):
        print(f"  File not found: {path}")
        return None
    with open(path, 'r') as f:
        return json.load(f)


def read_city_configs():
    """Read city_configs.py as text."""
    with open(CITY_CONFIGS_PATH, 'r') as f:
        return f.read()


def write_city_configs(content):
    """Write city_configs.py with backup."""
    # Create backup
    backup_path = CITY_CONFIGS_PATH + f".backup.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    with open(backup_path, 'w') as f:
        with open(CITY_CONFIGS_PATH, 'r') as orig:
            f.write(orig.read())
    print(f"  Backup saved to: {backup_path}")

    # Write new content
    with open(CITY_CONFIGS_PATH, 'w') as f:
        f.write(content)


def format_field_map(field_map, indent=12):
    """Format a field_map dict as Python code."""
    lines = ["{"]
    for key, value in field_map.items():
        lines.append(f'{" " * indent}"{key}": "{value}",')
    lines.append(f'{" " * (indent - 4)}}}')
    return "\n".join(lines)


def find_city_block(content, city_key):
    """Find the start and end positions of a city config block."""
    # Pattern to match the start of a city block
    pattern = rf'"{re.escape(city_key)}"\s*:\s*\{{'
    match = re.search(pattern, content)
    if not match:
        return None, None

    start = match.start()

    # Find the matching closing brace by counting braces
    pos = match.end()
    brace_count = 1
    while pos < len(content) and brace_count > 0:
        if content[pos] == '{':
            brace_count += 1
        elif content[pos] == '}':
            brace_count -= 1
        pos += 1

    return start, pos


def update_field_map(content, city_key, new_field_map):
    """Update the field_map for a city."""
    start, end = find_city_block(content, city_key)
    if start is None:
        return content, False

    city_block = content[start:end]

    # Find and replace the field_map in this block
    field_map_pattern = r'"field_map"\s*:\s*\{[^}]*\}'

    # Need to handle multi-line field_maps with nested content
    fm_match = re.search(r'"field_map"\s*:\s*\{', city_block)
    if not fm_match:
        return content, False

    fm_start = fm_match.start()
    fm_pos = fm_match.end()
    brace_count = 1
    while fm_pos < len(city_block) and brace_count > 0:
        if city_block[fm_pos] == '{':
            brace_count += 1
        elif city_block[fm_pos] == '}':
            brace_count -= 1
        fm_pos += 1

    # Build new field_map string
    new_fm_str = '"field_map": ' + format_field_map(new_field_map)

    # Replace in city block
    new_city_block = city_block[:fm_start] + new_fm_str + city_block[fm_pos:]

    # Replace in content
    new_content = content[:start] + new_city_block + content[end:]

    return new_content, True


def update_endpoint(content, city_key, new_endpoint, new_dataset_id=None):
    """Update the endpoint URL for a city."""
    start, end = find_city_block(content, city_key)
    if start is None:
        return content, False

    city_block = content[start:end]

    # Replace endpoint
    endpoint_pattern = r'"endpoint"\s*:\s*"[^"]*"'
    new_endpoint_str = f'"endpoint": "{new_endpoint}"'
    new_city_block = re.sub(endpoint_pattern, new_endpoint_str, city_block)

    # Replace dataset_id if provided
    if new_dataset_id:
        dataset_pattern = r'"dataset_id"\s*:\s*"[^"]*"'
        new_dataset_str = f'"dataset_id": "{new_dataset_id}"'
        new_city_block = re.sub(dataset_pattern, new_dataset_str, new_city_block)

    new_content = content[:start] + new_city_block + content[end:]
    return new_content, True


def deactivate_city(content, city_key, reason):
    """Set active=False for a city and add a comment."""
    start, end = find_city_block(content, city_key)
    if start is None:
        return content, False

    city_block = content[start:end]

    # Check if already inactive
    if '"active": False' in city_block:
        return content, False

    # Replace active: True with active: False and add comment
    active_pattern = r'"active"\s*:\s*True'
    new_active_str = f'"active": False,  # V12.31 Deactivated: {reason}'
    new_city_block = re.sub(active_pattern, new_active_str, city_block)

    if new_city_block == city_block:
        # Pattern didn't match, try without trailing comma
        active_pattern = r'"active"\s*:\s*True,'
        new_active_str = f'"active": False,  # V12.31 Deactivated: {reason}'
        new_city_block = re.sub(active_pattern, new_active_str, city_block)

    new_content = content[:start] + new_city_block + content[end:]
    return new_content, True


def phase1_apply_field_map_fixes(content, fixes, preview=False):
    """Phase 1: Apply field_map corrections from suggested_fixes.json."""
    print("\n" + "=" * 60)
    print("PHASE 1: Apply Field Map Fixes")
    print("=" * 60)

    if not fixes:
        print("  No suggested_fixes.json found. Download from server first.")
        return content, 0

    # Filter for field_map fixes only
    field_map_fixes = [f for f in fixes if f.get('action') == 'UPDATE_FIELD_MAP']
    print(f"  Found {len(field_map_fixes)} field_map fixes to apply")

    applied = 0
    for fix in field_map_fixes:
        city_key = fix.get('city')
        new_map = fix.get('suggested_field_map', {})

        if city_key in WORKING_CITIES:
            print(f"  SKIP {city_key}: In working cities list (do not touch)")
            continue

        if not new_map:
            print(f"  SKIP {city_key}: No suggested field_map")
            continue

        if preview:
            print(f"  PREVIEW {city_key}: Would update field_map to {new_map}")
        else:
            content, success = update_field_map(content, city_key, new_map)
            if success:
                print(f"  APPLIED {city_key}: Updated field_map")
                applied += 1
            else:
                print(f"  ERROR {city_key}: Could not find city block")

    print(f"\n  Phase 1 complete: {applied} cities updated")
    return content, applied


def phase2_apply_discoveries(content, validation, preview=False):
    """Phase 2: Apply Socrata discovery replacements."""
    print("\n" + "=" * 60)
    print("PHASE 2: Apply Discovery Replacements")
    print("=" * 60)

    if not validation:
        print("  No endpoint_validation.json found. Download from server first.")
        return content, 0

    discoveries = validation.get('discoveries', [])
    print(f"  Found {len(discoveries)} discovery replacements")

    applied = 0
    for item in discoveries:
        city_key = item.get('city')
        discovery = item.get('discovery', {})

        if city_key in WORKING_CITIES:
            print(f"  SKIP {city_key}: In working cities list (do not touch)")
            continue

        new_endpoint = discovery.get('link')
        new_dataset_id = discovery.get('id')
        available_fields = discovery.get('columns', [])

        if not new_endpoint:
            print(f"  SKIP {city_key}: No endpoint in discovery")
            continue

        # Build a field_map from discovered columns using inline mapper
        suggested_map = auto_map_fields(available_fields)

        if preview:
            print(f"  PREVIEW {city_key}:")
            print(f"    New endpoint: {new_endpoint}")
            print(f"    Suggested map: {suggested_map}")
        else:
            # Update endpoint
            content, ep_success = update_endpoint(content, city_key, new_endpoint, new_dataset_id)

            # Update field_map if we have suggestions
            if suggested_map:
                content, fm_success = update_field_map(content, city_key, suggested_map)
            else:
                fm_success = True

            if ep_success:
                print(f"  APPLIED {city_key}: Updated endpoint and field_map")
                applied += 1
            else:
                print(f"  ERROR {city_key}: Could not find city block")

    print(f"\n  Phase 2 complete: {applied} cities updated")
    return content, applied


def phase3_deactivate_broken(content, validation, preview=False):
    """Phase 3: Deactivate cities with unrecoverable errors."""
    print("\n" + "=" * 60)
    print("PHASE 3: Deactivate Unrecoverable Cities")
    print("=" * 60)

    if not validation:
        print("  No endpoint_validation.json found. Download from server first.")
        return content, 0

    # Get cities that are broken and not fixed by Phase 1-2
    dead_urls = validation.get('dead_url', [])
    timeouts = validation.get('timeout', [])
    errors = validation.get('error', [])
    no_endpoints = validation.get('no_endpoint', [])

    # Get discoveries that were fixed in Phase 2
    discovery_cities = {d.get('city') for d in validation.get('discoveries', [])}

    to_deactivate = []

    for item in dead_urls:
        city = item.get('city')
        if city not in discovery_cities and city not in WORKING_CITIES:
            to_deactivate.append((city, "Dead URL, no public API found"))

    for item in timeouts:
        city = item.get('city')
        if city not in WORKING_CITIES:
            to_deactivate.append((city, "Timeout, endpoint unresponsive"))

    for item in errors:
        city = item.get('city')
        if city not in WORKING_CITIES:
            to_deactivate.append((city, "Error parsing response"))

    for item in no_endpoints:
        city = item.get('city')
        if city not in WORKING_CITIES:
            to_deactivate.append((city, "No valid endpoint URL"))

    print(f"  Found {len(to_deactivate)} cities to deactivate")

    deactivated = 0
    for city_key, reason in to_deactivate:
        if preview:
            print(f"  PREVIEW {city_key}: Would deactivate ({reason})")
        else:
            content, success = deactivate_city(content, city_key, reason)
            if success:
                print(f"  DEACTIVATED {city_key}: {reason}")
                deactivated += 1
            else:
                # May already be inactive or not found
                pass

    print(f"\n  Phase 3 complete: {deactivated} cities deactivated")
    return content, deactivated


def main():
    parser = argparse.ArgumentParser(description='Apply endpoint fixes to city_configs.py')
    parser.add_argument('--preview', action='store_true', help='Preview changes without applying')
    parser.add_argument('--apply', action='store_true', help='Apply changes')
    parser.add_argument('--phase', type=int, choices=[1, 2, 3], help='Run only specific phase')
    args = parser.parse_args()

    if not args.preview and not args.apply:
        print("Usage: python apply_fixes.py --preview   # See what would change")
        print("       python apply_fixes.py --apply     # Apply all fixes")
        print("       python apply_fixes.py --apply --phase 1  # Apply only Phase 1")
        return

    preview = args.preview

    print("=" * 60)
    print("PermitGrab Endpoint Fix Pipeline V12.31")
    print("=" * 60)
    print(f"Mode: {'PREVIEW' if preview else 'APPLY'}")
    print(f"Phase: {args.phase if args.phase else 'ALL'}")

    # Load data files
    print("\nLoading data files...")
    fixes = load_json(SUGGESTED_FIXES_PATH)
    validation = load_json(VALIDATION_PATH)

    if not fixes and not validation:
        print("\nERROR: No data files found!")
        print("Download from server first:")
        print("  scp render:/app/data/suggested_fixes.json data/")
        print("  scp render:/app/data/endpoint_validation.json data/")
        return

    # Load city_configs.py
    content = read_city_configs()
    total_applied = 0

    # Run phases
    if args.phase is None or args.phase == 1:
        content, count = phase1_apply_field_map_fixes(content, fixes, preview)
        total_applied += count

    if args.phase is None or args.phase == 2:
        content, count = phase2_apply_discoveries(content, validation, preview)
        total_applied += count

    if args.phase is None or args.phase == 3:
        content, count = phase3_deactivate_broken(content, validation, preview)
        total_applied += count

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)

    if preview:
        print(f"  Would apply {total_applied} changes")
        print("  Run with --apply to make changes")
    else:
        if total_applied > 0:
            write_city_configs(content)
            print(f"  Applied {total_applied} changes to city_configs.py")
        else:
            print("  No changes to apply")

    print("\nNext steps:")
    print("  1. Review changes: git diff city_configs.py")
    print("  2. Commit: git add city_configs.py && git commit -m 'V12.31: Apply endpoint fixes'")
    print("  3. Deploy to Render")
    print("  4. Force collection: POST /api/admin/force-collection")
    print("  5. Check results: GET /api/admin/collection-status")


if __name__ == '__main__':
    main()
