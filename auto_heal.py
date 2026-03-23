"""
PermitGrab V12.54 — Auto Heal
Self-healing for broken endpoints.
Fixes 404s, handles 403s, adjusts timeouts, repairs stale field maps.
"""

import db as permitdb
from city_source_db import update_source_status, reset_failure
from auto_discover import (
    search_socrata_catalog, get_socrata_columns, generate_field_map,
    fetch_sample, validate_sample, auto_fix_field_map
)
import json
import time


def run_self_healing():
    """Check all active sources with consecutive failures and try to fix them."""
    conn = permitdb.get_connection()

    # Sources with 3+ consecutive failures
    failing = conn.execute("""
        SELECT * FROM city_sources
        WHERE status='active' AND consecutive_failures >= 3
        ORDER BY consecutive_failures DESC
    """).fetchall()

    if not failing:
        return

    print(f"[SelfHeal] Checking {len(failing)} failing sources...")

    for row in failing:
        source = dict(row)
        if source.get('field_map') and isinstance(source['field_map'], str):
            try:
                source['field_map'] = json.loads(source['field_map'])
            except (json.JSONDecodeError, TypeError):
                source['field_map'] = {}

        reason = source.get('last_failure_reason', '') or ''
        key = source['source_key']
        failures = source['consecutive_failures']

        # Auto-disable after 10 failures
        if failures >= 10:
            update_source_status(key, 'disabled', f"auto_disabled_after_{failures}_failures")
            print(f"[SelfHeal] Disabled {key}: {failures} consecutive failures")
            continue

        # Try to fix based on failure reason
        if '404' in reason:
            fix_404(source)
        elif '403' in reason:
            fix_403(source)
        elif 'timeout' in reason.lower():
            fix_timeout(source)
        elif reason == '0_permits' or 'normalized' in reason:
            fix_field_map(source)

        time.sleep(1)

    # Re-enable sources disabled 30+ days ago (weekly retry)
    disabled = conn.execute("""
        SELECT * FROM city_sources
        WHERE status='disabled'
        AND updated_at < datetime('now', '-30 days')
        LIMIT 10
    """).fetchall()

    for row in disabled:
        source = dict(row)
        print(f"[SelfHeal] Retrying disabled source: {source['source_key']}")
        sample = fetch_sample(source['endpoint'], source['platform'], limit=5)
        if sample:
            reset_failure(source['source_key'])
            update_source_status(source['source_key'], 'active', 'auto_re_enabled')
            print(f"[SelfHeal] Re-enabled {source['source_key']}")
        time.sleep(1)


def fix_404(source):
    """Endpoint returned 404. Try to find dataset at a new URL."""
    dataset_id = source.get('dataset_id', '')
    if not dataset_id:
        return

    # Search Socrata catalog for same dataset_id
    results, _ = search_socrata_catalog(dataset_id, limit=5)
    for r in results:
        resource = r.get('resource', {})
        if resource.get('id') == dataset_id:
            domain = r.get('metadata', {}).get('domain', '')
            new_endpoint = f"https://{domain}/resource/{dataset_id}.json"
            if new_endpoint != source['endpoint']:
                # Test new endpoint
                sample = fetch_sample(new_endpoint, 'socrata', limit=5)
                if sample:
                    conn = permitdb.get_connection()
                    conn.execute(
                        "UPDATE city_sources SET endpoint=?, consecutive_failures=0, updated_at=datetime('now') WHERE source_key=?",
                        (new_endpoint, source['source_key'])
                    )
                    conn.commit()
                    print(f"[SelfHeal] Fixed 404 for {source['source_key']}: new URL {new_endpoint}")
                    return
    print(f"[SelfHeal] Could not fix 404 for {source['source_key']}")


def fix_403(source):
    """Endpoint returned 403. Not much we can do except wait."""
    print(f"[SelfHeal] 403 for {source['source_key']} — will auto-disable at 10 failures")


def fix_timeout(source):
    """Reduce limit_per_page to reduce response size."""
    current_limit = source.get('limit_per_page', 2000)
    new_limit = max(100, current_limit // 2)
    if new_limit != current_limit:
        conn = permitdb.get_connection()
        conn.execute(
            "UPDATE city_sources SET limit_per_page=?, updated_at=datetime('now') WHERE source_key=?",
            (new_limit, source['source_key'])
        )
        conn.commit()
        print(f"[SelfHeal] Reduced limit for {source['source_key']}: {current_limit} -> {new_limit}")


def fix_field_map(source):
    """Field map may be stale. Re-detect from fresh sample."""
    sample = fetch_sample(source['endpoint'], source['platform'], limit=10)
    if not sample:
        return

    new_field_map = auto_fix_field_map(sample)
    if new_field_map and validate_sample(sample, new_field_map):
        conn = permitdb.get_connection()
        conn.execute(
            "UPDATE city_sources SET field_map=?, consecutive_failures=0, updated_at=datetime('now') WHERE source_key=?",
            (json.dumps(new_field_map), source['source_key'])
        )
        conn.commit()
        print(f"[SelfHeal] Fixed field_map for {source['source_key']}")
