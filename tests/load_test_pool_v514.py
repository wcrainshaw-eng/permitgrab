"""V514 / V523 pool fix — local concurrency load test.

Spawns gunicorn-shaped concurrent load against a real local Postgres
to exercise the V514 patch in db_engine.py:
- ThreadedConnectionPool sized min=2 / max=25
- 5s circuit breaker (was 10-retry-30s exponential)
- 60s pool monitor thread
- PgConnection.__del__ GC safety net
- _bg_conn_semaphore caps daemon threads at 10 slots

Scenarios:
  Scenario A — production-shape concurrency.
    12 web threads + 3 daemon threads × 100 iterations.
    Expected: all queries succeed, pool plateaus well below max=25.

  Scenario B — over-pressure burst.
    40 threads each grabbing a connection and sleeping 6s before
    releasing (40 > maxconn=25 → some must wait or fail).
    Expected: ~25 succeed immediately, the rest either succeed within
    the 6s sleep window OR raise PoolError after ~5s — and PoolError
    arrives in ~5s (the V514 circuit breaker), NOT ~30s (the old V67
    retry loop).

Pre-cutover this script catches:
  - Pool sizing too small (Scenario A would error)
  - Circuit breaker not firing (Scenario B would hang past 5s)
  - GC safety net broken (steady state would leak slots)

Run: cd repo root, ensure local Postgres is up + permitgrab_loadtest db
exists, then `DATABASE_URL=postgresql://localhost:5432/permitgrab_loadtest python3 tests/load_test_pool_v514.py`.
"""
from __future__ import annotations

import os
import sys
import threading
import time

# Force USE_POSTGRES at import time. db_engine evaluates DATABASE_URL
# at module load, so set it before the import.
if "DATABASE_URL" not in os.environ:
    os.environ["DATABASE_URL"] = "postgresql://localhost:5432/permitgrab_loadtest"

# Add repo root to sys.path so `import db_engine` works when run from
# tests/.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db_engine as de
import psycopg2.pool


def _seed_pool():
    """Spin up the pool and verify it is enabled."""
    assert de.USE_POSTGRES, "DATABASE_URL not picked up — db_engine thinks it's SQLite mode"
    ok = de.enable_pg_pool()
    assert ok, "enable_pg_pool() returned False"
    assert de.is_pg_pool_enabled(), "pool not flagged enabled"


def _query_once(thread_label, ix):
    """Single get → query → put round-trip via the public API.
    Returns (ok: bool, elapsed_s, error_str_or_None)."""
    t0 = time.monotonic()
    conn = None
    try:
        conn = de.get_connection()
        cur = conn.execute("SELECT count(*) FROM smoke")
        row = cur.fetchone()
        assert row is not None and row[0] == 1000, f"unexpected row: {row}"
        elapsed = time.monotonic() - t0
        return (True, elapsed, None)
    except Exception as e:
        elapsed = time.monotonic() - t0
        return (False, elapsed, f"{type(e).__name__}: {e}")
    finally:
        if conn is not None:
            try:
                de.put_connection(conn)
            except Exception:
                pass


def scenario_a_steady_state(per_thread=100):
    """12 web threads + 3 daemon threads, each doing per_thread iters.

    The daemon threads use the auto-detect path (thread name contains
    'scheduled_collection' / 'enrichment_daemon' / 'email_scheduler')
    so the V65 background semaphore caps them at 10 slots.
    """
    print("=== Scenario A: 12 web + 3 daemon × {} iters ===".format(per_thread))
    web_results, daemon_results = [], []
    web_lock, daemon_lock = threading.Lock(), threading.Lock()

    def web_worker(label):
        local = []
        for i in range(per_thread):
            local.append(_query_once(label, i))
        with web_lock:
            web_results.extend(local)

    def daemon_worker(label):
        local = []
        for i in range(per_thread):
            local.append(_query_once(label, i))
        with daemon_lock:
            daemon_results.extend(local)

    threads = []
    for i in range(12):
        t = threading.Thread(target=web_worker, args=(f"web-{i}",), name=f"gthread-{i}", daemon=True)
        threads.append(t)
    for name in ("scheduled_collection", "enrichment_daemon", "email_scheduler"):
        t = threading.Thread(target=daemon_worker, args=(name,), name=name, daemon=True)
        threads.append(t)

    t0 = time.monotonic()
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=180)
    elapsed = time.monotonic() - t0

    web_ok = sum(1 for ok, *_ in web_results if ok)
    web_fail = sum(1 for ok, *_ in web_results if not ok)
    daemon_ok = sum(1 for ok, *_ in daemon_results if ok)
    daemon_fail = sum(1 for ok, *_ in daemon_results if not ok)

    print(f"  web: {web_ok} ok / {web_fail} fail of {len(web_results)} total queries")
    print(f"  daemon: {daemon_ok} ok / {daemon_fail} fail of {len(daemon_results)} total queries")
    print(f"  wall time: {elapsed:.2f}s")
    if web_fail or daemon_fail:
        # show first 5 errors
        for ok, et, err in web_results + daemon_results:
            if not ok:
                print(f"    err: {err} (after {et*1000:.1f}ms)")
                break
    pool = de._pg_pool
    print(f"  post-test pool: in_use={len(pool._used)} idle={len(pool._pool)} max={pool.maxconn}")
    return web_fail == 0 and daemon_fail == 0 and len(pool._used) == 0


