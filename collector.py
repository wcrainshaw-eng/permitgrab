"""
PermitGrab - Data Collector
Pulls real permit data from free municipal Socrata/SODA APIs
"""

import requests
import json
import os
import re
from datetime import datetime, timedelta
from config import CITY_SOURCES, TRADE_CATEGORIES, PERMIT_VALUE_TIERS

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(DATA_DIR, exist_ok=True)


def fetch_permits(city_key, days_back=30):
    """Fetch recent permits from a city's Socrata API."""
    source = CITY_SOURCES.get(city_key)
    if not source:
        print(f"  [SKIP] Unknown city: {city_key}")
        return []

    endpoint = source["endpoint"]
    date_field = source["date_field"]
    limit = source.get("limit", 500)

    # Calculate date filter
    since_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%dT00:00:00")

    params = {
        "$limit": limit,
        "$order": f"{date_field} DESC",
        "$where": f"{date_field} > '{since_date}'",
    }

    print(f"  Fetching {source['name']} permits (last {days_back} days)...")

    try:
        resp = requests.get(endpoint, params=params, timeout=30)
        resp.raise_for_status()
        raw = resp.json()
        print(f"  ✓ Got {len(raw)} raw permits from {source['name']}")
        return raw
    except requests.exceptions.HTTPError as e:
        print(f"  [ERROR] HTTP {e.response.status_code} for {source['name']}: {e}")
        return []
    except requests.exceptions.ConnectionError:
        print(f"  [ERROR] Connection failed for {source['name']}")
        return []
    except requests.exceptions.Timeout:
        print(f"  [ERROR] Timeout for {source['name']}")
        return []
    except Exception as e:
        print(f"  [ERROR] {source['name']}: {e}")
        return []


def normalize_permit(raw_record, city_key):
    """Normalize a raw permit record into our standard schema."""
    source = CITY_SOURCES[city_key]
    fmap = source["field_map"]

    def get_field(field_name):
        raw_key = fmap.get(field_name, "")
        if not raw_key:
            return ""
        return str(raw_record.get(raw_key, "")).strip()

    # Build address
    address_parts = []
    if get_field("address"):
        address_parts.append(get_field("address"))
    if get_field("street"):
        address_parts.append(get_field("street"))
    if "street_name" in fmap and fmap["street_name"] != fmap.get("street", ""):
        sn = get_field("street_name") if "street_name" in fmap else ""
        if sn:
            address_parts.append(sn)
    elif not get_field("street") and get_field("street_name"):
        address_parts.append(get_field("street_name"))

    address = " ".join(address_parts) if address_parts else get_field("address")
    if not address:
        address = "Address not provided"

    # Parse cost
    cost_str = get_field("estimated_cost")
    try:
        cost = float(re.sub(r'[^\d.]', '', cost_str)) if cost_str else 0
    except (ValueError, TypeError):
        cost = 0

    # Parse date
    date_str = get_field("filing_date")
    parsed_date = ""
    if date_str:
        for fmt in ["%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%m/%d/%Y"]:
            try:
                parsed_date = datetime.strptime(date_str[:26], fmt).strftime("%Y-%m-%d")
                break
            except ValueError:
                continue
        if not parsed_date:
            parsed_date = date_str[:10]

    # Build description from available fields
    desc = get_field("description") or get_field("work_type") or get_field("permit_type")

    # Classify trade
    trade = classify_trade(desc + " " + get_field("work_type") + " " + get_field("permit_type"))

    # Score value
    value_tier = score_value(cost)

    # Owner/contact info
    owner = get_field("owner_name")
    if get_field("owner_last"):
        owner = f"{owner} {get_field('owner_last')}".strip()
    contact = get_field("contact_name") or owner
    phone = get_field("owner_phone") or get_field("contact_phone")

    return {
        "id": f"{city_key}_{get_field('permit_number')}",
        "city": source["name"],
        "state": source["state"],
        "permit_number": get_field("permit_number"),
        "permit_type": get_field("permit_type"),
        "work_type": get_field("work_type"),
        "trade_category": trade,
        "address": address,
        "zip": get_field("zip"),
        "filing_date": parsed_date,
        "status": get_field("status"),
        "estimated_cost": cost,
        "value_tier": value_tier,
        "description": desc[:500] if desc else "",
        "contact_name": contact,
        "contact_phone": phone,
        "borough": get_field("borough") if "borough" in fmap else "",
        "source_city": city_key,
    }


