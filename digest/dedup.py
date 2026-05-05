"""V524: digest dedup + logging helpers, extracted from server.py.

Three callables — all pure-ish (db conn passed in) so the regression
tests can exercise the production code path with an in-memory SQLite,
not a mocked daemon loop.

  - _log_digest(recipient, result, status):
      INSERT a row into digest_log. Used by routes/admin.py and the
      scheduler's success/error path.

  - digest_already_fired_today(conn) -> bool:
      V515 fix. Query digest_log for today's 'scheduled' sent row.
      The dup-fire bug (2026-05-05) happened because in-memory dedup
      raced worker respawn; durable digest_log is the ground truth.

  - bootstrap_seen_dates(conn, et_tz, today=None) -> dict:
      V276 fix. Seed last_digest_date / last_lifecycle_date /
      last_onboarding_date from system_state at thread start so a
      respawning worker doesn't re-fire emails that already went.
"""
from __future__ import annotations

from datetime import datetime, date as _date_t

import db as permitdb


def _log_digest(recipient, result, status):
    """V158: Log digest send attempt to digest_log table.

    Lifted from server.py:2439-2449 unchanged — same try/except
    fail-quiet behavior so callers (e.g. admin trigger endpoint)
    don't have to wrap.
    """
    try:
        conn = permitdb.get_connection()
        conn.execute("""
            INSERT INTO digest_log (recipient_email, permits_count, status, error_message)
            VALUES (?, 0, ?, ?)
        """, (recipient, status, str(result)[:500]))
        conn.commit()
    except Exception:
        pass  # Table may not exist yet


def digest_already_fired_today(conn) -> bool:
    """V515 dup-fire guard. True iff digest_log has today's
    `recipient_email='scheduled'` `status='sent'` row.

    Fail-open: on any DB error, return False so the caller fires
    rather than missing a digest. The dup-fire blast radius (1 extra
    email) is much smaller than the missed-digest blast radius
    (paying customer thinks the product is broken).

    The 2026-05-05 incident: Worker A inserted digest_log row 36 at
    11:02 UTC then died. Worker B respawned with last_digest_date=None
    (in-memory state lost) and re-fired at 11:29 UTC because the V276
    bootstrap missed the row in some race path. Querying digest_log
    directly at the fire decision point sidesteps the race entirely.
    """
    try:
        row = conn.execute(
            "SELECT 1 FROM digest_log "
            "WHERE date(sent_at) = date('now') "
            "AND recipient_email = 'scheduled' "
            "AND status = 'sent' "
            "LIMIT 1"
        ).fetchone()
        return row is not None
    except Exception as e:
        print(f"[digest.dedup] V515: digest_log dedup query failed (proceeding to fire): {e}")
        return False


def bootstrap_seen_dates(conn, et_tz, today=None):
    """V276 deploy-restart dedup. Read system_state.digest_last_success
    et al. and return `{'digest': date|None, 'lifecycle': date|None,
    'onboarding': date|None}`. Each value is `today` if the stored
    ISO timestamp is also today, else None.

    The scheduler uses this to seed last_*_date counters at thread
    start, so a worker that respawns mid-morning doesn't re-fire
    emails that went earlier the same day.
    """
    seeded = {'digest': None, 'lifecycle': None, 'onboarding': None}
    if today is None:
        today = datetime.now(et_tz).date()
    try:
        seed = {}
        for row in conn.execute(
            "SELECT key, value FROM system_state WHERE key IN "
            "('digest_last_success', 'lifecycle_last_success', 'onboarding_last_success')"
        ).fetchall():
            # Normalize sqlite3.Row / tuple / dict into (key, value)
            if isinstance(row, dict):
                seed[row['key']] = row['value']
            else:
                seed[row[0]] = row[1]
        key_to_var = (
            ('digest_last_success', 'digest'),
            ('lifecycle_last_success', 'lifecycle'),
            ('onboarding_last_success', 'onboarding'),
        )
        for sysstate_key, var_name in key_to_var:
            iso = seed.get(sysstate_key)
            if not iso:
                continue
            try:
                stored = datetime.fromisoformat(iso).date()
            except ValueError:
                continue
            if stored == today:
                seeded[var_name] = today
    except Exception as e:
        print(f"[digest.dedup] V276: bootstrap skipped ({e}); counters stay None")
    return seeded
