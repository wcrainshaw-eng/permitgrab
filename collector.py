"""
PermitGrab - Data Collector
Pulls real permit data from free municipal APIs
Supports: Socrata (SODA API), ArcGIS REST, CKAN, CARTO
"""

import requests
import json
import os
import re
import time
import tempfile
from datetime import datetime, timedelta
from city_configs import (
    CITY_REGISTRY, TRADE_CATEGORIES, PERMIT_VALUE_TIERS, BULK_SOURCES,
    get_active_cities, get_city_config,
    get_active_bulk_sources, get_bulk_source_config
)

# Use Render persistent disk if available, otherwise local
if os.path.isdir('/var/data'):
    DATA_DIR = '/var/data'
else:
    DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(DATA_DIR, exist_ok=True)

# Rate limiting: 1 second between city pulls
RATE_LIMIT_DELAY = 1.0

# V12.29: Batch processing to prevent server overload
BATCH_SIZE = 50
BATCH_PAUSE_SECONDS = 5  # Pause between batches

# V12.29: Shorter timeout to fail fast on dead endpoints
API_TIMEOUT_SECONDS = 15

# V12.31: Bulk source settings
BULK_PAGE_SIZE = 50000  # Records per API call for bulk sources
BULK_MAX_PAGES = 20     # Max pages to fetch (1M records total)

# V12.2: Shared session with proper headers — required for Socrata to not block us
SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "PermitGrab/1.0 (permit lead aggregator; contact@permitgrab.com)",
    "Accept": "application/json",
    "Accept-Encoding": "gzip, deflate",
})

# Optional: Socrata app token for higher rate limits
SOCRATA_APP_TOKEN = os.environ.get("SOCRATA_APP_TOKEN", "")
if SOCRATA_APP_TOKEN:
    SESSION.headers["X-App-Token"] = SOCRATA_APP_TOKEN
    print(f"[Collector] Using Socrata app token: {SOCRATA_APP_TOKEN[:8]}...")

# V12.2: Collection lock to prevent concurrent runs
COLLECTION_LOCK_FILE = os.path.join(DATA_DIR, ".collection_lock")


def _acquire_lock():
    """Prevent concurrent collection runs."""
    if os.path.exists(COLLECTION_LOCK_FILE):
        try:
            with open(COLLECTION_LOCK_FILE) as f:
                lock_data = json.load(f)
            lock_time = datetime.fromisoformat(lock_data["started"])
            # If lock is older than 2 hours, assume it's stale
            if (datetime.now() - lock_time).total_seconds() < 7200:
                print(f"  [SKIP] Collection already running since {lock_data['started']}")
                return False
        except Exception:
            pass

    with open(COLLECTION_LOCK_FILE, "w") as f:
        json.dump({"started": datetime.now().isoformat(), "pid": os.getpid()}, f)
    return True


def _release_lock():
    """Release collection lock."""
    try:
        os.remove(COLLECTION_LOCK_FILE)
    except FileNotFoundError:
        pass


def atomic_write_json(filepath, data, indent=2):
    """
    V12.16: Atomic JSON write to prevent corruption.
    Writes to a temp file first, then renames to final path.
    os.rename() is atomic on POSIX systems, so the file is either
    fully written or not changed at all.
    """
    dir_path = os.path.dirname(filepath)
    try:
        # Write to temp file in same directory (required for atomic rename)
        fd, temp_path = tempfile.mkstemp(suffix='.tmp', dir=dir_path)
        try:
            with os.fdopen(fd, 'w') as f:
                json.dump(data, f, indent=indent, default=str)
            # Atomic rename
            os.rename(temp_path, filepath)
        except Exception:
            # Clean up temp file on error
            try:
                os.unlink(temp_path)
            except OSError:
                pass
            raise
    except Exception as e:
        print(f"  [ERROR] Atomic write failed for {filepath}: {e}")
        raise


# ============================================================================
# PLATFORM-SPECIFIC FETCHERS
# ============================================================================

def fetch_socrata(config, days_back):
    """Fetch permits from a Socrata SODA API."""
    endpoint = config["endpoint"]
    date_field = config["date_field"]
    limit = config.get("limit", 2000)

    # Calculate date filter
    since_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%dT00:00:00")

    where_clause = f"{date_field} > '{since_date}'"

    # V12.7: Add city filter for county/state datasets
    city_filter = config.get("city_filter")
    if city_filter:
        filter_field = city_filter["field"]
        filter_value = city_filter["value"]
        where_clause += f" AND upper({filter_field}) = upper('{filter_value}')"

    params = {
        "$limit": limit,
        "$order": f"{date_field} DESC",
        "$where": where_clause,
    }

    resp = SESSION.get(endpoint, params=params, timeout=API_TIMEOUT_SECONDS)
    resp.raise_for_status()
    return resp.json()


def fetch_arcgis(config, days_back):
    """Fetch permits from an ArcGIS REST API FeatureServer."""
    endpoint = config["endpoint"]
    date_field = config["date_field"]
    limit = config.get("limit", 2000)
    date_format = config.get("date_format", "date")  # "date", "epoch", or "none"

    # Calculate date filter
    since_dt = datetime.now() - timedelta(days=days_back)

    if date_format == "epoch":
        since_epoch = int(since_dt.timestamp() * 1000)
        where_clause = f"{date_field} >= {since_epoch}"
    elif date_format == "none":
        where_clause = "1=1"
    else:
        since_date = since_dt.strftime("%Y-%m-%d")
        where_clause = f"{date_field} >= DATE '{since_date}'"

    # V12.7: Add city filter for county datasets
    city_filter = config.get("city_filter")
    if city_filter:
        filter_field = city_filter["field"]
        filter_value = city_filter["value"]
        where_clause += f" AND upper({filter_field}) = upper('{filter_value}')"

    params = {
        "where": where_clause,
        "outFields": "*",
        "resultRecordCount": limit,
        "orderByFields": f"{date_field} DESC",
        "f": "json",
    }

    resp = SESSION.get(endpoint, params=params, timeout=API_TIMEOUT_SECONDS)
    resp.raise_for_status()
    data = resp.json()

    # V12.4: Detect ArcGIS-level errors (HTTP 200 with error JSON body)
    if "error" in data:
        error_msg = data["error"].get("message", "Unknown ArcGIS error")
        error_code = data["error"].get("code", "unknown")
        raise Exception(f"ArcGIS error {error_code}: {error_msg}")

    if "features" in data:
        results = [f["attributes"] for f in data["features"]]
        # If using "none" date_format, filter in Python
        if date_format == "none" and date_field and results:
            since_epoch = int(since_dt.timestamp() * 1000)
            results = [r for r in results if r.get(date_field, 0) and r[date_field] >= since_epoch]
        return results
    return []


