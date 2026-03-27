#!/usr/bin/env python3
"""
Export complete source inventory for PermitGrab collector redesign.
Phase 1 task from COLLECTOR_REDESIGN.txt

Outputs:
- source_inventory.json - Full inventory with all details
- source_inventory_summary.csv - Summary for quick review
"""

import json
import csv
from city_configs import CITY_REGISTRY, BULK_SOURCES

def export_inventory():
    # Process CITY_REGISTRY entries
    city_sources = []
    for key, config in CITY_REGISTRY.items():
        city_sources.append({
            "source_id": key,
            "source_name": config.get("name", key),
            "city": config.get("name", ""),
            "state": config.get("state", ""),
            "slug": config.get("slug", ""),
            "source_type": config.get("platform", "unknown"),
            "endpoint": config.get("endpoint", ""),
            "dataset_id": config.get("dataset_id", ""),
            "active": config.get("active", False),
            "scope": "city",
            "notes": config.get("notes", ""),
            "date_field": config.get("date_field", ""),
        })

    # Process BULK_SOURCES entries
    bulk_sources = []
    for key, config in BULK_SOURCES.items():
        bulk_sources.append({
            "source_id": key,
            "source_name": config.get("name", key),
            "city": "",  # Bulk sources cover multiple cities
            "state": config.get("state", ""),
            "slug": "",
            "source_type": config.get("platform", "unknown"),
            "endpoint": config.get("endpoint", ""),
            "dataset_id": config.get("dataset_id", ""),
            "active": config.get("active", False),
            "scope": config.get("mode", "bulk"),
            "city_field": config.get("city_field", ""),
            "notes": config.get("notes", ""),
            "date_field": config.get("date_field", ""),
        })

    # Count statistics
    city_active = sum(1 for s in city_sources if s["active"])
    city_inactive = sum(1 for s in city_sources if not s["active"])
    bulk_active = sum(1 for s in bulk_sources if s["active"])
    bulk_inactive = sum(1 for s in bulk_sources if not s["active"])

    # Group by platform type
    platforms_city = {}
    for s in city_sources:
        p = s["source_type"]
        if p not in platforms_city:
            platforms_city[p] = {"active": 0, "inactive": 0}
        if s["active"]:
            platforms_city[p]["active"] += 1
        else:
            platforms_city[p]["inactive"] += 1

    platforms_bulk = {}
    for s in bulk_sources:
        p = s["source_type"]
        if p not in platforms_bulk:
            platforms_bulk[p] = {"active": 0, "inactive": 0}
        if s["active"]:
            platforms_bulk[p]["active"] += 1
        else:
            platforms_bulk[p]["inactive"] += 1

    # Build full inventory
    inventory = {
        "export_date": "2026-03-27",
        "summary": {
            "city_registry": {
                "total": len(city_sources),
                "active": city_active,
                "inactive": city_inactive,
                "by_platform": platforms_city,
            },
            "bulk_sources": {
                "total": len(bulk_sources),
                "active": bulk_active,
                "inactive": bulk_inactive,
                "by_platform": platforms_bulk,
            },
        },
        "city_sources": city_sources,
        "bulk_sources": bulk_sources,
    }

    # Export JSON
    with open("source_inventory.json", "w") as f:
        json.dump(inventory, f, indent=2)
    print("Exported source_inventory.json")

    # Export CSV summary
    all_sources = city_sources + bulk_sources
    with open("source_inventory_summary.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "source_id", "source_name", "state", "scope", "source_type",
            "active", "endpoint", "dataset_id", "notes"
        ])
        for s in all_sources:
            writer.writerow([
                s["source_id"],
                s["source_name"],
                s["state"],
                s["scope"],
                s["source_type"],
                "YES" if s["active"] else "NO",
                s["endpoint"],
                s["dataset_id"],
                s["notes"][:100] + "..." if len(s.get("notes", "")) > 100 else s.get("notes", ""),
            ])
    print("Exported source_inventory_summary.csv")

    # Print summary
    print("\n" + "=" * 60)
    print("SOURCE INVENTORY SUMMARY")
    print("=" * 60)
    print(f"\nCITY_REGISTRY:")
    print(f"  Total:    {len(city_sources)}")
    print(f"  Active:   {city_active}")
    print(f"  Inactive: {city_inactive}")
    print(f"\n  By Platform:")
    for p, counts in sorted(platforms_city.items()):
        print(f"    {p}: {counts['active']} active, {counts['inactive']} inactive")

    print(f"\nBULK_SOURCES:")
    print(f"  Total:    {len(bulk_sources)}")
    print(f"  Active:   {bulk_active}")
    print(f"  Inactive: {bulk_inactive}")
    print(f"\n  By Platform:")
    for p, counts in sorted(platforms_bulk.items()):
        print(f"    {p}: {counts['active']} active, {counts['inactive']} inactive")

    print(f"\nCOMBINED:")
    print(f"  Total Sources:    {len(city_sources) + len(bulk_sources)}")
    print(f"  Total Active:     {city_active + bulk_active}")
    print(f"  Total Inactive:   {city_inactive + bulk_inactive}")

    # List inactive bulk sources (these are high-value to reactivate)
    print("\n" + "=" * 60)
    print("INACTIVE BULK SOURCES (high-value to reactivate)")
    print("=" * 60)
    for s in bulk_sources:
        if not s["active"]:
            print(f"\n  {s['source_id']}:")
            print(f"    Name: {s['source_name']}")
            print(f"    State: {s['state']}")
            print(f"    Endpoint: {s['endpoint']}")
            print(f"    Notes: {s['notes']}")

if __name__ == "__main__":
    export_inventory()
