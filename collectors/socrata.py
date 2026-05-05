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
    """Normalize raw Socrata records via collector.normalize_permit.

    Phase A: re-export shim. Phase B will move the normalization
    here so platform-specific quirks (e.g. Socrata's :id pseudo-
    column, datetime ISO offsets) live with the platform.
    """
    from collector import normalize_permit
    out = []
    config = {'field_map': field_map} if field_map else {}
    for record in raw_records:
        try:
            normalized = normalize_permit(record, source_id_or_config=config)
            if normalized and normalized.get('permit_number'):
                out.append(normalized)
        except TypeError:
            # Older signature: normalize_permit(record, city_key)
            try:
                normalized = normalize_permit(record, '')
                if normalized and normalized.get('permit_number'):
                    out.append(normalized)
            except Exception:
                continue
        except Exception:
            continue
    return out


def health_check(city_slug):
    """V527: Pass/Degraded/Fail diagnosis for a Socrata city."""
    return _base_health_check(city_slug, PLATFORM)


__all__ = ['PLATFORM', 'fetch', 'fetch_bulk', 'parse', 'health_check']
