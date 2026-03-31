"""
V35: Endpoint Discovery for Stale Cities
Searches Socrata Discovery API, ArcGIS Hub, and CKAN catalogs to find
current building permit datasets for cities whose endpoints have gone stale.

Runs ON Render (full network access). Called by /api/admin/discover-and-activate.
"""

import requests
import json
import re
import time
from datetime import datetime, timedelta

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "PermitGrab/1.0 (permit lead aggregator; contact@permitgrab.com)",
    "Accept": "application/json",
})

SOCRATA_APP_TOKEN = ""  # Optional, set via env
API_TIMEOUT = 20

# ============================================================================
# PLATFORM SEARCH APIs
# ============================================================================

def search_socrata(city_name, state):
    """Search Socrata Discovery API for building permit datasets in a city.
    Returns list of {endpoint, dataset_id, name, domain, updated_at, rows}."""
    results = []
    queries = [
        f"{city_name} building permits",
        f"{city_name} permits",
        f"{city_name} building",
    ]
    seen_ids = set()

    for query in queries:
        try:
            # Socrata Discovery API (catalog search)
            params = {
                "q": query,
                "categories": "permits",
                "limit": 10,
                "only": "datasets",
            }
            resp = SESSION.get(
                "http://api.us.socrata.com/api/catalog/v1",
                params=params,
                timeout=API_TIMEOUT
            )
            if resp.status_code != 200:
                continue
            data = resp.json()

            for item in data.get("results", []):
                resource = item.get("resource", {})
                dataset_id = resource.get("id", "")
                if dataset_id in seen_ids:
                    continue
                seen_ids.add(dataset_id)

                name = resource.get("name", "").lower()
                desc = resource.get("description", "").lower()
                domain = item.get("metadata", {}).get("domain", "")

                # Filter: must relate to building permits
                permit_keywords = ["permit", "building", "construction", "zoning"]
                if not any(kw in name or kw in desc for kw in permit_keywords):
                    continue

                # Filter: must be from the right city/state domain or name
                city_lower = city_name.lower()
                state_lower = state.lower()
                location_match = (
                    city_lower in domain.lower() or
                    city_lower in name or
                    state_lower in domain.lower()
                )
                if not location_match:
                    continue

                results.append({
                    "platform": "socrata",
                    "endpoint": f"https://{domain}/resource/{dataset_id}.json",
                    "dataset_id": dataset_id,
                    "domain": domain,
                    "name": resource.get("name", ""),
                    "description": resource.get("description", ""),
                    "updated_at": resource.get("updatedAt", ""),
                    "rows": resource.get("page_views", {}).get("page_views_total", 0),
                    "columns": resource.get("columns_name", []),
                    "columns_field_name": resource.get("columns_field_name", []),
                })
        except Exception as e:
            print(f"  [Discovery] Socrata search error for '{query}': {e}")
            continue

    # Also try broader search without category filter
    try:
        params = {
            "q": f"{city_name} {state} building permits",
            "limit": 10,
            "only": "datasets",
        }
        resp = SESSION.get(
            "http://api.us.socrata.com/api/catalog/v1",
            params=params,
            timeout=API_TIMEOUT
        )
        if resp.status_code == 200:
            data = resp.json()
            for item in data.get("results", []):
                resource = item.get("resource", {})
                dataset_id = resource.get("id", "")
                if dataset_id in seen_ids:
                    continue
                seen_ids.add(dataset_id)
                name = resource.get("name", "").lower()
                desc = resource.get("description", "").lower()
                domain = item.get("metadata", {}).get("domain", "")
                permit_keywords = ["permit", "building", "construction"]
                if not any(kw in name or kw in desc for kw in permit_keywords):
                    continue
                results.append({
                    "platform": "socrata",
                    "endpoint": f"https://{domain}/resource/{dataset_id}.json",
                    "dataset_id": dataset_id,
                    "domain": domain,
                    "name": resource.get("name", ""),
                    "description": resource.get("description", ""),
                    "updated_at": resource.get("updatedAt", ""),
                    "rows": resource.get("page_views", {}).get("page_views_total", 0),
                    "columns": resource.get("columns_name", []),
                    "columns_field_name": resource.get("columns_field_name", []),
                })
    except Exception:
        pass

    return results


