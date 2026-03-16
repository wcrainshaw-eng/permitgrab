"""
PermitGrab - City Health Monitoring
Tests API connectivity for all configured cities and tracks health status.
"""

import requests
import json
import os
import time
from datetime import datetime
from city_configs import CITY_REGISTRY, get_active_cities, get_city_config

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
HEALTH_FILE = os.path.join(DATA_DIR, "city_health.json")


def test_socrata(config):
    """Test Socrata API with limit=1 query."""
    endpoint = config["endpoint"]
    params = {"$limit": 1}

    start = time.time()
    resp = requests.get(endpoint, params=params, timeout=15)
    elapsed = round(time.time() - start, 2)

    resp.raise_for_status()
    data = resp.json()

    return {
        "success": True,
        "response_time": elapsed,
        "sample_count": len(data),
    }


def test_arcgis(config):
    """Test ArcGIS API with limit=1 query."""
    endpoint = config["endpoint"]
    params = {
        "where": "1=1",
        "outFields": "*",
        "resultRecordCount": 1,
        "f": "json",
    }

    start = time.time()
    resp = requests.get(endpoint, params=params, timeout=15)
    elapsed = round(time.time() - start, 2)

    resp.raise_for_status()
    data = resp.json()

    feature_count = len(data.get("features", []))

    return {
        "success": True,
        "response_time": elapsed,
        "sample_count": feature_count,
    }


def test_ckan(config):
    """Test CKAN API with limit=1 query."""
    endpoint = config["endpoint"]
    dataset_id = config["dataset_id"]
    params = {
        "resource_id": dataset_id,
        "limit": 1,
    }

    start = time.time()
    resp = requests.get(endpoint, params=params, timeout=15)
    elapsed = round(time.time() - start, 2)

    resp.raise_for_status()
    data = resp.json()

    record_count = len(data.get("result", {}).get("records", []))

    return {
        "success": True,
        "response_time": elapsed,
        "sample_count": record_count,
    }


def test_carto(config):
    """Test CARTO API with limit=1 query."""
    endpoint = config["endpoint"]
    table_name = config.get("table_name", config["dataset_id"])

    params = {
        "q": f"SELECT * FROM {table_name} LIMIT 1",
        "format": "json",
    }

    start = time.time()
    resp = requests.get(endpoint, params=params, timeout=15)
    elapsed = round(time.time() - start, 2)

    resp.raise_for_status()
    data = resp.json()

    row_count = len(data.get("rows", []))

    return {
        "success": True,
        "response_time": elapsed,
        "sample_count": row_count,
    }


def test_city(city_key):
    """Test a single city's API connectivity."""
    config = get_city_config(city_key)
    if not config:
        return {"success": False, "error": "Unknown city"}

    if not config.get("active", False):
        return {"success": False, "error": "City inactive", "status": "inactive"}

    platform = config.get("platform", "socrata")

    try:
        if platform == "socrata":
            result = test_socrata(config)
        elif platform == "arcgis":
            result = test_arcgis(config)
        elif platform == "ckan":
            result = test_ckan(config)
        elif platform == "carto":
            result = test_carto(config)
        else:
            return {"success": False, "error": f"Unknown platform: {platform}"}

        result["status"] = "healthy"
        result["city"] = config["name"]
        result["platform"] = platform
        result["tested_at"] = datetime.now().isoformat()
        return result

    except requests.exceptions.Timeout:
        return {
            "success": False,
            "error": "Timeout",
            "status": "error",
            "city": config["name"],
            "platform": platform,
            "tested_at": datetime.now().isoformat(),
        }
    except requests.exceptions.HTTPError as e:
        return {
            "success": False,
            "error": f"HTTP {e.response.status_code}",
            "status": "error",
            "city": config["name"],
            "platform": platform,
            "tested_at": datetime.now().isoformat(),
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "status": "error",
            "city": config["name"],
            "platform": platform,
            "tested_at": datetime.now().isoformat(),
        }


def check_all_cities():
    """Run health checks on all active cities."""
    results = {}
    active_cities = get_active_cities()

    print("=" * 60)
    print("PermitGrab - City Health Check")
    print(f"Testing {len(active_cities)} active cities")
    print("=" * 60)

    healthy = 0
    errors = 0

    for city_key in active_cities:
        config = get_city_config(city_key)
        print(f"  Testing {config['name']}...", end=" ")

        result = test_city(city_key)
        results[city_key] = result

        if result.get("success"):
            print(f"OK ({result['response_time']}s)")
            healthy += 1
        else:
            print(f"FAILED: {result.get('error', 'Unknown error')}")
            errors += 1

        # Rate limiting between tests
        time.sleep(0.5)

    # Load previous results for comparison
    previous_results = {}
    if os.path.exists(HEALTH_FILE):
        try:
            with open(HEALTH_FILE) as f:
                previous_data = json.load(f)
                previous_results = previous_data.get("cities", {})
        except Exception:
            pass

    # Check for record count drops (warning indicator)
    for city_key, result in results.items():
        if result.get("success") and city_key in previous_results:
            prev = previous_results[city_key]
            if prev.get("last_record_count", 0) > 0:
                current_count = result.get("sample_count", 0)
                # This is just a sample count (1), so we just verify we got data
                if current_count == 0 and prev.get("sample_count", 0) > 0:
                    result["status"] = "warning"
                    result["warning"] = "No records returned (previously had data)"

    # Save results
    health_data = {
        "checked_at": datetime.now().isoformat(),
        "summary": {
            "total_cities": len(active_cities),
            "healthy": healthy,
            "errors": errors,
        },
        "cities": results,
    }

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(HEALTH_FILE, "w") as f:
        json.dump(health_data, f, indent=2)

    # Print summary
    print("\n" + "=" * 60)
    print("HEALTH CHECK COMPLETE")
    print("=" * 60)
    print(f"Healthy: {healthy}")
    print(f"Errors: {errors}")
    print(f"Results saved to: {HEALTH_FILE}")

    return health_data


def get_health_status():
    """Get current health status from file."""
    if not os.path.exists(HEALTH_FILE):
        return None

    with open(HEALTH_FILE) as f:
        return json.load(f)


def get_city_status_badge(city_key):
    """Get status badge color for a city (green/yellow/red)."""
    health = get_health_status()
    if not health or city_key not in health.get("cities", {}):
        return "gray"  # Unknown

    city_health = health["cities"][city_key]
    status = city_health.get("status", "unknown")

    if status == "healthy":
        return "green"
    elif status == "warning":
        return "yellow"
    elif status == "error":
        return "red"
    else:
        return "gray"


if __name__ == "__main__":
    check_all_cities()
