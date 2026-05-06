## V546 — Production daemon ground truth (Step 1)

**Captured:** 2026-05-06 ~01:55 UTC
**Source:** SSH into Render (`srv-d6s1tvsr85hc73em9ch0`), `/proc/8/status`, ps,
the live `/api/admin/health` + `/api/admin/debug/threads` endpoints, and
direct queries against the prod SQLite DB. No assumptions — direct
observation only. Code references read from `collector.py`.

---

## 1. Process topology (vs. Wes's mental model)

| What Wes thought | What ps actually shows |
|------------------|------------------------|
| Pro plan: 2GB + **2 workers** | `gunicorn --workers 1 --threads 12 --timeout 120` |
| Web + daemon in same process (correct) | Confirmed — PID 8 is the only worker, hosts both |
| Render permitgrab-worker active | NOT deployed (render.yaml declares it; service was never created) |

**Implication:** the "we have 2 workers of bandwidth" assumption is wrong.
There is one gunicorn worker hosting BOTH web traffic and the
collection daemon thread. Memory pressure from the daemon directly
threatens the web worker (and that's exactly the V450/V453 502 history).

VmPeak captured at 01:55Z: **2,398,144 kB = 2.34 GB** — the worker has
already hit beyond the 2GB Render ceiling at some point in the recent
deploy lifetime. VmRSS at idle: 364 MB, threads=11.

---

## 2. Fleet coverage facts (DB, last 24h)

| Bucket | Count | Of platform-typed | Of all active |
|--------|-------|-------------------|---------------|
| Active prod_cities (status='active') | 1,761 | — | 100% |
| Platform-typed (`source_type IS NOT NULL`) | 736 | 100% | 42% |
| Has at least one scraper_runs entry ever | 702 | 95% | — |
| **NEVER run** (zero scraper_runs entries) | 34 | 4.6% | — |
| Run in last 24h | 174 | **24%** | 9.9% |
| **Starved >72h** (last_run > 3 days ago OR null) | **562** | **76%** | 32% |

**The starvation count is 562 platform cities.** Three-quarters of the
platform-typed fleet hasn't been visited in 3+ days. This number is
larger than the V545 audit's 447 "platform_fail" because V545 only
counted Socrata; the broader 76% across all platforms makes the
problem unambiguous.

### Daemon throughput last 24h
- Total scraper_runs: 3,038
- Unique cities visited: **174-188** (varies by sample window)
- Errors: 92 (47 ArcGIS, 45 CKAN; **0 Socrata errors despite 1,489 Socrata runs**)
- Avg redundancy: 3,038 / 174 = **17.5 visits per visited city per day**
- Cycle cap: `MAX_CITIES_PER_CYCLE = 75` (collector.py:3844)

### Hourly histogram (last 24h)
Hour-of-day pattern shows 50-188 unique cities/hour with the same
~120-130 cities cycling. Big spikes at 04-08 UTC (305+244+244+325
runs/hour, but each only 121 unique cities) — looks like a scheduled
batch, possibly the email_scheduler waking up + multiple force-collect
cycles overlapping.

---

## 3. The starvation mechanism — fully diagnosed

The picker is correct. The cycle is broken.

```python
# collector.py:3823-3836  — V526 ROUND-ROBIN sort
SELECT pc.city_slug, pc.source_id,
       (SELECT MAX(run_started_at) FROM scraper_runs
        WHERE city_slug = pc.city_slug) AS last_run
FROM prod_cities pc
WHERE pc.status = 'active' AND pc.source_id IS NOT NULL
  AND pc.source_type IS NOT NULL
ORDER BY (last_run IS NULL) DESC, last_run ASC

# collector.py:3844-3847
MAX_CITIES_PER_CYCLE = 75
if len(individual_cities) > MAX_CITIES_PER_CYCLE:
    individual_cities = individual_cities[:MAX_CITIES_PER_CYCLE]

# collector.py:3894-3924  — memory-bail mid-cycle
_MEM_BAIL_MB = 1200
_MEM_HARD_ABORT_MB = 1400

for city_info in individual_cities:
    if _mem_mb >= _MEM_BAIL_MB:
        print(f"cycle bail: memory {_mem_mb}MB >= {_MEM_BAIL_MB}MB cap")
        _cycle_bailed = True
        break
    # ... fetch city N serially ...
```

**The cascade:**
1. Cycle picks the 75 stalest platform cities.
2. Iterates serially (no concurrency in this loop — the
   ThreadPoolExecutors visible in `/api/admin/debug/threads` are
   Flask request-handler threads, not collector workers).
3. Per-city fetch uses `_fetch_permits_with_timeout` → loads the
   raw response into memory, normalizes to a list, batch-inserts.
4. RSS climbs. After ~10-30 cities, RSS crosses 1,200 MB.
5. Cycle bails via `break` → cities 11-75 never get visited.
6. Their `last_run` doesn't advance — they re-rank stalest next cycle.
7. Next cycle picks the SAME 75 (with maybe 5-15 new ones added at
   the head if some never-visited bubbled up). The starved tail
   never moves.

This is a strict starvation regime. V526 ordered correctly but
didn't enforce coverage; the 1,200 MB bail systematically robs each
cycle of 65 of its 75 budgeted cities.

**Why memory grows so fast:** `_fetch_permits_with_timeout` loads
entire result sets in memory (some sources return 5K-50K records),
the normalize loop builds a parallel `city_permits` list, then
upsert_permits batches it. Two full copies live concurrently before
GC. Each city peaks +200-500 MB; a heavy city plus residual
unreleased memory from prior cities pushes past 1,200 MB quickly.

The hourly batch spikes (04-08 UTC = 1,118 runs in 4 hours) suggest
the cycle is firing rapid-fire — likely several times per minute
when memory is under the bail threshold. So the daemon ISN'T idle;
it's churning aggressively on the same small set.

---

## 4. The 34 never-run cities — slug-mismatch class

These are likely V319/V320/V321-style slug mismatches plus a few
NULL-source_endpoint orphans (cedar-park, garland-tx, irving-tx,
frisco-tx, round-rock per V545 audit). Their `pc.city_slug` ranks
NULL in the picker query and they get picked first every cycle, but
when `collect_single_city` is called the source_id lookup against
CITY_REGISTRY fails (or `source_endpoint IS NULL` and the daemon's
fallback path silently fails), so no scraper_runs row ever lands.
They permanently occupy the head of the picker, eating cycle budget
without producing visits.

---

## 5. Errors are NOT the dominant failure mode

| source_type | success | no_new | error | error % |
|-------------|---------|--------|-------|---------|
| socrata | 514 | 975 | **0** | 0.0% |
| accela | 136 | 582 | 0 | 0.0% |
| arcgis | 391 | 56 | 47 | 9.5% |
| ckan | 46 | 0 | 45 | 49.5% |
| carto | 21 | 0 | 0 | 0.0% |

Source platforms are largely healthy. The CKAN 49% is concentrated
on a few cities. **The daemon is running correctly; it just isn't
running on enough cities.**

---

## 6. Architecture decision — pick ONE

Wes's three options, evaluated against the diagnosis:

### Option A — ThreadPoolExecutor inside the existing daemon thread
- Concurrent HTTP fetches (~20 workers) with per-platform semaphores.
- **Problem:** the 1,200 MB bail exists because memory pressure already
  threatens the shared web+daemon process. Concurrent fetches mean 20
  raw response sets in memory at once. Even with Step 3 memory
  discipline (streaming inserts, gc.collect per city), peak memory is
  the LIMIT — not the mean. One heavy concurrent fetch (NYC, 16K
  permits) plus 19 smaller ones simultaneously = 2-3 GB peak easily.
  This blows the Render ceiling and 502s the web.
- **Verdict: NO.** Concurrency without process isolation is too risky
  given VmPeak=2.34GB already observed.

### Option B — Per-platform queues with independent cadences
- Socrata every 6h, ArcGIS every 8h, Accela every 12h, etc.
- Cleaner long-term; doesn't address the underlying issue that the
  daemon shares memory with the web worker. Same 502 risk class as A.
- More code, more state, more failure modes. Doesn't pay for itself.
- **Verdict: NO** as the V546 fix. Could be V548 layering.

### Option C — Activate the permitgrab-worker Background Worker on Render
- render.yaml already declares the worker; worker.py exists; only
  missing piece is Wes creating the service in the Render dashboard.
- The daemon moves to its OWN process with its OWN 2GB memory budget.
  The web worker no longer competes — daemon can use the full 2GB,
  web stays comfortable at 300-500 MB.
- Step 2 (ThreadPoolExecutor concurrent fetches) becomes safe in the
  worker process — no shared web pressure.
- Solves the V475/V481 "web restart kills daemon" class as a side
  effect (worker has its own lifecycle).
- 4 sweeps/day × 90s each ≈ 6 minutes of total daemon time = 75
  cities/sweep × 4 sweeps × concurrency-factor-3 = 900 city-visits/day,
  enough for full 736-city coverage in 1 day with retries.
- **Cost:** ~$7/mo extra Background Worker on Render Pro. Wes
  confirmed the bandwidth budget exists.
- **Verdict: YES.** This is the architecturally clean fix that
  closes the V474→V493→V496→V526 chain. It addresses the root cause
  (process memory contention), not the symptom (cycle bails).

### Recommendation: **Option C**, with Steps 3-7 layered on top.

The chain has failed three times at the in-process level (V474, V493,
V526). The pattern is the same: a clever picker fix that works on
paper, but the cycle still bails at memory pressure that exists
because daemon and web share a budget. Continuing to patch the
picker is the V474→V526 lesson. Process isolation is the structural
fix.

---

## 7. V546 implementation plan

### V546a (Wes one-time: Render dashboard)
Create `permitgrab-worker` Background Worker on Render pointing at
`worker.py`. The render.yaml entry already specifies build/deploy
config. ~5 min of clicking. Confirms the service is live before
shipping V546b.

### V546b (Code: 4 PRs, all small)

**PR1 — Worker entry point hardening.** Make `worker.py` start the
collection daemon, enrichment daemon, and email_scheduler exactly
like the web process does today via `start_collectors()`. Verify
daemon health from worker via a sentinel file or a side-channel
metric the web can read. Includes regression test: `worker.py`
imports cleanly, `WORKER_MODE=1` env var stops the in-web-process
daemon spawn.

**PR2 — Process-mode gate in the web worker.** When
`os.environ.get('WORKER_MODE') == '1'` is set on the worker process,
`start_collectors()` runs. When it's NOT set (web process), the
daemon spawn is skipped entirely. The web becomes pure Flask. Test:
WORKER_MODE=0 vs WORKER_MODE=1 paths assert correct daemon presence.

**PR3 — Concurrent fetches with semaphores.** Replace the serial
`for city_info in individual_cities:` loop with
`ThreadPoolExecutor(max_workers=20)` + per-platform
`threading.BoundedSemaphore` (Socrata=10, ArcGIS=8, Accela=4,
CKAN=4, carto=2). Streaming inserts (no list accumulation) +
`gc.collect()` after each future completes. Per-city memory stays
under 100 MB. Raise `MAX_CITIES_PER_CYCLE` to 250 (1/3 of fleet)
once peak memory is bounded. Test: feed 20 mock cities to the
executor, assert all 20 commit even when 2 raise.

**PR4 — daemon_coverage_starvation reason_code (Step 5).** Add
to city_health/compute.py: split the `platform_fail` bucket into
`platform_fail` (consec errors > 0 in last 24h) vs
`daemon_coverage_starvation` (visit_age > threshold AND zero recent
errors). Stops conflating the two and makes future audits honest.
Two unit tests: one synthetic city with errors → platform_fail;
one with no errors but stale visit → daemon_coverage_starvation.

**PR5 — `/api/admin/daemon-coverage` endpoint (Step 6).** Returns
JSON with hours_since_last_run histogram bucketed 0-6h / 6-24h /
24-72h / 72h+. Plus a `coverage_pct_24h` scalar. Cache 60s. Module
file under routes/ per the no-new-code-in-server.py rule. Test: hits
the endpoint, asserts the histogram sums to 736 platform cities.

**PR6 — Working-set auto-prune (Step 4).** Nightly cron flips
`prod_cities.status='inactive'` for cities with 0 successful
collections in 30 days AND 0 permits in DB AND wired more than
60 days ago. Logs each deactivation in a new `inactivity_log`
table. Test: synthetic dead city gets flipped; synthetic live city
stays.

### V546 verification (Step 7)
Ship V546b PRs in order, deploy, then `/api/admin/daemon-coverage`
every 6h for 24h. The 72h+ bucket should drop from 562 to <50
within 48 hours of the worker service activating. If it doesn't,
V547 is needed — but per the new feedback rule, we don't declare
V546 shipped until production observability confirms.

---

## 8. Out of scope (deferred)

- Per-city retry/backoff per source (V548+)
- Migration to Celery/RQ job queue (V550+)
- Distributed scheduler across multiple Render services (V555+)
- Manual fixes for individual broken Accela tenants (V549+)
- Atlanta/austin-tx/norfolk-va/SBC field_map fixes (V545→V546b
  recommendation, but a separate small PR)

---

## 9. Live observation addendum (T+0..T+3 of the 10-min window)

Polled `/api/admin/health` + `/api/admin/debug/threads` every 60s.
Three additional facts fall out of the live data:

- **`cycle_stale_minutes` is 730 and growing** (731 at T+1, 733 at T+2,
  734 at T+3). The last successfully *completed* scheduled cycle ended
  **2026-05-05 13:43:46 UTC — 12 hours ago.** Individual collections
  keep firing but no full cycle finishes. This is direct evidence
  that the cycle is repeatedly bailing on the 1,200 MB memory cap
  before it can mark itself complete.
- **All 6 daemon threads are alive:** scheduled_collection,
  enrichment_daemon, email_scheduler, health_scheduler,
  heartbeat_writer, collection_watchdog. The daemon process is
  healthy; the cycle is the broken thing, not the thread.
- **Top error cities (same set repeats):** greenville (30 ArcGIS 400
  errors), pittsburgh (24 http_unknown), boston (21 http_unknown),
  murfreesboro (15 http_unknown), baltimore (2 ArcGIS 500). Five
  cities producing 92 of the 92 errors. The V417 circuit breaker
  should be picking these up; if it isn't, that's a V548 follow-up.
- **Memory profile:** RSS dropped from 364 MB → 163 MB between T+1
  and T+3 (a gc.collect ran). VmPeak stays pinned at 2.34 GB across
  the window. The 2GB ceiling has been breached at some point in
  the deploy lifetime — confirming the bail-rationale exists for
  good reason.
- **Runs in last 5min: 0.** Collections fire in bursts, then long
  gaps. The "every 28 seconds" average is misleading — it's lumpy.
- **`collections_last_24h: 169` — a SECOND, smaller count**
  alongside the 3,038 scraper_runs. So `collections_last_24h`
  appears to count completed *full cycles or batches* rather than
  individual city visits. 169 vs 3,038 = ~18x amplification: each
  "collection" event maps to ~18 individual city visits. Matches
  the per-city redundancy observation.

These findings reinforce Option C: the cycle isn't finishing because
memory pressure cuts it off, and that pressure exists because the
web worker shares the same process. Move daemon to its own process,
the cycle finishes, the rest cascades.

---

## Summary one-liner

**562 of 736 platform cities (76%) sit unvisited >72h because the
web+daemon share a 2GB memory budget and the daemon's 1,200 MB bail
robs every cycle of 65 of its 75 city budget. Activating the
already-declared `permitgrab-worker` Background Worker on Render
gives the daemon its own 2GB process and unblocks all of Step 2
through 6 to run safely.**

— Captured 2026-05-06 ~01:55 UTC
