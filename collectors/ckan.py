"""V527: CKAN platform module.

Used by Pittsburgh (WPRDC) and Boston. CKAN's datastore_search API
is similar in shape to Socrata but with different param names.
"""
from __future__ import annotations

from ._base import health_check as _base_health_check

PLATFORM = 'ckan'


def fetch(config, days_back=30):
    """Re-export shim around collector.fetch_ckan."""
    from collector import fetch_ckan
    return fetch_ckan(config, days_back)


def parse(raw_records, field_map):
    """Phase A: apply field_map to CKAN datastore_search records."""
    from ._base import apply_field_map
    out = []
    for record in raw_records or []:
        normalized = apply_field_map(record, field_map)
        if normalized:
            out.append(normalized)
    return out


def health_check(city_slug):
    return _base_health_check(city_slug, PLATFORM)


__all__ = ['PLATFORM', 'fetch', 'parse', 'health_check']
