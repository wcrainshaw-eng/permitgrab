"""
PermitGrab - Data Collector
Pulls real permit data from free municipal APIs (Socrata/SODA and ArcGIS REST)
"""

import requests
import json
import os
import re
from datetime import datetime, timedelta
from config import CITY_SOURCES, TRADE_CATEGORIES, PERMIT_VALUE_TIERS

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(DATA_DIR, exist_ok=True)


def fetch_permits_socrata(source, days_back):
    """Fetch permits from a Socrata SODA API."""
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

    resp = requests.get(endpoint, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def fetch_permits_arcgis(source, days_back):
    """Fetch permits from an ArcGIS REST API FeatureServer."""
    endpoint = source["endpoint"]
    date_field = source["date_field"]
    limit = source.get("limit", 500)
    date_format = source.get("date_format", "date")  # "date", "epoch", or "none"

    # Calculate date filter
    since_dt = datetime.now() - timedelta(days=days_back)

    if date_format == "epoch":
        # Some ArcGIS services store dates as Unix epoch milliseconds
        # Use TIMESTAMP syntax which works better with epoch fields
        since_epoch = int(since_dt.timestamp() * 1000)
        where_clause = f"{date_field} >= TIMESTAMP '{since_dt.strftime('%Y-%m-%d %H:%M:%S')}'"
    elif date_format == "none":
        # Skip date filtering, just get most recent by order
        where_clause = "1=1"
    else:
        # Standard ArcGIS DATE format
        since_date = since_dt.strftime("%Y-%m-%d")
        where_clause = f"{date_field} >= DATE '{since_date}'"

    # ArcGIS query parameters
    params = {
        "where": where_clause,
        "outFields": "*",
        "resultRecordCount": limit,
        "orderByFields": f"{date_field} DESC",
        "f": "json",
    }

    resp = requests.get(endpoint, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    # ArcGIS returns features in a nested structure
    if "features" in data:
        results = [f["attributes"] for f in data["features"]]
        # If using "none" date_format, filter in Python
        if date_format == "none" and date_field and results:
            since_epoch = int(since_dt.timestamp() * 1000)
            results = [r for r in results if r.get(date_field, 0) and r[date_field] >= since_epoch]
        return results
    return []


def fetch_permits(city_key, days_back=30):
    """Fetch recent permits from a city's API (Socrata or ArcGIS)."""
    source = CITY_SOURCES.get(city_key)
    if not source:
        print(f"  [SKIP] Unknown city: {city_key}")
        return []

    api_type = source.get("api_type", "socrata")
    print(f"  Fetching {source['name']} permits (last {days_back} days)...")

    try:
        if api_type == "arcgis":
            raw = fetch_permits_arcgis(source, days_back)
        else:
            raw = fetch_permits_socrata(source, days_back)

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
        # Check if it's an epoch timestamp (milliseconds)
        if date_str.isdigit() and len(date_str) >= 10:
            try:
                epoch_ms = int(date_str)
                parsed_date = datetime.fromtimestamp(epoch_ms / 1000).strftime("%Y-%m-%d")
            except (ValueError, OSError):
                pass
        if not parsed_date:
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


def normalize_address(address):
    """Normalize an address for consistent indexing and matching."""
    if not address:
        return ""
    # Lowercase, strip whitespace, normalize common abbreviations
    addr = address.lower().strip()
    # Remove extra whitespace
    addr = re.sub(r'\s+', ' ', addr)
    # Common abbreviations
    replacements = [
        (r'\bstreet\b', 'st'),
        (r'\bavenue\b', 'ave'),
        (r'\bboulevard\b', 'blvd'),
        (r'\bdrive\b', 'dr'),
        (r'\broad\b', 'rd'),
        (r'\blane\b', 'ln'),
        (r'\bcourt\b', 'ct'),
        (r'\bplace\b', 'pl'),
        (r'\bapartment\b', 'apt'),
        (r'\bsuite\b', 'ste'),
        (r'\bnorth\b', 'n'),
        (r'\bsouth\b', 's'),
        (r'\beast\b', 'e'),
        (r'\bwest\b', 'w'),
    ]
    for pattern, replacement in replacements:
        addr = re.sub(pattern, replacement, addr)
    # Remove punctuation except for essential chars
    addr = re.sub(r'[^\w\s#-]', '', addr)
    return addr


def fetch_permit_history_socrata(source, years_back=3):
    """Fetch historical permits from a Socrata SODA API."""
    endpoint = source["endpoint"]
    date_field = source["date_field"]

    # Calculate date filter - go back 3 years
    since_date = (datetime.now() - timedelta(days=years_back * 365)).strftime("%Y-%m-%d")

    params = {
        "$limit": 5000,  # Higher limit for historical data
        "$order": f"{date_field} DESC",
        "$where": f"{date_field} > '{since_date}T00:00:00'",
    }

    resp = requests.get(endpoint, params=params, timeout=60)
    resp.raise_for_status()
    return resp.json()


def fetch_permit_history_arcgis(source, years_back=3):
    """Fetch historical permits from an ArcGIS REST API FeatureServer."""
    endpoint = source["endpoint"]
    date_field = source["date_field"]
    date_format = source.get("date_format", "date")

    # Calculate date filter - go back 3 years
    since_dt = datetime.now() - timedelta(days=years_back * 365)

    if date_format == "epoch":
        where_clause = f"{date_field} >= TIMESTAMP '{since_dt.strftime('%Y-%m-%d %H:%M:%S')}'"
    elif date_format == "none":
        where_clause = "1=1"
    else:
        since_date = since_dt.strftime("%Y-%m-%d")
        where_clause = f"{date_field} >= DATE '{since_date}'"

    params = {
        "where": where_clause,
        "outFields": "*",
        "resultRecordCount": 5000,
        "orderByFields": f"{date_field} DESC",
        "f": "json",
    }

    resp = requests.get(endpoint, params=params, timeout=60)
    resp.raise_for_status()
    data = resp.json()

    if "features" in data:
        results = [f["attributes"] for f in data["features"]]
        if date_format == "none" and date_field and results:
            since_epoch = int(since_dt.timestamp() * 1000)
            results = [r for r in results if r.get(date_field, 0) and r[date_field] >= since_epoch]
        return results
    return []


def fetch_permit_history(city_key, years_back=3):
    """Fetch historical permits for a city (last 3 years)."""
    source = CITY_SOURCES.get(city_key)
    if not source:
        print(f"  [SKIP] Unknown city: {city_key}")
        return []

    api_type = source.get("api_type", "socrata")
    print(f"  Fetching {source['name']} permit history (last {years_back} years)...")

    try:
        if api_type == "arcgis":
            raw = fetch_permit_history_arcgis(source, years_back)
        else:
            raw = fetch_permit_history_socrata(source, years_back)

        print(f"  ✓ Got {len(raw)} historical permits from {source['name']}")
        return raw
    except requests.exceptions.HTTPError as e:
        print(f"  [ERROR] HTTP {e.response.status_code} for {source['name']}: {e}")
        return []
    except requests.exceptions.Timeout:
        print(f"  [ERROR] Timeout for {source['name']} (history takes longer)")
        return []
    except Exception as e:
        print(f"  [ERROR] {source['name']}: {e}")
        return []


def collect_permit_history(years_back=3):
    """Collect permit history from all cities, indexed by normalized address."""
    history_index = {}
    stats = {}

    print("=" * 60)
    print("PermitGrab - Permit History Collection")
    print(f"Pulling {years_back} years of history from {len(CITY_SOURCES)} cities")
    print("=" * 60)

    for city_key in CITY_SOURCES:
        raw = fetch_permit_history(city_key, years_back)
        city_count = 0

        for record in raw:
            try:
                normalized = normalize_permit(record, city_key)
                if not normalized["permit_number"]:
                    continue

                # Index by normalized address
                addr_key = normalize_address(normalized["address"])
                if not addr_key or addr_key == "address not provided":
                    continue

                if addr_key not in history_index:
                    history_index[addr_key] = {
                        "address": normalized["address"],
                        "city": normalized["city"],
                        "state": normalized["state"],
                        "permits": []
                    }

                history_index[addr_key]["permits"].append({
                    "permit_number": normalized["permit_number"],
                    "permit_type": normalized["permit_type"],
                    "work_type": normalized["work_type"],
                    "trade_category": normalized["trade_category"],
                    "filing_date": normalized["filing_date"],
                    "estimated_cost": normalized["estimated_cost"],
                    "description": normalized["description"][:200],
                    "contractor": normalized["contact_name"],
                })
                city_count += 1

            except Exception:
                continue

        stats[city_key] = {
            "raw": len(raw),
            "indexed": city_count,
            "city_name": CITY_SOURCES[city_key]["name"],
        }

    # Sort permits by date for each address
    for addr_key in history_index:
        history_index[addr_key]["permits"].sort(
            key=lambda x: x["filing_date"] or "0000-00-00",
            reverse=True
        )
        history_index[addr_key]["permit_count"] = len(history_index[addr_key]["permits"])

    # Save to JSON
    output_file = os.path.join(DATA_DIR, "permit_history.json")
    with open(output_file, "w") as f:
        json.dump(history_index, f, indent=2, default=str)

    # Calculate stats
    total_addresses = len(history_index)
    total_permits = sum(len(h["permits"]) for h in history_index.values())
    repeat_renovators = sum(1 for h in history_index.values() if len(h["permits"]) >= 3)

    # Print summary
    print("\n" + "=" * 60)
    print("PERMIT HISTORY COLLECTION COMPLETE")
    print("=" * 60)
    print(f"Total unique addresses: {total_addresses}")
    print(f"Total historical permits: {total_permits}")
    print(f"Repeat Renovators (3+ permits): {repeat_renovators}")
    print(f"\nBy City:")
    for key, s in sorted(stats.items(), key=lambda x: -x[1]["indexed"]):
        print(f"  {s['city_name']}: {s['indexed']} permits indexed ({s['raw']} raw)")
    print(f"\nData saved to: {output_file}")

    return history_index, stats


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