def scenario_b_overpressure_burst(n_threads=40, hold_s=6.0):
    """Spawn n_threads (> maxconn=25), each holding a connection for
    hold_s seconds. We expect:
      - ~25 succeed immediately (size of the pool)
      - The remaining ~15 wait. The V514 circuit breaker kicks in at
        timeout_s=5.0 and raises PoolError. Their elapsed time on
        failure should be ~5s, NOT ~30s (the old V67 30s-cap exponential).
    """
    print(f"=== Scenario B: {n_threads}-thread burst, each holds for {hold_s}s ===")
    results = []
    lock = threading.Lock()

    def burst_worker(ix):
        t0 = time.monotonic()
        conn = None
        try:
            conn = de.get_connection()
            cur = conn.execute("SELECT pg_sleep(%s)", (hold_s,))
            cur.fetchall()
            elapsed = time.monotonic() - t0
            with lock:
                results.append(("ok", ix, elapsed, None))
        except Exception as e:
            elapsed = time.monotonic() - t0
            with lock:
                results.append(("fail", ix, elapsed, f"{type(e).__name__}: {e}"))
        finally:
            if conn is not None:
                try:
                    de.put_connection(conn)
                except Exception:
                    pass

    threads = [
        threading.Thread(target=burst_worker, args=(i,), name=f"burst-{i}", daemon=True)
        for i in range(n_threads)
    ]
    t0 = time.monotonic()
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=hold_s + 30)
    wall = time.monotonic() - t0

    ok_count = sum(1 for r in results if r[0] == "ok")
    fail_count = sum(1 for r in results if r[0] == "fail")
    fail_elapsed = [r[2] for r in results if r[0] == "fail"]
    pool = de._pg_pool

    print(f"  ok: {ok_count} fail: {fail_count}")
    print(f"  wall time: {wall:.2f}s")
    if fail_elapsed:
        # All failures should be ~5s (the circuit breaker timeout)
        max_fail = max(fail_elapsed)
        min_fail = min(fail_elapsed)
        avg_fail = sum(fail_elapsed) / len(fail_elapsed)
        print(f"  failure timing: min={min_fail:.2f}s max={max_fail:.2f}s avg={avg_fail:.2f}s")
        # Pass condition: max failure timing < 7s. The V67 retry loop
        # would push failures to ~30s; if we see anything close to that,
        # the V514 circuit breaker isn't doing its job.
        breaker_ok = max_fail < 7.0
        print(f"  V514 circuit breaker active (max fail < 7s): {'YES' if breaker_ok else 'NO — REGRESSION'}")
    else:
        breaker_ok = True
        print("  no failures — pool was big enough to absorb the burst (unexpected at 40>25, "
              "but means more ok=succeeded waiters than expected)")
    print(f"  post-burst pool: in_use={len(pool._used)} idle={len(pool._pool)} max={pool.maxconn}")
    return breaker_ok


if __name__ == "__main__":
    print(f"DATABASE_URL = {os.environ['DATABASE_URL']}")
    _seed_pool()
    print(f"pool created (min={de._pg_pool.minconn} max={de._pg_pool.maxconn})")

    a_ok = scenario_a_steady_state(per_thread=100)
    b_ok = scenario_b_overpressure_burst(n_threads=40, hold_s=6.0)

    overall = a_ok and b_ok
    print()
    print("=" * 60)
    print(f"  Scenario A (steady state): {'PASS' if a_ok else 'FAIL'}")
    print(f"  Scenario B (burst circuit breaker): {'PASS' if b_ok else 'FAIL'}")
    print(f"  OVERALL: {'PASS' if overall else 'FAIL'}")
    print("=" * 60)
    sys.exit(0 if overall else 1)
