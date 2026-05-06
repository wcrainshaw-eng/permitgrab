"""V538: user lookup + auth decorator. Extracted from server.py
per the V524 module-extraction template.

Public API:
- find_user_by_email(email) -> User | None
- get_current_user() -> dict | None  (V7 dict-shape compat)
- get_current_user_object() -> User | None
- load_users() -> list[dict]
- save_users(users) -> None  (V7 deprecated no-op)
- update_user_by_email(email, updates) -> bool
- login_required(view) -> view  (Flask decorator)

Per the durable rules:
- No new feature code in server.py — new code goes into modules.
- Bug-fix CODE_V### PRs ship with regression tests.
- Always run pytest tests/test_smoke.py + test_imports.py +
  test_routes.py locally before pushing.
"""
from .decorators import login_required
from .lookup import (
    find_user_by_email,
    get_current_user,
    get_current_user_object,
    load_users,
    save_users,
    update_user_by_email,
)

__all__ = [
    'find_user_by_email',
    'get_current_user',
    'get_current_user_object',
    'load_users',
    'save_users',
    'update_user_by_email',
    'login_required',
]
