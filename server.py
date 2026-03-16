"""
PermitGrab — Production Server
Flask app that serves the dashboard and API endpoints
Deploy to any VPS (DigitalOcean, Railway, Render, etc.)
"""

from flask import Flask, jsonify, request, send_from_directory, render_template_string, session
import json
import os
import threading
import time
import secrets
from datetime import datetime
import stripe
from werkzeug.security import generate_password_hash, check_password_hash
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

app = Flask(__name__, static_folder='static')
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))

# Rate limiter setup
limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=["200 per day", "50 per hour"],
    storage_uri="memory://",
)

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
SUBSCRIBERS_FILE = os.path.join(DATA_DIR, 'subscribers.json')
USERS_FILE = os.path.join(DATA_DIR, 'users.json')

# Admin password from environment
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', '')

# ===========================
# DATA LOADING
# ===========================
def load_permits():
    """Load permits from JSON file."""
    path = os.path.join(DATA_DIR, 'permits.json')
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return []

def load_stats():
    """Load collection stats."""
    path = os.path.join(DATA_DIR, 'collection_stats.json')
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}

def load_subscribers():
    """Load subscriber list."""
    if os.path.exists(SUBSCRIBERS_FILE):
        with open(SUBSCRIBERS_FILE) as f:
            return json.load(f)
    return []

def save_subscribers(subs):
    """Save subscriber list."""
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(SUBSCRIBERS_FILE, 'w') as f:
        json.dump(subs, f, indent=2)


def load_users():
    """Load user list."""
    if os.path.exists(USERS_FILE):
        with open(USERS_FILE) as f:
            return json.load(f)
    return []


def save_users(users):
    """Save user list."""
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(USERS_FILE, 'w') as f:
        json.dump(users, f, indent=2)


def get_current_user():
    """Get the currently logged-in user from session."""
    user_email = session.get('user_email')
    if not user_email:
        return None
    users = load_users()
    return next((u for u in users if u['email'] == user_email), None)


def generate_unsubscribe_token():
    """Generate a unique unsubscribe token."""
    return secrets.token_urlsafe(32)


# ===========================
# API ROUTES
# ===========================

@app.route('/')
def index():
    """Serve the dashboard."""
    return send_from_directory('.', 'dashboard_production.html')

@app.route('/api/permits')
@limiter.limit("60 per minute")
def api_permits():
    """
    GET /api/permits
    Query params: city, trade, value, status, search, page, per_page
    Returns paginated, filtered permit data.
    """
    permits = load_permits()

    # Apply filters
    city = request.args.get('city', '')
    trade = request.args.get('trade', '')
    value = request.args.get('value', '')
    status = request.args.get('status', '')
    search = request.args.get('search', '').lower()

    if city:
        permits = [p for p in permits if p.get('city') == city]
    if trade:
        permits = [p for p in permits if p.get('trade_category') == trade]
    if value:
        permits = [p for p in permits if p.get('value_tier') == value]
    if status:
        permits = [p for p in permits if p.get('status') == status]
    if search:
        permits = [p for p in permits if search in
                   f"{p.get('address','')} {p.get('description','')} {p.get('contact_name','')} {p.get('permit_number','')} {p.get('zip','')}".lower()]

    # Pagination
    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 20))
    total = len(permits)
    start = (page - 1) * per_page
    page_permits = permits[start:start + per_page]

    return jsonify({
        'permits': page_permits,
        'total': total,
        'page': page,
        'per_page': per_page,
        'total_pages': (total + per_page - 1) // per_page,
    })

@app.route('/api/stats')
def api_stats():
    """GET /api/stats - Dashboard statistics."""
    permits = load_permits()
    stats = load_stats()

    return jsonify({
        'total_permits': len(permits),
        'total_value': sum(p.get('estimated_cost', 0) for p in permits),
        'high_value_count': len([p for p in permits if p.get('value_tier') == 'high']),
        'cities': len(set(p.get('city') for p in permits)),
        'trade_breakdown': stats.get('trade_breakdown', {}),
        'value_breakdown': stats.get('value_breakdown', {}),
        'last_updated': stats.get('collected_at', ''),
    })

@app.route('/api/filters')
def api_filters():
    """GET /api/filters - Available filter options."""
    permits = load_permits()

    cities = sorted(set(p.get('city', '') for p in permits if p.get('city')))
    trades = sorted(set(p.get('trade_category', '') for p in permits if p.get('trade_category')))
    statuses = sorted(set(p.get('status', '') for p in permits if p.get('status')))

    return jsonify({
        'cities': cities,
        'trades': trades,
        'statuses': statuses,
    })

