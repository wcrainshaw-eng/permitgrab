"""V479 stats cache.

Pre-computes ALL aggregate stats that templates need (global counts,
state grid, per-city counts, top-trades / top-contractors / recent
activity per city) and stores them in a single dict.

Templates and route handlers ONLY read from this cache via
get_cached_stats(). They never run aggregate queries themselves.

The daemon calls refresh_stats_cache(conn) at the end of every
collection cycle. The admin endpoint /api/admin/refresh-stats can
also force a refresh.

A JSON snapshot is written to disk so the cache survives worker
restarts — the next worker reads it on first call to
get_cached_stats() (no DB hit, no cold-start spike).
"""

import json
import os
import time
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# Module-level cache. Read by request handlers (cheap dict lookup).
_STATS_CACHE = {}

# Persisted to /var/data on Render so a fresh worker can start with
# warm stats without waiting for the next daemon cycle.
_CACHE_FILE = os.path.join(
    os.environ.get('DATA_DIR', '/var/data'), 'stats_cache.json'
)
_CACHE_TIMESTAMP = 0


def _default_stats():
    """Sensible empty defaults. Templates render fine with zeros."""
    return {
        'global': {
            'total_permits': 0,
            'total_cities': 0,
            'total_states': 0,
            'total_value': 0,
            'total_contractors': 0,
            'total_phones': 0,
        },
        'state_counts': {},   # {state_abbrev: city_count}
        'city_stats': {},     # {city_slug: {permits, contractors, phones, violations, owners}}
        'city_top_trades': {},      # {city_slug: [{trade, count}, ...top 5]}
        'city_top_contractors': {}, # {city_slug: [{name, count}, ...top 5]}
        'city_recent_count': {},    # {city_slug: permits_last_7d}
        'updated_at': None,
    }


def get_cached_stats():
    """Return cached stats. Loads from disk on first call. NEVER queries DB."""
    global _STATS_CACHE
    if not _STATS_CACHE:
        _load_from_disk()
    return _STATS_CACHE if _STATS_CACHE else _default_stats()


def _load_from_disk():
    global _STATS_CACHE
    try:
        if os.path.exists(_CACHE_FILE):
            with open(_CACHE_FILE, 'r') as f:
                _STATS_CACHE = json.load(f)
            logger.info(f"[V479] loaded stats cache from {_CACHE_FILE}: "
                        f"{len(_STATS_CACHE.get('city_stats', {}))} cities")
    except Exception as e:
        logger.error(f"[V479] failed to load stats cache from disk: {e}")


def _save_to_disk(stats):
    try:
        # Atomic write — temp file + rename so a partial write can't
        # corrupt the cache visible to other workers.
        tmp = _CACHE_FILE + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(stats, f)
        os.replace(tmp, _CACHE_FILE)
    except Exception as e:
        logger.error(f"[V479] failed to save stats cache: {e}")


