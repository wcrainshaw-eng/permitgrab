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
# V12.54: Import static lookups from city_configs, but city/bulk source functions from SQLite wrapper
from city_configs import TRADE_CATEGORIES, PERMIT_VALUE_TIERS
from city_source_db import (
    get_active_cities, get_city_config,
    get_active_bulk_sources, get_bulk_source_config,
    record_collection, reset_failure, increment_failure
)
import db as permitdb  # V12.50: SQLite database layer


# V15: Helper function for logging collection runs to scraper_runs table
def _log_v15_collection(city_key, city_name, state, permits_found, permits_inserted,
                        status, error_message=None, duration_ms=None):
    """V15: Log collection run to scraper_runs and update prod_cities if exists."""
    try:
        # Generate city slug
        city_slug = city_name.lower().replace(' ', '-').replace(',', '') if city_name else city_key

        # Log to scraper_runs
        permitdb.log_scraper_run(
            source_name=city_key,
            city=city_name,
            state=state,
            city_slug=city_slug,
            permits_found=permits_found,
            permits_inserted=permits_inserted,
            status=status,
            error_message=error_message,
            duration_ms=duration_ms,
            collection_type='scheduled',
            triggered_by='collector'
        )

        # Update prod_cities if this city exists there
        is_prod, _ = permitdb.is_prod_city(city_slug)
        if is_prod:
            if status in ('success', 'no_new'):
                # Get newest permit date
                newest_date = None
                if permits_found > 0:
                    conn = permitdb.get_connection()
                    row = conn.execute(
                        "SELECT MAX(filing_date) as newest FROM permits WHERE city = ?",
                        (city_name,)
                    ).fetchone()
                    if row and row['newest']:
                        newest_date = row['newest']

                permitdb.update_prod_city_collection(
                    city_slug,
                    permits_found=permits_inserted,
                    last_permit_date=newest_date,
                    error=None
                )
            else:
                permitdb.update_prod_city_collection(
                    city_slug,
                    error=error_message or status
                )
    except Exception as e:
        # Don't let V15 logging errors break collection
        print(f"  [V15] Logging error: {str(e)[:50]}")


def parse_address_value(val):
    """V12.55c/V12.57: Parse Socrata location fields and GeoJSON points.
    Handles:
      - Socrata location: {'latitude': '39.23', 'longitude': '-77.27', 'human_address': '{"address": "123 MAIN ST", ...}'}
      - GeoJSON Point: {'type': 'Point', 'coordinates': [-117.55, 33.83]}
      - String representations of either format
    Returns the street address, or empty string if only coordinates.
    """
    if not val:
        return ''
    if isinstance(val, dict):
        # V12.57: Handle GeoJSON Point objects — these have no address, just coords
        if val.get('type') == 'Point' and val.get('coordinates'):
            return ''  # No address info in GeoJSON points

        human = val.get('human_address', '')
        if human:
            try:
                if isinstance(human, str):
                    human = json.loads(human)
                if isinstance(human, dict):
                    parts = []
                    if human.get('address'):
                        parts.append(human['address'].strip())
                    # V13.1: Return empty string if human_address has no useful data
                    # (all fields empty), NOT the raw JSON object
                    return ' '.join(parts) if parts else ''
            except (json.JSONDecodeError, TypeError):
                pass
        if val.get('address'):
            return str(val['address']).strip()
        lat = val.get('latitude') or val.get('lat')
        lng = val.get('longitude') or val.get('lng') or val.get('lon')
        if lat and lng:
            return ''  # V12.57: Don't show "Near lat, lng" — not useful as address
        return ''
    s = str(val).strip()
    # V12.57: Handle string representations of GeoJSON Point
    if s.startswith('{') and "'type': 'Point'" in s:
        return ''
    if s.startswith('{') and ('human_address' in s or 'latitude' in s):
        try:
            import ast
            parsed = ast.literal_eval(s)
            return parse_address_value(parsed)
        except (ValueError, SyntaxError):
            try:
                parsed = json.loads(s.replace("'", '"'))
                return parse_address_value(parsed)
            except (json.JSONDecodeError, ValueError):
                pass
    return s


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
API_TIMEOUT_SECONDS = 30  # V12.48: Increased from 15 to 30 per spec

# V12.31: Bulk source settings
# V12.40: Reduced from 50K to 10K to prevent memory issues on Render (512MB)
BULK_PAGE_SIZE = 10000  # Records per API call for bulk sources
BULK_MAX_PAGES = 50     # Max pages to fetch (500K records total)