@app.route('/api/subscribe', methods=['POST'])
@limiter.limit("5 per minute")
def api_subscribe():
    """POST /api/subscribe - Add email alert subscriber."""
    data = request.get_json()
    if not data or not data.get('email'):
        return jsonify({'error': 'Email required'}), 400

    subs = load_subscribers()

    # Check for duplicate
    existing_emails = [s['email'] for s in subs]
    if data['email'] in existing_emails:
        return jsonify({'error': 'Email already subscribed'}), 409

    sub = {
        'email': data['email'],
        'name': data.get('name', ''),
        'company': data.get('company', ''),
        'city': data.get('city', ''),
        'trade': data.get('trade', ''),
        'plan': data.get('plan', 'free'),
        'subscribed_at': datetime.now().isoformat(),
        'active': True,
        'unsubscribe_token': generate_unsubscribe_token(),
    }

    subs.append(sub)
    save_subscribers(subs)

    return jsonify({
        'message': f'Successfully subscribed {sub["email"]}',
        'subscriber': sub,
    }), 201

@app.route('/api/subscribers')
def api_subscribers():
    """GET /api/subscribers - List all subscribers (admin endpoint)."""
    # Check admin authentication
    if not session.get('admin_authenticated'):
        return jsonify({'error': 'Admin authentication required'}), 401

    subs = load_subscribers()
    return jsonify({
        'total': len(subs),
        'subscribers': subs,
    })

@app.route('/api/export')
def api_export():
    """GET /api/export - Export filtered permits as CSV."""
    permits = load_permits()

    # Apply same filters as /api/permits
    city = request.args.get('city', '')
    trade = request.args.get('trade', '')
    if city:
        permits = [p for p in permits if p.get('city') == city]
    if trade:
        permits = [p for p in permits if p.get('trade_category') == trade]

    # Build CSV
    if not permits:
        return "No permits match your filters", 404

    headers = ['permit_number', 'city', 'state', 'address', 'zip', 'trade_category',
               'description', 'estimated_cost', 'filing_date', 'status', 'contact_name', 'contact_phone']

    lines = [','.join(headers)]
    for p in permits:
        row = [str(p.get(h, '')).replace(',', ';').replace('"', "'") for h in headers]
        lines.append(','.join(f'"{v}"' for v in row))

    csv_content = '\n'.join(lines)

    from flask import Response
    return Response(
        csv_content,
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename=permitgrab_export_{datetime.now().strftime("%Y%m%d")}.csv'}
    )


# ===========================
# STRIPE PAYMENT ENDPOINTS
# ===========================

# Stripe configuration from environment variables
STRIPE_SECRET_KEY = os.environ.get('STRIPE_SECRET_KEY', '')
STRIPE_PRICE_ID = os.environ.get('STRIPE_PRICE_ID', '')
STRIPE_WEBHOOK_SECRET = os.environ.get('STRIPE_WEBHOOK_SECRET', '')
SITE_URL = os.environ.get('SITE_URL', 'http://localhost:5000')

@app.route('/api/create-checkout-session', methods=['POST'])
def create_checkout_session():
    """Create a Stripe Checkout Session for Professional plan ($149/mo)."""
    if not STRIPE_SECRET_KEY or not STRIPE_PRICE_ID:
        return jsonify({'error': 'Stripe not configured'}), 500

    stripe.api_key = STRIPE_SECRET_KEY

    data = request.get_json() or {}
    customer_email = data.get('email')

    try:
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price': STRIPE_PRICE_ID,
                'quantity': 1,
            }],
            mode='subscription',
            success_url=f'{SITE_URL}/?payment=success',
            cancel_url=f'{SITE_URL}/?payment=cancelled',
            customer_email=customer_email,
            metadata={
                'plan': 'professional',
            },
        )
        return jsonify({'url': checkout_session.url})
    except stripe.error.StripeError as e:
        return jsonify({'error': str(e)}), 400


