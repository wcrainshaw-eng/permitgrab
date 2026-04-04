"""
PermitGrab V12.54 — City Source Database Wrapper
Replaces city_configs.py imports with SQLite queries.
Falls back to city_configs.py if city_sources table is empty.
"""

import json
import db as permitdb


def get_active_cities():
    """Return list of active city-level sources as dicts.
    Falls back to city_configs.py if city_sources table is empty.
    V66: Fixed connection leak."""
    conn = permitdb.get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM city_sources WHERE status='active' AND mode='city'"
        ).fetchall()
        if rows:
            results = []
            for r in rows:
                d = dict(r)
                # Parse field_map from JSON string back to dict
                if d.get('field_map'):
                    try:
                        d['field_map'] = json.loads(d['field_map'])
                    except (json.JSONDecodeError, TypeError):
                        d['field_map'] = {}
                # V13.4: Bridge status/active key mismatch
                if 'active' not in d and d.get('status') == 'active':
                    d['active'] = True
                results.append(d)
            return results
        # Fallback: city_configs.py
        from city_configs import get_active_cities as _legacy_get
        return _legacy_get()
    finally:
        conn.close()


def get_city_config(source_key):
    """Get a single source config by key. Returns dict or None. V66: Fixed connection leak."""
    conn = permitdb.get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM city_sources WHERE source_key = ?", (source_key,)
        ).fetchone()
        if row:
            d = dict(row)
            if d.get('field_map'):
                try:
                    d['field_map'] = json.loads(d['field_map'])
                except (json.JSONDecodeError, TypeError):
                    d['field_map'] = {}
            # V13.4: Bridge status/active key mismatch — DB uses "status"
            # but fetch_permits/city_health check config.get("active", False)
            if 'active' not in d and d.get('status') == 'active':
                d['active'] = True
            return d

        # V17: Check discovered_sources table
        try:
            row = conn.execute(
                "SELECT * FROM discovered_sources WHERE source_key = ?", (source_key,)
            ).fetchone()
            if row:
                d = dict(row)
                if d.get('field_map'):
                    try:
                        d['field_map'] = json.loads(d['field_map'])
                    except (json.JSONDecodeError, TypeError):
                        d['field_map'] = {}
                if 'active' not in d and d.get('status') == 'active':
                    d['active'] = True
                return d
        except:
            pass  # Table may not exist yet

        # Fallback to legacy dict
        from city_configs import get_city_config as _legacy_get
        return _legacy_get(source_key)
    finally:
        conn.close()


def get_active_bulk_sources():
    """Return list of active bulk source keys.
    V17: Also includes sources from discovered_sources table.
    V33: Always merges DB sources with BULK_SOURCES dict so new entries
    added to city_configs.py are picked up immediately.
    V66: Fixed connection leak."""
    conn = permitdb.get_connection()
    try:
        source_keys = set()

        # Check city_sources table
        rows = conn.execute(
            "SELECT source_key FROM city_sources WHERE status='active' AND mode='bulk'"
        ).fetchall()
        for r in rows:
            source_keys.add(r['source_key'])

        # V17: Also check discovered_sources table
        try:
            rows = conn.execute(
                "SELECT source_key FROM discovered_sources WHERE status='active' AND mode='bulk'"
            ).fetchall()
            for r in rows:
                source_keys.add(r['source_key'])
        except:
            pass  # Table may not exist yet

        # V33: Always merge with BULK_SOURCES dict (don't just fallback)
        # This ensures new entries added to city_configs.py are picked up
        # even when the DB already has some bulk sources
        from city_configs import get_active_bulk_sources as _legacy_get
        for key in _legacy_get():
            source_keys.add(key)

        return list(source_keys)
    finally:
        conn.close()


def get_bulk_source_config(source_key):
    """Get bulk source config. Same as get_city_config but for bulk mode."""
    return get_city_config(source_key)  # Same table, just different mode


def upsert_city_source(source_dict):
    """Insert or update a city_sources row. V66: Fixed connection leak."""
    conn = permitdb.get_connection()
    try:
        field_map = source_dict.get('field_map')
        if isinstance(field_map, dict):
            field_map = json.dumps(field_map)
        conn.execute("""
            INSERT INTO city_sources (
                source_key, name, state, platform, mode, endpoint, dataset_id,
                field_map, date_field, city_field, limit_per_page, status,
                discovery_score, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(source_key) DO UPDATE SET
                name=excluded.name, endpoint=excluded.endpoint,
                field_map=excluded.field_map, date_field=excluded.date_field,
                status=excluded.status, discovery_score=excluded.discovery_score,
                updated_at=datetime('now')
        """, (
            source_dict['source_key'], source_dict['name'], source_dict.get('state'),
            source_dict['platform'], source_dict.get('mode', 'city'),
            source_dict['endpoint'], source_dict.get('dataset_id'),
            field_map, source_dict.get('date_field'), source_dict.get('city_field'),
            source_dict.get('limit_per_page', 2000), source_dict.get('status', 'active'),
            source_dict.get('discovery_score', 0)
        ))
        conn.commit()
    finally:
        conn.close()


def update_source_status(source_key, status, reason=None):
    """Update a source's status. V66: Fixed connection leak."""
    conn = permitdb.get_connection()
    try:
        conn.execute(
            "UPDATE city_sources SET status=?, last_failure_reason=?, updated_at=datetime('now') WHERE source_key=?",
            (status, reason, source_key)
        )
        conn.commit()
    finally:
        conn.close()


def increment_failure(source_key, reason):
    """Bump consecutive_failures counter. V66: Fixed connection leak."""
    conn = permitdb.get_connection()
    try:
        conn.execute("""
            UPDATE city_sources
            SET consecutive_failures = consecutive_failures + 1,
                last_failure_reason = ?,
                updated_at = datetime('now')
            WHERE source_key = ?
        """, (reason, source_key))
        conn.commit()
    finally:
        conn.close()


