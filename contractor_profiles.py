"""V182 C1: Contractor intelligence — aggregates permit data into contractor_profiles.

Builds per-city contractor activity profiles from permits. Feeds the emblem
system (V182 PR2) and "top contractors" UI on city pages. Reuses the
normalize + enrichment helpers from contractor_enrichment.py.

Design:
  - Iterate prod_cities WHERE status='active' (guarantees garbage cities
    without a canonical prod_cities row are filtered out automatically).
  - Group permits via permits.prod_city_id FK (avoids source_city_key
    fragmentation — NYC's 'new_york'/'new-york'/None variants all
    collapse to the same canonical 'new-york-city' slug).
  - Skip non-contractor placeholder names (OWNER, HOMEOWNER, N/A, etc.)
    before aggregation.
  - UPSERT with ON CONFLICT DO UPDATE (Postgres + SQLite compatible; no
    INSERT OR REPLACE per V180 Golden Rule).
  - Per-city transactions — scales to 2K cities without a single giant lock.
"""

import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta

import db as permitdb
from contractor_enrichment import normalize_contractor_name


# Tokens that indicate the contractor_name field actually contains the
# owner/applicant/placeholder — skip these from profile aggregation.
# Matched against the lowercase normalized form.
# V182 PR2: expanded after PR1 deploy surfaced "to be bid" / "not applicable"
# as top contractors on Phoenix + NYC.
SKIP_NAMES = {
    'owner', 'home owner', 'homeowner', 'self', 'property owner',
    'tenant', 'applicant', 'na', 'n a', 'none', 'tbd', 'tba',
    'unknown', 'not available', 'see plans', 'various', 'null',
    'no contractor', 'same as owner', 'pending',
    # V182 PR2 additions
    'to be bid', 'not applicable', 'to be determined',
    'various contractors', 'no applicable', 'not assigned',
    'general contractor', 'contractor', 'no applicant',
    'not specified', 'not listed', 'default',
}


def is_license_number(normalized: str) -> bool:
    """Return True if the normalized name is purely digits (with optional
    hyphens/spaces). These are contractor license numbers, not company
    names — V182 PR2 renders them as "License #<raw>" in the UI.
    """
    if not normalized:
        return False
    stripped = normalized.replace('-', '').replace(' ', '')
    return bool(stripped) and stripped.isdigit()


def city_passes_public_filter(population, total_permits) -> bool:
    """V182 PR2: exclude bulk-misattribution cities from public rankings.

    A city appears in public rankings only when:
      - population >= 10,000, OR
      - total_permits <= 5 × population (permit volume is consistent
        with a small place's own activity, not a bulk-state dump).

    Why: Fenner NY (pop 1,900) shows 39K permits because NY state bulk
    datasets get misattributed to small centroids. Same for Taliaferro
    GA, Dickens TX, Lindley NY. Those shouldn't surface in rankings.
    Missing data falls back to True (benefit of doubt).
    """
    try:
        population = int(population or 0)
        total_permits = int(total_permits or 0)
    except (TypeError, ValueError):
        return True
    if population >= 10000:
        return True
    if population <= 0 or total_permits <= 0:
        return True
    return total_permits <= 5 * population


def _is_real_contractor(normalized: str) -> bool:
    """Return False for empty/placeholder/owner-type names.

    V242 P0.5 Part D: also rejects pure-numeric and short alphanumeric
    code values. Pre-V242 kept these so the UI could render them as
    "License #<raw>", but the 2026-04-22 audit found 16,356 such rows
    across Portland (AMANDA Customer RSNs — not license numbers),
    Fenner NY (NYC DOB IDs mis-bucketed), and NYC (DOB applicant
    license IDs, not businesses). Every enrichment cycle on those was
    fabricating random phone numbers against unresolvable IDs — 43%
    of all phones in the system were garbage. Reject upstream so no
    new garbage profiles get created.
    """
    if not normalized:
        return False
    if normalized in SKIP_NAMES:
        return False
    if len(normalized) < 3:
        return False
    # Pure-numeric (any length): "016662", "4656746"
    if normalized.isdigit():
        return False
    # Short alphanumeric code: "C558633", "R273248"
    import re as _re
    if _re.match(r'^[A-Z]\d{5,}$', normalized.upper()):
        return False
    return True