# V12.48: Track failures to skip broken endpoints after 3 failures
ENDPOINT_FAILURES = {}  # {city_key: failure_count}
MAX_FAILURES_BEFORE_SKIP = 3

def reset_failure_tracking():
    """V12.48: Reset failure tracking at start of new collection run."""
    global ENDPOINT_FAILURES
    ENDPOINT_FAILURES = {}

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
    """Prevent concurrent collection runs. V12.57: Reduced stale timeout to 30 min."""
    if os.path.exists(COLLECTION_LOCK_FILE):
        try:
            with open(COLLECTION_LOCK_FILE) as f:
                lock_data = json.load(f)
            lock_time = datetime.fromisoformat(lock_data["started"])
            # V12.57: Reduced from 2 hours to 30 min — instances get killed on deploy
            # and leave orphaned locks. 30 min is enough for a normal collection cycle.
            if (datetime.now() - lock_time).total_seconds() < 1800:
                print(f"  [SKIP] Collection already running since {lock_data['started']}")
                return False
            else:
                print(f"  [LOCK] Stale lock from {lock_data['started']} — overriding")
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


def check_data_freshness(city_name, permits):
    """V12.58: Log warning if newest permit is >30 days old.
    Also returns (newest_date, days_stale) for metrics."""
    if not permits:
        return None, None
    dates = [p.get('filing_date', '') for p in permits if p.get('filing_date')]
    if not dates:
        print(f"[FRESHNESS] WARNING: {city_name} has NO filing dates at all")
        return None, None
    newest = max(dates)
    try:
        # Handle various date formats
        if 'T' in newest:
            newest_dt = datetime.fromisoformat(newest.replace('Z', '+00:00').split('+')[0])
        else:
            newest_dt = datetime.strptime(newest[:10], '%Y-%m-%d')
        days_old = (datetime.now() - newest_dt).days
        if days_old > 30:
            print(f"[FRESHNESS] WARNING: {city_name} data is {days_old} days stale (newest: {newest})")
        return newest[:10], days_old
    except (ValueError, TypeError):
        return newest[:10] if newest else None, None


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
    V12.40: Added verbose logging for production debugging.
    Returns all records without city filtering - caller handles grouping.
    """
    endpoint = config["endpoint"]
    date_field = config.get("date_field", "")
    source_name = config.get("name", "Unknown")

    # Calculate date filter
    since_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%dT00:00:00")

    print(f"    [V12.40] Starting bulk fetch: {source_name}", flush=True)
    print(f"    [V12.40] Endpoint: {endpoint}", flush=True)
    print(f"    [V12.40] Date field: {date_field}, since: {since_date}", flush=True)

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

        print(f"    Fetching page {page + 1} (offset {offset})...", flush=True)

        try:
            resp = SESSION.get(endpoint, params=params, timeout=90)  # V12.40: Increased timeout
            print(f"    [V12.40] Response status: {resp.status_code}", flush=True)
            resp.raise_for_status()

            # V12.40: Check for error responses from Socrata
            try:
                records = resp.json()
            except Exception as json_err:
                print(f"    [V12.40] JSON parse error: {json_err}", flush=True)
                print(f"    [V12.40] Response text: {resp.text[:500]}", flush=True)
                break

            if isinstance(records, dict) and records.get("error"):
                print(f"    [V12.40] Socrata error: {records}", flush=True)
                break

            if not records:
                print(f"    No more records at page {page + 1}", flush=True)
                break

            all_records.extend(records)
            print(f"    Got {len(records)} records (total: {len(all_records)})", flush=True)

            if len(records) < BULK_PAGE_SIZE:
                # Last page
                break

            offset += BULK_PAGE_SIZE
            time.sleep(1)  # Rate limit between pages

        except requests.exceptions.Timeout as e:
            print(f"    [V12.40] TIMEOUT on page {page + 1}: {e}", flush=True)
            break
        except requests.exceptions.RequestException as e:
            print(f"    [V12.40] REQUEST ERROR on page {page + 1}: {type(e).__name__}: {e}", flush=True)
            break
        except Exception as e:
            print(f"    [V12.40] UNEXPECTED ERROR on page {page + 1}: {type(e).__name__}: {e}", flush=True)
            import traceback
            traceback.print_exc()
            break

    print(f"    [V12.40] Bulk fetch complete: {len(all_records)} total records", flush=True)
    return all_records


# V17: ArcGIS bulk page size (smaller than Socrata — ArcGIS servers often cap at 1000-2000)
ARCGIS_BULK_PAGE_SIZE = 2000
ARCGIS_BULK_MAX_PAGES = 100  # 200K records max


def fetch_arcgis_bulk(config, days_back=90):
    """
    V17: Fetch ALL permits from a bulk ArcGIS FeatureServer with pagination.
    Returns all records without city filtering — caller handles grouping.
    Mirrors fetch_socrata_bulk() pattern with ArcGIS query API.
    """
    endpoint = config["endpoint"]
    date_field = config.get("date_field", "")
    date_format = config.get("date_format", "date")
    source_name = config.get("name", "Unknown")

    # Calculate date filter
    since_dt = datetime.now() - timedelta(days=days_back)

    if date_format == "epoch":
        since_epoch = int(since_dt.timestamp() * 1000)
        where_clause = f"{date_field} >= {since_epoch}" if date_field else "1=1"
    elif date_format == "none" or not date_field:
        where_clause = "1=1"
    else:
        since_date = since_dt.strftime("%Y-%m-%d")
        where_clause = f"{date_field} >= DATE '{since_date}'"

    print(f"    [V17] Starting ArcGIS bulk fetch: {source_name}", flush=True)
    print(f"    [V17] Endpoint: {endpoint}", flush=True)
    print(f"    [V17] Where: {where_clause}", flush=True)

    all_records = []
    offset = 0

    for page in range(ARCGIS_BULK_MAX_PAGES):
        params = {
            "where": where_clause,
            "outFields": "*",
            "resultRecordCount": ARCGIS_BULK_PAGE_SIZE,
            "resultOffset": offset,
            "f": "json",
        }

        if date_field and date_field != "none":
            params["orderByFields"] = f"{date_field} DESC"

        print(f"    Fetching page {page + 1} (offset {offset})...", flush=True)

        try:
            resp = SESSION.get(endpoint, params=params, timeout=90)
            print(f"    [V17] Response status: {resp.status_code}", flush=True)
            resp.raise_for_status()

            try:
                data = resp.json()
            except Exception as json_err:
                print(f"    [V17] JSON parse error: {json_err}", flush=True)
                print(f"    [V17] Response text: {resp.text[:500]}", flush=True)
                break

            # ArcGIS error responses come as HTTP 200 with error body
            if "error" in data:
                error_msg = data["error"].get("message", "Unknown")
                error_code = data["error"].get("code", "unknown")
                print(f"    [V17] ArcGIS error {error_code}: {error_msg}", flush=True)
                break

            if "features" not in data or not data["features"]:
                print(f"    No more records at page {page + 1}", flush=True)
                break

            records = [f["attributes"] for f in data["features"]]
            all_records.extend(records)
            print(f"    Got {len(records)} records (total: {len(all_records)})", flush=True)

            if len(records) < ARCGIS_BULK_PAGE_SIZE:
                # Last page
                break

            # Check if server supports pagination (exceededTransferLimit)
            if not data.get("exceededTransferLimit", False) and len(records) < ARCGIS_BULK_PAGE_SIZE:
                break

            offset += ARCGIS_BULK_PAGE_SIZE
            time.sleep(1)  # Rate limit between pages

        except requests.exceptions.Timeout as e:
            print(f"    [V17] TIMEOUT on page {page + 1}: {e}", flush=True)
            break
        except requests.exceptions.RequestException as e:
            print(f"    [V17] REQUEST ERROR on page {page + 1}: {type(e).__name__}: {e}", flush=True)
            break
        except Exception as e:
            print(f"    [V17] UNEXPECTED ERROR on page {page + 1}: {type(e).__name__}: {e}", flush=True)
            import traceback
            traceback.print_exc()
            break

    # Post-fetch: if date_format is "none", filter by epoch in Python
    if date_format == "none" and date_field and all_records:
        since_epoch = int(since_dt.timestamp() * 1000)
        before_count = len(all_records)
        all_records = [r for r in all_records if r.get(date_field, 0) and r[date_field] >= since_epoch]
        print(f"    [V17] Date filter (epoch): {before_count} -> {len(all_records)} records", flush=True)

    print(f"    [V17] ArcGIS bulk fetch complete: {len(all_records)} total records", flush=True)
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
    elif platform == "arcgis":
        raw_records = fetch_arcgis_bulk(config, days_back)
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

    # Build address — V12.55c: handle Socrata location objects
    raw_addr = raw_record.get(fmap.get("address", ""), "")
    address = parse_address_value(raw_addr)
    if not address:
        address = get_field("address")
    if not address:
        # Try common address field patterns
        for fallback in ["location", "property_address", "site_address", "street_address"]:
            raw_val = raw_record.get(fallback, "")
            val = parse_address_value(raw_val)
            if not val:
                val = str(raw_val).strip()
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

    # Parse date — V15: Try filing_date, then date, then issued_date field_map keys
    date_str = get_field("filing_date") or get_field("date") or get_field("issued_date")
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
        # V12.58: Handle single-digit M/D/YYYY (e.g., San Jose CKAN: "9/9/2025")
        if not parsed_date and '/' in str(date_str):
            try:
                parts = str(date_str).split()[0].split('/')
                if len(parts) == 3:
                    m, d, y = int(parts[0]), int(parts[1]), int(parts[2])
                    parsed_date = f"{y:04d}-{m:02d}-{d:02d}"
            except (ValueError, IndexError):
                pass
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
        "date": parsed_date,  # V15: populate both date columns
        "issued_date": parsed_date,  # V15: populate all date columns
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
    V12.48: Skip endpoints that have failed 3+ times in this session.
    """
    # V12.48: Skip if this endpoint has failed too many times
    if ENDPOINT_FAILURES.get(city_key, 0) >= MAX_FAILURES_BEFORE_SKIP:
        print(f"  [SKIP] {city_key} - disabled after {MAX_FAILURES_BEFORE_SKIP} failures")
        return [], "skip_failed"

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

    # V12.48: Track failure and log warning
    ENDPOINT_FAILURES[city_key] = ENDPOINT_FAILURES.get(city_key, 0) + 1
    fail_count = ENDPOINT_FAILURES[city_key]
    print(f"  [WARNING] {city_key} returned 0 results (failure #{fail_count})")
    if fail_count >= MAX_FAILURES_BEFORE_SKIP:
        print(f"  [WARNING] {city_key} will be skipped for remainder of this session")
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

    # Build address — V12.55c: handle Socrata location objects
    raw_addr = raw_record.get(fmap.get("address", ""), "")
    parsed_addr = parse_address_value(raw_addr)
    address_parts = []
    if parsed_addr:
        address_parts.append(parsed_addr)
    elif get_field("address"):
        address_parts.append(get_field("address"))
    if get_field("street"):
        address_parts.append(get_field("street"))
    if "street_name" in fmap and fmap["street_name"] != fmap.get("street", ""):
        sn = get_field("street_name") if "street_name" in fmap else ""
        if sn:
            address_parts.append(sn)
    elif not get_field("street") and get_field("street_name"):
        address_parts.append(get_field("street_name"))

    address = " ".join(address_parts) if address_parts else (parsed_addr or get_field("address"))

    # Fallback: try common address field names not in field_map
    if not address:
        for fallback_key in ["location", "project_address", "site_address",
                             "property_address", "address_full", "location_1",
                             "mapped_location"]:
            raw_val = raw_record.get(fallback_key, "")
            val = parse_address_value(raw_val)
            if not val:
                val = str(raw_val).strip()
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

    # Parse date — V15: Try filing_date, then date, then issued_date field_map keys
    date_str = get_field("filing_date") or get_field("date") or get_field("issued_date")
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
        # V12.58: Handle single-digit M/D/YYYY (e.g., San Jose CKAN: "9/9/2025")
        if not parsed_date and '/' in str(date_str):
            try:
                parts = str(date_str).split()[0].split('/')
                if len(parts) == 3:
                    m, d, y = int(parts[0]), int(parts[1]), int(parts[2])
                    parsed_date = f"{y:04d}-{m:02d}-{d:02d}"
            except (ValueError, IndexError):
                pass
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
        "date": parsed_date,  # V15: populate both date columns
        "issued_date": parsed_date,  # V15: populate all date columns
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

