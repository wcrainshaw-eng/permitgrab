"""V540 PR1: city-health compute. Per Wes's directive — converts the
V527 collectors module (which gave us per-platform diagnosis) into
the customer-visible city_health contract that the signup flow + the
digest pipeline can consult.

Public API:
- CityHealth (dataclass-like): slug, status, reason_code, reason_detail,
  evidence (dict), computed_at (ISO string).
- compute_city_health(slug) -> CityHealth: layered rubric on top of
  collectors.<platform>.health_check.
- compute_all_city_health() -> dict: nightly runner. Hits every active
  city, writes the row to city_health, returns summary {pass, degraded,
  fail, total, errored}.

Rubric (V540):
  Pass     = permits > 100 AND profiles > 100 AND last_collection < 7d
             AND with_phone_count > 0
  Degraded = exactly one threshold missed (any single short circuit)
  Fail     = source feed dead/stale > 21d  OR  collectors.health_check
             returned 'fail' for that platform

Out of scope (V541+):
- per-platform retry/backoff
- per-persona rubrics (real-estate-agent vs contractor)
- per-subscriber per-city pause/skip controls
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta

import db as permitdb


# Status constants. Capitalized per Wes's V540 spec to keep them
# distinct from V527 collectors' lowercase status (collectors uses
# 'pass'/'degraded'/'fail' for per-platform state; city_health
# capitalizes for the higher-level customer-visible contract).
PASS = 'Pass'
DEGRADED = 'Degraded'
FAIL = 'Fail'

# Reason codes — stable strings the signup flow + digest pipeline
# can switch on without parsing reason_detail prose.
REASON = {
    'all_thresholds_met': 'All thresholds met',
    'low_permits': 'Permit count below 100',
    'low_profiles': 'Contractor profile count below 100',
    'no_phones': 'No contractor profiles have phone numbers yet',
    'stale_collection': 'Last successful collection > 7 days ago',
    'dead_source': 'Source feed dead — no successful collection in 21+ days',
    'platform_fail': 'Platform-level health check returned fail',
    'unknown_platform': 'Source platform not recognized (likely bulk-source recipient)',
    'never_visited': 'No scraper_runs row — city has never been collected',
    'compute_error': 'Internal error computing city health (see evidence)',
}


@dataclass
class CityHealth:
    """A single city's health row. Mirrors the city_health table schema."""
    slug: str
    status: str
    reason_code: str
    reason_detail: str
    evidence: dict = field(default_factory=dict)
    computed_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def to_row(self):
        """Tuple in city_health table column order — for INSERT/UPDATE."""
        return (
            self.slug,
            self.status,
            self.reason_code,
            self.reason_detail,
            json.dumps(self.evidence),
            self.computed_at,
        )

    def to_dict(self):
        d = asdict(self)
        return d


