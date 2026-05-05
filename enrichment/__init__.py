"""V530: enrichment scheduler. Extracted from server.py:9040-9057
inline `_enrichment_daemon` per the V524 (digest) template.

The actual enrichment work lives in license_enrichment.py and
web_enrichment.py, both already top-level modules. V530 only lifts
the SCHEDULER thread that drives them — keeping the package small
and focused.

Public API:
- start_thread(): idempotent spawn of the scheduler thread.
- enrichment_daemon(): the loop body (exposed for tests).

Per the durable rules:
- No new feature code in server.py — new code goes into modules.
- Bug-fix CODE_V### PRs ship with regression tests.
- Always run `pytest tests/test_smoke.py tests/test_imports.py
  tests/test_routes.py -q` locally before pushing.

V530 fixes-by-extraction the V475-class silent-thread-drop bug for
the enrichment_daemon thread the same way V524 pinned it for
email_scheduler. See tests/test_enrichment.py.
"""
from .scheduler import enrichment_daemon, start_thread

__all__ = ['enrichment_daemon', 'start_thread']