def search_arcgis_hub(city_name, state):
    """Search ArcGIS Hub / ArcGIS Online for building permit FeatureServers.
    Returns list of {endpoint, name, updated_at, type}."""
    results = []
    queries = [
        f"{city_name} building permits",
        f"{city_name} {state} permits",
        f"{city_name} building permit issued",
    ]
    seen_urls = set()

    for query in queries:
        try:
            params = {
                "q": query,
                "type": "Feature Service",
                "f": "json",
                "num": 10,
            }
            resp = SESSION.get(
                "https://www.arcgis.com/sharing/rest/search",
                params=params,
                timeout=API_TIMEOUT
            )
            if resp.status_code != 200:
                continue
            data = resp.json()

            for item in data.get("results", []):
                url = item.get("url", "")
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)

                title = item.get("title", "").lower()
                desc = item.get("description", "").lower() if item.get("description") else ""
                snippet = item.get("snippet", "").lower() if item.get("snippet") else ""

                # Must be permit-related
                combined = f"{title} {desc} {snippet}"
                permit_keywords = ["permit", "building", "construction"]
                if not any(kw in combined for kw in permit_keywords):
                    continue

                # Build query URL for layer 0
                base_url = url.rstrip("/")
                if "/FeatureServer" not in base_url and "/MapServer" not in base_url:
                    base_url += "/FeatureServer"
                if not base_url.endswith("/query"):
                    # Add /0/query for first layer
                    if re.search(r'/\d+$', base_url):
                        base_url += "/query"
                    else:
                        base_url += "/0/query"

                results.append({
                    "platform": "arcgis",
                    "endpoint": base_url,
                    "name": item.get("title", ""),
                    "description": item.get("snippet", "") or item.get("description", ""),
                    "updated_at": datetime.fromtimestamp(
                        item.get("modified", 0) / 1000
                    ).isoformat() if item.get("modified") else "",
                    "owner": item.get("owner", ""),
                    "access": item.get("access", ""),
                })
        except Exception as e:
            print(f"  [Discovery] ArcGIS Hub search error for '{query}': {e}")
            continue

    return results


def search_ckan(city_name, state, known_ckan_portals=None):
    """Search known CKAN portals for building permit datasets.
    Returns list of {endpoint, dataset_id, name, updated_at}."""
    results = []

    # Common CKAN portals for US cities
    default_portals = [
        f"https://data.{city_name.lower().replace(' ', '')}.gov",
        f"https://data.{city_name.lower().replace(' ', '-')}.gov",
        f"https://opendata.{city_name.lower().replace(' ', '')}.gov",
    ]
    portals = (known_ckan_portals or []) + default_portals

    for portal in portals:
        try:
            # CKAN package_search for building permits
            params = {
                "q": "building permits",
                "rows": 10,
            }
            resp = SESSION.get(
                f"{portal}/api/3/action/package_search",
                params=params,
                timeout=API_TIMEOUT
            )
            if resp.status_code != 200:
                continue
            data = resp.json()
            if not data.get("success"):
                continue

            for pkg in data.get("result", {}).get("results", []):
                name = pkg.get("title", "").lower()
                notes = pkg.get("notes", "").lower() if pkg.get("notes") else ""

                # Must be permit-related
                if not any(kw in name or kw in notes for kw in ["permit", "building"]):
                    continue

                # Find the datastore resource
                for resource in pkg.get("resources", []):
                    if resource.get("datastore_active") or resource.get("format", "").upper() in ("CSV", "JSON"):
                        results.append({
                            "platform": "ckan",
                            "endpoint": f"{portal}/api/3/action/datastore_search",
                            "dataset_id": resource.get("id", ""),
                            "name": pkg.get("title", ""),
                            "description": pkg.get("notes", ""),
                            "updated_at": resource.get("last_modified", "") or pkg.get("metadata_modified", ""),
                            "portal": portal,
                        })
        except Exception as e:
            # Most portals won't exist — that's fine
            continue

    return results


# ============================================================================
# FRESHNESS TESTING
# ============================================================================