def fetch_ckan(config, days_back):
    """Fetch permits from a CKAN datastore API."""
    endpoint = config["endpoint"]
    dataset_id = config["dataset_id"]
    limit = config.get("limit", 2000)
    date_field = config.get("date_field", "")

    # Calculate date filter
    since_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")

    params = {
        "resource_id": dataset_id,
        "limit": limit,
    }

    # CKAN doesn't have great date filtering, so we fetch and filter in Python
    if date_field:
        params["sort"] = f"{date_field} desc"

    resp = SESSION.get(endpoint, params=params, timeout=API_TIMEOUT_SECONDS)
    resp.raise_for_status()
    data = resp.json()

    if data.get("success") and "result" in data and "records" in data["result"]:
        records = data["result"]["records"]
        # Filter by date in Python
        if date_field:
            filtered = []
            for r in records:
                date_val = r.get(date_field, "")
                if date_val and str(date_val)[:10] >= since_date:
                    filtered.append(r)
            records = filtered

        # V12.7: Add city filter (Python-side filtering)
        city_filter = config.get("city_filter")
        if city_filter and records:
            filter_field = city_filter["field"]
            filter_value = city_filter["value"].upper()
            records = [r for r in records
                       if str(r.get(filter_field, "")).upper() == filter_value]

        return records
    return []


def fetch_carto(config, days_back):
    """Fetch permits from a CARTO SQL API."""
    endpoint = config["endpoint"]
    table_name = config.get("table_name", config["dataset_id"])
    limit = config.get("limit", 2000)
    date_field = config.get("date_field", "")

    # Calculate date filter
    since_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")

    # Build SQL query
    if date_field:
        sql = f"SELECT * FROM {table_name} WHERE {date_field} >= '{since_date}' ORDER BY {date_field} DESC LIMIT {limit}"
    else:
        sql = f"SELECT * FROM {table_name} LIMIT {limit}"

    params = {"q": sql, "format": "json"}

    resp = SESSION.get(endpoint, params=params, timeout=API_TIMEOUT_SECONDS)
    resp.raise_for_status()
    data = resp.json()

    # V12.4: Detect CARTO SQL errors
    if "error" in data:
        error_msgs = data.get("error", [])
        raise Exception(f"CARTO SQL error: {error_msgs}")

    if "rows" in data:
        return data["rows"]
    return []


# ============================================================================
# BULK SOURCE FETCHERS (V12.31)
# ============================================================================

def fetch_socrata_bulk(config, days_back=90):
    """
    V12.31: Fetch ALL permits from a bulk Socrata source with pagination.
    Returns all records without city filtering - caller handles grouping.
    """
    endpoint = config["endpoint"]
    date_field = config.get("date_field", "")

    # Calculate date filter
    since_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%dT00:00:00")

    all_records = []
    offset = 0

    for page in range(BULK_MAX_PAGES):
        params = {
            "$limit": BULK_PAGE_SIZE,
            "$offset": offset,
            "$order": f"{date_field} DESC" if date_field else ":id",
        }

        if date_field:
            params["$where"] = f"{date_field} > '{since_date}'"

        print(f"    Fetching page {page + 1} (offset {offset})...")

        try:
            resp = SESSION.get(endpoint, params=params, timeout=60)  # Longer timeout for bulk
            resp.raise_for_status()
            records = resp.json()

            if not records:
                print(f"    No more records at page {page + 1}")
                break

            all_records.extend(records)
            print(f"    Got {len(records)} records (total: {len(all_records)})")

            if len(records) < BULK_PAGE_SIZE:
                # Last page
                break

            offset += BULK_PAGE_SIZE
            time.sleep(1)  # Rate limit between pages

        except Exception as e:
            print(f"    [ERROR] Page {page + 1} failed: {e}")
            break

    return all_records


def slugify_city_name(city_name, state):
    """
    V12.31: Convert a city name to a URL-safe slug.
    e.g., "Newark City" -> "newark", "East Orange" -> "east-orange"
    """
    if not city_name:
        return None

    # Clean up common suffixes
    name = city_name.strip()
    for suffix in [" City", " Township", " Borough", " Town", " Village"]:
        if name.endswith(suffix):
            name = name[:-len(suffix)]

    # Convert to lowercase and replace spaces/special chars with hyphens
    slug = re.sub(r'[^a-z0-9]+', '-', name.lower())
    slug = slug.strip('-')

    # Add state suffix for disambiguation
    return f"{slug}-{state.lower()}" if slug else None


