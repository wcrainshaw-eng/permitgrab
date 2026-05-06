"""V540: city-health contract. Per Wes's directive — converts the
V527 collectors module's per-platform diagnosis into the customer-
visible Pass/Degraded/Fail rubric that the signup flow + digest
pipeline can consult.

Public API:
- CityHealth: dataclass mirroring the city_health table row.
- compute_city_health(slug) -> CityHealth: the rubric.
- compute_all_city_health() -> dict: nightly runner summary.
- upsert_city_health(health) -> bool: persist a row.
- ensure_table() -> bool: idempotent CREATE TABLE.
- start_thread() -> Thread: spawn the health_scheduler daemon.
- PASS / DEGRADED / FAIL: status constants.
- REASON: dict of stable reason_code → human-readable text.

Per the durable rules:
- No new feature code in server.py — new code goes into modules.
- Bug-fix CODE_V### PRs ship with regression tests.
- Always run pytest tests/test_smoke.py + test_imports.py +
  test_routes.py locally before pushing.

V540 PR1 ships: schema + compute + scheduler. PR2 wires the
endpoint. PR3 wires signup flow consultation. PR4 wires the
digest pipeline consultation. Each PR is small, tested, single-
concern.
"""
from .compute import (
    CityHealth,
    PASS,
    DEGRADED,
    FAIL,
    REASON,
    compute_city_health,
    compute_all_city_health,
    upsert_city_health,
)
from .curation import (
    has_city_health_data,
    is_sellable_city,
    get_sellable_cities,
    filter_to_sellable,
)
from .digest_safety import filter_subscriber_cities_for_digest
from .schema import ensure_table
from .scheduler import health_daemon, start_thread

__all__ = [
    'CityHealth',
    'PASS',
    'DEGRADED',
    'FAIL',
    'REASON',
    'compute_city_health',
    'compute_all_city_health',
    'upsert_city_health',
    'has_city_health_data',
    'is_sellable_city',
    'get_sellable_cities',
    'filter_to_sellable',
    'filter_subscriber_cities_for_digest',
    'ensure_table',
    'health_daemon',
    'start_thread',
]
