FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# V508: install Chromium for Playwright-based scraping (SBCO Accela
# JS-SPA path). First attempt used `playwright install --with-deps
# chromium` which failed because Playwright 1.45's bundled apt
# dependency list still references `ttf-unifont` + `ttf-ubuntu-font-family`
# (renamed to `fonts-unifont` + `fonts-ubuntu` in modern Debian).
# Workaround: install the system deps explicitly with the correct
# modern package names, then `playwright install chromium` (no
# --with-deps so Playwright doesn't try to apt-install ttf-* itself).
RUN apt-get update && apt-get install -y --no-install-recommends \
        libnss3 libnspr4 libdbus-1-3 libatk1.0-0 libatk-bridge2.0-0 \
        libcups2 libdrm2 libxkbcommon0 libatspi2.0-0 libx11-6 \
        libxcomposite1 libxdamage1 libxext6 libxfixes3 libxrandr2 \
        libgbm1 libpango-1.0-0 libcairo2 libasound2 \
        fonts-liberation fonts-unifont fonts-ubuntu \
    && rm -rf /var/lib/apt/lists/* \
    && python -m playwright install chromium

COPY . .

EXPOSE 5000

# Render sets $PORT dynamically; fall back to 5000 for local dev.
# V229 B8: added --threads 4. V452 bumped to 12 for slow city-page
# queries.
#
# V496 2026-05-04: REVERTED --workers 4 → 1.
# V481 had bumped to 4 on the premise that the permitgrab-worker
# Background Worker service was running the daemons. That service
# was declared in render.yaml but never created in the Render
# dashboard, so V493 had to restore the in-process daemons. Nobody
# reverted the worker count. Result: with 4 workers, each has its
# own process-local _collector_started flag and threading.enumerate()
# state — only ONE worker (whichever handled /api/admin/start-collectors)
# actually has the 3 daemon threads. When V455/V457 SIGTERMs that
# worker on the memory ceiling, gunicorn respawns a fresh daemonless
# worker, and the next /start-collectors call may land on a different
# worker whose flag still says "running" → daemon stays dead silently
# for hours. Single worker eliminates the cross-process state
# mismatch and lets V455/V457's SIGTERM-recycle pattern work as
# originally intended (the new worker auto-spawns daemons on the
# first request via _ensure_deferred_startup_spawned, restored in V496).
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT:-5000} --workers 1 --threads 12 --timeout 120 --graceful-timeout 30 server:app"]