def _measure_city(slug):
    """Read the four threshold inputs from the DB. Returns a dict of
    raw measurements that the rubric can compare against.

    V541b: every aggregate is aliased (`AS cnt` / `AS last_run`) so
    callers can access by column name on both psycopg2 RealDictRow
    AND sqlite3.Row. The previous version used
    `list(row.values())[0]` for the dict-row branch — but sqlite3.Row
    has `keys()` and supports string indexing, but does NOT have
    `.values()`. That silently AttributeError'd into the `except
    Exception: pass` and left every count/max at the default 0/None,
    which made compute_city_health Fail every active city as
    'never_visited' even though scraper_runs had 255+ rows. Caught
    by V541 audit when chicago-il came back as Fail despite obviously
    being healthy in production.
    """
    out = {
        'permits_count': 0,
        'profiles_count': 0,
        'with_phone_count': 0,
        'last_collection_at': None,
        'platform': None,
        'platform_health': None,
    }
    try:
        conn = permitdb.get_connection()
    except Exception as e:
        out['db_error'] = f'connection failed: {e}'
        return out

    def _row_value(row, key):
        """Cross-dialect single-value extractor.
        - sqlite3.Row: supports both integer and string indexing
        - psycopg2 RealDictRow: dict subclass, string indexing only
        - sqlite3 default tuple: integer indexing only (no row_factory)

        Try string key first (works on sqlite3.Row + RealDictRow);
        fall back to positional (sqlite3 plain tuple). Plain-tuple
        access by string raises TypeError, not KeyError, so that's
        included in the except list."""
        if row is None:
            return None
        try:
            return row[key]
        except (KeyError, IndexError, TypeError):
            pass
        try:
            return row[0]
        except Exception:
            return None

    try:
        row = conn.execute(
            "SELECT source_type FROM prod_cities "
            "WHERE city_slug = ? AND status = 'active' LIMIT 1",
            (slug,),
        ).fetchone()
        out['platform'] = _row_value(row, 'source_type')
    except Exception:
        pass

    try:
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM permits WHERE source_city_key = ?",
            (slug,),
        ).fetchone()
        v = _row_value(row, 'cnt')
        if v is not None:
            out['permits_count'] = int(v)
    except Exception:
        pass

    try:
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM contractor_profiles WHERE source_city_key = ?",
            (slug,),
        ).fetchone()
        v = _row_value(row, 'cnt')
        if v is not None:
            out['profiles_count'] = int(v)
    except Exception:
        pass

    try:
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM contractor_profiles "
            "WHERE source_city_key = ? AND phone IS NOT NULL AND phone != ''",
            (slug,),
        ).fetchone()
        v = _row_value(row, 'cnt')
        if v is not None:
            out['with_phone_count'] = int(v)
    except Exception:
        pass

    try:
        row = conn.execute(
            "SELECT MAX(run_started_at) AS last_run FROM scraper_runs WHERE city_slug = ?",
            (slug,),
        ).fetchone()
        out['last_collection_at'] = _row_value(row, 'last_run')
    except Exception:
        pass

    return out


def _platform_health(slug, platform):
    """Delegate to V527 collectors.<platform>.health_check for the
    per-platform rubric. Returns None if platform unknown or import
    fails (in which case the caller marks unknown_platform)."""
    if not platform:
        return None
    try:
        from collectors import get_collector_for
        mod = get_collector_for(platform)
        if mod is None:
            return None
        return mod.health_check(slug)
    except Exception as e:
        return {'status': 'fail', 'reason': f'platform health_check raised: {e}'}


def _days_since(ts_str):
    """Return days since the given ISO/SQLite timestamp string, or
    None if unparseable."""
    if not ts_str:
        return None
    try:
        # Normalize: SQLite returns 'YYYY-MM-DD HH:MM:SS', Postgres returns
        # 'YYYY-MM-DDTHH:MM:SS.ffffff' with optional offset.
        s = str(ts_str).replace('T', ' ').split('.')[0].split('+')[0].rstrip('Z').strip()
        ts = datetime.fromisoformat(s)
        delta = datetime.utcnow() - ts
        return max(0, delta.days)
    except Exception:
        return None