def collect_bulk_source(source_key, config, days_back=90):
    """
    V12.31: Collect permits from a bulk source and split by city.
    Returns dict of {city_slug: [permits]} and stats.
    """
    print(f"\n  === BULK SOURCE: {config['name']} ===")

    platform = config.get("platform", "socrata")
    city_field = config.get("city_field")
    state = config.get("state", "")

    if not city_field:
        print(f"    [ERROR] No city_field defined for bulk source {source_key}")
        return {}, {"status": "error_no_city_field"}

    # Fetch all records
    if platform == "socrata":
        raw_records = fetch_socrata_bulk(config, days_back)
    else:
        print(f"    [ERROR] Bulk mode not yet supported for platform: {platform}")
        return {}, {"status": f"error_unsupported_platform_{platform}"}

    if not raw_records:
        return {}, {"status": "empty", "raw": 0}

    print(f"    Total records fetched: {len(raw_records)}")

    # Group records by city
    city_groups = {}
    unknown_city_count = 0

    for record in raw_records:
        city_name = record.get(city_field, "").strip()
        if not city_name:
            unknown_city_count += 1
            continue

        # Normalize city name for grouping
        city_name_normalized = city_name.title()

        if city_name_normalized not in city_groups:
            city_groups[city_name_normalized] = []
        city_groups[city_name_normalized].append(record)

    print(f"    Found {len(city_groups)} unique cities")
    if unknown_city_count > 0:
        print(f"    ({unknown_city_count} records with no city value)")

    # Process each city group
    city_permits = {}  # city_slug -> [normalized permits]
    city_stats = {}

    for city_name, records in city_groups.items():
        # Create a virtual city config for normalization
        city_slug = slugify_city_name(city_name, state)
        if not city_slug:
            continue

        # Create virtual config for this city
        virtual_config = {
            "name": city_name,
            "state": state,
            "slug": city_slug,
            "platform": platform,
            "field_map": config.get("field_map", {}),
        }

        # Normalize each permit
        normalized = []
        for record in records:
            try:
                permit = normalize_permit_bulk(record, virtual_config, source_key)
                if permit and permit.get("permit_number"):
                    normalized.append(permit)
            except Exception:
                continue

        if normalized:
            city_permits[city_slug] = normalized
            city_stats[city_slug] = {
                "city_name": city_name,
                "raw": len(records),
                "normalized": len(normalized),
            }

    stats = {
        "status": "success",
        "source": source_key,
        "raw_total": len(raw_records),
        "cities_found": len(city_groups),
        "cities_with_permits": len(city_permits),
        "total_normalized": sum(len(p) for p in city_permits.values()),
        "city_breakdown": city_stats,
    }

    print(f"    Normalized {stats['total_normalized']} permits across {stats['cities_with_permits']} cities")

    return city_permits, stats


def normalize_permit_bulk(raw_record, virtual_config, source_key):
    """
    V12.31: Normalize a permit from a bulk source.
    Similar to normalize_permit but uses a virtual config.
    """
    fmap = virtual_config.get("field_map", {})

    def get_field(field_name):
        raw_key = fmap.get(field_name, "")
        if not raw_key:
            return ""
        return str(raw_record.get(raw_key, "")).strip()

    # Build address
    address = get_field("address")
    if not address:
        # Try common address field patterns
        for fallback in ["location", "property_address", "site_address", "street_address"]:
            val = str(raw_record.get(fallback, "")).strip()
            if val and val.lower() not in ["none", "n/a", ""]:
                address = val
                break

    if not address:
        address = "Address not provided"

    # Parse cost
    cost_str = get_field("estimated_cost")
    try:
        cost = float(re.sub(r'[^\d.]', '', cost_str)) if cost_str else 0
    except (ValueError, TypeError):
        cost = 0

    # Cap outlier values
    if cost > 50_000_000:
        cost = 50_000_000

    # Parse date
    date_str = get_field("filing_date")
    parsed_date = ""
    if date_str:
        for fmt in ["%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%m/%d/%Y"]:
            try:
                parsed_date = datetime.strptime(str(date_str)[:26], fmt).strftime("%Y-%m-%d")
                break
            except ValueError:
                continue
        if not parsed_date:
            parsed_date = str(date_str)[:10]

    # Build description
    desc = get_field("description") or get_field("work_type") or get_field("permit_type")

    # Classify trade
    trade = classify_trade(desc + " " + get_field("work_type") + " " + get_field("permit_type"))

    # Score value
    value_tier = score_value(cost)

    # Generate permit number if missing
    permit_num = get_field("permit_number")
    if not permit_num:
        import hashlib
        raw_str = f"{source_key}_{address}_{parsed_date}"
        permit_num = f"PG-{source_key[:3].upper()}-{hashlib.md5(raw_str.encode()).hexdigest()[:8]}"

    city_slug = virtual_config.get("slug", source_key)

    return {
        "id": f"{city_slug}_{permit_num}",
        "city": virtual_config.get("name", ""),
        "state": virtual_config.get("state", ""),
        "permit_number": sanitize_string(permit_num),
        "permit_type": sanitize_string(get_field("permit_type")),
        "work_type": sanitize_string(get_field("work_type")),
        "trade_category": trade,
        "address": sanitize_string(address),
        "zip": sanitize_string(get_field("zip")),
        "filing_date": parsed_date,
        "status": sanitize_string(get_field("status")),
        "estimated_cost": cost,
        "value_tier": value_tier,
        "description": sanitize_string(desc[:500]) if desc else "",
        "contact_name": sanitize_string(get_field("contractor_name")),
        "contact_phone": "",
        "borough": "",
        "source_city": city_slug,
        "source_bulk": source_key,  # Track which bulk source this came from
    }


