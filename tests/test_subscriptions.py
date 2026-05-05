"""V531 regression tests for subscriptions/.

Pin the access-tier rubric — wrong return = free users accessing pro
features OR pro users denied paid features. Tests cover every branch
of get_user_plan + is_pro + is_enterprise + generate_unsubscribe_token.
"""
from __future__ import annotations

from collections import namedtuple
from datetime import datetime


# Lightweight user mock with the attributes the rubric checks
class FakeUser:
    def __init__(self, plan=None, stripe_subscription_status=None):
        self.plan = plan
        self.stripe_subscription_status = stripe_subscription_status


def test_get_user_plan_anonymous_for_none_or_empty():
    """Empty dict / None are falsy → 'anonymous' (early return).
    Pinning current behavior — V531 must not change this branch."""
    from subscriptions import get_user_plan
    assert get_user_plan(None) == 'anonymous'
    assert get_user_plan({}) == 'anonymous'  # empty dict is falsy
    # Real user with no plan set → 'free' (not anonymous)
    assert get_user_plan({'email': 'x@y.com', 'plan': ''}) == 'free'
    assert get_user_plan({'email': 'x@y.com', 'plan': 'random_unknown'}) == 'free'


def test_get_user_plan_returns_enterprise():
    from subscriptions import get_user_plan
    assert get_user_plan(FakeUser(plan='enterprise')) == 'enterprise'
    assert get_user_plan(FakeUser(plan='ENTERPRISE')) == 'enterprise'  # case-insensitive
    assert get_user_plan({'plan': 'enterprise'}) == 'enterprise'


def test_get_user_plan_returns_pro_for_pro_or_professional():
    """V252 F1: 'professional' (legacy Stripe label) maps to 'pro'."""
    from subscriptions import get_user_plan
    assert get_user_plan(FakeUser(plan='pro')) == 'pro'
    assert get_user_plan(FakeUser(plan='professional')) == 'pro'
    assert get_user_plan(FakeUser(plan='Pro')) == 'pro'  # case-insensitive


def test_get_user_plan_falls_back_to_stripe_status():
    """User with empty plan but active Stripe subscription → pro."""
    from subscriptions import get_user_plan
    u = FakeUser(plan=None, stripe_subscription_status='active')
    assert get_user_plan(u) == 'pro'
    # dict-style user also works
    assert get_user_plan({'plan': '', 'stripe_subscription_status': 'active'}) == 'pro'


def test_get_user_plan_returns_free_for_inactive_or_missing():
    from subscriptions import get_user_plan
    assert get_user_plan(FakeUser(plan='free')) == 'free'
    assert get_user_plan(FakeUser(plan='')) == 'free'
    assert get_user_plan(FakeUser(plan=None, stripe_subscription_status='canceled')) == 'free'
    assert get_user_plan(FakeUser(plan=None, stripe_subscription_status='past_due')) == 'free'


def test_is_pro_returns_true_for_pro_and_enterprise():
    """V531 contract: enterprise users have ALL pro features. The
    rubric `is_pro = plan in (pro, enterprise)` is what every gating
    callsite expects."""
    from subscriptions import is_pro
    assert is_pro(FakeUser(plan='pro')) is True
    assert is_pro(FakeUser(plan='enterprise')) is True
    assert is_pro(FakeUser(plan='professional')) is True
    assert is_pro(FakeUser(plan='free')) is False
    assert is_pro(None) is False


def test_is_enterprise_only_for_enterprise():
    """V252 F1: Enterprise-only features check this. Pro users must
    return False or they'll get features they didn't pay for."""
    from subscriptions import is_enterprise
    assert is_enterprise(FakeUser(plan='enterprise')) is True
    assert is_enterprise(FakeUser(plan='pro')) is False, (
        "V531 regression: pro users must NOT pass is_enterprise gate"
    )
    assert is_enterprise(FakeUser(plan='professional')) is False
    assert is_enterprise(FakeUser(plan='free')) is False
    assert is_enterprise(None) is False


def test_generate_unsubscribe_token_shape():
    """V531: 32-byte secret → URL-safe base64 → 43 char string.
    Anything shorter/different = downgrade attack surface."""
    from subscriptions import generate_unsubscribe_token
    t1 = generate_unsubscribe_token()
    t2 = generate_unsubscribe_token()
    # URL-safe base64 of 32 bytes is 43 chars (4 * ceil(32/3) - padding)
    assert len(t1) >= 43, f'token too short: {len(t1)} chars'
    assert t1 != t2, 'two consecutive tokens must differ (random)'
    # URL-safe = no '+', '/', '='
    for char in '+/=':
        assert char not in t1, f'token has non-URL-safe char {char!r}: {t1!r}'


