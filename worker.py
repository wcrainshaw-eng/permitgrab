"""
PermitGrab Background Worker — V365b / V473 worker-restart

Runs the collector daemon, enrichment, and email scheduler as a standalone
process, separate from the gunicorn web server. This eliminates memory
pressure on the web process. Standard plan has 2GB RAM.

Deployed as a Render Background Worker service (~$7/mo Starter plan).
Communicates with the web process only via the shared PostgreSQL database.

Usage:
    python3 worker.py

Environment:
    DATABASE_URL  — PostgreSQL connection string (Render provides this)

V473 worker-restart 2026-04-30: this comment edit forces Render to redeploy
the permitgrab-worker service after it stalled for ~4.5h with no fresh
scraper_runs entries (last cycle 2026-04-29 21:14:40 UTC, db_now
2026-04-30 01:51 UTC). No code change — just a docstring update to
trigger the auto-deploy.
"""

import os
import sys
import time
import json
import gc
import signal
import threading
from datetime import datetime, timedelta

# Ensure repo root is on the path so all imports resolve
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ─── Configuration ───────────────────────────────────────────────────────────

MEMORY_LIMIT_MB = 1600      # Skip cycles if above this (2GB Standard plan)
MEMORY_CRITICAL_MB = 1800   # Abort + force GC if above this
HEARTBEAT_INTERVAL = 300    # Log memory every 5 minutes
DATA_DIR = '/var/data' if os.path.isdir('/var/data') else os.path.join(
    os.path.dirname(os.path.abspath(__file__)), 'data')

# ─── Memory monitoring ───────────────────────────────────────────────────────

def get_memory_mb():
    """Return current process RSS in MB."""
    try:
        import psutil
        return psutil.Process(os.getpid()).memory_info().rss / 1024 / 1024
    except Exception:
        return 0.0


def memory_ok(label=""):
    """Check if memory is under the limit. Log and gc.collect() if not."""
    mem = get_memory_mb()
    if mem > MEMORY_CRITICAL_MB:
        print(f"[WORKER] CRITICAL: memory {mem:.0f}MB > {MEMORY_CRITICAL_MB}MB "
              f"({label}) — forcing gc.collect()", flush=True)
        gc.collect()
        mem = get_memory_mb()
        print(f"[WORKER] post-gc: {mem:.0f}MB", flush=True)
        return mem < MEMORY_CRITICAL_MB
    if mem > MEMORY_LIMIT_MB:
        print(f"[WORKER] WARNING: memory {mem:.0f}MB > {MEMORY_LIMIT_MB}MB "
              f"({label}) — skipping", flush=True)
        gc.collect()
        return False
    return True


# ─── Database initialisation ─────────────────────────────────────────────────

def init_database():
    """Initialise the database connection (same path as server.py startup)."""
    import db as permitdb
    permitdb.init_db()
    print(f"[WORKER] Database initialised (DATA_DIR={DATA_DIR})", flush=True)


# ─── Config sync (mirrors server.py start_collectors) ────────────────────────

def sync_configs():
    """Apply config fixes and sync CITY_REGISTRY → prod_cities."""
    try:
        from city_configs import fix_known_broken_configs
        fixes = fix_known_broken_configs()
        print(f"[WORKER] Applied {len(fixes)} config fixes", flush=True)
    except Exception as e:
        print(f"[WORKER] Config fix error (non-fatal): {e}", flush=True)

    try:
        from city_configs import sync_city_registry_to_prod_cities
        sync_city_registry_to_prod_cities()
        print(f"[WORKER] Registry sync complete", flush=True)
    except Exception as e:
        print(f"[WORKER] Registry sync error (non-fatal): {e}", flush=True)


# ─── Initial collection (one-time on startup) ────────────────────────────────

