"""V527: ArcGIS platform module (covers FeatureServer + MapServer).

Both ArcGIS variants flow through the same fetcher; the URL itself
distinguishes them (`.../FeatureServer/<n>/query` vs
`.../MapServer/<n>/query`). Per-platform retry/backoff differences
between the two are V530+ work, not V527.
"""
from __future__ import annotations

from ._base import health_check as _base_health_check

PLATFORM = 'arcgis'


def fetch(config, days_back=30):
    """Re-export shim around collector.fetch_arcgis. Handles both
    FeatureServer and MapServer endpoints based on the URL in
    config['endpoint']."""
    from collector import fetch_arcgis
    return fetch_arcgis(config, days_back)


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