def fetch_permits(city_key, days_back=30):
    """Fetch recent permits from a city's API using the appropriate platform fetcher.

    V12.2: Returns (data, status_string) tuple for proper failure tracking.
    """
    config = get_city_config(city_key)
    if not config:
        print(f"  [SKIP] Unknown city: {city_key}")
        return [], "skip"

    if not config.get("active", False):
        print(f"  [SKIP] Inactive city: {city_key}")
        return [], "skip"

    platform = config.get("platform", "socrata")
    print(f"  Fetching {config['name']} permits (last {days_back} days) via {platform}...")

    max_retries = 3
    last_error = None

    for attempt in range(max_retries):
        try:
            if platform == "socrata":
                raw = fetch_socrata(config, days_back)
            elif platform == "arcgis":
                raw = fetch_arcgis(config, days_back)
            elif platform == "ckan":
                raw = fetch_ckan(config, days_back)
            elif platform == "carto":
                raw = fetch_carto(config, days_back)
            else:
                print(f"  [ERROR] Unknown platform: {platform}")
                return [], "error_unknown_platform"

            print(f"  Got {len(raw)} raw permits from {config['name']}")

            if len(raw) >= config.get("limit", 2000):
                print(f"  [WARNING] Hit limit of {config.get('limit', 2000)}")

            return raw, "success"

        except requests.exceptions.HTTPError as e:
            status_code = e.response.status_code if e.response else "unknown"
            print(f"  [ERROR] HTTP {status_code} for {config['name']} (attempt {attempt+1}/{max_retries})")
            last_error = f"http_{status_code}"
            if status_code in (403, 404):
                break  # Don't retry 403/404
        except requests.exceptions.ConnectionError as e:
            print(f"  [ERROR] Connection failed for {config['name']} (attempt {attempt+1}/{max_retries}): {str(e)[:80]}")
            last_error = "connection_error"
        except requests.exceptions.Timeout:
            print(f"  [ERROR] Timeout for {config['name']} (attempt {attempt+1}/{max_retries})")
            last_error = "timeout"
        except Exception as e:
            print(f"  [ERROR] {config['name']}: {str(e)[:100]} (attempt {attempt+1}/{max_retries})")
            last_error = f"error_{str(e)[:50]}"

        if attempt < max_retries - 1:
            wait_time = 2 ** attempt  # 1s, 2s, 4s
            print(f"  Retrying in {wait_time}s...")
            time.sleep(wait_time)

    return [], last_error


# ============================================================================
# NORMALIZATION FUNCTIONS
# ============================================================================

def sanitize_string(value):
    """Remove control characters that break JSON parsing."""
    if not isinstance(value, str):
        return value
    # Remove ASCII control chars (0x00-0x1F) except common whitespace
    # \x09 = tab, \x0A = newline, \x0D = carriage return — keep these
    sanitized = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', value)
    # Replace newlines and tabs with spaces for single-line fields
    sanitized = sanitized.replace('\n', ' ').replace('\r', ' ').replace('\t', ' ')
    # Collapse multiple spaces
    sanitized = re.sub(r'  +', ' ', sanitized)
    return sanitized.strip()


def normalize_permit(raw_record, city_key):
    """Normalize a raw permit record into our standard schema."""
    config = get_city_config(city_key)
    if not config:
        return None

    fmap = config["field_map"]

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

    # Fallback: try common address field names not in field_map
    if not address:
        for fallback_key in ["location", "project_address", "site_address",
                             "property_address", "address_full", "location_1",
                             "mapped_location"]:
            val = str(raw_record.get(fallback_key, "")).strip()
            if val and val != "None":
                address = val
                break

    if not address:
        address = "Address not provided"

    # Parse cost
    cost_str = get_field("estimated_cost")
    try:
        cost = float(re.sub(r'[^\d.]', '', cost_str)) if cost_str else 0
    except (ValueError, TypeError):
        cost = 0

    # V12.21: Sanity check - cap outlier values at $50M (likely data entry errors)
    MAX_REASONABLE_COST = 50_000_000  # $50M
    if cost > MAX_REASONABLE_COST:
        cost = MAX_REASONABLE_COST

    # Parse date
    date_str = get_field("filing_date")
    parsed_date = ""
    if date_str:
        # Check if it's an epoch timestamp (milliseconds)
        if str(date_str).isdigit() and len(str(date_str)) >= 10:
            try:
                epoch_ms = int(date_str)
                parsed_date = datetime.fromtimestamp(epoch_ms / 1000).strftime("%Y-%m-%d")
            except (ValueError, OSError):
                pass
        if not parsed_date:
            for fmt in ["%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%m/%d/%Y"]:
                try:
                    parsed_date = datetime.strptime(str(date_str)[:26], fmt).strftime("%Y-%m-%d")
                    break
                except ValueError:
                    continue
        if not parsed_date:
            parsed_date = str(date_str)[:10]

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

    # Handle missing permit numbers with synthetic IDs
    import hashlib
    permit_num = get_field("permit_number")
    if not permit_num:
        # Generate a synthetic permit number from city + date + hash
        raw_str = f"{city_key}_{address}_{parsed_date}"
        permit_num = f"PG-{city_key[:3].upper()}-{hashlib.md5(raw_str.encode()).hexdigest()[:8]}"

    return {
        "id": f"{city_key}_{permit_num}",
        "city": config["name"],
        "state": config["state"],
        "permit_number": sanitize_string(permit_num),
        "permit_type": sanitize_string(get_field("permit_type")),
        "work_type": sanitize_string(get_field("work_type")),
        "trade_category": trade,
        "address": sanitize_string(address),
        "zip": sanitize_string(get_field("zip")),
        "filing_date": parsed_date,
        "status": sanitize_string(get_field("status")),
        "estimated_cost": cost,
        "value_tier": value_tier,
        "description": sanitize_string(desc[:500]) if desc else "",
        "contact_name": sanitize_string(contact),
        "contact_phone": sanitize_string(phone),
        "borough": sanitize_string(get_field("borough")) if "borough" in fmap else "",
        "source_city": city_key,
    }


def classify_trade(text):
    """
    Classify a permit into a trade category based on description text.
    Uses keyword matching with priority to avoid over-classifying as General Construction.
    """
    if not text:
        return "General Construction"

    text_lower = text.lower()
    scores = {}

    # Check all trades for keyword matches
    for trade, keywords in TRADE_CATEGORIES.items():
        score = sum(1 for kw in keywords if kw in text_lower)
        if score > 0:
            scores[trade] = score

    if not scores:
        return "General Construction"

    # Priority order for ties: specific trades > New Construction/Addition > General
    priority_trades = [
        "Electrical", "Plumbing", "HVAC", "Roofing", "Solar", "Fire Protection",
        "Demolition", "Signage", "Windows & Doors", "Structural",
        "Interior Renovation", "Landscaping & Exterior",
        "New Construction", "Addition", "General Construction"
    ]

    # Get all non-General matches
    specific_matches = {t: s for t, s in scores.items() if t not in ["General Construction"]}

    if specific_matches:
        # Return the specific trade with the highest score
        # On ties, use priority order
        max_score = max(specific_matches.values())
        top_matches = [t for t, s in specific_matches.items() if s == max_score]

        if len(top_matches) == 1:
            return top_matches[0]

        # Break ties with priority order
        for trade in priority_trades:
            if trade in top_matches:
                return trade

        return top_matches[0]

    # Only General Construction matched
    return "General Construction"


