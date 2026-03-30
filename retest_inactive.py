#!/usr/bin/env python3
"""
retest_inactive.py — V31 City Scaling Phase 1
==============================================
Iterates over all inactive CITY_REGISTRY entries, hits each endpoint with
a test query (last 7 days), and reports which ones return valid data.

Sources that respond with permits can be reactivated by flipping active: True
in city_configs.py and adding/updating the prod_cities entry.

Usage:
    python retest_inactive.py                  # Test all inactive sources
    python retest_inactive.py --activate       # Also flip active + update prod_cities
    python retest_inactive.py --platform socrata  # Only test one platform type
    python retest_inactive.py --limit 20       # Test first N inactive sources

Can also be called from other modules:
    from retest_inactive import retest_all_inactive
    results = retest_all_inactive(activate=False)
"""

import sys
import os
import time
import json
import argparse
from datetime import datetime, timedelta

# Ensure the project root is on the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import requests
from city_configs import CITY_REGISTRY, BULK_SOURCES

# Try importing DB module (only needed if --activate is used)
try:
    import db as permitdb
    DB_AVAILABLE = True
except Exception:
    DB_AVAILABLE = False

# ─── Config ─────────────────────────────────────────────────────────────
TEST_DAYS_BACK = 7
TIMEOUT_SECONDS = 15
RATE_LIMIT_DELAY = 0.5  # seconds between requests
SESSION = requests.Session()
SESSION.headers.update({
    'User-Agent': 'PermitGrab/1.0 (retest-inactive)'
})


# ─── Platform fetchers (standalone, no active-flag check) ───────────────

def test_socrata(config, days_back=TEST_DAYS_BACK):
    """Test a Socrata endpoint. Returns (record_count, error_or_None)."""
    endpoint = config.get("endpoint")
    date_field = config.get("date_field")
    if not endpoint or not date_field:
        return 0, "missing_endpoint_or_date_field"

    since_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%dT00:00:00")
    where_clause = f"{date_field} > '{since_date}'"

    city_filter = config.get("city_filter")
    if city_filter:
        ff = city_filter["field"]
        fv = city_filter["value"]
        where_clause += f" AND upper({ff}) = upper('{fv}')"

    params = {
        "$limit": 5,  # Only need a few to prove it works
        "$order": f"{date_field} DESC",
        "$where": where_clause,
    }

    resp = SESSION.get(endpoint, params=params, timeout=TIMEOUT_SECONDS)
    resp.raise_for_status()
    data = resp.json()
    return len(data), None


def test_arcgis(config, days_back=TEST_DAYS_BACK):
    """Test an ArcGIS endpoint. Returns (record_count, error_or_None)."""
    endpoint = config.get("endpoint")
    date_field = config.get("date_field")
    if not endpoint:
        return 0, "missing_endpoint"

    since_dt = datetime.now() - timedelta(days=days_back)
    date_format = config.get("date_format", "date")

    if date_format == "epoch":
        since_epoch = int(since_dt.timestamp() * 1000)
        where_clause = f"{date_field} >= {since_epoch}"
    elif date_format == "none" or not date_field:
        where_clause = "1=1"
    else:
        since_date = since_dt.strftime("%Y-%m-%d")
        where_clause = f"{date_field} >= DATE '{since_date}'"

    city_filter = config.get("city_filter")
    if city_filter:
        ff = city_filter["field"]
        fv = city_filter["value"]
        where_clause += f" AND upper({ff}) = upper('{fv}')"

    params = {
        "where": where_clause,
        "outFields": "*",
        "resultRecordCount": 5,
        "orderByFields": f"{date_field} DESC" if date_field else "",
        "f": "json",
    }

    resp = SESSION.get(endpoint, params=params, timeout=TIMEOUT_SECONDS)
    resp.raise_for_status()
    data = resp.json()

    if "error" in data:
        return 0, f"arcgis_error_{data['error'].get('code', 'unknown')}"

    if "features" in data:
        return len(data["features"]), None

    return 0, "no_features_key"