def _frequency_label(permits_90d: int) -> str:
    if permits_90d > 10:
        return 'high'
    if permits_90d >= 3:
        return 'medium'
    return 'low'


def _primary_area(addresses):
    """Pick the most common 5-digit zip or street-prefix token."""
    areas = []
    for a in addresses:
        if not a:
            continue
        m = re.search(r'\b(\d{5})\b', a)
        if m:
            areas.append(m.group(1))
            continue
        parts = a.split()
        if parts:
            areas.append(parts[0][:20])
    if not areas:
        return None
    return Counter(areas).most_common(1)[0][0]


def refresh_contractor_profiles(city_slug: str = None, now_utc: datetime = None) -> dict:
    """Refresh contractor_profiles for one city (by city_slug) or all active.

    Args:
        city_slug: specific prod_cities.city_slug to refresh; if None,
                   refresh all prod_cities WHERE status='active'.
        now_utc: reference timestamp for 90d/30d windows (for tests).

    Returns:
        {'cities_processed', 'profiles_upserted', 'skipped_rows'}
    """
    now = now_utc or datetime.utcnow()
    cutoff_90 = (now - timedelta(days=90)).strftime('%Y-%m-%d')
    cutoff_30 = (now - timedelta(days=30)).strftime('%Y-%m-%d')
    now_iso = now.strftime('%Y-%m-%d %H:%M:%S')

    conn = permitdb.get_connection()
    try:
        if city_slug:
            cities = conn.execute(
                "SELECT id, city, state, city_slug FROM prod_cities "
                "WHERE city_slug = ? AND status = 'active'",
                (city_slug,)
            ).fetchall()
        else:
            cities = conn.execute(
                "SELECT id, city, state, city_slug FROM prod_cities "
                "WHERE status = 'active'"
            ).fetchall()

        # V182 PR2: evict stale profiles for names now in SKIP_NAMES.
        # This makes re-running after a SKIP_NAMES expansion idempotent —
        # rows that used to aggregate are removed, not just not re-upserted.
        placeholders = ','.join('?' * len(SKIP_NAMES))
        if city_slug:
            deleted = conn.execute(
                f"DELETE FROM contractor_profiles "
                f"WHERE source_city_key = ? "
                f"AND contractor_name_normalized IN ({placeholders})",
                (city_slug, *SKIP_NAMES)
            ).rowcount
        else:
            deleted = conn.execute(
                f"DELETE FROM contractor_profiles "
                f"WHERE contractor_name_normalized IN ({placeholders})",
                tuple(SKIP_NAMES)
            ).rowcount
        conn.commit()
        if deleted:
            print(f"[V182 profiles] Evicted {deleted} stale placeholder profiles", flush=True)

        total_upserted = 0
        total_skipped = 0
        cities_processed = 0

        for city_row in cities:
            # sqlite3.Row supports both index and key access; dict does too.
            prod_city_id = city_row['id']
            city_name = city_row['city']
            state = city_row['state']
            slug = city_row['city_slug']

            rows = conn.execute("""
                SELECT contractor_name, trade_category, estimated_cost,
                       filing_date, address
                FROM permits
                WHERE prod_city_id = ?
                  AND contractor_name IS NOT NULL
                  AND contractor_name != ''
            """, (prod_city_id,)).fetchall()

            if not rows:
                continue

            # Group by normalized name.
            groups = defaultdict(list)
            for r in rows:
                raw = r['contractor_name']
                norm = normalize_contractor_name(raw)
                if not _is_real_contractor(norm):
                    total_skipped += 1
                    continue
                groups[norm].append(r)

            upserted = 0
            for norm, group_rows in groups.items():
                total_permits = len(group_rows)
                permits_90d = 0
                permits_30d = 0
                trades = Counter()
                costs = []
                addresses = []
                dates = []
                raw_names = Counter()

                for r in group_rows:
                    raw = r['contractor_name']
                    trade = r['trade_category']
                    cost = r['estimated_cost']
                    filed = r['filing_date']
                    addr = r['address']

                    raw_names[raw] += 1
                    trades[trade or 'General Construction'] += 1
                    if cost and cost > 0:
                        costs.append(cost)
                    if addr:
                        addresses.append(addr)
                    if filed:
                        dates.append(filed)
                        # SQLite stores ISO dates as TEXT; lexicographic
                        # compare is correct for 'YYYY-MM-DD' prefix.
                        if filed >= cutoff_90:
                            permits_90d += 1
                        if filed >= cutoff_30:
                            permits_30d += 1

                primary_trade = trades.most_common(1)[0][0] if trades else 'General Construction'
                trade_breakdown = json.dumps(dict(trades))
                avg_val = sum(costs) / len(costs) if costs else 0
                max_val = max(costs) if costs else 0
                total_val = sum(costs) if costs else 0
                area = _primary_area(addresses)
                first_date = min(dates) if dates else None
                last_date = max(dates) if dates else None
                is_active = 1 if (last_date and last_date >= cutoff_90) else 0
                freq = _frequency_label(permits_90d)
                display_raw = raw_names.most_common(1)[0][0]

                # UPSERT (Postgres + SQLite compatible; no INSERT OR REPLACE).
                conn.execute("""
                    INSERT INTO contractor_profiles (
                        contractor_name_raw, contractor_name_normalized,
                        source_city_key, city, state,
                        total_permits, permits_90d, permits_30d,
                        primary_trade, trade_breakdown,
                        avg_project_value, max_project_value, total_project_value,
                        primary_area, first_permit_date, last_permit_date,
                        is_active, permit_frequency, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(contractor_name_normalized, source_city_key) DO UPDATE SET
                        contractor_name_raw = excluded.contractor_name_raw,
                        city = excluded.city,
                        state = excluded.state,
                        total_permits = excluded.total_permits,
                        permits_90d = excluded.permits_90d,
                        permits_30d = excluded.permits_30d,
                        primary_trade = excluded.primary_trade,
                        trade_breakdown = excluded.trade_breakdown,
                        avg_project_value = excluded.avg_project_value,
                        max_project_value = excluded.max_project_value,
                        total_project_value = excluded.total_project_value,
                        primary_area = excluded.primary_area,
                        first_permit_date = excluded.first_permit_date,
                        last_permit_date = excluded.last_permit_date,
                        is_active = excluded.is_active,
                        permit_frequency = excluded.permit_frequency,
                        updated_at = excluded.updated_at
                """, (display_raw, norm, slug, city_name, state,
                      total_permits, permits_90d, permits_30d,
                      primary_trade, trade_breakdown,
                      avg_val, max_val, total_val,
                      area, first_date, last_date,
                      is_active, freq, now_iso))
                upserted += 1

            conn.commit()
            cities_processed += 1
            total_upserted += upserted
            print(f"[V182 profiles] {slug}: upserted {upserted} contractors "
                  f"(skipped_raw_names so far={total_skipped})", flush=True)

        return {
            'cities_processed': cities_processed,
            'profiles_upserted': total_upserted,
            'skipped_rows': total_skipped,
        }
    finally:
        conn.close()


