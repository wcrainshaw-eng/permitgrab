"""V538: login_required decorator. Extracted from server.py:4028
per the V524 module-extraction template.

V458: standardized auth gate. Pairs with subscriptions/decorators.py:
subscription_required (which implicitly includes login_required's
behavior + plan/trial checks).
"""
from __future__ import annotations

from functools import wraps
from urllib.parse import quote as _q

from flask import redirect, request, session


def login_required(view_func):
    """Redirect anonymous visitors to /login?next=<original-url>.

    V538: lifted from server.py:4028 unchanged. The next= URL is
    quoted so query strings + paths with special chars survive
    the redirect roundtrip.
    """
    @wraps(view_func)
    def _wrapped(*args, **kwargs):
        if not session.get('user_email'):
            nxt = _q(request.full_path or '/', safe='')
            return redirect(f'/login?next={nxt}')
        return view_func(*args, **kwargs)
    return _wrapped