def test_socrata_freshness(endpoint, days=30):
    """Test a Socrata endpoint for fresh data. Returns {fresh, newest_date, sample_count, columns, sample_record}."""
    try:
        # First get column metadata
        metadata_url = endpoint.replace("/resource/", "/api/views/").replace(".json", "")
        # Just fetch a small sample first
        since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00")

        # Try to identify date column by fetching 1 record
        resp = SESSION.get(endpoint, params={"$limit": 1}, timeout=API_TIMEOUT)
        if resp.status_code != 200:
            return {"fresh": False, "error": f"HTTP {resp.status_code}"}
        sample = resp.json()
        if not sample:
            return {"fresh": False, "error": "Empty dataset"}

        record = sample[0]
        columns = list(record.keys())

        # Identify date columns
        date_candidates = _find_date_columns(record, columns)

        # Test each date candidate for freshness
        for date_col in date_candidates:
            try:
                params = {
                    "$where": f"{date_col} > '{since}'",
                    "$limit": 5,
                    "$order": f"{date_col} DESC",
                }
                resp = SESSION.get(endpoint, params=params, timeout=API_TIMEOUT)
                if resp.status_code == 200:
                    data = resp.json()
                    if data and len(data) > 0:
                        newest = data[0].get(date_col, "")
                        return {
                            "fresh": True,
                            "date_field": date_col,
                            "newest_date": str(newest)[:10],
                            "sample_count": len(data),
                            "columns": columns,
                            "sample_record": data[0],
                        }
            except Exception:
                continue

        # If no date column worked with filtering, try fetching recent sorted records
        for date_col in date_candidates:
            try:
                params = {"$limit": 5, "$order": f"{date_col} DESC"}
                resp = SESSION.get(endpoint, params=params, timeout=API_TIMEOUT)
                if resp.status_code == 200:
                    data = resp.json()
                    if data:
                        newest = str(data[0].get(date_col, ""))[:10]
                        try:
                            newest_dt = datetime.strptime(newest, "%Y-%m-%d")
                            days_old = (datetime.now() - newest_dt).days
                            return {
                                "fresh": days_old <= days,
                                "date_field": date_col,
                                "newest_date": newest,
                                "days_old": days_old,
                                "sample_count": len(data),
                                "columns": columns,
                                "sample_record": data[0],
                            }
                        except ValueError:
                            continue
            except Exception:
                continue

        return {"fresh": False, "error": "No usable date column found", "columns": columns, "sample_record": record}

    except Exception as e:
        return {"fresh": False, "error": str(e)}