def score_value(cost):
    """Score the lead value based on estimated project cost."""
    if cost >= PERMIT_VALUE_TIERS["high"]["min_cost"]:
        return "high"
    elif cost >= PERMIT_VALUE_TIERS["medium"]["min_cost"]:
        return "medium"
    return "low"


def score_permit_quality(permit):
    """
    V12.22: Score a permit's data quality for deduplication.
    Higher score = better data (prefer keeping this one).
    """
    score = 0

    # Has a real address (not placeholder)
    address = permit.get('address', '') or ''
    address_clean = address.strip().lower()
    if address_clean and address_clean not in ['not provided', 'address not provided', 'n/a', 'none', '']:
        score += 30
        # Bonus for having a street number
        if any(c.isdigit() for c in address_clean[:5]):
            score += 10

    # Has estimated cost
    if permit.get('estimated_cost', 0) > 0:
        score += 20

    # Has contact info
    if permit.get('contact_name'):
        score += 15
    if permit.get('contact_phone'):
        score += 15

    # Has description
    if permit.get('description'):
        score += 5

    # Has city assigned
    if permit.get('city'):
        score += 5

    return score


def deduplicate_permits(permits):
    """
    V12.22: Remove duplicate permits by permit_number.
    When duplicates exist (same permit collected under multiple city filters),
    keep the one with the best data quality.
    """
    if not permits:
        return permits

    seen = {}  # permit_number -> best permit
    duplicates_found = 0

    for permit in permits:
        permit_num = permit.get('permit_number', '')
        if not permit_num:
            # No permit number - can't dedupe, keep it
            continue

        if permit_num in seen:
            duplicates_found += 1
            # Compare quality scores and keep the better one
            existing = seen[permit_num]
            existing_score = score_permit_quality(existing)
            new_score = score_permit_quality(permit)

            if new_score > existing_score:
                seen[permit_num] = permit
        else:
            seen[permit_num] = permit

    # Build deduplicated list
    deduped = list(seen.values())

    # Also include permits without permit_number (rare edge case)
    for permit in permits:
        if not permit.get('permit_number'):
            deduped.append(permit)

    if duplicates_found > 0:
        print(f"  [V12.22] Removed {duplicates_found} duplicate permits")

    return deduped


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


# ============================================================================
# HISTORY FETCHERS
# ============================================================================

def fetch_history_socrata(config, years_back=3):
    """Fetch historical permits from a Socrata SODA API."""
    endpoint = config["endpoint"]
    date_field = config["date_field"]

    since_date = (datetime.now() - timedelta(days=years_back * 365)).strftime("%Y-%m-%d")

    params = {
        "$limit": 5000,
        "$order": f"{date_field} DESC",
        "$where": f"{date_field} > '{since_date}T00:00:00'",
    }

    resp = requests.get(endpoint, params=params, timeout=API_TIMEOUT_SECONDS)
    resp.raise_for_status()
    return resp.json()


def fetch_history_arcgis(config, years_back=3):
    """Fetch historical permits from an ArcGIS REST API FeatureServer."""
    endpoint = config["endpoint"]
    date_field = config["date_field"]
    date_format = config.get("date_format", "date")

    since_dt = datetime.now() - timedelta(days=years_back * 365)

    if date_format == "epoch":
        since_epoch = int(since_dt.timestamp() * 1000)
        where_clause = f"{date_field} >= {since_epoch}"
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

    resp = requests.get(endpoint, params=params, timeout=API_TIMEOUT_SECONDS)
    resp.raise_for_status()
    data = resp.json()

    if "features" in data:
        results = [f["attributes"] for f in data["features"]]
        if date_format == "none" and date_field and results:
            since_epoch = int(since_dt.timestamp() * 1000)
            results = [r for r in results if r.get(date_field, 0) and r[date_field] >= since_epoch]
        return results
    return []


def fetch_history_ckan(config, years_back=3):
    """Fetch historical permits from a CKAN API."""
    endpoint = config["endpoint"]
    dataset_id = config["dataset_id"]
    date_field = config.get("date_field", "")

    since_date = (datetime.now() - timedelta(days=years_back * 365)).strftime("%Y-%m-%d")

    params = {
        "resource_id": dataset_id,
        "limit": 5000,
    }
    if date_field:
        params["sort"] = f"{date_field} desc"

    resp = requests.get(endpoint, params=params, timeout=API_TIMEOUT_SECONDS)
    resp.raise_for_status()
    data = resp.json()

    if data.get("success") and "result" in data and "records" in data["result"]:
        records = data["result"]["records"]
        if date_field:
            filtered = []
            for r in records:
                date_val = r.get(date_field, "")
                if date_val and str(date_val)[:10] >= since_date:
                    filtered.append(r)
            return filtered
        return records
    return []


def fetch_history_carto(config, years_back=3):
    """Fetch historical permits from a CARTO SQL API."""
    endpoint = config["endpoint"]
    table_name = config.get("table_name", config["dataset_id"])
    date_field = config.get("date_field", "")

    since_date = (datetime.now() - timedelta(days=years_back * 365)).strftime("%Y-%m-%d")

    if date_field:
        sql = f"SELECT * FROM {table_name} WHERE {date_field} >= '{since_date}' ORDER BY {date_field} DESC LIMIT 5000"
    else:
        sql = f"SELECT * FROM {table_name} LIMIT 5000"

    params = {"q": sql, "format": "json"}

    resp = requests.get(endpoint, params=params, timeout=API_TIMEOUT_SECONDS)
    resp.raise_for_status()
    data = resp.json()

    if "rows" in data:
        return data["rows"]
    return []


