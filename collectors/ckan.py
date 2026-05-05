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


__all__ = ['PLATFORM', 'fetch', 'parse', 'health_check']