def refresh_stats_cache(conn):
    """Run all aggregate queries ONCE and update the cache.

    Called from the daemon thread after each collection cycle and from
    /api/admin/refresh-stats. Never call this from a request handler
    other than the admin one — the queries are expensive (1.28M-row
    GROUP BY) and will time out a request thread.
    """
    global _STATS_CACHE, _CACHE_TIMESTAMP
    started = time.time()
    try:
        stats = _default_stats()
        stats['updated_at'] = datetime.utcnow().isoformat()

        # ---- Global counts (6 single-aggregate queries) ----
        try:
            stats['global']['total_permits'] = (
                conn.execute("SELECT COUNT(*) FROM permits").fetchone()[0] or 0
            )
        except Exception as e:
            logger.error(f"[V479] total_permits failed: {e}")
        try:
            stats['global']['total_cities'] = (
                conn.execute(
                    "SELECT COUNT(*) FROM prod_cities "
                    "WHERE status = 'active' AND total_permits > 0"
                ).fetchone()[0] or 0
            )
        except Exception as e:
            logger.error(f"[V479] total_cities failed: {e}")
        try:
            stats['global']['total_states'] = (
                conn.execute(
                    "SELECT COUNT(DISTINCT state) FROM prod_cities "
                    "WHERE status = 'active' AND total_permits > 0"
                ).fetchone()[0] or 0
            )
        except Exception as e:
            logger.error(f"[V479] total_states failed: {e}")
        try:
            row = conn.execute(
                "SELECT SUM(estimated_cost) FROM permits "
                "WHERE estimated_cost IS NOT NULL AND estimated_cost > 0"
            ).fetchone()
            stats['global']['total_value'] = row[0] or 0
        except Exception as e:
            logger.error(f"[V479] total_value failed: {e}")
        try:
            stats['global']['total_contractors'] = (
                conn.execute("SELECT COUNT(*) FROM contractor_profiles").fetchone()[0] or 0
            )
        except Exception as e:
            logger.error(f"[V479] total_contractors failed: {e}")
        try:
            stats['global']['total_phones'] = (
                conn.execute(
                    "SELECT COUNT(*) FROM contractor_profiles "
                    "WHERE phone IS NOT NULL AND phone <> ''"
                ).fetchone()[0] or 0
            )
        except Exception as e:
            logger.error(f"[V479] total_phones failed: {e}")

        # ---- State grid ----
        try:
            for r in conn.execute(
                "SELECT state, COUNT(*) AS cnt FROM prod_cities "
                "WHERE status = 'active' AND total_permits > 0 "
                "AND state IS NOT NULL AND state <> '' "
                "GROUP BY state ORDER BY cnt DESC"
            ).fetchall():
                stats['state_counts'][r[0]] = r[1]
        except Exception as e:
            logger.error(f"[V479] state_counts failed: {e}")

        # ---- Per-city aggregates (single-pass each) ----
        city_permits = {}
        city_contractors = {}
        city_phones = {}
        city_violations = {}
        city_owners = {}
        try:
            for r in conn.execute(
                "SELECT source_city_key, COUNT(*) FROM permits "
                "WHERE source_city_key IS NOT NULL "
                "GROUP BY source_city_key"
            ).fetchall():
                city_permits[r[0]] = r[1]
        except Exception as e:
            logger.error(f"[V479] city_permits failed: {e}")
        try:
            for r in conn.execute(
                "SELECT source_city_key, COUNT(*) FROM contractor_profiles "
                "WHERE source_city_key IS NOT NULL "
                "GROUP BY source_city_key"
            ).fetchall():
                city_contractors[r[0]] = r[1]
        except Exception as e:
            logger.error(f"[V479] city_contractors failed: {e}")
        try:
            for r in conn.execute(
                "SELECT source_city_key, COUNT(*) FROM contractor_profiles "
                "WHERE source_city_key IS NOT NULL "
                "AND phone IS NOT NULL AND phone <> '' "
                "GROUP BY source_city_key"
            ).fetchall():
                city_phones[r[0]] = r[1]
        except Exception as e:
            logger.error(f"[V479] city_phones failed: {e}")
        try:
            for r in conn.execute(
                "SELECT source_city_key, COUNT(*) FROM violations "
                "WHERE source_city_key IS NOT NULL "
                "GROUP BY source_city_key"
            ).fetchall():
                city_violations[r[0]] = r[1]
        except Exception as e:
            logger.error(f"[V479] city_violations failed: {e}")
        try:
            for r in conn.execute(
                "SELECT LOWER(REPLACE(REPLACE(city, ' ', '-'), '.', '')) AS slug, "
                "COUNT(*) FROM property_owners "
                "WHERE city IS NOT NULL AND city <> '' "
                "GROUP BY slug"
            ).fetchall():
                city_owners[r[0]] = r[1]
        except Exception as e:
            logger.error(f"[V479] city_owners failed: {e}")

        # Merge per-city dicts
        all_slugs = (set(city_permits) | set(city_contractors)
                     | set(city_phones) | set(city_violations))
        for slug in all_slugs:
            if not slug:
                continue
            stats['city_stats'][slug] = {
                'permits': city_permits.get(slug, 0),
                'contractors': city_contractors.get(slug, 0),
                'phones': city_phones.get(slug, 0),
                'violations': city_violations.get(slug, 0),
                'owners': city_owners.get(slug, 0),
            }

        # ---- Per-city insights ----
        # Top 5 trades per city (one big sorted scan, then trim in Python)
        try:
            trade_rows = conn.execute(
                "SELECT source_city_key, trade_category, COUNT(*) AS cnt "
                "FROM contractor_profiles "
                "WHERE trade_category IS NOT NULL AND trade_category <> '' "
                "AND source_city_key IS NOT NULL "
                "GROUP BY source_city_key, trade_category "
                "ORDER BY source_city_key, cnt DESC"
            ).fetchall()
            trades_by_city = {}
            for r in trade_rows:
                slug = r[0]
                bucket = trades_by_city.setdefault(slug, [])
                if len(bucket) < 5:
                    bucket.append({'trade': r[1], 'count': r[2]})
            stats['city_top_trades'] = trades_by_city
        except Exception as e:
            logger.error(f"[V479] city_top_trades failed: {e}")

        # Top 5 contractors per city (last 180 days)
        try:
            con_rows = conn.execute(
                "SELECT source_city_key, contractor_name, COUNT(*) AS cnt "
                "FROM permits "
                "WHERE contractor_name IS NOT NULL AND contractor_name <> '' "
                "AND source_city_key IS NOT NULL "
                "AND date >= date('now', '-180 days') "
                "GROUP BY source_city_key, contractor_name "
                "ORDER BY source_city_key, cnt DESC"
            ).fetchall()
            con_by_city = {}
            for r in con_rows:
                slug = r[0]
                bucket = con_by_city.setdefault(slug, [])
                if len(bucket) < 5:
                    bucket.append({'name': r[1], 'count': r[2]})
            stats['city_top_contractors'] = con_by_city
        except Exception as e:
            logger.error(f"[V479] city_top_contractors failed: {e}")

        # Recent activity (last 7 days) per city
        try:
            for r in conn.execute(
                "SELECT source_city_key, COUNT(*) AS cnt "
                "FROM permits "
                "WHERE source_city_key IS NOT NULL "
                "AND date >= date('now', '-7 days') "
                "GROUP BY source_city_key"
            ).fetchall():
                stats['city_recent_count'][r[0]] = r[1]
        except Exception as e:
            logger.error(f"[V479] city_recent_count failed: {e}")

        # Commit cache
        _STATS_CACHE = stats
        _CACHE_TIMESTAMP = time.time()
        _save_to_disk(stats)
        elapsed = time.time() - started
        logger.info(
            f"[V479] stats cache refreshed in {elapsed:.1f}s — "
            f"{len(stats['city_stats'])} cities, "
            f"{stats['global']['total_permits']:,} permits"
        )
        print(
            f"[{datetime.now()}] [V479] stats cache refreshed in {elapsed:.1f}s "
            f"— {len(stats['city_stats'])} cities", flush=True,
        )
    except Exception as e:
        logger.error(f"[V479] refresh_stats_cache top-level failed: {e}")
        # Leave the existing cache intact — stale > empty.


def get_city_stats(slug):
    """Convenience accessor for templates: returns the per-city sub-dict."""
    return get_cached_stats().get('city_stats', {}).get(slug, {
        'permits': 0, 'contractors': 0, 'phones': 0,
        'violations': 0, 'owners': 0,
    })
