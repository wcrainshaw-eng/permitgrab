"""
PermitGrab — Production Server
Flask app that serves the dashboard and API endpoints
Deploy to any VPS (DigitalOcean, Railway, Render, etc.)
"""

from flask import Flask, jsonify, request, send_from_directory, render_template_string
import json
import os
import threading
import time
from datetime import datetime
import stripe

app = Flask(__name__, static_folder='static')

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
SUBSCRIBERS_FILE = os.path.join(DATA_DIR, 'subscribers.json')

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


# ===========================
# API ROUTES
# ===========================

@app.route('/')
def index():
    """Serve the dashboard."""
    return send_from_directory('.', 'dashboard_production.html')

@app.route('/api/permits')
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
def api_subscribe():
    """POST /api/subscribe - Add email alert subscriber."""
    data = request.get_json()
    if not data or not data.get('email'):
        return jsonify({'error': 'Email required'}), 400

    subs = load_subscribers()
    sub = {
        'email': data['email'],
        'name': data.get('name', ''),
        'company': data.get('company', ''),
        'city': data.get('city', ''),
        'trade': data.get('trade', ''),
        'plan': data.get('plan', 'free'),
        'subscribed_at': datetime.now().isoformat(),
        'active': True,
    }

    # Check for duplicate
    existing_emails = [s['email'] for s in subs]
    if sub['email'] in existing_emails:
        return jsonify({'error': 'Email already subscribed'}), 409

    subs.append(sub)
    save_subscribers(subs)

    return jsonify({
        'message': f'Successfully subscribed {sub["email"]}',
        'subscriber': sub,
    }), 201

@app.route('/api/subscribers')
def api_subscribers():
    """GET /api/subscribers - List all subscribers (admin endpoint)."""
    # In production, add authentication here
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
