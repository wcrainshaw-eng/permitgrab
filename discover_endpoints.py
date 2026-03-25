#!/usr/bin/env python3
"""
V17h: Automated endpoint discovery for cities missing data.

PHASE 1: Test known candidate endpoints for high-priority cities
PHASE 2: Re-test all inactive cities from city_configs.py
PHASE 3: Try Socrata Discovery API to find permit datasets

Run on Render shell where outbound HTTP is allowed:
    python3 discover_endpoints.py

Output: discovery_results.json + suggested city_configs entries
"""

import json
import sys
import time
import urllib.request
import urllib.parse
import urllib.error
import ssl

# Relax SSL for government sites with bad certs
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

TIMEOUT = 12

# ─────────────────────────────────────────────────────────────────
# KNOWN SOCRATA DOMAINS — Discovery API: /api/catalog/v1
# These are cities/counties known to run Socrata open data portals
# ─────────────────────────────────────────────────────────────────
SOCRATA_DOMAINS = {
    # Big cities with known portals
    "minneapolis": {"domain": "opendata.minneapolismn.gov", "name": "Minneapolis", "state": "MN"},
    "detroit": {"domain": "data.detroitmi.gov", "name": "Detroit", "state": "MI"},
    "sacramento": {"domain": "data.cityofsacramento.org", "name": "Sacramento", "state": "CA"},
    "columbus": {"domain": "data.columbus.gov", "name": "Columbus", "state": "OH"},
    "fort_worth": {"domain": "data.fortworthtexas.gov", "name": "Fort Worth", "state": "TX"},
    "memphis": {"domain": "data.memphistn.gov", "name": "Memphis", "state": "TN"},
    "louisville": {"domain": "data.louisvilleky.gov", "name": "Louisville", "state": "KY"},
    "milwaukee": {"domain": "data.milwaukee.gov", "name": "Milwaukee", "state": "WI"},
    "tucson": {"domain": "data.tucsonaz.gov", "name": "Tucson", "state": "AZ"},
    "san_jose": {"domain": "data.sanjoseca.gov", "name": "San Jose", "state": "CA"},
    "charlotte": {"domain": "data.charlottenc.gov", "name": "Charlotte", "state": "NC"},
    "indianapolis": {"domain": "data.indy.gov", "name": "Indianapolis", "state": "IN"},
    "las_vegas": {"domain": "opendataportal-lasvegas.opendata.arcgis.com", "name": "Las Vegas", "state": "NV"},
    "jacksonville": {"domain": "data.coj.net", "name": "Jacksonville", "state": "FL"},
    "virginia_beach": {"domain": "data.vbgov.com", "name": "Virginia Beach", "state": "VA"},
    "albuquerque": {"domain": "data.cabq.gov", "name": "Albuquerque", "state": "NM"},
    "cleveland": {"domain": "data.clevelandohio.gov", "name": "Cleveland", "state": "OH"},
    "oakland": {"domain": "data.oaklandca.gov", "name": "Oakland", "state": "CA"},
    "long_beach": {"domain": "data.longbeach.gov", "name": "Long Beach", "state": "CA"},
    "omaha": {"domain": "data.cityofomaha.org", "name": "Omaha", "state": "NE"},
    "st_louis": {"domain": "data.stlouis-mo.gov", "name": "St. Louis", "state": "MO"},
    "oklahoma_city": {"domain": "data.okc.gov", "name": "Oklahoma City", "state": "OK"},
    "fresno": {"domain": "data.fresno.gov", "name": "Fresno", "state": "CA"},
    "tampa": {"domain": "data.tampagov.net", "name": "Tampa", "state": "FL"},
    "orlando": {"domain": "data.cityoforlando.net", "name": "Orlando", "state": "FL"},
    "dallas": {"domain": "www.dallasopendata.com", "name": "Dallas", "state": "TX"},
}


