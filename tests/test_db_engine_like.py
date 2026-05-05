"""V528 regression tests for the LIKE → ILIKE translation.

The V525 SQL audit found 80 unwrapped LIKE callsites that would
silently under-match on the Postgres path because Postgres LIKE is
case-sensitive while SQLite LIKE is ASCII-case-insensitive by default.

V528 fix: db_engine._translate_sql swaps LIKE → ILIKE on the
Postgres path. SQLite path is untouched (LIKE there is already
case-insensitive).
"""
from __future__ import annotations

import os

os.environ.setdefault('DATABASE_URL', 'postgresql://localhost:5432/permitgrab_loadtest_unused')

import db_engine  # noqa: E402


def _x(sql):
    return db_engine._translate_sql(sql)


def test_bare_like_becomes_ilike():
    """The most common shape: WHERE col LIKE pattern."""
    out = _x("SELECT * FROM permits WHERE city LIKE 'chicago%'")
    assert ' LIKE ' not in out, (
        f'V528 regression: bare LIKE not translated to ILIKE: {out!r}'
    )
    assert ' ILIKE ' in out


def test_not_like_becomes_not_ilike():
    """assessor_collector.py:920, web_enrichment.py:324-329."""
    out = _x("WHERE owner_name NOT LIKE '%Pending%'")
    assert 'NOT LIKE' not in out
    assert 'NOT ILIKE' in out


def test_lower_wrap_still_works_after_translation():
    """The 8 LOWER()-wrapped LIKEs already in the codebase. After
    LIKE → ILIKE swap, LOWER(x) ILIKE 'foo' is functionally equal to
    LOWER(x) LIKE 'foo' (since LOWER returns lowercase, ILIKE's
    insensitivity is a no-op). No semantic change."""
    out = _x("WHERE LOWER(city) LIKE 'chicago%'")
    assert 'LOWER(city) ILIKE' in out


def test_existing_ilike_callsites_not_double_translated():
    """routes/admin.py:5363-5367 already use ILIKE for bot UA matching.
    The \\bLIKE\\b regex must not chip away at the LIKE inside ILIKE
    (no word boundary between I and L)."""
    src = "WHEN user_agent ILIKE '%googlebot%' THEN 'Googlebot'"
    out = _x(src)
    # ILIKE should remain ILIKE — exactly one occurrence, not corrupted
    assert out.count('ILIKE') == 1
    assert 'IILIKE' not in out  # the catastrophic regression shape
    assert 'ILILIKE' not in out


def test_lowercase_like_translates():
    """Some Python code builds SQL with lowercase 'like' — must
    translate too, otherwise the Postgres parser still parses it
    correctly (case-insensitive keyword) but as case-sensitive LIKE."""
    out = _x("WHERE col like 'foo%'")
    assert ' like ' not in out.lower() or ' ilike ' in out.lower()
    assert 'ilike' in out.lower() or 'ILIKE' in out


def test_like_in_complex_query():
    """A real production query shape from server.py: multi-condition
    WHERE with LIKE on multiple fields."""
    sql = (
        "SELECT * FROM permits "
        "WHERE (LOWER(description) LIKE ? OR permit_type LIKE ?) "
        "AND city LIKE 'chicago%' "
        "AND contractor_name NOT LIKE 'OWNER%' "
        "ORDER BY filing_date DESC"
    )
    out = _x(sql)
    assert ' LIKE ' not in out
    assert 'NOT LIKE' not in out
    # 3 positive LIKE + 1 NOT LIKE → 4 total ILIKE word matches
    assert out.count('ILIKE') == 4
    assert 'NOT ILIKE' in out
    # Parameterized placeholders also got translated to %s
    assert '?' not in out
    assert '%s' in out


def test_like_inside_string_literal_still_translated():
    """Edge case: literal string 'LIKE' inside a value should also
    become 'ILIKE' — that's a slight semantic shift but matches the
    SQLite behavior (both LIKE and a column-stored 'LIKE' literal
    are independent of the keyword translation in SQL parsing).
    The translator can't distinguish keyword from literal without
    full SQL parsing; the practical exposure is near-zero (no
    callsite stores literal 'LIKE' in user-visible data)."""
    out = _x("WHERE col = 'cake LIKE bread'")
    # We accept that the literal becomes 'cake ILIKE bread'. Document
    # this as known behavior — not a regression.
    assert 'cake ILIKE bread' in out


def test_not_a_word_like_substring_not_translated():
    """Identifiers like UNLIKE_NUMBER, child_likely_id — must not be
    mangled. Word-boundary protection."""
    out = _x("SELECT child_likelihood, UNLIKELY_FLAG FROM tbl")
    assert 'child_likelihood' in out
    assert 'UNLIKELY_FLAG' in out
    # The literal 'LIKE' substring inside UNLIKELY shouldn't be touched
    assert 'UNILIKELY' not in out
    assert 'child_iliKEelihood' not in out.lower()


def test_sqlite_path_untouched_when_use_postgres_false(monkeypatch):
    """When DATABASE_URL is not set (USE_POSTGRES=False),
    _translate_sql returns input unchanged. The LIKE swap must NOT
    fire on the SQLite path because SQLite is already case-insensitive
    and ILIKE doesn't exist in SQLite."""
    monkeypatch.setattr(db_engine, 'USE_POSTGRES', False)
    sql = "WHERE city LIKE 'chicago%'"
    out = db_engine._translate_sql(sql)
    assert out == sql, f'SQLite path must not translate LIKE; got {out!r}'