def enrich_city_profiles(city_slug: str, max_cost: float = 25.0) -> dict:
    """Enrich contractor_profiles for one city via Google Places.

    V182: Gated on GOOGLE_PLACES_API_KEY env var. No-op if unset.

    Reuses contractor_enrichment.lookup_contractor for the actual API call,
    then persists phone/website/google_place_id back onto the profile row
    and writes one enrichment_log entry per attempt.
    """
    import os
    from contractor_enrichment import lookup_contractor

    api_key = os.environ.get('GOOGLE_PLACES_API_KEY')
    if not api_key:
        return {'status': 'skipped_no_api_key', 'enriched': 0, 'cost': 0.0}

    now_iso = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    cost_per_lookup = 0.017  # Google Places Text Search ~ $17 per 1000 at current pricing
    running_cost = 0.0
    enriched = 0

    conn = permitdb.get_connection()
    try:
        profiles = conn.execute("""
            SELECT id, contractor_name_raw, contractor_name_normalized, city, state
            FROM contractor_profiles
            WHERE source_city_key = ?
              AND enrichment_status = 'pending'
              AND is_active = 1
            ORDER BY total_permits DESC
        """, (city_slug,)).fetchall()

        for p in profiles:
            if running_cost >= max_cost:
                break
            result = lookup_contractor(p['contractor_name_raw'], p['city'] or '', p['state'] or '')
            running_cost += cost_per_lookup
            if result:
                conn.execute("""
                    UPDATE contractor_profiles
                    SET phone = ?, website = ?, google_place_id = ?,
                        enrichment_status = 'enriched', enriched_at = ?, updated_at = ?
                    WHERE id = ?
                """, (result.get('phone'), result.get('website'), result.get('place_id'),
                      now_iso, now_iso, p['id']))
                conn.execute("""
                    INSERT INTO enrichment_log (contractor_profile_id, source, status, cost, created_at)
                    VALUES (?, 'google_places', 'enriched', ?, ?)
                """, (p['id'], cost_per_lookup, now_iso))
                enriched += 1
            else:
                conn.execute("""
                    UPDATE contractor_profiles
                    SET enrichment_status = 'not_found', enriched_at = ?, updated_at = ?
                    WHERE id = ?
                """, (now_iso, now_iso, p['id']))
                conn.execute("""
                    INSERT INTO enrichment_log (contractor_profile_id, source, status, cost, created_at)
                    VALUES (?, 'google_places', 'not_found', ?, ?)
                """, (p['id'], cost_per_lookup, now_iso))
            # Rate limit: 5 req/sec
            import time
            time.sleep(0.2)

        conn.commit()
        return {
            'status': 'ok',
            'city_slug': city_slug,
            'enriched': enriched,
            'profiles_seen': len(profiles),
            'cost': round(running_cost, 4),
            'cost_cap': max_cost,
        }
    finally:
        conn.close()


