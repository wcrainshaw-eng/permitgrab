"""V531: subscription tier + token helpers. Extracted from server.py
per the V524/V527/V530 module-extraction template.

Public API:
- get_user_plan(user) -> 'enterprise' | 'pro' | 'free' | 'anonymous'
- is_pro(user) -> bool
- is_enterprise(user) -> bool
- generate_unsubscribe_token() -> str (32-byte URL-safe token)

These four functions cover the access-tier rubric. The
`subscription_required` decorator (server.py:4320) is NOT in scope
for V531 because it depends on Flask request context + several
server.py helpers (`get_current_user_object`, `_v458_wraps`,
`session`, `redirect`); a future PR can extract it once those
dependencies are themselves modular.

Per the durable rules:
- No new feature code in server.py — new code goes into modules.
- Bug-fix CODE_V### PRs ship with regression tests.
- Run pytest tests/test_smoke.py tests/test_imports.py
  tests/test_routes.py -q locally before pushing.
"""
from .access import get_user_plan, is_pro, is_enterprise
from .tokens import generate_unsubscribe_token

__all__ = [
    'get_user_plan',
    'is_pro',
    'is_enterprise',
    'generate_unsubscribe_token',
]