# ─────────────────────────────────────────────────────────────────
# PHASE 1: Known candidate endpoints (manually researched)
# ─────────────────────────────────────────────────────────────────
KNOWN_CANDIDATES = {
    "minneapolis": [
        {
            "platform": "arcgis",
            "label": "OpenDataMPLS CCS Permits",
            "url": "https://services.arcgis.com/afSMGVsC7QlRK1kZ/arcgis/rest/services/CCS_Permits/FeatureServer/0/query",
            "params": {"where": "1=1", "outFields": "*", "resultRecordCount": "3", "f": "json"},
        },
    ],
    "las_vegas": [
        {
            "platform": "arcgis",
            "label": "Clark County Building Permits (Hub)",
            "url": "https://services1.arcgis.com/0MSEUqKaxRlEPj5g/arcgis/rest/services/Building_Permits/FeatureServer/0/query",
            "params": {"where": "1=1", "outFields": "*", "resultRecordCount": "3", "f": "json"},
        },
    ],
    "sacramento": [
        {
            "platform": "socrata",
            "label": "Sacramento Building Permits (utr9-2uk7)",
            "url": "https://data.cityofsacramento.org/resource/utr9-2uk7.json",
            "params": {"$limit": "3", "$order": ":id"},
        },
        {
            "platform": "socrata",
            "label": "Sacramento Building Permits (iim8-5xrd)",
            "url": "https://data.cityofsacramento.org/resource/iim8-5xrd.json",
            "params": {"$limit": "3"},
        },
    ],
    "columbus": [
        {
            "platform": "socrata",
            "label": "Columbus Building Permits",
            "url": "https://data.columbus.gov/resource/rkj4-kqk5.json",
            "params": {"$limit": "3"},
        },
    ],
    "detroit": [
        {
            "platform": "socrata",
            "label": "Detroit Building Permits (but4-ky7y)",
            "url": "https://data.detroitmi.gov/resource/but4-ky7y.json",
            "params": {"$limit": "3"},
        },
        {
            "platform": "socrata",
            "label": "Detroit Building Permits (xw2a-a7tf)",
            "url": "https://data.detroitmi.gov/resource/xw2a-a7tf.json",
            "params": {"$limit": "3"},
        },
    ],
    "charlotte": [
        {
            "platform": "arcgis",
            "label": "Mecklenburg Code Enforcement Permits",
            "url": "https://gis.mecklenburgcountync.gov/agsrest/rest/services/agsOpenData/OpenData/MapServer/0/query",
            "params": {"where": "1=1", "outFields": "*", "resultRecordCount": "3", "f": "json"},
        },
    ],
    "indianapolis": [
        {
            "platform": "arcgis",
            "label": "Indy Open Data Permits",
            "url": "https://xmaps.indy.gov/arcgis/rest/services/OpenData/OpenData_Permits/MapServer/0/query",
            "params": {"where": "1=1", "outFields": "*", "resultRecordCount": "3", "f": "json"},
        },
        {
            "platform": "socrata",
            "label": "Indy Socrata Permits",
            "url": "https://data.indy.gov/resource/nnxn-fxrx.json",
            "params": {"$limit": "3"},
        },
    ],
    "fort_worth": [
        {
            "platform": "socrata",
            "label": "Fort Worth Building Permits (kzjm-qs2t)",
            "url": "https://data.fortworthtexas.gov/resource/kzjm-qs2t.json",
            "params": {"$limit": "3"},
        },
        {
            "platform": "socrata",
            "label": "Fort Worth Building Permits (3bgt-mfym)",
            "url": "https://data.fortworthtexas.gov/resource/3bgt-mfym.json",
            "params": {"$limit": "3"},
        },
    ],
    "memphis": [
        {
            "platform": "socrata",
            "label": "Memphis Building Permits",
            "url": "https://data.memphistn.gov/resource/zkg7-s2ja.json",
            "params": {"$limit": "3"},
        },
    ],
    "louisville": [
        {
            "platform": "socrata",
            "label": "Louisville Building Permits",
            "url": "https://data.louisvilleky.gov/resource/6yv6-jhas.json",
            "params": {"$limit": "3"},
        },
    ],
    "milwaukee": [
        {
            "platform": "socrata",
            "label": "Milwaukee Building Permits",
            "url": "https://data.milwaukee.gov/resource/nhz4-ua37.json",
            "params": {"$limit": "3"},
        },
    ],
    "tucson": [
        {
            "platform": "socrata",
            "label": "Tucson Permits",
            "url": "https://data.tucsonaz.gov/resource/k8gp-7x4q.json",
            "params": {"$limit": "3"},
        },
    ],
    "san_jose": [
        {
            "platform": "socrata",
            "label": "San Jose Building Permits (w3se-yg9h)",
            "url": "https://data.sanjoseca.gov/resource/w3se-yg9h.json",
            "params": {"$limit": "3"},
        },
    ],
    "jacksonville": [
        {
            "platform": "socrata",
            "label": "Jacksonville Building Permits",
            "url": "https://data.coj.net/resource/3bfz-cma5.json",
            "params": {"$limit": "3"},
        },
    ],
    "oklahoma_city": [
        {
            "platform": "socrata",
            "label": "OKC Building Permits",
            "url": "https://data.okc.gov/resource/building-permits.json",
            "params": {"$limit": "3"},
        },
    ],
    "albuquerque": [
        {
            "platform": "socrata",
            "label": "Albuquerque Building Permits",
            "url": "https://data.cabq.gov/resource/p264-kbbh.json",
            "params": {"$limit": "3"},
        },
    ],
    "cleveland": [
        {
            "platform": "socrata",
            "label": "Cleveland Building Permits",
            "url": "https://data.clevelandohio.gov/resource/uxfa-b9ac.json",
            "params": {"$limit": "3"},
        },
    ],
    "oakland": [
        {
            "platform": "socrata",
            "label": "Oakland Building Permits",
            "url": "https://data.oaklandca.gov/resource/yv2t-jfbp.json",
            "params": {"$limit": "3"},
        },
    ],
    "long_beach": [
        {
            "platform": "socrata",
            "label": "Long Beach Building Permits",
            "url": "https://data.longbeach.gov/resource/7vu9-abxq.json",
            "params": {"$limit": "3"},
        },
    ],
    "omaha": [
        {
            "platform": "socrata",
            "label": "Omaha Building Permits",
            "url": "https://data.cityofomaha.org/resource/rxrh-bzig.json",
            "params": {"$limit": "3"},
        },
    ],
    "st_louis": [
        {
            "platform": "socrata",
            "label": "St. Louis Building Permits",
            "url": "https://data.stlouis-mo.gov/resource/building-permits.json",
            "params": {"$limit": "3"},
        },
    ],
    "fresno": [
        {
            "platform": "socrata",
            "label": "Fresno Building Permits",
            "url": "https://data.fresno.gov/resource/building-permits.json",
            "params": {"$limit": "3"},
        },
    ],
    "tampa": [
        {
            "platform": "socrata",
            "label": "Tampa Building Permits",
            "url": "https://data.tampagov.net/resource/sxrx-mxe3.json",
            "params": {"$limit": "3"},
        },
    ],
    "orlando": [
        {
            "platform": "socrata",
            "label": "Orlando Building Permits",
            "url": "https://data.cityoforlando.net/resource/ryhf-m453.json",
            "params": {"$limit": "3"},
        },
    ],
    "virginia_beach": [
        {
            "platform": "socrata",
            "label": "Virginia Beach Building Permits",
            "url": "https://data.vbgov.com/resource/building-permits.json",
            "params": {"$limit": "3"},
        },
    ],
}