def test_ckan(config, days_back=TEST_DAYS_BACK):
    """Test a CKAN endpoint. Returns (record_count, error_or_None)."""
    endpoint = config.get("endpoint")
    if not endpoint:
        return 0, "missing_endpoint"

    date_field = config.get("date_field", "")
    since_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")

    if date_field:
        sql = f'SELECT * FROM "{config.get("dataset_id", "")}" WHERE "{date_field}" >= \'{since_date}\' LIMIT 5'
    else:
        sql = f'SELECT * FROM "{config.get("dataset_id", "")}" LIMIT 5'

    params = {"sql": sql}
    resp = SESSION.get(endpoint, params=params, timeout=TIMEOUT_SECONDS)
    resp.raise_for_status()
    data = resp.json()
    records = data.get("result", {}).get("records", [])
    return len(records), None


def test_carto(config, days_back=TEST_DAYS_BACK):
    """Test a Carto endpoint. Returns (record_count, error_or_None)."""
    endpoint = config.get("endpoint")
    if not endpoint:
        return 0, "missing_endpoint"

    date_field = config.get("date_field", "")
    table = config.get("table", "")
    since_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")

    if date_field and table:
        sql = f"SELECT * FROM {table} WHERE {date_field} >= '{since_date}' LIMIT 5"
    elif table:
        sql = f"SELECT * FROM {table} LIMIT 5"
    else:
        return 0, "missing_table"

    params = {"q": sql, "format": "json"}
    resp = SESSION.get(endpoint, params=params, timeout=TIMEOUT_SECONDS)
    resp.raise_for_status()
    data = resp.json()
    rows = data.get("rows", [])
    return len(rows), None


PLATFORM_TESTERS = {
    "socrata": test_socrata,
    "arcgis": test_arcgis,
    "ckan": test_ckan,
    "carto": test_carto,
}


# ─── Main retest logic ─────────────────────────────────────────────────

def get_inactive_cities(platform_filter=None):
    """Return dict of {key: config} for all inactive CITY_REGISTRY entries."""
    inactive = {}
    for key, config in CITY_REGISTRY.items():
        if config.get("active", False):
            continue
        if platform_filter and config.get("platform") != platform_filter:
            continue
        # Skip entries with empty endpoint (placeholder configs)
        if not config.get("endpoint"):
            continue
        # Skip entries with empty field_map (skeleton configs)
        if not config.get("field_map"):
            continue
        inactive[key] = config
    return inactive


def get_inactive_bulk_sources():
    """Return dict of {key: config} for inactive BULK_SOURCES."""
    inactive = {}
    for key, config in BULK_SOURCES.items():
        if config.get("active", False):
            continue
        inactive[key] = config
    return inactive


def test_single_source(key, config):
    """
    Test a single city source. Returns dict with results.
    """
    platform = config.get("platform", "socrata")
    tester = PLATFORM_TESTERS.get(platform)

    if not tester:
        return {
            "key": key,
            "name": config.get("name", key),
            "state": config.get("state", ""),
            "platform": platform,
            "status": "skip",
            "error": f"no_tester_for_{platform}",
            "records": 0,
        }

    try:
        count, error = tester(config)
        if error:
            status = "error"
        elif count > 0:
            status = "alive"
        else:
            status = "empty"

        return {
            "key": key,
            "name": config.get("name", key),
            "state": config.get("state", ""),
            "platform": platform,
            "status": status,
            "error": error,
            "records": count,
        }
    except requests.exceptions.HTTPError as e:
        code = e.response.status_code if e.response else "unknown"
        return {
            "key": key,
            "name": config.get("name", key),
            "state": config.get("state", ""),
            "platform": platform,
            "status": "http_error",
            "error": f"HTTP {code}",
            "records": 0,
        }
    except requests.exceptions.ConnectionError:
        return {
            "key": key,
            "name": config.get("name", key),
            "state": config.get("state", ""),
            "platform": platform,
            "status": "connection_error",
            "error": "connection_failed",
            "records": 0,
        }
    except requests.exceptions.Timeout:
        return {
            "key": key,
            "name": config.get("name", key),
            "state": config.get("state", ""),
            "platform": platform,
            "status": "timeout",
            "error": "timeout",
            "records": 0,
        }
    except Exception as e:
        return {
            "key": key,
            "name": config.get("name", key),
            "state": config.get("state", ""),
            "platform": platform,
            "status": "error",
            "error": str(e)[:120],
            "records": 0,
        }