def fetch_history_socrata(config, years_back=1):
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


def fetch_history_arcgis(config, years_back=1):
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


def fetch_history_ckan(config, years_back=1):
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


def fetch_history_carto(config, years_back=1):
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


def fetch_permit_history(city_key, years_back=1):
    """Fetch historical permits for a city (last 1 year)."""
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

def collect_permit_history(years_back=1):
    """V12.50: Collect permit history into SQLite."""
    permitdb.init_db()
    active_cities = get_active_cities()
    stats = {}

    print("=" * 60)
    print("PermitGrab V12.50 - Permit History Collection")
    print(f"Pulling {years_back} years of history from {len(active_cities)} cities")
    print("=" * 60)

    for city_key in active_cities:
        config = get_city_config(city_key)
        raw = fetch_permit_history(city_key, years_back)
        city_count = 0

        # Process in batches of 500 to limit memory
        batch = []
        for record in raw:
            try:
                normalized = normalize_permit(record, city_key)
                if not normalized or not normalized["permit_number"]:
                    continue
                addr_key = normalize_address(normalized["address"])
                if not addr_key or addr_key == "address not provided":
                    continue

                batch.append((addr_key, normalized))
                city_count += 1

                if len(batch) >= 500:
                    _flush_history_batch(batch)
                    batch = []

            except Exception:
                continue

        # Flush remaining
        if batch:
            _flush_history_batch(batch)

        stats[city_key] = {"raw": len(raw), "indexed": city_count, "city_name": config["name"]}
        time.sleep(RATE_LIMIT_DELAY)

    # Print summary
    conn = permitdb.get_connection()
    total = conn.execute("SELECT COUNT(*) FROM permit_history").fetchone()[0]
    addresses = conn.execute("SELECT COUNT(DISTINCT address_key) FROM permit_history").fetchone()[0]
    repeats = conn.execute("""
        SELECT COUNT(*) FROM (
            SELECT address_key FROM permit_history GROUP BY address_key HAVING COUNT(*) >= 3
        )
    """).fetchone()[0]

    print("\n" + "=" * 60)
    print("PERMIT HISTORY COLLECTION COMPLETE")
    print("=" * 60)
    print(f"Total addresses: {addresses}")
    print(f"Total historical permits: {total}")
    print(f"Repeat Renovators (3+): {repeats}")
    print(f"\nBy City:")
    for key, s in sorted(stats.items(), key=lambda x: -x[1]["indexed"]):
        print(f"  {s['city_name']}: {s['indexed']} permits indexed ({s['raw']} raw)")

    return stats


