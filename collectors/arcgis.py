"""V527/V535: ArcGIS platform module (covers FeatureServer + MapServer).

Both ArcGIS variants flow through the same fetcher; the URL itself
distinguishes them (`.../FeatureServer/<n>/query` vs
`.../MapServer/<n>/query`). V535 Phase B moved the fetch_arcgis body
here from collector.py per the V534 (socrata) pattern.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from ._base import health_check as _base_health_check

PLATFORM = 'arcgis'


def fetch(config, days_back=30):
    """Fetch permits from an ArcGIS REST API FeatureServer or MapServer.

    V535: lifted from collector.py:1030-1185 unchanged. Handles:
    - 5 date_format variants (date, epoch, string, string_mdy, none)
    - V126 date-format auto-retry across formats
    - V12.7 city_filter for county/state datasets
    - V131 pagination with MAX_PAGES=10 / MAX_RECORDS=50000 caps
    - V295 returnGeometry=false (Table endpoints reject default true)
    - V49/V154 client-side date filtering for string_mdy and none
    - V295 ArcGIS-level error detection (HTTP 200 with error JSON)
    """
    from collector import SESSION, API_TIMEOUT_SECONDS

    endpoint = config["endpoint"]
    date_field = config["date_field"]
    limit = config.get("limit", 2000)
    # V35: Check field_map._date_format as override (used for city_sources entries)
    fmap = config.get("field_map", {})
    date_format = fmap.get("_date_format") or config.get("date_format", "date")  # "date", "epoch", "string", or "none"

    # Calculate date filter
    since_dt = datetime.now() - timedelta(days=days_back)

    if date_format == "epoch":
        since_epoch = int(since_dt.timestamp() * 1000)
        where_clause = f"{date_field} >= {since_epoch}"
    elif date_format == "string":
        # V54: For string-type date fields (ISO format like "2026-03-31"),
        # use server-side string comparison instead of fetching everything
        since_date = since_dt.strftime("%Y-%m-%d")
        where_clause = f"{date_field} >= '{since_date}'"
    elif date_format == "string_mdy":
        # V154: For M/D/YYYY string date fields (e.g., Worcester), use LIKE to filter by year
        # and do fine-grained filtering client-side. Cover both since_year and current_year.
        since_year = since_dt.year
        current_year = datetime.now().year
        if since_year == current_year:
            where_clause = f"{date_field} LIKE '%/{current_year}'"
        else:
            where_clause = f"({date_field} LIKE '%/{since_year}' OR {date_field} LIKE '%/{current_year}')"
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

    page_size = limit
    MAX_PAGES = 10  # V131: Safety limit
    MAX_RECORDS = 50000  # V131: Hard cap

    # V126: Ensure endpoint ends with /query for ArcGIS
    query_endpoint = endpoint
    if '/query' not in query_endpoint:
        query_endpoint = query_endpoint.rstrip('/') + '/query'

    # V131: Helper to fetch one page with date format auto-retry (V126)
    def _fetch_arcgis_page(wc, offs):
        params = {
            "where": wc,
            "outFields": "*",
            "resultRecordCount": page_size,
            "resultOffset": offs,
            "orderByFields": f"{date_field} DESC",
            # V295: ArcGIS *Table* endpoints (e.g. Las Vegas OpenData_Building_Permits_
            # at services1.arcgis.com/F1v0ufATbBQScMtY/.../FeatureServer/0 — a Table,
            # not a Feature Layer) 400 with "Invalid or missing input parameters"
            # when returnGeometry defaults to true. Safe for Feature Layers too.
            "returnGeometry": "false",
            "f": "json",
        }
        r = SESSION.get(query_endpoint, params=params, timeout=API_TIMEOUT_SECONDS)
        r.raise_for_status()
        return r.json()

    # First page — with date format auto-retry (V126)
    data = _fetch_arcgis_page(where_clause, 0)

    if "error" in data and date_field:
        print(f"    [V126] ArcGIS date query failed with format '{date_format}', trying alternates...", flush=True)
        alt_formats = []
        if date_format != "epoch":
            alt_formats.append(("epoch", f"{date_field} >= {int(since_dt.timestamp() * 1000)}"))
        if date_format != "date":
            alt_formats.append(("DATE", f"{date_field} >= DATE '{since_dt.strftime('%Y-%m-%d')}'"))
        if date_format != "string":
            alt_formats.append(("string", f"{date_field} >= '{since_dt.strftime('%Y-%m-%d')}'"))
        alt_formats.append(("timestamp", f"{date_field} >= timestamp '{since_dt.strftime('%Y-%m-%d')} 00:00:00'"))

        for fmt_name, alt_where in alt_formats:
            try:
                alt_data = _fetch_arcgis_page(alt_where, 0)
                if "error" not in alt_data:
                    print(f"    [V126] Date format '{fmt_name}' worked for {endpoint[:60]}", flush=True)
                    data = alt_data
                    where_clause = alt_where  # Use working format for pagination
                    break
            except Exception:
                continue

    # V12.4: Detect ArcGIS-level errors (HTTP 200 with error JSON body)
    if "error" in data:
        error_msg = data["error"].get("message", "Unknown ArcGIS error")
        error_code = data["error"].get("code", "unknown")
        raise Exception(f"ArcGIS error {error_code}: {error_msg}")

    # V131: Paginate through all results
    all_features = data.get("features", [])
    exceeded = data.get("exceededTransferLimit", False)
    for page_num in range(1, MAX_PAGES):
        if len(all_features) >= MAX_RECORDS:
            break
        page_has_full = len(data.get("features", [])) >= page_size
        if not page_has_full and not exceeded:
            break
        offset = page_num * page_size
        try:
            data = _fetch_arcgis_page(where_clause, offset)
            if "error" in data or "features" not in data:
                break
            all_features.extend(data["features"])
            exceeded = data.get("exceededTransferLimit", False)
        except Exception:
            break  # Network error on pagination — keep what we have

    # Build final data dict for downstream processing
    data = {"features": all_features}

    if "features" in data:
        results = [f["attributes"] for f in data["features"]]
        # If using "none" or "string_mdy" date_format, filter in Python
        if date_format in ("none", "string_mdy") and date_field and results:
            since_epoch = int(since_dt.timestamp() * 1000)
            since_iso = since_dt.strftime("%Y-%m-%d")
            filtered = []
            for r in results:
                val = r.get(date_field)
                if not val:
                    continue
                # Handle epoch milliseconds (numbers)
                if isinstance(val, (int, float)) and val >= since_epoch:
                    filtered.append(r)
                # Handle ISO/string dates (e.g. "2026-01-15" or "2026-01-15T...")
                elif isinstance(val, str) and len(val) >= 10 and val[:4].isdigit() and val[:10] >= since_iso:
                    filtered.append(r)
                # Handle US date strings (e.g. "03/22/2026" or "3/1/2026" -> M/D/YYYY) - V49, V154
                elif isinstance(val, str) and len(val) >= 8 and '/' in val:
                    try:
                        parts = val.split('/')
                        if len(parts) == 3:
                            iso_val = f"{parts[2][:4]}-{parts[0].zfill(2)}-{parts[1].zfill(2)}"
                            if iso_val >= since_iso:
                                filtered.append(r)
                    except (IndexError, ValueError):
                        filtered.append(r)  # Don't drop data silently
                # If we can't parse it, include it (don't drop data silently)
                elif not isinstance(val, (int, float, str)):
                    filtered.append(r)
            results = filtered
        return results
    return []


def fetch_bulk(config, days_back=90):
    from collector import fetch_arcgis_bulk
    return fetch_arcgis_bulk(config, days_back)


def parse(raw_records, field_map):
    """Phase A: apply field_map to each raw ArcGIS record. ArcGIS
    wraps the actual attributes in {'attributes': {...}, 'geometry':
    {...}} — apply_field_map auto-unwraps."""
    from ._base import apply_field_map
    out = []
    for record in raw_records or []:
        normalized = apply_field_map(record, field_map)
        if normalized:
            out.append(normalized)
    return out


def health_check(city_slug):
    return _base_health_check(city_slug, PLATFORM)


__all__ = ['PLATFORM', 'fetch', 'fetch_bulk', 'parse', 'health_check']
