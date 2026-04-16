# V179 Postmortem — DB Unification Attempt Frozen at P2.5

## What happened

V179 aimed to unify app reads/writes on Postgres, eliminating the dual-database
architecture (SQLite + Postgres) that was causing data locality issues.

- **P0–P2.5 succeeded**: Postgres schemas migrated, SQLite data copied, Houston
  `source_city_key` backfilled, prod_cities deduped. Postgres is a full
  point-in-time snapshot as of 2026-04-15.
- **Phase 3 attempted three architecturally distinct designs and failed each time:**
  - **P3-A** (commits 2fa1928 / 36dbdce / ff2110e): Auto-enable psycopg2 pool
    at import time. Failure: gunicorn worker OOM, SIGKILL, port never bound.
    Reverted in 3 commits.
  - **P3-B v1** (commit 3bf7887): Reuse Flask-SQLAlchemy's pool via
    `set_sqlalchemy_engine(db.engine)` called at module import. Failure:
    `db.engine` resolves via `current_app` which doesn't exist at import time
    → `RuntimeError: Working outside of application context`. Render deploy failed.
    Rolled back by Wes.
  - **P3-C** (branch never committed): Phase 0 inventory revealed 6 additional
    latent bugs (INSERT OR REPLACE at 8+ callsites, PRAGMA table_info in
    migration helpers, executescript() outside init_db, date('now') in live
    queries, two conflicting wrapper classes, tests hardcoded to sqlite3.Row).
    Stopped before any code change per the STOP-AND-ASK rule.

## Why we stopped

Total across three P3 attempts: **9 latent bugs spread across 200+ callsites**.
Every fix we shipped unmasked a new bug down the chain. The pattern proved
the "runtime-flip DB backend with a translator layer" design was wrong:

- A single `_translate_sql` layer cannot economically cover SQLite-to-Postgres
  dialect gaps that exist at every callsite.
- `db.py`'s state machine (USE_POSTGRES, \_HAS_ENGINE, is_pg_pool_enabled,
  \_sqla_engine) produced 16+ combinations; each P3 attempt discovered a new
  broken one at deploy time.
- Callers leaked SQLite-specific syntax (`INSERT OR REPLACE`, `PRAGMA`,
  `date('now', '-N days')`, `executescript`) into production code paths.

## Current state

- **Postgres**: full snapshot of SQLite data as of 2026-04-15. No live writes.
  Intended as DR baseline and future flip target for V180 Phase 9.
- **SQLite**: live production DB. All app reads and writes.
- `permitdb.get_connection()`: effectively returns a SQLite connection only
  (the Postgres paths are guarded behind flags that are off in production).
- All V179 flags (`USE_POSTGRES`, `_HAS_ENGINE`, `is_pg_pool_enabled`, etc.)
  remain in `db.py`/`db_engine.py` but are dormant (pool never enabled on
  Render).

## Successor: V180

V180 replaces the runtime-flip approach with a **compile-time strategy**:
make every query backend-agnostic (Postgres-compatible SQL that also works
on SQLite) over 10–15 small PRs. Once every query is compatible, flipping
the backend is a trivial 5-line change with near-zero regression surface.

Phase order:
- **Phase 0**: Freeze V179 at P2.5, postmortem (this doc).
- **Phases 1–3**: Ship user-facing V178 T2/T5/T6 work against SQLite.
- **Phases 4–8 (DC1–DC5)**: Dialect cleanup — eliminate INSERT OR REPLACE,
  PRAGMA, date('now'), executescript, consolidate wrappers, fix tests.
- **Phase 9**: Flip app reads to Postgres (the unification). Safe now
  because every preceding query is backend-agnostic.
- **Phase 10**: Dual-write critical tables to Postgres.
- **Phase 11**: Delete SQLite code path and file.

See `CODE_V180_MASTER_PLAN.txt` for the full plan.

## Do NOT

- Do not reintroduce `enable_pg_pool`, auto-enable-on-import, or any
  runtime DB-backend flip logic without a new design review.
- Do not write new SQLite-specific SQL (`INSERT OR REPLACE`, `PRAGMA`,
  `date('now')`, `strftime(fmt, col)` in SQL, `executescript`). See the
  V180 Golden Rule in `CODE_V180_MASTER_PLAN.txt`.
- Do not revive the `v179-p3b-v2-appcontext` or `v179-p3c-singlepath`
  branches (deleted in this commit).
- Do not modify the Postgres schema to diverge from SQLite during V180
  Phases 1–8. Schemas must stay in sync so Phase 9's flip works.
- Do not delete any SQLite code before V180 Phase 11. Rollback depends
  on SQLite staying alive.
