"""V537: permit-data transforms (re-classify, generate description,
format address, validate dates). Extracted from server.py:3479-3630
per the V524 module-extraction template.

Per the durable rules:
- No new feature code in server.py — new code goes into modules.
- Bug-fix CODE_V### PRs ship with regression tests.
- Always run pytest tests/test_smoke.py + test_imports.py +
  test_routes.py locally before pushing.
"""
from .address import normalize_address_for_lookup
from .transforms import (
    reclassify_permit,
    generate_permit_description,
    format_permit_address,
    validate_permit_dates,
)

__all__ = [
    'reclassify_permit',
    'generate_permit_description',
    'format_permit_address',
    'validate_permit_dates',
    'normalize_address_for_lookup',
]
