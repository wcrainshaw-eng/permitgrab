# PermitGrab

Building permit data aggregation and alerts for contractors.

## Architecture

- `server.py` — Flask web app, serves UI + API
- `collector.py` — Permit collection from Socrata/Accela/ArcGIS/CKAN/Carto
- `accela_portal_collector.py` — Accela portal scraper (requests+BS4, no Playwright)
- `violation_collector.py` — Code violations from Socrata SODA
- `email_alerts.py` — Daily digests, welcome emails, trial lifecycle
- `db.py` — SQLite connection + schema (WAL mode)
- Tables: prod_cities, permits, violations, subscribers, city_sources, scraper_runs

## Local development

```bash
make install    # Install all dependencies
make smoke      # Fast tests — run before every push
make test       # Full test suite
make lint       # Ruff linter
make check      # Lint + smoke
```

## Deployment

1. Run `make check`. If red, fix and re-run.
2. Commit. Pre-push hook runs smoke tests again.
3. Push to main. GitHub Actions runs CI.
4. Render auto-deploys on green CI.
5. After deploy, verify:
   - curl /api/health returns expected version
   - curl /api/diagnostics returns healthy memory/counts
   - Render events page shows service "live"

## Key endpoints

- `GET /api/health` — Version + status
- `GET /healthz` — Lightweight probe (no DB)
- `GET /api/diagnostics` — Memory, counts, activity (admin key required)
- `POST /api/admin/query` — SELECT queries (admin key required)
- `POST /api/admin/execute` — INSERT/UPDATE/DELETE (admin key required)