def run_initial_collection():
    """One-time startup collection with 180-day lookback for backfill.

    Mirrors server.py run_initial_collection() but with memory guards.

    V473b optimization: now skipped by default. The V414-era reasoning
    that prompted skipping run_initial_collection on the WEB side
    ("scheduled_collection picks up the same set of cities within ~30
    min") applies equally to the worker — running a 180-day backfill
    across ~2,200 active cities on every warm restart is a 30-60 min
    SQLite-writer monopoly that blocks scheduled_collection from
    landing fresh data. Set RUN_INITIAL_COLLECTION=1 to opt in for
    cold/post-incident bootstrap.
    """
    if os.environ.get('RUN_INITIAL_COLLECTION', '').lower() not in ('1', 'true', 'yes'):
        print(f"[WORKER] V473b: skipping run_initial_collection (set "
              f"RUN_INITIAL_COLLECTION=1 to enable). scheduled_collection "
              f"will pick up the same cities within ~30 min.", flush=True)
        return

    print(f"[WORKER] Initial collection waiting 120s for startup...", flush=True)
    time.sleep(120)

    if not memory_ok("initial_collection_start"):
        print(f"[WORKER] Skipping initial collection — memory too high", flush=True)
        return

    try:
        # Clear stale lock files
        lock_file = os.path.join(DATA_DIR, ".collection_lock")
        if os.path.exists(lock_file):
            try:
                os.remove(lock_file)
                print(f"[WORKER] Cleared stale collection lock")
            except Exception as e:
                print(f"[WORKER] Could not clear lock: {e}")

        # 180-day lookback for backfill
        print(f"[WORKER] Running initial collection (180 days)...", flush=True)
        from collector import collect_refresh
        collect_refresh(days_back=180)
        gc.collect()

        # Violations
        if memory_ok("initial_violations"):
            try:
                from violation_collector import collect_violations
                collect_violations()
                gc.collect()
            except Exception as e:
                print(f"[WORKER] Initial violation collection error: {e}", flush=True)

        # Signals
        if memory_ok("initial_signals"):
            try:
                from signal_collector import collect_all_signals
                collect_all_signals(days_back=90)
                gc.collect()
            except Exception as e:
                print(f"[WORKER] Initial signal collection error: {e}", flush=True)

        print(f"[WORKER] Initial collection complete. Memory: {get_memory_mb():.0f}MB",
              flush=True)
    except Exception as e:
        print(f"[WORKER] Initial collection error: {e}", flush=True)
        import traceback
        traceback.print_exc()


# ─── Scheduled collection (main daemon loop) ─────────────────────────────────

