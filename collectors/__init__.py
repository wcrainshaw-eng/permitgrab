"""V527: per-platform collector modules. Extracted from collector.py /
accela_portal_collector.py / license_enrichment.py per the V524
template (small, tested, server.py-shrinking).

Public contract for each platform module:
  fetch(config, days_back=30) -> list[dict]
      Raw record fetch from the upstream API. Same shape as the
      legacy collector.py functions; this is intentionally a thin
      re-export shim in V527 Phase A so behavior doesn't change.
  parse(raw_records, field_map) -> list[dict]
      Normalize raw records into PermitGrab's permit row shape.
  health_check(city_slug) -> dict
      Returns:
        {'status': 'pass'|'degraded'|'fail',
         'reason': str,
         'platform': str,
         'last_run': str|None,
         'last_run_status': str|None,
         'permits_24h': int,
         'consecutive_failures': int}
      Backed by scraper_runs + prod_cities. Does NOT make outbound
      HTTP calls — this is local-DB inspection only, safe to call
      every request.

Design rules
- Phase A (this PR): the fetch/parse functions are re-export shims to
  preserve behavior. Phase B will move bodies into these modules and
  collector.py will import-from-here. The directive: "thin shims at
  first; second pass after V527 lands clean replaces the shims with
  direct imports."
- health_check() is NEW. It's the foundation for the city-health
  contract Wes asked for ("every city has a Pass/Degraded/Fail score
  the system computes nightly, the signup flow consults at checkout,
  and the digest pipeline respects").
- Each platform module is tested independently in tests/test_collectors_*.py.
- A central routing helper `get_collector_for(platform)` returns the
  module object, so callers can fan out by platform without giant
  if/elif chains.

Out of scope (NOT this PR)
- V528: LIKE case-sensitivity wrapping (80 unwrapped callsites)
- V529: NULL ordering audit
- V530+: per-collector retry/backoff
- V531+: assessor_collector extraction (separate vertical)
"""
from __future__ import annotations

from . import accela, arcgis, ckan, csv_state, socrata

# Map of (platform_name) → module. The map name strings match the
# values in CITY_REGISTRY[*]['platform'] and prod_cities.source_type.
_PLATFORMS = {
    'socrata': socrata,
    'arcgis': arcgis,
    'arcgis_featureserver': arcgis,
    'arcgis_mapserver': arcgis,
    'accela': accela,
    'accela_arcgis_hybrid': accela,
    'ckan': ckan,
    'csv_state': csv_state,
}

SUPPORTED_PLATFORMS = ('socrata', 'arcgis', 'accela', 'ckan', 'csv_state')


def get_collector_for(platform: str):
    """Return the platform module, or None if unsupported.

    Accepts the legacy synonyms (arcgis_featureserver, arcgis_mapserver,
    accela_arcgis_hybrid) so callers don't need to canonicalize.
    """
    if not platform:
        return None
    return _PLATFORMS.get(platform.lower())


def health_check_all(slugs: list[str]) -> list[dict]:
    """Return health_check() for a list of slugs in one call. Used by
    the /api/admin/collector-health endpoint."""
    out = []
    for slug in slugs:
        # Resolve platform per slug; we look it up from prod_cities
        # via the platform module itself which uses scraper_runs.
        # Try each supported platform's health_check; the one that
        # actually owns the slug (matched by source_type in prod_cities)
        # returns a non-fail status. If none claim it, return a
        # placeholder.
        result = _resolve_health(slug)
        out.append(result)
    return out


def _resolve_health(slug: str) -> dict:
    """Single-slug version of health_check_all. Imports lazy because
    db is the parent of this package's lookup target."""
    import db as permitdb
    try:
        conn = permitdb.get_connection()
        row = conn.execute(
            "SELECT source_type FROM prod_cities WHERE city_slug = ? "
            "AND status = 'active' LIMIT 1",
            (slug,),
        ).fetchone()
    except Exception as e:
        return {
            'slug': slug,
            'status': 'fail',
            'reason': f'db lookup failed: {e}',
            'platform': None,
        }
    if not row:
        return {
            'slug': slug,
            'status': 'fail',
            'reason': 'not in prod_cities or status != active',
            'platform': None,
        }
    platform = row[0] if not hasattr(row, 'keys') else row['source_type']
    mod = get_collector_for(platform)
    if mod is None:
        return {
            'slug': slug,
            'status': 'degraded',
            'reason': f'unsupported or NULL platform: {platform!r}',
            'platform': platform,
        }
    res = mod.health_check(slug)
    res['slug'] = slug
    res['platform'] = platform
    return res


__all__ = [
    'SUPPORTED_PLATFORMS',
    'get_collector_for',
    'health_check_all',
    'socrata',
    'arcgis',
    'accela',
    'ckan',
    'csv_state',
]