def test_arcgis_freshness(endpoint, days=30):
    """Test an ArcGIS endpoint for fresh data. Returns {fresh, newest_date, sample_count, fields, sample_record}."""
    try:
        # First get layer metadata to find date fields
        meta_url = endpoint.replace("/query", "")
        try:
            resp = SESSION.get(meta_url, params={"f": "json"}, timeout=API_TIMEOUT)
            if resp.status_code == 200:
                meta = resp.json()
                fields = meta.get("fields", [])
            else:
                fields = []
        except Exception:
            fields = []

        # Identify date fields
        date_fields = [
            f["name"] for f in fields
            if f.get("type") in ("esriFieldTypeDate",) or
            any(kw in f.get("name", "").lower() for kw in ["date", "issued", "filed", "created", "applied"])
        ]

        # Also try fetching a sample to identify date fields from data
        try:
            params = {"where": "1=1", "outFields": "*", "resultRecordCount": 1, "f": "json"}
            resp = SESSION.get(endpoint, params=params, timeout=API_TIMEOUT)
            if resp.status_code == 200:
                data = resp.json()
                if "error" not in data and "features" in data and data["features"]:
                    sample_record = data["features"][0].get("attributes", {})
                    all_fields = list(sample_record.keys())

                    # Add date field candidates from sample data
                    for k, v in sample_record.items():
                        if k not in date_fields:
                            if isinstance(v, (int, float)) and v > 1000000000000 and v < 2000000000000:
                                date_fields.append(k)  # Epoch ms
                            elif isinstance(v, str) and re.match(r'\d{4}-\d{2}-\d{2}', str(v)):
                                date_fields.append(k)
                else:
                    return {"fresh": False, "error": "Empty dataset or ArcGIS error", "raw": data.get("error", {})}
        except Exception as e:
            return {"fresh": False, "error": f"Sample fetch failed: {e}"}

        if not date_fields:
            # Guess from field names
            for k in all_fields:
                kl = k.lower()
                if any(kw in kl for kw in ["date", "issued", "filed", "created", "applied", "permit_dat"]):
                    date_fields.append(k)

        # Try each date field
        since_epoch = int((datetime.now() - timedelta(days=days)).timestamp() * 1000)
        since_iso = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

        for date_field in date_fields:
            # Try epoch comparison first
            for where_clause in [
                f"{date_field} >= {since_epoch}",
                f"{date_field} >= DATE '{since_iso}'",
                "1=1",  # Fallback: fetch all, filter in Python
            ]:
                try:
                    params = {
                        "where": where_clause,
                        "outFields": "*",
                        "resultRecordCount": 5,
                        "orderByFields": f"{date_field} DESC",
                        "f": "json",
                    }
                    resp = SESSION.get(endpoint, params=params, timeout=API_TIMEOUT)
                    if resp.status_code != 200:
                        continue
                    data = resp.json()
                    if "error" in data:
                        continue
                    features = data.get("features", [])
                    if not features:
                        continue

                    records = [f["attributes"] for f in features]
                    newest_val = records[0].get(date_field)

                    # Parse the date value
                    newest_date = None
                    if isinstance(newest_val, (int, float)) and newest_val > 1000000000000:
                        newest_date = datetime.fromtimestamp(newest_val / 1000)
                    elif isinstance(newest_val, str):
                        try:
                            newest_date = datetime.strptime(str(newest_val)[:10], "%Y-%m-%d")
                        except ValueError:
                            pass

                    if newest_date:
                        days_old = (datetime.now() - newest_date).days
                        # Determine date_format for config
                        if isinstance(newest_val, (int, float)):
                            date_format = "epoch"
                        else:
                            date_format = "date"
                        # If the where=1=1 fallback was needed, use "none"
                        if where_clause == "1=1":
                            date_format = "none"

                        return {
                            "fresh": days_old <= days,
                            "date_field": date_field,
                            "date_format": date_format,
                            "newest_date": newest_date.strftime("%Y-%m-%d"),
                            "days_old": days_old,
                            "sample_count": len(records),
                            "fields": all_fields if 'all_fields' in dir() else [f["name"] for f in fields],
                            "sample_record": records[0],
                        }
                except Exception:
                    continue

        return {
            "fresh": False,
            "error": "No date field produced fresh data",
            "date_fields_tried": date_fields,
            "fields": all_fields if 'all_fields' in dir() else [f["name"] for f in fields],
        }

    except Exception as e:
        return {"fresh": False, "error": str(e)}


