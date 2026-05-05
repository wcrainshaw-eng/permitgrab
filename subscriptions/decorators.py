"""V533: subscription_required decorator. Extracted from
server.py:4320-4345 per the V524 module-extraction template.

Why this lives in subscriptions/ alongside access.py: this decorator
IS the access-tier gating contract — it's the runtime expression of
the get_user_plan / is_pro / is_enterprise rubric. Bug here =
unauthenticated traffic on Pro pages or paying users blocked from
features they paid for.

The lazy `from server import get_current_user_object` inside the
wrapper avoids a circular import: server.py imports subscriptions/
at top-level (V531), and subscriptions/decorators.py needs
get_current_user_object — which lives in server.py. The lazy in-
function import resolves at request time, by which point both
modules are fully loaded.
"""
from __future__ import annotations

from datetime import datetime
from functools import wraps
from urllib.parse import quote as _q

from flask import redirect, request, session


def subscription_required(view_func):
    """Require login AND an active Pro/Enterprise plan or unexpired
    trial. Redirect to /login (with next= query) when not signed in,
    or /pricing?expired=1 when no active plan / trial.

    V533: lifted from server.py:4320 unchanged. Behavior preserved
    1-to-1 — same redirect targets, same plan-string match, same
    trial_end_date semantics. Tests in test_subscriptions.py pin
    every branch.
    """
    @wraps(view_func)
    def _wrapped(*args, **kwargs):
        if not session.get('user_email'):
            nxt = _q(request.full_path or '/', safe='')
            return redirect(f'/login?next={nxt}')
        # Lazy import to avoid circular (server imports subscriptions
        # at module-top per V531; subscriptions imports server only
        # at request time when the decorator fires).
        from server import get_current_user_object
        u = get_current_user_object()
        if not u:
            return redirect('/login')
        # Pro/Enterprise → allowed (gated by trial_end_date if set)
        plan = (getattr(u, 'plan', None) or '').lower()
        if plan in ('pro', 'professional', 'enterprise'):
            ted = getattr(u, 'trial_end_date', None)
            if ted and ted < datetime.utcnow():
                return redirect('/pricing?expired=1')
            return view_func(*args, **kwargs)
        # Free/free_trial without active plan → check trial window
        if plan in ('free_trial', 'trial'):
            ted = getattr(u, 'trial_end_date', None)
            if ted and ted > datetime.utcnow():
                return view_func(*args, **kwargs)
        return redirect('/pricing?expired=1')
    return _wrapped
