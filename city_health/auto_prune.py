"""V546 PR6: nightly auto-prune of dead working set.

Per V546 ground truth, the daemon's effective working set is much smaller
than the 1,761 active prod_cities row count. ~1,019 are bulk-source
recipients (status=active, source_type IS NULL) that don't get visited
per-city anyway, and a long tail of platform-typed rows have produced
zero permits in 60+ days because the source went dark or the field_map
never worked. Pruning those keeps the picker's eligible-pool honest and
makes daemon coverage math match reality.

Pause criteria (ALL three required):
  1. zero successful collections in the last 30 days, AND
  2. zero permits in the permits table for this slug, AND
  3. row was added to prod_cities more than 60 days ago

Hits all three → flip prod_cities.status from 'active' to 'paused' (the
schema CHECK constraint accepts 'active'/'paused'/'failed'/'pending';
'paused' is reversible by an explicit reactivate endpoint, while
'failed' tends to imply a transient retry-soon state).

Each pause is recorded in `inactivity_log` so the pattern is visible
to future audits ("did V546 prune anything that shouldn't have been
pruned?"). Reactivation just flips status back; the inactivity_log
row stays as audit trail.

Default dry_run=True. Wes calls /api/admin/v546/prune-inactive-cities
with dry_run=False once he's reviewed the candidates.
"""
from __future__ import annotations

import db as permitdb


_INACTIVITY_LOG_DDL = """
CREATE TABLE IF NOT EXISTS inactivity_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    city_slug TEXT NOT NULL,
    paused_at TEXT DEFAULT (datetime('now')),
    reason TEXT,
    permit_count INTEGER,
    days_since_last_success INTEGER,
    days_since_added INTEGER
)
"""


def ensure_inactivity_log_table(conn=None):
    """Idempotent — safe to call on every prune invocation."""
    if conn is None:
        conn = permitdb.get_connection()
    conn.execute(_INACTIVITY_LOG_DDL)
    conn.commit()


def find_prune_candidates(conn=None):
    """Return the list of (city_slug, permit_count, days_since_last_success,
    days_since_added) tuples eligible for prune.

    The query joins permits + prod_cities. SQLite-syntax (the prod DB
    is SQLite via the V514 pool path; Postgres path is currently
    inactive). If we move back to Postgres the date arithmetic needs
    re-translation.
    """
    if conn is None:
        conn = permitdb.get_connection()
    rows = conn.execute(
        """
        SELECT pc.city_slug,
               COALESCE((SELECT COUNT(*) FROM permits
                          WHERE source_city_key = pc.city_slug), 0) AS permit_count,
               CAST(julianday('now') -
                    julianday(COALESCE(pc.last_successful_collection,
                                       pc.added_at,
                                       '1970-01-01'))
                    AS INTEGER) AS days_since_last_success,
               CAST(julianday('now') - julianday(pc.added_at) AS INTEGER)
                    AS days_since_added
          FROM prod_cities pc
         WHERE pc.status = 'active'
           AND CAST(julianday('now') - julianday(pc.added_at) AS INTEGER) > 60
        """
    ).fetchall()

    candidates = []
    for r in rows:
        slug = r['city_slug'] if hasattr(r, 'keys') else r[0]
        permits = int((r['permit_count'] if hasattr(r, 'keys') else r[1]) or 0)
        d_last = r['days_since_last_success'] if hasattr(r, 'keys') else r[2]
        d_added = r['days_since_added'] if hasattr(r, 'keys') else r[3]
        if permits != 0:
            continue
        if d_last is None or int(d_last) <= 30:
            continue
        candidates.append({
            'city_slug': slug,
            'permit_count': permits,
            'days_since_last_success': int(d_last),
            'days_since_added': int(d_added) if d_added is not None else None,
        })
    return candidates


def prune_inactive_cities(dry_run=True, conn=None):
    """Pause all cities matching the V546 PR6 prune criteria. Returns
    a summary dict with `candidates`, `paused_count`, `dry_run`.

    With dry_run=True (default), nothing is mutated — only the candidate
    list is returned, so Wes can review before flipping anything.

    With dry_run=False, each candidate is updated to status='paused' +
    pause_reason='V546_auto_prune' and an inactivity_log row is inserted.
    """
    if conn is None:
        conn = permitdb.get_connection()
    ensure_inactivity_log_table(conn)
    candidates = find_prune_candidates(conn)
    if dry_run or not candidates:
        return {
            'dry_run': dry_run,
            'candidates': candidates,
            'paused_count': 0,
        }

    paused = 0
    for c in candidates:
        try:
            conn.execute(
                "UPDATE prod_cities SET status='paused', "
                "pause_reason='V546_auto_prune' "
                "WHERE city_slug=? AND status='active'",
                (c['city_slug'],),
            )
            conn.execute(
                "INSERT INTO inactivity_log "
                "(city_slug, reason, permit_count, "
                "days_since_last_success, days_since_added) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    c['city_slug'],
                    'V546_auto_prune: 0 successful collections in 30d, '
                    '0 permits in DB, wired >60d ago',
                    c['permit_count'],
                    c['days_since_last_success'],
                    c['days_since_added'],
                ),
            )
            paused += 1
        except Exception:
            # Don't let a single bad row poison the batch — keep going,
            # surface the count delta in the summary instead.
            continue
    conn.commit()
    return {
        'dry_run': False,
        'candidates': candidates,
        'paused_count': paused,
    }
