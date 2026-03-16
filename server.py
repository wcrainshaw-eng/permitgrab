"""
PermitGrab — Production Server
Flask app that serves the dashboard and API endpoints
Deploy to any VPS (DigitalOcean, Railway, Render, etc.)
"""

from flask import Flask, jsonify, request, send_from_directory, render_template_string, session, render_template, Response
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

app = Flask(__name__, static_folder='static', template_folder='templates')
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
# SEO CITY LANDING PAGES
# ===========================

# City configurations with SEO content
CITY_SEO_CONFIG = {
    "new-york": {
        "name": "New York City",
        "state": "NY",
        "meta_title": "New York City Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse 2000+ active building permits in New York City. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>New York City's construction market is one of the largest and most dynamic in the world. With constant development across all five boroughs, NYC building permits represent billions of dollars in annual construction activity. From luxury high-rise developments in Manhattan to residential renovations in Brooklyn and Queens, the opportunities for contractors are endless.</p>
            <p>The NYC construction industry spans every trade imaginable—HVAC installations in commercial towers, electrical upgrades in historic brownstones, plumbing renovations in pre-war buildings, and roofing projects across thousands of residential properties. New York City construction permits are filed daily with the Department of Buildings, creating a steady stream of new contractor leads.</p>
            <p>For contractors seeking NYC building permits and construction leads, timing is everything. PermitGrab delivers fresh New York City permit data daily, giving you the edge to connect with property owners before your competition even knows the project exists.</p>
        """
    },
    "los-angeles": {
        "name": "Los Angeles",
        "state": "CA",
        "meta_title": "Los Angeles Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse 2000+ active building permits in Los Angeles. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Los Angeles is experiencing unprecedented construction growth, making it one of the hottest markets for contractor leads in the nation. From Santa Monica to Downtown LA to the Valley, LA building permits cover everything from ADU (Accessory Dwelling Unit) construction to major commercial developments and earthquake retrofit projects.</p>
            <p>The LA construction market is unique in its diversity—solar panel installations are booming, pool construction remains strong year-round, and seismic retrofitting creates steady demand for structural contractors. Los Angeles construction permits also reflect the city's focus on sustainability, with green building projects and EV charger installations on the rise.</p>
            <p>Contractors looking for Los Angeles building permits need fast access to new filings. PermitGrab pulls LA permit data directly from official city sources, delivering actionable contractor leads for every trade from roofing to HVAC to general construction.</p>
        """
    },
    "chicago": {
        "name": "Chicago",
        "state": "IL",
        "meta_title": "Chicago Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse 2000+ active building permits in Chicago. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Chicago's construction industry is thriving, with billions invested in residential, commercial, and infrastructure projects each year. Chicago building permits cover everything from downtown high-rise construction to single-family renovations in neighborhoods across the city. The Windy City's harsh winters create strong seasonal demand for HVAC, roofing, and weatherization projects.</p>
            <p>The Chicago contractor market benefits from the city's aging housing stock—thousands of greystone and brick buildings require ongoing maintenance, window replacements, tuckpointing, and interior renovations. Chicago construction permits also reflect the city's industrial heritage, with many warehouse-to-residential conversions creating opportunities for general contractors and specialty trades alike.</p>
            <p>For contractors seeking Chicago building permits and construction leads, staying ahead of the competition means accessing permit data as soon as it's filed. PermitGrab delivers fresh Chicago permit leads daily, helping you find and win jobs across Cook County.</p>
        """
    },
    "san-francisco": {
        "name": "San Francisco",
        "state": "CA",
        "meta_title": "San Francisco Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse 2000+ active building permits in San Francisco. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>San Francisco's construction market commands some of the highest project values in the nation. SF building permits range from tech company office buildouts to Victorian home renovations in Pacific Heights to seismic retrofitting in older buildings. The city's strict building codes and permitting requirements mean property owners actively seek qualified, reliable contractors.</p>
            <p>The San Francisco construction industry reflects the city's unique character—historic preservation projects, ADU construction under California's housing laws, and high-end residential renovations drive steady permit activity. San Francisco construction permits also include significant solar and green building projects as the city pushes toward sustainability goals.</p>
            <p>Contractors targeting San Francisco building permits face stiff competition in this premium market. PermitGrab gives you the advantage of seeing new SF permit filings first, so you can reach property owners while they're still evaluating contractors.</p>
        """
    },
    "austin": {
        "name": "Austin",
        "state": "TX",
        "meta_title": "Austin Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse 2000+ active building permits in Austin. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Austin is one of America's fastest-growing cities, and the construction boom shows no signs of slowing. Austin building permits reflect the city's explosive growth—new residential developments, commercial construction, and infrastructure projects create constant demand for contractors of every trade. From Round Rock to South Austin, the permit pipeline is full.</p>
            <p>The Austin construction market offers unique opportunities including new home construction in master-planned communities, office buildouts for tech companies relocating to Texas, and renovation projects in established neighborhoods like Hyde Park and Travis Heights. Austin construction permits span HVAC installations critical for Texas summers, pool construction, and outdoor living projects.</p>
            <p>For contractors seeking Austin building permits, speed matters in this competitive market. PermitGrab delivers fresh Austin permit data daily, connecting you with property owners and builders who need quality contractors now.</p>
        """
    },
    "seattle": {
        "name": "Seattle",
        "state": "WA",
        "meta_title": "Seattle Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse 900+ active building permits in Seattle. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Seattle's construction industry continues to boom, driven by tech industry growth and population influx. Seattle building permits cover high-rise development downtown, residential construction in neighborhoods like Capitol Hill and Ballard, and renovation projects across King County. The Pacific Northwest climate creates strong demand for roofing, weatherization, and moisture-control projects.</p>
            <p>The Seattle construction market includes significant green building activity—the city leads in LEED-certified construction, solar installations, and energy-efficient upgrades. Seattle construction permits also reflect the region's seismic concerns, with retrofit and structural reinforcement projects common in older buildings.</p>
            <p>Contractors pursuing Seattle building permits benefit from accessing new filings before they become public knowledge. PermitGrab pulls permit data from official Seattle sources daily, delivering contractor leads for every specialty from plumbing to electrical to general construction.</p>
        """
    },
    "new-orleans": {
        "name": "New Orleans",
        "state": "LA",
        "meta_title": "New Orleans Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse 2000+ active building permits in New Orleans. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>New Orleans has a vibrant construction market shaped by the city's unique architecture, climate, and ongoing revitalization efforts. New Orleans building permits cover historic preservation in the French Quarter, residential renovations in the Garden District, and new construction in rapidly developing neighborhoods like the Bywater and Mid-City.</p>
            <p>The New Orleans construction industry requires specialized knowledge—hurricane-resistant construction, moisture control, foundation work in challenging soil conditions, and historic preservation standards create demand for skilled contractors. NOLA construction permits reflect seasonal patterns, with roofing and exterior work concentrated outside hurricane season.</p>
            <p>For contractors seeking New Orleans building permits and construction leads, local market knowledge combined with fast permit access creates winning opportunities. PermitGrab delivers fresh NOLA permit data to help you find and win jobs throughout the Crescent City.</p>
        """
    },
    "baton-rouge": {
        "name": "Baton Rouge",
        "state": "LA",
        "meta_title": "Baton Rouge Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse 1200+ active building permits in Baton Rouge. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Baton Rouge's construction market benefits from Louisiana's capital city status and growing economy. Baton Rouge building permits cover residential construction in areas like Prairieville and Denham Springs, commercial development along I-10 and I-12 corridors, and renovation projects throughout East Baton Rouge Parish.</p>
            <p>The Baton Rouge construction industry reflects regional priorities—flood mitigation, hurricane-resistant construction, and energy-efficient HVAC systems are common project types. BR construction permits also include significant industrial and petrochemical-related construction given the area's economic base.</p>
            <p>Contractors pursuing Baton Rouge building permits find steady work in this growing market. PermitGrab delivers fresh EBR permit data daily, connecting contractors with property owners who need quality work done right.</p>
        """
    },
    "nashville": {
        "name": "Nashville",
        "state": "TN",
        "meta_title": "Nashville Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse 900+ active building permits in Nashville. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Nashville is one of America's hottest construction markets, with unprecedented growth driving demand for every type of contractor. Nashville building permits reflect the city's transformation—luxury condo towers downtown, new residential developments in surrounding counties, and renovations in trendy neighborhoods like East Nashville and The Nations.</p>
            <p>The Nashville construction industry benefits from the city's booming entertainment, healthcare, and corporate relocation activity. Music City construction permits include high-end residential work, commercial tenant improvements, and hospitality projects serving the tourism industry. HVAC installation is critical given Tennessee's hot summers.</p>
            <p>For contractors seeking Nashville building permits, getting to leads first is essential in this competitive market. PermitGrab delivers fresh Nashville permit data daily, giving you the inside track on new construction projects throughout Davidson County.</p>
        """
    },
    "atlanta": {
        "name": "Atlanta",
        "state": "GA",
        "meta_title": "Atlanta Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse 500+ active building permits in Atlanta. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Atlanta's construction market is booming, fueled by corporate relocations, population growth, and major infrastructure investments. Atlanta building permits span high-rise development in Midtown and Buckhead, residential construction in metro Atlanta suburbs, and renovation projects in historic neighborhoods like Virginia-Highland and Inman Park.</p>
            <p>The Atlanta construction industry reflects the region's diversity—from luxury home construction in North Fulton to commercial buildouts in the Perimeter area to adaptive reuse projects in emerging neighborhoods. ATL construction permits include significant HVAC and electrical work given the hot Georgia summers and aging housing stock.</p>
            <p>Contractors pursuing Atlanta building permits compete in a fast-moving market where early access to permits means more wins. PermitGrab delivers fresh Atlanta permit data daily, connecting you with property owners and developers who need quality contractors now.</p>
        """
    },
    "cincinnati": {
        "name": "Cincinnati",
        "state": "OH",
        "meta_title": "Cincinnati Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse 300+ active building permits in Cincinnati. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Cincinnati's construction market is experiencing a renaissance, with major investments in downtown development and neighborhood revitalization. Cincinnati building permits cover riverfront development projects, residential renovations in historic neighborhoods like Over-the-Rhine and Mount Adams, and commercial construction throughout Hamilton County.</p>
            <p>The Cincinnati construction industry benefits from the city's aging housing stock—Victorian-era homes require ongoing maintenance, window replacements, roofing projects, and interior renovations. Cincy construction permits also reflect the region's industrial legacy with many warehouse-to-residential conversions and adaptive reuse projects.</p>
            <p>For contractors seeking Cincinnati building permits, accessing new filings quickly means beating the competition to quality leads. PermitGrab delivers fresh Cincinnati permit data daily, helping you find and win jobs throughout the Queen City.</p>
        """
    },
}

# List of all cities for navigation
ALL_CITIES = [
    {"slug": "new-york", "name": "New York City"},
    {"slug": "los-angeles", "name": "Los Angeles"},
    {"slug": "chicago", "name": "Chicago"},
    {"slug": "san-francisco", "name": "San Francisco"},
    {"slug": "austin", "name": "Austin"},
    {"slug": "seattle", "name": "Seattle"},
    {"slug": "new-orleans", "name": "New Orleans"},
    {"slug": "baton-rouge", "name": "Baton Rouge"},
    {"slug": "nashville", "name": "Nashville"},
    {"slug": "atlanta", "name": "Atlanta"},
    {"slug": "cincinnati", "name": "Cincinnati"},
]


@app.route('/permits/<city_slug>')
def city_landing(city_slug):
    """Render SEO-optimized city landing page."""
    if city_slug not in CITY_SEO_CONFIG:
        return "City not found", 404

    config = CITY_SEO_CONFIG[city_slug]
    permits = load_permits()

    # Filter permits for this city
    city_permits = [p for p in permits if p.get('city') == config['name']]

    # Calculate stats
    permit_count = len(city_permits)
    total_value = sum(p.get('estimated_cost', 0) for p in city_permits)
    high_value_count = len([p for p in city_permits if p.get('value_tier') == 'high'])

    # Trade breakdown
    trade_breakdown = {}
    for p in city_permits:
        trade = p.get('trade_category', 'Other')
        trade_breakdown[trade] = trade_breakdown.get(trade, 0) + 1

    # Sort permits by value for preview
    sorted_permits = sorted(city_permits, key=lambda x: x.get('estimated_cost', 0), reverse=True)

    # Other cities for footer links
    other_cities = [c for c in ALL_CITIES if c['slug'] != city_slug]

    return render_template(
        'city_landing.html',
        city_name=config['name'],
        city_slug=city_slug,
        meta_title=config['meta_title'],
        meta_description=config['meta_description'],
        seo_content=config['seo_content'],
        canonical_url=f"{SITE_URL}/permits/{city_slug}",
        permit_count=permit_count,
        total_value=total_value,
        high_value_count=high_value_count,
        trade_breakdown=trade_breakdown,
        permits=sorted_permits,
        other_cities=other_cities,
        current_year=datetime.now().year,
    )


# ===========================
# SITEMAP & ROBOTS.TXT
# ===========================

@app.route('/sitemap.xml')
def sitemap():
    """Generate XML sitemap for SEO."""
    today = datetime.now().strftime('%Y-%m-%d')

    urls = [
        {'loc': SITE_URL, 'changefreq': 'daily', 'priority': '1.0'},
    ]

    # Add city landing pages
    for city in ALL_CITIES:
        urls.append({
            'loc': f"{SITE_URL}/permits/{city['slug']}",
            'changefreq': 'weekly',
            'priority': '0.8',
        })

    xml = '<?xml version="1.0" encoding="UTF-8"?>\n'
    xml += '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'

    for url in urls:
        xml += '  <url>\n'
        xml += f"    <loc>{url['loc']}</loc>\n"
        xml += f"    <lastmod>{today}</lastmod>\n"
        xml += f"    <changefreq>{url['changefreq']}</changefreq>\n"
        xml += f"    <priority>{url['priority']}</priority>\n"
        xml += '  </url>\n'

    xml += '</urlset>'

    return Response(xml, mimetype='application/xml')


@app.route('/robots.txt')
def robots():
    """Serve robots.txt for search engines."""
    content = f"""User-agent: *
Allow: /

Sitemap: {SITE_URL}/sitemap.xml
"""
    return Response(content, mimetype='text/plain')


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
