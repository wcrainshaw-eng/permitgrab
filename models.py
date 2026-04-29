"""V471 PR2 (CODE_V471 Part 1C): SQLAlchemy models extracted from server.py.

Holds the no-arg `db = SQLAlchemy()` so both server.py and any future
blueprint module can `from models import db, User, SavedSearch` without
a circular import. server.py calls `db.init_app(app)` after constructing
the Flask app.
"""
import json
from datetime import datetime

from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class User(UserMixin, db.Model):
    """User model for PostgreSQL storage (V7 — replaces JSON file).

    V459 (CODE_V456): added UserMixin so flask-login's current_user and
    @login_required decorator work directly against this model. UserMixin
    provides default is_authenticated/is_active/is_anonymous/get_id —
    no overrides needed since SQLAlchemy already gives us .id.
    """
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    name = db.Column(db.String(255), nullable=False, default='')
    password_hash = db.Column(db.String(255), nullable=False)
    plan = db.Column(db.String(50), default='free')
    city = db.Column(db.String(255))
    trade = db.Column(db.String(255))
    daily_alerts = db.Column(db.Boolean, default=False)
    onboarding_completed = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    stripe_customer_id = db.Column(db.String(255))
    stripe_subscription_id = db.Column(db.String(255))
    stripe_subscription_status = db.Column(db.String(50))
    # V12.26: Competitor Watch - JSON list of competitor names to track
    watched_competitors = db.Column(db.Text, default='[]')
    # V12.26: Weekly digest city subscriptions - JSON list of city names
    digest_cities = db.Column(db.Text, default='[]')
    # V251 F4: per-user filter defaults for daily digest.
    digest_zip_filter = db.Column(db.String(16), nullable=True)
    digest_trade_filter = db.Column(db.String(64), nullable=True)
    # V254 Phase 1: 10 free phone-reveal credits per signup. Decrements on
    # each unique /api/reveal-phone call. Pro/Enterprise bypass entirely.
    reveal_credits = db.Column(db.Integer, default=10)
    # V254 Phase 1: JSON list of already-revealed profile_ids — so a
    # repeat reveal of the same contractor doesn't burn a fresh credit.
    revealed_profile_ids = db.Column(db.Text, default='[]')

    # V12.53: Email system fields
    email_verified = db.Column(db.Boolean, default=False)
    email_verified_at = db.Column(db.DateTime)
    email_verification_token = db.Column(db.String(64))
    unsubscribe_token = db.Column(db.String(64))
    digest_active = db.Column(db.Boolean, default=True)  # Can receive digest emails
    last_login_at = db.Column(db.DateTime)
    last_digest_sent_at = db.Column(db.DateTime)
    last_reengagement_sent_at = db.Column(db.DateTime)
    # Trial tracking
    trial_started_at = db.Column(db.DateTime)
    trial_end_date = db.Column(db.DateTime)
    trial_midpoint_sent = db.Column(db.Boolean, default=False)
    trial_ending_sent = db.Column(db.Boolean, default=False)
    trial_expired_sent = db.Column(db.Boolean, default=False)
    # Welcome email tracking
    welcome_email_sent = db.Column(db.Boolean, default=False)

    def to_dict(self):
        """Convert to dictionary for JSON responses."""
        return {
            'id': self.id,
            'email': self.email,
            'name': self.name,
            'plan': self.plan,
            'city': self.city,
            'trade': self.trade,
            'daily_alerts': self.daily_alerts,
            'onboarding_completed': self.onboarding_completed,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'stripe_customer_id': self.stripe_customer_id,
            'stripe_subscription_id': self.stripe_subscription_id,
            'stripe_subscription_status': self.stripe_subscription_status,
            # V12.26: Competitor Watch and Digest Cities
            'watched_competitors': json.loads(self.watched_competitors or '[]'),
            'digest_cities': json.loads(self.digest_cities or '[]'),
            # V12.53: Email system fields
            'email_verified': self.email_verified,
            'digest_active': self.digest_active,
            'trial_end_date': self.trial_end_date.isoformat() if self.trial_end_date else None,
        }

    def is_pro(self):
        """Check if user has Pro access (paid or trial)."""
        if self.plan in ('professional', 'pro', 'enterprise'):
            # Check if trial has expired
            if self.trial_end_date and datetime.utcnow() > self.trial_end_date:
                return False
            return True
        return False

    def days_until_trial_ends(self):
        """Get days remaining in trial, or None if not on trial."""
        if not self.trial_end_date:
            return None
        delta = self.trial_end_date - datetime.utcnow()
        return max(0, delta.days)


class SavedSearch(db.Model):
    """V170 B4: User saved search for daily alerts."""
    __tablename__ = 'saved_searches'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    name = db.Column(db.String(255), nullable=False)
    city_slug = db.Column(db.String(255))
    trade = db.Column(db.String(100))
    tier = db.Column(db.String(50))
    min_value = db.Column(db.Integer)
    frequency = db.Column(db.String(20), nullable=False, default='daily')
    last_sent_at = db.Column(db.DateTime)
    active = db.Column(db.Integer, nullable=False, default=1)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    user = db.relationship('User', backref=db.backref('saved_searches', lazy=True))

    def to_dict(self):
        return {
            'id': self.id, 'name': self.name, 'city_slug': self.city_slug,
            'trade': self.trade, 'tier': self.tier, 'min_value': self.min_value,
            'frequency': self.frequency, 'active': self.active,
            'last_sent_at': self.last_sent_at.isoformat() if self.last_sent_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }
