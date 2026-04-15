# V178 T2 + T5 Diagnostic Findings (2026-04-15)

## DIAG 1 — T2: Empty fields per city

Only 3 cities have linked permits in Postgres (via source_city_key = source_id):

| City | Total | Empty Desc | Empty Type | Empty Trade | Empty Contractor | Empty Value |
|------|-------|-----------|-----------|------------|-----------------|------------|
| Philadelphia | 1,413 | 0% | 0% | 0% | 100% | 100% |
| New York City | 19,065 | 0% | 0% | 0% | 6% | 10% |
| Los Angeles | 12,961 | 0% | 0% | 0% | 100% | 5% |

Houston, Austin, Dallas, Chicago returned **0 permits** — their source_city_key
doesn't match any prod_cities.source_id in Postgres.

**Root cause:** Most cities' permits aren't linked to prod_cities via the
source_city_key → source_id join. The join only works for cities whose
collector was configured with a matching source_id.

## DIAG 2 — T5: Normalized address + violations

- `normalized_address` column did NOT exist on Postgres permits or violations
- **Added** via SSH: ALTER TABLE permits ADD COLUMN normalized_address TEXT
- **Violations table did NOT exist on Postgres** — only on SQLite
- **Created** via SSH with V162 schema (id, prod_city_id, city, state, etc.)
- Indexes created on both tables
- Backfill of normalized_address on permits triggered

## Critical Architecture Issue

The app has a **dual-database problem**:
- `db.py get_connection()` returns **SQLite** (thread-local file connection)
- Flask-SQLAlchemy `db.session` uses **Postgres** (via DATABASE_URL)
- Violation collector writes to **SQLite** (via permitdb.get_connection())
- Web routes read from **Postgres** (via Flask-SQLAlchemy for users/searches)
  BUT city pages use **SQLite** (via permitdb.get_connection())

This means:
1. Violations collected by the daemon go to SQLite, not Postgres
2. City pages read permits from SQLite
3. User/search data is in Postgres
4. The violations table was never created on Postgres until this session

## What Needs to Happen

1. **violation_collector.py** should write to Postgres when DATABASE_URL is set
2. OR city pages should read violations from SQLite (where they actually exist)
3. The normalized_address backfill needs to run against whichever DB the city
   pages actually read from (SQLite)
4. The source_city_key → source_id join needs investigation for Houston/Austin/
   Dallas/Chicago — these cities' permits exist but aren't linked
