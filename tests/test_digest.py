"""V524 regression tests. These pin two prior bugs so they don't
come back on the next refactor.

  - test_email_scheduler_thread_spawned_with_correct_name
    pins V475: V471 PR4 silently dropped the email_scheduler thread
    spawn, and the V473b corrective restored only 2 of 3 threads,
    missing this one. Daily digests stopped firing on 2026-04-30
    until V475 corrected it. The thread name 'email_scheduler' is
    the contract that routes/admin.py's /api/admin/digest/status
    relies on; if it ever changes (rename, accidental shadow), this
    test fails.

  - test_dup_fire_skipped_when_digest_log_row_exists
    pins V515: 2026-05-05 sent two digests 27 min apart. Worker A
    inserted digest_log row 36 at 11:02 UTC; Worker B respawned and
    fired again at 11:29 because last_digest_date in-memory was
    None. The fix queries digest_log directly at the fire decision.
    This test simulates the race: digest_log has today's row,
    in-memory state would say "fire", dedup MUST return True.

  - test_bootstrap_seen_dates_seeds_today_when_system_state_matches
    sanity check on the V276 bootstrap helper that V515 depends on.
    If the bootstrap silently breaks (e.g. row_factory mismatch
    returning tuples when the code expects dicts) the morning re-fire
    happens because last_digest_date stays None.
"""
import sqlite3
import time as _time
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo


def _make_db_with_digest_log():
    """In-memory SQLite mirror of the relevant production schema."""
    conn = sqlite3.connect(':memory:')
    conn.execute("""
        CREATE TABLE digest_log (
            id INTEGER PRIMARY KEY,
            recipient_email TEXT,
            permits_count INTEGER DEFAULT 0,
            status TEXT,
            error_message TEXT,
            sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE system_state (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    return conn


def test_email_scheduler_thread_spawned_with_correct_name():
    """V475 regression: thread name must be 'email_scheduler'.

    Mock the loop body so the test doesn't actually run the
    scheduler — we only care that the spawn happens, the thread is
    daemonic, and the name is exact. The name is what
    routes/admin.py later asserts against to verify the thread is
    alive.
    """
    import digest.scheduler as scheduler_mod
    # Reset the module-global so a previous test invocation doesn't
    # short-circuit start_thread()'s idempotency check.
    scheduler_mod._thread = None

    with patch.object(scheduler_mod, 'schedule_email_tasks', lambda: None):
        t = scheduler_mod.start_thread()
        assert t is not None
        assert t.name == 'email_scheduler', (
            f"V475 regression: thread name is {t.name!r}, must be "
            "'email_scheduler' for routes/admin.py's alive check."
        )
        assert t.daemon is True

        # Idempotent — second call either returns the same live thread
        # or, if the no-op body finished and the first thread died,
        # spawns a fresh one. Either way the name contract holds.
        # Give the no-op a moment to complete so we exercise the
        # respawn branch.
        for _ in range(20):
            if not t.is_alive():
                break
            _time.sleep(0.01)
        t2 = scheduler_mod.start_thread()
        assert t2 is not None
        assert t2.name == 'email_scheduler'

    # Cleanup so re-running the suite doesn't leak state.
    scheduler_mod._thread = None


def test_dup_fire_skipped_when_digest_log_row_exists():
    """V515 regression: digest_log dedup must short-circuit the fire.

    Simulate the 2026-05-05 race: Worker A wrote digest_log row at
    11:02, then died. Worker B respawns at 11:25 with
    last_digest_date=None (in-memory state lost). The dedup helper
    queries digest_log directly and MUST report 'already fired'
    so Worker B does not re-call send_daily_digest().
    """
    from digest.dedup import digest_already_fired_today

    conn = _make_db_with_digest_log()
    # Worker A's row from earlier this morning
    conn.execute(
        "INSERT INTO digest_log (recipient_email, permits_count, status) "
        "VALUES ('scheduled', 12, 'sent')"
    )
    conn.commit()

    # Worker B's perspective: same calendar day, no in-memory dedup,
    # but durable digest_log has the row.
    assert digest_already_fired_today(conn) is True, (
        "V515 regression: digest_log row is present for today but "
        "dedup helper says not fired — would re-fire and dup."
    )

    # Negative case: empty digest_log on a fresh morning → not fired.
    conn2 = _make_db_with_digest_log()
    assert digest_already_fired_today(conn2) is False, (
        "Empty digest_log must NOT report 'already fired' or "
        "the morning digest will never go out."
    )

    # Recipient/status filter must be exact: a manual_trigger row
    # or a failed scheduled row should NOT count as 'already fired'.
    conn3 = _make_db_with_digest_log()
    conn3.execute(
        "INSERT INTO digest_log (recipient_email, permits_count, status) "
        "VALUES ('manual@example.com', 0, 'manual_trigger')"
    )
    conn3.execute(
        "INSERT INTO digest_log (recipient_email, status, error_message) "
        "VALUES ('scheduled', 'failed', 'SMTP timeout')"
    )
    conn3.commit()
    assert digest_already_fired_today(conn3) is False, (
        "Only ('scheduled', 'sent') counts as 'already fired today'; "
        "manual triggers or failed sends must not block the daily fire."
    )


def test_bootstrap_seen_dates_seeds_today_when_system_state_matches():
    """V276 / V515 regression: bootstrap must read system_state and
    seed today's date when digest_last_success matches today.

    If the bootstrap silently breaks (e.g. row_factory mismatch
    returning tuples when the code expects dicts) and last_digest_date
    stays None, a respawning worker fires a duplicate digest. The
    helper handles both tuple and dict rows.
    """
    from digest.dedup import bootstrap_seen_dates

    conn = _make_db_with_digest_log()
    et = ZoneInfo('America/New_York')
    today = datetime.now(et).date()
    conn.execute(
        "INSERT INTO system_state (key, value) VALUES "
        "('digest_last_success', ?)",
        (datetime.now().isoformat(),),
    )
    conn.commit()

    seeded = bootstrap_seen_dates(conn, et, today)
    assert seeded['digest'] == today, (
        f"V276/V515 regression: bootstrap returned {seeded['digest']!r} "
        f"for digest, expected {today!r}. The morning dedup will fail "
        "and a respawning worker will re-fire."
    )
    assert seeded['lifecycle'] is None
    assert seeded['onboarding'] is None

    # Stale system_state row (yesterday) must NOT seed today.
    conn2 = _make_db_with_digest_log()
    yesterday_iso = datetime(2024, 1, 1, 7, 0, 0).isoformat()
    conn2.execute(
        "INSERT INTO system_state (key, value) VALUES "
        "('digest_last_success', ?)",
        (yesterday_iso,),
    )
    conn2.commit()
    seeded2 = bootstrap_seen_dates(conn2, et, today)
    assert seeded2['digest'] is None, (
        "Stale digest_last_success (different day) must NOT seed "
        "today's date or we'll skip a real digest."
    )