def fetch_json(url, params=None):
    """Fetch JSON from a URL with query parameters."""
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "User-Agent": "PermitGrab-Discovery/1.0",
        "Accept": "application/json",
    })
    try:
        resp = urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx)
        data = resp.read().decode("utf-8", errors="replace")
        return json.loads(data)
    except urllib.error.HTTPError as e:
        return {"_error": f"HTTP {e.code}: {e.reason}"}
    except urllib.error.URLError as e:
        return {"_error": f"URL Error: {e.reason}"}
    except json.JSONDecodeError:
        return {"_error": "Invalid JSON response"}
    except Exception as e:
        return {"_error": str(e)}


def test_arcgis(url, params=None):
    """Test an ArcGIS endpoint. Returns (fields, sample, error)."""
    if params is None:
        params = {"where": "1=1", "outFields": "*", "resultRecordCount": "3", "f": "json"}
    data = fetch_json(url, params)
    if "_error" in data:
        return None, None, data["_error"]
    if "error" in data:
        return None, None, f"ArcGIS error: {data['error'].get('message', 'unknown')}"
    features = data.get("features", [])
    if not features:
        return None, None, "No features returned"
    attrs = features[0].get("attributes", {})
    return list(attrs.keys()), attrs, None


def test_socrata(url, params=None):
    """Test a Socrata endpoint. Returns (fields, sample, error)."""
    if params is None:
        params = {"$limit": "3"}
    data = fetch_json(url, params)
    if "_error" in data:
        return None, None, data["_error"]
    if isinstance(data, dict) and ("error" in data or "message" in data):
        return None, None, f"Socrata error: {data.get('message', data.get('error', 'unknown'))}"
    if not isinstance(data, list) or len(data) == 0:
        return None, None, "No records returned"
    return list(data[0].keys()), data[0], None


