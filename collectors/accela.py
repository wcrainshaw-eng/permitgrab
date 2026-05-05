"""V527: Accela HTML platform module.

Accela is the heaviest platform — each permit detail page is its own
HTTP fetch. The V162/V163 rewrite from Playwright to requests+BS4
made it survivable as a request-time scraper, but it's still slow per
city. Per-cycle visit threshold is 72h instead of 36h (see _base.py).
"""
from __future__ import annotations

from ._base import health_check as _base_health_check

PLATFORM = 'accela'


def fetch(config, days_back=30):
    """Re-export shim around accela_portal_collector.fetch_accela."""
    from accela_portal_collector import fetch_accela
    return fetch_accela(config, days_back)


def fetch_arcgis_hybrid(config, days_back=30):
    """V476-style hybrid: pull permit list from a local ArcGIS service,
    then per-permit parse Licensed Professional from the linked Accela
    detail HTML. Tampa pattern. Re-export of accela_portal_collector
    function."""
    from accela_portal_collector import fetch_accela_arcgis_hybrid
    return fetch_accela_arcgis_hybrid(config, days_back)


def parse(raw_records, field_map):
    """Phase A: apply field_map to Accela records. Records are
    already-parsed dicts from the BS4 scrape in accela_portal_collector."""
    from ._base import apply_field_map
    out = []
    for record in raw_records or []:
        normalized = apply_field_map(record, field_map)
        if normalized:
            out.append(normalized)
    return out


def health_check(city_slug):
    return _base_health_check(city_slug, PLATFORM)


__all__ = ['PLATFORM', 'fetch', 'fetch_arcgis_hybrid', 'parse', 'health_check']
