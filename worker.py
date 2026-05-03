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
            # V488 follow-up: write a per-cycle summary row to scraper_runs
            # so the health endpoint can tell whether the worker is alive.
            # collector._log_v15_collection has a broad try/except that's
            # been silently dropping every per-city log since 2026-05-01
            # 12:41 — without this top-level row the health metric goes
            # dark even though permits.collected_at is being stamped.
            permit_cycle_t0 = time.time()
            permit_cycle_status = 'success'
            permit_cycle_err = None
            permits_in_cycle = 0
            try:
                from collector import collect_refresh
                # Snapshot the highest collected_at NOW so we can diff after
                # the cycle finishes — collect_refresh returns (new_permits,
                # stats) but new_permits is empty in the V166 inline-upsert
                # path; the diff is the only honest count.
                _pre = permitdb.get_connection().execute(
                    "SELECT COUNT(*) FROM permits WHERE collected_at > datetime('now', '-1 minute')"
                ).fetchone()[0]
                collect_refresh(days_back=7)
                gc.collect()
                _post = permitdb.get_connection().execute(
                    "SELECT COUNT(*) FROM permits WHERE collected_at > datetime('now', '-1 minute')"
                ).fetchone()[0]
                permits_in_cycle = max(0, _post - _pre)
                print(f"[WORKER] Permit refresh complete (+{permits_in_cycle} stamped this cycle)", flush=True)
            except Exception as e:
                permit_cycle_status = 'error'
                permit_cycle_err = str(e)[:300]
                print(f"[WORKER] Permit collection error: {e}", flush=True)
                import traceback
                traceback.print_exc()
            # Single per-cycle row — one is enough for the health endpoint.
            # Per-city granularity comes from collector._log_v15_collection
            # if/when that path is repaired; this row is the heartbeat.
            try:
                permitdb.log_scraper_run(
                    source_name='worker_scheduled_collection',
                    city='ALL',
                    state=None,
                    city_slug=None,
                    permits_found=permits_in_cycle,
                    permits_inserted=permits_in_cycle,
                    status=permit_cycle_status,
                    error_message=permit_cycle_err,
                    duration_ms=int((time.time() - permit_cycle_t0) * 1000),
                    collection_type='scheduled',
                    triggered_by='worker',
                )
            except Exception as log_err:
                print(f"[WORKER] scraper_runs cycle log failed: {log_err}", flush=True)

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

        # V488 IRONCLAD: violations + web_enrichment + staleness +
        # stats_cache refresh have been MOVED to secondary_loop()
        # (separate daemon thread, ~2hr cadence). Their cumulative
        # ~3hr-per-cycle wall time was the reason permits were only
        # refreshing every 4 hrs instead of the 30-min target — they
        # blocked scheduled_collection's sleep math. Splitting them
        # off lets the permit cycle return to fast cadence (≤10 min
        # work + 30min sleep) while heavy aggregations still run
        # often enough to keep stats_cache and violations fresh.

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


# ─── Secondary loop (V488 IRONCLAD): violations + staleness + stats_cache ────

