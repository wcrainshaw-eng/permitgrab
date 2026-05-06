"""V538 regression tests for users/ module."""
from __future__ import annotations

from unittest.mock import patch


def test_login_required_redirects_anonymous_to_login():
    """No user_email in session → 302 to /login?next=<url>."""
    from users import login_required
    from flask import Flask
    app = Flask(__name__)

    @login_required
    def protected():
        return 'should not reach'

    with app.test_request_context('/permits/chicago-il'):
        resp = protected()
        assert resp.status_code in (301, 302)
        assert '/login' in resp.headers['Location']
        assert 'next=' in resp.headers['Location']


def test_login_required_passes_through_logged_in_user():
    """Session has user_email → view function runs."""
    from users import login_required
    from flask import Flask
    app = Flask(__name__)
    app.secret_key = 'test'

    @login_required
    def protected():
        return 'OK'

    with app.test_request_context('/'):
        from flask import session as _s
        _s['user_email'] = 'a@b.com'
        result = protected()
        assert result == 'OK'


def test_get_current_user_returns_none_without_session():
    """No session.user_email → None (without DB lookup)."""
    from users import get_current_user
    from flask import Flask
    app = Flask(__name__)
    app.secret_key = 'test'

    with app.test_request_context('/'):
        assert get_current_user() is None


def test_get_current_user_object_returns_none_without_session():
    from users import get_current_user_object
    from flask import Flask
    app = Flask(__name__)
    app.secret_key = 'test'

    with app.test_request_context('/'):
        assert get_current_user_object() is None


def test_get_current_user_returns_dict_when_logged_in(monkeypatch):
    """When session has user_email AND find_user_by_email returns a
    User, get_current_user returns the dict shape."""
    from users import get_current_user, lookup
    from flask import Flask

    class FakeUser:
        def to_dict(self):
            return {'email': 'a@b.com', 'plan': 'pro', 'name': 'Test'}

    monkeypatch.setattr(lookup, 'find_user_by_email', lambda e: FakeUser())

    app = Flask(__name__)
    app.secret_key = 'test'
    with app.test_request_context('/'):
        from flask import session as _s
        _s['user_email'] = 'a@b.com'
        result = get_current_user()
        assert result == {'email': 'a@b.com', 'plan': 'pro', 'name': 'Test'}


def test_save_users_is_no_op():
    """V7 contract: save_users is a deprecated no-op shim."""
    from users import save_users
    # Should not raise even with garbage input
    save_users(None)
    save_users([])
    save_users([{'email': 'x'}])


def test_users_re_exported_from_server():
    """V538 contract: server.py keeps re-exports so existing
    `from server import login_required` consumers keep working."""
    import server
    for name in ('find_user_by_email', 'get_current_user',
                 'get_current_user_object', 'load_users', 'save_users',
                 'update_user_by_email', 'login_required'):
        assert hasattr(server, name), (
            f'V538 regression: server.py no longer re-exports {name!r}'
        )
