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

# V527c: db_engine.USE_POSTGRES is set ONCE at module load. If another
# test (test_smoke.py / test_imports.py) imported db_engine first
# without DATABASE_URL set, USE_POSTGRES is frozen at False — making
# every translation test below pass trivially against the SQLite
# passthrough path. Force-flip the module attr at test-module load
# so we're always exercising the Postgres branch. The per-test
# monkeypatch in test_sqlite_path_untouched still works (monkeypatch
# undoes itself at test teardown).
db_engine.USE_POSTGRES = True


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


# ---------------------------------------------------------------------
# INSERT OR IGNORE → INSERT ... ON CONFLICT DO NOTHING
# ---------------------------------------------------------------------

def test_insert_or_ignore_appends_on_conflict_do_nothing():
    """db.py:1477/1588/3656, license_enrichment.py:949/956/962/967,
    collector.py:2845. Without this, every PK/UNIQUE conflict on the
    Postgres path crashes the request."""
    out = _x("INSERT OR IGNORE INTO permit_history (address_key, permit_number) VALUES (?, ?)")
    assert "INSERT OR IGNORE" not in out
    assert "ON CONFLICT DO NOTHING" in out


def test_insert_or_ignore_with_trailing_semicolon():
    out = _x("INSERT OR IGNORE INTO foo (a) VALUES (?);")
    assert "ON CONFLICT DO NOTHING" in out
    assert out.rstrip().endswith(";")


def test_insert_or_ignore_skipped_when_caller_already_has_on_conflict():
    """Don't double-append if a caller is explicit."""
    out = _x("INSERT OR IGNORE INTO foo (a) VALUES (?) ON CONFLICT (a) DO UPDATE SET a=excluded.a")
    assert out.upper().count("ON CONFLICT") == 1, (
        f"V525 regression: ON CONFLICT got duplicated: {out!r}"
    )


# ---------------------------------------------------------------------
# INSERT OR REPLACE → INSERT (callsites add explicit ON CONFLICT)
# ---------------------------------------------------------------------

def test_insert_or_replace_strips_or_replace():
    """Translator strips OR REPLACE so plain INSERT survives. The 3
    real callsites in db.py (permits / system_state / discovered_sources)
    are patched to use explicit ON CONFLICT (col) DO UPDATE clauses;
    this test pins the translator-level fallback for any callsite that
    slips through unpatched. Behavior: fail-loud (PoolError on PK
    conflict) rather than silent-data-loss."""
    out = _x("INSERT OR REPLACE INTO system_state (key, value) VALUES (?, ?)")
    assert "INSERT OR REPLACE" not in out
    assert out.startswith("INSERT INTO")


def test_patched_db_py_callsites_use_explicit_on_conflict():
    """V525 hand-patched 3 callsites in db.py to drop OR REPLACE and
    add explicit ON CONFLICT (col) DO UPDATE SET ... clauses. Verify
    the file has zero remaining INSERT OR REPLACE + the new ON CONFLICT
    clauses are present. If a future PR re-introduces INSERT OR REPLACE
    in db.py, this test fires and the cutover-blocker pattern surfaces
    before deploy."""
    src = open(os.path.join(os.path.dirname(__file__), '..', 'db.py')).read()
    assert "INSERT OR REPLACE" not in src, (
        "V525 regression: INSERT OR REPLACE re-introduced in db.py. Use "
        "explicit cross-dialect ON CONFLICT (col) DO UPDATE SET col2="
        "excluded.col2 syntax instead."
    )
    # Each of the 3 patched targets has its ON CONFLICT (col) clause
    assert "ON CONFLICT (permit_number) DO UPDATE SET" in src
    assert "ON CONFLICT (key) DO UPDATE SET" in src
    assert "ON CONFLICT (source_key) DO UPDATE SET" in src
