#!/bin/bash
# V470 PR 1 (CODE_V470 Phase 3C): kill leftover gunicorn before starting.
#
# After a worker wedges and gets killed by Render's deploy, the new container
# can spawn a fresh gunicorn while the old one is still in the pid table
# holding the port. We saw this on 2026-04-29 — pid 7 hung in module import
# for 30 min, blocked port 10000, while pid 17577 booted normally but couldn't
# accept connections. Manual SSH `kill -9 7` was the unblock.
#
# This pkill runs before exec'ing the new gunicorn so any leftover process is
# torn down first. `|| true` because pkill returns 1 if no match (normal case
# on a clean start).

pkill -f gunicorn 2>/dev/null || true
sleep 1
exec gunicorn server:app -c gunicorn.conf.py