def classify_trade(text):
    """Classify a permit into a trade category based on description text."""
    text_lower = text.lower()
    scores = {}
    for trade, keywords in TRADE_CATEGORIES.items():
        score = sum(1 for kw in keywords if kw in text_lower)
        if score > 0:
            scores[trade] = score

    if scores:
        return max(scores, key=scores.get)
    return "Other / Unclassified"


def score_value(cost):
    """Score the lead value based on estimated project cost."""
    if cost >= PERMIT_VALUE_TIERS["high"]["min_cost"]:
        return "high"
    elif cost >= PERMIT_VALUE_TIERS["medium"]["min_cost"]:
        return "medium"
    return "low"


def collect_all(days_back=30):
    """Collect permits from all configured cities."""
    all_permits = []
    stats = {}

    print("=" * 60)
    print("PermitGrab - Data Collection")
    print(f"Pulling permits from {len(CITY_SOURCES)} cities (last {days_back} days)")
    print("=" * 60)

    for city_key in CITY_SOURCES:
        raw = fetch_permits(city_key, days_back)
        city_permits = []

        for record in raw:
            try:
                normalized = normalize_permit(record, city_key)
                if normalized["permit_number"]:  # Skip records without permit numbers
                    city_permits.append(normalized)
            except Exception as e:
                continue  # Skip malformed records

        all_permits.extend(city_permits)
        stats[city_key] = {
            "raw": len(raw),
            "normalized": len(city_permits),
            "city_name": CITY_SOURCES[city_key]["name"],
        }

    # Trade category breakdown
    trade_counts = {}
    for p in all_permits:
        cat = p["trade_category"]
        trade_counts[cat] = trade_counts.get(cat, 0) + 1

    # Value tier breakdown
    value_counts = {"high": 0, "medium": 0, "low": 0}
    for p in all_permits:
        value_counts[p["value_tier"]] = value_counts.get(p["value_tier"], 0) + 1

    # Save to JSON
    output_file = os.path.join(DATA_DIR, "permits.json")
    with open(output_file, "w") as f:
        json.dump(all_permits, f, indent=2, default=str)

    # Save stats
    stats_file = os.path.join(DATA_DIR, "collection_stats.json")
    collection_stats = {
        "collected_at": datetime.now().isoformat(),
        "days_back": days_back,
        "total_permits": len(all_permits),
        "city_stats": stats,
        "trade_breakdown": dict(sorted(trade_counts.items(), key=lambda x: -x[1])),
        "value_breakdown": value_counts,
    }
    with open(stats_file, "w") as f:
        json.dump(collection_stats, f, indent=2)

    # Print summary
    print("\n" + "=" * 60)
    print("COLLECTION COMPLETE")
    print("=" * 60)
    print(f"Total permits collected: {len(all_permits)}")
    print(f"\nBy City:")
    for key, s in sorted(stats.items(), key=lambda x: -x[1]["normalized"]):
        print(f"  {s['city_name']}: {s['normalized']} permits ({s['raw']} raw)")
    print(f"\nBy Trade Category:")
    for trade, count in sorted(trade_counts.items(), key=lambda x: -x[1]):
        print(f"  {trade}: {count}")
    print(f"\nBy Value Tier:")
    print(f"  High ($50K+): {value_counts['high']}")
    print(f"  Medium ($10K-$50K): {value_counts['medium']}")
    print(f"  Standard (<$10K): {value_counts['low']}")
    print(f"\nData saved to: {output_file}")

    return all_permits, collection_stats


if __name__ == "__main__":
    permits, stats = collect_all(days_back=60)
