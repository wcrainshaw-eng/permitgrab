#!/usr/bin/env python3
"""
PermitGrab City Discovery Script
V12.7 - Automated discovery of building permit API endpoints

Sweeps Socrata Discovery API, ArcGIS Hub, and CKAN portals to find
real building permit datasets across US cities.

Usage:
    python scripts/discover_cities.py                     # full discovery sweep
    python scripts/discover_cities.py --socrata-only      # just Socrata
    python scripts/discover_cities.py --arcgis-only       # just ArcGIS
    python scripts/discover_cities.py --ckan-only         # just CKAN
    python scripts/discover_cities.py --verify-existing   # check current configs
    python scripts/discover_cities.py --output FILE       # write JSON results
    python scripts/discover_cities.py --generate-configs  # output Python dict entries
"""

import requests
import json
import os
import re
import sys
import time
import argparse
from datetime import datetime
from typing import Dict, List, Tuple, Optional, Any

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Top 300 US cities with their states for lookup
CITY_STATE_LOOKUP = {
    "new york": "NY", "los angeles": "CA", "chicago": "IL", "houston": "TX",
    "phoenix": "AZ", "philadelphia": "PA", "san antonio": "TX", "san diego": "CA",
    "dallas": "TX", "san jose": "CA", "austin": "TX", "jacksonville": "FL",
    "fort worth": "TX", "columbus": "OH", "charlotte": "NC", "san francisco": "CA",
    "indianapolis": "IN", "seattle": "WA", "denver": "CO", "washington": "DC",
    "boston": "MA", "el paso": "TX", "nashville": "TN", "detroit": "MI",
    "oklahoma city": "OK", "portland": "OR", "las vegas": "NV", "memphis": "TN",
    "louisville": "KY", "baltimore": "MD", "milwaukee": "WI", "albuquerque": "NM",
    "tucson": "AZ", "fresno": "CA", "mesa": "AZ", "sacramento": "CA",
    "atlanta": "GA", "kansas city": "MO", "colorado springs": "CO", "miami": "FL",
    "raleigh": "NC", "omaha": "NE", "long beach": "CA", "virginia beach": "VA",
    "oakland": "CA", "minneapolis": "MN", "tulsa": "OK", "tampa": "FL",
    "arlington": "TX", "new orleans": "LA", "wichita": "KS", "cleveland": "OH",
    "bakersfield": "CA", "aurora": "CO", "anaheim": "CA", "honolulu": "HI",
    "santa ana": "CA", "riverside": "CA", "corpus christi": "TX", "lexington": "KY",
    "stockton": "CA", "henderson": "NV", "saint paul": "MN", "st paul": "MN",
    "st. paul": "MN", "cincinnati": "OH", "pittsburgh": "PA", "greensboro": "NC",
    "anchorage": "AK", "plano": "TX", "lincoln": "NE", "orlando": "FL",
    "irvine": "CA", "newark": "NJ", "toledo": "OH", "durham": "NC",
    "chula vista": "CA", "fort wayne": "IN", "jersey city": "NJ", "st. petersburg": "FL",
    "st petersburg": "FL", "laredo": "TX", "madison": "WI", "chandler": "AZ",
    "buffalo": "NY", "lubbock": "TX", "scottsdale": "AZ", "reno": "NV",
    "glendale": "AZ", "gilbert": "AZ", "winston-salem": "NC", "winston salem": "NC",
    "north las vegas": "NV", "norfolk": "VA", "chesapeake": "VA", "garland": "TX",
    "irving": "TX", "hialeah": "FL", "fremont": "CA", "boise": "ID",
    "richmond": "VA", "baton rouge": "LA", "spokane": "WA", "des moines": "IA",
    "tacoma": "WA", "san bernardino": "CA", "modesto": "CA", "fontana": "CA",
    "santa clarita": "CA", "birmingham": "AL", "oxnard": "CA", "fayetteville": "NC",
    "moreno valley": "CA", "rochester": "NY", "glendale": "CA", "huntington beach": "CA",
    "salt lake city": "UT", "grand rapids": "MI", "amarillo": "TX", "yonkers": "NY",
    "aurora": "IL", "montgomery": "AL", "akron": "OH", "little rock": "AR",
    "huntsville": "AL", "augusta": "GA", "port st. lucie": "FL", "grand prairie": "TX",
    "columbus": "GA", "tallahassee": "FL", "overland park": "KS", "tempe": "AZ",
    "mckinney": "TX", "mobile": "AL", "cape coral": "FL", "shreveport": "LA",
    "frisco": "TX", "knoxville": "TN", "worcester": "MA", "brownsville": "TX",
    "vancouver": "WA", "fort lauderdale": "FL", "sioux falls": "SD", "ontario": "CA",
    "chattanooga": "TN", "providence": "RI", "newport news": "VA", "rancho cucamonga": "CA",
    "santa rosa": "CA", "oceanside": "CA", "salem": "OR", "elk grove": "CA",
    "garden grove": "CA", "pembroke pines": "FL", "peoria": "AZ", "eugene": "OR",
    "corona": "CA", "cary": "NC", "springfield": "MO", "fort collins": "CO",
    "jackson": "MS", "alexandria": "VA", "hayward": "CA", "lancaster": "CA",
    "lakewood": "CO", "clarksville": "TN", "palmdale": "CA", "salinas": "CA",
    "springfield": "MA", "hollywood": "FL", "pasadena": "TX", "sunnyvale": "CA",
    "macon": "GA", "kansas city": "KS", "pomona": "CA", "escondido": "CA",
    "killeen": "TX", "naperville": "IL", "joliet": "IL", "bellevue": "WA",
    "rockford": "IL", "savannah": "GA", "paterson": "NJ", "torrance": "CA",
    "bridgeport": "CT", "mcallen": "TX", "mesquite": "TX", "syracuse": "NY",
    "midland": "TX", "pasadena": "CA", "murfreesboro": "TN", "miramar": "FL",
    "dayton": "OH", "fullerton": "CA", "olathe": "KS", "orange": "CA",
    "thornton": "CO", "roseville": "CA", "denton": "TX", "waco": "TX",
    "surprise": "AZ", "carrollton": "TX", "west valley city": "UT", "charleston": "SC",
    "warren": "MI", "hampton": "VA", "gainesville": "FL", "visalia": "CA",
    "coral springs": "FL", "columbia": "SC", "cedar rapids": "IA", "sterling heights": "MI",
    "new haven": "CT", "stamford": "CT", "concord": "CA", "kent": "WA",
    "santa clara": "CA", "elizabeth": "NJ", "round rock": "TX", "thousand oaks": "CA",
    "lafayette": "LA", "athens": "GA", "topeka": "KS", "simi valley": "CA",
    "fargo": "ND", "norman": "OK", "columbia": "MO", "abilene": "TX",
    "wilmington": "NC", "hartford": "CT", "victorville": "CA", "pearland": "TX",
    "vallejo": "CA", "ann arbor": "MI", "berkeley": "CA", "allentown": "PA",
    "richardson": "TX", "odessa": "TX", "arvada": "CO", "cambridge": "MA",
    "sugar land": "TX", "beaumont": "TX", "lansing": "MI", "evansville": "IN",
    "rochester": "MN", "independence": "MO", "fairfield": "CA", "provo": "UT",
    "clearwater": "FL", "college station": "TX", "west jordan": "UT", "carlsbad": "CA",
    "el monte": "CA", "murrieta": "CA", "temecula": "CA", "springfield": "IL",
    "palm bay": "FL", "costa mesa": "CA", "westminster": "CO", "north charleston": "SC",
    "miami gardens": "FL", "manchester": "NH", "high point": "NC", "downey": "CA",
    "clovis": "CA", "pompano beach": "FL", "pueblo": "CO", "elgin": "IL",
    "lowell": "MA", "antioch": "CA", "west palm beach": "FL", "peoria": "IL",
    "everett": "WA", "ventura": "CA", "centennial": "CO", "lakeland": "FL",
    "gresham": "OR", "richmond": "CA", "billings": "MT", "inglewood": "CA",
    "broken arrow": "OK", "sandy springs": "GA", "jurupa valley": "CA", "hillsboro": "OR",
    "waterbury": "CT", "santa maria": "CA", "boulder": "CO", "greeley": "CO",
    "daly city": "CA", "meridian": "ID", "lewisville": "TX", "davie": "FL",
    "west covina": "CA", "league city": "TX", "tyler": "TX", "norwalk": "CA",
    "san mateo": "CA", "green bay": "WI", "wichita falls": "TX", "sparks": "NV",
    "lakewood": "NJ", "burbank": "CA", "rialto": "CA", "allen": "TX",
    "el cajon": "CA", "las cruces": "NM", "renton": "WA", "davenport": "IA",
    "south bend": "IN", "vista": "CA", "tuscaloosa": "AL", "clinton": "MI",
    "edison": "NJ", "woodbridge": "NJ", "san angelo": "TX", "kenosha": "WI",
    "vacaville": "CA",
}