def test_ckan_freshness(endpoint, dataset_id, days=30):
    """Test a CKAN endpoint for fresh data."""
    try:
        params = {"resource_id": dataset_id, "limit": 5}
        resp = SESSION.get(endpoint, params=params, timeout=API_TIMEOUT)
        if resp.status_code != 200:
            return {"fresh": False, "error": f"HTTP {resp.status_code}"}
        data = resp.json()
        if not data.get("success") or "result" not in data:
            return {"fresh": False, "error": "CKAN API error"}

        records = data["result"].get("records", [])
        if not records:
            return {"fresh": False, "error": "Empty dataset"}

        # Get field names and metadata
        fields_meta = data["result"].get("fields", [])
        columns = [f.get("id", "") for f in fields_meta]
        record = records[0]

        # Find date columns
        date_candidates = _find_date_columns(record, columns)

        since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

        # Sort by each date candidate and check freshness
        for date_col in date_candidates:
            try:
                params = {"resource_id": dataset_id, "limit": 5, "sort": f"{date_col} desc"}
                resp = SESSION.get(endpoint, params=params, timeout=API_TIMEOUT)
                if resp.status_code != 200:
                    continue
                data = resp.json()
                if not data.get("success"):
                    continue
                recs = data["result"].get("records", [])
                if not recs:
                    continue
                newest = str(recs[0].get(date_col, ""))[:10]
                try:
                    newest_dt = datetime.strptime(newest, "%Y-%m-%d")
                    days_old = (datetime.now() - newest_dt).days
                    return {
                        "fresh": days_old <= days,
                        "date_field": date_col,
                        "newest_date": newest,
                        "days_old": days_old,
                        "sample_count": len(recs),
                        "columns": columns,
                        "sample_record": recs[0],
                    }
                except ValueError:
                    # Try M/D/YYYY format
                    try:
                        newest_dt = datetime.strptime(newest, "%m/%d/%Y")
                        days_old = (datetime.now() - newest_dt).days
                        return {
                            "fresh": days_old <= days,
                            "date_field": date_col,
                            "newest_date": newest_dt.strftime("%Y-%m-%d"),
                            "days_old": days_old,
                            "sample_count": len(recs),
                            "columns": columns,
                            "sample_record": recs[0],
                        }
                    except ValueError:
                        continue
            except Exception:
                continue

        return {"fresh": False, "error": "No usable date column", "columns": columns, "sample_record": record}

    except Exception as e:
        return {"fresh": False, "error": str(e)}


# ============================================================================
# FIELD MAPPING BUILDER
# ============================================================================

def build_field_map(columns, sample_record, platform):
    """Auto-detect field mapping from column names and sample data.
    Returns a dict compatible with CITY_REGISTRY field_map format."""

    field_map = {}
    cols_lower = {c.lower(): c for c in columns}

    # Permit number patterns
    for pattern in ["permit_number", "permit_num", "permitnum", "permitnumb",
                    "permit_no", "permit #", "permit#", "record id", "record_id",
                    "application", "app_no", "case_number", "casenumber",
                    "permit_id", "permitid", "folderrsn"]:
        if pattern in cols_lower:
            field_map["permit_number"] = cols_lower[pattern]
            break

    # Permit type patterns
    for pattern in ["permit_type", "permittype", "type", "permit_type_name",
                    "work_class", "work_type", "category", "p_type"]:
        if pattern in cols_lower:
            field_map["permit_type"] = cols_lower[pattern]
            break

    # Work type / subtype
    for pattern in ["work_type", "sub_type", "subtype", "permit_subtype",
                    "classwork", "work_class", "permit_subtype_name"]:
        if pattern in cols_lower and cols_lower[pattern] != field_map.get("permit_type"):
            field_map["work_type"] = cols_lower[pattern]
            break

    # Address
    for pattern in ["address", "full_address", "site_address", "project_address",
                    "property_address", "location", "street_address"]:
        if pattern in cols_lower:
            field_map["address"] = cols_lower[pattern]
            break

    # ZIP code
    for pattern in ["zip", "zipcode", "zip_code", "postal_code"]:
        if pattern in cols_lower:
            field_map["zip"] = cols_lower[pattern]
            break

    # Owner
    for pattern in ["owner_name", "owner", "property_owner", "applicant_name"]:
        if pattern in cols_lower:
            field_map["owner_name"] = cols_lower[pattern]
            break

    # Contractor / contact
    for pattern in ["contractor", "contractor_name", "contact_name",
                    "permit_applicant", "primary_contact", "applicant"]:
        if pattern in cols_lower:
            field_map["contact_name"] = cols_lower[pattern]
            break

    # Cost / valuation
    for pattern in ["estimated_cost", "valuation", "construction_total_cost",
                    "declared_valuation", "total_cost", "project_value",
                    "construction total cost", "declared valuation",
                    "permitvalu", "fees_paid", "job_value"]:
        if pattern in cols_lower:
            field_map["estimated_cost"] = cols_lower[pattern]
            break

    # Description
    for pattern in ["description", "desc_of_work", "work_desc", "work_description",
                    "project_name", "project_description", "descriptio",
                    "scope_of_work", "p_desc", "use_of_building"]:
        if pattern in cols_lower:
            field_map["description"] = cols_lower[pattern]
            break

    # Status
    for pattern in ["status", "application_status", "current_status",
                    "application_status_name", "permit_status"]:
        if pattern in cols_lower:
            field_map["status"] = cols_lower[pattern]
            break

    return field_map