def reset_failure(source_key):
    """Reset failure counter after successful collection. V66: Fixed connection leak."""
    conn = permitdb.get_connection()
    try:
        conn.execute(
            "UPDATE city_sources SET consecutive_failures=0, last_failure_reason=NULL, updated_at=datetime('now') WHERE source_key=?",
            (source_key,)
        )
        conn.commit()
    finally:
        conn.close()


def record_collection(source_key, permit_count):
    """Record a successful collection. V66: Fixed connection leak."""
    conn = permitdb.get_connection()
    try:
        conn.execute("""
            UPDATE city_sources
            SET last_collected_at = datetime('now'),
                total_permits_collected = total_permits_collected + ?,
                updated_at = datetime('now')
            WHERE source_key = ?
        """, (permit_count, source_key))
        conn.commit()
    finally:
        conn.close()


def get_next_unsearched_county():
    """Get the highest-priority unsearched county. V66: Fixed connection leak."""
    conn = permitdb.get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM us_counties WHERE status='not_started' ORDER BY priority ASC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_next_unsearched_city():
    """Get the highest-priority unsearched city (not covered by county). V66: Fixed connection leak."""
    conn = permitdb.get_connection()
    try:
        row = conn.execute("""
            SELECT * FROM us_cities
            WHERE status='not_started'
            ORDER BY priority ASC
            LIMIT 1
        """).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_city_status(slug, status, reason=None):
    """Update a city's search status. V66: Fixed connection leak."""
    conn = permitdb.get_connection()
    try:
        conn.execute(
            "UPDATE us_cities SET status=?, status_reason=?, last_searched_at=datetime('now') WHERE slug=?",
            (status, reason, slug)
        )
        conn.commit()
    finally:
        conn.close()


def update_county_status(fips, status, reason=None):
    """Update a county's search status. V66: Fixed connection leak."""
    conn = permitdb.get_connection()
    try:
        conn.execute(
            "UPDATE us_counties SET status=?, status_reason=?, last_searched_at=datetime('now') WHERE fips=?",
            (status, reason, fips)
        )
        conn.commit()
    finally:
        conn.close()


def mark_county_cities_covered(county_fips, source_key):
    """Mark all cities in a county as covered_by_county. Returns count updated. V66: Fixed connection leak."""
    conn = permitdb.get_connection()
    try:
        cursor = conn.execute("""
            UPDATE us_cities
            SET status='covered_by_county', covered_by_source=?
            WHERE county_fips=? AND status IN ('not_started', 'no_data_available')
        """, (source_key, county_fips))
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()


def increment_search_attempts(slug):
    """Bump search_attempts counter for a city. V66: Fixed connection leak."""
    conn = permitdb.get_connection()
    try:
        conn.execute(
            "UPDATE us_cities SET search_attempts = search_attempts + 1 WHERE slug=?",
            (slug,)
        )
        conn.commit()
    finally:
        conn.close()


def count_unsearched_counties():
    """How many counties haven't been searched yet. V66: Fixed connection leak."""
    conn = permitdb.get_connection()
    try:
        row = conn.execute("SELECT COUNT(*) as cnt FROM us_counties WHERE status='not_started'").fetchone()
        return row['cnt'] if row else 0
    finally:
        conn.close()


def count_unsearched_cities():
    """How many cities haven't been searched yet. V66: Fixed connection leak."""
    conn = permitdb.get_connection()
    try:
        row = conn.execute("SELECT COUNT(*) as cnt FROM us_cities WHERE status='not_started'").fetchone()
        return row['cnt'] if row else 0
    finally:
        conn.close()


def log_discovery_run(run_type, stats):
    """Log a discovery run to the audit table.
    V12.60: Include started_at to prevent NOT NULL constraint failure."""
    conn = permitdb.get_connection()
    try:
        conn.execute("""
            INSERT INTO discovery_runs (run_type, started_at, completed_at,
                targets_searched, sources_found, permits_loaded, cities_activated, errors)
            VALUES (?, datetime('now'), datetime('now'), ?, ?, ?, ?, ?)
        """, (
            run_type, stats.get('targets_searched', 0), stats.get('sources_found', 0),
            stats.get('permits_loaded', 0), stats.get('cities_activated', 0),
            json.dumps(stats.get('errors', []))
        ))
        conn.commit()
    except Exception as e:
        print(f"[Autonomy] log_discovery_run error: {e}", flush=True)


def get_autonomy_status():
    """Get overall autonomy engine status for admin dashboard."""
    conn = permitdb.get_connection()
    city_counts = {}
    for row in conn.execute("SELECT status, COUNT(*) as cnt FROM us_cities GROUP BY status").fetchall():
        city_counts[row['status']] = row['cnt']

    total_cities = conn.execute("SELECT COUNT(*) as cnt FROM us_cities").fetchone()['cnt']
    total_sources = conn.execute("SELECT COUNT(*) as cnt FROM city_sources WHERE status='active'").fetchone()['cnt']
    total_permits = conn.execute("SELECT COUNT(*) as cnt FROM permits").fetchone()['cnt']
    searched = total_cities - city_counts.get('not_started', 0) - city_counts.get('searching', 0)
    pct = round((searched / total_cities) * 100, 1) if total_cities > 0 else 0

    return {
        'total_cities': total_cities,
        'by_status': city_counts,
        'search_progress_pct': pct,
        'total_sources': total_sources,
        'total_permits': total_permits,
        'engine_mode': 'search' if count_unsearched_counties() + count_unsearched_cities() > 0 else 'maintenance',
    }
