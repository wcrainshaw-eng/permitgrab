"""
PermitGrab - Pre-Construction Signal Collector
Pulls early-stage intelligence: zoning applications, planning approvals,
variance requests, and pre-permit filings from city open-data APIs.
"""

import requests
import json
import os
import re
import hashlib
from datetime import datetime, timedelta

# Use Render persistent disk if available, otherwise local
if os.path.isdir('/var/data'):
    DATA_DIR = '/var/data'
else:
    DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(DATA_DIR, exist_ok=True)

SIGNALS_FILE = os.path.join(DATA_DIR, "signals.json")
PERMITS_FILE = os.path.join(DATA_DIR, "permits.json")

# Signal type constants
SIGNAL_TYPES = {
    "zoning_application": {"label": "Zoning Application", "color": "purple"},
    "planning_approval": {"label": "Planning Approval", "color": "blue"},
    "variance_request": {"label": "Variance Request", "color": "orange"},
    "demolition_filing": {"label": "Demolition Filing", "color": "red"},
    "new_building_filing": {"label": "New Building Filing", "color": "green"},
    "land_use_review": {"label": "Land Use Review", "color": "purple"},
}

# Signal sources configuration
SIGNAL_SOURCES = {
    # NYC Sources
    "nyc_zap": {
        "name": "NYC Zoning Application Portal",
        "city": "New York City",
        "state": "NY",
        "endpoint": "https://data.cityofnewyork.us/resource/2iga-a6mk.json",
        "signal_type": "zoning_application",
        "field_map": {
            "title": "project_name",
            "description": "project_brief",
            "address": "primary_applicant_address",
            "borough": "borough",
            "applicant_name": "primary_applicant",
            "status": "project_status",
            "date_filed": "certified_referred",
            "source_url": "project_id",
        },
    },
    "nyc_bsa": {
        "name": "NYC Board of Standards & Appeals",
        "city": "New York City",
        "state": "NY",
        "endpoint": "https://data.cityofnewyork.us/resource/yvxd-uipr.json",
        "signal_type": "variance_request",
        "field_map": {
            "title": "application_number",
            "description": "calendar_status",
            "address": "premises",
            "applicant_name": "applicant",
            "status": "calendar_status",
            "date_filed": "calendar_date",
        },
    },
    "nyc_dob_filings": {
        "name": "NYC DOB Job Application Filings",
        "city": "New York City",
        "state": "NY",
        "endpoint": "https://data.cityofnewyork.us/resource/ic3t-wcy2.json",
        "signal_type": "new_building_filing",
        "field_map": {
            "title": "job__",
            "address_number": "house__",
            "address_street": "street_name",
            "borough": "borough",
            "applicant_name": "owner_s_first_name",
            "status": "job_status",
            "date_filed": "pre__filing_date",
            "estimated_value": "initial_cost",
            "job_type": "job_type",
        },
    },
    "nyc_new_buildings": {
        "name": "NYC DOB New Buildings",
        "city": "New York City",
        "state": "NY",
        "endpoint": "https://data.cityofnewyork.us/resource/6xbh-bxki.json",
        "signal_type": "new_building_filing",
        "field_map": {
            "title": "job_number",
            "address": "address",
            "borough": "borough",
            "applicant_name": "owner_name",
            "estimated_value": "estimated_cost",
            "status": "job_status",
            "date_filed": "filing_date",
        },
    },
    # Chicago Sources
    "chicago_affordable": {
        "name": "Chicago Affordable Housing Pipeline",
        "city": "Chicago",
        "state": "IL",
        "endpoint": "https://data.cityofchicago.org/resource/s6ha-ppgi.json",
        "signal_type": "planning_approval",
        "field_map": {
            "title": "property_name",
            "address": "address",
            "description": "community_area",
            "applicant_name": "developer",
            "metadata_units": "units",
        },
    },
    "chicago_new_construction": {
        "name": "Chicago New Construction Permits",
        "city": "Chicago",
        "state": "IL",
        "endpoint": "https://data.cityofchicago.org/resource/ydr8-5enu.json",
        "signal_type": "new_building_filing",
        "field_map": {
            "title": "permit_",
            "address": "street_address",
            "status": "permit_status",
            "date_filed": "application_start_date",
            "applicant_name": "contact_1_name",
            "estimated_value": "reported_cost",
        },
        "filter": "$where=permit_type='PERMIT - NEW CONSTRUCTION'",
    },
    # Los Angeles
    "la_plan_check": {
        "name": "LA Building & Safety Applications",
        "city": "Los Angeles",
        "state": "CA",
        "endpoint": "https://data.lacity.org/resource/yv23-pmwf.json",
        "signal_type": "new_building_filing",
        "field_map": {
            "title": "pcis_permit",
            "address": "address",
            "status": "latest_status",
            "date_filed": "submitted_date",
            "estimated_value": "valuation",
        },
    },
    # Austin
    "austin_in_review": {
        "name": "Austin Building Permits In Review",
        "city": "Austin",
        "state": "TX",
        "endpoint": "https://data.austintexas.gov/resource/3syk-w9eu.json",
        "signal_type": "new_building_filing",
        "field_map": {
            "title": "permit_num",
            "address": "original_address1",
            "status": "status_current",
            "date_filed": "filed_date",
            "applicant_name": "applicant_full_name",
            "estimated_value": "total_valuation_remodel",
        },
        "filter": "$where=status_current='In Review'",
    },
    # Seattle
    "seattle_in_review": {
        "name": "Seattle Building Permits In Review",
        "city": "Seattle",
        "state": "WA",
        "endpoint": "https://data.seattle.gov/resource/76t5-zuj7.json",
        "signal_type": "new_building_filing",
        "field_map": {
            "title": "application_permit_number",
            "address": "address",
            "status": "status",
            "date_filed": "application_date",
            "estimated_value": "value",
            "description": "description",
        },
        "filter": "$where=status='Application Accepted' OR status='In Review'",
    },
    # San Francisco
    "sf_plan_check": {
        "name": "SF DBI Building Permit Applications",
        "city": "San Francisco",
        "state": "CA",
        "endpoint": "https://data.sfgov.org/resource/i98e-djp9.json",
        "signal_type": "new_building_filing",
        "field_map": {
            "title": "permit_number",
            "address": "street_number",
            "address_street": "street_name",
            "status": "status",
            "date_filed": "filed_date",
            "estimated_value": "revised_cost",
            "description": "description",
        },
        "filter": "$where=status='filed' OR status='plancheck'",
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


def normalize_address(address, city="", state=""):
    """Normalize an address for consistent matching."""
    if not address:
        return ""

    addr = str(address).lower().strip()
    addr = re.sub(r'\s+', ' ', addr)

    # Expand common abbreviations
    expansions = [
        (r'\bst\b', 'street'),
        (r'\bave\b', 'avenue'),
        (r'\bblvd\b', 'boulevard'),
        (r'\bdr\b', 'drive'),
        (r'\brd\b', 'road'),
        (r'\bln\b', 'lane'),
        (r'\bct\b', 'court'),
        (r'\bpl\b', 'place'),
    ]
    for pattern, replacement in expansions:
        addr = re.sub(pattern, replacement, addr)

    # Remove apartment/unit/suite designations
    addr = re.sub(r'\b(apt|unit|suite|ste|#)\s*\w*', '', addr)

    # Remove punctuation
    addr = re.sub(r'[^\w\s]', '', addr)

    # Add city and state if provided
    if city:
        addr = f"{addr} {city.lower()}"
    if state:
        addr = f"{addr} {state.lower()}"

    return addr.strip()


def generate_signal_id(city, address, signal_type, source):
    """Generate a unique signal ID."""
    timestamp = int(datetime.now().timestamp())
    hash_input = f"{city}_{address}_{signal_type}_{source}_{timestamp}"
    hash_value = hashlib.md5(hash_input.encode()).hexdigest()[:8]
    city_slug = city.lower().replace(' ', '_')[:10]
    return f"signal_{city_slug}_{timestamp}_{hash_value}"


def load_signals():
    """Load existing signals from JSON file."""
    if os.path.exists(SIGNALS_FILE):
        with open(SIGNALS_FILE) as f:
            return json.load(f)
    return []


def save_signals(signals):
    """Save signals to JSON file."""
    with open(SIGNALS_FILE, 'w') as f:
        json.dump(signals, f, indent=2, default=str)


def load_permits():
    """Load existing permits from JSON file."""
    if os.path.exists(PERMITS_FILE):
        with open(PERMITS_FILE) as f:
            return json.load(f)
    return []


def save_permits(permits):
    """Save permits to JSON file."""
    with open(PERMITS_FILE, 'w') as f:
        json.dump(permits, f, indent=2, default=str)


def fetch_signals_socrata(source, days_back=90):
    """Fetch signals from a Socrata SODA API."""
    endpoint = source["endpoint"]

    since_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%dT00:00:00")

    params = {
        "$limit": 1000,
        "$order": ":id DESC",
    }

    # Add custom filter if specified
    if source.get("filter"):
        # Append date filter to existing filter
        params["$where"] = source["filter"].replace("$where=", "")
    else:
        params["$limit"] = 500

    try:
        resp = requests.get(endpoint, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"  [ERROR] Fetching {source['name']}: {e}")
        return []


def normalize_signal(raw_record, source_key):
    """Normalize a raw signal record into our standard schema."""
    source = SIGNAL_SOURCES[source_key]
    fmap = source["field_map"]

    def get_field(field_name):
        raw_key = fmap.get(field_name, "")
        if not raw_key:
            return ""
        return str(raw_record.get(raw_key, "")).strip()

    # Build address
    address = get_field("address")
    if get_field("address_number"):
        address = f"{get_field('address_number')} {address}"
    if get_field("address_street"):
        address = f"{address} {get_field('address_street')}"
    if get_field("borough"):
        address = f"{address}, {get_field('borough')}"

    if not address:
        return None

    # Parse date
    date_str = get_field("date_filed")
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

    # Parse estimated value
    value_str = get_field("estimated_value")
    estimated_value = None
    if value_str:
        try:
            estimated_value = float(re.sub(r'[^\d.]', '', value_str))
        except (ValueError, TypeError):
            pass

    # Determine signal type
    signal_type = source["signal_type"]
    job_type = get_field("job_type")
    if job_type:
        if job_type.upper() == "DM":
            signal_type = "demolition_filing"
        elif job_type.upper() == "NB":
            signal_type = "new_building_filing"

    # Normalize status
    status_raw = get_field("status").lower()
    if any(s in status_raw for s in ["approved", "issued", "complete"]):
        status = "approved"
    elif any(s in status_raw for s in ["denied", "rejected", "disapproved"]):
        status = "denied"
    elif any(s in status_raw for s in ["withdrawn", "cancelled"]):
        status = "withdrawn"
    else:
        status = "pending"

    # Build title
    title = get_field("title")
    if not title:
        title = f"{SIGNAL_TYPES.get(signal_type, {}).get('label', 'Signal')} - {address[:50]}"

    signal_id = generate_signal_id(source["city"], address, signal_type, source_key)

    return {
        "signal_id": signal_id,
        "city": source["city"],
        "state": source["state"],
        "address": sanitize_string(address),
        "address_normalized": normalize_address(address, source["city"], source["state"]),
        "signal_type": signal_type,
        "title": sanitize_string(title[:200]),
        "description": sanitize_string(get_field("description")[:500]) if get_field("description") else "",
        "source_url": sanitize_string(get_field("source_url")),
        "source_dataset": source_key,
        "date_filed": parsed_date,
        "date_collected": datetime.now().strftime("%Y-%m-%d"),
        "status": sanitize_string(status),
        "applicant_name": sanitize_string(get_field("applicant_name")),
        "estimated_value": estimated_value,
        "linked_permits": [],
        "metadata": {
            "units": sanitize_string(get_field("metadata_units")) if "metadata_units" in fmap else None,
        },
    }


def fetch_signals(source_key, days_back=90):
    """Fetch signals for a specific source."""
    source = SIGNAL_SOURCES.get(source_key)
    if not source:
        return []

    print(f"  Fetching {source['name']} signals...")
    raw = fetch_signals_socrata(source, days_back)
    print(f"  ✓ Got {len(raw)} raw records from {source['name']}")
    return raw


def deduplicate_signal(new_signal, existing_signals):
    """Check if a signal already exists (by address + type + source)."""
    for existing in existing_signals:
        if (existing.get("address_normalized") == new_signal.get("address_normalized") and
            existing.get("signal_type") == new_signal.get("signal_type") and
            existing.get("source_dataset") == new_signal.get("source_dataset")):
            return True
    return False


def match_signals_to_permits():
    """Match signals to permits by normalized address."""
    signals = load_signals()
    permits = load_permits()

    if not signals or not permits:
        print("No signals or permits to match")
        return

    # Build normalized permit address index
    permit_index = {}
    for p in permits:
        addr = normalize_address(p.get("address", ""), p.get("city", ""), p.get("state", ""))
        if addr:
            if addr not in permit_index:
                permit_index[addr] = []
            permit_index[addr].append(p.get("permit_number") or p.get("id"))

    # Match signals to permits
    matches = 0
    for signal in signals:
        signal_addr = signal.get("address_normalized", "")
        if not signal_addr:
            continue

        # Exact match
        if signal_addr in permit_index:
            for permit_id in permit_index[signal_addr]:
                if permit_id not in signal.get("linked_permits", []):
                    signal.setdefault("linked_permits", []).append(permit_id)
                    matches += 1

        # Partial match (signal address contained in permit address or vice versa)
        for permit_addr, permit_ids in permit_index.items():
            if signal_addr in permit_addr or permit_addr in signal_addr:
                for permit_id in permit_ids:
                    if permit_id not in signal.get("linked_permits", []):
                        signal.setdefault("linked_permits", []).append(permit_id)
                        matches += 1

    # Update permits with linked_signals
    signal_index = {}
    for s in signals:
        for permit_id in s.get("linked_permits", []):
            if permit_id not in signal_index:
                signal_index[permit_id] = []
            signal_index[permit_id].append(s.get("signal_id"))

    for p in permits:
        permit_id = p.get("permit_number") or p.get("id")
        if permit_id in signal_index:
            p["linked_signals"] = signal_index[permit_id]

    # Save updated data
    save_signals(signals)
    save_permits(permits)

    print(f"  ✓ Matched {matches} signal-permit links")


def collect_all_signals(days_back=90):
    """Collect signals from all configured sources."""
    existing_signals = load_signals()
    new_signals = []
    stats = {}

    print("=" * 60)
    print("PermitGrab - Pre-Construction Signal Collection")
    print(f"Pulling signals from {len(SIGNAL_SOURCES)} sources (last {days_back} days)")
    print("=" * 60)

    for source_key in SIGNAL_SOURCES:
        raw = fetch_signals(source_key, days_back)
        source_count = 0

        for record in raw:
            try:
                normalized = normalize_signal(record, source_key)
                if not normalized:
                    continue

                # Check for duplicates
                if deduplicate_signal(normalized, existing_signals + new_signals):
                    continue

                new_signals.append(normalized)
                source_count += 1

            except Exception as e:
                continue

        stats[source_key] = {
            "raw": len(raw),
            "new": source_count,
            "source_name": SIGNAL_SOURCES[source_key]["name"],
            "city": SIGNAL_SOURCES[source_key]["city"],
        }

    # Merge with existing signals
    all_signals = existing_signals + new_signals

    # Save signals
    save_signals(all_signals)

    # Calculate stats
    type_counts = {}
    status_counts = {"pending": 0, "approved": 0, "denied": 0, "withdrawn": 0}
    for s in all_signals:
        signal_type = s.get("signal_type", "unknown")
        type_counts[signal_type] = type_counts.get(signal_type, 0) + 1
        status = s.get("status", "pending")
        if status in status_counts:
            status_counts[status] += 1

    # Save stats
    stats_file = os.path.join(DATA_DIR, "signal_collection_stats.json")
    collection_stats = {
        "collected_at": datetime.now().isoformat(),
        "days_back": days_back,
        "total_signals": len(all_signals),
        "new_signals": len(new_signals),
        "source_stats": stats,
        "type_breakdown": type_counts,
        "status_breakdown": status_counts,
    }
    with open(stats_file, "w") as f:
        json.dump(collection_stats, f, indent=2)

    # Print summary
    print("\n" + "=" * 60)
    print("SIGNAL COLLECTION COMPLETE")
    print("=" * 60)
    print(f"Total signals in database: {len(all_signals)}")
    print(f"New signals collected: {len(new_signals)}")
    print(f"\nBy Source:")
    for key, s in sorted(stats.items(), key=lambda x: -x[1]["new"]):
        print(f"  {s['source_name']} ({s['city']}): {s['new']} new ({s['raw']} raw)")
    print(f"\nBy Type:")
    for signal_type, count in sorted(type_counts.items(), key=lambda x: -x[1]):
        print(f"  {signal_type}: {count}")
    print(f"\nBy Status:")
    for status, count in status_counts.items():
        print(f"  {status}: {count}")
    print(f"\nData saved to: {SIGNALS_FILE}")

    # Run address matching
    print("\nMatching signals to permits...")
    match_signals_to_permits()

    return all_signals, collection_stats


if __name__ == "__main__":
    signals, stats = collect_all_signals(days_back=90)