def activate_source(key, config, result):
    """
    Flip a source to active in memory and update prod_cities if DB is available.
    Note: To persist the CITY_REGISTRY change, you still need to update city_configs.py.
    This function updates the prod_cities DB table so the city shows up immediately.
    """
    if not DB_AVAILABLE:
        print(f"    [WARN] DB not available — can't update prod_cities for {key}")
        return False

    try:
        permitdb.init_db()
        city_name = config.get("name", key)
        state = config.get("state", "")
        slug = config.get("slug", key.replace("_", "-"))

        # Upsert into prod_cities as active
        conn = permitdb.get_connection()
        conn.execute("""
            INSERT INTO prod_cities (city, state, city_slug, source_type, source_id, status, total_permits)
            VALUES (?, ?, ?, 'city_registry', ?, 'active', 0)
            ON CONFLICT(city_slug) DO UPDATE SET
                status = 'active',
                source_id = excluded.source_id
        """, (city_name, state, slug, key))
        conn.commit()

        # Log activation
        try:
            permitdb.log_city_activation(
                city_slug=slug,
                city_name=city_name,
                state=state,
                source='retest_inactive',
                initial_permits=result.get('records', 0)
            )
        except Exception:
            pass

        return True
    except Exception as e:
        print(f"    [ERROR] Failed to activate {key}: {e}")
        return False


def retest_all_inactive(platform_filter=None, limit=None, activate=False):
    """
    Main entry point. Tests all inactive CITY_REGISTRY sources.

    Args:
        platform_filter: Only test sources on this platform (e.g. 'socrata')
        limit: Max number of sources to test
        activate: If True, flip alive sources to active in prod_cities

    Returns:
        dict with 'alive', 'empty', 'error', 'skip' lists and summary stats
    """
    inactive = get_inactive_cities(platform_filter)
    total = len(inactive)

    if limit:
        keys = list(inactive.keys())[:limit]
        inactive = {k: inactive[k] for k in keys}

    print(f"\n{'='*60}")
    print(f"  RETEST INACTIVE SOURCES")
    print(f"  Total inactive entries: {total}")
    print(f"  Testing: {len(inactive)} sources")
    if platform_filter:
        print(f"  Platform filter: {platform_filter}")
    print(f"  Date range: last {TEST_DAYS_BACK} days")
    print(f"  Auto-activate: {'YES' if activate else 'no'}")
    print(f"{'='*60}\n")

    results = {
        "alive": [],
        "empty": [],
        "error": [],
        "skip": [],
        "started_at": datetime.now().isoformat(),
    }

    for i, (key, config) in enumerate(inactive.items(), 1):
        name = config.get("name", key)
        state = config.get("state", "")
        platform = config.get("platform", "?")

        print(f"[{i}/{len(inactive)}] {name}, {state} ({platform})...", end=" ", flush=True)

        result = test_single_source(key, config)

        if result["status"] == "alive":
            print(f"✓ ALIVE ({result['records']} records)")
            results["alive"].append(result)

            if activate:
                ok = activate_source(key, config, result)
                if ok:
                    print(f"    → Activated in prod_cities")
                else:
                    print(f"    → Activation failed (update city_configs.py manually)")

        elif result["status"] == "empty":
            print(f"· empty (endpoint works, 0 recent records)")
            results["empty"].append(result)

        elif result["status"] == "skip":
            print(f"- skip ({result['error']})")
            results["skip"].append(result)

        else:
            print(f"✗ {result['status']} ({result['error']})")
            results["error"].append(result)

        time.sleep(RATE_LIMIT_DELAY)

    # Summary
    results["finished_at"] = datetime.now().isoformat()
    alive_count = len(results["alive"])
    empty_count = len(results["empty"])
    error_count = len(results["error"])
    skip_count = len(results["skip"])

    print(f"\n{'='*60}")
    print(f"  RESULTS SUMMARY")
    print(f"{'='*60}")
    print(f"  ✓ Alive (reactivatable):  {alive_count}")
    print(f"  · Empty (works, no data): {empty_count}")
    print(f"  ✗ Error (dead/broken):    {error_count}")
    print(f"  - Skipped:                {skip_count}")
    print(f"{'='*60}")

    if alive_count > 0:
        print(f"\n  REACTIVATABLE SOURCES:")
        for r in results["alive"]:
            print(f"    {r['name']}, {r['state']} — {r['records']} records ({r['platform']})")
            print(f"      key: {r['key']}")

    if empty_count > 0:
        print(f"\n  EMPTY BUT RESPONDING (may have seasonal data):")
        for r in results["empty"]:
            print(f"    {r['name']}, {r['state']} ({r['platform']})")

    return results