def secondary_loop():
    """V488 IRONCLAD: Run the heavy non-permit phases on their own ~2hr
    cadence so they stop blocking the permit refresh cycle.

    Was: scheduled_collection ran permits + violations + enrichment +
    staleness + stats_cache back-to-back. Cumulative wall time hit ~3hr
    so permits were only refreshing every ~4hrs (observed live: cycles
    landed at 14:30 + 18:00 with nothing in between, vs the 30-min
    target). Each cycle, only the top-15 high-volume cities got picked
    by ORDER BY permits_last_30d DESC — same 15 every cycle while 1,742
    other active cities went silent.

    This loop runs its own 2hr sleep cadence. It still enqueues violation
    collection, staleness audit, and stats_cache refresh, but doesn't
    block the permit cycle from running every 30 min.
    """
    import db as permitdb

    print(f"[SEC] Secondary loop waiting 5 min for warmup...", flush=True)
    time.sleep(300)

    while True:
        try:
            from license_enrichment import is_import_running
            if is_import_running():
                print(f"[SEC] Paused — license import in progress", flush=True)
                time.sleep(60)
                continue
        except Exception:
            pass

        cycle_start = time.time()
        print(f"[SEC] Starting secondary cycle... mem={get_memory_mb():.0f}MB", flush=True)

        # ── Violations ──
        if memory_ok("violation_collection"):
            try:
                from violation_collector import collect_violations
                t0 = time.time()
                results = collect_violations()
                elapsed = time.time() - t0
                print(f"[SEC] Violation collection complete ({elapsed:.1f}s)", flush=True)
                gc.collect()

                # Log results to collection_log (same pattern as before)
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
                        status = 'error' if err else ('success' if ins > 0 else 'caught_up')
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
                    print(f"[SEC] collection_log write failed (non-fatal): {e}", flush=True)
            except Exception as e:
                print(f"[SEC] Violation collection error: {e}", flush=True)

        # ── Staleness audit ──
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
                print(f"[SEC] {len(stale_rows)} stale cities detected", flush=True)
        except Exception as e:
            print(f"[SEC] Staleness check error (non-fatal): {e}", flush=True)

        # ── Stats cache refresh ──
        if memory_ok("stats_cache_refresh"):
            try:
                from stats_cache import refresh_stats_cache
                conn = permitdb.get_connection()
                refresh_stats_cache(conn)
                print(f"[SEC] Stats cache refreshed", flush=True)
                gc.collect()
            except Exception as e:
                print(f"[SEC] Stats cache refresh error (non-fatal): {e}",
                      flush=True)

        # ── V488 IRONCLAD: persona stats system_state cache refresh ──
        # Web /leads/* pages read from this. Without periodic refresh,
        # the system_state blob goes stale and either AdsBot/Googlebot
        # see old numbers or the web pays the cold-start COUNT cost.
        if memory_ok("persona_stats_refresh"):
            try:
                from routes.persona_pages import refresh_persona_stats_cache
                t0 = time.time()
                result = refresh_persona_stats_cache()
                if result:
                    print(f"[SEC] V488 persona_stats_cache refreshed "
                          f"(violations={result.get('total_violations')}, "
                          f"owners={result.get('total_owners')}, "
                          f"contractors={result.get('total_contractors')}) "
                          f"in {time.time()-t0:.1f}s", flush=True)
                gc.collect()
            except Exception as e:
                print(f"[SEC] V488 persona_stats refresh error (non-fatal): {e}",
                      flush=True)

        elapsed_total = time.time() - cycle_start
        mem = get_memory_mb()
        print(f"[SEC] Secondary cycle complete: {elapsed_total:.0f}s, mem={mem:.0f}MB", flush=True)

        # 2hr cadence — violations + stats are heavy and don't need
        # 30-min freshness. If the cycle itself ran >2hrs, sleep min 5min.
        sleep_time = max(300, 7200 - elapsed_total)
        print(f"[SEC] Sleeping {sleep_time:.0f}s until next secondary cycle", flush=True)
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

    # V488 IRONCLAD (digest-stuck-since-04-29 fix): persist last-run dates
    # to system_state so they survive worker restarts. The old code stored
    # last_digest_date in module-local memory — any worker restart past
    # 7am ET would skip that day's digest because:
    #   1. Module reload set last_digest_date = None
    #   2. The `hour == 7` strict gate already passed
    #   3. Worker waited until tomorrow at 7am... and then often restarted again
    # Result: 4 days of missed digests from 2026-04-29 → 2026-05-03.
    # Fix: persist last-run dates AND relax the gate to "hour >= 7 AND
    # today not yet sent" so a worker restart at 8am, 11am, or 2pm
    # still catches today's digest.
    import db as permitdb_email

    def _last_run_date(key):
        try:
            r = permitdb_email.get_system_state(key)
            if r and r.get('value'):
                from datetime import date as _date
                return _date.fromisoformat(r['value'])
        except Exception:
            pass
        return None

    def _set_last_run_date(key, d):
        try:
            permitdb_email.set_system_state(key, d.isoformat())
        except Exception as _se:
            print(f"[WORKER] system_state write failed for {key}: {_se}", flush=True)

    last_digest_date = _last_run_date('email_last_digest_date')
    last_trial_date = _last_run_date('email_last_trial_date')
    last_nudge_date = _last_run_date('email_last_nudge_date')

    print(f"[WORKER] Email scheduler resumed: "
          f"last_digest={last_digest_date} last_trial={last_trial_date} "
          f"last_nudge={last_nudge_date}", flush=True)

    while True:
        try:
            now_et = datetime.now(et)
            today = now_et.date()

            # Daily digest at >= 7 AM ET (relaxed from `== 7` so a worker
            # restart any time after 7am still catches today)
            if now_et.hour >= 7 and today != last_digest_date:
                print(f"[WORKER] Running daily digest "
                      f"(now {now_et.isoformat(timespec='minutes')}, "
                      f"last_digest_date={last_digest_date})...", flush=True)
                try:
                    send_daily_digest()
                    last_digest_date = today
                    _set_last_run_date('email_last_digest_date', today)
                    print(f"[WORKER] Daily digest sent for {today}", flush=True)
                except Exception as e:
                    print(f"[WORKER] Daily digest error: {e}", flush=True)

            # Trial lifecycle at >= 8 AM ET, once per day
            if now_et.hour >= 8 and today != last_trial_date:
                try:
                    check_trial_lifecycle()
                    last_trial_date = today
                    _set_last_run_date('email_last_trial_date', today)
                except Exception as e:
                    print(f"[WORKER] Trial lifecycle error: {e}", flush=True)

            # Onboarding nudges at >= 9 AM ET, once per day
            if now_et.hour >= 9 and today != last_nudge_date:
                try:
                    check_onboarding_nudges()
                    last_nudge_date = today
                    _set_last_run_date('email_last_nudge_date', today)
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
    """Log memory + thread count every 5 minutes. Force GC when high.

    V485 (2026-05-02 root-cause fix): also force a wal_checkpoint(TRUNCATE)
    on every heartbeat. The auto-checkpoint at 200 pages (db.py) only runs
    PASSIVE checkpoints — those silently skip when ANY reader holds a
    transaction (V479 stats refresh, V483b NOLA backfill, the 4 web
    workers' constant page-render reads). A skipped passive checkpoint
    leaves the WAL file growing unbounded. The 695MB WAL freeze on
    2026-05-02 was the third such incident in ten days; this is the
    permanent cure: an active checkpoint from a thread (heartbeat) that
    the collector does NOT serialize against. The worker holds no other
    long readers, so this call almost always succeeds — and even when
    it returns busy=1, it still flushes whatever frames it can.
    """
    while True:
        mem = get_memory_mb()
        threads = threading.active_count()
        thread_names = [t.name for t in threading.enumerate()]

        # V485: WAL maintenance — runs every heartbeat (~5 min). Cheap if
        # WAL is small (~ms), critical if it's been growing.
        try:
            import db as _permitdb
            _conn = _permitdb.get_connection()
            _r = _conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            # _r = (busy, num_frames_in_wal, num_checkpointed)
            import os
            _wal_path = os.path.join(DATA_DIR, 'permitgrab.db-wal')
            _wal_mb = os.path.getsize(_wal_path) / 1024 / 1024 if os.path.exists(_wal_path) else 0
            if _r and (_r[1] > 1000 or _wal_mb > 32):
                print(f"[WORKER] V485 wal_checkpoint: busy={_r[0]} frames={_r[1]} "
                      f"checkpointed={_r[2]} wal_size={_wal_mb:.0f}MB", flush=True)
        except Exception as e:
            print(f"[WORKER] V485 wal_checkpoint error (non-fatal): {e}", flush=True)

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

    # 2b. V483: refresh the V479 stats cache once on startup so a fresh
    # worker boot immediately reflects any recent direct-DB writes (e.g.
    # the V482 Miami-Dade owner slug realignment). Subsequent refreshes
    # happen at the end of each scheduled_collection cycle. Wrapped in
    # try/except so a refresh failure never blocks the worker's main loop.
    try:
        import db as _permitdb
        from stats_cache import refresh_stats_cache as _v483_refresh
        _v483_refresh(_permitdb.get_connection())
        print(f"[WORKER] V483: startup stats cache refresh complete", flush=True)
    except Exception as _v483_e:
        print(f"[WORKER] V483: startup stats refresh failed (non-fatal): "
              f"{_v483_e}", flush=True)

    # 2c. V488 IRONCLAD: persona stats system_state cache startup refresh.
    # The /leads/* pages read from system_state; without this, the very
    # first request after a deploy pays the 5-COUNT cold-start cost (which
    # is what tripped AdsBot's 10s landing-page health threshold and
    # caused V484 ad disapprovals). secondary_loop refreshes every 2 hr,
    # but it has a 5-min warmup sleep, so we ALSO fire one here at boot.
    try:
        from routes.persona_pages import refresh_persona_stats_cache as _v488_persona_refresh
        _r = _v488_persona_refresh()
        if _r:
            print(f"[WORKER] V488 persona_stats startup refresh: "
                  f"violations={_r.get('total_violations')} "
                  f"owners={_r.get('total_owners')} "
                  f"contractors={_r.get('total_contractors')}", flush=True)
    except Exception as _v488_e:
        print(f"[WORKER] V488 persona_stats startup refresh failed (non-fatal): "
              f"{_v488_e}", flush=True)

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

    # V488 IRONCLAD: Secondary loop (violations + staleness + stats_cache)
    # — split off so it stops blocking the permit cycle.
    t_secondary = threading.Thread(target=secondary_loop,
                                   name='secondary_loop', daemon=True)
    t_secondary.start()
    threads.append(t_secondary)
    print(f"[WORKER] Started: secondary_loop", flush=True)
    time.sleep(15)

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