@app.route('/api/stripe-webhook', methods=['POST'])
def stripe_webhook():
    """Handle Stripe webhook events."""
    payload = request.get_data(as_text=True)
    sig_header = request.headers.get('Stripe-Signature')

    if not STRIPE_SECRET_KEY:
        return jsonify({'error': 'Stripe not configured'}), 500

    stripe.api_key = STRIPE_SECRET_KEY

    try:
        if STRIPE_WEBHOOK_SECRET:
            event = stripe.Webhook.construct_event(
                payload, sig_header, STRIPE_WEBHOOK_SECRET
            )
        else:
            # For testing without webhook signature verification
            event = json.loads(payload)
    except ValueError:
        return jsonify({'error': 'Invalid payload'}), 400
    except stripe.error.SignatureVerificationError:
        return jsonify({'error': 'Invalid signature'}), 400

    # Handle checkout.session.completed event
    if event['type'] == 'checkout.session.completed':
        session = event['data']['object']
        customer_email = session.get('customer_email') or session.get('customer_details', {}).get('email')
        plan = session.get('metadata', {}).get('plan', 'professional')

        if customer_email:
            # Create or update subscriber with professional plan
            subs = load_subscribers()
            existing = next((s for s in subs if s['email'] == customer_email), None)

            if existing:
                existing['plan'] = plan
                existing['stripe_customer_id'] = session.get('customer')
                existing['subscription_id'] = session.get('subscription')
                existing['upgraded_at'] = datetime.now().isoformat()
            else:
                subs.append({
                    'email': customer_email,
                    'name': session.get('customer_details', {}).get('name', ''),
                    'company': '',
                    'city': '',
                    'trade': '',
                    'plan': plan,
                    'subscribed_at': datetime.now().isoformat(),
                    'active': True,
                    'stripe_customer_id': session.get('customer'),
                    'subscription_id': session.get('subscription'),
                })

            save_subscribers(subs)
            print(f"[Stripe] Subscriber {customer_email} upgraded to {plan}")

    return jsonify({'status': 'success'})


# ===========================
# USER AUTHENTICATION
# ===========================

@app.route('/api/register', methods=['POST'])
@limiter.limit("10 per hour")
def api_register():
    """POST /api/register - Register a new user."""
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Invalid request'}), 400

    email = data.get('email', '').strip().lower()
    password = data.get('password', '')
    name = data.get('name', '')

    if not email or not password:
        return jsonify({'error': 'Email and password required'}), 400

    if len(password) < 8:
        return jsonify({'error': 'Password must be at least 8 characters'}), 400

    users = load_users()

    # Check for duplicate
    if any(u['email'] == email for u in users):
        return jsonify({'error': 'Email already registered'}), 409

    user = {
        'email': email,
        'name': name,
        'password_hash': generate_password_hash(password),
        'plan': 'free',
        'created_at': datetime.now().isoformat(),
    }

    users.append(user)
    save_users(users)

    # Log in the user
    session['user_email'] = email

    # Return user without password hash
    return jsonify({
        'message': 'Registration successful',
        'user': {
            'email': user['email'],
            'name': user['name'],
            'plan': user['plan'],
        }
    }), 201


@app.route('/api/login', methods=['POST'])
@limiter.limit("20 per minute")
def api_login():
    """POST /api/login - Log in a user."""
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Invalid request'}), 400

    email = data.get('email', '').strip().lower()
    password = data.get('password', '')

    if not email or not password:
        return jsonify({'error': 'Email and password required'}), 400

    users = load_users()
    user = next((u for u in users if u['email'] == email), None)

    if not user or not check_password_hash(user['password_hash'], password):
        return jsonify({'error': 'Invalid email or password'}), 401

    # Log in the user
    session['user_email'] = email

    return jsonify({
        'message': 'Login successful',
        'user': {
            'email': user['email'],
            'name': user['name'],
            'plan': user['plan'],
        }
    })


@app.route('/api/logout', methods=['POST'])
def api_logout():
    """POST /api/logout - Log out the current user."""
    session.pop('user_email', None)
    return jsonify({'message': 'Logged out'})


@app.route('/api/me')
def api_me():
    """GET /api/me - Get current logged-in user."""
    user = get_current_user()
    if not user:
        return jsonify({'user': None})

    return jsonify({
        'user': {
            'email': user['email'],
            'name': user['name'],
            'plan': user['plan'],
        }
    })


# ===========================
# UNSUBSCRIBE
# ===========================