def search_socrata_catalog(domain, search_term="permit"):
    """Use Socrata Discovery API to find permit datasets on a domain."""
    url = f"https://api.us.socrata.com/api/catalog/v1"
    params = {
        "domains": domain,
        "search_context": domain,
        "q": search_term,
        "limit": "10",
    }
    data = fetch_json(url, params)
    if "_error" in data:
        return None, data["_error"]

    results = data.get("results", [])
    if not results:
        return None, "No datasets found"

    datasets = []
    for r in results:
        resource = r.get("resource", {})
        link = r.get("link", "")
        name = resource.get("name", "")
        dataset_id = resource.get("id", "")
        desc = resource.get("description", "")[:100]
        row_count = resource.get("page_views", {}).get("page_views_total", 0)
        columns = resource.get("columns_name", [])

        # Filter for building/construction permits
        name_lower = name.lower()
        if any(k in name_lower for k in ["permit", "building", "construction", "development"]):
            datasets.append({
                "name": name,
                "dataset_id": dataset_id,
                "description": desc,
                "columns": columns[:20],
                "link": link,
                "views": row_count,
            })

    return datasets, None


def auto_map_fields(fields, sample):
    """Auto-detect field mappings from field names."""
    fm = {}
    for f in fields:
        fl = f.lower()
        # Permit number
        if not fm.get("permit_number") and any(k in fl for k in ["permit_n", "permno", "case_n", "casenumber", "folderrsn", "record_id", "permit_id", "permitno", "permit_num"]):
            fm["permit_number"] = f
        # Address
        elif not fm.get("address") and any(k in fl for k in ["address", "addr", "street", "location"]):
            fm["address"] = f
        # Permit type
        elif not fm.get("permit_type") and any(k in fl for k in ["permit_type", "permittype", "type", "class", "category", "permit_class"]):
            fm["permit_type"] = f
        # Work type
        elif not fm.get("work_type") and any(k in fl for k in ["work_type", "worktype", "subtype"]):
            fm["work_type"] = f
        # Contractor
        elif not fm.get("contact_name") and any(k in fl for k in ["contractor", "builder", "applicant", "permittee"]):
            fm["contact_name"] = f
        # Owner
        elif not fm.get("owner_name") and any(k in fl for k in ["owner"]):
            fm["owner_name"] = f
        # Zip
        elif not fm.get("zip") and any(k in fl for k in ["zip", "postal"]):
            fm["zip"] = f
        # Date
        elif not fm.get("filing_date") and any(k in fl for k in ["issued", "issue_date", "issueddate", "filing_date", "date_issued", "issuance_date", "applied_date"]):
            fm["filing_date"] = f
        # Status
        elif not fm.get("status") and any(k in fl for k in ["status"]):
            fm["status"] = f
        # Cost
        elif not fm.get("estimated_cost") and any(k in fl for k in ["valuation", "cost", "value", "amount", "fee"]):
            fm["estimated_cost"] = f
        # Description
        elif not fm.get("description") and any(k in fl for k in ["description", "desc", "work_desc", "scope"]):
            fm["description"] = f
        # Phone
        elif not fm.get("contact_phone") and any(k in fl for k in ["phone", "tel"]):
            fm["contact_phone"] = f

    # Detect epoch dates
    date_format = ""
    date_field = fm.get("filing_date", "")
    if date_field and date_field in sample:
        val = sample[date_field]
        if isinstance(val, (int, float)) and val > 1000000000:
            date_format = "epoch"

    return fm, date_field, date_format


def print_config_entry(city_key, name, state, platform, endpoint, fields, sample):
    """Print a suggested city_configs.py entry."""
    fm, date_field, date_format = auto_map_fields(fields, sample)
    slug = city_key.replace("_", "-")

    print(f'\n    "{city_key}": {{')
    print(f'        "name": "{name}",')
    print(f'        "state": "{state}",')
    print(f'        "slug": "{slug}",')
    print(f'        "platform": "{platform}",')
    print(f'        "endpoint": "{endpoint}",')
    print(f'        "dataset_id": "{city_key}_permits",')
    print(f'        "description": "Building Permits",')
    print(f'        "field_map": {{')
    for k, v in fm.items():
        print(f'            "{k}": "{v}",')
    print(f'        }},')
    print(f'        "date_field": "{date_field}",')
    if date_format:
        print(f'        "date_format": "{date_format}",')
    print(f'        "limit": 2000,')
    print(f'        "active": True,')
    print(f'        "notes": "V17h: Auto-discovered endpoint",')
    print(f'    }},')