def scheduled_collection():
    """Main collection loop — runs every ~30 minutes.

    Mirrors server.py scheduled_collection() with memory guards added.
    """
    import db as permitdb

    # V473b optimization: 5-min sleep dropped to 60s. The original 5-min
    # gave run_initial_collection room to land its backfill first; with
    # initial_collection now opt-in (RUN_INITIAL_COLLECTION env var) the
    # scheduled loop should kick off as soon as the worker is healthy.
    init_wait = int(os.environ.get('SCHEDULED_INIT_WAIT', '60'))
    print(f"[WORKER] Scheduled collector waiting {init_wait}s for startup...", flush=True)
    time.sleep(init_wait)

    while True:
        # V242: Yield to license imports
        try:
            from license_enrichment import is_import_running
            if is_import_running():
                print(f"[WORKER] Collection paused — license import in progress", flush=True)
                time.sleep(30)
                continue
        except Exception:
            pass

        cycle_start = time.time()
        print(f"[WORKER] Starting collection cycle... Memory: {get_memory_mb():.0f}MB",
              flush=True)

        # ── Permit collection ──
        if memory_ok("permit_collection"):
            try:
                from collector import collect_refresh
                collect_refresh(days_back=7)
                gc.collect()
                print(f"[WORKER] Permit refresh complete", flush=True)
            except Exception as e:
                print(f"[WORKER] Permit collection error: {e}", flush=True)
                import traceback
                traceback.print_exc()

        # ── Prune old permits ──
        try:
            deleted = permitdb.delete_old_permits(days=365)
            if deleted > 0:
                print(f"[WORKER] Pruned {deleted} old permits", flush=True)
        except Exception as e:
            print(f"[WORKER] Prune error: {e}", flush=True)

        # ── Refresh contractor profiles ──
        if memory_ok("profile_refresh"):
            try:
                from contractor_profiles import refresh_contractor_profiles
                t0 = time.time()
                prof_result = refresh_contractor_profiles()
                print(f"[WORKER] Profile refresh: {prof_result['profiles_upserted']} profiles "
                      f"across {prof_result['cities_processed']} cities, "
                      f"{time.time() - t0:.1f}s", flush=True)
                gc.collect()
            except Exception as e:
                print(f"[WORKER] Profile refresh error (non-fatal): {e}", flush=True)

        # ── Violation collection ──
        if memory_ok("violation_collection"):
            try:
                from violation_collector import collect_violations
                t0 = time.time()
                results = collect_violations()
                elapsed = time.time() - t0
                print(f"[WORKER] Violation collection complete ({elapsed:.1f}s)", flush=True)
                gc.collect()

                # Log results to collection_log
                try:
                    conn = permitdb.get_connection()
                    for slug, agg in (results or {}).items():
                        if not isinstance(agg, dict):
                            agg = {'inserted': int(agg or 0), 'api_rows_returned': 0,
                                   'duplicate_rows_skipped': 0, 'api_url': None,
                                   'query_params': None, 'error': None}
                        ins = agg.get('inserted') or 0
                        api_rows = agg.get('api_rows_returned') or 0
                        dupes = agg.get('duplicate_rows_skipped') or 0
                        err = agg.get('error')
                        if err:
                            status = 'error'
                        elif ins > 0:
                            status = 'success'
                        elif api_rows > 0:
                            status = 'caught_up'
                        else:
                            status = 'caught_up'  # simplified from server.py
                        conn.execute("""
                            INSERT INTO collection_log
                              (city_slug, collection_type, status,
                               records_fetched, records_inserted,
                               duration_seconds, api_url, query_params,
                               api_rows_returned, duplicate_rows_skipped,
                               error_message)
                            VALUES (?, 'violations', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (slug, status, api_rows, ins, round(elapsed, 2),
                              agg.get('api_url'), agg.get('query_params'),
                              api_rows, dupes, err))
                    conn.commit()
                except Exception as e:
                    print(f"[WORKER] collection_log write failed (non-fatal): {e}", flush=True)
            except Exception as e:
                print(f"[WORKER] Violation collection error: {e}", flush=True)

        # ── Web enrichment (DDG search for phones) ──
        if memory_ok("web_enrichment"):
            try:
                from web_enrichment import enrich_batch
                result = enrich_batch(limit=200)
                print(f"[WORKER] Web enrichment cycle: {result}", flush=True)
                gc.collect()
            except Exception as e:
                print(f"[WORKER] Web enrichment error (non-fatal): {e}", flush=True)

        # ── Staleness check ──
        try:
            conn = permitdb.get_connection()
            stale_rows = conn.execute("""
                SELECT pc.city_slug,
                       COALESCE(pc.expected_freshness_days, 14) AS expected_days,
                       MAX(p.filing_date) newest,
                       CAST(julianday('now') - julianday(MAX(p.filing_date)) AS INTEGER) age_days
                FROM prod_cities pc
                JOIN permits p ON p.source_city_key = pc.city_slug
                WHERE pc.status = 'active'
                GROUP BY pc.city_slug
                HAVING age_days > expected_days
                ORDER BY age_days DESC
            """).fetchall()
            if stale_rows:
                print(f"[WORKER] {len(stale_rows)} stale cities detected", flush=True)
        except Exception as e:
            print(f"[WORKER] Staleness check error (non-fatal): {e}", flush=True)

        # ── Cycle complete ──
        elapsed_total = time.time() - cycle_start
        mem = get_memory_mb()
        print(f"[WORKER] Cycle complete: {elapsed_total:.0f}s, memory: {mem:.0f}MB", flush=True)

        # Force GC at end of every cycle
        gc.collect()

        # Dynamic sleep: aim for 30-min cycle cadence
        sleep_time = max(300, 1800 - elapsed_total)  # at least 5 minutes
        print(f"[WORKER] Sleeping {sleep_time:.0f}s until next cycle", flush=True)
        time.sleep(sleep_time)


# ─── Enrichment daemon (dedicated thread) ────────────────────────────────────

def enrichment_daemon():
    """Dedicated enrichment thread — runs on its own 30-min cadence.

    Separate from the main collection loop so enrichment drains steadily
    even when a collection cycle is slow/stuck.
    """
    print(f"[WORKER] Enrichment daemon waiting 600s for warmup...", flush=True)
    time.sleep(600)

    while True:
        # Yield to license imports
        try:
            from license_enrichment import is_import_running
            if is_import_running():
                print(f"[WORKER] Enrichment paused — license import in progress", flush=True)
                time.sleep(30)
                continue
        except Exception:
            pass

        if not memory_ok("enrichment_daemon"):
            time.sleep(300)
            continue

        try:
            from web_enrichment import enrich_batch
            result = enrich_batch(limit=200)
            print(f"[WORKER] Enrichment daemon: {result}", flush=True)
        except Exception as e:
            print(f"[WORKER] Enrichment daemon error: {e}", flush=True)

        gc.collect()
        time.sleep(1800)


# ─── Email scheduler ─────────────────────────────────────────────────────────

def email_scheduler():
    """Daily digest emails + trial lifecycle + onboarding nudges.

    Mirrors server.py schedule_email_tasks() but standalone.
    """
    print(f"[WORKER] Email scheduler waiting 3 minutes for startup...", flush=True)
    time.sleep(180)

    try:
        import pytz
        from email_alerts import send_daily_digest, check_trial_lifecycle, check_onboarding_nudges
    except ImportError as e:
        print(f"[WORKER] Email scheduler import error: {e}", flush=True)
        return

    # Create subscribers.json if missing
    try:
        from pathlib import Path
        subscribers_path = Path("/var/data/subscribers.json")
        if not subscribers_path.exists() and Path("/var/data").exists():
            default_subscribers = [{
                "email": "wcrainshaw@gmail.com",
                "active": True,
                "digest_cities": ["atlanta"],
                "created_at": datetime.now().strftime("%Y-%m-%d")
            }]
            subscribers_path.write_text(json.dumps(default_subscribers, indent=2))
            print(f"[WORKER] Created default subscribers.json")
    except Exception as e:
        print(f"[WORKER] subscribers.json creation error: {e}", flush=True)

    et = pytz.timezone('America/New_York')
    last_digest_date = None

    while True:
        try:
            now_et = datetime.now(et)

            # Daily digest at 7 AM ET
            if now_et.hour == 7 and now_et.date() != last_digest_date:
                print(f"[WORKER] Running daily digest...", flush=True)
                try:
                    send_daily_digest()
                    last_digest_date = now_et.date()
                    print(f"[WORKER] Daily digest sent", flush=True)
                except Exception as e:
                    print(f"[WORKER] Daily digest error: {e}", flush=True)

            # Trial lifecycle at 8 AM ET
            if now_et.hour == 8 and now_et.minute < 5:
                try:
                    check_trial_lifecycle()
                except Exception as e:
                    print(f"[WORKER] Trial lifecycle error: {e}", flush=True)

            # Onboarding nudges at 9 AM ET
            if now_et.hour == 9 and now_et.minute < 5:
                try:
                    check_onboarding_nudges()
                except Exception as e:
                    print(f"[WORKER] Onboarding nudge error: {e}", flush=True)

        except Exception as e:
            print(f"[WORKER] Email scheduler error: {e}", flush=True)

        # Check every 5 minutes during morning window, 30 min otherwise
        if 6 <= now_et.hour <= 9:
            time.sleep(300)
        else:
            time.sleep(1800)


# ─── Heartbeat ────────────────────────────────────────────────────────────────

def heartbeat():
    """Log memory + thread count every 5 minutes. Force GC when high."""
    while True:
        mem = get_memory_mb()
        threads = threading.active_count()
        thread_names = [t.name for t in threading.enumerate()]
        print(f"[WORKER] heartbeat: {mem:.0f}MB, {threads} threads: "
              f"{', '.join(thread_names)}", flush=True)
        if mem > MEMORY_LIMIT_MB:
            gc.collect()
            print(f"[WORKER] heartbeat gc: {mem:.0f}MB → {get_memory_mb():.0f}MB", flush=True)
        time.sleep(HEARTBEAT_INTERVAL)


# ─── Graceful shutdown ────────────────────────────────────────────────────────

_shutdown = False

def handle_signal(signum, frame):
    global _shutdown
    print(f"[WORKER] Received signal {signum}, shutting down gracefully...", flush=True)
    _shutdown = True
    sys.exit(0)

signal.signal(signal.SIGTERM, handle_signal)
signal.signal(signal.SIGINT, handle_signal)


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60, flush=True)
    print(f"[WORKER] PermitGrab Background Worker V365b", flush=True)
    print(f"[WORKER] PID={os.getpid()}, memory={get_memory_mb():.0f}MB", flush=True)
    print(f"[WORKER] DATA_DIR={DATA_DIR}", flush=True)
    print(f"[WORKER] DATABASE_URL={'set' if os.environ.get('DATABASE_URL') else 'NOT SET'}",
          flush=True)
    print("=" * 60, flush=True)

    # 1. Initialise database
    init_database()

    # 2. Sync configs
    sync_configs()

    # 3. Start threads
    threads = []

    # Initial collection (one-time, then exits)
    t_init = threading.Thread(target=run_initial_collection,
                              name='initial_collection', daemon=True)
    t_init.start()
    threads.append(t_init)
    print(f"[WORKER] Started: initial_collection", flush=True)
    time.sleep(30)

    # Scheduled collection (main loop)
    t_sched = threading.Thread(target=scheduled_collection,
                               name='scheduled_collection', daemon=True)
    t_sched.start()
    threads.append(t_sched)
    print(f"[WORKER] Started: scheduled_collection", flush=True)
    time.sleep(30)

    # Enrichment daemon
    t_enrich = threading.Thread(target=enrichment_daemon,
                                name='enrichment_daemon', daemon=True)
    t_enrich.start()
    threads.append(t_enrich)
    print(f"[WORKER] Started: enrichment_daemon", flush=True)

    # Email scheduler
    t_email = threading.Thread(target=email_scheduler,
                               name='email_scheduler', daemon=True)
    t_email.start()
    threads.append(t_email)
    print(f"[WORKER] Started: email_scheduler", flush=True)

    # Heartbeat (main thread keeps alive)
    print(f"[WORKER] All threads started. Entering heartbeat loop.", flush=True)
    heartbeat()


if __name__ == '__main__':
    main()