def _find_date_columns(record, columns):
    """Identify likely date columns from a sample record."""
    candidates = []
    date_keywords = ["date", "issued", "filed", "created", "applied", "submitted",
                     "approved", "status_date", "filing"]
    for col in columns:
        col_lower = col.lower()
        if any(kw in col_lower for kw in date_keywords):
            candidates.append(col)
            continue
        # Check value looks like a date
        val = record.get(col, "")
        if isinstance(val, str) and re.match(r'\d{4}-\d{2}-\d{2}', val):
            candidates.append(col)
        elif isinstance(val, str) and re.match(r'\d{1,2}/\d{1,2}/\d{4}', val):
            candidates.append(col)
        elif isinstance(val, (int, float)) and 1000000000000 < val < 2000000000000:
            candidates.append(col)  # Epoch ms

    # Prioritize: filing_date, issue_date, date_issued, then others
    priority = ["filing_date", "issue_date", "date_issued", "date issued",
                "issued_date", "applied_date", "status_date", "permit_dat",
                "dateissued", "issueddate"]
    sorted_candidates = []
    for p in priority:
        for c in candidates:
            if c.lower() == p or c.lower().replace("_", "") == p.replace("_", ""):
                sorted_candidates.append(c)
    for c in candidates:
        if c not in sorted_candidates:
            sorted_candidates.append(c)

    return sorted_candidates


# ============================================================================
# MAIN DISCOVERY FLOW
# ============================================================================

# Cities we want to find fresh endpoints for, with known stale info
STALE_CITIES = {
    "washington_dc": {
        "name": "Washington DC", "state": "DC", "slug": "washington-dc",
        "lat": 38.907, "lon": -77.037, "population": 690000,
        "known_portals": ["https://opendata.dc.gov"],
        "notes": "DCRA FeatureServer may be working — also try opendata.dc.gov Socrata",
    },
    "baltimore": {
        "name": "Baltimore", "state": "MD", "slug": "baltimore",
        "lat": 39.290, "lon": -76.612, "population": 600000,
        "notes": "ArcGIS endpoint was fresh (2026-03-29) but date_format broke it. Should work now with 'none' fix.",
    },
    "milwaukee": {
        "name": "Milwaukee", "state": "WI", "slug": "milwaukee",
        "lat": 43.039, "lon": -87.906, "population": 580000,
        "known_portals": ["https://data.milwaukee.gov"],
        "notes": "CKAN endpoint stale since Feb 2026. May have switched vendors.",
    },
    "sacramento": {
        "name": "Sacramento", "state": "CA", "slug": "sacramento",
        "lat": 38.582, "lon": -121.494, "population": 525000,
        "notes": "ArcGIS 'CurrentYear' endpoint stale Feb 2026. Check if new year dataset exists.",
    },
    "kansas_city": {
        "name": "Kansas City", "state": "MO", "slug": "kansas-city",
        "lat": 39.1, "lon": -94.581, "population": 500000,
        "known_portals": ["https://data.kcmo.org"],
        "notes": "Socrata returned empty. May have new dataset ID or switched platforms.",
    },
    "honolulu": {
        "name": "Honolulu", "state": "HI", "slug": "honolulu",
        "lat": 21.307, "lon": -157.858, "population": 350000,
        "notes": "Static Socrata dataset through June 2025. HNL Build (Salesforce) may have API now.",
    },
    "pittsburgh": {
        "name": "Pittsburgh", "state": "PA", "slug": "pittsburgh",
        "lat": 40.441, "lon": -79.996, "population": 300000,
        "notes": "Was fresh (2026-03-27) but collection failed. Should work now with fixes.",
    },
    "durham": {
        "name": "Durham", "state": "NC", "slug": "durham",
        "lat": 35.994, "lon": -78.899, "population": 280000,
        "notes": "ArcGIS MapServer — check if date_format 'none' fix resolved it.",
    },
    "knoxville": {
        "name": "Knoxville", "state": "TN", "slug": "knoxville",
        "lat": 35.961, "lon": -83.921, "population": 190000,
        "notes": "ArcGIS FeatureServer with date_format 'none'. Should work after fix.",
    },
    "chattanooga": {
        "name": "Chattanooga", "state": "TN", "slug": "chattanooga",
        "lat": 35.046, "lon": -85.309, "population": 180000,
        "notes": "ArcGIS FeatureServer — dataset name says '2025'. May need 2026 dataset.",
    },
}