def fetch_permit_history(city_key, years_back=3):
    """Fetch historical permits for a city (last 3 years)."""
    config = get_city_config(city_key)
    if not config:
        print(f"  [SKIP] Unknown city: {city_key}")
        return []

    if not config.get("active", False):
        print(f"  [SKIP] Inactive city: {city_key}")
        return []

    platform = config.get("platform", "socrata")
    print(f"  Fetching {config['name']} permit history (last {years_back} years)...")

    try:
        if platform == "socrata":
            raw = fetch_history_socrata(config, years_back)
        elif platform == "arcgis":
            raw = fetch_history_arcgis(config, years_back)
        elif platform == "ckan":
            raw = fetch_history_ckan(config, years_back)
        elif platform == "carto":
            raw = fetch_history_carto(config, years_back)
        else:
            return []

        print(f"  Got {len(raw)} historical permits from {config['name']}")
        return raw
    except requests.exceptions.HTTPError as e:
        print(f"  [ERROR] HTTP {e.response.status_code} for {config['name']}: {e}")
        return []
    except requests.exceptions.Timeout:
        print(f"  [ERROR] Timeout for {config['name']} (history takes longer)")
        return []
    except Exception as e:
        print(f"  [ERROR] {config['name']}: {e}")
        return []


# ============================================================================
# COLLECTION FUNCTIONS
# ============================================================================

def collect_permit_history(years_back=3):
    """Collect permit history from all active cities, indexed by normalized address."""
    history_index = {}
    stats = {}
    active_cities = get_active_cities()

    print("=" * 60)
    print("PermitGrab - Permit History Collection")
    print(f"Pulling {years_back} years of history from {len(active_cities)} cities")
    print("=" * 60)

    for city_key in active_cities:
        config = get_city_config(city_key)
        raw = fetch_permit_history(city_key, years_back)
        city_count = 0

        for record in raw:
            try:
                normalized = normalize_permit(record, city_key)
                if not normalized or not normalized["permit_number"]:
                    continue

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
            "city_name": config["name"],
        }

        # Rate limiting
        time.sleep(RATE_LIMIT_DELAY)

    # Sort permits by date for each address
    for addr_key in history_index:
        history_index[addr_key]["permits"].sort(
            key=lambda x: x["filing_date"] or "0000-00-00",
            reverse=True
        )
        history_index[addr_key]["permit_count"] = len(history_index[addr_key]["permits"])

    # Save to JSON (atomic write)
    output_file = os.path.join(DATA_DIR, "permit_history.json")
    atomic_write_json(output_file, history_index)

    total_addresses = len(history_index)
    total_permits = sum(len(h["permits"]) for h in history_index.values())
    repeat_renovators = sum(1 for h in history_index.values() if len(h["permits"]) >= 3)

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


def load_existing_permits():
    """V12.33: Load existing permits from disk for additive collection."""
    permits_file = os.path.join(DATA_DIR, "permits.json")
    if os.path.exists(permits_file):
        try:
            with open(permits_file, 'r') as f:
                permits = json.load(f)
            print(f"[V12.33] Loaded {len(permits)} existing permits from disk")
            return permits
        except Exception as e:
            print(f"[V12.33] Could not load existing permits: {e}")
    return []


def merge_permits(existing, new_permits):
    """
    V12.33: Merge new permits into existing data, deduplicating by permit_number.
    New permits with the same permit_number overwrite old ones (fresher data).
    """
    # Build index of existing permits
    by_permit_num = {}
    for p in existing:
        pn = p.get('permit_number', '')
        if pn:
            by_permit_num[pn] = p

    # Add/update with new permits
    new_count = 0
    updated_count = 0
    for p in new_permits:
        pn = p.get('permit_number', '')
        if pn:
            if pn in by_permit_num:
                updated_count += 1
            else:
                new_count += 1
            by_permit_num[pn] = p

    merged = list(by_permit_num.values())
    print(f"[V12.33] Merged: {new_count} new, {updated_count} updated, {len(merged)} total")
    return merged


def collect_refresh(days_back=7):
    """
    V12.33: Refresh collection - only fetch recent permits and merge with existing.
    This is the default mode for scheduled collection (every 6-12 hours).
    Failed sources don't wipe existing data.
    """
    if not _acquire_lock():
        return [], {}

    try:
        print("=" * 60)
        print("PermitGrab - REFRESH Collection (Additive Mode)")
        print(f"Fetching permits from last {days_back} days only")
        print("=" * 60)

        # Load existing permits first
        existing_permits = load_existing_permits()

        # Collect new permits
        new_permits, stats = _collect_all_inner(days_back, additive_mode=True)

        # Merge with existing
        merged = merge_permits(existing_permits, new_permits)

        # Save merged result
        output_file = os.path.join(DATA_DIR, "permits.json")
        atomic_write_json(output_file, merged)
        print(f"[V12.33] Saved {len(merged)} total permits")

        return merged, stats
    finally:
        _release_lock()


def collect_full(days_back=60):
    """
    V12.33: Full collection - rebuild the entire dataset from scratch.
    Use for initial data load and periodic cleanup (once per day at 2 AM).
    """
    if not _acquire_lock():
        return [], {}

    try:
        print("=" * 60)
        print("PermitGrab - FULL Collection (Rebuild Mode)")
        print(f"Fetching all permits from last {days_back} days")
        print("=" * 60)
        return _collect_all_inner(days_back, additive_mode=False)
    finally:
        _release_lock()


