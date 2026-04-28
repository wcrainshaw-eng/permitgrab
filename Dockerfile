FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

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
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT:-5000} --workers 1 --threads 4 --timeout 120 --graceful-timeout 30 --max-requests 5000 --max-requests-jitter 500 server:app"]