def _flush_history_batch(batch):
    """V12.50: Write a batch of history records to SQLite."""
    conn = permitdb.get_connection()
    for addr_key, n in batch:
        conn.execute("""
            INSERT OR IGNORE INTO permit_history (
                address_key, address, city, state,
                permit_number, permit_type, work_type, trade_category,
                filing_date, estimated_cost, description, contractor
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            addr_key, n["address"], n["city"], n["state"],
            n["permit_number"], n["permit_type"], n["work_type"], n["trade_category"],
            n["filing_date"], n["estimated_cost"], n.get("description", "")[:200],
            n.get("contact_name")
        ))
    conn.commit()


# V12.51: Removed load_existing_permits() and merge_permits()
# These JSON-based functions are no longer needed - SQLite handles deduplication via upsert


def collect_refresh(days_back=7):
    """
    V12.50: Delta collection. Fetch recent permits and upsert into SQLite.
    No risk of data loss — INSERT OR REPLACE only touches rows we're updating.
    """
    if not _acquire_lock():
        return [], {}

    try:
        permitdb.init_db()
        reset_failure_tracking()

        print("=" * 60)
        print("PermitGrab V12.50 - REFRESH Collection (Delta Mode)")
        print(f"Fetching permits from last {days_back} days")
        print("=" * 60)

        # Collect new permits from all active sources
        new_permits, stats = _collect_all_inner(days_back, additive_mode=True)

        # Process each permit (reclassify, validate, etc.) before saving
        for permit in new_permits:
            try:
                from server import reclassify_permit, validate_permit_dates, format_permit_address, generate_permit_description
                reclassify_permit(permit)
                validate_permit_dates(permit)
                format_permit_address(permit)
                permit['display_description'] = generate_permit_description(permit)
                if permit.get('estimated_cost', 0) > 50_000_000:
                    permit['estimated_cost'] = 50_000_000
            except ImportError:
                pass  # Running standalone
            except Exception as e:
                print(f"  [WARN] Failed to process permit: {e}")

        # V12.50: Upsert into SQLite (replaces load→merge→save cycle)
        if new_permits:
            new_count, updated_count = permitdb.upsert_permits(new_permits)
            print(f"[V12.50] Upserted: {new_count} new, {updated_count} updated")
        else:
            print("[V12.50] No new permits collected")

        return new_permits, stats
    finally:
        _release_lock()


def collect_full(days_back=365):
    """
    V12.50: Full collection. Rebuilds permits table from scratch.
    Uses a transaction so the old data stays visible until the new data is ready.
    """
    if not _acquire_lock():
        return [], {}

    try:
        permitdb.init_db()
        reset_failure_tracking()

        print("=" * 60)
        print("PermitGrab V12.50 - FULL Collection (Rebuild Mode)")
        print(f"Fetching all permits from last {days_back} days")
        print("=" * 60)

        new_permits, stats = _collect_all_inner(days_back, additive_mode=False)

        # Process permits
        for permit in new_permits:
            try:
                from server import reclassify_permit, validate_permit_dates, format_permit_address, generate_permit_description
                reclassify_permit(permit)
                validate_permit_dates(permit)
                format_permit_address(permit)
                permit['display_description'] = generate_permit_description(permit)
                if permit.get('estimated_cost', 0) > 50_000_000:
                    permit['estimated_cost'] = 50_000_000
            except ImportError:
                pass
            except Exception:
                continue

        # Safety check: don't wipe DB if collection returned almost nothing
        if len(new_permits) < 1000:
            print(f"[V12.50] WARNING: Only {len(new_permits)} permits collected, skipping full rebuild")
            # Fall back to upsert instead of replace
            permitdb.upsert_permits(new_permits)
            return new_permits, stats

        # V12.51: Full rebuild in a single atomic transaction
        # This ensures old data remains visible until new data is fully inserted
        conn = permitdb.get_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM permits")

            # Inline insert (don't use upsert_permits which has its own commit)
            now = datetime.now().isoformat()
            for p in new_permits:
                pn = p.get('permit_number')
                if not pn:
                    continue
                conn.execute("""
                    INSERT OR REPLACE INTO permits (
                        permit_number, city, state, address, zip,
                        permit_type, permit_sub_type, work_type, trade_category,
                        description, display_description, estimated_cost, value_tier,
                        status, filing_date, issued_date, date,
                        contact_name, contact_phone, contact_email, owner_name,
                        contractor_name, square_feet, lifecycle_label,
                        source_city_key, collected_at, updated_at
                    ) VALUES (
                        ?, ?, ?, ?, ?,
                        ?, ?, ?, ?,
                        ?, ?, ?, ?,
                        ?, ?, ?, ?,
                        ?, ?, ?, ?,
                        ?, ?, ?,
                        ?, ?, ?
                    )
                """, (
                    pn, p.get('city'), p.get('state'), p.get('address'), p.get('zip'),
                    p.get('permit_type'), p.get('permit_sub_type'), p.get('work_type'), p.get('trade_category'),
                    p.get('description'), p.get('display_description'), p.get('estimated_cost', 0), p.get('value_tier'),
                    p.get('status'), p.get('filing_date'), p.get('issued_date'), p.get('date'),
                    p.get('contact_name'), p.get('contact_phone'), p.get('contact_email'), p.get('owner_name'),
                    p.get('contractor_name'), p.get('square_feet'), p.get('lifecycle_label'),
                    None, now, now
                ))

            conn.commit()
            print(f"[V12.51] Full rebuild complete: {len(new_permits)} permits (atomic transaction)")
        except Exception as e:
            conn.rollback()
            print(f"[V12.51] Full rebuild FAILED, rolled back: {e}")
            raise

        return new_permits, stats
    finally:
        _release_lock()


def collect_single_source(source_key, source_type='bulk'):
    """
    V12.50: Collect from a single source and upsert to SQLite.
    Use when adding a new bulk source mid-day.

    source_type: 'bulk' for BULK_SOURCES, 'city' for CITY_REGISTRY
    """
    if not _acquire_lock():
        return [], {}

    try:
        permitdb.init_db()
        print("=" * 60)
        print(f"PermitGrab V12.50 - SINGLE SOURCE Collection: {source_key}")
        print("=" * 60)

        new_permits = []
        stats = {}

        if source_type == 'bulk':
            config = get_bulk_source_config(source_key)
            if not config:
                print(f"[ERROR] Bulk source not found: {source_key}")
                return [], {"error": "source_not_found"}

            city_permits, source_stats = collect_bulk_source(source_key, config, days_back=90)
            for city_slug, permits in city_permits.items():
                new_permits.extend(permits)
            stats[source_key] = source_stats
            print(f"  ✓ {config['name']}: {len(new_permits)} permits")

        else:  # city
            config = get_city_config(source_key)
            if not config:
                print(f"[ERROR] City not found: {source_key}")
                return [], {"error": "source_not_found"}

            raw, fetch_status = fetch_permits(source_key, days_back=365)
            for record in raw:
                try:
                    normalized = normalize_permit(record, source_key)
                    if normalized and normalized.get("permit_number"):
                        new_permits.append(normalized)
                except Exception:
                    continue
            stats[source_key] = {"raw": len(raw), "normalized": len(new_permits), "status": fetch_status}
            print(f"  ✓ {config['name']}: {len(new_permits)} permits")

        # V12.50: Upsert to SQLite
        if new_permits:
            new_count, updated_count = permitdb.upsert_permits(new_permits, source_city_key=source_key)
            print(f"[V12.50] Upserted: {new_count} new, {updated_count} updated")

        return new_permits, stats
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

            start_time = time.time()  # V15: Track timing for scraper_runs
            try:
                city_permits, source_stats = collect_bulk_source(source_key, config, days_back=90)

                # Add all permits from bulk source
                for city_slug, permits in city_permits.items():
                    all_permits.extend(permits)
                    cities_from_bulk.add(city_slug)

                bulk_stats[source_key] = source_stats
                total_permits = source_stats.get('total_normalized', 0)
                print(f"  ✓ {config['name']}: {total_permits} permits from {source_stats.get('cities_with_permits', 0)} cities")

                # V15: Log bulk source success to scraper_runs
                duration_ms = int((time.time() - start_time) * 1000)
                _log_v15_collection(
                    city_key=source_key,
                    city_name=config.get('name', source_key),
                    state=config.get('state', ''),
                    permits_found=total_permits,
                    permits_inserted=total_permits,
                    status='success' if total_permits > 0 else 'no_new',
                    duration_ms=duration_ms
                )

            except Exception as e:
                print(f"  ✗ {config['name']}: ERROR - {str(e)[:100]}")
                bulk_stats[source_key] = {"status": f"error: {str(e)[:100]}"}
                # V15: Log bulk source error to scraper_runs
                duration_ms = int((time.time() - start_time) * 1000)
                _log_v15_collection(
                    city_key=source_key,
                    city_name=config.get('name', source_key),
                    state=config.get('state', ''),
                    permits_found=0,
                    permits_inserted=0,
                    status='error',
                    error_message=str(e)[:200],
                    duration_ms=duration_ms
                )

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
    for city_info in active_cities:
        # V13.3: Handle both dict format (SQLite) and string format (legacy)
        if isinstance(city_info, dict):
            city_key = city_info.get('source_key', '')
        else:
            city_key = city_info
        if failure_tracker.get(city_key, 0) >= 10:
            skipped_count += 1
        else:
            filtered_cities.append(city_info)  # Keep original format for downstream

    if skipped_count > 0:
        print(f"  Skipping {skipped_count} cities with 10+ consecutive failures")
        print(f"  (Reset by deleting {failure_tracker_path})")

    active_cities = filtered_cities

    print("\n" + "=" * 60)
    print("PermitGrab - INDIVIDUAL CITY Collection")
    print(f"Pulling permits from {len(active_cities)} direct city APIs (last {days_back} days)")
    print("=" * 60)

    # V13.2: Track collection stats for diagnostics
    sources_attempted = 0
    sources_succeeded = 0
    sources_failed = 0

    for i, city_info in enumerate(active_cities):
        # V13.2: Handle both dict format (SQLite) and string format (legacy)
        if isinstance(city_info, dict):
            city_key = city_info.get('source_key')
            config = city_info  # Already have full config from SQLite
        else:
            city_key = city_info
            config = get_city_config(city_key)

        if not city_key:
            print(f"  ⚠ Skipping source {i}: missing source_key")
            continue

        if not config:
            print(f"  ⚠ Skipping {city_key}: no config found")
            sources_failed += 1
            continue

        sources_attempted += 1
        start_time = time.time()  # V15: Track timing for scraper_runs

        try:
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

            # V13.2: Get display name (handles both dict and legacy config)
            display_name = config.get("name", city_key) if config else city_key

            # V12.2: Use the ACTUAL fetch status, not always "success"
            if fetch_status == "success":
                stats[city_key] = {
                    "raw": len(raw),
                    "normalized": len(city_permits),
                    "city_name": display_name,
                    "status": "success" if len(city_permits) > 0 else "success_empty",
                }
                print(f"  ✓ {display_name}: {len(city_permits)} permits")
                sources_succeeded += 1
                # V12.54: Track successful collection in SQLite
                if len(city_permits) > 0:
                    try:
                        record_collection(city_key, len(city_permits))
                        reset_failure(city_key)
                    except Exception:
                        pass  # Don't let tracking errors break collection
                # V15: Log to scraper_runs table
                duration_ms = int((time.time() - start_time) * 1000)
                _log_v15_collection(
                    city_key=city_key,
                    city_name=display_name,
                    state=config.get('state', ''),
                    permits_found=len(city_permits),
                    permits_inserted=len(city_permits),  # Actual insert count not tracked here
                    status='success' if len(city_permits) > 0 else 'no_new',
                    duration_ms=duration_ms
                )
            elif fetch_status == "skip":
                stats[city_key] = {
                    "raw": 0,
                    "normalized": 0,
                    "city_name": display_name,
                    "status": "skip",
                }
            else:
                stats[city_key] = {
                    "raw": 0,
                    "normalized": 0,
                    "city_name": display_name,
                    "status": fetch_status,
                }
                print(f"  ✗ {display_name}: FAILED ({fetch_status})")
                sources_failed += 1
                # V12.54: Track failure in SQLite
                try:
                    increment_failure(city_key, fetch_status)
                except Exception:
                    pass
                # V15: Log failure to scraper_runs table
                duration_ms = int((time.time() - start_time) * 1000)
                _log_v15_collection(
                    city_key=city_key,
                    city_name=display_name,
                    state=config.get('state', ''),
                    permits_found=0,
                    permits_inserted=0,
                    status='error',
                    error_message=fetch_status,
                    duration_ms=duration_ms
                )

        except Exception as e:
            display_name = config.get("name", city_key) if config else city_key
            stats[city_key] = {
                "raw": 0,
                "normalized": 0,
                "city_name": display_name,
                "status": f"error: {str(e)[:100]}",
            }
            print(f"  ✗ {city_key}: {str(e)[:100]}")
            sources_failed += 1
            # V15: Log exception to scraper_runs table
            duration_ms = int((time.time() - start_time) * 1000)
            _log_v15_collection(
                city_key=city_key,
                city_name=display_name,
                state=config.get('state', '') if config else '',
                permits_found=0,
                permits_inserted=0,
                status='error',
                error_message=str(e)[:200],
                duration_ms=duration_ms
            )

        # Rate limiting
        time.sleep(RATE_LIMIT_DELAY)

        # V12.50: Removed batch JSON writes — SQLite handles persistence
        # V12.29: Still pause between batches to let server breathe
        if (i + 1) % BATCH_SIZE == 0 and all_permits:
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
            print(f"  [Pausing {BATCH_PAUSE_SECONDS}s before next batch...]")
            time.sleep(BATCH_PAUSE_SECONDS)

    # V13.2: Summary logging for diagnostics
    print("\n" + "-" * 60)
    print(f"[V13.2] CITY COLLECTION SUMMARY:")
    print(f"  Sources attempted: {sources_attempted}")
    print(f"  Sources succeeded: {sources_succeeded} ({sources_succeeded*100//max(1,sources_attempted)}%)")
    print(f"  Sources failed:    {sources_failed}")
    print(f"  Permits collected: {len(all_permits)}")
    print("-" * 60)

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

    # V12.50: Caller (collect_refresh/collect_full) handles SQLite writes
    # This function now purely returns collected permits without file I/O
    print(f"[V12.50] Returning {len(all_permits)} permits for SQLite upsert")

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

    # V13.3: Clean up stale failure tracker entries
    # Remove entries with 0 failures (useless) and entries for unknown sources
    valid_source_keys = set(stats.keys())
    stale_keys = [k for k, v in failure_tracker.items() if v == 0 or k not in valid_source_keys]
    if stale_keys:
        for k in stale_keys:
            del failure_tracker[k]
        print(f"[V13.3] Cleaned up {len(stale_keys)} stale failure tracker entries")

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

    # V12.51: SQLite handles persistence - no hot-reload needed here
    # The caller (collect_refresh/collect_full) handles SQLite upsert
    print(f"\n[V12.50] Returning {len(all_permits)} permits for SQLite persistence")

    return all_permits, collection_stats


if __name__ == "__main__":
    permits, stats = collect_all(days_back=365)
