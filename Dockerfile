# V508 attempt #3: use Microsoft's official Playwright Python image as
# base. It ships Ubuntu 22.04 (jammy) with the correct system deps +
# Chromium pre-installed, version-matched to the Playwright Python
# package. This sidesteps every Debian package-rename headache that
# bricked attempts #1 and #2 (ttf-unifont → fonts-unifont in bookworm,
# libasound2 → libasound2t64 in bookworm/t64 transition, etc.).
#
# Image is ~1.5 GB (vs python:3.11-slim's ~150 MB) but Render Standard
# plan handles it fine. The pre-installed Chromium saves ~3-5 min of
# build time on every deploy that would otherwise re-fetch + apt-install.
FROM mcr.microsoft.com/playwright/python:v1.45.0-jammy

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

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
