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
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT:-5000} --workers 1 --threads 4 --timeout 120 --graceful-timeout 30 --max-requests 500 --max-requests-jitter 50 server:app"]