@app.route('/api/unsubscribe')
def api_unsubscribe():
    """GET /api/unsubscribe?token=xxx - Unsubscribe from email alerts."""
    token = request.args.get('token', '')

    if not token:
        return render_template_string('''
            <!DOCTYPE html>
            <html><head><title>Error</title></head>
            <body style="font-family: sans-serif; padding: 40px; text-align: center;">
                <h1>Invalid Link</h1>
                <p>This unsubscribe link is invalid or has expired.</p>
            </body></html>
        '''), 400

    subs = load_subscribers()
    subscriber = next((s for s in subs if s.get('unsubscribe_token') == token), None)

    if not subscriber:
        return render_template_string('''
            <!DOCTYPE html>
            <html><head><title>Error</title></head>
            <body style="font-family: sans-serif; padding: 40px; text-align: center;">
                <h1>Invalid Link</h1>
                <p>This unsubscribe link is invalid or has already been used.</p>
            </body></html>
        '''), 404

    # Mark as inactive
    subscriber['active'] = False
    subscriber['unsubscribed_at'] = datetime.now().isoformat()
    save_subscribers(subs)

    return render_template_string('''
        <!DOCTYPE html>
        <html><head><title>Unsubscribed</title></head>
        <body style="font-family: sans-serif; padding: 40px; text-align: center;">
            <h1>You've been unsubscribed</h1>
            <p>{{ email }} will no longer receive permit alerts.</p>
            <p style="margin-top: 20px; color: #666;">
                Changed your mind? <a href="/">Re-subscribe anytime</a>
            </p>
        </body></html>
    ''', email=subscriber['email'])


# ===========================
# ADMIN PAGE
# ===========================

