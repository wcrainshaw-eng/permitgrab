"""V530: enrichment scheduler thread, extracted from server.py:9040-9057.

Behavior is unchanged from the V229 C5 inline version. This is a
verbatim lift; the V475 silent-thread-drop regression class is now
pinned by tests/test_enrichment.py the same way V524 pinned the
email_scheduler thread.

Public API:
- start_thread(): idempotent spawn; thread name MUST stay
  'enrichment_daemon' (V475 contract — routes/admin.py
  /api/admin/digest/status and the V493 IRONCLAD watchdog both
  enumerate threads looking for that exact name).
- enrichment_daemon(): the loop body. 600s startup delay, then
  pause-on-license-import + enrich_batch every 1800s.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime


def enrichment_daemon():
    """V229 C5: dedicated enrichment daemon. scheduled_collection runs
    enrich_batch per cycle (30-60 min apart). This second thread runs
    enrichment on its own 30-min cadence so the queue drains steadily
    even when a collection cycle is slow/stuck.

    V530: lifted from server.py unchanged. Imports of
    license_enrichment / web_enrichment stay lazy in-loop so a
    transient ImportError on one cycle doesn't kill the daemon."""
    time.sleep(600)  # let collection warm up first
    while True:
        try:
            from license_enrichment import is_import_running
            if is_import_running():
                print(
                    f"[{datetime.now()}] V242: enrichment daemon paused — "
                    f"license import in progress",
                    flush=True,
                )
                time.sleep(30)
                continue
        except Exception:
            pass
        try:
            from web_enrichment import enrich_batch
            result = enrich_batch(limit=200)
            print(
                f"[{datetime.now()}] [V229 C5] enrichment daemon: {result}",
                flush=True,
            )
        except Exception as e:
            print(
                f"[{datetime.now()}] [V229 C5] enrichment daemon error: {e}",
                flush=True,
            )
        time.sleep(1800)


# Module-global thread handle so start_thread() is idempotent.
_thread = None


def start_thread():
    """Idempotent spawn of the enrichment daemon.

    V530 contract: the thread name MUST be 'enrichment_daemon'.
    routes/admin.py and /api/admin/debug/threads enumerate threads
    looking for that exact name; the V493 IRONCLAD self-heal watchdog
    does the same. tests/test_enrichment.py pins this so a future
    refactor that renames the thread (or the V475-class silent-drop
    regression that prompted V524 + V530) fails the test before
    it ships.
    """
    global _thread
    if _thread is not None and _thread.is_alive():
        return _thread
    _thread = threading.Thread(
        target=enrichment_daemon,
        name='enrichment_daemon',
        daemon=True,
    )
    _thread.start()
    return _thread