def collect_single_source(source_key, source_type='bulk'):
    """
    V12.33: Collect from a single source and merge with existing data.
    Use when adding a new bulk source mid-day.

    source_type: 'bulk' for BULK_SOURCES, 'city' for CITY_REGISTRY
    """
    if not _acquire_lock():
        return [], {}

    try:
        print("=" * 60)
        print(f"PermitGrab - SINGLE SOURCE Collection: {source_key}")
        print("=" * 60)

        existing_permits = load_existing_permits()
        new_permits = []
        stats = {}

        if source_type == 'bulk':
            config = get_bulk_source_config(source_key)
            if not config:
                print(f"[ERROR] Bulk source not found: {source_key}")
                return existing_permits, {"error": "source_not_found"}

            city_permits, source_stats = collect_bulk_source(source_key, config, days_back=90)
            for city_slug, permits in city_permits.items():
                new_permits.extend(permits)
            stats[source_key] = source_stats
            print(f"  ✓ {config['name']}: {len(new_permits)} permits")

        else:  # city
            config = get_city_config(source_key)
            if not config:
                print(f"[ERROR] City not found: {source_key}")
                return existing_permits, {"error": "source_not_found"}

            raw, fetch_status = fetch_permits(source_key, days_back=60)
            for record in raw:
                try:
                    normalized = normalize_permit(record, source_key)
                    if normalized and normalized.get("permit_number"):
                        new_permits.append(normalized)
                except Exception:
                    continue
            stats[source_key] = {"raw": len(raw), "normalized": len(new_permits), "status": fetch_status}
            print(f"  ✓ {config['name']}: {len(new_permits)} permits")

        # Merge with existing
        merged = merge_permits(existing_permits, new_permits)

        # Save merged result
        output_file = os.path.join(DATA_DIR, "permits.json")
        atomic_write_json(output_file, merged)
        print(f"[V12.33] Saved {len(merged)} total permits")

        return merged, stats
    finally:
        _release_lock()


def collect_all(days_back=30):
    """Collect permits from all active cities (legacy - calls refresh mode)."""
    # V12.33: Default to refresh mode for backwards compatibility
    return collect_refresh(days_back)


