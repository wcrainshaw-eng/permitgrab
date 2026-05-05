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
    """Accela records are already-parsed dicts coming out of the BS4
    scrape (see accela_portal_collector). Normalize via the same
    normalize_permit path as the other platforms."""
    from collector import normalize_permit
    out = []
    config = {'field_map': field_map} if field_map else {}
    for record in raw_records:
        try:
            normalized = normalize_permit(record, source_id_or_config=config)
            if normalized and normalized.get('permit_number'):
                out.append(normalized)
        except TypeError:
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
    return _base_health_check(city_slug, PLATFORM)


__all__ = ['PLATFORM', 'fetch', 'fetch_arcgis_hybrid', 'parse', 'health_check']
