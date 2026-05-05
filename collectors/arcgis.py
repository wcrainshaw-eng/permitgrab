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
    """Normalize ArcGIS-shaped records. ArcGIS wraps the actual
    attributes in {'attributes': {...}, 'geometry': {...}} — the
    normalization step pulls out attributes and applies field_map."""
    from collector import normalize_permit
    out = []
    config = {'field_map': field_map} if field_map else {}
    for record in raw_records:
        # ArcGIS records often arrive as {'attributes': {...}}; flatten.
        if isinstance(record, dict) and 'attributes' in record and isinstance(record['attributes'], dict):
            record = record['attributes']
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


__all__ = ['PLATFORM', 'fetch', 'fetch_bulk', 'parse', 'health_check']
