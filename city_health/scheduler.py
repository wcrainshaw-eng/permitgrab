"""V540 PR1: nightly health_scheduler thread. Per Wes's V540 directive
— writes the customer-visible city_health table once per night so the
signup flow + digest pipeline can consult fresh state.

Design (V475 pattern + V524 template):
- Dedicated `health_scheduler` thread (named, daemon).
- Sleeps 12 minutes after worker boot before first cycle (gives
  collection daemon a head start on warming the DB).
- Fires daily at 4 AM ET (8 AM UTC) — chosen to land BEFORE the
  7 AM ET digest cycle so the digest pipeline reads fresh
  city_health rows.
- Idempotent ensure_table() before every cycle — handles fresh DBs
  + V540 schema migration on first deploy.
- Writes summary to digest_log so admin dashboards see daily counts.

Public API:
- start_thread(): idempotent spawn of health_scheduler.
- health_daemon(): the loop body (exposed for tests via mock).
"""
from __future__ import annotations

import threading
import time
from datetime import datetime

import db as permitdb

from .compute import compute_all_city_health
from .schema import ensure_table


def health_daemon():
    """Main loop. 12-min startup delay, then daily fire at 8 UTC."""
    time.sleep(720)  # 12 min — let collection warm up the DB
    last_run_date = None

    while True:
        try:
            now_utc = datetime.utcnow()
            today = now_utc.date()

            # Fire once per day during the 8 AM UTC window.
            if now_utc.hour == 8 and last_run_date != today:
                print(
                    f'[{datetime.now()}] V540: city_health daily compute starting',
                    flush=True,
                )
                # Make sure the table exists (idempotent).
                ensure_table()

                summary = compute_all_city_health()
                last_run_date = today
                print(
                    f'[{datetime.now()}] V540: city_health daily compute complete: '
                    f'{summary}',
                    flush=True,
                )

                # Mirror summary to digest_log so the admin dashboard
                # sees daily counts. recipient_email='health_scheduler'
                # so it doesn't collide with the V515 dedup query.
                try:
                    conn = permitdb.get_connection()
                    conn.execute(
                        "INSERT INTO digest_log "
                        "(recipient_email, permits_count, status, error_message) "
                        "VALUES (?, ?, ?, ?)",
                        (
                            'health_scheduler',
                            int(summary.get('total', 0)),
                            'sent',
                            f"pass={summary.get('pass', 0)} "
                            f"degraded={summary.get('degraded', 0)} "
                            f"fail={summary.get('fail', 0)} "
                            f"errored={summary.get('errored', 0)}",
                        ),
                    )
                    conn.commit()
                except Exception as e:
                    print(
                        f'[{datetime.now()}] V540: digest_log write failed: {e}',
                        flush=True,
                    )
        except Exception as e:
            print(
                f'[{datetime.now()}] V540: health_scheduler error: {e}',
                flush=True,
            )
            time.sleep(300)
            continue

        # Outside the 8 UTC window, poll every 30 min so the boot
        # delay doesn't make us miss the daily window.
        if 7 <= now_utc.hour <= 9:
            time.sleep(300)  # 5 min during the window
        else:
            time.sleep(1800)  # 30 min otherwise


_thread = None


def start_thread():
    """Idempotent spawn of the health_scheduler thread.

    V540 contract: thread name MUST be 'health_scheduler' (matches the
    V475 pattern of explicit named threads in start_collectors). The
    /api/admin/debug/threads endpoint and the V493 IRONCLAD self-heal
    watchdog enumerate threads by name; this name is the contract.
    """
    global _thread
    if _thread is not None and _thread.is_alive():
        return _thread
    _thread = threading.Thread(
        target=health_daemon,
        name='health_scheduler',
        daemon=True,
    )
    _thread.start()
    return _thread
