FROM python:3.11-slim

# V470 PR 1: procps for pkill (used by start.sh to kill leftover gunicorn
# processes from a wedge before starting fresh — addresses the 2026-04-29
# 90-min outage where pid 7 stuck in module-import phase blocked port 10000
# while a new pid 17577 booted normally but couldn't accept connections).
RUN apt-get update && apt-get install -y --no-install-recommends procps && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod +x /app/start.sh

EXPOSE 5000

# Render sets $PORT dynamically; fall back to 5000 for local dev.
# V229 B8: added --threads 4. render.yaml's startCommand was claiming
# --workers 2 --threads 4 but is ignored because a Dockerfile is present,
# so only --workers 1 ever ran in prod. Kept workers=1 deliberately (the
# scheduled_collection daemon + _collector_started flag are process-
# local; two workers would spawn two daemon threads) and added threads=4
# so the admin dashboard / JSON endpoints stay responsive while a
# force-collect or enrich call is in flight on the single worker.
# V448 (CODE_V447 follow-on): bumped --max-requests 500 → 5000.
# The 500 cap was recycling the worker every 1–2 hours under normal
# traffic (admin probes + user requests + healthcheck pings), and
# scheduled_collection cycles take 60–90 min. Cycles were getting
# killed mid-flight, so the daemon never wrote completion logs and
# top cities went stale. 5000 buys ~80h of uptime, plenty for the
# daemon to complete several cycles per worker. WAL is capped at
# 64MB by V445 and process memory tops out around 1.2GB / 2GB so
# the recycle is no longer needed for leak protection at the old
# rate.
# V452 (CODE_V448 follow-on): bumped --threads 4 → 12. After V450
# unblocked the collector, city-page queries (slow ones in
# city_trade_landing / _get_property_owners) saturated the 4-thread
# pool every time more than 2-3 users hit a slow page concurrently.
# Render's gateway returned 502 because /healthz had no thread to
# serve. 12 threads gives headroom while we work on the slow queries
# themselves; gthread workers are cheap (each is a Python thread, not
# a process), and the gunicorn timeout=120 still bounds runaway calls.
# V470 PR 1 (CODE_V470 Phase 3B): switch from inline flags to gunicorn.conf.py
# via start.sh. start.sh pkills any leftover gunicorn before exec'ing the new
# one — prevents the wedged-process port-conflict pattern we saw 2026-04-29.
# Config differences from the prior inline cmd:
#   - threads 12 → 8 (lower memory pressure on 2GB box)
#   - preload_app=True (~30-60MB memory savings via copy-on-write)
#   - post_fork DB engine reset (prevents stale shared-connection corruption)
#   - post_request memory log when worker RSS > 1500MB (visibility)
#   - max_requests 5000 → 1000 (catch slow leaks earlier)
CMD ["bash", "/app/start.sh"]