def discover_for_city(city_key, city_info, existing_config=None):
    """Run full discovery for a single city:
    1. Test existing config if we have one
    2. Search all platforms for alternatives
    3. Test each discovered endpoint for 30-day freshness
    4. Return best working config

    Returns:
    {
        "city_key": str,
        "status": "FOUND" | "EXISTING_WORKS" | "STALE" | "NOT_FOUND",
        "config": {...} if found,
        "freshness": {...} test results,
        "all_discovered": [...] all endpoints found,
        "all_tested": [...] freshness test results for each,
    }
    """
    city_name = city_info["name"]
    state = city_info["state"]
    result = {
        "city_key": city_key,
        "city_name": city_name,
        "state": state,
        "population": city_info.get("population", 0),
        "status": "NOT_FOUND",
        "config": None,
        "freshness": None,
        "all_discovered": [],
        "all_tested": [],
    }

    print(f"\n{'='*60}")
    print(f"[Discovery] {city_name}, {state} (pop {city_info.get('population', '?')})")
    print(f"{'='*60}")

    # Step 1: Test existing config if available
    if existing_config and existing_config.get("endpoint"):
        print(f"  Testing existing config: {existing_config['platform']} @ {existing_config['endpoint'][:80]}...")
        platform = existing_config.get("platform", "socrata")
        endpoint = existing_config["endpoint"]

        if platform == "socrata":
            test = test_socrata_freshness(endpoint, 30)
        elif platform == "arcgis":
            test = test_arcgis_freshness(endpoint, 30)
        elif platform == "ckan":
            test = test_ckan_freshness(endpoint, existing_config.get("dataset_id", ""), 30)
        else:
            test = {"fresh": False, "error": f"Unknown platform: {platform}"}

        result["all_tested"].append({"source": "existing_config", "endpoint": endpoint, **test})

        if test.get("fresh"):
            print(f"  ✓ EXISTING CONFIG IS FRESH! Newest: {test.get('newest_date')}")
            result["status"] = "EXISTING_WORKS"
            result["config"] = existing_config
            result["freshness"] = test
            return result
        else:
            print(f"  ✗ Existing config stale/broken: {test.get('error', 'no fresh data')}")

    # Step 2: Search all platforms
    print(f"  Searching Socrata...")
    socrata_results = search_socrata(city_name, state)
    print(f"  Found {len(socrata_results)} Socrata datasets")
    result["all_discovered"].extend(socrata_results)

    print(f"  Searching ArcGIS Hub...")
    arcgis_results = search_arcgis_hub(city_name, state)
    print(f"  Found {len(arcgis_results)} ArcGIS services")
    result["all_discovered"].extend(arcgis_results)

    if city_info.get("known_portals"):
        print(f"  Searching CKAN portals...")
        ckan_results = search_ckan(city_name, state, city_info.get("known_portals"))
        print(f"  Found {len(ckan_results)} CKAN datasets")
        result["all_discovered"].extend(ckan_results)

    if not result["all_discovered"]:
        print(f"  ✗ No datasets found on any platform")
        result["status"] = "NOT_FOUND"
        return result

    # Step 3: Test each discovered endpoint for freshness
    print(f"  Testing {len(result['all_discovered'])} discovered endpoints...")
    best = None
    best_score = -1

    for i, disc in enumerate(result["all_discovered"]):
        platform = disc["platform"]
        endpoint = disc["endpoint"]
        print(f"    [{i+1}/{len(result['all_discovered'])}] {platform}: {disc.get('name', endpoint[:60])}...")

        if platform == "socrata":
            test = test_socrata_freshness(endpoint, 30)
        elif platform == "arcgis":
            test = test_arcgis_freshness(endpoint, 30)
        elif platform == "ckan":
            test = test_ckan_freshness(endpoint, disc.get("dataset_id", ""), 30)
        else:
            test = {"fresh": False, "error": f"Unknown platform"}

        test_entry = {"source": "discovered", "endpoint": endpoint, "name": disc.get("name"), **test}
        result["all_tested"].append(test_entry)

        if test.get("fresh"):
            # Score: prefer more records, newer data, more columns
            score = test.get("sample_count", 0) * 10
            if test.get("days_old") is not None:
                score += max(0, 30 - test["days_old"])  # Fresher = higher score
            cols = test.get("columns") or test.get("fields") or []
            score += len(cols)  # More fields = better
            print(f"      ✓ FRESH! Newest: {test.get('newest_date')}, score: {score}")

            if score > best_score:
                best_score = score
                best = (disc, test)
        else:
            print(f"      ✗ {test.get('error', 'stale')}")

        time.sleep(0.5)  # Rate limiting between tests

    if not best:
        print(f"  ✗ No fresh endpoints found for {city_name}")
        result["status"] = "STALE"
        return result

    # Step 4: Build config from best result
    disc, test = best
    platform = disc["platform"]
    columns = test.get("columns") or test.get("fields") or []
    sample = test.get("sample_record", {})
    field_map = build_field_map(columns, sample, platform)

    # Add date field info
    date_field = test.get("date_field", "")
    if date_field:
        field_map["filing_date"] = date_field

    config = {
        "name": city_name,
        "state": state,
        "slug": city_info.get("slug", city_name.lower().replace(" ", "-")),
        "lat": city_info.get("lat", 0),
        "lon": city_info.get("lon", 0),
        "platform": platform,
        "endpoint": disc["endpoint"],
        "dataset_id": disc.get("dataset_id", ""),
        "description": disc.get("name", f"{city_name} Building Permits"),
        "field_map": field_map,
        "date_field": date_field,
        "limit": 2000,
        "active": True,
        "notes": f"V35 Auto-discovered {datetime.now().strftime('%Y-%m-%d')}. Source: {disc.get('name', 'unknown')}",
    }

    # Add date_format for ArcGIS
    if platform == "arcgis" and test.get("date_format"):
        config["date_format"] = test["date_format"]

    # Add CKAN dataset_id
    if platform == "ckan" and disc.get("dataset_id"):
        config["dataset_id"] = disc["dataset_id"]

    print(f"  ✓ FOUND: {disc.get('name', endpoint)} (newest: {test.get('newest_date')})")
    print(f"    Fields mapped: {list(field_map.keys())}")

    result["status"] = "FOUND"
    result["config"] = config
    result["freshness"] = test
    return result


