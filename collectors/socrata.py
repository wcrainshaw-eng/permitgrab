"""V527/V534: Socrata platform module.

V527 Phase A shipped this as a re-export shim around collector.py's
fetch_socrata. V534 Phase B moves the fetch body here so the platform
quirks (Socrata's $where syntax, $offset pagination, $order direction,
SODA app-token if/when we add one) live with the platform module.
collector.py's fetch_socrata is now a back-compat shim that calls
collectors.socrata.fetch — preserves the existing public API for any
caller still importing fetch_socrata directly.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from ._base import health_check as _base_health_check

PLATFORM = 'socrata'


def fetch(config, days_back=30):
    """Fetch permits from a Socrata SODA API. Paginates until exhausted.

    V131 (lifted from collector.py): MAX_PAGES=10, MAX_RECORDS=50000
    safety caps. Date filter via $where, ordered DESC by date_field.
    Optional city_filter for county/state datasets, optional
    where_filter for permit-type narrowing.

    V534: lifted from collector.py:1019-1062 unchanged. SESSION +
    API_TIMEOUT_SECONDS imported lazily from collector to avoid the
    V527 contract violation (importing collectors must not pull
    collector.py into sys.modules).
    """
    # Lazy import: keeps the collectors package import-light.
    from collector import SESSION, API_TIMEOUT_SECONDS

    endpoint = config["endpoint"]
    # V60: Removed V55 /query auto-append — was incorrectly appending
    # /query to Socrata .json URLs causing 404s.
    date_field = config["date_field"]
    page_size = config.get("limit", 2000)
    MAX_PAGES = 10  # V131: Safety limit — max 10 pages (20,000 records)
    MAX_RECORDS = 50000  # V131: Hard cap

    # Calculate date filter
    since_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%dT00:00:00")

    where_clause = f"{date_field} > '{since_date}'"

    # V12.7: Add city filter for county/state datasets
    city_filter = config.get("city_filter")
    if city_filter:
        filter_field = city_filter["field"]
        filter_value = city_filter["value"]
        where_clause += f" AND upper({filter_field}) = upper('{filter_value}')"

    # V31: Append extra where_filter if configured (e.g., permit type filtering)
    extra_filter = config.get("where_filter")
    if extra_filter:
        where_clause += f" AND ({extra_filter})"

    # V131: Paginate through all results
    all_records = []
    offset = 0
    for page_num in range(MAX_PAGES):
        params = {
            "$limit": page_size,
            "$offset": offset,
            "$order": f"{date_field} DESC",
            "$where": where_clause,
        }
        resp = SESSION.get(endpoint, params=params, timeout=API_TIMEOUT_SECONDS)
        resp.raise_for_status()
        page = resp.json()
        all_records.extend(page)
        if len(page) < page_size or len(all_records) >= MAX_RECORDS:
            break  # Last page or safety limit
        offset += page_size
    return all_records


def fetch_bulk(config, days_back=90):
    """Fetch records from a multi-city Socrata bulk endpoint."""
    from collector import fetch_socrata_bulk
    return fetch_socrata_bulk(config, days_back)


def parse(raw_records, field_map):
    """Phase A: apply field_map to each raw Socrata record. Phase B
    will move the full normalize_permit semantics (date parsing,
    trade classification, value tiers) here so Socrata-specific
    quirks live with the platform."""
    from ._base import apply_field_map
    out = []
    for record in raw_records or []:
        normalized = apply_field_map(record, field_map)
        if normalized:
            out.append(normalized)
    return out


def health_check(city_slug):
    """V527: Pass/Degraded/Fail diagnosis for a Socrata city."""
    return _base_health_check(city_slug, PLATFORM)


__all__ = ['PLATFORM', 'fetch', 'fetch_bulk', 'parse', 'health_check']
