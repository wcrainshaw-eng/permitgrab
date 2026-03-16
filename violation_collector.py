"""
PermitGrab - Code Violation Collector
Pulls code violation data from city open-data Socrata APIs
"""

import requests
import json
import os
import re
from datetime import datetime, timedelta

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(DATA_DIR, exist_ok=True)

# Code violation data sources (Socrata APIs)
VIOLATION_SOURCES = {
    "nyc": {
        "name": "New York City",
        "state": "NY",
        "endpoint": "https://data.cityofnewyork.us/resource/wvxf-dwi5.json",
        "date_field": "inspectiondate",
        "field_map": {
            "address": "streetname",
            "street_number": "housenumber",
            "borough": "boro",
            "violation_type": "novdescription",
            "status": "currentstatus",
            "violation_date": "inspectiondate",
            "violation_id": "violationid",
        },
    },
    "chicago": {
        "name": "Chicago",
        "state": "IL",
        "endpoint": "https://data.cityofchicago.org/resource/22u3-xenr.json",
        "date_field": "violation_date",
        "field_map": {
            "address": "address",
            "violation_type": "violation_description",
            "status": "violation_status",
            "violation_date": "violation_date",
            "violation_id": "id",
        },
    },
    "la": {
        "name": "Los Angeles",
        "state": "CA",
        "endpoint": "https://data.lacity.org/resource/2uz8-3tj3.json",
        "date_field": "date_case_generated",
        "field_map": {
            "address": "address_street_name",
            "street_number": "address_house_number",
            "violation_type": "case_type",
            "status": "case_status",
            "violation_date": "date_case_generated",
            "violation_id": "ladbs_inspection_district",
        },
    },
    "austin": {
        "name": "Austin",
        "state": "TX",
        "endpoint": "https://data.austintexas.gov/resource/5gje-qqi7.json",
        "date_field": "case_opened",
        "field_map": {
            "address": "full_address",
            "violation_type": "case_type_description",
            "status": "case_status",
            "violation_date": "case_opened",
            "violation_id": "case_id",
        },
    },
    "sf": {
        "name": "San Francisco",
        "state": "CA",
        "endpoint": "https://data.sfgov.org/resource/gm2e-bten.json",
        "date_field": "file_date",
        "field_map": {
            "address": "address",
            "violation_type": "violation_type_description",
            "status": "status",
            "violation_date": "file_date",
            "violation_id": "complaint_number",
        },
    },
    "seattle": {
        "name": "Seattle",
        "state": "WA",
        "endpoint": "https://data.seattle.gov/resource/ez4a-iug7.json",
        "date_field": "insp_date",
        "field_map": {
            "address": "insp_addr",
            "violation_type": "insp_rslt",
            "status": "insp_rslt",
            "violation_date": "insp_date",
            "violation_id": "insp_caession",
        },
    },
}


def normalize_address(address):
    """Normalize an address for consistent indexing and matching."""
    if not address:
        return ""
    addr = address.lower().strip()
    addr = re.sub(r'\s+', ' ', addr)
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
    addr = re.sub(r'[^\w\s#-]', '', addr)
    return addr


def fetch_violations_socrata(source, days_back=90):
    """Fetch violations from a Socrata SODA API."""
    endpoint = source["endpoint"]
    date_field = source["date_field"]

    # Calculate date filter - last 90 days
    since_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%dT00:00:00")

    params = {
        "$limit": 2000,
        "$order": f"{date_field} DESC",
        "$where": f"{date_field} > '{since_date}'",
    }

    try:
        resp = requests.get(endpoint, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.HTTPError as e:
        print(f"  [ERROR] HTTP {e.response.status_code} for violations: {e}")
        return []
    except requests.exceptions.Timeout:
        print(f"  [ERROR] Timeout for violations")
        return []
    except Exception as e:
        print(f"  [ERROR] Fetching violations: {e}")
        return []


def normalize_violation(raw_record, city_key):
    """Normalize a raw violation record into our standard schema."""
    source = VIOLATION_SOURCES[city_key]
    fmap = source["field_map"]

    def get_field(field_name):
        raw_key = fmap.get(field_name, "")
        if not raw_key:
            return ""
        return str(raw_record.get(raw_key, "")).strip()

    # Build address
    address = get_field("address")
    if get_field("street_number"):
        address = f"{get_field('street_number')} {address}"

    if not address:
        address = "Address not provided"

    # Parse date
    date_str = get_field("violation_date")
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

    # Determine status (open/closed)
    status_raw = get_field("status").lower()
    if any(s in status_raw for s in ["open", "active", "pending", "in progress"]):
        status = "open"
    elif any(s in status_raw for s in ["closed", "resolved", "complete", "dismissed"]):
        status = "closed"
    else:
        status = status_raw[:50] if status_raw else "unknown"

    return {
        "id": f"{city_key}_{get_field('violation_id')}",
        "city": source["name"],
        "state": source["state"],
        "address": address,
        "normalized_address": normalize_address(address),
        "violation_type": get_field("violation_type")[:200],
        "violation_date": parsed_date,
        "status": status,
        "borough": get_field("borough") if "borough" in fmap else "",
        "source_city": city_key,
    }


def fetch_violations(city_key, days_back=90):
    """Fetch recent violations for a city."""
    source = VIOLATION_SOURCES.get(city_key)
    if not source:
        print(f"  [SKIP] Unknown city: {city_key}")
        return []

    print(f"  Fetching {source['name']} violations (last {days_back} days)...")

    raw = fetch_violations_socrata(source, days_back)
    print(f"  ✓ Got {len(raw)} raw violations from {source['name']}")
    return raw


def collect_all_violations(days_back=90):
    """Collect violations from all configured cities."""
    all_violations = []
    stats = {}

    print("=" * 60)
    print("PermitGrab - Code Violation Collection")
    print(f"Pulling violations from {len(VIOLATION_SOURCES)} cities (last {days_back} days)")
    print("=" * 60)

    for city_key in VIOLATION_SOURCES:
        raw = fetch_violations(city_key, days_back)
        city_violations = []

        for record in raw:
            try:
                normalized = normalize_violation(record, city_key)
                if normalized["address"] != "Address not provided":
                    city_violations.append(normalized)
            except Exception as e:
                continue  # Skip malformed records

        all_violations.extend(city_violations)
        stats[city_key] = {
            "raw": len(raw),
            "normalized": len(city_violations),
            "city_name": VIOLATION_SOURCES[city_key]["name"],
        }

    # Calculate status breakdown
    status_counts = {"open": 0, "closed": 0, "unknown": 0}
    for v in all_violations:
        status = v.get("status", "unknown")
        if status in status_counts:
            status_counts[status] += 1
        else:
            status_counts["unknown"] += 1

    # Save to JSON
    output_file = os.path.join(DATA_DIR, "violations.json")
    with open(output_file, "w") as f:
        json.dump(all_violations, f, indent=2, default=str)

    # Save stats
    stats_file = os.path.join(DATA_DIR, "violation_stats.json")
    violation_stats = {
        "collected_at": datetime.now().isoformat(),
        "days_back": days_back,
        "total_violations": len(all_violations),
        "city_stats": stats,
        "status_breakdown": status_counts,
    }
    with open(stats_file, "w") as f:
        json.dump(violation_stats, f, indent=2)

    # Print summary
    print("\n" + "=" * 60)
    print("VIOLATION COLLECTION COMPLETE")
    print("=" * 60)
    print(f"Total violations collected: {len(all_violations)}")
    print(f"\nBy City:")
    for key, s in sorted(stats.items(), key=lambda x: -x[1]["normalized"]):
        print(f"  {s['city_name']}: {s['normalized']} violations ({s['raw']} raw)")
    print(f"\nBy Status:")
    print(f"  Open: {status_counts['open']}")
    print(f"  Closed: {status_counts['closed']}")
    print(f"  Unknown: {status_counts['unknown']}")
    print(f"\nData saved to: {output_file}")

    return all_violations, violation_stats


if __name__ == "__main__":
    violations, stats = collect_all_violations(days_back=90)
