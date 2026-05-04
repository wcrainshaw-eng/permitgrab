"""V492: city-page stats system_state cache.

Mirrors the V488 IRONCLAD pt.4 (V485 B2 deferred) persona-stats pattern.
AdsBot crawls /permits/<state>/<city> and times out when the live
aggregate COUNT queries pile up behind the WAL lock — Google flags
"Destination not working" and auto-pauses the ad. V484 hit this for
the persona pages; V492 fixes the same pattern for city pages.

Three layers (read path):
  1. process-memory cache (5 min TTL) — fastest, ~1µs
  2. system_state row (4 hr TTL) — survives process restarts, ~1ms
  3. compute synchronously — first-deploy-ever fallback only

Worker.secondary_loop calls refresh_city_stats_cache_all() every 4 hr
to keep system_state warm for ALL active cities. Web reads only.

Per-city blob format (stored as JSON in system_state.value):
  {
    "slug": "buffalo-ny",
    "city_name": "Buffalo",
    "state": "NY",
    "profiles": 1122,
    "phones": 87,
    "owners": 59000,
    "violations": 2076,
    "permits": 5400,
    "newest_date": "2026-04-30",
    "computed_at": 1714826400
  }
"""
import json
import time

from server import permitdb


_CITY_STATS_MEM_CACHE = {}  # {slug: {'data': {...}, 'ts': epoch}}
_CITY_STATS_MEM_TTL = 300   # 5 min in-process


def _cache_key(slug):
    return f"city_stats:{slug}"


def _compute_city_stats(slug, conn=None):
    """Live aggregate queries. Called by worker refresh and by the
    fallback path on first hit of a fresh deploy. Defensive — every
    block is independently try/excepted so one slow query doesn't
    poison the whole stats blob.
    """
    if conn is None:
        conn = permitdb.get_connection()
    out = {
        'slug': slug,
        'computed_at': int(time.time()),
        'profiles': 0,
        'phones': 0,
        'owners': 0,
        'violations': 0,
        'permits': 0,
        'newest_date': None,
        'city_name': None,
        'state': None,
    }

    # Profiles + phones
    try:
        row = conn.execute(
            "SELECT COUNT(*), "
            "SUM(CASE WHEN phone IS NOT NULL AND phone <> '' THEN 1 ELSE 0 END) "
            "FROM contractor_profiles WHERE source_city_key = ?",
            (slug,)
        ).fetchone()
        if row:
            out['profiles'] = row[0] or 0
            out['phones'] = row[1] or 0
    except Exception as e:
        print(f"[V492] profiles count failed for {slug}: {e}", flush=True)

    # Owners — needs city + state from prod_cities first, then a
    # case-insensitive match against property_owners.city
    try:
        row = conn.execute(
            "SELECT city, state FROM prod_cities WHERE city_slug = ? LIMIT 1",
            (slug,)
        ).fetchone()
        if row:
            city_name = row[0] if not isinstance(row, dict) else row['city']
            state = row[1] if not isinstance(row, dict) else row['state']
            out['city_name'] = city_name
            out['state'] = state
            try:
                r2 = conn.execute(
                    "SELECT COUNT(*) FROM property_owners "
                    "WHERE LOWER(city) = LOWER(?) AND state = ?",
                    (city_name, state)
                ).fetchone()
                out['owners'] = r2[0] if r2 else 0
            except Exception as e:
                print(f"[V492] owners count failed for {slug}: {e}", flush=True)
    except Exception as e:
        print(f"[V492] prod_cities lookup failed for {slug}: {e}", flush=True)

    # Violations
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM violations WHERE source_city_key = ?",
            (slug,)
        ).fetchone()
        out['violations'] = row[0] if row else 0
    except Exception as e:
        print(f"[V492] violations count failed for {slug}: {e}", flush=True)

    # Permit count + freshness (newest filing_date)
    try:
        row = conn.execute(
            "SELECT COUNT(*), MAX(filing_date) "
            "FROM permits WHERE source_city_key = ?",
            (slug,)
        ).fetchone()
        if row:
            out['permits'] = row[0] or 0
            out['newest_date'] = row[1]
    except Exception as e:
        print(f"[V492] permits count failed for {slug}: {e}", flush=True)

    return out


def refresh_city_stats_cache(slug):
    """Refresh ONE city. Used by:
      - worker per-city loop
      - admin force-refresh endpoint
      - read-path layer 3 fallback
    """
    data = _compute_city_stats(slug)
    try:
        permitdb.set_system_state(_cache_key(slug), json.dumps(data))
        _CITY_STATS_MEM_CACHE[slug] = {'data': data, 'ts': time.time()}
    except Exception as e:
        print(f"[V492] city_stats cache write failed for {slug}: {e}",
              flush=True)
    return data


def refresh_city_stats_cache_all(limit=None):
    """Refresh all active cities. Called by worker.secondary_loop every
    4 hr. limit=N for partial warmup at boot (V492 spec calls for
    limit=20 at startup, full pass next cycle).

    Returns (n_ok, n_total). Per-city try/except so one slow city
    doesn't kill the loop.
    """
    conn = permitdb.get_connection()
    q = (
        "SELECT city_slug FROM prod_cities "
        "WHERE status='active' AND city_slug IS NOT NULL"
    )
    if limit:
        q += f" ORDER BY total_permits DESC NULLS LAST LIMIT {int(limit)}"
    try:
        slugs = [r[0] if not isinstance(r, dict) else r['city_slug']
                 for r in conn.execute(q).fetchall()]
    except Exception as e:
        # Fallback to no NULLS LAST clause for SQLite versions without it
        try:
            q2 = (
                "SELECT city_slug FROM prod_cities "
                "WHERE status='active' AND city_slug IS NOT NULL"
            )
            if limit:
                q2 += f" ORDER BY total_permits DESC LIMIT {int(limit)}"
            slugs = [r[0] if not isinstance(r, dict) else r['city_slug']
                     for r in conn.execute(q2).fetchall()]
        except Exception as e2:
            print(f"[V492] active-cities query failed: {e2}", flush=True)
            return 0, 0

    n_ok = 0
    for s in slugs:
        try:
            refresh_city_stats_cache(s)
            n_ok += 1
        except Exception as e:
            print(f"[V492] refresh failed for {s}: {e}", flush=True)
    print(f"[V492] city_stats refresh: {n_ok}/{len(slugs)} ok",
          flush=True)
    return n_ok, len(slugs)


def get_city_stats(slug):
    """Read path used by city-page routes. Three layers:
      1. process memory (~1µs)
      2. system_state DB row (~1ms)
      3. live compute (~1-4s) — fallback only
    Always returns a dict (with zero values if compute fails).
    """
    now = time.time()

    # Layer 1: memory
    hit = _CITY_STATS_MEM_CACHE.get(slug)
    if hit and (now - hit['ts']) < _CITY_STATS_MEM_TTL:
        return hit['data']

    # Layer 2: system_state
    try:
        state = permitdb.get_system_state(_cache_key(slug))
        if state and state.get('value'):
            data = json.loads(state['value'])
            _CITY_STATS_MEM_CACHE[slug] = {'data': data, 'ts': now}
            return data
    except Exception as e:
        print(f"[V492] system_state read failed for {slug}: {e}",
              flush=True)

    # Layer 3: synchronous compute (cold-start fallback only)
    return refresh_city_stats_cache(slug)