def test_subscription_helpers_re_exported_from_server():
    """V531 contract: server.py re-exports the 4 helpers via
    `from subscriptions import ...` so the existing in-server-py
    callsites (line ~2399, ~4564, ~4579-4580) keep resolving without
    a wider audit. If a future refactor breaks the re-export, the
    callsites would NameError at request time — pin it here."""
    import server
    for name in ('get_user_plan', 'is_pro', 'is_enterprise',
                 'generate_unsubscribe_token', 'subscription_required'):
        assert hasattr(server, name), (
            f"V531/V533 regression: server.py no longer re-exports {name!r}; "
            f"existing callsites in server.py will NameError at request time."
        )


# ---------------------------------------------------------------------
# V533: subscription_required decorator
# ---------------------------------------------------------------------

def test_v533_decorator_redirects_to_login_when_no_session():
    """No user_email in session → redirect to /login?next=<current>."""
    from subscriptions import subscription_required
    from flask import Flask
    app = Flask(__name__)

    @subscription_required
    def protected_view():
        return 'should not reach here'

    with app.test_request_context('/permits/chicago-il'):
        resp = protected_view()
        assert resp.status_code in (301, 302), f'expected redirect, got {resp.status_code}'
        assert '/login' in resp.headers['Location']
        assert 'next=' in resp.headers['Location']


def test_v533_decorator_allows_pro_user_through(monkeypatch):
    """Logged-in pro user → view function runs."""
    from subscriptions import subscription_required
    from flask import Flask

    class FakeUser:
        plan = 'pro'
        trial_end_date = None
    monkeypatch.setattr('server.get_current_user_object', lambda: FakeUser())

    app = Flask(__name__)
    app.secret_key = 'test'

    @subscription_required
    def protected_view():
        return 'OK'

    with app.test_request_context('/'):
        from flask import session as _s
        _s['user_email'] = 'pro@example.com'
        result = protected_view()
        assert result == 'OK', (
            f'V533 regression: pro user blocked from view: {result!r}'
        )


def test_v533_decorator_blocks_free_user(monkeypatch):
    """Logged-in free user → redirect to /pricing?expired=1."""
    from subscriptions import subscription_required
    from flask import Flask

    class FakeUser:
        plan = 'free'
        trial_end_date = None
    monkeypatch.setattr('server.get_current_user_object', lambda: FakeUser())

    app = Flask(__name__)
    app.secret_key = 'test'

    @subscription_required
    def protected_view():
        return 'should not reach here'

    with app.test_request_context('/'):
        from flask import session as _s
        _s['user_email'] = 'free@example.com'
        resp = protected_view()
        assert resp.status_code in (301, 302)
        assert 'expired=1' in resp.headers['Location'], (
            f"V533 regression: free user not redirected to /pricing?expired=1: "
            f"{resp.headers.get('Location')}"
        )


def test_v533_decorator_blocks_expired_pro_user(monkeypatch):
    """Pro user with trial_end_date in the past → redirect to /pricing?expired=1."""
    from subscriptions import subscription_required
    from flask import Flask

    class FakeUser:
        plan = 'pro'
        trial_end_date = datetime(2020, 1, 1)  # WAY in the past
    monkeypatch.setattr('server.get_current_user_object', lambda: FakeUser())

    app = Flask(__name__)
    app.secret_key = 'test'

    @subscription_required
    def protected_view():
        return 'should not reach here'

    with app.test_request_context('/'):
        from flask import session as _s
        _s['user_email'] = 'expired@example.com'
        resp = protected_view()
        assert resp.status_code in (301, 302)
        assert 'expired=1' in resp.headers['Location']


def test_v533_decorator_allows_active_trial(monkeypatch):
    """Free trial user with trial_end_date in the future → view runs."""
    from subscriptions import subscription_required
    from flask import Flask

    class FakeUser:
        plan = 'free_trial'
        trial_end_date = datetime(2099, 1, 1)  # WAY in the future
    monkeypatch.setattr('server.get_current_user_object', lambda: FakeUser())

    app = Flask(__name__)
    app.secret_key = 'test'

    @subscription_required
    def protected_view():
        return 'OK'

    with app.test_request_context('/'):
        from flask import session as _s
        _s['user_email'] = 'trial@example.com'
        result = protected_view()
        assert result == 'OK'
