"""V538: user-lookup helpers. Extracted from server.py:3988-4089
per the V524 module-extraction template.

Six helpers around the User SQLAlchemy model. Bug class: wrong
user lookup = session-stitching bug = users see other users' data
or get logged out.
"""
from __future__ import annotations

from flask import session

from models import User, db


def find_user_by_email(email):
    """Find a user by email (case-insensitive). Returns User object
    or None.

    V538: lifted from server.py:3988 unchanged.
    """
    if not email:
        return None
    email_lower = email.lower().strip()
    return User.query.filter(db.func.lower(User.email) == email_lower).first()


def get_current_user():
    """Get the currently logged-in user from session. Returns dict
    for backward compatibility (matches V7 dict-shape consumers)."""
    user_email = session.get('user_email')
    if not user_email:
        return None
    user = find_user_by_email(user_email)
    if user:
        return user.to_dict()
    return None


def get_current_user_object():
    """Get the currently logged-in user as a User SQLAlchemy object
    (for callers that need ORM operations beyond the dict shape)."""
    user_email = session.get('user_email')
    if not user_email:
        return None
    return find_user_by_email(user_email)


def load_users():
    """V7: Load all users from database as a list of dicts (backward
    compatibility for code that still expects the old JSON-file shape)."""
    users = User.query.all()
    return [u.to_dict() for u in users]


def save_users(users):
    """V7: DEPRECATED no-op shim. Kept for backward compatibility
    with code that still calls save_users(users_list). Individual
    user updates should use db.session.commit() directly via
    update_user_by_email or the ORM."""
    # No-op: database operations should be done directly
    # Individual updates use db.session.commit()
    pass


def update_user_by_email(email, updates):
    """V7: Update a user's fields by email. Returns True on success,
    False if user not found."""
    user = find_user_by_email(email)
    if user:
        for key, value in updates.items():
            if hasattr(user, key):
                setattr(user, key, value)
        db.session.commit()
        return True
    return False