class CityDiscovery:
    """Discover building permit API endpoints across platforms."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "PermitGrab/1.0 Discovery",
            "Accept": "application/json"
        })
        self.results = []
        self.socrata_results = []
        self.arcgis_results = []
        self.ckan_results = []
        self.county_results = []
        self.seen_datasets = set()  # (domain, dataset_id) pairs
        self.existing_cities = {}   # Loaded from city_configs.py

    def load_existing_configs(self):
        """Load existing CITY_REGISTRY for cross-referencing."""
        try:
            from city_configs import CITY_REGISTRY
            self.existing_cities = CITY_REGISTRY
            print(f"[Discovery] Loaded {len(CITY_REGISTRY)} existing city configs")
        except ImportError:
            print("[Discovery] Warning: Could not load city_configs.py")
            self.existing_cities = {}

    def sweep_socrata(self):
        """Sweep Socrata Discovery API for building permit datasets."""
        print("\n" + "=" * 60)
        print("SOCRATA DISCOVERY SWEEP")
        print("=" * 60)

        search_queries = [
            "building permits",
            "construction permits",
            "building permit applications",
            "permits issued",
            "building permits issued",
            "permit issuance"
        ]

        all_datasets = []

        for query in search_queries:
            print(f"\n[Socrata] Searching: '{query}'")
            datasets = self._socrata_search(query)
            all_datasets.extend(datasets)
            print(f"  Found {len(datasets)} datasets")

        # Deduplicate
        unique_datasets = []
        for ds in all_datasets:
            key = (ds["domain"], ds["dataset_id"])
            if key not in self.seen_datasets:
                self.seen_datasets.add(key)
                unique_datasets.append(ds)

        print(f"\n[Socrata] Total unique datasets: {len(unique_datasets)}")

        # Filter and verify
        filtered = self._filter_socrata_datasets(unique_datasets)
        print(f"[Socrata] After filtering: {len(filtered)}")

        verified = self._verify_socrata_datasets(filtered)
        print(f"[Socrata] Verified with permit data: {len(verified)}")

        self.socrata_results = verified
        self.results.extend(verified)
        return verified

    def _socrata_search(self, query: str) -> List[Dict]:
        """Search Socrata Discovery API for datasets matching query."""
        datasets = []
        offset = 0
        limit = 100

        while True:
            url = "https://api.us.socrata.com/api/catalog/v1"
            params = {
                "q": query,
                "only": "datasets",
                "limit": limit,
                "offset": offset,
                "order": "relevance"
            }

            try:
                resp = self.session.get(url, params=params, timeout=30)
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                print(f"  [Error] Socrata API: {e}")
                break

            results = data.get("results", [])
            if not results:
                break

            for r in results:
                resource = r.get("resource", {})
                metadata = r.get("metadata", {})

                ds = {
                    "domain": metadata.get("domain", ""),
                    "dataset_id": resource.get("id", ""),
                    "name": resource.get("name", ""),
                    "description": resource.get("description", ""),
                    "columns": resource.get("columns_field_name", []),
                    "updated_at": resource.get("updatedAt", ""),
                    "page_views": resource.get("page_views", {}).get("page_views_total", 0),
                    "resource_type": resource.get("type", ""),
                }
                datasets.append(ds)

            offset += limit
            if offset >= data.get("resultSetSize", 0):
                break

            time.sleep(0.3)  # Rate limit

        return datasets

    def _filter_socrata_datasets(self, datasets: List[Dict]) -> List[Dict]:
        """Filter out non-US and invalid datasets."""
        filtered = []

        for ds in datasets:
            domain = ds["domain"]

            # Skip non-US domains
            if any(domain.endswith(ext) for ext in [".ca", ".au", ".uk", ".nz"]):
                continue

            # Skip internal domains
            if domain.startswith("internal-"):
                continue

            # Skip if not a dataset
            if ds["resource_type"] != "dataset":
                continue

            # Skip if no page views and stale (likely abandoned)
            if ds["page_views"] == 0 and not ds["updated_at"]:
                continue

            filtered.append(ds)

        return filtered

    def _verify_socrata_datasets(self, datasets: List[Dict]) -> List[Dict]:
        """Test each dataset endpoint and verify it has permit data."""
        verified = []

        for i, ds in enumerate(datasets):
            print(f"  [{i+1}/{len(datasets)}] Testing {ds['domain']}/{ds['dataset_id'][:8]}...", end=" ")

            url = f"https://{ds['domain']}/resource/{ds['dataset_id']}.json?$limit=2"

            try:
                resp = self.session.get(url, timeout=15)
                resp.raise_for_status()
                data = resp.json()

                if not isinstance(data, list) or len(data) == 0:
                    print("empty")
                    continue

                # Auto-map fields
                sample = data[0]
                field_map, date_field = auto_map_fields(sample, "socrata")

                # Check for at least 2 key fields
                key_fields = ["permit_number", "address"]
                found = sum(1 for f in key_fields if f in field_map)
                has_date = date_field is not None

                if found >= 1 and has_date:
                    print(f"VERIFIED ({found+1} fields)")
                    result = {
                        "city_name": None,  # Will be detected
                        "state": None,
                        "slug": None,
                        "platform": "socrata",
                        "endpoint": f"https://{ds['domain']}/resource/{ds['dataset_id']}.json",
                        "dataset_id": ds["dataset_id"],
                        "dataset_name": ds["name"],
                        "field_map": field_map,
                        "date_field": date_field,
                        "sample_fields": list(sample.keys()),
                        "record_count_sample": len(data),
                        "last_updated": ds["updated_at"],
                        "verified": True,
                        "is_new": True,
                        "source": "socrata_discovery",
                        "domain": ds["domain"],
                        "page_views": ds["page_views"],
                    }

                    # Detect city/state
                    city, state, slug = detect_city_state(ds["domain"], ds["name"], ds["description"])
                    result["city_name"] = city
                    result["state"] = state
                    result["slug"] = slug

                    verified.append(result)
                else:
                    print(f"insufficient fields ({found})")

            except Exception as e:
                print(f"error: {e}")

            time.sleep(0.5)  # Rate limit

        return verified

    def sweep_arcgis(self):
        """Sweep ArcGIS Hub for building permit datasets."""
        print("\n" + "=" * 60)
        print("ARCGIS HUB DISCOVERY SWEEP")
        print("=" * 60)

        verified = []

        # Known ArcGIS hubs from spec
        known_hubs = [
            ("Durham", "NC", "live-durhamnc.opendata.arcgis.com", "all-building-permits"),
            ("Las Vegas", "NV", "opendataportal-lasvegas.opendata.arcgis.com", "building-permits"),
            ("Denver", "CO", "opendata-geospatialdenver.hub.arcgis.com", "building-permits"),
            ("Columbus", "OH", "data-columbus.opendata.arcgis.com", "building-permits"),
            ("Sacramento", "CA", "data-saccity.opendata.arcgis.com", "building-permits"),
            ("Colorado Springs", "CO", "data-cos-gis.opendata.arcgis.com", "building-permits"),
            ("St. Paul", "MN", "information-stpaul.hub.arcgis.com", "building-permits"),
            ("Charlotte", "NC", "data.charlottenc.gov", "building-permits"),
            ("Cape Coral", "FL", "capecoral-capegis.opendata.arcgis.com", "building-permits"),
            ("Carlsbad", "CA", "open-data-carlsbad.hub.arcgis.com", "building-permits"),
        ]

        # Try ArcGIS Online search
        print("\n[ArcGIS] Searching ArcGIS Online...")
        search_results = self._arcgis_online_search("building permits")
        print(f"  Found {len(search_results)} potential datasets")

        # Try known hubs
        print("\n[ArcGIS] Testing known hubs...")
        for city, state, hub, dataset_slug in known_hubs:
            print(f"  Testing {city}, {state} ({hub})...")
            result = self._test_arcgis_hub(hub, dataset_slug, city, state)
            if result:
                verified.append(result)
                print(f"    VERIFIED")
            else:
                print(f"    not found")

        # Test search results
        print(f"\n[ArcGIS] Testing search results...")
        for i, sr in enumerate(search_results[:50]):  # Limit to first 50
            print(f"  [{i+1}/{min(50, len(search_results))}] Testing {sr.get('title', 'unknown')[:40]}...", end=" ")
            result = self._test_arcgis_service(sr)
            if result:
                verified.append(result)
                print("VERIFIED")
            else:
                print("skip")
            time.sleep(1)

        self.arcgis_results = verified
        self.results.extend(verified)
        print(f"\n[ArcGIS] Total verified: {len(verified)}")
        return verified

    def _arcgis_online_search(self, query: str) -> List[Dict]:
        """Search ArcGIS Online for Feature Services."""
        results = []
        url = "https://www.arcgis.com/sharing/rest/search"

        params = {
            "q": f'"{query}" AND type:"Feature Service"',
            "num": 100,
            "start": 1,
            "f": "json",
            "sortField": "numViews",
            "sortOrder": "desc"
        }

        try:
            resp = self.session.get(url, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            results = data.get("results", [])
        except Exception as e:
            print(f"  [Error] ArcGIS Online search: {e}")

        return results

    def _test_arcgis_hub(self, hub: str, dataset_slug: str, city: str, state: str) -> Optional[Dict]:
        """Test an ArcGIS Hub dataset for building permit data."""
        # Try to get the GeoService URL from the hub API
        api_url = f"https://{hub}/api/v3/datasets/{dataset_slug}"

        try:
            resp = self.session.get(api_url, timeout=15)
            if resp.status_code != 200:
                return None
            data = resp.json()

            # Look for the service URL
            service_url = data.get("data", {}).get("attributes", {}).get("url")
            if not service_url:
                return None

            # Test the service
            return self._test_feature_service(service_url, city, state, hub)

        except Exception as e:
            return None

    def _test_arcgis_service(self, search_result: Dict) -> Optional[Dict]:
        """Test an ArcGIS search result for building permit data."""
        url = search_result.get("url")
        if not url:
            return None

        # Make sure it ends with FeatureServer or MapServer
        if "FeatureServer" not in url and "MapServer" not in url:
            return None

        # Try to detect city/state from title
        title = search_result.get("title", "")
        city, state, _ = detect_city_state("", title, search_result.get("description", ""))

        return self._test_feature_service(url, city, state, "arcgis_online")

    def _test_feature_service(self, service_url: str, city: str, state: str, source: str) -> Optional[Dict]:
        """Test an ArcGIS Feature Service URL."""
        # Ensure URL ends with layer index
        if not service_url.endswith("/0") and not service_url.endswith("/1"):
            service_url = service_url.rstrip("/") + "/0"

        query_url = f"{service_url}/query"
        params = {
            "f": "json",
            "where": "1=1",
            "resultRecordCount": 2,
            "outFields": "*",
            "returnGeometry": "false"
        }

        try:
            resp = self.session.get(query_url, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()

            # Check for error
            if "error" in data:
                return None

            features = data.get("features", [])
            if not features:
                return None

            # Get sample record
            sample = features[0].get("attributes", {})
            if not sample:
                return None

            # Auto-map fields
            field_map, date_field = auto_map_fields(sample, "arcgis")

            # Need at least permit_number or address, plus a date
            key_fields = ["permit_number", "address"]
            found = sum(1 for f in key_fields if f in field_map)
            has_date = date_field is not None

            if found >= 1 and has_date:
                slug = city.lower().replace(" ", "-").replace(".", "") if city else "unknown"
                return {
                    "city_name": city,
                    "state": state,
                    "slug": slug,
                    "platform": "arcgis",
                    "endpoint": service_url,
                    "dataset_id": service_url.split("/")[-2] if "/" in service_url else "",
                    "dataset_name": f"Building Permits - {city}",
                    "field_map": field_map,
                    "date_field": date_field,
                    "sample_fields": list(sample.keys()),
                    "record_count_sample": len(features),
                    "last_updated": "",
                    "verified": True,
                    "is_new": True,
                    "source": f"arcgis_{source}",
                }

        except Exception as e:
            return None

        return None

    def sweep_ckan(self):
        """Sweep known CKAN portals for building permit datasets."""
        print("\n" + "=" * 60)
        print("CKAN PORTAL DISCOVERY SWEEP")
        print("=" * 60)

        verified = []

        # Known CKAN portals to check
        ckan_portals = [
            ("data.boston.gov", "Boston", "MA"),
            ("data.sanjoseca.gov", "San Jose", "CA"),
            ("www.phoenixopendata.com", "Phoenix", "AZ"),
            ("opendata.minneapolismn.gov", "Minneapolis", "MN"),
            ("data.louisvilleky.gov", "Louisville", "KY"),
        ]

        for portal, city, state in ckan_portals:
            print(f"\n[CKAN] Searching {portal} ({city}, {state})...")
            result = self._search_ckan_portal(portal, city, state)
            if result:
                verified.append(result)
                print(f"  VERIFIED")
            else:
                print(f"  not found")

        self.ckan_results = verified
        self.results.extend(verified)
        print(f"\n[CKAN] Total verified: {len(verified)}")
        return verified

    def _search_ckan_portal(self, portal: str, city: str, state: str) -> Optional[Dict]:
        """Search a CKAN portal for building permit data."""
        url = f"https://{portal}/api/3/action/package_search"
        params = {"q": "building permits", "rows": 100}

        try:
            resp = self.session.get(url, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()

            if not data.get("success"):
                return None

            results = data.get("result", {}).get("results", [])
            if not results:
                return None

            # Find a dataset with a datastore resource
            for pkg in results:
                resources = pkg.get("resources", [])
                for res in resources:
                    # Look for datastore-enabled resources
                    if res.get("datastore_active") or res.get("format", "").upper() in ["CSV", "JSON"]:
                        resource_id = res.get("id")
                        if not resource_id:
                            continue

                        # Test the datastore
                        test_url = f"https://{portal}/api/3/action/datastore_search"
                        test_params = {"resource_id": resource_id, "limit": 2}

                        try:
                            test_resp = self.session.get(test_url, params=test_params, timeout=15)
                            test_data = test_resp.json()

                            if test_data.get("success"):
                                records = test_data.get("result", {}).get("records", [])
                                if records:
                                    sample = records[0]
                                    field_map, date_field = auto_map_fields(sample, "ckan")

                                    key_fields = ["permit_number", "address"]
                                    found = sum(1 for f in key_fields if f in field_map)

                                    if found >= 1:
                                        slug = city.lower().replace(" ", "-")
                                        return {
                                            "city_name": city,
                                            "state": state,
                                            "slug": slug,
                                            "platform": "ckan",
                                            "endpoint": f"https://{portal}/api/3/action/datastore_search",
                                            "dataset_id": resource_id,
                                            "dataset_name": pkg.get("title", f"Building Permits - {city}"),
                                            "field_map": field_map,
                                            "date_field": date_field,
                                            "sample_fields": list(sample.keys()),
                                            "record_count_sample": len(records),
                                            "last_updated": pkg.get("metadata_modified", ""),
                                            "verified": True,
                                            "is_new": True,
                                            "source": "ckan_search",
                                        }
                        except:
                            continue

        except Exception as e:
            print(f"  [Error] CKAN {portal}: {e}")

        return None

    def sweep_counties(self):
        """Find county/state datasets that can be split by city."""
        print("\n" + "=" * 60)
        print("COUNTY/STATE DATASET SWEEP")
        print("=" * 60)

        # Search for county-level datasets
        county_queries = [
            "county building permits",
            "county construction permits",
        ]

        all_datasets = []
        for query in county_queries:
            print(f"\n[County] Searching: '{query}'")
            datasets = self._socrata_search(query)
            all_datasets.extend(datasets)

        # Deduplicate
        unique = []
        for ds in all_datasets:
            key = (ds["domain"], ds["dataset_id"])
            if key not in self.seen_datasets:
                self.seen_datasets.add(key)
                unique.append(ds)

        print(f"[County] Found {len(unique)} unique county datasets")

        # Verify and extract city fields
        verified = []
        for ds in unique[:20]:  # Limit to first 20
            print(f"  Testing {ds['domain']}/{ds['dataset_id'][:8]}...", end=" ")
            result = self._analyze_county_dataset(ds)
            if result:
                verified.append(result)
                print(f"VERIFIED ({len(result.get('cities_found', []))} cities)")
            else:
                print("skip")
            time.sleep(0.5)

        self.county_results = verified
        print(f"\n[County] Total county datasets with city filtering: {len(verified)}")
        return verified

    def _analyze_county_dataset(self, ds: Dict) -> Optional[Dict]:
        """Analyze a county dataset to find city field and list cities."""
        url = f"https://{ds['domain']}/resource/{ds['dataset_id']}.json?$limit=100"

        try:
            resp = self.session.get(url, timeout=15)
            resp.raise_for_status()
            data = resp.json()

            if not data:
                return None

            sample = data[0]

            # Look for city/municipality field
            city_field = None
            city_patterns = ["city", "municipality", "town", "jurisdiction", "place", "community", "location_city"]

            for key in sample.keys():
                kl = key.lower()
                if any(p in kl for p in city_patterns):
                    city_field = key
                    break

            if not city_field:
                return None

            # Get unique cities
            cities = set()
            for record in data:
                city_val = record.get(city_field)
                if city_val and isinstance(city_val, str):
                    cities.add(city_val)

            if len(cities) < 2:
                return None

            field_map, date_field = auto_map_fields(sample, "socrata")

            return {
                "platform": "socrata",
                "endpoint": f"https://{ds['domain']}/resource/{ds['dataset_id']}.json",
                "dataset_id": ds["dataset_id"],
                "dataset_name": ds["name"],
                "domain": ds["domain"],
                "city_field": city_field,
                "cities_found": list(cities)[:50],  # Limit to 50
                "field_map": field_map,
                "date_field": date_field,
                "source": "county_sweep",
            }

        except Exception as e:
            return None

    def verify_existing(self):
        """Verify all existing city configs still work."""
        print("\n" + "=" * 60)
        print("VERIFYING EXISTING CITY CONFIGS")
        print("=" * 60)

        self.load_existing_configs()
        results = {"working": [], "broken": [], "deactivated": 0}

        for slug, config in self.existing_cities.items():
            if not config.get("active", False):
                results["deactivated"] += 1
                continue

            print(f"  Testing {slug}...", end=" ")

            platform = config.get("platform", "socrata")
            endpoint = config.get("endpoint", "")

            try:
                if platform == "socrata":
                    url = f"{endpoint}?$limit=1"
                    resp = self.session.get(url, timeout=15)
                elif platform == "arcgis":
                    url = f"{endpoint}/query?f=json&where=1=1&resultRecordCount=1&outFields=*&returnGeometry=false"
                    resp = self.session.get(url, timeout=15)
                elif platform == "ckan":
                    dataset_id = config.get("dataset_id", "")
                    base = endpoint.replace("/api/3/action/datastore_search", "")
                    url = f"{base}/api/3/action/datastore_search?resource_id={dataset_id}&limit=1"
                    resp = self.session.get(url, timeout=15)
                else:
                    print("unknown platform")
                    continue

                resp.raise_for_status()
                data = resp.json()

                # Check for actual data
                if platform == "socrata" and isinstance(data, list) and len(data) > 0:
                    print("OK")
                    results["working"].append(slug)
                elif platform == "arcgis" and data.get("features"):
                    print("OK")
                    results["working"].append(slug)
                elif platform == "ckan" and data.get("success") and data.get("result", {}).get("records"):
                    print("OK")
                    results["working"].append(slug)
                else:
                    print("empty")
                    results["broken"].append(slug)

            except Exception as e:
                print(f"error: {e}")
                results["broken"].append(slug)

            time.sleep(0.5)

        print(f"\n[Verify] Working: {len(results['working'])}")
        print(f"[Verify] Broken: {len(results['broken'])}")
        print(f"[Verify] Deactivated: {results['deactivated']}")

        return results

    def cross_reference(self):
        """Check which discovered datasets are new vs existing."""
        self.load_existing_configs()

        for result in self.results:
            slug = result.get("slug", "")
            if slug and slug.replace("-", "_") in self.existing_cities:
                result["is_new"] = False
            else:
                result["is_new"] = True

    def run_all(self):
        """Run full discovery sweep across all platforms."""
        print("\n" + "#" * 60)
        print("PERMITGRAB V12.7 CITY DISCOVERY")
        print(f"Started: {datetime.now().isoformat()}")
        print("#" * 60)

        self.load_existing_configs()

        self.sweep_socrata()
        self.sweep_arcgis()
        self.sweep_ckan()
        self.sweep_counties()

        self.cross_reference()

        # Summary
        print("\n" + "=" * 60)
        print("DISCOVERY SUMMARY")
        print("=" * 60)
        new_count = sum(1 for r in self.results if r.get("is_new"))
        existing_count = len(self.results) - new_count

        print(f"  Socrata: {len(self.socrata_results)}")
        print(f"  ArcGIS: {len(self.arcgis_results)}")
        print(f"  CKAN: {len(self.ckan_results)}")
        print(f"  County datasets: {len(self.county_results)}")
        print(f"  TOTAL VERIFIED: {len(self.results)}")
        print(f"  New cities: {new_count}")
        print(f"  Existing matches: {existing_count}")

        return self.results

    def output_report(self, filepath: str):
        """Write discovery results to JSON file."""
        report = {
            "run_date": datetime.now().isoformat(),
            "summary": {
                "socrata_found": len(self.socrata_results),
                "arcgis_found": len(self.arcgis_results),
                "ckan_found": len(self.ckan_results),
                "county_datasets": len(self.county_results),
                "total_verified": len(self.results),
                "new_cities": sum(1 for r in self.results if r.get("is_new")),
                "existing_match": sum(1 for r in self.results if not r.get("is_new")),
            },
            "results": self.results,
            "county_datasets": self.county_results,
        }

        with open(filepath, "w") as f:
            json.dump(report, f, indent=2)

        print(f"\n[Output] Report written to {filepath}")

    def output_configs(self, filepath: str = None):
        """Generate Python config entries for discovered cities."""
        lines = [
            f"# Auto-generated by discover_cities.py on {datetime.now().strftime('%Y-%m-%d')}",
            "# Paste these into city_configs.py CITY_REGISTRY dict",
            "",
        ]

        for result in self.results:
            if not result.get("is_new"):
                continue

            slug = result.get("slug", "unknown")
            key = slug.replace("-", "_")

            entry = f'''    "{key}": {{
        "name": "{result.get('city_name', 'Unknown')}",
        "state": "{result.get('state', 'XX')}",
        "slug": "{slug}",
        "platform": "{result.get('platform', 'socrata')}",
        "endpoint": "{result.get('endpoint', '')}",
        "dataset_id": "{result.get('dataset_id', '')}",
        "description": "{result.get('dataset_name', '')[:50]}",
        "field_map": {json.dumps(result.get('field_map', {}))},
        "date_field": "{result.get('date_field', '')}",
        "date_format": "iso",
        "limit": 2000,
        "active": True,
        "notes": "V12.7 - Auto-discovered via {result.get('source', 'discovery')}"
    }},
'''
            lines.append(entry)

        output = "\n".join(lines)

        if filepath:
            with open(filepath, "w") as f:
                f.write(output)
            print(f"\n[Output] Configs written to {filepath}")
        else:
            print(output)


def auto_map_fields(sample_record: Dict, platform: str = "socrata") -> Tuple[Dict, Optional[str]]:
    """Detect field mappings from a sample permit record.

    Returns dict mapping our standard fields to source API fields.
    Standard fields: permit_number, permit_type, address, street,
                     description, estimated_cost, owner_name
    Also returns the date_field separately.
    """
    field_map = {}
    date_field = None
    keys = list(sample_record.keys())

    for key in keys:
        kl = key.lower().replace(" ", "_")

        # --- Permit Number ---
        if not field_map.get("permit_number"):
            if any(p in kl for p in [
                "permit_number", "permit_no", "permitno", "permit_num",
                "permit_id", "permitid", "application_number",
                "application_no", "case_number", "job_filing", "permno",
                "record_id", "folderrsn", "permit_"
            ]):
                # Avoid matching "permit_type" or "permit_status"
                if not any(x in kl for x in ["type", "status", "desc", "class", "date"]):
                    field_map["permit_number"] = key

        # --- Permit Type ---
        if not field_map.get("permit_type"):
            if any(p in kl for p in [
                "permit_type", "permittype", "work_type", "worktype",
                "permit_kind", "type_of_work", "permit_category"
            ]):
                field_map["permit_type"] = key

        # --- Address ---
        if not field_map.get("address"):
            if any(p in kl for p in [
                "address", "location", "site_addr", "property_addr",
                "full_addr", "street_address", "project_address",
                "permit_address", "originaladdress", "property_location"
            ]):
                field_map["address"] = key

        # --- Street (separate from address in some APIs) ---
        if not field_map.get("street"):
            if any(p in kl for p in [
                "street_name", "streetname", "street"
            ]) and "address" not in kl:
                field_map["street"] = key

        # --- Description ---
        if not field_map.get("description"):
            if any(p in kl for p in [
                "description", "work_desc", "job_description",
                "scope_of_work", "work_description", "project_description"
            ]):
                field_map["description"] = key

        # --- Estimated Cost ---
        if not field_map.get("estimated_cost"):
            if any(p in kl for p in [
                "estimated_cost", "est_cost", "project_value", "job_value",
                "total_cost", "construction_cost", "valuation",
                "estimated_job_cost", "costwork", "estprojectcost",
                "declared_valuation", "value"
            ]):
                field_map["estimated_cost"] = key

        # --- Owner / Applicant ---
        if not field_map.get("owner_name"):
            if any(p in kl for p in [
                "owner_name", "ownername", "property_owner", "applicant",
                "applicant_name", "contractor", "contractor_name",
                "company_name", "companyname"
            ]):
                field_map["owner_name"] = key

        # --- Date (prefer issue date) ---
        if any(p in kl for p in [
            "issue_date", "issued_date", "issueddate", "issuedate",
            "date_issued", "permit_date", "permitdate",
            "permitissuedate", "permitissueddate"
        ]):
            date_field = key  # Always overwrite — these are the best dates
        elif not date_field and any(p in kl for p in [
            "processed_date", "approved_date", "applied_date",
            "application_date", "filed_date", "filing_date",
            "status_date", "created_date"
        ]):
            date_field = key  # Fallback date field
        elif not date_field and kl in ["date"]:
            date_field = key  # Last resort

    return field_map, date_field


def detect_city_state(domain: str, name: str, description: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Extract city name and state from domain/dataset metadata.

    Returns (city_name, state_code, slug) tuple.
    """
    city = None
    state = None

    # Parse domain for city names
    # Pattern: data.cityof{city}.org
    match = re.search(r'data\.cityof(\w+)\.', domain)
    if match:
        city = match.group(1).title()

    # Pattern: data.{city}.gov or data.{city}tx.gov etc
    if not city:
        match = re.search(r'data\.(\w+)\.gov', domain)
        if match:
            city_candidate = match.group(1).lower()
            # Remove state suffix if present
            city_candidate = re.sub(r'(tx|ca|ny|fl|il|pa|oh|ga|nc|mi|nj|va|wa|az|ma|tn|in|mo|md|wi|mn|co|al|sc|la|ky|or|ok|ct|ut|ia|nv|ar|ms|ks|nm|ne|wv|id|hi|nh|me|mt|ri|de|sd|nd|ak|vt|wy)$', '', city_candidate)
            if len(city_candidate) > 2:
                city = city_candidate.replace("cityof", "").title()

    # Pattern: data-{city}.opendata.arcgis.com
    if not city:
        match = re.search(r'data-?(\w+)\.opendata\.arcgis\.com', domain)
        if match:
            city = match.group(1).replace("-", " ").title()

    # Pattern: {city}-data.opendata.arcgis.com
    if not city:
        match = re.search(r'(\w+)-data\.', domain)
        if match:
            city = match.group(1).title()

    # Try to extract from name/description
    if not city:
        # Common patterns in dataset names
        for pattern in [
            r'^(\w+(?:\s+\w+)?)\s+building\s+permits',
            r'^(\w+(?:\s+\w+)?)\s+construction\s+permits',
            r'city\s+of\s+(\w+(?:\s+\w+)?)',
        ]:
            match = re.search(pattern, name, re.IGNORECASE)
            if match:
                city = match.group(1).title()
                break

    # Look up state from city name
    if city:
        city_lower = city.lower()
        state = CITY_STATE_LOOKUP.get(city_lower)

    # Generate slug
    slug = None
    if city:
        slug = city.lower().replace(" ", "-").replace(".", "")

    return city, state, slug


def main():
    parser = argparse.ArgumentParser(description="PermitGrab City Discovery Script")
    parser.add_argument("--socrata-only", action="store_true", help="Only run Socrata sweep")
    parser.add_argument("--arcgis-only", action="store_true", help="Only run ArcGIS sweep")
    parser.add_argument("--ckan-only", action="store_true", help="Only run CKAN sweep")
    parser.add_argument("--verify-existing", action="store_true", help="Verify existing configs")
    parser.add_argument("--output", type=str, help="Output JSON file path")
    parser.add_argument("--generate-configs", action="store_true", help="Output Python config entries")

    args = parser.parse_args()

    discovery = CityDiscovery()

    if args.verify_existing:
        discovery.verify_existing()
    elif args.socrata_only:
        discovery.load_existing_configs()
        discovery.sweep_socrata()
        discovery.cross_reference()
    elif args.arcgis_only:
        discovery.load_existing_configs()
        discovery.sweep_arcgis()
        discovery.cross_reference()
    elif args.ckan_only:
        discovery.load_existing_configs()
        discovery.sweep_ckan()
        discovery.cross_reference()
    else:
        discovery.run_all()

    if args.output:
        discovery.output_report(args.output)

    if args.generate_configs:
        discovery.output_configs()


if __name__ == "__main__":
    main()
