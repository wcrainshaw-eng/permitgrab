"""V527: shared health_check() + minimal parse() helpers used by all
platform modules.

The platform-specific quirks are minimal — the actual rubric is the
same: did the daemon visit this slug recently, did it succeed, are
fresh permits landing? Each platform module passes its name in so the
diagnosis can fork on platform-specific thresholds (e.g. Accela cities
visit less often by design, so the freshness threshold is wider).

apply_field_map() is the V527 Phase A normalization stand-in. It does
JUST ENOUGH to pin the record-shape contract per platform — extract
the permit_number + the canonical fields named in the field_map.
Phase B will replace it with the full collector.normalize_permit
semantics (date parsing, trade classification, value tiers, owner/
contact fallbacks). Keeping it minimal in Phase A means fixture-
based tests don't need to load city_configs / collector.py.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import db as permitdb


def apply_field_map(record, field_map):
    """V527 Phase A: extract canonical fields from a raw record using
    the source-key → canonical-key map. Returns a dict, or None if
    the record has no permit_number (= unusable).

    ArcGIS records arrive wrapped as {'attributes': {...}, 'geometry':
    {...}}. Auto-unwrap so callers don't have to.
    """
    if not isinstance(record, dict):
        return None
    if 'attributes' in record and isinstance(record['attributes'], dict):
        record = record['attributes']
    if not field_map:
        return None
    result = {}
    for canonical, source_key in field_map.items():
        if not source_key:
            continue
        # exact match first, then case-insensitive
        val = record.get(source_key)
        if val is None:
            for k, v in record.items():
                if k.lower() == source_key.lower():
                    val = v
                    break
        if val is not None:
            # Normalize to string per the existing pipeline convention
            result[canonical] = str(val).strip() if not isinstance(val, (dict, list)) else val
    if not result.get('permit_number'):
        return None
    return result


# Per-platform thresholds for "recent enough" visit. Tuned to the
# historical cadence each platform sustains — Accela is slower because
# the HTML scrape per permit is heavier per city.
_RECENT_VISIT_HOURS = {
    'socrata': 36,
    'arcgis': 36,
    'ckan': 36,
    'accela': 72,
    'csv_state': 24 * 30,  # state license imports run monthly
}

# Per-platform thresholds for "permits landed recently" — what age of
# the latest permit row should trigger a degraded status. These reflect
# real city cadence: a city collecting daily should have a permit
# from the last 7 days.
_FRESH_PERMIT_DAYS = {
    'socrata': 7,
    'arcgis': 7,
    'ckan': 14,
    'accela': 14,
    'csv_state': 60,
}


def health_check(city_slug: str, platform: str) -> dict:
    """Compute Pass/Degraded/Fail for one (slug, platform) pair.

    Inspects scraper_runs + permits without making outbound HTTP
    calls. Safe to call from request hot paths.

    Returns dict with keys:
      status: 'pass' | 'degraded' | 'fail'
      reason: str
      platform: str (echo of input)
      last_run: ISO timestamp or None
      last_run_status: 'success' | 'no_new' | 'error' | 'skip' | None
      permits_24h: int
      newest_permit_date: str or None
      consecutive_failures: int
    """
    out = {
        'status': 'fail',
        'reason': 'init',
        'platform': platform,
        'last_run': None,
        'last_run_status': None,
        'permits_24h': 0,
        'newest_permit_date': None,
        'consecutive_failures': 0,
    }

    try:
        conn = permitdb.get_connection()
    except Exception as e:
        out['reason'] = f'db unavailable: {e}'
        return out

    # Last scraper_runs row for this slug
    try:
        row = conn.execute(
            "SELECT run_started_at, status, error_message "
            "FROM scraper_runs "
            "WHERE city_slug = ? "
            "ORDER BY run_started_at DESC LIMIT 1",
            (city_slug,),
        ).fetchone()
    except Exception as e:
        out['reason'] = f'scraper_runs query failed: {e}'
        return out

    if row is None:
        out['status'] = 'fail'
        out['reason'] = 'no scraper_runs entry — city has never been visited'
        return out

    last_run_iso = row[0] if not hasattr(row, 'keys') else row['run_started_at']
    last_run_status = row[1] if not hasattr(row, 'keys') else row['status']
    last_run_err = row[2] if not hasattr(row, 'keys') else row['error_message']
    out['last_run'] = str(last_run_iso) if last_run_iso else None
    out['last_run_status'] = last_run_status

    # Consecutive-failure count
    try:
        cf_row = conn.execute(
            "SELECT consecutive_failures FROM prod_cities "
            "WHERE city_slug = ? LIMIT 1",
            (city_slug,),
        ).fetchone()
        if cf_row is not None:
            out['consecutive_failures'] = int(
                cf_row[0] if not hasattr(cf_row, 'keys')
                else cf_row['consecutive_failures'] or 0
            )
    except Exception:
        pass

    # Permits in last 24h
    try:
        p24 = conn.execute(
            "SELECT COUNT(*) FROM permits "
            "WHERE source_city_key = ? "
            "AND collected_at > datetime('now', '-1 day')",
            (city_slug,),
        ).fetchone()
        if p24 is not None:
            out['permits_24h'] = int(
                p24[0] if not hasattr(p24, 'keys') else list(p24.values())[0]
            )
    except Exception:
        pass

    # Newest permit date for this slug
    try:
        nd = conn.execute(
            "SELECT MAX(date) FROM permits WHERE source_city_key = ?",
            (city_slug,),
        ).fetchone()
        if nd is not None:
            out['newest_permit_date'] = (
                nd[0] if not hasattr(nd, 'keys') else list(nd.values())[0]
            )
    except Exception:
        pass

    # Now compute the rubric.
    recent_hours = _RECENT_VISIT_HOURS.get(platform, 48)
    fresh_days = _FRESH_PERMIT_DAYS.get(platform, 7)

    # Parse last_run for freshness compare
    visit_age_hours = None
    try:
        if last_run_iso:
            # SQLite datetime() returns 'YYYY-MM-DD HH:MM:SS'; isoformat too
            iso_norm = str(last_run_iso).replace('T', ' ').split('.')[0]
            visit_dt = datetime.fromisoformat(iso_norm)
            visit_age_hours = (datetime.utcnow() - visit_dt).total_seconds() / 3600.0
    except Exception:
        pass

    if last_run_status == 'error' and out['consecutive_failures'] >= 3:
        out['status'] = 'fail'
        out['reason'] = (
            f'{out["consecutive_failures"]} consecutive failures'
            + (f' — last error: {str(last_run_err)[:80]}' if last_run_err else '')
        )
    elif visit_age_hours is not None and visit_age_hours > recent_hours:
        out['status'] = 'fail'
        out['reason'] = (
            f'last visit was {visit_age_hours:.1f}h ago (> {recent_hours}h '
            f'threshold for {platform})'
        )
    elif last_run_status == 'error':
        out['status'] = 'degraded'
        out['reason'] = (
            f'last run errored ({out["consecutive_failures"]} consecutive '
            f'failures so far)'
        )
    elif out['newest_permit_date']:
        try:
            newest_dt = datetime.fromisoformat(out['newest_permit_date'])
            permit_age_days = (datetime.utcnow() - newest_dt).days
            if permit_age_days > fresh_days:
                out['status'] = 'degraded'
                out['reason'] = (
                    f'newest permit is {permit_age_days}d old '
                    f'(> {fresh_days}d threshold for {platform}); '
                    f'daemon visiting but source has gone idle'
                )
            else:
                out['status'] = 'pass'
                out['reason'] = (
                    f'last run {visit_age_hours:.1f}h ago, '
                    f'newest permit {permit_age_days}d old'
                )
        except Exception:
            out['status'] = 'pass'
            out['reason'] = 'last run recent; newest_permit_date unparseable'
    else:
        # Visited recently, no permits at all — pass with caveat
        out['status'] = 'degraded'
        out['reason'] = 'visited recently but zero permits in DB'

    return out
