"""V527: Socrata platform module.

Phase A: thin re-export shims around collector.py's `fetch_socrata` /
`fetch_socrata_bulk` plus a NEW health_check() backed by scraper_runs +
permits. Phase B will move the fetch bodies in here.
"""
from __future__ import annotations

from ._base import health_check as _base_health_check

PLATFORM = 'socrata'


def fetch(config, days_back=30):
    """Fetch raw records from a Socrata endpoint. Re-export shim
    around collector.py's fetch_socrata. See that for full semantics."""
    from collector import fetch_socrata
    return fetch_socrata(config, days_back)


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
