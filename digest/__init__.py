"""V524: daily email digest scheduler. Extracted from server.py.

Public API (re-exported from .scheduler and .dedup):
- DIGEST_STATUS: dict mutated in-place to expose thread state.
- schedule_email_tasks(): the daemon loop body.
- start_thread(): idempotent spawn of the named scheduler thread.
- _log_digest(recipient, result, status): write digest_log row.

The pre-extraction shape (server.py:46 + 2439 + 8765-9017 + 9336-9343)
is preserved 1-to-1 for behavior; only the V515 dedup query and the
V276 bootstrap block were lifted into testable helpers in
digest/dedup.py.

Per the durable rules (CLAUDE.md):
- No new feature code in server.py — new code goes into modules.
- Bug-fix CODE_V### PRs ship with regression tests.

V524 fixes-by-extraction the V475 thread-spawn-drop and the V515
dup-fire bugs by giving them a stable home + tests; see
tests/test_digest.py.
"""
from .scheduler import DIGEST_STATUS, schedule_email_tasks, start_thread
from .dedup import _log_digest, digest_already_fired_today, bootstrap_seen_dates

__all__ = [
    'DIGEST_STATUS',
    'schedule_email_tasks',
    'start_thread',
    '_log_digest',
    'digest_already_fired_today',
    'bootstrap_seen_dates',
]