def _collect_all_inner(days_back=30, additive_mode=True):
    """Inner implementation of collect_all (called with lock held).

    V12.33: additive_mode controls whether we save incrementally:
      - True (default): Returns permits without saving (caller handles merge)
      - False: Saves directly (full rebuild mode)
    """
    all_permits = []
    stats = {}
    bulk_stats = {}
    cities_from_bulk = set()  # Track cities covered by bulk sources

    # V12.31: First, process bulk sources (county/state-level datasets)
    active_bulk_sources = get_active_bulk_sources()
    if active_bulk_sources:
        print("=" * 60)
        print("PermitGrab - BULK SOURCE Collection")
        print(f"Processing {len(active_bulk_sources)} bulk sources (counties/states)")
        print("=" * 60)

        for source_key in active_bulk_sources:
            config = get_bulk_source_config(source_key)
            if not config:
                continue

            try:
                city_permits, source_stats = collect_bulk_source(source_key, config, days_back=90)

                # Add all permits from bulk source
                for city_slug, permits in city_permits.items():
                    all_permits.extend(permits)
                    cities_from_bulk.add(city_slug)

                bulk_stats[source_key] = source_stats
                print(f"  ✓ {config['name']}: {source_stats.get('total_normalized', 0)} permits from {source_stats.get('cities_with_permits', 0)} cities")

            except Exception as e:
                print(f"  ✗ {config['name']}: ERROR - {str(e)[:100]}")
                bulk_stats[source_key] = {"status": f"error: {str(e)[:100]}"}

            # Pause between bulk sources
            time.sleep(5)

        print(f"\n  Bulk sources complete: {len(cities_from_bulk)} cities from bulk data")
        print(f"  Total permits from bulk: {len(all_permits)}")

    # V12.31: Now process individual city APIs
    active_cities = get_active_cities()

    # V12 Fix 4.2: Skip cities with 10+ consecutive failures to save time
    failure_tracker_path = os.path.join(DATA_DIR, "city_failures.json")
    try:
        with open(failure_tracker_path) as f:
            failure_tracker = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        failure_tracker = {}

    skipped_count = 0
    filtered_cities = []
    for city_key in active_cities:
        if failure_tracker.get(city_key, 0) >= 10:
            skipped_count += 1
        else:
            filtered_cities.append(city_key)

    if skipped_count > 0:
        print(f"  Skipping {skipped_count} cities with 10+ consecutive failures")
        print(f"  (Reset by deleting {failure_tracker_path})")

    active_cities = filtered_cities

    print("\n" + "=" * 60)
    print("PermitGrab - INDIVIDUAL CITY Collection")
    print(f"Pulling permits from {len(active_cities)} direct city APIs (last {days_back} days)")
    print("=" * 60)

    for i, city_key in enumerate(active_cities):
        try:
            config = get_city_config(city_key)
            raw, fetch_status = fetch_permits(city_key, days_back)
            city_permits = []

            for record in raw:
                try:
                    normalized = normalize_permit(record, city_key)
                    if normalized and normalized["permit_number"]:
                        city_permits.append(normalized)
                except Exception:
                    continue

            all_permits.extend(city_permits)

            # V12.2: Use the ACTUAL fetch status, not always "success"
            if fetch_status == "success":
                stats[city_key] = {
                    "raw": len(raw),
                    "normalized": len(city_permits),
                    "city_name": config["name"],
                    "status": "success" if len(city_permits) > 0 else "success_empty",
                }
                print(f"  ✓ {config['name']}: {len(city_permits)} permits")
            elif fetch_status == "skip":
                stats[city_key] = {
                    "raw": 0,
                    "normalized": 0,
                    "city_name": config["name"] if config else city_key,
                    "status": "skip",
                }
            else:
                stats[city_key] = {
                    "raw": 0,
                    "normalized": 0,
                    "city_name": config["name"] if config else city_key,
                    "status": fetch_status,
                }
                print(f"  ✗ {config['name'] if config else city_key}: FAILED ({fetch_status})")

        except Exception as e:
            config_name = config.get("name", city_key) if config else city_key
            stats[city_key] = {
                "raw": 0,
                "normalized": 0,
                "city_name": config_name,
                "status": f"error: {str(e)[:100]}",
            }
            print(f"  ✗ {city_key}: {str(e)[:100]}")

        # Rate limiting
        time.sleep(RATE_LIMIT_DELAY)

        # V12.29: Batch processing - save and pause every BATCH_SIZE cities
        if (i + 1) % BATCH_SIZE == 0 and all_permits:
            output_file = os.path.join(DATA_DIR, "permits.json")
            atomic_write_json(output_file, all_permits)
            # V12.30: Update timestamp on every batch so homepage shows recent activity
            stats_file = os.path.join(DATA_DIR, "collection_stats.json")
            batch_stats = {
                "collected_at": datetime.now().isoformat(),
                "total_permits": len(all_permits),
                "cities_processed": i + 1,
                "in_progress": True,
            }
            try:
                atomic_write_json(stats_file, batch_stats)
            except Exception as e:
                print(f"  [WARNING] Failed to update batch stats: {e}")
            print(f"  [Batch {(i+1)//BATCH_SIZE}: {len(all_permits)} permits after {i+1} cities]")
            # V12.29: Pause between batches to let server breathe
            print(f"  [Pausing {BATCH_PAUSE_SECONDS}s before next batch...]")
            time.sleep(BATCH_PAUSE_SECONDS)

    # V12.22: Deduplicate permits by permit_number
    # County datasets split by city_filter can cause the same permit to appear
    # under multiple cities if the filter doesn't match exactly
    original_count = len(all_permits)
    all_permits = deduplicate_permits(all_permits)
    if original_count != len(all_permits):
        print(f"  [V12.22] Final count: {len(all_permits)} unique permits (was {original_count})")

    # Trade category breakdown
    trade_counts = {}
    for p in all_permits:
        cat = p["trade_category"]
        trade_counts[cat] = trade_counts.get(cat, 0) + 1

    # Value tier breakdown
    value_counts = {"high": 0, "medium": 0, "low": 0}
    for p in all_permits:
        value_counts[p["value_tier"]] = value_counts.get(p["value_tier"], 0) + 1

    # V12.33: In additive mode, caller handles saving after merge
    # In full mode, save directly here
    if not additive_mode:
        output_file = os.path.join(DATA_DIR, "permits.json")
        print(f"[V12.33] FULL MODE: Writing {len(all_permits)} permits to {output_file}...")
        atomic_write_json(output_file, all_permits)
        print(f"[V12.33] Permits written successfully.")
    else:
        print(f"[V12.33] ADDITIVE MODE: Returning {len(all_permits)} permits for merge")

    # V12.30: Save stats FIRST with explicit error handling
    # This ensures timestamp updates even if hot-reload fails
    stats_file = os.path.join(DATA_DIR, "collection_stats.json")
    collection_stats = {
        "collected_at": datetime.now().isoformat(),
        "days_back": days_back,
        "total_permits": len(all_permits),
        "city_stats": stats,
        "bulk_stats": bulk_stats,  # V12.31: Include bulk source stats
        "cities_from_bulk": len(cities_from_bulk),  # V12.31
        "trade_breakdown": dict(sorted(trade_counts.items(), key=lambda x: -x[1])),
        "value_breakdown": value_counts,
        "in_progress": False,  # Mark as complete
    }
    print(f"[V12.30] Writing collection stats to {stats_file}...")
    try:
        atomic_write_json(stats_file, collection_stats)
        print(f"[V12.30] Collection stats written successfully.")
    except Exception as e:
        print(f"[V12.30] ERROR writing collection stats: {e}")

    # V12 Fix 4.1: Save diagnostic report
    diagnostic = {
        "collected_at": datetime.now().isoformat(),
        "total_active_cities": len(active_cities),
        "cities_with_permits": sum(1 for s in stats.values() if s.get("normalized", 0) > 0),
        "cities_with_errors": sum(1 for s in stats.values() if "error" in str(s.get("status", ""))),
        "cities_timeout": sum(1 for s in stats.values() if s.get("status") == "timeout"),
        "cities_connection_error": sum(1 for s in stats.values() if s.get("status") == "connection_error"),
        "cities_zero_permits": sum(1 for s in stats.values() if s.get("status") == "success" and s.get("normalized", 0) == 0),
        "by_status": {},
        "failing_cities": [],
    }

    for key, s in stats.items():
        status = str(s.get("status", "unknown"))
        diagnostic["by_status"][status] = diagnostic["by_status"].get(status, 0) + 1
        if s.get("normalized", 0) == 0:
            diagnostic["failing_cities"].append({
                "key": key,
                "name": s.get("city_name", key),
                "status": status,
            })

    diag_file = os.path.join(DATA_DIR, "collection_diagnostic.json")
    atomic_write_json(diag_file, diagnostic)

    # V12 Fix 4.2: Track consecutive failures
    failure_tracker_path = os.path.join(DATA_DIR, "city_failures.json")
    try:
        with open(failure_tracker_path) as f:
            failure_tracker = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        failure_tracker = {}

    # V12.2: Fixed failure tracker logic
    for key, s in stats.items():
        status = str(s.get("status", "unknown"))
        if status.startswith("success") and s.get("normalized", 0) > 0:
            failure_tracker[key] = 0  # Reset on actual data received
        elif status == "success_empty":
            # API responded but returned 0 permits — don't count as failure
            # (might just have no recent permits)
            pass
        elif status == "skip":
            # Intentionally skipped — don't count
            pass
        else:
            failure_tracker[key] = failure_tracker.get(key, 0) + 1

    atomic_write_json(failure_tracker_path, failure_tracker)

    # Log cities with 5+ consecutive failures
    chronic_failures = {k: v for k, v in failure_tracker.items() if v >= 5}
    if chronic_failures:
        print(f"\n[WARNING] {len(chronic_failures)} cities have failed 5+ times consecutively:")
        for k, v in sorted(chronic_failures.items(), key=lambda x: -x[1])[:10]:
            print(f"  {k}: {v} consecutive failures")

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

    # V12.18: Hot-reload data in the running server without restart
    # Since collector runs in the same process via APScheduler, we can
    # directly call the server's reload function to refresh in-memory data
    try:
        from server import preload_data_from_disk
        preload_data_from_disk()
        print(f"[V12.18] Hot-reloaded {len(all_permits)} permits into server memory")
    except ImportError as e:
        print(f"[V12.18] Could not hot-reload server data: {e}")
    except Exception as e:
        print(f"[V12.18] Hot-reload error: {e}")

    return all_permits, collection_stats


if __name__ == "__main__":
    permits, stats = collect_all(days_back=60)
