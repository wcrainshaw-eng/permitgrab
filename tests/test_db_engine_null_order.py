"""V529 regression tests for NULL-ordering parity.

SQLite default: NULLs sort as LESS than non-NULL.
  ORDER BY col ASC    → NULLs FIRST
  ORDER BY col DESC   → NULLs LAST

Postgres default: NULLs sort as GREATER than non-NULL.
  ORDER BY col ASC    → NULLs LAST       (drift from SQLite)
  ORDER BY col DESC   → NULLs FIRST      (drift from SQLite)

V529 fix: db_engine._translate_sql appends `NULLS FIRST` after ASC
and `NULLS LAST` after DESC on the Postgres path. Idempotent — already-
explicit `NULLS FIRST/LAST` callsites are skipped via negative
lookahead.
"""
from __future__ import annotations

import os

os.environ.setdefault('DATABASE_URL', 'postgresql://localhost:5432/permitgrab_loadtest_unused')

import db_engine  # noqa: E402

# V527c: see test_db_engine_translate.py for why this module-level
# attr-set is needed (USE_POSTGRES freezes at first import).
db_engine.USE_POSTGRES = True


def _x(sql):
    return db_engine._translate_sql(sql)


def test_asc_appends_nulls_first():
    out = _x("SELECT * FROM permits ORDER BY filing_date ASC")
    assert 'ASC NULLS FIRST' in out, (
        f'V529 regression: ASC did not get NULLS FIRST: {out!r}'
    )


def test_desc_appends_nulls_last():
    out = _x("SELECT * FROM permits ORDER BY filing_date DESC LIMIT 10")
    assert 'DESC NULLS LAST' in out, (
        f'V529 regression: DESC did not get NULLS LAST: {out!r}'
    )


def test_lowercase_asc_translates():
    out = _x("ORDER BY col asc")
    assert 'NULLS FIRST' in out.upper()


def test_lowercase_desc_translates():
    out = _x("ORDER BY col desc")
    assert 'NULLS LAST' in out.upper()


def test_multi_column_order_by_each_gets_its_own_nulls_clause():
    """The V526 stale-first sort + Phase 2 city_rank are real
    multi-column ORDER BYs in production."""
    out = _x("ORDER BY (last_run IS NULL) DESC, last_run ASC, source_id DESC")
    # Each direction keyword gets its own NULLS clause
    assert out.count('DESC NULLS LAST') == 2
    assert out.count('ASC NULLS FIRST') == 1


def test_already_explicit_nulls_first_not_double_appended():
    """server.py:8128 already has `ORDER BY p.filing_date DESC NULLS LAST`.
    The negative lookahead must skip these so we don't get
    `DESC NULLS LAST NULLS LAST`."""
    src = "ORDER BY p.filing_date DESC NULLS LAST"
    out = _x(src)
    assert out.count('NULLS LAST') == 1
    assert out.count('NULLS FIRST') == 0


def test_already_explicit_nulls_last_after_asc_not_double_appended():
    src = "ORDER BY total_permits ASC NULLS LAST"
    out = _x(src)
    # The translator preserves the caller's explicit NULLS LAST
    # (even though it differs from V529's "ASC → NULLS FIRST" default).
    # Caller knows best.
    assert out.count('NULLS LAST') == 1
    assert 'NULLS FIRST' not in out


def test_v526_stale_sort_translates_correctly():
    """V526's exact sort query — pin the post-V529 translated form."""
    sql = """
        SELECT pc.city_slug, pc.source_id,
               (SELECT MAX(run_started_at) FROM scraper_runs
                WHERE city_slug = pc.city_slug) AS last_run
        FROM prod_cities pc
        WHERE pc.status = 'active' AND pc.source_id IS NOT NULL
          AND pc.source_type IS NOT NULL
        ORDER BY (last_run IS NULL) DESC, last_run ASC
    """
    out = _x(sql)
    # The (IS NULL) DESC sentinel + ASC tail must both pick up
    # explicit NULLS clauses for parity.
    assert 'DESC NULLS LAST' in out
    assert 'ASC NULLS FIRST' in out


def test_no_false_match_on_descriptor_or_ascend_identifiers():
    """Identifier names with ASC/DESC substrings must NOT trigger
    the translation. Word-boundary protection."""
    out = _x("SELECT description, ascending_flag FROM tbl ORDER BY id ASC")
    # The ASC at the end gets translated; description/ascending stay intact
    assert 'description' in out
    assert 'ascending_flag' in out
    assert 'descrIPTION' not in out  # case-sensitive identifier preservation
    assert 'ASC NULLS FIRST' in out
    # No regressions on the inline 'descending' / 'ascending'
    assert 'NULLS FIRSTending_flag' not in out


def test_sqlite_path_untouched(monkeypatch):
    monkeypatch.setattr(db_engine, 'USE_POSTGRES', False)
    sql = "ORDER BY filing_date DESC"
    out = db_engine._translate_sql(sql)
    assert out == sql, f'SQLite path must not translate; got {out!r}'
