"""V471 PR2 (CODE_V471 Part 1B): auth blueprint extracted from server.py.

Routes: 8 URLs across 8 handlers.

Helpers/globals from server.py are accessed via `from server import *`,
which imports everything server.py defined before this blueprint was loaded
(blueprints are registered at the bottom of server.py, after all globals
are set). Underscored helpers are listed below explicitly because `import *`
skips names starting with `_`.
"""
from flask import Blueprint, request, jsonify, render_template, session, redirect, abort, Response, g, url_for, send_from_directory
from datetime import datetime, timedelta
import os, json, time, re, threading, random, string, hashlib, hmac
from werkzeug.security import generate_password_hash, check_password_hash

# Pull in server.py's helpers, models, and globals (server is fully loaded
# by the time this blueprint module is imported because server.py registers
# blueprints at the very end of its module body).
from server import *
# Underscored helpers / module-level state that `import *` skips:
from server import _flask_logout_user
import server as _s

auth_bp = Blueprint('auth', __name__)


@auth_bp.route('/signup')
def signup_page():
    """Render the Sign Up page.

    V375 (CODE_V363 P0): when a logged-in user lands on /signup with
    ?plan=<slug>, route them straight into Stripe checkout — they
    already have an account, the only thing left is to charge them.
    Before this fix, "Start Free Trial" on /pricing → /signup?plan=pro
    just redirected logged-in users to "/" (homepage), losing every
    conversion. Google Ads was burning $3/click for this dead end.
    """
    user = get_current_user()
    if user:
        plan = (request.args.get('plan') or '').strip().lower()
        if plan:
            # Forward to the unified checkout entrypoint, which handles
            # already-paid users + creates a Stripe Checkout session.
            return redirect(f'/start-checkout?plan={plan}')
        return redirect('/')
    footer_cities = get_cities_with_data()
    return render_template('signup.html', footer_cities=footer_cities)


@auth_bp.route('/login')
def login_page():
    """Render the Login page."""
    # Redirect if already logged in
    if get_current_user():
        return redirect('/')
    footer_cities = get_cities_with_data()
    # V13.7: Handle redirect messages (e.g., from /dashboard redirect)
    message = request.args.get('message', '')
    login_message = None
    if message == 'login_required':
        login_message = 'Please log in to access your dashboard.'
    return render_template('login.html', footer_cities=footer_cities, login_message=login_message)


@auth_bp.route('/forgot-password')
def forgot_password_page():
    """Render the Forgot Password page."""
    footer_cities = get_cities_with_data()
    return render_template('forgot_password.html', footer_cities=footer_cities)


@auth_bp.route('/reset-password/<token>')
def reset_password_page(token):
    """Render the Reset Password page."""
    # Validate token
    cleanup_expired_tokens()
    tokens = load_reset_tokens()

    if token not in tokens:
        return render_template('reset_password.html', error='Invalid or expired reset link. Please request a new one.', token=None)

    token_data = tokens[token]
    if token_data.get('used'):
        return render_template('reset_password.html', error='This reset link has already been used.', token=None)

    now = datetime.now().isoformat()
    if token_data.get('expires', '') < now:
        return render_template('reset_password.html', error='This reset link has expired. Please request a new one.', token=None)

    footer_cities = get_cities_with_data()
    return render_template('reset_password.html', token=token, error=None, footer_cities=footer_cities)


@auth_bp.route('/onboarding')
def onboarding_page():
    """Render the post-signup onboarding flow."""
    # Require login
    user = get_current_user()
    if not user:
        return redirect('/signup')
    # V9 Fix 10: Only show cities with actual permit data (not all 300+ cities)
    cities = get_cities_with_data()
    trades = get_all_trades()
    return render_template('onboarding.html', cities=cities, trades=trades)


@auth_bp.route('/register')
def register_redirect():
    """Redirect /register to /signup."""
    return redirect('/signup', code=301)


@auth_bp.route('/logout')
def logout_page():
    """Log out and redirect to homepage."""
    session.clear()
    # V459 (CODE_V456): also clear flask-login state.
    try:
        _flask_logout_user()
    except Exception:
        pass
    return redirect('/')


@auth_bp.route('/account')
def account_page():
    """Account settings page."""
    if 'user_email' not in session:
        return redirect('/login')
    user = find_user_by_email(session['user_email'])
    if not user:
        session.clear()
        return redirect('/login')
    footer_cities = get_cities_with_data()
    is_pro = user.plan in ('pro', 'professional', 'enterprise')
    return render_template('account.html', user=user, is_pro=is_pro, footer_cities=footer_cities)