def discover_all(target_cities=None):
    """Run discovery for all stale cities (or a subset).
    Returns dict of {city_key: discovery_result}."""
    targets = target_cities or list(STALE_CITIES.keys())
    results = {}

    # Load existing configs for comparison
    try:
        from city_configs import CITY_REGISTRY
        existing_configs = CITY_REGISTRY
    except ImportError:
        existing_configs = {}

    for city_key in targets:
        if city_key not in STALE_CITIES:
            print(f"[Discovery] Unknown city: {city_key}, skipping")
            continue

        city_info = STALE_CITIES[city_key]
        existing = existing_configs.get(city_key)
        result = discover_for_city(city_key, city_info, existing)
        results[city_key] = result
        time.sleep(1)  # Rate limit between cities

    # Summary
    print(f"\n{'='*60}")
    print(f"DISCOVERY SUMMARY")
    print(f"{'='*60}")
    found = [k for k, v in results.items() if v["status"] in ("FOUND", "EXISTING_WORKS")]
    stale = [k for k, v in results.items() if v["status"] == "STALE"]
    missing = [k for k, v in results.items() if v["status"] == "NOT_FOUND"]

    print(f"  FRESH: {len(found)} — {', '.join(found)}")
    print(f"  STALE: {len(stale)} — {', '.join(stale)}")
    print(f"  NOT FOUND: {len(missing)} — {', '.join(missing)}")

    return results