def update_city_emblems() -> dict:
    """V182 PR2: recompute has_enrichment + has_violations flags on prod_cities.

    Called after profile refresh and after violation collection. Cheap
    (two GROUP BY queries + per-city UPDATE) — runs in well under 1s
    for 2K cities.

    has_permits is NOT stored — derived at render time from existing
    total_permits + newest_permit_date.
    """
    conn = permitdb.get_connection()
    try:
        # Reset flags first so cities that lost their data get downgraded
        conn.execute("UPDATE prod_cities SET has_enrichment = 0, has_violations = 0")
        # has_enrichment: any contractor_profiles row with is_active=1
        conn.execute("""
            UPDATE prod_cities SET has_enrichment = 1
            WHERE city_slug IN (
                SELECT DISTINCT source_city_key FROM contractor_profiles WHERE is_active = 1
            )
        """)
        # has_violations: any row in violations table for this city_slug
        # violations table uses prod_city_id — join through it.
        conn.execute("""
            UPDATE prod_cities SET has_violations = 1
            WHERE id IN (SELECT DISTINCT prod_city_id FROM violations WHERE prod_city_id IS NOT NULL)
        """)
        conn.commit()

        stats = conn.execute("""
            SELECT
                SUM(CASE WHEN has_enrichment = 1 THEN 1 ELSE 0 END) AS with_enrichment,
                SUM(CASE WHEN has_violations = 1 THEN 1 ELSE 0 END) AS with_violations,
                SUM(CASE WHEN total_permits > 0 THEN 1 ELSE 0 END) AS with_permits,
                COUNT(*) AS total
            FROM prod_cities WHERE status = 'active'
        """).fetchone()
        return {
            'cities_with_enrichment': stats['with_enrichment'] or 0,
            'cities_with_violations': stats['with_violations'] or 0,
            'cities_with_permits': stats['with_permits'] or 0,
            'active_cities_total': stats['total'] or 0,
        }
    finally:
        conn.close()