def compute_city_health(slug):
    """Compute the city-health row for `slug`. Pure read-only — does
    NOT write to city_health. Caller decides when to persist."""
    if not slug:
        return CityHealth(
            slug='',
            status=FAIL,
            reason_code='compute_error',
            reason_detail='empty slug',
            evidence={},
        )

    measurements = _measure_city(slug)
    platform = measurements.get('platform')
    measurements['platform_health'] = _platform_health(slug, platform)

    # FAIL conditions (most-severe first)
    last_run = measurements.get('last_collection_at')
    days_since = _days_since(last_run)
    measurements['days_since_last_collection'] = days_since

    if last_run is None and platform is not None:
        # Active typed city with no scraper_runs at all
        return CityHealth(
            slug=slug, status=FAIL,
            reason_code='never_visited',
            reason_detail=REASON['never_visited'],
            evidence=measurements,
        )

    if days_since is not None and days_since > 21:
        return CityHealth(
            slug=slug, status=FAIL,
            reason_code='dead_source',
            reason_detail=f'{REASON["dead_source"]} (last_run={last_run}, {days_since}d ago)',
            evidence=measurements,
        )

    plat_h = measurements.get('platform_health')
    if plat_h and plat_h.get('status') == 'fail':
        return CityHealth(
            slug=slug, status=FAIL,
            reason_code='platform_fail',
            reason_detail=f'{REASON["platform_fail"]} ({plat_h.get("reason", "no reason")})',
            evidence=measurements,
        )

    if platform is None:
        # Bulk-source recipient or NULL source_type — surface as Degraded
        # so signup flow knows not to promise live updates, even if the
        # bulk pipeline is feeding data.
        return CityHealth(
            slug=slug, status=DEGRADED,
            reason_code='unknown_platform',
            reason_detail=REASON['unknown_platform'],
            evidence=measurements,
        )

    # Threshold checks for Pass vs Degraded
    permits = measurements['permits_count']
    profiles = measurements['profiles_count']
    with_phone = measurements['with_phone_count']

    if days_since is not None and days_since > 7:
        return CityHealth(
            slug=slug, status=DEGRADED,
            reason_code='stale_collection',
            reason_detail=f'{REASON["stale_collection"]} (last_run {days_since}d ago)',
            evidence=measurements,
        )

    if permits <= 100:
        return CityHealth(
            slug=slug, status=DEGRADED,
            reason_code='low_permits',
            reason_detail=f'{REASON["low_permits"]} (have {permits})',
            evidence=measurements,
        )

    if profiles <= 100:
        return CityHealth(
            slug=slug, status=DEGRADED,
            reason_code='low_profiles',
            reason_detail=f'{REASON["low_profiles"]} (have {profiles})',
            evidence=measurements,
        )

    if with_phone <= 0:
        return CityHealth(
            slug=slug, status=DEGRADED,
            reason_code='no_phones',
            reason_detail=REASON['no_phones'],
            evidence=measurements,
        )

    # All four thresholds met
    return CityHealth(
        slug=slug, status=PASS,
        reason_code='all_thresholds_met',
        reason_detail=REASON['all_thresholds_met'],
        evidence=measurements,
    )


def upsert_city_health(health):
    """Persist a CityHealth row to the city_health table. Uses
    cross-dialect ON CONFLICT (city_slug) DO UPDATE SET ... so the
    same SQL works on Postgres + SQLite 3.24+ (V525b pattern)."""
    if not isinstance(health, CityHealth):
        raise TypeError(f'expected CityHealth, got {type(health)}')
    try:
        conn = permitdb.get_connection()
        conn.execute(
            "INSERT INTO city_health "
            "(city_slug, status, reason_code, reason_detail, evidence_json, computed_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (city_slug) DO UPDATE SET "
            "  status=excluded.status, reason_code=excluded.reason_code, "
            "  reason_detail=excluded.reason_detail, "
            "  evidence_json=excluded.evidence_json, "
            "  computed_at=excluded.computed_at",
            health.to_row(),
        )
        conn.commit()
        return True
    except Exception as e:
        print(f'[city_health.compute] upsert failed for {health.slug!r}: {e}', flush=True)
        return False


def compute_all_city_health():
    """Nightly runner. Iterates every active city in prod_cities and
    writes a row to city_health. Returns a summary dict for
    health_scheduler to log to digest_log.
    """
    summary = {'pass': 0, 'degraded': 0, 'fail': 0, 'total': 0, 'errored': 0}
    try:
        conn = permitdb.get_connection()
        rows = conn.execute(
            "SELECT city_slug FROM prod_cities "
            "WHERE status = 'active' "
            "ORDER BY city_slug"
        ).fetchall()
    except Exception as e:
        print(f'[city_health.compute] enumerate failed: {e}', flush=True)
        summary['errored'] = 1
        return summary

    for row in rows:
        slug = row[0] if not hasattr(row, 'keys') else row['city_slug']
        if not slug:
            continue
        summary['total'] += 1
        try:
            ch = compute_city_health(slug)
        except Exception as e:
            print(f'[city_health.compute] compute failed for {slug!r}: {e}', flush=True)
            summary['errored'] += 1
            continue
        if ch.status == PASS:
            summary['pass'] += 1
        elif ch.status == DEGRADED:
            summary['degraded'] += 1
        else:
            summary['fail'] += 1
        upsert_city_health(ch)
    return summary
