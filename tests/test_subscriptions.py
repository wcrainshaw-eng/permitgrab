"""V531 regression tests for subscriptions/.

Pin the access-tier rubric — wrong return = free users accessing pro
features OR pro users denied paid features. Tests cover every branch
of get_user_plan + is_pro + is_enterprise + generate_unsubscribe_token.
"""
from __future__ import annotations

from collections import namedtuple


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
    for name in ('get_user_plan', 'is_pro', 'is_enterprise', 'generate_unsubscribe_token'):
        assert hasattr(server, name), (
            f"V531 regression: server.py no longer re-exports {name!r}; "
            f"existing callsites in server.py will NameError at request time."
        )
