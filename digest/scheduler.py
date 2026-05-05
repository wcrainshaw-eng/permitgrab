"""V524: email scheduler thread, extracted from server.py:8765-9017.

Behavior is unchanged from the V515 version that was running in
production. The only structural change is that the inline V276
bootstrap block and the inline V515 dedup block now call helpers
in `digest.dedup` so the regression tests can exercise the same
code path that production runs.

Public API:
- DIGEST_STATUS: dict mutated in-place to expose thread state to
  routes/admin.py's /api/admin/digest/status endpoint.
- schedule_email_tasks(): the daemon loop. 4-min startup delay,
  then check every 5 min during 7-9 AM ET, every 30 min outside.
- start_thread(): idempotent spawn; the thread name MUST stay
  'email_scheduler' (see tests/test_digest.py — the V475 spawn-
  drop regression).
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path

import db as permitdb
from .dedup import bootstrap_seen_dates, digest_already_fired_today

# V78: Global tracking for email digest daemon thread. Mutated in-place
# by the scheduler loop; read by routes/admin.py /api/admin/digest/status.
DIGEST_STATUS = {
    'thread_started': None,
    'last_heartbeat': None,
    'last_digest_attempt': None,
    'last_digest_result': None,
    'last_digest_sent': 0,
    'last_digest_failed': 0,
    'thread_alive': False,
}


def schedule_email_tasks():
    """V12.53: Schedule all email tasks to run at specific times daily.

    - Daily digest: 7 AM ET (12:00 UTC)
    - Trial lifecycle check: 8 AM ET (13:00 UTC)
    - Onboarding nudges: 9 AM ET (14:00 UTC)

    V64: Added robust error logging, heartbeat, and crash recovery.
    V78: Added DIGEST_STATUS tracking and fixed timing (5-min checks during 7 AM window).
    V515: digest_log dedup query at fire decision point.
    V524: lifted from server.py; V515/V276 inline blocks refactored
    to call digest.dedup helpers so tests pin the same code path.
    """
    global DIGEST_STATUS

    # V78: Mark thread as started
    DIGEST_STATUS['thread_started'] = datetime.now().isoformat()
    DIGEST_STATUS['thread_alive'] = True

    # V64: Wrap imports in try/except to catch missing dependencies
    try:
        import pytz
        from email_alerts import send_daily_digest, check_trial_lifecycle, check_onboarding_nudges
    except ImportError as e:
        print(f"[{datetime.now()}] [CRITICAL] Email scheduler failed to import: {e}")
        import traceback
        traceback.print_exc()
        DIGEST_STATUS['thread_alive'] = False
        DIGEST_STATUS['last_digest_result'] = f'import_error: {e}'
        return  # Don't silently die — exit with error logged

    # V78: Auto-create subscribers.json if it doesn't exist
    try:
        subscribers_path = Path("/var/data/subscribers.json")
        if not subscribers_path.exists():
            var_data = Path("/var/data")
            if var_data.exists():
                default_subscribers = [
                    {
                        "email": "wcrainshaw@gmail.com",
                        "active": True,
                        "digest_cities": ["atlanta"],
                        "created_at": datetime.now().strftime("%Y-%m-%d"),
                    }
                ]
                subscribers_path.write_text(json.dumps(default_subscribers, indent=2))
                print(f"[{datetime.now()}] V78: Created subscribers.json with default subscriber")
            else:
                print(f"[{datetime.now()}] V78: /var/data not found - running locally, skipping subscribers.json creation")
        else:
            print(f"[{datetime.now()}] V78: subscribers.json already exists")
    except Exception as e:
        print(f"[{datetime.now()}] V78: Could not create subscribers.json: {e}")

    # V68: Wait 3 minutes for initial startup (increased from 2)
    # V515: bumped to 4 min so a worker that respawns inside the 11:00 UTC
    # digest window gives the prior worker's digest_log INSERT 60+ extra
    # seconds to land before this worker's email_scheduler considers
    # firing. Combined with the V515 digest_log dedup guard below, this
    # eliminates the race window that produced the 2026-05-05 dup-fire.
    print(f"[{datetime.now()}] V515: Email scheduler waiting 4 minutes for startup...")
    time.sleep(240)

    et = pytz.timezone('America/New_York')

    # V78: Track if we've already run digest today to prevent duplicates
    last_digest_date = None
    # V229 addendum H1: Track lifecycle/onboarding runs too. Without these,
    # the 5-min polling inside the 8:00-8:29 / 9:00-9:29 windows fired
    # check_trial_lifecycle() and check_onboarding_nudges() up to 6 times
    # per day, spamming trial users with duplicate emails.
    last_lifecycle_date = None
    last_onboarding_date = None

    # V276 / V524: deploy-restart dedup via bootstrap helper. Tests in
    # tests/test_digest.py exercise the same helper.
    try:
        _bootstrap_today = datetime.now(et).date()
        _conn = permitdb.get_connection()
        seeded = bootstrap_seen_dates(_conn, et, _bootstrap_today)
        last_digest_date = seeded['digest']
        last_lifecycle_date = seeded['lifecycle']
        last_onboarding_date = seeded['onboarding']
        print(
            f"[{datetime.now()}] V276: digest dedup bootstrap — digest={last_digest_date}, "
            f"lifecycle={last_lifecycle_date}, onboarding={last_onboarding_date}"
        )
    except Exception as e:
        print(f"[{datetime.now()}] V276: dedup bootstrap skipped ({e}); counters stay None")

    while True:
        try:
            now_et = datetime.now(et)
            today_date = now_et.date()

            # V78: Update heartbeat timestamp
            DIGEST_STATUS['last_heartbeat'] = datetime.now().isoformat()

            # V64: Heartbeat every cycle so we can verify thread is alive in Render logs
            print(
                f"[{datetime.now()}] V78: Email scheduler heartbeat: "
                f"{now_et.strftime('%I:%M %p ET')} (thread_alive=True)"
            )

            # Check if it's time for daily tasks (7-9 AM ET window)
            if 7 <= now_et.hour <= 9:
                # V515 / V524: digest_log dup-fire guard. Helper queries
                # digest_log for today's 'scheduled' sent row. If present,
                # we KNOW some prior worker already fired today even if our
                # in-memory last_digest_date is stale.
                _digest_already_fired = False
                if now_et.hour == 7 and last_digest_date != today_date:
                    try:
                        _dup_conn = permitdb.get_connection()
                        if digest_already_fired_today(_dup_conn):
                            _digest_already_fired = True
                            last_digest_date = today_date  # sync in-memory state
                            print(
                                f"[{datetime.now()}] V515: digest already fired today "
                                f"(digest_log row present) — skipping send"
                            )
                        try:
                            _dup_conn.close()
                        except Exception:
                            pass
                    except Exception as _dup_e:
                        print(
                            f"[{datetime.now()}] V515: digest_log dedup query failed "
                            f"(proceeding to fire): {_dup_e}"
                        )

                if (
                    now_et.hour == 7
                    and last_digest_date != today_date
                    and not _digest_already_fired
                ):
                    print(f"[{datetime.now()}] V78: Running daily digest...")
                    DIGEST_STATUS['last_digest_attempt'] = datetime.now().isoformat()
                    try:
                        sent, failed = send_daily_digest()
                        print(f"[{datetime.now()}] V78: Daily digest complete - {sent} sent, {failed} failed")
                        DIGEST_STATUS['last_digest_result'] = 'success'
                        DIGEST_STATUS['last_digest_sent'] = sent
                        DIGEST_STATUS['last_digest_failed'] = failed
                        last_digest_date = today_date  # Mark as done for today
                        # V158: Log success to DB
                        try:
                            _conn = permitdb.get_connection()
                            _conn.execute(
                                "INSERT OR REPLACE INTO system_state (key, value, updated_at) "
                                "VALUES ('digest_last_success', ?, datetime('now'))",
                                (datetime.now().isoformat(),),
                            )
                            _conn.execute(
                                "INSERT OR REPLACE INTO system_state (key, value, updated_at) "
                                "VALUES ('digest_last_sent_count', ?, datetime('now'))",
                                (str(sent),),
                            )
                            _conn.execute(
                                "INSERT INTO digest_log (recipient_email, permits_count, status) "
                                "VALUES ('scheduled', ?, 'sent')",
                                (sent,),
                            )
                            _conn.commit()
                        except Exception:
                            pass
                    except Exception as e:
                        print(f"[{datetime.now()}] [ERROR] Daily digest failed: {e}")
                        import traceback
                        traceback.print_exc()
                        DIGEST_STATUS['last_digest_result'] = f'error: {e}'
                        # V158: Log failure to DB
                        try:
                            _conn = permitdb.get_connection()
                            _conn.execute(
                                "INSERT OR REPLACE INTO system_state (key, value, updated_at) "
                                "VALUES ('digest_last_error', ?, datetime('now'))",
                                (str(e)[:500],),
                            )
                            _conn.execute(
                                "INSERT INTO digest_log (recipient_email, status, error_message) "
                                "VALUES ('scheduled', 'failed', ?)",
                                (str(e)[:500],),
                            )
                            _conn.commit()
                        except Exception:
                            pass

                # Trial lifecycle at 8 AM ET (V229 addendum H1: once-per-day guard)
                if now_et.hour == 8 and last_lifecycle_date != today_date:
                    print(f"[{datetime.now()}] V64: Checking trial lifecycle...")
                    try:
                        results = check_trial_lifecycle()
                        print(f"[{datetime.now()}] V64: Trial lifecycle complete - {results}")
                        last_lifecycle_date = today_date
                        # V276: persist so deploy restarts skip re-running
                        try:
                            _conn = permitdb.get_connection()
                            _conn.execute(
                                "INSERT OR REPLACE INTO system_state (key, value, updated_at) "
                                "VALUES ('lifecycle_last_success', ?, datetime('now'))",
                                (datetime.now().isoformat(),),
                            )
                            _conn.commit()
                        except Exception:
                            pass
                    except Exception as e:
                        print(f"[{datetime.now()}] [ERROR] Trial lifecycle failed: {e}")
                        import traceback
                        traceback.print_exc()

                # Onboarding nudges at 9 AM ET (V229 addendum H1: once-per-day guard)
                if now_et.hour == 9 and last_onboarding_date != today_date:
                    print(f"[{datetime.now()}] V64: Checking onboarding nudges...")
                    try:
                        sent = check_onboarding_nudges()
                        print(f"[{datetime.now()}] V64: Onboarding nudges complete - {sent} sent")
                        last_onboarding_date = today_date
                        # V276: persist so deploy restarts skip re-running
                        try:
                            _conn = permitdb.get_connection()
                            _conn.execute(
                                "INSERT OR REPLACE INTO system_state (key, value, updated_at) "
                                "VALUES ('onboarding_last_success', ?, datetime('now'))",
                                (datetime.now().isoformat(),),
                            )
                            _conn.commit()
                        except Exception:
                            pass
                    except Exception as e:
                        print(f"[{datetime.now()}] [ERROR] Onboarding nudges failed: {e}")
                        import traceback
                        traceback.print_exc()

        except Exception as e:
            print(f"[{datetime.now()}] [ERROR] Email scheduler error: {e}")
            import traceback
            traceback.print_exc()
            DIGEST_STATUS['last_digest_result'] = f'loop_error: {e}'
            # V64: Wait 5 min on error before retrying, don't die
            time.sleep(300)
            continue

        # V78: Check every 5 minutes during 7-9 AM ET window to not miss digest
        # Check every 30 minutes outside that window to save resources
        if 6 <= now_et.hour <= 9:
            time.sleep(300)  # 5 minutes during morning window
        else:
            time.sleep(1800)  # 30 minutes otherwise


# Module-global thread handle so start_thread() is idempotent.
_thread = None


def start_thread():
    """Idempotent spawn of the email scheduler.

    V524 contract: the thread name MUST be 'email_scheduler'.
    routes/admin.py /api/admin/digest/status enumerates threads
    looking for that exact name; the diagnostics endpoint does
    the same. tests/test_digest.py pins this so a future refactor
    that renames the thread (or the V475 silent-drop regression
    that prompted V524) fails the test before it ships.
    """
    global _thread
    if _thread is not None and _thread.is_alive():
        return _thread
    _thread = threading.Thread(
        target=schedule_email_tasks,
        name='email_scheduler',
        daemon=True,
    )
    _thread.start()
    return _thread
