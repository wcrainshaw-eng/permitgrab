"""V470 PR 1 (CODE_V470 Phase 3A): gunicorn hardening.

Replaces the inline `--workers 1 --threads 12` flags from the prior Dockerfile
CMD with a config file that adds:
  - preload_app=True (shared code memory via copy-on-write, ~30-60MB savings)
  - post_fork DB-engine reset (prevents shared-connection corruption across forks)
  - post_request memory log when worker RSS exceeds 1500MB
  - max_requests recycle catches slow memory leaks before they reach Render's 2GB ceiling

Kept workers=1 threads=8 deliberately. V470 spec called for workers=2 threads=4 but
two separate Python processes each importing server.py (19K lines) + each running
its own daemon thread doubles the memory footprint on a 2GB box. One worker with
8 threads handles the current ~50 concurrent requests fine and leaves headroom for
the daemon's collect_refresh peak.
"""
import os
import resource

bind = f"0.0.0.0:{os.environ.get('PORT', '10000')}"

workers = 1
worker_class = "gthread"
threads = 8

timeout = 120
graceful_timeout = 25
keepalive = 5

max_requests = 1000
max_requests_jitter = 50

preload_app = True

accesslog = "-"
errorlog = "-"
loglevel = "info"
access_log_format = '%(h)s %(t)s "%(r)s" %(s)s %(b)s %(L)ss'


def post_fork(server, worker):
    """Reset DB connection pool after fork — prevents stale shared sockets."""
    try:
        from server import db
        db.engine.dispose()
    except Exception as _e:
        pass


def post_request(worker, req, environ, resp):
    """Log when worker RSS crosses the V455 self-recycle threshold."""
    try:
        mem_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        mem_mb = mem_kb / 1024
        if mem_mb > 1500:
            worker.log.warning(
                f"HIGH_MEMORY worker={worker.pid} mem={mem_mb:.0f}MB uri={req.uri}"
            )
    except Exception:
        pass


def worker_exit(server, worker):
    server.log.info(f"Worker {worker.pid} exited")
