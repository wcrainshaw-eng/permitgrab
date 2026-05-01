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
# V452 bumped --threads 4 → 12 to handle slow city-page queries.
# V481 (worker/web split): dropped --max-requests / --max-requests-jitter.
# The historical reason for periodic recycling was leak protection
# from in-process daemons (collection / enrichment / digest). With the
# daemons moved to permitgrab-worker, the web process is lightweight
# and stable — recycling just dropped in-flight requests and forced a
# 30s graceful-timeout window every 5K requests for no benefit.
# Bumped --workers 1 → 4 now that no daemon-thread locality is needed.
# 4 workers × 8 threads = 32 concurrent request slots, each fully
# isolated from the daemons.
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT:-5000} --workers 4 --threads 8 --timeout 120 --graceful-timeout 30 server:app"]
