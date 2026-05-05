"""V536: lead scoring rubric (calculate_lead_score + add_lead_scores).
Extracted from server.py:3461-3598.

Pure-function module — no Flask context, no DB, no I/O. Bug here =
wrong lead ranking on city pages → user-visible quality regression.
Tests in tests/test_lead_scoring.py pin every score bracket.

Per the durable rules:
- No new feature code in server.py — new code goes into modules.
- Bug-fix CODE_V### PRs ship with regression tests.
- Always run pytest tests/test_smoke.py + test_imports.py +
  test_routes.py locally before pushing.
"""
from .score import calculate_lead_score, add_lead_scores

__all__ = ['calculate_lead_score', 'add_lead_scores']
