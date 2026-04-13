"""
PermitGrab - Code Violation Collector
Pulls code violation data from city open-data Socrata APIs
"""

import requests
import json
import os
import re
from datetime import datetime, timedelta

# Use Render persistent disk if available, otherwise local
if os.path.isdir('/var/data'):
    DATA_DIR = '/var/data'
else:
    DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(DATA_DIR, exist_ok=True)

# V156: Code violation data sources (Socrata APIs) — verified 2026-04-12
VIOLATION_SOURCES = {
    "nyc_hpd": {
        "name": "New York City",
        "state": "NY",
        "endpoint": "https://data.cityofnewyork.us/resource/wvxf-dwi5.json",
        "dataset_id": "wvxf-dwi5",
        "date_field": "inspectiondate",
        "field_map": {
            "address": "streetname",
            "street_number": "housenumber",
            "borough": "boro",
            "violation_type": "violationtype",
            "description": "novdescription",
            "status": "currentstatus",
            "violation_date": "inspectiondate",
            "violation_id": "violationid",
        },
        "notes": "NYC HPD Housing Violations. 10.8M records, updated daily.",
    },
    "nyc_dob": {
        "name": "New York City",
        "state": "NY",
        "endpoint": "https://data.cityofnewyork.us/resource/3h2n-5cm9.json",
        "dataset_id": "3h2n-5cm9",
        "date_field": "issue_date",
        "field_map": {
            "address": "street",
            "street_number": "house_number",
            "violation_type": "violation_type_code",
            "description": "description",
            "status": "disposition_date",  # NULL = still open
            "violation_date": "issue_date",
            "violation_id": "isn_dob_bis_viol",
        },
        "status_logic": "dob",  # special: NULL disposition = open
        "notes": "NYC DOB Building Violations. 2.47M records.",
    },
    "la_open": {
        "name": "Los Angeles",
        "state": "CA",
        "endpoint": "https://data.lacity.org/resource/u82d-eh7z.json",
        "dataset_id": "u82d-eh7z",
        "date_field": "adddttm",
        "field_map": {
            "address": "stname",
            "street_number": "stno",
            "street_prefix": "predir",
            "street_suffix": "suffix",
            "violation_type": "aptype",
            "description": "apname",
            "status": "stat",
            "violation_date": "adddttm",
            "violation_id": "apno",
        },
        "notes": "LA Code Enforcement — Open Cases. 28K records.",
    },
    "la_closed": {
        "name": "Los Angeles",
        "state": "CA",
        "endpoint": "https://data.lacity.org/resource/rken-a55j.json",
        "dataset_id": "rken-a55j",
        "date_field": "adddttm",
        "field_map": {
            "address": "stname",
            "street_number": "stno",
            "street_prefix": "predir",
            "street_suffix": "suffix",
            "violation_type": "aptype",
            "description": "apname",
            "status": "stat",
            "violation_date": "adddttm",
            "violation_id": "apno",
        },
        "notes": "LA Code Enforcement — Closed Cases (last 6 months).",
    },
    "chicago": {
        "name": "Chicago",
        "state": "IL",
        "endpoint": "https://data.cityofchicago.org/resource/22u3-xenr.json",
        "dataset_id": "22u3-xenr",
        "date_field": "violation_date",
        "field_map": {
            "address": "violation_location",
            "violation_type": "violation_code",
            "description": "violation_description",
            "status": "violation_status",
            "violation_date": "violation_date",
            "violation_id": "id",
        },
        "notes": "Chicago Building Violations. 2M records, updated daily.",
    },
    "austin": {
        "name": "Austin",
        "state": "TX",
        "endpoint": "https://data.austintexas.gov/resource/6wtj-zbtb.json",
        "dataset_id": "6wtj-zbtb",
        "date_field": "opened_date",
        "field_map": {
            "address": "address",
            "violation_type": "case_type",
            "description": "description",
            "status": "status",
            "violation_date": "opened_date",
            "violation_id": "case_id",
        },
        "notes": "Austin Code Enforcement Cases. 82K records, updated daily.",
    },
}


