"""V531: subscription tier rubric. Extracted from server.py:4382-4417
per the V524 module-extraction template.

Pure functions — no Flask context, no DB, no I/O. The gating rubric is
the bug class that hurts most: a wrong return value here = free users
accessing pro features OR pro users blocked from features they paid
for. Tests pin every branch.
"""
from __future__ import annotations


def get_user_plan(user):
    """Returns one of: 'enterprise', 'pro', 'free', 'anonymous'.

    V252 F1: split 'pro' into 'pro' vs 'enterprise' so Enterprise-only
    features (property owners, webhooks, market reports) can gate
    separately. Existing 'professional' Stripe label maps to 'pro'.

    V531: lifted verbatim from server.py:4382. Behavior unchanged.
    """
    if not user:
        return 'anonymous'

    plan = (user.get('plan') if hasattr(user, 'get') else getattr(user, 'plan', '')) or ''
    plan = plan.lower()

    if plan == 'enterprise':
        return 'enterprise'
    if plan in ('pro', 'professional'):
        return 'pro'

    # Stripe subscription status — safe accessor
    sub_status = (
        user.get('stripe_subscription_status')
        if hasattr(user, 'get')
        else getattr(user, 'stripe_subscription_status', None)
    )
    if sub_status == 'active':
        return 'pro'

    return 'free'


def is_pro(user):
    """Returns True if user has Pro-or-above access. Enterprise counts as Pro."""
    return get_user_plan(user) in ('pro', 'enterprise')


def is_enterprise(user):
    """V252 F1: Enterprise-tier gate for webhook / owner-append / PDF reports."""
    return get_user_plan(user) == 'enterprise'
