"""V527: CSV-state-license platform module.

Wraps license_enrichment.py's per-state importers (FL DBPR, MN DLI,
NY DOL, WA L&I, CA CSLB, AZ ROC). Cadence is monthly — these run via
admin-triggered import endpoints, not the per-cycle daemon.

Phase A is intentionally narrow: expose a uniform fetch() that runs a
single state's importer, plus a health_check() that asks "when did
this state last successfully import?". Phase B can decompose into
per-state submodules (csv_state/fl_dbpr.py, csv_state/mn_dli.py, etc.)
once the layout proves out.
"""
from __future__ import annotations

from ._base import health_check as _base_health_check

PLATFORM = 'csv_state'


# Map state code → (license_enrichment.py entry-point name) so callers
# can dispatch by state without importing license_enrichment up front.
_STATE_ENTRY_POINTS = {
    'FL': 'import_fl_dbpr',
    'MN': 'import_mn_dli',
    'NY': 'import_ny_dol',
    'WA': 'import_wa_li',
    'CA': 'import_ca_cslb',
    'AZ': 'import_az_roc',
}


def fetch(config, days_back=None):
    """Run a state license import. config['state'] selects the state.
    days_back is ignored — state license imports always pull the full
    bulk download. Returns a result dict from the importer."""
    state = (config or {}).get('state', '').upper()
    entry = _STATE_ENTRY_POINTS.get(state)
    if not entry:
        return {'status': 'error', 'error': f'unsupported state: {state}'}
    import license_enrichment as le
    fn = getattr(le, entry, None)
    if fn is None:
        return {
            'status': 'error',
            'error': f'license_enrichment.{entry} not found',
        }
    try:
        return fn()
    except Exception as e:
        return {'status': 'error', 'error': str(e)}


def parse(raw_records, field_map):
    """No-op for state license imports — they write to applicant_phones
    / applicant_phones_by_license directly inside the importer.
    Provided for API uniformity with the other platform modules."""
    return list(raw_records or [])


def health_check(city_slug):
    """For CSV-state platforms, city_slug is treated as a state code
    (e.g. 'FL', 'MN'). The base health check uses scraper_runs which
    doesn't track state imports — a Phase B improvement is to query
    system_state for the last successful import per state. For now,
    return degraded with the right reason so the endpoint surfaces
    the gap."""
    state = (city_slug or '').upper()
    if state not in _STATE_ENTRY_POINTS:
        return {
            'status': 'fail',
            'reason': f'unknown state: {state}',
            'platform': PLATFORM,
            'last_run': None,
            'last_run_status': None,
            'permits_24h': 0,
            'newest_permit_date': None,
            'consecutive_failures': 0,
        }
    # Phase A: surface the platform-specific gap rather than lying.
    return _base_health_check(state.lower(), PLATFORM)


__all__ = ['PLATFORM', 'fetch', 'parse', 'health_check']
