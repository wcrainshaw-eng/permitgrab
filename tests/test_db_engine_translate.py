"""V525 regression tests for db_engine._translate_sql.

Cutover attempt 2 (USE_POSTGRES=true at 16:55 UTC) failed criterion (a)
because db_engine._translate_sql rewrote the bare `datetime('now')`
form but NOT the multi-arg `datetime('now', '-N units')` /
`date('now', '-N units')` forms. Postgres has no `datetime(unknown,
unknown)` function — every freshness window query 500'd, taking the
whole site to 503.

These tests pin every variant found in the codebase as of the V525 fix.
They MUST fail on the unpatched _translate_sql and pass on the patched
version. Each assertion names the production file:line that uses that
form so a future search for "where is this used" lands.
"""
from __future__ import annotations

import os
import re

# Force USE_POSTGRES so _translate_sql actually runs the rewrites.
# (When USE_POSTGRES is false, _translate_sql returns the input
# unchanged, which would make these tests trivially pass.)
os.environ.setdefault("DATABASE_URL", "postgresql://localhost:5432/permitgrab_loadtest_unused")

import db_engine  # noqa: E402


def _x(sql):
    """Helper: run the translator and return the output."""
    return db_engine._translate_sql(sql)


# ---------------------------------------------------------------------
# datetime('now', '-N units') — TIMESTAMP windows
# ---------------------------------------------------------------------

def test_datetime_now_minus_seven_days():
    """server.py:1748, collector.py:201/3538/4039, web_enrichment.py."""
    out = _x("DELETE FROM scraper_runs WHERE run_started_at < datetime('now', '-30 days')")
    assert "datetime(" not in out.lower(), (
        f"V525 regression: datetime() left untranslated: {out!r}"
    )
    assert "INTERVAL" in out and "-30 days" in out, (
        f"V525 regression: expected INTERVAL '-30 days', got: {out!r}"
    )


def test_datetime_now_minus_one_day():
    out = _x("WHERE collected_at > datetime('now', '-1 day')")
    assert "datetime(" not in out.lower()
    assert "-1 day" in out
    assert "INTERVAL" in out


def test_datetime_now_minus_one_hour():
    """collector.py:3381 / 4039."""
    out = _x("WHERE run_started_at > datetime('now', '-1 hour')")
    assert "datetime(" not in out.lower()
    assert "-1 hour" in out
    assert "INTERVAL" in out


def test_datetime_now_minus_one_minute():
    """worker.py:223 / 228."""
    out = _x("SELECT COUNT(*) FROM permits WHERE collected_at > datetime('now', '-1 minute')")
    assert "datetime(" not in out.lower()
    assert "-1 minute" in out
    assert "INTERVAL" in out


def test_datetime_now_minus_twelve_hours():
    """collector.py:416."""
    out = _x("AND collected_at > datetime('now', '-12 hours')")
    assert "datetime(" not in out.lower()
    assert "-12 hours" in out


def test_datetime_now_no_space_after_comma():
    """admin.py: some callsites omit the space — datetime('now','-6 hours')."""
    out = _x("WHEN sr.last_run > datetime('now','-6 hours') THEN 'fresh'")
    assert "datetime(" not in out.lower(), (
        f"V525 regression: no-space-after-comma form not translated: {out!r}"
    )
    assert "-6 hours" in out


def test_datetime_now_minus_ninety_days():
    """server.py:1523."""
    out = _x("AND sr.run_started_at > datetime('now', '-90 days')")
    assert "datetime(" not in out.lower()
    assert "-90 days" in out


def test_datetime_now_minutes_form():
    """server.py:5113 — f-string already resolved by the time it
    reaches the translator: datetime('now', '-15 minutes')."""
    out = _x("WHERE collected_at >= datetime('now', '-15 minutes')")
    assert "datetime(" not in out.lower()
    assert "-15 minutes" in out


# ---------------------------------------------------------------------
# date('now', '-N units') — DATE/TEXT windows
# ---------------------------------------------------------------------

def test_date_now_minus_seven_days():
    """collector.py:453."""
    out = _x("WHERE newest_permit_date >= date('now', '-7 days')")
    assert " date('now'" not in out, (
        f"V525 regression: date('now', ...) not translated: {out!r}"
    )
    assert "INTERVAL" in out
    assert "-7 days" in out


def test_date_now_minus_thirty_days():
    """db.py:3841, collector.py:150."""
    out = _x("SUM(CASE WHEN filing_date >= date('now', '-30 days') THEN 1 ELSE 0 END)")
    assert " date('now'" not in out
    assert "-30 days" in out


def test_date_now_no_space_after_comma():
    """server.py:4953-4957 — date('now','-7 days')."""
    out = _x("WHERE COALESCE(filing_date,issued_date,date) >= date('now','-7 days')")
    assert " date('now'" not in out, (
        f"V525 regression: date('now', ...) (no space) not translated: {out!r}"
    )
    assert "-7 days" in out


def test_date_now_minutes_in_query():
    out = _x("WHERE filing_date >= date('now', '-90 days')")
    assert " date('now'" not in out
    assert "-90 days" in out


# ---------------------------------------------------------------------
# Negative space: don't break the bare datetime('now') translation
# or any other already-handled form.
# ---------------------------------------------------------------------

def test_bare_datetime_now_still_works():
    """The pre-existing translation must still fire for bare datetime('now')."""
    out = _x("INSERT INTO foo (created_at) VALUES (datetime('now'))")
    assert "datetime('now')" not in out
    assert "NOW()" in out


def test_qmark_to_pct_s_still_works():
    out = _x("SELECT * FROM permits WHERE source_city_key = ? AND collected_at > datetime('now', '-7 days')")
    assert " ? " not in out and "= ?" not in out
    assert "%s" in out
    assert "datetime(" not in out.lower()


# ---------------------------------------------------------------------
# Regression hardening: routes/health.py builds the SQL via f-string,
# so the translator sees a fully-resolved string. Make sure it still
# matches.
# ---------------------------------------------------------------------

def test_health_endpoint_pattern_translates():
    """The exact shape /api/admin/health uses (routes/health.py:115)
    that broke cutover attempt 2 at 16:55 UTC."""
    fstring_resolved = "SELECT COUNT(*) FROM scraper_runs WHERE run_started_at >= datetime('now', '-1 hours')"
    out = _x(fstring_resolved)
    assert "datetime(" not in out.lower(), (
        f"V525 regression — the EXACT health endpoint query that 503'd "
        f"the site on USE_POSTGRES=true did not translate: {out!r}"
    )
    assert "INTERVAL" in out