@app.route('/admin')
def admin_page():
    """GET /admin - Admin dashboard (password protected)."""
    # Check for admin password in query param or session
    password = request.args.get('password', '')

    if password and ADMIN_PASSWORD and password == ADMIN_PASSWORD:
        session['admin_authenticated'] = True

    if not session.get('admin_authenticated'):
        if not ADMIN_PASSWORD:
            return render_template_string('''
                <!DOCTYPE html>
                <html><head><title>Admin</title></head>
                <body style="font-family: sans-serif; padding: 40px; text-align: center;">
                    <h1>Admin Not Configured</h1>
                    <p>Set the ADMIN_PASSWORD environment variable to enable admin access.</p>
                </body></html>
            '''), 500

        return render_template_string('''
            <!DOCTYPE html>
            <html><head><title>Admin Login</title></head>
            <body style="font-family: sans-serif; padding: 40px; max-width: 400px; margin: 0 auto;">
                <h1>Admin Login</h1>
                <form method="GET" action="/admin">
                    <input type="password" name="password" placeholder="Admin Password"
                           style="width: 100%; padding: 12px; margin-bottom: 12px; border: 1px solid #ccc; border-radius: 4px;">
                    <button type="submit" style="width: 100%; padding: 12px; background: #2563eb; color: white; border: none; border-radius: 4px; cursor: pointer;">
                        Login
                    </button>
                </form>
            </body></html>
        ''')

    # Load data for admin dashboard
    permits = load_permits()
    subscribers = load_subscribers()
    stats = load_stats()
    users = load_users()

    active_subs = [s for s in subscribers if s.get('active', True)]
    paid_subs = [s for s in subscribers if s.get('plan') in ('professional', 'enterprise')]

    return render_template_string('''
        <!DOCTYPE html>
        <html>
        <head>
            <title>PermitGrab Admin</title>
            <style>
                * { margin: 0; padding: 0; box-sizing: border-box; }
                body { font-family: -apple-system, BlinkMacSystemFont, sans-serif; background: #f3f4f6; }
                .header { background: #111827; color: white; padding: 20px 32px; }
                .header h1 { font-size: 24px; }
                .container { max-width: 1200px; margin: 0 auto; padding: 24px; }
                .stats-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-bottom: 24px; }
                .stat-card { background: white; padding: 20px; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,.1); }
                .stat-card .value { font-size: 32px; font-weight: 700; color: #111827; }
                .stat-card .label { font-size: 14px; color: #6b7280; margin-top: 4px; }
                .section { background: white; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,.1); margin-bottom: 24px; }
                .section-header { padding: 16px 20px; border-bottom: 1px solid #e5e7eb; font-weight: 600; }
                .section-body { padding: 20px; }
                table { width: 100%; border-collapse: collapse; }
                th, td { padding: 12px; text-align: left; border-bottom: 1px solid #e5e7eb; }
                th { background: #f9fafb; font-weight: 600; font-size: 13px; color: #6b7280; }
                .badge { padding: 2px 8px; border-radius: 12px; font-size: 12px; font-weight: 500; }
                .badge-active { background: #dcfce7; color: #166534; }
                .badge-inactive { background: #fee2e2; color: #991b1b; }
                .badge-pro { background: #dbeafe; color: #1e40af; }
                .logout-link { color: rgba(255,255,255,.7); text-decoration: none; font-size: 14px; }
            </style>
        </head>
        <body>
            <div class="header">
                <div style="display: flex; justify-content: space-between; align-items: center; max-width: 1200px; margin: 0 auto;">
                    <h1>PermitGrab Admin</h1>
                    <a href="/admin?logout=1" class="logout-link">Logout</a>
                </div>
            </div>
            <div class="container">
                <div class="stats-grid">
                    <div class="stat-card">
                        <div class="value">{{ total_permits }}</div>
                        <div class="label">Total Permits</div>
                    </div>
                    <div class="stat-card">
                        <div class="value">{{ total_subscribers }}</div>
                        <div class="label">Total Subscribers</div>
                    </div>
                    <div class="stat-card">
                        <div class="value">{{ active_subscribers }}</div>
                        <div class="label">Active Subscribers</div>
                    </div>
                    <div class="stat-card">
                        <div class="value">{{ paid_subscribers }}</div>
                        <div class="label">Paid Subscribers</div>
                    </div>
                </div>

                <div class="section">
                    <div class="section-header">Collection Status</div>
                    <div class="section-body">
                        <p><strong>Last Updated:</strong> {{ last_updated or 'Never' }}</p>
                        <p><strong>Total Users:</strong> {{ total_users }}</p>
                    </div>
                </div>

                <div class="section">
                    <div class="section-header">Subscribers ({{ total_subscribers }})</div>
                    <div class="section-body" style="overflow-x: auto;">
                        <table>
                            <thead>
                                <tr>
                                    <th>Email</th>
                                    <th>Name</th>
                                    <th>City</th>
                                    <th>Trade</th>
                                    <th>Plan</th>
                                    <th>Status</th>
                                    <th>Subscribed</th>
                                </tr>
                            </thead>
                            <tbody>
                                {% for sub in subscribers %}
                                <tr>
                                    <td>{{ sub.email }}</td>
                                    <td>{{ sub.name or '-' }}</td>
                                    <td>{{ sub.city or '-' }}</td>
                                    <td>{{ sub.trade or '-' }}</td>
                                    <td>
                                        {% if sub.plan in ['professional', 'enterprise'] %}
                                        <span class="badge badge-pro">{{ sub.plan }}</span>
                                        {% else %}
                                        {{ sub.plan or 'free' }}
                                        {% endif %}
                                    </td>
                                    <td>
                                        {% if sub.active != false %}
                                        <span class="badge badge-active">Active</span>
                                        {% else %}
                                        <span class="badge badge-inactive">Inactive</span>
                                        {% endif %}
                                    </td>
                                    <td>{{ sub.subscribed_at[:10] if sub.subscribed_at else '-' }}</td>
                                </tr>
                                {% endfor %}
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
        </body>
        </html>
    ''',
        total_permits=len(permits),
        total_subscribers=len(subscribers),
        active_subscribers=len(active_subs),
        paid_subscribers=len(paid_subs),
        total_users=len(users),
        last_updated=stats.get('collected_at', ''),
        subscribers=subscribers,
    )


# Handle admin logout
@app.before_request
def check_admin_logout():
    if request.path == '/admin' and request.args.get('logout'):
        session.pop('admin_authenticated', None)


# ===========================
# SCHEDULED DATA COLLECTION
# ===========================
def scheduled_collection():
    """Run data collection every 24 hours."""
    while True:
        try:
            print(f"[{datetime.now()}] Running scheduled data collection...")
            from collector import collect_all
            collect_all(days_back=60)
            print(f"[{datetime.now()}] Collection complete.")
        except Exception as e:
            print(f"[{datetime.now()}] Collection error: {e}")

        # Sleep 24 hours
        time.sleep(86400)


# ===========================
# MAIN
# ===========================
if __name__ == '__main__':
    os.makedirs(DATA_DIR, exist_ok=True)

    # Start background data collection
    collector_thread = threading.Thread(target=scheduled_collection, daemon=True)
    collector_thread.start()

    print("=" * 50)
    print("PermitGrab Server Starting")
    print(f"Dashboard: http://localhost:5000")
    print(f"API: http://localhost:5000/api/permits")
    print("=" * 50)

    app.run(host='0.0.0.0', port=5000, debug=True)