def sanitize_string(value):
    """Remove control characters that break JSON parsing."""
    if not isinstance(value, str):
        return value
    # Remove ASCII control chars (0x00-0x1F) except common whitespace
    sanitized = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', value)
    # Replace newlines and tabs with spaces for single-line fields
    sanitized = sanitized.replace('\n', ' ').replace('\r', ' ').replace('\t', ' ')
    # Collapse multiple spaces
    sanitized = re.sub(r'  +', ' ', sanitized)
    return sanitized.strip()


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
        "$limit": 50000,
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

    # Build address — V156: handle multi-part LA addresses
    address_parts = []
    if get_field("street_number"):
        address_parts.append(get_field("street_number"))
    if get_field("street_prefix"):
        address_parts.append(get_field("street_prefix"))
    if get_field("address"):
        address_parts.append(get_field("address"))
    if get_field("street_suffix"):
        address_parts.append(get_field("street_suffix"))
    address = ' '.join(address_parts) if address_parts else ""

    # V156: Append borough for NYC
    if get_field("borough"):
        address = f"{address}, {get_field('borough')}"

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

    # Determine status (open/closed) — V156: DOB special logic
    if source.get("status_logic") == "dob":
        # NYC DOB: disposition_date NULL = still open
        status = "closed" if get_field("status") else "open"
    else:
        status_raw = get_field("status").lower()
        if any(s in status_raw for s in ["open", "active", "pending", "in progress", "o"]):
            status = "open"
        elif any(s in status_raw for s in ["closed", "resolved", "complete", "dismissed"]):
            status = "closed"
        else:
            status = status_raw[:50] if status_raw else "unknown"

    # V156: Get description field
    description = get_field("description") or get_field("violation_type")

    return {
        "id": f"{city_key}_{get_field('violation_id')}",
        "city": source["name"],
        "state": source["state"],
        "violation_id": get_field("violation_id"),
        "address": sanitize_string(address),
        "normalized_address": normalize_address(address),
        "violation_type": sanitize_string(get_field("violation_type")[:200]),
        "violation_date": parsed_date,
        "description": sanitize_string(description[:500]) if description else "",
        "status": sanitize_string(status),
        "source_dataset": source.get("dataset_id", ""),
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


def upsert_violations(violations):
    """V156: Insert violations into the database, skip duplicates."""
    if not violations:
        return 0
    import db as permitdb

    # V156: Ensure violations table exists
    conn = permitdb.get_connection()
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS violations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL, state TEXT NOT NULL, violation_id TEXT,
            address TEXT, violation_date TEXT, violation_type TEXT,
            description TEXT, status TEXT, source_dataset TEXT, source_url TEXT,
            collected_at TEXT DEFAULT (datetime('now')),
            UNIQUE(city, state, violation_id)
        )""")
        conn.commit()
    except Exception as e:
        print(f"[V156] Table creation note: {e}")

    inserted = 0
    errors = 0
    print(f"[V156] upsert_violations called with {len(violations)} violations")
    if violations:
        v0 = violations[0]
        print(f"[V156] First violation: city={v0.get('city')}, state={v0.get('state')}, vid={v0.get('violation_id')}, addr={str(v0.get('address',''))[:40]}")
    for v in violations:
        try:
            conn.execute(
                "INSERT OR REPLACE INTO violations "
                "(city, state, violation_id, address, violation_date, "
                "violation_type, description, status, source_dataset) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    v['city'], v['state'], v['violation_id'], v['address'],
                    v['violation_date'], v['violation_type'], v.get('description', ''),
                    v['status'], v.get('source_dataset', ''),
                )
            )
            inserted += 1
        except Exception as e:
            errors += 1
            if errors <= 3:
                print(f"[V156] Violation insert error #{errors}: {type(e).__name__}: {e}")
    try:
        conn.commit()
    except Exception as e:
        print(f"[V156] Commit error: {e}")
    if errors > 0:
        print(f"[V156] Total insert errors: {errors}/{len(violations)}")
    return inserted


def collect_all_violations(days_back=180):
    """V156: Collect violations from all configured cities and store in DB."""
    stats = {}

    print("=" * 60)
    print("PermitGrab - Code Violation Collection (V156)")
    print(f"Pulling violations from {len(VIOLATION_SOURCES)} sources (last {days_back} days)")
    print("=" * 60)

    total_inserted = 0
    for city_key in VIOLATION_SOURCES:
        raw = fetch_violations(city_key, days_back)
        city_violations = []

        for record in raw:
            try:
                normalized = normalize_violation(record, city_key)
                if normalized["address"] != "Address not provided" and normalized.get("violation_id"):
                    city_violations.append(normalized)
            except Exception:
                continue

        # V156: Write to database
        count = upsert_violations(city_violations)
        total_inserted += count

        # V156: Diagnostic — if 0 inserted from 1000+ normalized, test with 1 record
        diag = None
        if count == 0 and len(city_violations) > 0:
            test_v = city_violations[0]
            try:
                import db as permitdb
                tc = permitdb.get_connection()
                tc.execute(
                    "INSERT OR REPLACE INTO violations "
                    "(city, state, violation_id, address, violation_date, "
                    "violation_type, description, status, source_dataset) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (test_v['city'], test_v['state'], test_v['violation_id'],
                     test_v['address'], test_v['violation_date'],
                     test_v['violation_type'], test_v.get('description', ''),
                     test_v['status'], test_v.get('source_dataset', ''))
                )
                tc.commit()
                diag = f"single_insert_ok: vid={test_v['violation_id']}"
            except Exception as e:
                diag = f"single_insert_error: {type(e).__name__}: {str(e)[:200]}"

        stats[city_key] = {
            "raw": len(raw),
            "normalized": len(city_violations),
            "inserted": count,
            "city_name": VIOLATION_SOURCES[city_key]["name"],
            **({"diagnostic": diag} if diag else {}),
        }

    # Also save to JSON for backwards compat
    all_violations = []
    for city_key in VIOLATION_SOURCES:
        raw = []  # Already processed above
    output_file = os.path.join(DATA_DIR, "violation_stats.json")
    violation_stats = {
        "collected_at": datetime.now().isoformat(),
        "days_back": days_back,
        "total_inserted": total_inserted,
        "city_stats": stats,
    }
    with open(output_file, "w") as f:
        json.dump(violation_stats, f, indent=2)

    # Print summary
    print("\n" + "=" * 60)
    print("VIOLATION COLLECTION COMPLETE")
    print("=" * 60)
    print(f"Total violations inserted/updated: {total_inserted}")
    print(f"\nBy Source:")
    for key, s in sorted(stats.items(), key=lambda x: -x[1].get("inserted", 0)):
        print(f"  {s['city_name']} ({key}): {s.get('inserted', 0)} inserted ({s['raw']} raw, {s['normalized']} normalized)")

    return violation_stats


if __name__ == "__main__":
    stats = collect_all_violations(days_back=180)