def main():
    print("=" * 70)
    print("PermitGrab Endpoint Discovery — V17h")
    print("=" * 70)

    successes = []
    failures = []

    # ─── PHASE 1: Test known candidate endpoints ───
    print(f"\n\n{'=' * 70}")
    print("PHASE 1: Testing known candidate endpoints")
    print(f"{'=' * 70}")
    print(f"  {len(KNOWN_CANDIDATES)} cities to test\n")

    for city_key, tests in KNOWN_CANDIDATES.items():
        info = SOCRATA_DOMAINS.get(city_key, {})
        name = info.get("name", city_key.replace("_", " ").title())
        state = info.get("state", "??")
        print(f"\n  {'─' * 40}")
        print(f"  {name}, {state}")

        found = False
        for test in tests:
            platform = test["platform"]
            label = test["label"]
            url = test["url"]
            params = test.get("params")
            print(f"    Testing: {label}")

            if platform == "arcgis":
                fields, sample, error = test_arcgis(url, params)
            else:
                fields, sample, error = test_socrata(url, params)

            if error:
                print(f"    ❌ {error}")
            else:
                print(f"    ✅ SUCCESS — {len(fields)} fields: {', '.join(fields[:10])}...")
                successes.append({
                    "city_key": city_key, "name": name, "state": state,
                    "platform": platform, "endpoint": url,
                    "fields": fields, "sample": sample,
                })
                found = True
                break

            time.sleep(0.2)

        if not found:
            failures.append({"city_key": city_key, "name": name, "state": state})

    # ─── PHASE 2: Socrata Discovery API for failed cities ───
    print(f"\n\n{'=' * 70}")
    print("PHASE 2: Socrata Discovery API search for failed cities")
    print(f"{'=' * 70}")

    failed_with_socrata = [f for f in failures if f["city_key"] in SOCRATA_DOMAINS]
    print(f"  {len(failed_with_socrata)} failed cities have known Socrata domains\n")

    for city_info in failed_with_socrata:
        city_key = city_info["city_key"]
        domain = SOCRATA_DOMAINS[city_key]["domain"]
        name = city_info["name"]
        state = city_info["state"]
        print(f"\n  Searching {domain} for permit datasets...")

        datasets, error = search_socrata_catalog(domain, "building permit")
        if error:
            print(f"    ❌ Catalog search failed: {error}")
            # Try broader search
            datasets, error = search_socrata_catalog(domain, "permit")
            if error:
                print(f"    ❌ Broader search also failed: {error}")
                continue

        if not datasets:
            print(f"    ⚠️  No permit datasets found on {domain}")
            continue

        print(f"    Found {len(datasets)} candidate datasets:")
        for ds in datasets:
            print(f"      • {ds['name']} ({ds['dataset_id']}) — {len(ds['columns'])} cols")

        # Test the top dataset
        for ds in datasets[:3]:
            test_url = f"https://{domain}/resource/{ds['dataset_id']}.json"
            print(f"    Testing {ds['dataset_id']}...")
            fields, sample, error = test_socrata(test_url)
            if error:
                print(f"      ❌ {error}")
            else:
                print(f"      ✅ SUCCESS — {len(fields)} fields")
                successes.append({
                    "city_key": city_key, "name": name, "state": state,
                    "platform": "socrata", "endpoint": test_url,
                    "fields": fields, "sample": sample,
                    "dataset_name": ds["name"],
                })
                # Remove from failures
                failures = [f for f in failures if f["city_key"] != city_key]
                break

            time.sleep(0.2)

    # ─── SUMMARY ───
    print(f"\n\n{'=' * 70}")
    print("DISCOVERY SUMMARY")
    print(f"{'=' * 70}")
    print(f"\n✅ Working endpoints found: {len(successes)}")
    for s in successes:
        print(f"   • {s['name']}, {s['state']} — {s['platform']} — {len(s['fields'])} fields")
    print(f"\n❌ No working endpoint: {len(failures)}")
    for f in failures:
        print(f"   • {f['name']}, {f['state']}")

    # ─── SAVE RESULTS ───
    with open("discovery_results.json", "w") as fp:
        json.dump({"successes": successes, "failures": failures}, fp, indent=2, default=str)
    print(f"\nResults saved to discovery_results.json")

    # ─── PRINT CONFIG ENTRIES ───
    if successes:
        print(f"\n\n{'=' * 70}")
        print("SUGGESTED city_configs.py ENTRIES")
        print("(Copy these into city_configs.py, review field mappings)")
        print(f"{'=' * 70}")
        for s in successes:
            print_config_entry(
                s["city_key"], s["name"], s["state"],
                s["platform"], s["endpoint"],
                s["fields"], s["sample"],
            )

    return len(successes), len(failures)


if __name__ == "__main__":
    found, missed = main()
    sys.exit(0 if found > 0 else 1)
