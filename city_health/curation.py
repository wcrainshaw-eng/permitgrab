"""V540 PR3: pre-curation gates. Per Wes's V540 reframe — the system
inventory IS the trust signal. Users never see Fail/Degraded cities
as sellable options. Trust the system; don't triage exceptions.

Public API:
- is_sellable_city(slug) -> bool: gate function for picker / sitemap /
  ads / blog auto-gen / soft-degrade.
- get_sellable_cities() -> set[str]: bulk list for filtering.
- has_city_health_data() -> bool: detect fresh-deploy state where
  the scheduler hasn't yet populated city_health.

Design note (fail-open during cold start):
The curation gate fail-OPENS when city_health is empty or the table
is missing. Reasoning: a fresh deploy + scheduler-hasn't-fired-yet
must NOT hide every city from the picker. The cron fires daily; until
then, the existing 'every active city is sellable' contract holds.
After the first cron fire, the gate kicks in.
"""
from __future__ import annotations

import db as permitdb


def has_city_health_data():
    """True iff the city_health table has at least one row.

    Pre-curation gates fail-open until at least one row exists, so a
    fresh deploy doesn't hide every city before the daily cron runs.

    V546c (2026-05-06): V541b/V543 row.values() bug class was hiding
    here too. `list(row.values())[0]` AttributeError'd on sqlite3.Row
    (which has keys() but NO values()), tripped the broad except, and
    returned False — silently fail-opening V540 PR3 sitemap and picker
    filters even when city_health was fully populated. Caught when the
    V544-filtered sitemap-cities.xml was returning 1,713 URLs instead
    of the expected 28 Pass cities. Fix is the same cross-dialect
    `COUNT(*) AS cnt` + alias-based access used in compute.py and
    collectors/_base.py.
    """
    try:
        conn = permitdb.get_connection()
        row = conn.execute("SELECT COUNT(*) AS cnt FROM city_health").fetchone()
        if row is None:
            return False
        # Cross-dialect single-value extractor — sqlite3.Row supports
        # both string and integer indexing; psycopg2 RealDictRow only
        # supports string. Try string first, fall back to position 0.
        try:
            cnt = row['cnt']
        except (KeyError, IndexError, TypeError):
            try:
                cnt = row[0]
            except Exception:
                return False
        return int(cnt or 0) > 0
    except Exception:
        # Table missing or DB unavailable — treat as "no data yet"
        return False


def is_sellable_city(slug):
    """True if `slug` is a sellable city per V540 curation.

    Returns True when:
      - city_health has no rows yet (fresh-deploy fail-open), OR
      - the row's status == 'Pass'.

    Returns False when:
      - city_health has rows AND this slug's status is 'Degraded'
        or 'Fail', OR
      - the slug isn't in city_health at all (means the daily cron
        ran but didn't see this slug as 'active' in prod_cities).

    The False case for "not in city_health" is intentional: if the
    cron ran and didn't enumerate this slug, the slug isn't in
    `prod_cities WHERE status='active'` — i.e. it's deactivated or
    a typo. We don't promote it.
    """
    if not slug:
        return False
    if not has_city_health_data():
        return True  # fresh-deploy fail-open
    try:
        conn = permitdb.get_connection()
        row = conn.execute(
            "SELECT status FROM city_health WHERE city_slug = ? LIMIT 1",
            (slug,),
        ).fetchone()
    except Exception:
        return True  # DB error — fail-open
    if row is None:
        return False  # cron ran but didn't see this slug
    status = row[0] if not hasattr(row, 'keys') else row['status']
    return status == 'Pass'


def get_sellable_cities():
    """Return the set of slugs whose city_health.status == 'Pass'.

    Used by sitemap generator + signup picker + ad geo feed for bulk
    filtering. Returns empty set on any DB error so callers can detect
    cold-start (and combine with has_city_health_data() for the fail-
    open behavior).
    """
    try:
        conn = permitdb.get_connection()
        rows = conn.execute(
            "SELECT city_slug FROM city_health WHERE status = 'Pass'"
        ).fetchall()
        return {
            (r[0] if not hasattr(r, 'keys') else r['city_slug'])
            for r in rows
        }
    except Exception:
        return set()


def filter_to_sellable(slugs):
    """Filter an iterable of slugs to just the sellable ones, with
    fail-open during cold start. Convenience wrapper around the two
    helpers above."""
    if not has_city_health_data():
        return list(slugs)
    sellable = get_sellable_cities()
    return [s for s in slugs if s in sellable]