def retest_bulk_sources():
    """Test the 4 inactive BULK_SOURCES separately."""
    inactive = get_inactive_bulk_sources()

    if not inactive:
        print("No inactive bulk sources found.")
        return {}

    print(f"\n{'='*60}")
    print(f"  RETEST INACTIVE BULK SOURCES ({len(inactive)} total)")
    print(f"{'='*60}\n")

    results = {"alive": [], "error": []}

    for key, config in inactive.items():
        platform = config.get("platform", "socrata")
        mode = config.get("mode", "bulk")
        name = config.get("name", key)

        print(f"  {name} ({platform}/{mode})...", end=" ", flush=True)

        # Bulk sources use the same endpoint format, just test connectivity
        tester = PLATFORM_TESTERS.get(platform)
        if not tester:
            print(f"skip (no tester for {platform})")
            results["error"].append({"key": key, "name": name, "error": f"no_tester_{platform}"})
            continue

        try:
            count, error = tester(config)
            if error:
                print(f"✗ {error}")
                results["error"].append({"key": key, "name": name, "error": error})
            elif count > 0:
                print(f"✓ ALIVE ({count} records)")
                results["alive"].append({"key": key, "name": name, "records": count})
            else:
                print(f"· empty")
                results["error"].append({"key": key, "name": name, "error": "empty"})
        except Exception as e:
            print(f"✗ {str(e)[:80]}")
            results["error"].append({"key": key, "name": name, "error": str(e)[:120]})

        time.sleep(RATE_LIMIT_DELAY)

    return results


# ─── CLI ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Retest inactive PermitGrab sources")
    parser.add_argument("--activate", action="store_true",
                        help="Activate sources that respond with permits")
    parser.add_argument("--platform", type=str, default=None,
                        help="Only test sources on this platform (socrata, arcgis, ckan, carto)")
    parser.add_argument("--limit", type=int, default=None,
                        help="Max number of sources to test")
    parser.add_argument("--bulk", action="store_true",
                        help="Also test inactive BULK_SOURCES")
    parser.add_argument("--output", type=str, default=None,
                        help="Save results to JSON file")
    args = parser.parse_args()

    # Test city registry inactive sources
    results = retest_all_inactive(
        platform_filter=args.platform,
        limit=args.limit,
        activate=args.activate,
    )

    # Test bulk sources if requested
    if args.bulk:
        bulk_results = retest_bulk_sources()
        results["bulk"] = bulk_results

    # Save results
    if args.output:
        with open(args.output, "w") as f:
            json.dump(results, f, indent=2, default=str)
        print(f"\nResults saved to {args.output}")

    # Print instructions if not auto-activating
    alive = results.get("alive", [])
    if alive and not args.activate:
        print(f"\n{'='*60}")
        print(f"  TO REACTIVATE THESE {len(alive)} SOURCES:")
        print(f"  Run: python retest_inactive.py --activate")
        print(f"  Then update city_configs.py: set active: True for each key")
        print(f"  Then git push to deploy")
        print(f"{'='*60}")

    return results


if __name__ == "__main__":
    main()
