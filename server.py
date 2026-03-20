"""
PermitGrab — Production Server
Flask app that serves the dashboard and API endpoints
Deploy to any VPS (DigitalOcean, Railway, Render, etc.)
"""

from flask import Flask, jsonify, request, send_from_directory, render_template_string, session, render_template, Response, redirect, abort, g
from difflib import SequenceMatcher
import json
import os
import threading
import time
import secrets
import uuid
from datetime import datetime, timedelta
import stripe
from werkzeug.security import generate_password_hash, check_password_hash
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from city_configs import get_all_cities_info, get_city_count, get_city_by_slug, CITY_REGISTRY, TRADE_CATEGORIES, format_city_name
from lifecycle import get_lifecycle_label
from trade_configs import TRADE_REGISTRY, get_trade, get_all_trades, get_trade_slugs
import analytics

# V12.17: static_url_path='' serves static files from root (needed for GSC verification)
app = Flask(__name__, static_folder='static', static_url_path='', template_folder='templates')
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))


# V12.17: Google Search Console verification - MUST be registered first before any catch-alls
@app.route('/google3ef154d70f8049a0.html')
def google_verification():
    return Response('google-site-verification: google3ef154d70f8049a0.html', mimetype='text/html')


# ===========================
# V12.19: ADMIN ENDPOINTS FOR DATA RECOVERY
# ===========================

@app.route('/api/admin/reset-permits', methods=['POST'])
def admin_reset_permits():
    """Delete corrupted permits.json so next collection writes clean data."""
    secret = request.headers.get('X-Admin-Key')
    expected = os.environ.get('ADMIN_KEY', 'permitgrab-reset-2026')
    if secret != expected:
        return jsonify({'error': 'Unauthorized'}), 401

    # DATA_DIR is defined later, use the same logic
    data_dir = '/var/data' if os.path.isdir('/var/data') else os.path.join(os.path.dirname(__file__), 'data')
    filepath = os.path.join(data_dir, 'permits.json')
    deleted = False
    if os.path.exists(filepath):
        os.remove(filepath)
        deleted = True
        print(f"[Admin] Deleted corrupted permits.json at {filepath}")

    return jsonify({
        'deleted': deleted,
        'path': filepath,
        'message': 'File deleted. Next collection cycle will write clean data.'
    })


@app.route('/api/admin/force-collection', methods=['POST'])
def admin_force_collection():
    """Trigger data collection immediately instead of waiting for scheduler."""
    secret = request.headers.get('X-Admin-Key')
    expected = os.environ.get('ADMIN_KEY', 'permitgrab-reset-2026')
    if secret != expected:
        return jsonify({'error': 'Unauthorized'}), 401

    import threading
    def run_collection():
        try:
            from collector import collect_all
            print("[Admin] Starting forced collection...")
            collect_all(days_back=60)
            print("[Admin] Forced collection complete.")
            # V12.19: Explicitly reload data after collection completes
            # (collector.py also calls this but belt-and-suspenders approach)
            preload_data_from_disk()
            print("[Admin] Data reloaded into server memory.")
        except Exception as e:
            print(f"[Admin] Force collection error: {e}")

    thread = threading.Thread(target=run_collection, daemon=True)
    thread.start()

    return jsonify({
        'message': 'Collection started in background',
        'note': 'Data will appear on homepage within 15-20 minutes'
    })


@app.route('/api/admin/collection-status')
def admin_collection_status():
    """V12.29: Get last collection run status for debugging."""
    secret = request.headers.get('X-Admin-Key')
    expected = os.environ.get('ADMIN_KEY', 'permitgrab-reset-2026')
    if secret != expected:
        return jsonify({'error': 'Unauthorized'}), 401

    stats_file = os.path.join(DATA_DIR, "collection_stats.json")
    if not os.path.exists(stats_file):
        return jsonify({'error': 'No collection stats found', 'path': stats_file}), 404

    try:
        with open(stats_file) as f:
            stats = json.load(f)

        # Calculate summary
        city_stats = stats.get('city_stats', {})
        total_cities = len(city_stats)
        cities_with_permits = sum(1 for s in city_stats.values() if s.get('normalized', 0) > 0)
        cities_empty = sum(1 for s in city_stats.values() if s.get('status') == 'success_empty')
        cities_errored = sum(1 for s in city_stats.values() if 'error' in str(s.get('status', '')))
        cities_timeout = sum(1 for s in city_stats.values() if 'timeout' in str(s.get('status', '').lower()))

        # Get list of failed cities
        failed_cities = [
            {'city': k, 'name': v.get('city_name', k), 'status': v.get('status', 'unknown')}
            for k, v in city_stats.items()
            if 'error' in str(v.get('status', '')) or 'timeout' in str(v.get('status', '').lower())
        ]

        return jsonify({
            'collected_at': stats.get('collected_at'),
            'total_permits': stats.get('total_permits', 0),
            'summary': {
                'total_cities_attempted': total_cities,
                'cities_with_permits': cities_with_permits,
                'cities_empty': cities_empty,
                'cities_errored': cities_errored,
                'cities_timeout': cities_timeout,
            },
            'failed_cities': failed_cities[:50],  # Limit to 50 to avoid huge response
            'trade_breakdown': stats.get('trade_breakdown', {}),
        })
    except Exception as e:
        return jsonify({'error': f'Failed to read stats: {str(e)}'}), 500


@app.route('/api/admin/validation-results')
def admin_validation_results():
    """V12.31: Get endpoint validation results for applying fixes."""
    secret = request.headers.get('X-Admin-Key')
    expected = os.environ.get('ADMIN_KEY', 'permitgrab-reset-2026')
    if secret != expected:
        return jsonify({'error': 'Unauthorized'}), 401

    validation_file = os.path.join(DATA_DIR, "endpoint_validation.json")
    if not os.path.exists(validation_file):
        return jsonify({'error': 'No validation results found. Run validate_endpoints.py first.'}), 404

    try:
        with open(validation_file) as f:
            data = json.load(f)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': f'Failed to read validation results: {str(e)}'}), 500


@app.route('/api/admin/suggested-fixes')
def admin_suggested_fixes():
    """V12.31: Get suggested fixes for broken endpoints."""
    secret = request.headers.get('X-Admin-Key')
    expected = os.environ.get('ADMIN_KEY', 'permitgrab-reset-2026')
    if secret != expected:
        return jsonify({'error': 'Unauthorized'}), 401

    fixes_file = os.path.join(DATA_DIR, "suggested_fixes.json")
    if not os.path.exists(fixes_file):
        return jsonify({'error': 'No suggested fixes found. Run validate_endpoints.py --fix first.'}), 404

    try:
        with open(fixes_file) as f:
            data = json.load(f)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': f'Failed to read suggested fixes: {str(e)}'}), 500


# ===========================
# DATABASE SETUP (V7)
# ===========================
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.exc import IntegrityError

# Configure PostgreSQL database (Render provides DATABASE_URL)
database_url = os.environ.get('DATABASE_URL', '')
# Render uses 'postgres://' but SQLAlchemy needs 'postgresql://'
if database_url.startswith('postgres://'):
    database_url = database_url.replace('postgres://', 'postgresql://', 1)

if database_url:
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
    print(f"[Database] Using PostgreSQL database")
else:
    # Fallback to SQLite for local development
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///permitgrab.db'
    print(f"[Database] Using SQLite (local development)")

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
# V11 Hotfix: pool_pre_ping verifies connections, pool_recycle prevents stale connections
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_pre_ping': True,
    'pool_recycle': 300,
}

# V12.24: Google Analytics and Search Console from env vars
app.config['GOOGLE_ANALYTICS_ID'] = os.environ.get('GOOGLE_ANALYTICS_ID', '')
app.config['GOOGLE_SITE_VERIFICATION'] = os.environ.get('GOOGLE_SITE_VERIFICATION', '')

db = SQLAlchemy(app)


class User(db.Model):
    """User model for PostgreSQL storage (V7 - replaces JSON file)."""
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
        }


# Create tables on startup
with app.app_context():
    db.create_all()
    print("[Database] Tables created/verified")


# Rate limiter setup
limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=["200 per day", "50 per hour"],
    storage_uri="memory://",
)

# Use Render persistent disk if available, otherwise local data directory
# Render disk is mounted at /var/data and persists across deploys
if os.path.isdir('/var/data'):
    DATA_DIR = '/var/data'
    print("[Server] Using Render persistent disk at /var/data")
else:
    DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
    print(f"[Server] Using local data directory at {DATA_DIR}")

# V11 Hotfix: Diagnostic logging for data directory
print(f"[Server] /var/data exists: {os.path.isdir('/var/data')}")
print(f"[Server] /var/data contents: {os.listdir('/var/data') if os.path.isdir('/var/data') else 'N/A'}")
print(f"[Server] DATA_DIR = {DATA_DIR}")
print(f"[Server] DATA_DIR exists: {os.path.isdir(DATA_DIR)}")
if os.path.isdir(DATA_DIR):
    print(f"[Server] DATA_DIR contents: {os.listdir(DATA_DIR)}")

# V12.1: Removed _sanitize_permits_file() - raw byte stripping corrupted JSON structure
# The correct approach is parse-then-rewrite in load_permits() using strict=False

SUBSCRIBERS_FILE = os.path.join(DATA_DIR, 'subscribers.json')
USERS_FILE = os.path.join(DATA_DIR, 'users.json')

# V12.12: Startup data loading state
# Track whether initial data has been loaded from disk
_initial_data_loaded = False
_collection_in_progress = False

def preload_data_from_disk():
    """V12.12: Load existing data from disk on startup BEFORE serving any requests.

    This ensures that even after a deploy, stale data is served immediately
    rather than showing 0 permits while waiting for collection to complete.

    V12.19: Auto-deletes corrupted files so next collection writes clean data.
    """
    global _initial_data_loaded
    permits_file = os.path.join(DATA_DIR, 'permits.json')
    if os.path.exists(permits_file):
        try:
            file_size = os.path.getsize(permits_file)
            print(f"[Server] V12.12: Found permits.json on disk ({file_size} bytes)")
            # Trigger a load to validate and process the data
            permits = load_permits()
            if permits:
                print(f"[Server] V12.12: Preloaded {len(permits)} permits from disk")
                _initial_data_loaded = True
            else:
                # load_permits returned empty - file may be corrupted
                print(f"[Server] V12.19: load_permits returned empty, checking file...")
        except json.JSONDecodeError as e:
            print(f"[Server] V12.19: CORRUPTED permits.json detected: {e}")
            print(f"[Server] V12.19: AUTO-DELETING corrupted file at {permits_file}")
            try:
                os.remove(permits_file)
                print(f"[Server] V12.19: Deleted corrupted file. Next collection will write clean data.")
            except OSError as del_err:
                print(f"[Server] V12.19: Failed to delete corrupted file: {del_err}")
        except Exception as e:
            print(f"[Server] V12.12: Failed to preload permits: {e}")
    else:
        print(f"[Server] V12.12: No permits.json found on disk - fresh deploy or disk wiped")

def is_data_loading():
    """V12.12: Check if we're in a loading state (no data available)."""
    if _initial_data_loaded:
        return False
    permits_file = os.path.join(DATA_DIR, 'permits.json')
    return not os.path.exists(permits_file) or os.path.getsize(permits_file) < 100

# Admin password from environment
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', '')

# ===========================
# LEAD SCORING ENGINE
# ===========================
# V7: FORCED LINEAR SPREAD - exact implementation from spec
# Output range: 40-99, guaranteed by linear mapping.

import hashlib


def calculate_lead_scores(permits):
    """
    V7 lead scoring with FORCED LINEAR SPREAD.
    Takes a list of permit dicts, returns a list of integer scores.
    Output range: 40-99, guaranteed by linear mapping.

    PROOF: When raw == min(raws) → output = 40 + 0 = 40
           When raw == max(raws) → output = 40 + 59 = 99
    If the minimum output score is not 40, this function is not being called.
    """
    from datetime import datetime

    if not permits:
        return []

    if len(permits) == 1:
        return [70]  # Single permit gets middle score

    # PHASE 1: Compute a raw score for each permit
    raw_scores = []
    for p in permits:
        raw = 0.0

        # Component A: Project value (will be normalized in phase 2)
        value = 0.0
        for key in ['project_value', 'estimated_cost', 'value', 'cost', 'amount']:
            v = p.get(key)
            if v is not None:
                try:
                    value = float(str(v).replace('$', '').replace(',', ''))
                    break
                except (ValueError, TypeError):
                    pass
        # Store raw value; normalization happens in phase 2

        # Component B: Recency (0-25 points)
        recency = 12.0  # default if no date found
        for key in ['filed_date', 'filing_date', 'date', 'created_date', 'issue_date']:
            d = p.get(key)
            if d:
                try:
                    if isinstance(d, str):
                        d = datetime.strptime(d[:10], '%Y-%m-%d')
                    days_old = (datetime.now() - d).days
                    # V12.27: Penalize future-dated permits (bad source data)
                    if days_old < 0:
                        recency = 0.0  # Future dates get zero recency points
                    else:
                        recency = max(0.0, 25.0 - (days_old * 0.8))
                    break
                except (ValueError, TypeError):
                    pass

        # Component C: Status (0-15 points)
        status_str = ''
        for key in ['status', 'permit_status', 'state']:
            s = p.get(key)
            if s:
                status_str = str(s).lower().strip()
                break
        status_scores = {
            'filed': 15, 'new': 15, 'active': 13, 'issued': 11,
            'approved': 11, 'permitted': 9, 'pending': 7, 'review': 7,
            'in progress': 6, 'completed': 3, 'closed': 1, 'expired': 1
        }
        status_pts = status_scores.get(status_str, 7.0)

        # Component D: Contact info (0-10 points)
        has_phone = False
        for key in ['phone', 'contact_phone', 'phone_number', 'tel']:
            if p.get(key):
                has_phone = True
                break
        has_name = False
        for key in ['contact_name', 'owner', 'owner_name', 'applicant', 'name']:
            if p.get(key):
                has_name = True
                break
        contact_pts = (5.0 if has_phone else 0.0) + (5.0 if has_name else 0.0)

        # V12.9: Component D2: Address quality (0-10 points)
        # Penalize permits without addresses - they're low-quality leads
        address = p.get('address', '') or ''
        address_clean = address.strip().lower()
        if not address_clean or address_clean in ['not provided', 'address not provided', 'n/a', 'none', '']:
            address_pts = 0.0
        elif len(address_clean) < 10:  # Very short addresses (just area names)
            address_pts = 3.0
        elif any(char.isdigit() for char in address_clean):  # Has street number
            address_pts = 10.0
        else:  # Has address but no street number
            address_pts = 5.0

        # Component E: Trade type (0-10 points)
        trade_str = ''
        for key in ['trade', 'permit_type', 'trade_category', 'type', 'work_type']:
            t = p.get(key)
            if t:
                trade_str = str(t).lower()
                break
        high_value = ['electrical', 'hvac', 'plumbing', 'new construction', 'fire']
        mid_value = ['roofing', 'interior', 'demolition', 'addition', 'structural']
        if any(h in trade_str for h in high_value):
            trade_pts = 10.0
        elif any(m in trade_str for m in mid_value):
            trade_pts = 7.0
        else:
            trade_pts = 4.0

        # Component F: Deterministic jitter (0-5 points, from permit ID hash)
        permit_id_str = ''
        for key in ['id', 'permit_number', 'permit_id', 'number']:
            pid = p.get(key)
            if pid is not None:
                permit_id_str = str(pid)
                break
        if not permit_id_str:
            permit_id_str = str(id(p))
        jitter = (int(hashlib.md5(permit_id_str.encode()).hexdigest()[:6], 16) % 50) / 10.0

        raw_scores.append({
            'value': value,
            'non_value_score': recency + status_pts + contact_pts + trade_pts + address_pts + jitter
        })

    # PHASE 2: Normalize the value component within this dataset
    values = [r['value'] for r in raw_scores]
    min_val = min(values)
    max_val = max(values)
    val_range = max_val - min_val if max_val != min_val else 1.0

    composite_scores = []
    for r in raw_scores:
        normalized_value = ((r['value'] - min_val) / val_range) * 35.0
        composite = normalized_value + r['non_value_score']
        composite_scores.append(composite)

    # PHASE 3: FORCED LINEAR SPREAD — THIS IS THE CRITICAL PART
    # Map the composite scores linearly so that:
    #   - The permit with the LOWEST composite score gets exactly 40
    #   - The permit with the HIGHEST composite score gets exactly 99
    #   - Everything else is evenly distributed between 40 and 99
    #
    # THIS IS NON-NEGOTIABLE. If the output min is not ~40, this step is broken.

    score_min = min(composite_scores)
    score_max = max(composite_scores)
    score_range = score_max - score_min if score_max != score_min else 1.0

    final_scores = []
    for cs in composite_scores:
        # Linear interpolation: score_min → 40, score_max → 99
        normalized = 40.0 + ((cs - score_min) / score_range) * 59.0
        final_scores.append(max(40, min(99, round(normalized))))

    return final_scores


def add_lead_scores(permits):
    """
    Wrapper that calls calculate_lead_scores and assigns scores to permits.
    Also assigns lead_quality tier based on score.
    """
    if not permits:
        return permits

    scores = calculate_lead_scores(permits)

    for i, p in enumerate(permits):
        score = scores[i] if i < len(scores) else 70

        # V12.21: Address quality penalty (replaces hard cap that clustered 95% at 65)
        # Use a percentage reduction to maintain differentiation between permits
        address = p.get('address', '') or ''
        address_clean = address.strip().lower()
        if not address_clean or address_clean in ['not provided', 'address not provided', 'n/a', 'none', '', 'location']:
            # V12.23: Reduce score by 50% for missing address (stronger penalty to push addressless permits down)
            score = max(40, round(score * 0.50))

        p['lead_score'] = score

        # Determine quality tier
        if score >= 85:
            p['lead_quality'] = 'hot'
        elif score >= 70:
            p['lead_quality'] = 'warm'
        else:
            p['lead_quality'] = 'standard'

    return permits


# ===========================
# TRADE CLASSIFICATION
# ===========================
def classify_trade(text):
    """Classify a permit into a trade category based on description text."""
    if not text:
        return "General Construction"

    text_lower = text.lower()
    scores = {}

    for trade, keywords in TRADE_CATEGORIES.items():
        score = sum(1 for kw in keywords if kw in text_lower)
        if score > 0:
            scores[trade] = score

    if not scores:
        return "General Construction"

    # Priority order for ties
    priority_trades = [
        "Electrical", "Plumbing", "HVAC", "Roofing", "Solar", "Fire Protection",
        "Demolition", "Signage", "Windows & Doors", "Structural",
        "Interior Renovation", "Landscaping & Exterior",
        "New Construction", "Addition", "General Construction"
    ]

    specific_matches = {t: s for t, s in scores.items() if t != "General Construction"}

    if specific_matches:
        max_score = max(specific_matches.values())
        top_matches = [t for t, s in specific_matches.items() if s == max_score]

        if len(top_matches) == 1:
            return top_matches[0]

        for trade in priority_trades:
            if trade in top_matches:
                return trade

        return top_matches[0]

    return "General Construction"


def reclassify_permit(permit):
    """Re-classify a permit's trade category based on its description and type fields."""
    text_parts = [
        permit.get('description', ''),
        permit.get('work_type', ''),
        permit.get('permit_type', '')
    ]
    text = ' '.join(filter(None, text_parts))
    permit['trade_category'] = classify_trade(text)
    return permit


def generate_permit_description(permit):
    """
    Generate a unique, factual description based on actual permit data.
    Falls back to building a description from permit fields if no real description exists,
    or if the description appears templated (same as others).
    """
    existing_desc = permit.get('description', '')

    # Build a factual description from permit data
    parts = []

    # Permit type
    permit_type = permit.get('permit_type', '') or permit.get('work_type', '')
    if permit_type:
        parts.append(permit_type.strip())

    # Trade category
    trade = permit.get('trade_category', '')
    if trade and trade not in ['General Construction', 'Other']:
        if not any(trade.lower() in p.lower() for p in parts):
            parts.append(f"({trade})")

    # Address
    address = permit.get('address', '')
    if address:
        parts.append(f"at {address}")

    # Value - V12.27: Skip if at $50M cap (unreliable data)
    cost = permit.get('estimated_cost', 0) or 0
    MAX_REASONABLE_COST = 50_000_000
    if cost > 0 and cost != MAX_REASONABLE_COST:
        if cost >= 1000000:
            parts.append(f"— ${cost/1000000:.1f}M project")
        elif cost >= 1000:
            parts.append(f"— ${cost/1000:.0f}K project")
        else:
            parts.append(f"— ${cost:,.0f}")

    # Status
    status = permit.get('status', '')
    if status:
        parts.append(f"[{status}]")

    # Permit number for uniqueness
    permit_num = permit.get('permit_number', '')
    if permit_num:
        parts.append(f"(Permit #{permit_num})")

    # Combine parts
    generated_desc = ' '.join(parts)

    # Return existing description if it's substantial and unique-looking
    # (has actual address or permit number in it), otherwise use generated
    if existing_desc and len(existing_desc) > 30:
        # Check if existing description contains unique identifiers
        has_address = address and address[:10] in existing_desc
        has_permit_num = permit_num and permit_num in existing_desc
        if has_address or has_permit_num:
            return existing_desc

    return generated_desc if generated_desc else existing_desc


# ===========================
# DATA LOADING
# ===========================
def format_permit_address(permit):
    """V12.11: Format address field appropriately.

    For county datasets, addresses may be location/area names (no street number).
    Detect these and label them as "Location:" instead of pretending they're street addresses.
    """
    address = permit.get('address', '') or ''
    if not address.strip():
        permit['display_address'] = 'Address not provided'
        permit['address_type'] = 'none'
        return

    address_clean = address.strip()

    # Check if it looks like a real street address (has a number at the start)
    import re
    has_street_number = bool(re.match(r'^\d+\s', address_clean))

    # Common area/location-only patterns (no street number, short, all caps)
    is_location_only = (
        not has_street_number and
        len(address_clean) < 30 and
        (address_clean.isupper() or
         address_clean.upper() in ['MONTGOMERY', 'ROCK SPRING', 'BETHESDA', 'SILVER SPRING',
                                   'ROCKVILLE', 'WHEATON', 'GERMANTOWN', 'GAITHERSBURG',
                                   'POTOMAC', 'CHEVY CHASE', 'TAKOMA PARK', 'KENSINGTON'])
    )

    if is_location_only:
        permit['display_address'] = f"Area: {address_clean.title()}"
        permit['address_type'] = 'location'
    elif not has_street_number and len(address_clean.split()) <= 3:
        # Short address without number - likely a location name
        permit['display_address'] = f"Location: {address_clean.title()}"
        permit['address_type'] = 'location'
    else:
        permit['display_address'] = address_clean
        permit['address_type'] = 'street'


def validate_permit_dates(permit):
    """V12.9: Validate and relabel future-dated permits.

    If filing_date is >30 days in the future, it's likely an expiration date,
    not a filing date. Relabel it appropriately.
    """
    filing_date_str = permit.get('filing_date', '')
    if not filing_date_str:
        return

    try:
        filing_date = datetime.strptime(str(filing_date_str)[:10], '%Y-%m-%d')
        days_from_now = (filing_date - datetime.now()).days

        if days_from_now > 30:
            # This is likely an expiration/completion date, not a filing date
            permit['expiration_date'] = filing_date_str
            permit['date_label'] = 'Expires'
            # Try to find an alternative filing date
            for alt_key in ['issued_date', 'issue_date', 'created_date', 'application_date']:
                alt_date = permit.get(alt_key)
                if alt_date:
                    try:
                        alt_parsed = datetime.strptime(str(alt_date)[:10], '%Y-%m-%d')
                        if (alt_parsed - datetime.now()).days <= 30:
                            permit['filing_date'] = str(alt_date)[:10]
                            permit['date_label'] = 'Filed'
                            return
                    except:
                        pass
            # No alternative found, keep expiration date but mark it
            permit['filing_date'] = filing_date_str
            permit['date_label'] = 'Expires'
        else:
            permit['date_label'] = 'Filed'
    except (ValueError, TypeError):
        permit['date_label'] = 'Filed'  # Default label


def load_permits():
    """Load permits from JSON file, re-classify trades, and generate unique descriptions."""
    path = os.path.join(DATA_DIR, 'permits.json')
    if os.path.exists(path):
        try:
            with open(path) as f:
                permits = json.load(f, strict=False)
            # V12.19: REMOVED re-write on load - this caused race conditions with collector
            # The collector already writes clean JSON via atomic_write_json()
            # Re-classify trades and generate descriptions on load
            for permit in permits:
                try:
                    reclassify_permit(permit)
                    validate_permit_dates(permit)  # V12.9: Fix future-dated permits
                    format_permit_address(permit)  # V12.11: Fix county address display
                    permit['display_description'] = generate_permit_description(permit)
                    # V12.21: Sanity check - cap outlier values at $50M (likely data entry errors)
                    if permit.get('estimated_cost', 0) > 50_000_000:
                        permit['estimated_cost'] = 50_000_000
                except Exception as e:
                    print(f"[Server] Warning: Failed to process permit: {e}")
                    continue
            return permits
        except json.JSONDecodeError as e:
            print(f"[Server] ERROR: Failed to parse permits.json: {e}")
            # V12.19: Auto-delete corrupted file so next collection writes clean data
            print(f"[Server] V12.19: AUTO-DELETING corrupted permits.json")
            try:
                os.remove(path)
                print(f"[Server] V12.19: Deleted corrupted file at {path}")
            except OSError as del_err:
                print(f"[Server] V12.19: Failed to delete: {del_err}")
            return []
        except Exception as e:
            print(f"[Server] ERROR: Unexpected error loading permits: {e}")
            return []
    return []

def load_stats():
    """Load collection stats."""
    path = os.path.join(DATA_DIR, 'collection_stats.json')
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f, strict=False)
        except Exception as e:
            print(f"[Server] ERROR: Failed to parse collection_stats.json: {e}")
            return {}
    return {}

def get_cities_with_data():
    """V12.10: Get cities sorted by permit volume (top cities first for footer)."""
    permits = load_permits()

    # Count permits per city
    city_counts = {}
    for p in permits:
        city = p.get('city', '')
        if city:
            city_counts[city] = city_counts.get(city, 0) + 1

    # Convert to city info format with slug, sorted by permit count
    all_cities = get_all_cities_info()
    city_lookup = {c['name']: c for c in all_cities}

    cities_with_counts = []
    for name, count in city_counts.items():
        if name in city_lookup:
            city_info = city_lookup[name].copy()
            city_info['permit_count'] = count
            cities_with_counts.append(city_info)

    # Sort by permit count descending (top cities first)
    cities_with_counts.sort(key=lambda x: x.get('permit_count', 0), reverse=True)
    return cities_with_counts


def get_suggested_cities(searched_slug, limit=6):
    """V12.9: Get similar city suggestions for 404 page using fuzzy matching."""
    all_cities = get_all_cities_info()
    active_cities = [c for c in all_cities if c.get('active', True)]

    # Calculate similarity scores
    suggestions = []
    searched_lower = searched_slug.lower().replace('-', ' ')

    for city in active_cities:
        slug_lower = city['slug'].lower().replace('-', ' ')
        name_lower = city['name'].lower()

        # Check multiple matching criteria
        slug_score = SequenceMatcher(None, searched_lower, slug_lower).ratio()
        name_score = SequenceMatcher(None, searched_lower, name_lower).ratio()

        # Boost if searched term is contained in name
        contains_boost = 0.3 if searched_lower in name_lower or name_lower in searched_lower else 0

        best_score = max(slug_score, name_score) + contains_boost
        if best_score > 0.3:  # Only include if somewhat similar
            suggestions.append((city, best_score))

    # Sort by score, take top matches
    suggestions.sort(key=lambda x: -x[1])
    return [s[0] for s in suggestions[:limit]]


def get_popular_cities(limit=12):
    """V12.9: Get popular cities for 404 page based on permit count."""
    permits = load_permits()
    # Count permits per city
    city_counts = {}
    for p in permits:
        city = p.get('city', '')
        if city:
            city_counts[city] = city_counts.get(city, 0) + 1

    # Get city info and sort by count
    all_cities = get_all_cities_info()
    city_lookup = {c['name']: c for c in all_cities}

    popular = []
    for name, count in sorted(city_counts.items(), key=lambda x: -x[1]):
        if name in city_lookup:
            city_info = city_lookup[name].copy()
            # V12.23: Don't show permit count on 404 page - it shows the collector limit (2000)
            # which looks suspiciously capped. Better to show no count than a misleading one.
            # city_info['permit_count'] = count  # Removed - shows API limit, not real total
            popular.append(city_info)
            if len(popular) >= limit:
                break

    return popular


def render_city_not_found(searched_slug):
    """V12.9: Render branded 404 page with city suggestions."""
    suggestions = get_suggested_cities(searched_slug)
    popular_cities = get_popular_cities()
    footer_cities = get_cities_with_data()

    return render_template(
        '404.html',
        searched_slug=searched_slug,
        suggestions=suggestions,
        popular_cities=popular_cities,
        footer_cities=footer_cities,
        show_city_suggestions=True,
    ), 404


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
# USER DATABASE FUNCTIONS (V7)
# ===========================
# All user operations now use PostgreSQL instead of JSON files


def find_user_by_email(email):
    """Find a user by email (case-insensitive). Returns User object or None."""
    if not email:
        return None
    email_lower = email.lower().strip()
    return User.query.filter(db.func.lower(User.email) == email_lower).first()


def get_current_user():
    """Get the currently logged-in user from session. Returns dict for backward compatibility."""
    user_email = session.get('user_email')
    if not user_email:
        return None
    user = find_user_by_email(user_email)
    if user:
        return user.to_dict()
    return None


def get_current_user_object():
    """Get the currently logged-in user as User object (for database operations)."""
    user_email = session.get('user_email')
    if not user_email:
        return None
    return find_user_by_email(user_email)


# ===========================
# BACKWARD COMPATIBILITY SHIMS (V7)
# ===========================
# These functions provide backward compatibility with code that expects
# the old dict-based user storage while actually using the database.

def load_users():
    """Load all users from database as list of dicts (backward compatibility)."""
    users = User.query.all()
    return [u.to_dict() for u in users]


def save_users(users):
    """Save users to database (backward compatibility - DEPRECATED).
    This is a no-op shim. Individual user updates should use db.session.commit().
    Kept for backward compatibility with code that still calls this.
    """
    # No-op: database operations should be done directly
    # Individual updates use db.session.commit()
    pass


def update_user_by_email(email, updates):
    """Update a user's fields by email (V7 helper)."""
    user = find_user_by_email(email)
    if user:
        for key, value in updates.items():
            if hasattr(user, key):
                setattr(user, key, value)
        db.session.commit()
        return True
    return False


def get_user_plan(user):
    """
    Returns 'pro', 'free', or 'anonymous'.
    Centralizes all plan checking logic to avoid inconsistencies.
    Recognizes Pro status from:
    - user.plan == 'pro'
    - user.plan == 'professional' (Stripe)
    - user.plan == 'enterprise'
    - user.stripe_subscription_status == 'active'
    """
    if not user:
        return 'anonymous'

    plan = (user.get('plan') or '').lower()

    # Check admin-set or Stripe-set plans
    if plan in ('pro', 'professional', 'enterprise'):
        return 'pro'

    # Check Stripe subscription status
    if user.get('stripe_subscription_status') == 'active':
        return 'pro'

    return 'free'


def is_pro(user):
    """Returns True if user has Pro access."""
    return get_user_plan(user) == 'pro'


@app.context_processor
def inject_nav_context():
    """Inject user, plan status, and nav_cities into all templates."""
    user = get_current_user()
    return {
        'user': user,
        'user_plan': get_user_plan(user),
        'is_pro': is_pro(user),
        'nav_cities': get_cities_with_data()
    }


# ===========================
# ANALYTICS HOOKS
# ===========================

@app.before_request
def analytics_before_request():
    """Capture UTM parameters and ensure session ID exists."""
    try:
        # Ensure analytics session ID
        if 'analytics_session_id' not in session:
            session['analytics_session_id'] = str(uuid.uuid4())

        # Capture UTM parameters
        utm_params = {}
        for key in ['utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content']:
            val = request.args.get(key)
            if val:
                utm_params[key] = val
        if utm_params:
            session['utm_params'] = utm_params
    except Exception:
        pass  # Never break the request


@app.after_request
def analytics_track_page_view(response):
    """Track page views for all successful page loads."""
    try:
        if response.status_code < 400 and request.endpoint:
            # Don't track static files, API calls, or health endpoint
            skip_prefixes = ('/static', '/api/', '/health', '/favicon', '/robots', '/sitemap')
            if not any(request.path.startswith(p) for p in skip_prefixes):
                analytics.track_event(
                    event_type='page_view',
                    page=request.path,
                    event_data={
                        'status_code': response.status_code,
                        'method': request.method
                    }
                )
    except Exception:
        pass  # Never break the response
    return response


def generate_unsubscribe_token():
    """Generate a unique unsubscribe token."""
    return secrets.token_urlsafe(32)


SAVED_LEADS_FILE = os.path.join(DATA_DIR, 'saved_leads.json')
PERMIT_HISTORY_FILE = os.path.join(DATA_DIR, 'permit_history.json')
VIOLATIONS_FILE = os.path.join(DATA_DIR, 'violations.json')
SIGNALS_FILE = os.path.join(DATA_DIR, 'signals.json')


def load_permit_history():
    """Load permit history index from JSON file."""
    if os.path.exists(PERMIT_HISTORY_FILE):
        with open(PERMIT_HISTORY_FILE) as f:
            return json.load(f)
    return {}


def load_violations():
    """Load code violations from JSON file."""
    if os.path.exists(VIOLATIONS_FILE):
        try:
            with open(VIOLATIONS_FILE) as f:
                return json.load(f, strict=False)
        except Exception as e:
            print(f"[Server] ERROR: Failed to parse violations.json: {e}")
            return []
    return []


def load_signals():
    """Load pre-construction signals from JSON file."""
    if os.path.exists(SIGNALS_FILE):
        try:
            with open(SIGNALS_FILE) as f:
                return json.load(f, strict=False)
        except Exception as e:
            print(f"[Server] ERROR: Failed to parse signals.json: {e}")
            return []
    return []


def normalize_address_for_lookup(address):
    """Normalize an address for lookup (matches collector.py logic)."""
    import re
    if not address:
        return ""
    addr = address.lower().strip()
    addr = re.sub(r'\s+', ' ', addr)
    replacements = [
        (r'\bstreet\b', 'st'),
        (r'\bavenue\b', 'ave'),
        (r'\bboulevard\b', 'blvd'),
        (r'\bdrive\b', 'dr'),
        (r'\broad\b', 'rd'),
        (r'\blane\b', 'ln'),
        (r'\bcourt\b', 'ct'),
        (r'\bplace\b', 'pl'),
        (r'\bapartment\b', 'apt'),
        (r'\bsuite\b', 'ste'),
        (r'\bnorth\b', 'n'),
        (r'\bsouth\b', 's'),
        (r'\beast\b', 'e'),
        (r'\bwest\b', 'w'),
    ]
    for pattern, replacement in replacements:
        addr = re.sub(pattern, replacement, addr)
    addr = re.sub(r'[^\w\s#-]', '', addr)
    return addr


def load_saved_leads():
    """Load saved leads from JSON file."""
    if os.path.exists(SAVED_LEADS_FILE):
        with open(SAVED_LEADS_FILE) as f:
            return json.load(f)
    return []


def save_saved_leads(leads):
    """Save saved leads to JSON file."""
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(SAVED_LEADS_FILE, 'w') as f:
        json.dump(leads, f, indent=2)


def get_user_saved_leads(user_email):
    """Get saved leads for a specific user."""
    all_leads = load_saved_leads()
    return [l for l in all_leads if l.get('user_email') == user_email]


# ===========================
# API ROUTES
# ===========================

@app.route('/')
def index():
    """Serve the dashboard."""
    # V8: Redirect new users to onboarding
    # V9 Fix: Only redirect truly new users - existing users with preferences or Pro plan skip onboarding
    if 'user_email' in session:
        user = find_user_by_email(session['user_email'])
        if user and not user.onboarding_completed:
            # Existing users who already have preferences or are Pro don't need onboarding
            has_preferences = user.city or user.trade
            is_pro = user.plan == 'pro'
            if has_preferences or is_pro:
                # Mark as completed so we don't check again
                user.onboarding_completed = True
                db.session.commit()
            else:
                return redirect('/onboarding')
    footer_cities = get_cities_with_data()

    # V9 Fix 5: Pass user preferences as default filters
    default_city = ''
    default_trade = ''
    if 'user_email' in session:
        user = find_user_by_email(session['user_email'])
        if user:
            default_city = user.city or ''
            default_trade = user.trade or ''

    # V12.15: Pass city_count for dynamic "X+ cities covered" display
    city_count = get_city_count()
    return render_template('dashboard.html', footer_cities=footer_cities,
                          default_city=default_city, default_trade=default_trade,
                          city_count=city_count)


# V9 Fix 9: /dashboard redirects to homepage
@app.route('/dashboard')
def dashboard_redirect():
    """Redirect /dashboard to / for user convenience."""
    return redirect('/')


# V10 Fix 5: /alerts redirects to account page
@app.route('/alerts')
def alerts_redirect():
    """Redirect /alerts to account page where alert settings live."""
    return redirect('/account')


@app.route('/health')
def health_check():
    """
    V12.12: Health check endpoint with data availability check.
    Returns 503 if no permit data is available (fresh deploy/empty disk).
    This prevents Render from routing traffic to an empty instance.
    """
    permits = load_permits()
    permit_count = len(permits)

    if permit_count == 0 and is_data_loading():
        # No data and we're in a loading state - return unhealthy
        return jsonify({
            'status': 'loading',
            'timestamp': datetime.now().isoformat(),
            'message': 'Data collection in progress',
            'permit_count': 0
        }), 503

    return jsonify({
        'status': 'ok',
        'timestamp': datetime.now().isoformat(),
        'permit_count': permit_count,
        'data_loaded': _initial_data_loaded
    }), 200


@app.route('/api/permits')
@limiter.limit("60 per minute")
def api_permits():
    """
    GET /api/permits
    Query params: city, trade, value, status, search, quality, page, per_page
    Returns paginated, filtered permit data with lead scores.

    FREEMIUM GATING: Non-Pro users see masked contact info on ALL permits.
    """
    permits = load_permits()

    # Add lead scores to all permits
    permits = add_lead_scores(permits)

    # Apply filters
    city = request.args.get('city', '')
    trade = request.args.get('trade', '')
    value = request.args.get('value', '')
    status = request.args.get('status', '')
    quality = request.args.get('quality', '')
    search = request.args.get('search', '').lower()

    # City filter - handle both slug (from onboarding) and city name (from dashboard)
    if city:
        # Try to resolve city slug to city name
        city_key, city_config = get_city_by_slug(city)
        if city_config:
            city_name = city_config.get('name', city)
        else:
            city_name = city  # Use as-is if not a valid slug
        permits = [p for p in permits if p.get('city') == city_name]

    # Trade filter - handle both slug (from onboarding) and trade category name (from dashboard)
    if trade and trade != 'all-trades':
        # Try to resolve trade slug to trade name
        trade_config = get_trade(trade)
        if trade_config:
            trade_name = trade_config.get('name', trade)
        else:
            trade_name = trade  # Use as-is if not a valid slug
        # Case-insensitive match for trade_category
        permits = [p for p in permits if p.get('trade_category', '').lower() == trade_name.lower()]
    if value:
        permits = [p for p in permits if p.get('value_tier') == value]
    if status:
        permits = [p for p in permits if p.get('status') == status]
    if quality:
        if quality == 'hot':
            permits = [p for p in permits if p.get('lead_quality') == 'hot']
        elif quality == 'warm':
            permits = [p for p in permits if p.get('lead_quality') in ('hot', 'warm')]
    if search:
        permits = [p for p in permits if search in
                   f"{p.get('address','')} {p.get('description','')} {p.get('contact_name','')} {p.get('permit_number','')} {p.get('zip','')}".lower()]

    # Sort by lead score (hot leads first)
    permits.sort(key=lambda x: x.get('lead_score', 0), reverse=True)

    # Pagination
    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 50))
    total = len(permits)
    start = (page - 1) * per_page
    page_permits = permits[start:start + per_page]

    # FREEMIUM GATING: Strip contact info for ALL permits for non-Pro users
    user = get_current_user()
    user_is_pro = is_pro(user)

    if not user_is_pro:
        # Strip contact info from ALL permits for non-Pro users
        for permit in page_permits:
            permit['contact_phone'] = None
            permit['contact_name'] = None
            permit['contact_email'] = None
            permit['owner_name'] = None
            permit['is_gated'] = True
    else:
        # Pro users get all contact info
        for permit in page_permits:
            permit['is_gated'] = False

    # V8: Add last_updated timestamp
    stats = load_stats()
    last_updated = stats.get('collected_at', '')

    # V12.27: Calculate total stats from ALL permits (not just page) for consistency
    all_permits = load_permits()
    total_value = sum(p.get('estimated_cost', 0) or 0 for p in all_permits)
    high_value_count = sum(1 for p in all_permits if (p.get('estimated_cost', 0) or 0) >= 100000)

    return jsonify({
        'permits': page_permits,
        'total': total,
        'page': page,
        'per_page': per_page,
        'total_pages': (total + per_page - 1) // per_page,
        'user_is_pro': user_is_pro,
        'last_updated': last_updated,
        # V12.27: Include aggregate stats for hero section consistency
        'total_value': total_value,
        'high_value_count': high_value_count,
        'total_permits': len(all_permits),
    })

@app.route('/api/stats')
def api_stats():
    """GET /api/stats - Dashboard statistics."""
    permits = load_permits()
    stats = load_stats()

    return jsonify({
        'total_permits': len(permits),
        'total_value': sum(p.get('estimated_cost', 0) for p in permits),
        # V12.18: High-value = $100K+ projects (more meaningful to contractors than lead score)
        'high_value_count': len([p for p in permits if p.get('estimated_cost', 0) >= 100000]),
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


@app.route('/api/cities')
def api_cities():
    """GET /api/cities - Get all active cities with info."""
    return jsonify({
        'count': get_city_count(),
        'cities': get_all_cities_info(),
    })


@app.route('/api/city-health')
def api_city_health():
    """GET /api/city-health - Get city API health status."""
    health_file = os.path.join(DATA_DIR, 'city_health.json')
    if os.path.exists(health_file):
        with open(health_file) as f:
            return jsonify(json.load(f))
    return jsonify({'status': 'no health data available'})


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

    # Track alert signup event
    analytics.track_event('alert_signup', event_data={
        'city': sub.get('city', ''),
        'trade': sub.get('trade', '')
    }, city_filter=sub.get('city'))

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
    """GET /api/export - Export filtered permits as CSV with lead scores.

    PRO FEATURE: Non-Pro users cannot export and are redirected to pricing.
    """
    # Check if user is Pro - exports are a Pro feature
    user = get_current_user()
    if not is_pro(user):
        return jsonify({
            'error': 'Export is a Pro feature',
            'upgrade_url': '/pricing'
        }), 403

    permits = load_permits()
    permits = add_lead_scores(permits)

    # Apply same filters as /api/permits
    city = request.args.get('city', '')
    trade = request.args.get('trade', '')
    quality = request.args.get('quality', '')

    if city:
        permits = [p for p in permits if p.get('city') == city]
    if trade:
        permits = [p for p in permits if p.get('trade_category') == trade]
    if quality:
        if quality == 'hot':
            permits = [p for p in permits if p.get('lead_quality') == 'hot']
        elif quality == 'warm':
            permits = [p for p in permits if p.get('lead_quality') in ('hot', 'warm')]

    # Sort by lead score
    permits.sort(key=lambda x: x.get('lead_score', 0), reverse=True)

    # Build CSV
    if not permits:
        return "No permits match your filters", 404

    headers = ['address', 'city', 'state', 'zip', 'trade_category', 'estimated_cost',
               'status', 'lifecycle_stage', 'filing_date', 'contact_name', 'contact_phone', 'description',
               'lead_score', 'lead_quality']

    lines = [','.join(headers)]
    for p in permits:
        # Build row with lifecycle stage
        row = []
        for h in headers:
            if h == 'lifecycle_stage':
                row.append(get_lifecycle_label(p))
            else:
                row.append(str(p.get(h, '')).replace(',', ';').replace('"', "'").replace('\n', ' ')[:200])
        lines.append(','.join(f'"{v}"' for v in row))

    csv_content = '\n'.join(lines)

    # Track CSV export event
    analytics.track_event('csv_export', event_data={
        'row_count': len(permits),
        'filters': {'city': city, 'trade': trade, 'quality': quality}
    }, city_filter=city, trade_filter=trade)

    return Response(
        csv_content,
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename=permitgrab_leads_{datetime.now().strftime("%Y%m%d")}.csv'}
    )


# ===========================
# SAVED LEADS / CRM API
# ===========================

@app.route('/api/saved-leads', methods=['GET'])
def get_saved_leads():
    """GET /api/saved-leads - Get saved leads for logged-in user."""
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Login required'}), 401

    user_leads = get_user_saved_leads(user['email'])

    # Enrich with permit data
    all_permits = load_permits()
    permits = add_lead_scores(all_permits)
    permit_map = {p.get('permit_number'): p for p in permits}

    enriched_leads = []
    for lead in user_leads:
        permit = permit_map.get(lead.get('permit_id'), {})
        enriched_leads.append({
            **lead,
            'permit': permit,
        })

    # Calculate stats
    total_value = sum(l['permit'].get('estimated_cost', 0) for l in enriched_leads if l.get('permit'))
    status_counts = {}
    for l in enriched_leads:
        status = l.get('status', 'new')
        status_counts[status] = status_counts.get(status, 0) + 1

    return jsonify({
        'leads': enriched_leads,
        'total': len(enriched_leads),
        'total_value': total_value,
        'status_counts': status_counts,
    })


@app.route('/api/saved-leads', methods=['POST'])
def save_lead():
    """POST /api/saved-leads - Save a lead for the logged-in user.

    PRO FEATURE: Only Pro users can save leads.
    """
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Login required'}), 401

    # Save Lead is a Pro feature
    if not is_pro(user):
        return jsonify({
            'error': 'Save Lead is a Pro feature',
            'upgrade_url': '/pricing'
        }), 403

    data = request.get_json()
    if not data or not data.get('permit_id'):
        return jsonify({'error': 'permit_id required'}), 400

    all_leads = load_saved_leads()

    # Check if already saved
    existing = next((l for l in all_leads if l['user_email'] == user['email'] and l['permit_id'] == data['permit_id']), None)
    if existing:
        return jsonify({'error': 'Lead already saved'}), 409

    new_lead = {
        'permit_id': data['permit_id'],
        'user_email': user['email'],
        'status': 'new',
        'notes': '',
        'date_saved': datetime.now().isoformat(),
    }

    all_leads.append(new_lead)
    save_saved_leads(all_leads)

    # Track lead save event
    analytics.track_event('lead_save', event_data={
        'permit_id': data['permit_id'],
        'permit_value': data.get('permit_value', 0)
    })

    return jsonify({'message': 'Lead saved', 'lead': new_lead}), 201


@app.route('/api/saved-leads/<permit_id>', methods=['PUT'])
def update_saved_lead(permit_id):
    """PUT /api/saved-leads/<permit_id> - Update status/notes for a saved lead."""
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Login required'}), 401

    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400

    all_leads = load_saved_leads()
    lead = next((l for l in all_leads if l['user_email'] == user['email'] and l['permit_id'] == permit_id), None)

    if not lead:
        return jsonify({'error': 'Lead not found'}), 404

    # Update fields
    if 'status' in data:
        lead['status'] = data['status']
    if 'notes' in data:
        lead['notes'] = data['notes']
    lead['updated_at'] = datetime.now().isoformat()

    save_saved_leads(all_leads)

    return jsonify({'message': 'Lead updated', 'lead': lead})


@app.route('/api/saved-leads/<permit_id>', methods=['DELETE'])
def delete_saved_lead(permit_id):
    """DELETE /api/saved-leads/<permit_id> - Remove a saved lead."""
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Login required'}), 401

    all_leads = load_saved_leads()
    original_count = len(all_leads)
    all_leads = [l for l in all_leads if not (l['user_email'] == user['email'] and l['permit_id'] == permit_id)]

    if len(all_leads) == original_count:
        return jsonify({'error': 'Lead not found'}), 404

    save_saved_leads(all_leads)

    return jsonify({'message': 'Lead removed'})


@app.route('/api/saved-leads/export')
def export_saved_leads():
    """GET /api/saved-leads/export - Export saved leads as CSV.

    PRO FEATURE: Only Pro users can export.
    """
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Login required'}), 401

    if not is_pro(user):
        return jsonify({
            'error': 'Export is a Pro feature',
            'upgrade_url': '/pricing'
        }), 403

    user_leads = get_user_saved_leads(user['email'])
    all_permits = load_permits()
    permits = add_lead_scores(all_permits)
    permit_map = {p.get('permit_number'): p for p in permits}

    if not user_leads:
        return "No saved leads to export", 404

    headers = ['address', 'city', 'state', 'zip', 'trade_category', 'estimated_cost',
               'permit_status', 'lifecycle_stage', 'filing_date', 'contact_name', 'contact_phone', 'description',
               'lead_score', 'lead_quality', 'crm_status', 'notes', 'date_saved']

    lines = [','.join(headers)]
    for lead in user_leads:
        permit = permit_map.get(lead.get('permit_id'), {})
        row = [
            str(permit.get('address', '')).replace(',', ';').replace('"', "'"),
            str(permit.get('city', '')),
            str(permit.get('state', '')),
            str(permit.get('zip', '')),
            str(permit.get('trade_category', '')),
            str(permit.get('estimated_cost', '')),
            str(permit.get('status', '')),
            get_lifecycle_label(permit),
            str(permit.get('filing_date', '')),
            str(permit.get('contact_name', '')).replace(',', ';'),
            str(permit.get('contact_phone', '')),
            str(permit.get('description', '')).replace(',', ';').replace('"', "'").replace('\n', ' ')[:150],
            str(permit.get('lead_score', '')),
            str(permit.get('lead_quality', '')),
            str(lead.get('status', '')),
            str(lead.get('notes', '')).replace(',', ';').replace('"', "'").replace('\n', ' ')[:100],
            str(lead.get('date_saved', ''))[:10],
        ]
        lines.append(','.join(f'"{v}"' for v in row))

    csv_content = '\n'.join(lines)

    return Response(
        csv_content,
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename=permitgrab_my_leads_{datetime.now().strftime("%Y%m%d")}.csv'}
    )


# ===========================
# SAVED SEARCHES API
# ===========================

SAVED_SEARCHES_FILE = os.path.join(DATA_DIR, 'saved_searches.json')

def load_saved_searches():
    """Load all saved searches from JSON file."""
    if os.path.exists(SAVED_SEARCHES_FILE):
        try:
            with open(SAVED_SEARCHES_FILE, 'r') as f:
                return json.load(f)
        except:
            return []
    return []

def save_saved_searches(searches):
    """Save all saved searches to JSON file."""
    with open(SAVED_SEARCHES_FILE, 'w') as f:
        json.dump(searches, f, indent=2)

def get_user_saved_searches(email):
    """Get saved searches for a specific user."""
    all_searches = load_saved_searches()
    return [s for s in all_searches if s.get('user_email') == email]

@app.route('/api/saved-searches', methods=['GET'])
def get_saved_searches():
    """GET /api/saved-searches - Get user's saved searches."""
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Login required'}), 401

    searches = get_user_saved_searches(user['email'])
    return jsonify({'searches': searches})

@app.route('/api/saved-searches', methods=['POST'])
def create_saved_search():
    """POST /api/saved-searches - Create a new saved search.

    PRO FEATURE: Only Pro users can save searches.
    """
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Login required'}), 401

    # Save Search is a Pro feature
    if not is_pro(user):
        return jsonify({
            'error': 'Save Search is a Pro feature',
            'upgrade_url': '/pricing'
        }), 403

    data = request.get_json()
    if not data:
        return jsonify({'error': 'Missing search data'}), 400

    # Create the saved search object
    search = {
        'id': str(uuid.uuid4()),
        'user_email': user['email'],
        'city': data.get('city', ''),
        'trade': data.get('trade', ''),
        'value_tier': data.get('value_tier', ''),
        'status': data.get('status', ''),
        'quality': data.get('quality', ''),
        'search_text': data.get('search_text', ''),
        'daily_alerts': True,  # Default to daily alerts enabled
        'created_at': datetime.now().isoformat(),
    }

    # Build a human-readable name for the search
    parts = []
    if search['city']:
        parts.append(search['city'])
    if search['trade']:
        parts.append(search['trade'])
    if search['value_tier']:
        parts.append(f"Value: {search['value_tier']}")
    search['name'] = ' | '.join(parts) if parts else 'All Permits'

    all_searches = load_saved_searches()
    all_searches.append(search)
    save_saved_searches(all_searches)

    return jsonify({'message': 'Search saved', 'search': search})

@app.route('/api/saved-searches/<search_id>', methods=['DELETE'])
def delete_saved_search(search_id):
    """DELETE /api/saved-searches/<id> - Delete a saved search."""
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Login required'}), 401

    all_searches = load_saved_searches()
    original_count = len(all_searches)
    all_searches = [s for s in all_searches if not (s['user_email'] == user['email'] and s['id'] == search_id)]

    if len(all_searches) == original_count:
        return jsonify({'error': 'Search not found'}), 404

    save_saved_searches(all_searches)
    return jsonify({'message': 'Search deleted'})

@app.route('/api/saved-searches/<search_id>', methods=['PUT'])
def update_saved_search(search_id):
    """PUT /api/saved-searches/<id> - Update a saved search (e.g., toggle alerts)."""
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Login required'}), 401

    data = request.get_json()
    all_searches = load_saved_searches()

    for search in all_searches:
        if search['id'] == search_id and search['user_email'] == user['email']:
            if 'daily_alerts' in data:
                search['daily_alerts'] = data['daily_alerts']
            if 'name' in data:
                search['name'] = data['name']
            save_saved_searches(all_searches)
            return jsonify({'message': 'Search updated', 'search': search})

    return jsonify({'error': 'Search not found'}), 404


# ===========================
# PERMIT HISTORY API
# ===========================

@app.route('/api/permit-history/<path:address>')
def api_permit_history(address):
    """
    GET /api/permit-history/<address>
    Returns historical permits at the given address.
    """
    history = load_permit_history()

    # Normalize the input address for lookup
    normalized_addr = normalize_address_for_lookup(address)

    if not normalized_addr:
        return jsonify({'error': 'Address required'}), 400

    # Look up in history index
    entry = history.get(normalized_addr)

    if not entry:
        # Try partial match
        for key, value in history.items():
            if normalized_addr in key or key in normalized_addr:
                entry = value
                break

    if not entry:
        return jsonify({
            'address': address,
            'permits': [],
            'permit_count': 0,
            'is_repeat_renovator': False,
        })

    permit_count = len(entry.get('permits', []))

    return jsonify({
        'address': entry.get('address', address),
        'city': entry.get('city', ''),
        'state': entry.get('state', ''),
        'permits': entry.get('permits', []),
        'permit_count': permit_count,
        'is_repeat_renovator': permit_count >= 3,
    })


# ===========================
# CODE VIOLATIONS API
# ===========================

@app.route('/api/violations')
def api_violations():
    """
    GET /api/violations
    Query params: city
    Returns recent code violations, flagged as pre-leads if no matching permit.
    """
    violations = load_violations()
    permits = load_permits()

    city = request.args.get('city', '')

    if city:
        violations = [v for v in violations if v.get('city') == city]

    # Build set of permit addresses for cross-reference
    permit_addresses = set()
    for p in permits:
        addr = normalize_address_for_lookup(p.get('address', ''))
        if addr:
            permit_addresses.add(addr)

    # Mark violations as pre-leads if no matching permit
    for v in violations:
        v_addr = normalize_address_for_lookup(v.get('address', ''))
        v['has_matching_permit'] = v_addr in permit_addresses
        v['is_pre_lead'] = not v['has_matching_permit']

    # Sort: pre-leads first, then by date
    violations.sort(key=lambda x: (not x.get('is_pre_lead', False), x.get('violation_date', '') or ''), reverse=True)

    # Stats
    pre_lead_count = sum(1 for v in violations if v.get('is_pre_lead'))
    cities = sorted(set(v.get('city', '') for v in load_violations() if v.get('city')))

    return jsonify({
        'violations': violations[:200],  # Limit response size
        'total': len(violations),
        'pre_lead_count': pre_lead_count,
        'cities': cities,
    })


@app.route('/api/violations/<path:address>')
def api_violations_by_address(address):
    """
    GET /api/violations/<address>
    Returns violations at a specific address.
    """
    violations = load_violations()
    normalized_addr = normalize_address_for_lookup(address)

    if not normalized_addr:
        return jsonify({'violations': [], 'count': 0})

    # Find violations at this address
    matching = []
    for v in violations:
        v_addr = normalize_address_for_lookup(v.get('address', ''))
        if normalized_addr == v_addr or normalized_addr in v_addr or v_addr in normalized_addr:
            matching.append(v)

    return jsonify({
        'violations': matching,
        'count': len(matching),
        'has_active_violations': any(v.get('status', '').lower() in ('open', 'active', 'pending') for v in matching),
    })


# ===========================
# CONTRACTOR INTELLIGENCE API
# ===========================

@app.route('/api/contractors')
def api_contractors():
    """
    GET /api/contractors
    Query params: city, search, sort_by, sort_order, page, per_page
    Returns aggregated contractor data from permits.
    """
    permits = load_permits()

    # Filter by city if specified
    city = request.args.get('city', '')
    if city:
        permits = [p for p in permits if p.get('city') == city]

    # Aggregate by contractor name
    contractors = {}
    for p in permits:
        name = p.get('contact_name', '').strip()
        if not name or name.lower() in ('n/a', 'unknown', 'none', ''):
            continue

        if name not in contractors:
            contractors[name] = {
                'name': name,
                'total_permits': 0,
                'total_value': 0,
                'cities': set(),
                'trades': {},
                'most_recent_date': '',
                'permits': [],
            }

        contractors[name]['total_permits'] += 1
        contractors[name]['total_value'] += p.get('estimated_cost', 0) or 0
        contractors[name]['cities'].add(p.get('city', ''))

        trade = p.get('trade_category', 'Other')
        contractors[name]['trades'][trade] = contractors[name]['trades'].get(trade, 0) + 1

        filing_date = p.get('filing_date', '')
        if filing_date > contractors[name]['most_recent_date']:
            contractors[name]['most_recent_date'] = filing_date

        contractors[name]['permits'].append(p.get('permit_number'))

    # Convert to list and determine primary trade
    contractor_list = []
    for name, data in contractors.items():
        primary_trade = max(data['trades'].items(), key=lambda x: x[1])[0] if data['trades'] else 'Unknown'
        contractor_list.append({
            'name': data['name'],
            'total_permits': data['total_permits'],
            'total_value': data['total_value'],
            'cities': sorted(list(data['cities'])),
            'city_count': len(data['cities']),
            'primary_trade': primary_trade,
            'most_recent_date': data['most_recent_date'],
            'permit_ids': data['permits'][:50],  # Limit stored permits
        })

    # Search filter
    search = request.args.get('search', '').lower()
    if search:
        contractor_list = [c for c in contractor_list if search in c['name'].lower()]

    # Sorting
    sort_by = request.args.get('sort_by', 'total_permits')
    sort_order = request.args.get('sort_order', 'desc')
    reverse = sort_order == 'desc'

    if sort_by == 'name':
        contractor_list.sort(key=lambda x: x['name'].lower(), reverse=reverse)
    elif sort_by == 'total_value':
        contractor_list.sort(key=lambda x: x['total_value'], reverse=reverse)
    elif sort_by == 'most_recent_date':
        contractor_list.sort(key=lambda x: x['most_recent_date'] or '', reverse=reverse)
    else:
        contractor_list.sort(key=lambda x: x['total_permits'], reverse=reverse)

    # Pagination
    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 50))
    total = len(contractor_list)
    start = (page - 1) * per_page
    page_contractors = contractor_list[start:start + per_page]

    return jsonify({
        'contractors': page_contractors,
        'total': total,
        'page': page,
        'per_page': per_page,
        'total_pages': (total + per_page - 1) // per_page,
    })


@app.route('/api/contractors/<path:name>')
def api_contractor_detail(name):
    """
    GET /api/contractors/<name>
    Returns all permits for a specific contractor.
    """
    permits = load_permits()
    permits = add_lead_scores(permits)

    # Find permits by contractor name (case-insensitive)
    contractor_permits = [p for p in permits if p.get('contact_name', '').lower() == name.lower()]

    if not contractor_permits:
        return jsonify({'error': 'Contractor not found'}), 404

    # Calculate stats
    total_value = sum(p.get('estimated_cost', 0) or 0 for p in contractor_permits)
    cities = sorted(set(p.get('city', '') for p in contractor_permits))
    trades = {}
    for p in contractor_permits:
        trade = p.get('trade_category', 'Other')
        trades[trade] = trades.get(trade, 0) + 1

    return jsonify({
        'name': name,
        'permits': contractor_permits,
        'total_permits': len(contractor_permits),
        'total_value': total_value,
        'cities': cities,
        'trade_breakdown': trades,
    })


@app.route('/api/contractors/top')
def api_top_contractors():
    """
    GET /api/contractors/top
    Query params: city, limit
    Returns top contractors by permit volume.
    """
    permits = load_permits()

    city = request.args.get('city', '')
    if city:
        permits = [p for p in permits if p.get('city') == city]

    limit = int(request.args.get('limit', 5))

    # Aggregate by contractor
    contractors = {}
    for p in permits:
        name = p.get('contact_name', '').strip()
        if not name or name.lower() in ('n/a', 'unknown', 'none', ''):
            continue

        if name not in contractors:
            contractors[name] = {'name': name, 'permits': 0, 'value': 0}

        contractors[name]['permits'] += 1
        contractors[name]['value'] += p.get('estimated_cost', 0) or 0

    # Sort by permit count
    top_list = sorted(contractors.values(), key=lambda x: x['permits'], reverse=True)[:limit]

    return jsonify({
        'top_contractors': top_list,
        'city': city or 'All Cities',
    })


@app.route('/contractors')
def contractors_page():
    """Render the Contractors Intelligence page."""
    footer_cities = get_cities_with_data()
    return render_template('contractors.html', footer_cities=footer_cities)


@app.route('/pricing')
def pricing_page():
    """Render the Pricing page."""
    user = get_current_user()
    cities = get_all_cities_info()
    city_count = get_city_count()
    footer_cities = get_cities_with_data()
    # V12.25: Pass permit count for dynamic "By the Numbers" section
    permits = load_permits()
    permit_count = len(permits)
    return render_template('pricing.html', user=user, cities=cities, city_count=city_count, footer_cities=footer_cities, permit_count=permit_count)


@app.route('/signup')
def signup_page():
    """Render the Sign Up page."""
    # Redirect if already logged in
    if get_current_user():
        return redirect('/')
    footer_cities = get_cities_with_data()
    return render_template('signup.html', footer_cities=footer_cities)


@app.route('/login')
def login_page():
    """Render the Login page."""
    # Redirect if already logged in
    if get_current_user():
        return redirect('/')
    footer_cities = get_cities_with_data()
    return render_template('login.html', footer_cities=footer_cities)


# ===========================
# PASSWORD RESET
# ===========================
PASSWORD_RESET_FILE = os.path.join(DATA_DIR, 'password_reset_tokens.json')


def load_reset_tokens():
    """Load password reset tokens from JSON file."""
    if os.path.exists(PASSWORD_RESET_FILE):
        with open(PASSWORD_RESET_FILE) as f:
            return json.load(f)
    return {}


def save_reset_tokens(tokens):
    """Save password reset tokens to JSON file."""
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(PASSWORD_RESET_FILE, 'w') as f:
        json.dump(tokens, f, indent=2)


def generate_reset_token():
    """Generate a secure random token for password reset."""
    return secrets.token_urlsafe(32)


def cleanup_expired_tokens():
    """Remove expired reset tokens."""
    tokens = load_reset_tokens()
    now = datetime.now().isoformat()
    valid_tokens = {k: v for k, v in tokens.items() if v.get('expires', '') > now}
    save_reset_tokens(valid_tokens)
    return valid_tokens


@app.route('/forgot-password')
def forgot_password_page():
    """Render the Forgot Password page."""
    footer_cities = get_cities_with_data()
    return render_template('forgot_password.html', footer_cities=footer_cities)


@app.route('/api/forgot-password', methods=['POST'])
@limiter.limit("5 per minute")
def api_forgot_password():
    """
    POST /api/forgot-password - Request a password reset email.
    Body: { email: string }
    """
    data = request.get_json()
    if not data or not data.get('email'):
        return jsonify({'error': 'Email is required'}), 400

    email = data['email'].lower().strip()

    # Check if user exists
    users = load_users()
    user = next((u for u in users if u['email'].lower() == email), None)

    # Always return success to prevent email enumeration
    if not user:
        return jsonify({'success': True, 'message': 'If that email exists, a reset link has been sent.'})

    # Generate token with 1-hour expiry
    token = generate_reset_token()
    expires = (datetime.now() + timedelta(hours=1)).isoformat()

    # Save token
    tokens = load_reset_tokens()
    tokens[token] = {
        'email': email,
        'expires': expires,
        'used': False
    }
    save_reset_tokens(tokens)

    # Send reset email
    reset_url = f"https://permitgrab.com/reset-password/{token}"
    html_body = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; color: #333; }}
            .container {{ max-width: 600px; margin: 0 auto; padding: 40px 20px; }}
            .logo {{ font-size: 24px; font-weight: 700; color: #111; margin-bottom: 24px; }}
            .logo span {{ color: #f97316; }}
            .btn {{ display: inline-block; background: #2563eb; color: white; padding: 14px 28px; border-radius: 8px; text-decoration: none; font-weight: 600; }}
            .footer {{ margin-top: 32px; font-size: 13px; color: #666; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="logo">Permit<span>Grab</span></div>
            <h2>Reset Your Password</h2>
            <p>We received a request to reset your password. Click the button below to create a new password:</p>
            <p><a href="{reset_url}" class="btn">Reset Password</a></p>
            <p>Or copy and paste this link into your browser:</p>
            <p style="word-break: break-all; color: #2563eb;">{reset_url}</p>
            <p><strong>This link expires in 1 hour.</strong></p>
            <p>If you didn't request this, you can safely ignore this email.</p>
            <div class="footer">
                <p>&copy; 2026 PermitGrab. All rights reserved.</p>
            </div>
        </div>
    </body>
    </html>
    """

    try:
        from email_alerts import send_email
        send_email(email, "Reset Your PermitGrab Password", html_body)
    except Exception as e:
        print(f"Failed to send password reset email: {e}")
        # Still return success to prevent email enumeration

    return jsonify({'success': True, 'message': 'If that email exists, a reset link has been sent.'})


@app.route('/reset-password/<token>')
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


@app.route('/api/reset-password', methods=['POST'])
@limiter.limit("10 per minute")
def api_reset_password():
    """
    POST /api/reset-password - Reset password with valid token.
    Body: { token: string, password: string }
    """
    data = request.get_json()
    if not data or not data.get('token') or not data.get('password'):
        return jsonify({'error': 'Token and password are required'}), 400

    token = data['token']
    new_password = data['password']

    # Validate password length
    if len(new_password) < 8:
        return jsonify({'error': 'Password must be at least 8 characters'}), 400

    # Validate token
    cleanup_expired_tokens()
    tokens = load_reset_tokens()

    if token not in tokens:
        return jsonify({'error': 'Invalid or expired reset link'}), 400

    token_data = tokens[token]
    if token_data.get('used'):
        return jsonify({'error': 'This reset link has already been used'}), 400

    now = datetime.now().isoformat()
    if token_data.get('expires', '') < now:
        return jsonify({'error': 'This reset link has expired'}), 400

    email = token_data['email']

    # Update user password (V7: direct database update)
    user = find_user_by_email(email)
    if not user:
        return jsonify({'error': 'User not found'}), 400

    user.password_hash = generate_password_hash(new_password)
    db.session.commit()

    # Mark token as used
    tokens[token]['used'] = True
    save_reset_tokens(tokens)

    return jsonify({'success': True, 'message': 'Password has been reset. You can now log in.'})


@app.route('/get-alerts')
def get_alerts_page():
    """Render the Get Alerts page."""
    cities = get_cities_with_data()  # Only show cities with data
    footer_cities = cities
    return render_template('get_alerts.html', cities=cities, footer_cities=footer_cities)


@app.route('/privacy')
def privacy_page():
    """Render the Privacy Policy page."""
    footer_cities = get_cities_with_data()
    return render_template('privacy.html', footer_cities=footer_cities)


@app.route('/terms')
def terms_page():
    """Render the Terms of Service page."""
    footer_cities = get_cities_with_data()
    return render_template('terms.html', footer_cities=footer_cities)


@app.route('/about')
def about_page():
    """Render the About page."""
    footer_cities = get_cities_with_data()
    return render_template('about.html', footer_cities=footer_cities)


@app.route('/stats')
def stats_page():
    """V12.23 SEO: Render building permit statistics page."""
    permits = load_permits()
    footer_cities = get_cities_with_data()

    # Calculate totals
    total_permits = len(permits)
    total_value = sum(p.get('estimated_cost', 0) or 0 for p in permits)
    high_value_count = sum(1 for p in permits if (p.get('estimated_cost', 0) or 0) >= 100000)

    # Top cities by permit count
    city_stats = {}
    for p in permits:
        city_name = p.get('city', '')
        if city_name:
            if city_name not in city_stats:
                city_stats[city_name] = {
                    'name': city_name,
                    'state': p.get('state', ''),
                    'slug': p.get('city', '').lower().replace(' ', '-'),
                    'permit_count': 0,
                    'total_value': 0,
                }
            city_stats[city_name]['permit_count'] += 1
            city_stats[city_name]['total_value'] += p.get('estimated_cost', 0) or 0

    # Calculate averages and sort
    for city in city_stats.values():
        city['avg_value'] = city['total_value'] / city['permit_count'] if city['permit_count'] > 0 else 0

    top_cities = sorted(city_stats.values(), key=lambda x: x['permit_count'], reverse=True)[:10]

    # Trade breakdown
    trade_counts = {}
    for p in permits:
        trade = p.get('trade_category', 'Other')
        trade_counts[trade] = trade_counts.get(trade, 0) + 1

    trade_breakdown = [
        {'name': trade, 'count': count, 'percentage': (count / total_permits * 100) if total_permits > 0 else 0}
        for trade, count in sorted(trade_counts.items(), key=lambda x: -x[1])
    ]

    # V12.25: Use get_city_count() for consistency with homepage
    return render_template('stats.html',
                           total_permits=total_permits,
                           total_value=total_value,
                           high_value_count=high_value_count,
                           city_count=get_city_count(),
                           top_cities=top_cities,
                           trade_breakdown=trade_breakdown,
                           last_updated=datetime.now().strftime('%Y-%m-%d'),
                           footer_cities=footer_cities)


@app.route('/map')
def map_page():
    """V12.26: Interactive permit heat map with Leaflet.js."""
    user = get_current_user()
    is_pro = user and user.plan == 'pro'
    cities = get_all_cities_info()
    footer_cities = get_cities_with_data()
    return render_template('map.html',
                           is_pro=is_pro,
                           cities=cities,
                           footer_cities=footer_cities)


@app.route('/contact')
def contact_page():
    """Render the Contact page."""
    footer_cities = get_cities_with_data()
    return render_template('contact.html', footer_cities=footer_cities)


@app.route('/api/contact', methods=['POST'])
def api_contact():
    """Handle contact form submissions."""
    data = request.get_json()
    if not data or not data.get('email') or not data.get('message'):
        return jsonify({'error': 'Email and message required'}), 400

    # Store contact message (in production, would email this)
    contact_file = os.path.join(DATA_DIR, 'contact_messages.json')
    messages = []
    if os.path.exists(contact_file):
        with open(contact_file) as f:
            messages = json.load(f)

    messages.append({
        'name': data.get('name', ''),
        'email': data['email'],
        'subject': data.get('subject', 'general'),
        'message': data['message'],
        'timestamp': datetime.now().isoformat()
    })

    with open(contact_file, 'w') as f:
        json.dump(messages, f, indent=2)

    return jsonify({'success': True})


@app.route('/onboarding')
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


@app.route('/api/onboarding', methods=['POST'])
def api_onboarding():
    """Save user onboarding preferences (city, trade, alerts)."""
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Not logged in'}), 401

    data = request.get_json()
    if not data:
        return jsonify({'error': 'Invalid request'}), 400

    city = data.get('city', '')
    trade = data.get('trade', '')
    daily_alerts = data.get('daily_alerts', False)

    # Update user preferences (V7: direct database update)
    user_obj = find_user_by_email(user['email'])
    if user_obj:
        user_obj.city = city
        user_obj.trade = trade
        user_obj.daily_alerts = daily_alerts
        user_obj.onboarding_completed = True
        db.session.commit()

    # If they opted into alerts, add them to subscribers
    if daily_alerts:
        subs = load_subscribers()
        if not any(s.get('email') == user['email'] for s in subs):
            subs.append({
                'email': user['email'],
                'name': user.get('name', ''),
                'city': city,
                'trade': trade,
                'subscribed_at': datetime.now().isoformat()
            })
            save_subscribers(subs)

    # Track onboarding complete event
    analytics.track_event('onboarding_complete', event_data={
        'city': city,
        'trade': trade,
        'daily_alerts': daily_alerts
    }, city_filter=city, trade_filter=trade)

    return jsonify({'success': True})


@app.route('/register')
def register_redirect():
    """Redirect /register to /signup."""
    return redirect('/signup', code=301)


# ===========================
# TREND ANALYTICS API
# ===========================

@app.route('/api/analytics/volume')
def api_analytics_volume():
    """
    GET /api/analytics/volume
    Query params: city, weeks (default 12)
    Returns weekly permit counts for trend analysis.
    """
    permits = load_permits()

    city = request.args.get('city', '')
    weeks = int(request.args.get('weeks', 12))

    if city:
        permits = [p for p in permits if p.get('city') == city]

    # Group by week (use module-level timedelta import)
    now = datetime.now()
    weekly_counts = {}

    for i in range(weeks):
        week_start = now - timedelta(weeks=i+1)
        week_end = now - timedelta(weeks=i)
        week_key = week_start.strftime('%Y-%m-%d')
        weekly_counts[week_key] = 0

    for p in permits:
        filing_date = p.get('filing_date', '')
        if not filing_date:
            continue
        try:
            filed = datetime.strptime(filing_date[:10], '%Y-%m-%d')
            weeks_ago = (now - filed).days // 7
            if 0 <= weeks_ago < weeks:
                week_start = now - timedelta(weeks=weeks_ago+1)
                week_key = week_start.strftime('%Y-%m-%d')
                if week_key in weekly_counts:
                    weekly_counts[week_key] += 1
        except (ValueError, TypeError):
            continue

    # Convert to sorted list
    volume_data = sorted(weekly_counts.items())

    # Calculate trend
    if len(volume_data) >= 2:
        recent_avg = sum(v for _, v in volume_data[-4:]) / min(4, len(volume_data))
        older_avg = sum(v for _, v in volume_data[:4]) / min(4, len(volume_data))
        if older_avg > 0:
            trend_pct = ((recent_avg - older_avg) / older_avg) * 100
        else:
            trend_pct = 0
        trend_direction = 'up' if trend_pct > 0 else 'down' if trend_pct < 0 else 'flat'
    else:
        trend_pct = 0
        trend_direction = 'flat'

    return jsonify({
        'volume': [{'week': k, 'count': v} for k, v in volume_data],
        'total': sum(v for _, v in volume_data),
        'trend_percentage': round(trend_pct, 1),
        'trend_direction': trend_direction,
        'city': city or 'All Cities',
    })


@app.route('/api/analytics/trades')
def api_analytics_trades():
    """
    GET /api/analytics/trades
    Query params: city
    Returns trade breakdown for the selected city.
    """
    permits = load_permits()

    city = request.args.get('city', '')
    if city:
        permits = [p for p in permits if p.get('city') == city]

    # Count by trade
    trade_counts = {}
    for p in permits:
        trade = p.get('trade_category', 'Other')
        trade_counts[trade] = trade_counts.get(trade, 0) + 1

    # Sort by count
    trades = sorted(trade_counts.items(), key=lambda x: -x[1])

    return jsonify({
        'trades': [{'trade': t, 'count': c} for t, c in trades],
        'total': len(permits),
        'city': city or 'All Cities',
    })


@app.route('/api/analytics/values')
def api_analytics_values():
    """
    GET /api/analytics/values
    Query params: city, weeks (default 12)
    Returns weekly average project values.
    """
    permits = load_permits()

    city = request.args.get('city', '')
    weeks = int(request.args.get('weeks', 12))

    if city:
        permits = [p for p in permits if p.get('city') == city]

    # Group by week (use module-level timedelta import)
    now = datetime.now()
    weekly_values = {}
    weekly_counts = {}

    for i in range(weeks):
        week_start = now - timedelta(weeks=i+1)
        week_key = week_start.strftime('%Y-%m-%d')
        weekly_values[week_key] = 0
        weekly_counts[week_key] = 0

    for p in permits:
        filing_date = p.get('filing_date', '')
        value = p.get('estimated_cost', 0) or 0
        if not filing_date or value <= 0:
            continue
        try:
            filed = datetime.strptime(filing_date[:10], '%Y-%m-%d')
            weeks_ago = (now - filed).days // 7
            if 0 <= weeks_ago < weeks:
                week_start = now - timedelta(weeks=weeks_ago+1)
                week_key = week_start.strftime('%Y-%m-%d')
                if week_key in weekly_values:
                    weekly_values[week_key] += value
                    weekly_counts[week_key] += 1
        except (ValueError, TypeError):
            continue

    # Calculate averages
    value_data = []
    for week_key in sorted(weekly_values.keys()):
        count = weekly_counts[week_key]
        avg = weekly_values[week_key] / count if count > 0 else 0
        value_data.append({'week': week_key, 'average_value': round(avg, 2), 'count': count})

    # Calculate trend
    recent_values = [d['average_value'] for d in value_data[-4:] if d['average_value'] > 0]
    older_values = [d['average_value'] for d in value_data[:4] if d['average_value'] > 0]

    if recent_values and older_values:
        recent_avg = sum(recent_values) / len(recent_values)
        older_avg = sum(older_values) / len(older_values)
        if older_avg > 0:
            trend_pct = ((recent_avg - older_avg) / older_avg) * 100
        else:
            trend_pct = 0
        trend_direction = 'up' if trend_pct > 0 else 'down' if trend_pct < 0 else 'flat'
    else:
        trend_pct = 0
        trend_direction = 'flat'

    return jsonify({
        'values': value_data,
        'trend_percentage': round(trend_pct, 1),
        'trend_direction': trend_direction,
        'city': city or 'All Cities',
    })


@app.route('/analytics')
def analytics_page():
    """Render the Analytics page (Pro users only)."""
    user = get_current_user()

    # Check if user has Pro plan using centralized utility
    if not is_pro(user):
        footer_cities = get_cities_with_data()
        return render_template('upgrade_gate.html',
            title="Analytics",
            icon="📊",
            heading="Analytics is a Pro Feature",
            description="Upgrade to Professional to access trend analytics, market insights, and contractor intelligence.",
            footer_cities=footer_cities
        )

    footer_cities = get_cities_with_data()
    return render_template('analytics.html', user=user, footer_cities=footer_cities)


# ===========================
# PRE-CONSTRUCTION SIGNALS API
# ===========================

SIGNAL_TYPES = {
    "zoning_application": {"label": "Zoning Application", "color": "purple"},
    "planning_approval": {"label": "Planning Approval", "color": "blue"},
    "variance_request": {"label": "Variance Request", "color": "orange"},
    "demolition_filing": {"label": "Demolition Filing", "color": "red"},
    "new_building_filing": {"label": "New Building Filing", "color": "green"},
    "land_use_review": {"label": "Land Use Review", "color": "purple"},
}


def calculate_lead_potential(signal):
    """Calculate lead potential for a signal."""
    estimated_value = signal.get('estimated_value') or 0
    signal_type = signal.get('signal_type', '')

    if estimated_value >= 500000 or signal_type == 'new_building_filing':
        return 'high'
    elif signal_type in ('zoning_application', 'planning_approval', 'land_use_review'):
        return 'medium'
    else:
        return 'low'


@app.route('/api/signals')
def api_signals():
    """
    GET /api/signals
    Query params: city, type, status, page, per_page
    Returns pre-construction signals.
    """
    signals = load_signals()

    city = request.args.get('city', '')
    signal_type = request.args.get('type', '')
    status = request.args.get('status', '')

    if city:
        signals = [s for s in signals if s.get('city') == city]
    if signal_type:
        signals = [s for s in signals if s.get('signal_type') == signal_type]
    if status:
        signals = [s for s in signals if s.get('status') == status]

    # Add lead potential
    for s in signals:
        s['lead_potential'] = calculate_lead_potential(s)
        s['has_permit'] = len(s.get('linked_permits', [])) > 0

    # Sort by date_filed desc
    signals.sort(key=lambda x: x.get('date_filed', '') or '', reverse=True)

    # Pagination
    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 50))
    total = len(signals)
    start = (page - 1) * per_page
    page_signals = signals[start:start + per_page]

    # Get available cities and types for filters
    all_signals = load_signals()
    cities = sorted(set(s.get('city', '') for s in all_signals if s.get('city')))
    types = sorted(set(s.get('signal_type', '') for s in all_signals if s.get('signal_type')))

    return jsonify({
        'signals': page_signals,
        'total': total,
        'page': page,
        'per_page': per_page,
        'total_pages': (total + per_page - 1) // per_page,
        'cities': cities,
        'types': types,
    })


@app.route('/api/signals/<signal_id>')
def api_signal_detail(signal_id):
    """
    GET /api/signals/<signal_id>
    Returns a single signal with linked permits.
    """
    signals = load_signals()
    signal = next((s for s in signals if s.get('signal_id') == signal_id), None)

    if not signal:
        return jsonify({'error': 'Signal not found'}), 404

    # Add lead potential
    signal['lead_potential'] = calculate_lead_potential(signal)

    # Load linked permits
    linked_permits = []
    if signal.get('linked_permits'):
        all_permits = load_permits()
        all_permits = add_lead_scores(all_permits)
        permit_map = {p.get('permit_number'): p for p in all_permits}
        for permit_id in signal['linked_permits']:
            if permit_id in permit_map:
                linked_permits.append(permit_map[permit_id])

    return jsonify({
        'signal': signal,
        'linked_permits': linked_permits,
    })


@app.route('/api/signals/stats')
def api_signal_stats():
    """
    GET /api/signals/stats
    Query params: city
    Returns signal counts by type and status.
    """
    signals = load_signals()

    city = request.args.get('city', '')
    if city:
        signals = [s for s in signals if s.get('city') == city]

    type_counts = {}
    status_counts = {'pending': 0, 'approved': 0, 'denied': 0, 'withdrawn': 0}
    lead_potential_counts = {'high': 0, 'medium': 0, 'low': 0}
    linked_count = 0

    for s in signals:
        signal_type = s.get('signal_type', 'unknown')
        type_counts[signal_type] = type_counts.get(signal_type, 0) + 1

        status = s.get('status', 'pending')
        if status in status_counts:
            status_counts[status] += 1

        potential = calculate_lead_potential(s)
        lead_potential_counts[potential] += 1

        if s.get('linked_permits'):
            linked_count += 1

    return jsonify({
        'total': len(signals),
        'type_breakdown': type_counts,
        'status_breakdown': status_counts,
        'lead_potential_breakdown': lead_potential_counts,
        'linked_to_permits': linked_count,
        'unlinked': len(signals) - linked_count,
        'city': city or 'All Cities',
    })


@app.route('/api/address-intel/<path:address>')
def api_address_intel(address):
    """
    GET /api/address-intel/<address>
    Returns ALL intelligence for an address: permits, signals, violations, history.
    """
    normalized = normalize_address_for_lookup(address)

    if not normalized:
        return jsonify({'error': 'Address required'}), 400

    # Load all data
    permits = load_permits()
    permits = add_lead_scores(permits)
    signals = load_signals()
    violations = load_violations()
    history = load_permit_history()

    # Find matching permits
    matching_permits = []
    for p in permits:
        p_addr = normalize_address_for_lookup(p.get('address', ''))
        if normalized in p_addr or p_addr in normalized:
            matching_permits.append(p)

    # Find matching signals
    matching_signals = []
    for s in signals:
        s_addr = s.get('address_normalized', '')
        if normalized in s_addr or s_addr in normalized:
            s['lead_potential'] = calculate_lead_potential(s)
            matching_signals.append(s)

    # Find matching violations
    matching_violations = []
    for v in violations:
        v_addr = normalize_address_for_lookup(v.get('address', ''))
        if normalized in v_addr or v_addr in normalized:
            matching_violations.append(v)

    # Find permit history
    history_entry = history.get(normalized, {})
    if not history_entry:
        # Try partial match
        for key, value in history.items():
            if normalized in key or key in normalized:
                history_entry = value
                break

    return jsonify({
        'address': address,
        'address_normalized': normalized,
        'permits': matching_permits,
        'permit_count': len(matching_permits),
        'signals': matching_signals,
        'signal_count': len(matching_signals),
        'violations': matching_violations,
        'violation_count': len(matching_violations),
        'has_active_violations': any(v.get('status', '').lower() in ('open', 'active', 'pending') for v in matching_violations),
        'history': history_entry,
        'historical_permit_count': history_entry.get('permit_count', 0),
        'is_repeat_renovator': history_entry.get('permit_count', 0) >= 3,
    })


@app.route('/early-intel')
def early_intel_page():
    """Render the Early Intel page (Pro users only)."""
    user = get_current_user()

    # Check if user has Pro plan using centralized utility
    if not is_pro(user):
        footer_cities = get_cities_with_data()
        return render_template('upgrade_gate.html',
            title="Early Intel",
            icon="🔮",
            heading="Early Intel is a Pro Feature",
            description="Upgrade to Professional to access pre-construction signals, zoning applications, and early-stage filings before permits are issued.",
            footer_cities=footer_cities
        )

    footer_cities = get_cities_with_data()
    return render_template('early_intel.html', user=user, footer_cities=footer_cities)


# ===========================
# STRIPE PAYMENT ENDPOINTS
# ===========================

# Stripe configuration from environment variables
STRIPE_SECRET_KEY = os.environ.get('STRIPE_SECRET_KEY', '')
STRIPE_PRICE_ID = os.environ.get('STRIPE_PRICE_ID', '')  # Monthly price ID ($149/mo)
# REPLACE WITH ANNUAL STRIPE PRICE ID - Create a new price in Stripe for $1,548/year ($129/mo)
STRIPE_ANNUAL_PRICE_ID = os.environ.get('STRIPE_ANNUAL_PRICE_ID', '')
STRIPE_WEBHOOK_SECRET = os.environ.get('STRIPE_WEBHOOK_SECRET', '')
SITE_URL = os.environ.get('SITE_URL', 'http://localhost:5000')

@app.route('/api/create-checkout-session', methods=['POST'])
def create_checkout_session():
    """Create a Stripe Checkout Session for Professional plan (monthly or annual)."""
    if not STRIPE_SECRET_KEY or not STRIPE_PRICE_ID:
        return jsonify({'error': 'Stripe not configured'}), 500

    stripe.api_key = STRIPE_SECRET_KEY

    data = request.get_json() or {}
    customer_email = data.get('email')
    billing_period = data.get('billing_period', 'monthly')

    # Choose the correct price based on billing period
    if billing_period == 'annual' and STRIPE_ANNUAL_PRICE_ID:
        price_id = STRIPE_ANNUAL_PRICE_ID
        plan_name = 'professional_annual'
    else:
        price_id = STRIPE_PRICE_ID
        plan_name = 'professional_monthly'

    # Track checkout started event
    analytics.track_event('checkout_started', event_data={
        'plan': plan_name,
        'billing': billing_period
    })

    try:
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price': price_id,
                'quantity': 1,
            }],
            mode='subscription',
            success_url=f'{SITE_URL}/?payment=success',
            cancel_url=f'{SITE_URL}/?payment=cancelled',
            customer_email=customer_email,
            metadata={
                'plan': plan_name,
                'billing_period': billing_period,
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

            # Track payment success event
            analytics.track_event('payment_success', event_data={
                'plan': plan,
                'stripe_customer_id': session.get('customer')
            }, user_id_override=customer_email)

    return jsonify({'status': 'success'})


@app.route('/api/webhooks/sendgrid', methods=['POST'])
def sendgrid_webhook():
    """
    Handle SendGrid Event Webhooks for email engagement tracking.
    NOTE: Configure this URL in SendGrid dashboard > Settings > Mail Settings > Event Webhook
    URL: https://permitgrab.com/api/webhooks/sendgrid
    Enable events: Delivered, Opened, Clicked, Bounced, Unsubscribed, Spam Report
    """
    try:
        events = request.get_json()
        if not events or not isinstance(events, list):
            return '', 200

        for event in events:
            sg_type = event.get('event')  # 'delivered', 'open', 'click', 'bounce', etc.
            email = event.get('email', '')

            if not sg_type:
                continue

            # Find user by email (if exists) for user_id
            user_id = None
            if email:
                users = load_users()
                user = next((u for u in users if u.get('email', '').lower() == email.lower()), None)
                if user:
                    user_id = user.get('email')

            # Track the email event
            analytics.track_event(
                event_type=f'email_{sg_type}',  # email_delivered, email_open, email_click, etc.
                event_data={
                    'email': email,
                    'subject': event.get('subject', ''),
                    'url': event.get('url', ''),  # For click events
                    'sg_event_id': event.get('sg_event_id', ''),
                    'sg_message_id': event.get('sg_message_id', ''),
                    'category': event.get('category', []),
                    'reason': event.get('reason', ''),  # For bounce/drop events
                },
                user_id_override=user_id
            )

    except Exception as e:
        print(f"[SendGrid Webhook] Error processing events: {e}")

    return '', 200


# ===========================
# USER AUTHENTICATION
# ===========================

@app.route('/api/register', methods=['POST'])
@limiter.limit("10 per hour")
def api_register():
    """POST /api/register - Register a new user.

    V7: Uses PostgreSQL database with UNIQUE constraint on email.
    Database constraint prevents duplicates even under race conditions.
    """
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Invalid request'}), 400

    email = data.get('email', '').strip().lower()
    password = data.get('password', '')
    name = data.get('name', '').strip()

    if not email or not password:
        return jsonify({'error': 'Email and password required'}), 400

    if len(password) < 8:
        return jsonify({'error': 'Password must be at least 8 characters'}), 400

    # Validate email format (basic check)
    if '@' not in email or '.' not in email.split('@')[-1]:
        return jsonify({'error': 'Please enter a valid email address'}), 400

    # Check for existing account BEFORE creating user
    existing = find_user_by_email(email)
    if existing:
        print(f"[Register] DUPLICATE BLOCKED: {email}")
        return jsonify({'error': 'An account with this email already exists. Please log in instead.'}), 409

    # Create new user in database
    try:
        new_user = User(
            email=email,
            name=name,
            password_hash=generate_password_hash(password),
            plan='free'
        )
        db.session.add(new_user)
        db.session.commit()
        print(f"[Register] User created in database: {email}")
    except IntegrityError:
        # Database UNIQUE constraint caught a race condition
        db.session.rollback()
        print(f"[Register] DUPLICATE BLOCKED (IntegrityError): {email}")
        return jsonify({'error': 'An account with this email already exists. Please log in instead.'}), 409

    # Log in the user
    session['user_email'] = email

    # Track signup event
    analytics.track_event('signup', event_data={'method': 'email'})

    # Return user without password hash
    return jsonify({
        'message': 'Registration successful',
        'user': {
            'email': new_user.email,
            'name': new_user.name,
            'plan': new_user.plan,
        }
    }), 201


@app.route('/api/login', methods=['POST'])
@limiter.limit("20 per minute")
def api_login():
    """POST /api/login - Log in a user (V7: uses PostgreSQL)."""
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Invalid request'}), 400

    email = data.get('email', '').strip().lower()
    password = data.get('password', '')

    if not email or not password:
        return jsonify({'error': 'Email and password required'}), 400

    # Find user in database
    user = find_user_by_email(email)

    if not user:
        print(f"[Login] No user found for email: {email}")
        return jsonify({'error': 'Invalid email or password'}), 401

    if not check_password_hash(user.password_hash, password):
        print(f"[Login] Invalid password for email: {email}")
        return jsonify({'error': 'Invalid email or password'}), 401

    # Log in the user
    session['user_email'] = email

    # Track login event
    analytics.track_event('login')

    return jsonify({
        'message': 'Login successful',
        'user': {
            'email': user.email,
            'name': user.name,
            'plan': user.plan,
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

    # V9 Fix 8: Include daily_alerts and city for alert widget status
    return jsonify({
        'user': {
            'email': user['email'],
            'name': user['name'],
            'plan': user['plan'],
            'daily_alerts': user.get('daily_alerts', False),
            'city': user.get('city', ''),
            'trade': user.get('trade', ''),
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

    # V11 Fix 2.1: Get real user stats from database
    all_users = User.query.all()
    pro_users = User.query.filter(User.plan.in_(['pro', 'professional', 'enterprise'])).all()
    alert_users = User.query.filter_by(daily_alerts=True).all()

    # Count unique cities in permits
    city_count = len(set(p.get('city', '') for p in permits if p.get('city')))

    # V12: Load collection diagnostic
    diag_path = os.path.join(DATA_DIR, 'collection_diagnostic.json')
    diagnostic = {}
    if os.path.exists(diag_path):
        try:
            with open(diag_path) as f:
                diagnostic = json.load(f, strict=False)
        except Exception:
            pass

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
                .form-row { display: flex; gap: 12px; align-items: flex-end; }
                .form-group { display: flex; flex-direction: column; gap: 4px; }
                .form-group label { font-size: 13px; font-weight: 500; color: #374151; }
                .form-group input, .form-group select { padding: 8px 12px; border: 1px solid #d1d5db; border-radius: 6px; font-size: 14px; }
                .btn-upgrade { background: #2563eb; color: white; border: none; padding: 8px 20px; border-radius: 6px; font-weight: 500; cursor: pointer; }
                .btn-upgrade:hover { background: #1d4ed8; }
                .alert { padding: 12px 16px; border-radius: 6px; margin-bottom: 16px; font-size: 14px; }
                .alert-success { background: #dcfce7; color: #166534; }
                .alert-error { background: #fee2e2; color: #991b1b; }
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
                        <div class="value">{{ city_count }}</div>
                        <div class="label">Active Cities</div>
                    </div>
                    <div class="stat-card">
                        <div class="value">{{ total_users }}</div>
                        <div class="label">Registered Users</div>
                    </div>
                    <div class="stat-card">
                        <div class="value">{{ pro_users }}</div>
                        <div class="label">Pro Users</div>
                    </div>
                </div>
                {% if last_updated %}
                <div style="text-align: center; margin-bottom: 16px; padding: 8px; background: #dbeafe; border-radius: 6px; font-size: 14px; color: #1e40af;">
                    Last data collection: {{ last_updated }}
                </div>
                {% endif %}

                {% if success_msg %}
                <div class="alert alert-success">{{ success_msg }}</div>
                {% endif %}
                {% if error_msg %}
                <div class="alert alert-error">{{ error_msg }}</div>
                {% endif %}

                <div class="section">
                    <div class="section-header">Upgrade User</div>
                    <div class="section-body">
                        <form method="POST" action="/admin/upgrade-user" class="form-row">
                            <div class="form-group">
                                <label for="email">Email</label>
                                <input type="email" id="email" name="email" placeholder="user@example.com" required style="width: 280px;">
                            </div>
                            <div class="form-group">
                                <label for="plan">Plan</label>
                                <select id="plan" name="plan" required>
                                    <option value="free">Free</option>
                                    <option value="pro">Pro</option>
                                    <option value="enterprise">Enterprise</option>
                                </select>
                            </div>
                            <button type="submit" class="btn-upgrade">Upgrade</button>
                        </form>
                    </div>
                </div>

                <div class="section">
                    <div class="section-header">Collection Status</div>
                    <div class="section-body">
                        <p><strong>Last Updated:</strong> {{ last_updated or 'Never' }}</p>
                        <p><strong>Total Users:</strong> {{ total_users }}</p>
                        {% if diagnostic %}
                        <hr style="margin: 16px 0; border: none; border-top: 1px solid #e5e7eb;">
                        <h4 style="margin-bottom: 12px; color: #374151;">Collection Diagnostic</h4>
                        <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin-bottom: 16px;">
                            <div style="background: #dcfce7; padding: 12px; border-radius: 6px; text-align: center;">
                                <div style="font-size: 24px; font-weight: 700; color: #16a34a;">{{ diagnostic.cities_with_permits }}</div>
                                <div style="font-size: 12px; color: #166534;">Cities With Permits</div>
                            </div>
                            <div style="background: #fef9c3; padding: 12px; border-radius: 6px; text-align: center;">
                                <div style="font-size: 24px; font-weight: 700; color: #ca8a04;">{{ diagnostic.cities_zero_permits }}</div>
                                <div style="font-size: 12px; color: #854d0e;">Zero Permits</div>
                            </div>
                            <div style="background: #fee2e2; padding: 12px; border-radius: 6px; text-align: center;">
                                <div style="font-size: 24px; font-weight: 700; color: #dc2626;">{{ diagnostic.cities_with_errors }}</div>
                                <div style="font-size: 12px; color: #991b1b;">Errors</div>
                            </div>
                        </div>
                        <p style="font-size: 13px; color: #6b7280;"><strong>Timeouts:</strong> {{ diagnostic.cities_timeout }} | <strong>Connection Errors:</strong> {{ diagnostic.cities_connection_error }}</p>
                        {% endif %}
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
        city_count=city_count,
        total_users=len(all_users),
        pro_users=len(pro_users),
        last_updated=stats.get('collected_at', ''),
        subscribers=subscribers,
        total_subscribers=len(subscribers),
        diagnostic=diagnostic,
        success_msg=request.args.get('success', ''),
        error_msg=request.args.get('error', ''),
    )


# Handle admin logout
@app.before_request
def check_admin_logout():
    if request.path == '/admin' and request.args.get('logout'):
        session.pop('admin_authenticated', None)


@app.route('/api/collection-status')
def api_collection_status():
    """GET /api/collection-status - Check data collection status (admin only)."""
    if not session.get('admin_authenticated'):
        return jsonify({'error': 'Admin authentication required'}), 401

    stats = load_stats()
    permits = load_permits()

    # Check data directory
    data_files = {}
    for filename in ['permits.json', 'collection_stats.json', 'violations.json',
                      'signals.json', 'permit_history.json', 'city_health.json']:
        filepath = os.path.join(DATA_DIR, filename)
        if os.path.exists(filepath):
            data_files[filename] = {
                'exists': True,
                'size_kb': round(os.path.getsize(filepath) / 1024, 1),
                'modified': datetime.fromtimestamp(os.path.getmtime(filepath)).isoformat(),
            }
        else:
            data_files[filename] = {'exists': False}

    # Count unique cities
    city_count = len(set(p.get('city', '') for p in permits if p.get('city')))

    return jsonify({
        'data_dir': DATA_DIR,
        'total_permits': len(permits),
        'unique_cities': city_count,
        'last_collection': stats.get('collected_at', 'Never'),
        'city_stats': stats.get('city_stats', {}),
        'data_files': data_files,
        'collector_started': _collector_started,
    })


@app.route('/admin/trigger-collection', methods=['POST'])
def admin_trigger_collection():
    """V12.2: Manually trigger data collection (admin only)."""
    if not session.get('admin_authenticated'):
        return jsonify({"error": "Unauthorized"}), 403

    import threading
    from collector import collect_all

    # Run in background thread so it doesn't block
    thread = threading.Thread(target=collect_all, kwargs={"days_back": 60}, daemon=True)
    thread.start()

    return jsonify({
        "status": "Collection triggered",
        "message": "Running in background. Check /api/stats in a few minutes."
    })


@app.route('/admin/upgrade-user', methods=['POST'])
def admin_upgrade_user():
    """POST /admin/upgrade-user - Upgrade a user's subscription plan."""
    # Check admin authentication
    if not session.get('admin_authenticated'):
        return jsonify({'error': 'Unauthorized'}), 401

    email = request.form.get('email', '').strip().lower()
    plan = request.form.get('plan', 'free').strip().lower()

    if not email:
        return redirect('/admin?error=Email+is+required')

    if plan not in ('free', 'pro', 'enterprise'):
        return redirect('/admin?error=Invalid+plan')

    # Update user (V7: direct database update)
    user_obj = find_user_by_email(email)
    user_found = user_obj is not None
    if user_obj:
        user_obj.plan = plan
        db.session.commit()

    # Also update subscriber if exists
    subs = load_subscribers()
    sub_found = False
    for sub in subs:
        if sub.get('email', '').lower() == email:
            sub['plan'] = plan
            sub['upgraded_at'] = datetime.now().isoformat()
            sub['upgraded_by'] = 'admin'
            sub_found = True
            break

    if sub_found:
        save_subscribers(subs)

    if user_found or sub_found:
        return redirect(f'/admin?success=Upgraded+{email}+to+{plan}')
    else:
        return redirect(f'/admin?error=User+{email}+not+found')


# ===========================
# ADMIN ANALYTICS DASHBOARD
# ===========================

# Admin emails list - add emails that should have admin access
ADMIN_EMAILS = os.environ.get('ADMIN_EMAILS', 'wcrainshaw@gmail.com').lower().split(',')

@app.route('/admin/analytics')
def admin_analytics_page():
    """Admin analytics dashboard."""
    # Check admin authentication
    user = get_current_user()
    if not user or user.get('email', '').lower() not in ADMIN_EMAILS:
        if not session.get('admin_authenticated'):
            return "Unauthorized - Admin access required", 403

    # Gather all analytics data
    data = {
        'visitors_today': analytics.get_visitors_today(),
        'signups_week': analytics.get_signups_this_week(),
        'active_users_7d': analytics.get_active_users_7d(),
        'trial_starts_30d': analytics.get_trial_starts_30d(),
        'daily_traffic': analytics.get_daily_traffic(30),
        'top_pages': analytics.get_top_pages(7, 20),
        'funnel': analytics.get_conversion_funnel(30),
        'event_counts': analytics.get_event_counts(7),
        'city_engagement': analytics.get_city_engagement(30),
        'traffic_sources': analytics.get_traffic_sources(30),
        'health_status': analytics.get_latest_health_status(),
        'health_failures': analytics.get_health_failures_recent(20),
        'city_health': analytics.get_city_health_summary(),
        'route_health': analytics.get_route_health_summary(),
        'service_health': analytics.get_service_health_status(),
        'email_perf_7d': analytics.get_email_performance(7),
        'email_perf_30d': analytics.get_email_performance(30),
    }

    return render_template('admin_analytics.html', data=data)


# ===========================
# MY LEADS CRM PAGE
# ===========================

@app.route('/my-leads')
def my_leads_page():
    """Render the My Leads CRM page."""
    user = get_current_user()
    if not user:
        # Redirect to login with message
        return redirect('/login?redirect=my-leads')

    footer_cities = get_cities_with_data()
    return render_template('my_leads.html', user=user, footer_cities=footer_cities)


# ===========================
# SAVED SEARCHES PAGE
# ===========================

@app.route('/saved-searches')
def saved_searches_page():
    """Render the Saved Searches page."""
    user = get_current_user()
    if not user:
        return redirect('/login?redirect=saved-searches')

    searches = get_user_saved_searches(user['email'])
    footer_cities = get_cities_with_data()
    return render_template('saved_searches.html', user=user, searches=searches, footer_cities=footer_cities)


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
    "cambridge": {
        "name": "Cambridge",
        "state": "MA",
        "meta_title": "Cambridge Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Cambridge, MA. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Cambridge, home to Harvard and MIT, has a thriving construction market driven by academic institutions, biotech companies, and residential demand. Cambridge building permits span laboratory construction, commercial office space, and renovations to the city's historic housing stock.</p>
            <p>The Cambridge construction industry benefits from the city's density and ongoing development around Kendall Square and Central Square. Cambridge construction permits reflect strong demand for HVAC, electrical, and plumbing work in both commercial and residential sectors.</p>
            <p>For contractors seeking Cambridge building permits, timing is key in this competitive market. PermitGrab delivers fresh Cambridge permit data daily, helping you connect with project owners across Middlesex County.</p>
        """
    },
    "washington-dc": {
        "name": "Washington DC",
        "state": "DC",
        "meta_title": "Washington DC Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Washington DC. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Washington DC's construction market is driven by government buildings, commercial development, and a dense residential market. DC building permits cover everything from federal facility renovations to row house restorations in Capitol Hill, Georgetown, and Adams Morgan.</p>
            <p>The DC construction industry benefits from constant government investment and the city's historic preservation requirements. Washington DC construction permits reflect strong demand for structural work, window replacements, and interior renovations in the city's iconic architecture.</p>
            <p>For contractors seeking DC building permits, quick access to new filings means getting ahead of the competition. PermitGrab delivers fresh Washington DC permit data daily, helping you win contracts across the District.</p>
        """
    },
    "san-antonio": {
        "name": "San Antonio",
        "state": "TX",
        "meta_title": "San Antonio Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in San Antonio. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>San Antonio is one of the fastest-growing cities in Texas, with a booming construction market across residential and commercial sectors. San Antonio building permits cover new home construction, commercial development along the I-35 corridor, and renovations throughout Bexar County.</p>
            <p>The San Antonio construction industry benefits from the city's affordable land and strong population growth. San Antonio construction permits reflect high demand for HVAC in the Texas heat, roofing projects, and general construction work.</p>
            <p>For contractors seeking San Antonio building permits, early access to new filings is essential. PermitGrab delivers fresh San Antonio permit data daily, helping you connect with property owners across the Alamo City.</p>
        """
    },
    "kansas-city": {
        "name": "Kansas City",
        "state": "MO",
        "meta_title": "Kansas City Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Kansas City. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Kansas City's construction market spans both Missouri and Kansas, with major development in downtown KC, the Plaza, and surrounding suburbs. Kansas City building permits cover commercial construction, residential development, and renovations across the metro area.</p>
            <p>The KC construction industry benefits from the region's central location and ongoing revitalization efforts. Kansas City construction permits reflect demand across all trades, from HVAC and electrical to general construction and roofing.</p>
            <p>For contractors seeking Kansas City building permits, quick access to permit data helps you beat the competition. PermitGrab delivers fresh KC permit data daily, helping you find quality leads across the metro.</p>
        """
    },
    "detroit": {
        "name": "Detroit",
        "state": "MI",
        "meta_title": "Detroit Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Detroit. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Detroit's construction market is experiencing a renaissance, with major investments in downtown development and neighborhood revitalization. Detroit building permits cover commercial construction in the central business district, residential renovations across the city's historic neighborhoods, and industrial development.</p>
            <p>The Detroit construction industry benefits from the city's comeback story—historic buildings being restored, new developments rising, and a growing population demanding quality contractors. Detroit construction permits reflect strong demand for renovation work, electrical upgrades, and HVAC installations.</p>
            <p>For contractors seeking Detroit building permits, early access to new filings is crucial. PermitGrab delivers fresh Detroit permit data daily, helping you win jobs across the Motor City.</p>
        """
    },
    "pittsburgh": {
        "name": "Pittsburgh",
        "state": "PA",
        "meta_title": "Pittsburgh Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Pittsburgh. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Pittsburgh's construction market is thriving, driven by tech industry growth, healthcare development, and residential demand. Pittsburgh building permits cover commercial construction downtown, university expansions, and renovations in neighborhoods like Shadyside, Lawrenceville, and the South Side.</p>
            <p>The Pittsburgh construction industry benefits from the city's transformation from industrial powerhouse to tech hub. Pittsburgh construction permits reflect strong demand for HVAC, electrical, and renovation work in both commercial and residential sectors.</p>
            <p>For contractors seeking Pittsburgh building permits, quick access to new filings helps you connect with project owners first. PermitGrab delivers fresh Pittsburgh permit data daily across Allegheny County.</p>
        """
    },
    "denver": {
        "name": "Denver",
        "state": "CO",
        "meta_title": "Denver Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Denver. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Denver's construction market is one of the hottest in the nation, with explosive growth in both residential and commercial development. Denver building permits cover high-rise construction downtown, residential development across the metro, and renovations throughout the Front Range.</p>
            <p>The Denver construction industry benefits from the city's population boom and strong economy. Denver construction permits reflect high demand for all trades—HVAC, electrical, plumbing, roofing, and general construction work are all in constant demand.</p>
            <p>For contractors seeking Denver building permits, timing is everything in this competitive market. PermitGrab delivers fresh Denver permit data daily, helping you win contracts across the Mile High City.</p>
        """
    },
    "portland": {
        "name": "Portland",
        "state": "OR",
        "meta_title": "Portland Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Portland, OR. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Portland's construction market reflects the city's commitment to sustainability and urban density. Portland building permits cover green building projects, residential development, ADU construction, and commercial renovations throughout Multnomah County.</p>
            <p>The Portland construction industry benefits from the city's unique building codes and environmental focus. Portland construction permits reflect strong demand for energy-efficient upgrades, solar installations, and sustainable building practices.</p>
            <p>For contractors seeking Portland building permits, early access to new filings helps you connect with eco-conscious project owners. PermitGrab delivers fresh Portland permit data daily, helping you win jobs across the Rose City.</p>
        """
    },
    "miami": {
        "name": "Miami-Dade County",
        "state": "FL",
        "meta_title": "Miami Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Miami-Dade County. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Miami's construction market is among the most active in the nation, with constant development across residential, commercial, and hospitality sectors. Miami building permits cover high-rise condo construction, luxury home development, and renovations throughout Miami-Dade County.</p>
            <p>The Miami construction industry benefits from the region's year-round building season and strong demand from domestic and international buyers. Miami construction permits reflect high demand for hurricane-resistant construction, HVAC work in the tropical climate, and pool construction.</p>
            <p>For contractors seeking Miami building permits, quick access to new filings is essential in this competitive market. PermitGrab delivers fresh Miami permit data daily, helping you win contracts across South Florida.</p>
        """
    },
    "raleigh": {
        "name": "Raleigh",
        "state": "NC",
        "meta_title": "Raleigh Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Raleigh, NC. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Raleigh's construction market is booming as part of the Research Triangle's explosive growth. Raleigh building permits cover residential development, commercial construction, and tech campus expansions throughout Wake County.</p>
            <p>The Raleigh construction industry benefits from the region's strong job growth and influx of new residents. Raleigh construction permits reflect high demand for new home construction, HVAC installations, and commercial build-outs.</p>
            <p>For contractors seeking Raleigh building permits, early access to permit data helps you stay ahead of the competition. PermitGrab delivers fresh Raleigh permit data daily, helping you win jobs across the Triangle.</p>
        """
    },
    "phoenix": {
        "name": "Phoenix",
        "state": "AZ",
        "meta_title": "Phoenix Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Phoenix. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Phoenix's construction market is one of the largest in the Southwest, with constant development across the Valley of the Sun. Phoenix building permits cover new home construction, commercial development, and renovations throughout Maricopa County.</p>
            <p>The Phoenix construction industry benefits from year-round building weather and strong population growth. Phoenix construction permits reflect high demand for HVAC in the desert heat, pool construction, and solar installations.</p>
            <p>For contractors seeking Phoenix building permits, quick access to new filings is crucial. PermitGrab delivers fresh Phoenix permit data daily, helping you win contracts across the Valley.</p>
        """
    },
    "san-jose": {
        "name": "San Jose",
        "state": "CA",
        "meta_title": "San Jose Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in San Jose. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>San Jose's construction market is driven by Silicon Valley's tech industry and strong housing demand. San Jose building permits cover tech campus construction, residential development, and ADU projects throughout Santa Clara County.</p>
            <p>The San Jose construction industry benefits from the region's high property values and constant development pressure. San Jose construction permits reflect strong demand for electrical work, seismic retrofitting, and energy-efficient upgrades.</p>
            <p>For contractors seeking San Jose building permits, timing is key in this premium market. PermitGrab delivers fresh San Jose permit data daily, helping you connect with project owners across the South Bay.</p>
        """
    },
    "san-diego": {
        "name": "San Diego",
        "state": "CA",
        "meta_title": "San Diego Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in San Diego. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>San Diego's construction market benefits from year-round building weather and strong residential demand. San Diego building permits cover new home construction, ADU development, and commercial projects throughout San Diego County.</p>
            <p>The San Diego construction industry reflects the region's military presence, biotech sector, and tourism industry. San Diego construction permits show strong demand for HVAC, solar installations, and pool construction.</p>
            <p>For contractors seeking San Diego building permits, early access to permit data helps you win more jobs. PermitGrab delivers fresh San Diego permit data daily, helping you grow your business across America's Finest City.</p>
        """
    },
    "sacramento": {
        "name": "Sacramento",
        "state": "CA",
        "meta_title": "Sacramento Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Sacramento. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Sacramento's construction market is thriving as California's capital attracts new residents and businesses. Sacramento building permits cover new home construction, commercial development, and renovations throughout the Sacramento Valley.</p>
            <p>The Sacramento construction industry benefits from the region's more affordable land compared to the Bay Area. Sacramento construction permits reflect strong demand for HVAC in the hot summers, roofing, and residential construction.</p>
            <p>For contractors seeking Sacramento building permits, quick access to new filings helps you compete effectively. PermitGrab delivers fresh Sacramento permit data daily, helping you win contracts across the region.</p>
        """
    },
    "boston": {
        "name": "Boston",
        "state": "MA",
        "meta_title": "Boston Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Boston. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Boston's construction market is driven by world-class universities, healthcare institutions, and a dense residential market. Boston building permits cover commercial development in the Seaport, residential renovations in historic neighborhoods, and institutional construction throughout Greater Boston.</p>
            <p>The Boston construction industry benefits from the region's strong economy and aging housing stock requiring constant maintenance. Boston construction permits reflect high demand for HVAC, electrical upgrades, and renovation work in the city's historic buildings.</p>
            <p>For contractors seeking Boston building permits, early access to permit data is essential. PermitGrab delivers fresh Boston permit data daily, helping you win jobs across the Greater Boston area.</p>
        """
    },
    "philadelphia": {
        "name": "Philadelphia",
        "state": "PA",
        "meta_title": "Philadelphia Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Philadelphia. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Philadelphia's construction market is experiencing strong growth, with major development downtown and in surrounding neighborhoods. Philadelphia building permits cover commercial construction, residential renovations in historic rowhomes, and new development across the city.</p>
            <p>The Philadelphia construction industry benefits from the city's affordability relative to NYC and DC. Philly construction permits reflect strong demand for HVAC, electrical work, and renovations in the city's historic housing stock.</p>
            <p>For contractors seeking Philadelphia building permits, quick access to new filings helps you stay competitive. PermitGrab delivers fresh Philly permit data daily, helping you win contracts across the City of Brotherly Love.</p>
        """
    },
    "baltimore": {
        "name": "Baltimore",
        "state": "MD",
        "meta_title": "Baltimore Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Baltimore. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Baltimore's construction market is driven by waterfront development, historic preservation, and neighborhood revitalization. Baltimore building permits cover commercial projects in the Inner Harbor, residential renovations in rowhouse neighborhoods, and institutional construction.</p>
            <p>The Baltimore construction industry benefits from major redevelopment initiatives and proximity to Washington DC. Baltimore construction permits reflect strong demand for renovation work, HVAC upgrades, and historic preservation projects.</p>
            <p>For contractors seeking Baltimore building permits, quick access to new filings is essential. PermitGrab delivers fresh Baltimore permit data daily, helping you win contracts across Charm City.</p>
        """
    },
    "charlotte": {
        "name": "Charlotte",
        "state": "NC",
        "meta_title": "Charlotte Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Charlotte. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Charlotte's construction market is booming as one of the fastest-growing cities in the Southeast. Charlotte building permits cover high-rise construction uptown, residential development across Mecklenburg County, and commercial projects throughout the metro.</p>
            <p>The Charlotte construction industry benefits from strong population growth and corporate relocations. Charlotte construction permits reflect high demand for new home construction, commercial build-outs, and HVAC installations.</p>
            <p>For contractors seeking Charlotte building permits, early access to permit data helps you stay competitive. PermitGrab delivers fresh Charlotte permit data daily, helping you win jobs across the Queen City.</p>
        """
    },
    "columbus": {
        "name": "Columbus",
        "state": "OH",
        "meta_title": "Columbus Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Columbus, OH. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Columbus is Ohio's fastest-growing city with a thriving construction market. Columbus building permits cover commercial development downtown, residential growth in suburbs like Dublin and Westerville, and university-related construction around Ohio State.</p>
            <p>The Columbus construction industry benefits from a diverse economy and strong housing demand. Columbus construction permits reflect steady demand for new construction, renovations, and commercial projects.</p>
            <p>For contractors seeking Columbus building permits, quick access to new filings helps you win more jobs. PermitGrab delivers fresh Columbus permit data daily across Franklin County.</p>
        """
    },
    "fort-worth": {
        "name": "Fort Worth",
        "state": "TX",
        "meta_title": "Fort Worth Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Fort Worth. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Fort Worth's construction market is thriving as part of the Dallas-Fort Worth metroplex's explosive growth. Fort Worth building permits cover residential development, commercial construction, and industrial projects across Tarrant County.</p>
            <p>The Fort Worth construction industry benefits from Texas's business-friendly environment and strong population growth. Fort Worth construction permits reflect high demand for HVAC, roofing, and general construction work.</p>
            <p>For contractors seeking Fort Worth building permits, early access to new filings is key. PermitGrab delivers fresh Fort Worth permit data daily, helping you compete across the DFW metroplex.</p>
        """
    },
    "honolulu": {
        "name": "Honolulu",
        "state": "HI",
        "meta_title": "Honolulu Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Honolulu. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Honolulu's construction market serves Hawaii's most populous island with residential, commercial, and hospitality projects. Honolulu building permits cover high-rise condos, resort renovations, and residential construction across Oahu.</p>
            <p>The Honolulu construction industry is unique with island-specific challenges and opportunities. Honolulu construction permits reflect steady demand for renovation work, HVAC installations, and new development.</p>
            <p>For contractors seeking Honolulu building permits, quick access to new filings is valuable. PermitGrab delivers fresh Honolulu permit data daily, helping you win contracts across the island.</p>
        """
    },
    "indianapolis": {
        "name": "Indianapolis",
        "state": "IN",
        "meta_title": "Indianapolis Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Indianapolis. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Indianapolis has a strong construction market driven by downtown development and suburban growth. Indianapolis building permits cover commercial construction, residential development across Marion County, and industrial projects.</p>
            <p>The Indianapolis construction industry benefits from the city's central location and affordable market. Indy construction permits reflect steady demand for all trades from HVAC to general construction.</p>
            <p>For contractors seeking Indianapolis building permits, early access to permit data is essential. PermitGrab delivers fresh Indy permit data daily, helping you grow your business across the Circle City.</p>
        """
    },
    "louisville": {
        "name": "Louisville",
        "state": "KY",
        "meta_title": "Louisville Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Louisville. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Louisville's construction market spans residential, commercial, and industrial development across Jefferson County. Louisville building permits cover downtown revitalization, residential growth in the Highlands and East End, and commercial construction.</p>
            <p>The Louisville construction industry benefits from the city's strategic location and growing economy. Louisville construction permits reflect demand for renovation work, new construction, and commercial build-outs.</p>
            <p>For contractors seeking Louisville building permits, quick access to new filings helps you compete. PermitGrab delivers fresh Louisville permit data daily across Kentucky's largest city.</p>
        """
    },
    "memphis": {
        "name": "Memphis",
        "state": "TN",
        "meta_title": "Memphis Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Memphis. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Memphis has a diverse construction market with residential, commercial, and industrial projects. Memphis building permits cover downtown development, residential renovations, and logistics/warehouse construction due to FedEx's headquarters.</p>
            <p>The Memphis construction industry benefits from the city's role as a logistics hub and growing economy. Memphis construction permits reflect steady demand for all construction trades.</p>
            <p>For contractors seeking Memphis building permits, early access to permit data is valuable. PermitGrab delivers fresh Memphis permit data daily, helping you win contracts across the Bluff City.</p>
        """
    },
    "mesa": {
        "name": "Mesa",
        "state": "AZ",
        "meta_title": "Mesa Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Mesa, AZ. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Mesa is Arizona's third-largest city with a thriving construction market. Mesa building permits cover residential development, commercial construction, and renovation projects across the East Valley.</p>
            <p>The Mesa construction industry benefits from Phoenix metro growth and year-round building weather. Mesa construction permits reflect high demand for HVAC, pool construction, and residential work.</p>
            <p>For contractors seeking Mesa building permits, quick access to new filings is essential. PermitGrab delivers fresh Mesa permit data daily, helping you compete across the Valley.</p>
        """
    },
    "milwaukee": {
        "name": "Milwaukee",
        "state": "WI",
        "meta_title": "Milwaukee Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Milwaukee. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Milwaukee's construction market includes residential, commercial, and industrial projects across the metro. Milwaukee building permits cover downtown development, residential renovations in historic neighborhoods, and commercial construction.</p>
            <p>The Milwaukee construction industry benefits from the city's manufacturing heritage and revitalization efforts. Milwaukee construction permits reflect strong demand for renovation work, HVAC, and general construction.</p>
            <p>For contractors seeking Milwaukee building permits, early access to permit data helps you compete. PermitGrab delivers fresh Milwaukee permit data daily across Wisconsin's largest city.</p>
        """
    },
    "oakland": {
        "name": "Oakland",
        "state": "CA",
        "meta_title": "Oakland Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Oakland. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Oakland's construction market is thriving with development across the East Bay. Oakland building permits cover high-density residential projects, commercial development, and renovations in neighborhoods from Rockridge to Jack London Square.</p>
            <p>The Oakland construction industry benefits from San Francisco Bay Area growth and relative affordability. Oakland construction permits reflect strong demand for residential construction and seismic retrofitting.</p>
            <p>For contractors seeking Oakland building permits, quick access to new filings is valuable. PermitGrab delivers fresh Oakland permit data daily across Alameda County.</p>
        """
    },
    "oklahoma-city": {
        "name": "Oklahoma City",
        "state": "OK",
        "meta_title": "Oklahoma City Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Oklahoma City. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Oklahoma City has a strong construction market driven by energy industry investment and population growth. OKC building permits cover commercial development downtown, residential growth in suburbs like Edmond, and industrial construction.</p>
            <p>The Oklahoma City construction industry benefits from affordable land and business-friendly policies. OKC construction permits reflect steady demand for all construction trades.</p>
            <p>For contractors seeking Oklahoma City building permits, early access to permit data is key. PermitGrab delivers fresh OKC permit data daily across the metro area.</p>
        """
    },
    "omaha": {
        "name": "Omaha",
        "state": "NE",
        "meta_title": "Omaha Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Omaha. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Omaha's construction market serves Nebraska's largest city with residential, commercial, and industrial projects. Omaha building permits cover downtown development, suburban residential growth, and commercial construction throughout Douglas County.</p>
            <p>The Omaha construction industry benefits from a stable economy and steady population growth. Omaha construction permits reflect consistent demand for new construction and renovation work.</p>
            <p>For contractors seeking Omaha building permits, quick access to new filings helps you compete. PermitGrab delivers fresh Omaha permit data daily across the metro.</p>
        """
    },
    "st-louis": {
        "name": "St. Louis",
        "state": "MO",
        "meta_title": "St. Louis Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in St. Louis. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>St. Louis has a diverse construction market with residential, commercial, and institutional projects. St. Louis building permits cover downtown revitalization, residential renovations in historic neighborhoods, and commercial development.</p>
            <p>The St. Louis construction industry benefits from the city's affordability and architectural heritage. STL construction permits reflect strong demand for renovation work, HVAC, and general construction.</p>
            <p>For contractors seeking St. Louis building permits, early access to permit data is valuable. PermitGrab delivers fresh St. Louis permit data daily across the Gateway City.</p>
        """
    },
    "tucson": {
        "name": "Tucson",
        "state": "AZ",
        "meta_title": "Tucson Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Tucson. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Tucson's construction market serves Arizona's second-largest city with residential, commercial, and university-related projects. Tucson building permits cover new home construction, commercial development, and renovation work across Pima County.</p>
            <p>The Tucson construction industry benefits from year-round building weather and steady growth. Tucson construction permits reflect high demand for HVAC, pool construction, and solar installations.</p>
            <p>For contractors seeking Tucson building permits, quick access to new filings is essential. PermitGrab delivers fresh Tucson permit data daily across Southern Arizona.</p>
        """
    },
    "long-beach": {
        "name": "Long Beach",
        "state": "CA",
        "meta_title": "Long Beach Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Long Beach. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Long Beach has a thriving construction market as LA County's second-largest city. Long Beach building permits cover waterfront development, residential renovations, and commercial construction throughout the port city.</p>
            <p>The Long Beach construction industry benefits from port-related development and residential demand. Long Beach construction permits reflect steady work for all trades.</p>
            <p>For contractors seeking Long Beach building permits, early access to permit data helps you compete. PermitGrab delivers fresh Long Beach permit data daily.</p>
        """
    },
    "fresno": {
        "name": "Fresno",
        "state": "CA",
        "meta_title": "Fresno Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Fresno. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Fresno's construction market serves California's Central Valley with residential, commercial, and agricultural projects. Fresno building permits cover new home construction, commercial development, and renovation work throughout Fresno County.</p>
            <p>The Fresno construction industry benefits from affordable land and California's housing demand. Fresno construction permits reflect steady work across all construction trades.</p>
            <p>For contractors seeking Fresno building permits, quick access to new filings is valuable. PermitGrab delivers fresh Fresno permit data daily across the Valley.</p>
        """
    },
    "las-vegas": {
        "name": "Las Vegas",
        "state": "NV",
        "meta_title": "Las Vegas Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Las Vegas. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Las Vegas has one of the most dynamic construction markets in the country. Las Vegas building permits cover casino and resort development, residential construction in rapidly growing suburbs, and commercial projects across Clark County.</p>
            <p>The Las Vegas construction industry benefits from constant tourism investment and population growth. Vegas construction permits reflect high demand for HVAC, pool construction, and commercial build-outs.</p>
            <p>For contractors seeking Las Vegas building permits, early access to permit data is essential. PermitGrab delivers fresh Las Vegas permit data daily, helping you win contracts across the Valley.</p>
        """
    },
    "orlando": {
        "name": "Orlando",
        "state": "FL",
        "meta_title": "Orlando Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Orlando. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Orlando's construction market is driven by tourism, population growth, and theme park expansion. Orlando building permits cover hospitality projects, residential development across Orange County, and commercial construction throughout Central Florida.</p>
            <p>The Orlando construction industry benefits from year-round building weather and strong economic growth. Orlando construction permits reflect high demand for all construction trades.</p>
            <p>For contractors seeking Orlando building permits, quick access to new filings helps you compete. PermitGrab delivers fresh Orlando permit data daily across the I-4 corridor.</p>
        """
    },
    "tampa": {
        "name": "Tampa",
        "state": "FL",
        "meta_title": "Tampa Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Tampa. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Tampa's construction market is thriving with waterfront development, residential growth, and commercial projects. Tampa building permits cover downtown high-rises, residential construction across Hillsborough County, and commercial development.</p>
            <p>The Tampa construction industry benefits from Florida's growth and year-round building weather. Tampa construction permits reflect high demand for HVAC, hurricane-resistant construction, and pool work.</p>
            <p>For contractors seeking Tampa building permits, early access to permit data is key. PermitGrab delivers fresh Tampa permit data daily across the Tampa Bay area.</p>
        """
    },
    "jacksonville": {
        "name": "Jacksonville",
        "state": "FL",
        "meta_title": "Jacksonville Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Jacksonville. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Jacksonville is Florida's largest city by area with a diverse construction market. Jacksonville building permits cover residential development, commercial construction, and port-related projects across Duval County.</p>
            <p>The Jacksonville construction industry benefits from affordable land and Florida's population growth. Jax construction permits reflect steady demand for all construction trades.</p>
            <p>For contractors seeking Jacksonville building permits, quick access to new filings is valuable. PermitGrab delivers fresh Jacksonville permit data daily across Northeast Florida.</p>
        """
    },
    "virginia-beach": {
        "name": "Virginia Beach",
        "state": "VA",
        "meta_title": "Virginia Beach Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Virginia Beach. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Virginia Beach has a strong construction market serving the Hampton Roads region. Virginia Beach building permits cover residential development, commercial construction, and military-related projects.</p>
            <p>The Virginia Beach construction industry benefits from military investment and tourism. VB construction permits reflect steady demand for renovation work and new construction.</p>
            <p>For contractors seeking Virginia Beach building permits, early access to permit data helps you compete. PermitGrab delivers fresh Virginia Beach permit data daily.</p>
        """
    },
    "albuquerque": {
        "name": "Albuquerque",
        "state": "NM",
        "meta_title": "Albuquerque Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Albuquerque. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Albuquerque's construction market serves New Mexico's largest city with residential, commercial, and institutional projects. Albuquerque building permits cover new home construction, commercial development, and renovation work across Bernalillo County.</p>
            <p>The Albuquerque construction industry benefits from film industry growth and steady population. ABQ construction permits reflect demand for HVAC, solar, and general construction work.</p>
            <p>For contractors seeking Albuquerque building permits, quick access to new filings is valuable. PermitGrab delivers fresh Albuquerque permit data daily.</p>
        """
    },
    "cleveland": {
        "name": "Cleveland",
        "state": "OH",
        "meta_title": "Cleveland Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Cleveland. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Cleveland's construction market includes downtown revitalization, residential renovations, and healthcare/university projects. Cleveland building permits cover commercial construction, residential work in neighborhoods like Ohio City and Tremont, and institutional development.</p>
            <p>The Cleveland construction industry benefits from major hospital systems and ongoing revitalization. Cleveland construction permits reflect strong demand for renovation work and HVAC.</p>
            <p>For contractors seeking Cleveland building permits, early access to permit data is essential. PermitGrab delivers fresh Cleveland permit data daily across Cuyahoga County.</p>
        """
    },
}

# Dynamic city list from city_configs.py
def get_all_cities_list():
    """Get all active cities for navigation."""
    return [{"slug": c["slug"], "name": c["name"]} for c in get_all_cities_info()]

ALL_CITIES = get_all_cities_list()

# V12.23 SEO: State hub pages
# V12.28 MEGA EXPANSION: Added 28 new states (38 total with 3+ cities)
STATE_CONFIG = {
    'texas': {'name': 'Texas', 'abbrev': 'TX'},
    'california': {'name': 'California', 'abbrev': 'CA'},
    'maryland': {'name': 'Maryland', 'abbrev': 'MD'},
    'colorado': {'name': 'Colorado', 'abbrev': 'CO'},
    'florida': {'name': 'Florida', 'abbrev': 'FL'},
    'louisiana': {'name': 'Louisiana', 'abbrev': 'LA'},
    'new-york': {'name': 'New York', 'abbrev': 'NY'},
    'illinois': {'name': 'Illinois', 'abbrev': 'IL'},
    'ohio': {'name': 'Ohio', 'abbrev': 'OH'},
    'washington': {'name': 'Washington', 'abbrev': 'WA'},
    # V12.28 New state hubs
    'arizona': {'name': 'Arizona', 'abbrev': 'AZ'},
    'massachusetts': {'name': 'Massachusetts', 'abbrev': 'MA'},
    'new-jersey': {'name': 'New Jersey', 'abbrev': 'NJ'},
    'north-carolina': {'name': 'North Carolina', 'abbrev': 'NC'},
    'virginia': {'name': 'Virginia', 'abbrev': 'VA'},
    'connecticut': {'name': 'Connecticut', 'abbrev': 'CT'},
    'utah': {'name': 'Utah', 'abbrev': 'UT'},
    'wisconsin': {'name': 'Wisconsin', 'abbrev': 'WI'},
    'nevada': {'name': 'Nevada', 'abbrev': 'NV'},
    'iowa': {'name': 'Iowa', 'abbrev': 'IA'},
    'missouri': {'name': 'Missouri', 'abbrev': 'MO'},
    'pennsylvania': {'name': 'Pennsylvania', 'abbrev': 'PA'},
    'georgia': {'name': 'Georgia', 'abbrev': 'GA'},
    'indiana': {'name': 'Indiana', 'abbrev': 'IN'},
    'south-carolina': {'name': 'South Carolina', 'abbrev': 'SC'},
    'idaho': {'name': 'Idaho', 'abbrev': 'ID'},
    'michigan': {'name': 'Michigan', 'abbrev': 'MI'},
    'tennessee': {'name': 'Tennessee', 'abbrev': 'TN'},
    'nebraska': {'name': 'Nebraska', 'abbrev': 'NE'},
    'new-mexico': {'name': 'New Mexico', 'abbrev': 'NM'},
    'alabama': {'name': 'Alabama', 'abbrev': 'AL'},
    'kansas': {'name': 'Kansas', 'abbrev': 'KS'},
    'rhode-island': {'name': 'Rhode Island', 'abbrev': 'RI'},
    'minnesota': {'name': 'Minnesota', 'abbrev': 'MN'},
    'oregon': {'name': 'Oregon', 'abbrev': 'OR'},
    'kentucky': {'name': 'Kentucky', 'abbrev': 'KY'},
    'oklahoma': {'name': 'Oklahoma', 'abbrev': 'OK'},
    'mississippi': {'name': 'Mississippi', 'abbrev': 'MS'},
}


def get_state_data(state_slug):
    """Get aggregated data for a state hub page."""
    if state_slug not in STATE_CONFIG:
        return None

    state_info = STATE_CONFIG[state_slug]
    state_abbrev = state_info['abbrev']
    permits = load_permits()

    # Get all cities in this state
    all_cities_info = get_all_cities_info()
    state_cities = [c for c in all_cities_info if c.get('state') == state_abbrev]

    # Count permits per city
    city_permit_counts = {}
    city_values = {}
    for p in permits:
        city_name = p.get('city', '')
        if p.get('state') == state_abbrev and city_name:
            city_permit_counts[city_name] = city_permit_counts.get(city_name, 0) + 1
            city_values[city_name] = city_values.get(city_name, 0) + (p.get('estimated_cost', 0) or 0)

    # Add counts to city info
    cities_with_data = []
    for c in state_cities:
        city_data = c.copy()
        city_data['permit_count'] = city_permit_counts.get(c['name'], 0)
        city_data['total_value'] = city_values.get(c['name'], 0)
        if city_data['permit_count'] > 0:
            cities_with_data.append(city_data)

    # Sort by permit count
    cities_with_data.sort(key=lambda x: x['permit_count'], reverse=True)

    # Calculate totals
    total_permits = sum(city_permit_counts.values())
    total_value = sum(city_values.values())

    return {
        'state_name': state_info['name'],
        'state_slug': state_slug,
        'cities': cities_with_data,
        'total_permits': total_permits,
        'total_value': total_value,
    }


@app.route('/permits/<state_slug>')
def state_or_city_landing(state_slug):
    """Route that handles both state hub pages and city landing pages."""
    # Check if it's a state slug first
    if state_slug in STATE_CONFIG:
        state_data = get_state_data(state_slug)
        if state_data and state_data['cities']:
            footer_cities = get_cities_with_data()
            return render_template('state_landing.html',
                                   footer_cities=footer_cities,
                                   **state_data)

    # Otherwise, fall through to city landing page logic
    return city_landing_inner(state_slug)


def city_landing_inner(city_slug):
    """Render SEO-optimized city landing page."""
    # Check for SEO config, or create fallback from city_configs
    if city_slug in CITY_SEO_CONFIG:
        config = CITY_SEO_CONFIG[city_slug]
    else:
        # Try to get city from city_configs for dynamic fallback
        city_key, city_config = get_city_by_slug(city_slug)
        if not city_config:
            return render_city_not_found(city_slug)
        # Generate basic SEO config with properly formatted city name
        display_name = format_city_name(city_config["name"])
        config = {
            "name": display_name,
            "raw_name": city_config["name"],  # For filtering permits
            "state": city_config["state"],
            "meta_title": f"{display_name}, {city_config['state']} Building Permits & Contractor Leads | PermitGrab",
            "meta_description": f"Browse active building permits in {display_name}, {city_config['state']}. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
            "seo_content": f"""
                <p>Track new building permits in {display_name}, {city_config['state']}. PermitGrab delivers fresh permit data daily, helping contractors find quality leads across the region.</p>
                <p>Access permit data including project values, contact information, and trade categories. Start browsing {display_name} construction permits today.</p>
            """
        }

    permits = load_permits()

    # Filter permits for this city (use raw_name if available for matching)
    filter_name = config.get('raw_name', config['name'])
    city_permits = [p for p in permits if p.get('city') == filter_name]

    # Calculate stats
    permit_count = len(city_permits)
    total_value = sum(p.get('estimated_cost', 0) for p in city_permits)
    # V12.18: High-value = $100K+ projects (more meaningful to contractors)
    high_value_count = len([p for p in city_permits if p.get('estimated_cost', 0) >= 100000])

    # V12.5: noindex for empty city pages to avoid thin content in Google
    robots_directive = "noindex, follow" if permit_count == 0 else "index, follow"

    # V12.11: Coming Soon flag for empty cities
    is_coming_soon = permit_count == 0

    # Trade breakdown
    trade_breakdown = {}
    for p in city_permits:
        trade = p.get('trade_category', 'Other')
        trade_breakdown[trade] = trade_breakdown.get(trade, 0) + 1

    # Sort permits by value for preview
    sorted_permits = sorted(city_permits, key=lambda x: x.get('estimated_cost', 0), reverse=True)

    # V12.17: Add "is_new" flag for permits filed in last 7 days
    seven_days_ago = datetime.now() - timedelta(days=7)
    for p in sorted_permits:
        filing_date_str = p.get('filing_date', '')
        if filing_date_str:
            try:
                filing_date = datetime.strptime(filing_date_str, '%Y-%m-%d')
                p['is_new'] = filing_date >= seven_days_ago
            except (ValueError, TypeError):
                p['is_new'] = False
        else:
            p['is_new'] = False

    # V12.9: Calculate alternative stats for cities without value data
    new_this_month = permit_count  # Fallback to total count
    unique_contractors = 0
    if total_value == 0:
        # Count permits from last 30 days
        # V12.23: Removed redundant local import that was shadowing module-level timedelta
        thirty_days_ago = datetime.now() - timedelta(days=30)
        recent_permits = []
        contractors_set = set()
        for p in city_permits:
            # Check filing date
            filing_date_str = p.get('filing_date', '')
            if filing_date_str:
                try:
                    filing_date = datetime.strptime(filing_date_str, '%Y-%m-%d')
                    if filing_date >= thirty_days_ago:
                        recent_permits.append(p)
                except:
                    pass
            # Count unique contractors
            contractor = p.get('contractor_name') or p.get('applicant_name')
            if contractor and contractor.strip():
                contractors_set.add(contractor.strip().lower())
        new_this_month = len(recent_permits) if recent_permits else permit_count
        unique_contractors = len(contractors_set) if contractors_set else '50+'

    # V12.17: Other cities for footer links - sorted by permit volume
    cities_by_volume = get_cities_with_data()  # Pre-sorted by permit count descending
    other_cities = [c for c in cities_by_volume if c['slug'] != city_slug]

    # V12.17: Nearby cities sorted by permit volume (not alphabetical)
    current_state = config.get('state', '')
    nearby_cities = [c for c in cities_by_volume if c.get('state') == current_state and c['slug'] != city_slug]
    # If fewer than 6 same-state cities, add top cities from other states
    if len(nearby_cities) < 6:
        other_state_cities = [c for c in cities_by_volume if c.get('state') != current_state][:6 - len(nearby_cities)]
        nearby_cities = nearby_cities + other_state_cities

    return render_template(
        'city_landing.html',
        city_name=config['name'],
        city_slug=city_slug,
        city_state=current_state,
        meta_title=config['meta_title'],
        meta_description=config['meta_description'],
        seo_content=config['seo_content'],
        canonical_url=f"{SITE_URL}/permits/{city_slug}",
        robots_directive=robots_directive,  # V12.5: noindex empty pages
        permit_count=permit_count,
        total_value=total_value,
        high_value_count=high_value_count,
        new_this_month=new_this_month,
        unique_contractors=unique_contractors,
        trade_breakdown=trade_breakdown,
        permits=sorted_permits,
        other_cities=other_cities,
        nearby_cities=nearby_cities,  # V12.11: Same-state cities for internal linking
        current_year=datetime.now().year,
        is_coming_soon=is_coming_soon,  # V12.11: Coming Soon badge
    )


@app.route('/permits/<city_slug>/<trade_slug>')
def city_trade_landing(city_slug, trade_slug):
    """Render SEO-optimized city × trade landing page."""
    # Get city from config
    city_key, city_config = get_city_by_slug(city_slug)
    if not city_config:
        return render_city_not_found(city_slug)

    # Get trade from config
    trade = get_trade(trade_slug)
    if not trade:
        return "Trade not found", 404

    permits = load_permits()

    # V12.9: Format city name for display
    display_name = format_city_name(city_config['name'])

    # Filter permits for this city and trade (use raw name for matching)
    city_permits = [p for p in permits if p.get('city') == city_config['name']]

    # Match permits to this trade based on keywords
    trade_keywords = [kw.lower() for kw in trade['keywords']]
    matching_permits = []
    for p in city_permits:
        text = ""
        if p.get("description"):
            text += p["description"].lower() + " "
        if p.get("permit_type"):
            text += p["permit_type"].lower() + " "
        if p.get("work_type"):
            text += p["work_type"].lower() + " "
        if p.get("trade_category"):
            text += p["trade_category"].lower()

        if any(kw in text for kw in trade_keywords):
            matching_permits.append(p)

    # Sort by date
    matching_permits.sort(key=lambda x: x.get('filing_date', ''), reverse=True)

    # Calculate stats
    # V12.23: Use module-level datetime/timedelta imports
    now = datetime.now()
    month_ago = (now - timedelta(days=30)).isoformat()
    week_ago = (now - timedelta(days=7)).isoformat()

    monthly_count = len([p for p in matching_permits if p.get('filing_date', '') >= month_ago])
    weekly_count = len([p for p in matching_permits if p.get('filing_date', '') >= week_ago])

    values = [p.get('estimated_cost', 0) for p in matching_permits if p.get('estimated_cost')]
    avg_value = int(sum(values) / len(values)) if values else 0

    # Build city dict for template with formatted name
    city_dict = {
        "name": display_name,
        "state": city_config['state'],
        "slug": city_slug,
    }

    stats = {
        "monthly_count": monthly_count or len(matching_permits),
        "weekly_count": weekly_count,
        "avg_value": f"{avg_value:,}" if avg_value else "N/A",
    }

    # Other trades for cross-linking (exclude current)
    other_trades = [t for t in get_all_trades() if t['slug'] != trade_slug]

    # Other cities for cross-linking (exclude current)
    other_cities = [{"name": c['name'], "slug": c['slug']} for c in ALL_CITIES if c['slug'] != city_slug]

    # V12.5: noindex for empty city×trade pages
    robots_directive = "noindex, follow" if len(matching_permits) == 0 else "index, follow"

    return render_template(
        'city_trade_landing.html',
        city=city_dict,
        trade=trade,
        permits=matching_permits[:10],
        stats=stats,
        other_trades=other_trades,
        other_cities=other_cities,
        robots_directive=robots_directive,
    )


# ===========================
# SITEMAP & ROBOTS.TXT
# ===========================

@app.route('/sitemap.xml')
def sitemap():
    """Generate XML sitemap for SEO - fully dynamic."""
    today = datetime.now().strftime('%Y-%m-%d')

    urls = [
        {'loc': SITE_URL, 'changefreq': 'daily', 'priority': '1.0'},
        {'loc': f"{SITE_URL}/pricing", 'changefreq': 'weekly', 'priority': '0.9'},
        {'loc': f"{SITE_URL}/contractors", 'changefreq': 'daily', 'priority': '0.8'},
        {'loc': f"{SITE_URL}/map", 'changefreq': 'daily', 'priority': '0.8'},  # V12.26: Permit heat map
        {'loc': f"{SITE_URL}/get-alerts", 'changefreq': 'weekly', 'priority': '0.7'},
        {'loc': f"{SITE_URL}/blog", 'changefreq': 'weekly', 'priority': '0.7'},
        {'loc': f"{SITE_URL}/stats", 'changefreq': 'daily', 'priority': '0.7'},  # V12.23 SEO
        {'loc': f"{SITE_URL}/about", 'changefreq': 'monthly', 'priority': '0.6'},
        {'loc': f"{SITE_URL}/contact", 'changefreq': 'monthly', 'priority': '0.5'},
        # V12.23 SEO: Removed /login, /signup from sitemap - auth pages shouldn't be indexed
        {'loc': f"{SITE_URL}/privacy", 'changefreq': 'monthly', 'priority': '0.3'},
        {'loc': f"{SITE_URL}/terms", 'changefreq': 'monthly', 'priority': '0.3'},
    ]

    # V12.23 SEO: Add state hub pages to sitemap
    for state_slug in STATE_CONFIG.keys():
        urls.append({
            'loc': f"{SITE_URL}/permits/{state_slug}",
            'changefreq': 'daily',
            'priority': '0.85',  # Between homepage and city pages
        })

    # V12.11: Only include cities with permits in sitemap (exclude empty/coming soon cities)
    cities_with_data = get_cities_with_data()
    cities_with_permits = {c['name'] for c in cities_with_data}

    for city in ALL_CITIES:
        # Skip cities with no permits (these have noindex anyway)
        if city['name'] not in cities_with_permits:
            continue

        urls.append({
            'loc': f"{SITE_URL}/permits/{city['slug']}",
            'changefreq': 'daily',
            'priority': '0.8',
        })

        # Add city × trade pages for each trade
        for trade_slug in get_trade_slugs():
            urls.append({
                'loc': f"{SITE_URL}/permits/{city['slug']}/{trade_slug}",
                'changefreq': 'daily',
                'priority': '0.8',
            })

    # Add blog posts
    blog_dir = os.path.join(os.path.dirname(__file__), 'blog')
    if os.path.exists(blog_dir):
        for filename in os.listdir(blog_dir):
            if filename.endswith('.md'):
                slug = filename.replace('.md', '')
                urls.append({
                    'loc': f"{SITE_URL}/blog/{slug}",
                    'changefreq': 'monthly',
                    'priority': '0.6',
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


@app.route('/logout')
def logout_page():
    """Log out and redirect to homepage."""
    session.clear()
    return redirect('/')


# ===========================
# ACCOUNT SETTINGS (V8)
# ===========================
@app.route('/account')
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


@app.route('/api/account', methods=['PUT'])
def api_update_account():
    """Update account settings."""
    if 'user_email' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    user = find_user_by_email(session['user_email'])
    if not user:
        return jsonify({'error': 'User not found'}), 404

    data = request.get_json()
    if not data:
        return jsonify({'error': 'Invalid request'}), 400

    if 'name' in data:
        user.name = data['name'].strip()
    if 'city' in data:
        user.city = data['city']
    if 'trade' in data:
        user.trade = data['trade']
    if 'daily_alerts' in data:
        user.daily_alerts = bool(data['daily_alerts'])
    # V12.26: Competitor Watch and Digest Cities
    if 'watched_competitors' in data:
        competitors = data['watched_competitors']
        if isinstance(competitors, list):
            # Limit to 5 competitors, Pro only
            if user.plan == 'pro':
                user.watched_competitors = json.dumps(competitors[:5])
    if 'digest_cities' in data:
        cities = data['digest_cities']
        if isinstance(cities, list):
            user.digest_cities = json.dumps(cities)

    db.session.commit()
    return jsonify({'success': True, 'user': user.to_dict()})


# V12.26: Competitor Watch API
@app.route('/api/competitors/watch', methods=['GET', 'POST', 'DELETE'])
def api_competitor_watch():
    """Manage watched competitors for Competitor Watch alerts."""
    if 'user_email' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    user = find_user_by_email(session['user_email'])
    if not user:
        return jsonify({'error': 'User not found'}), 404
    if user.plan != 'pro':
        return jsonify({'error': 'Competitor Watch is a Pro feature', 'upgrade_url': '/pricing'}), 403

    competitors = json.loads(user.watched_competitors or '[]')

    if request.method == 'GET':
        return jsonify({'competitors': competitors})

    elif request.method == 'POST':
        data = request.get_json()
        name = (data.get('name', '') if data else '').strip()
        if not name:
            return jsonify({'error': 'Competitor name required'}), 400
        if len(competitors) >= 5:
            return jsonify({'error': 'Maximum 5 competitors allowed'}), 400
        if name.lower() not in [c.lower() for c in competitors]:
            competitors.append(name)
            user.watched_competitors = json.dumps(competitors)
            db.session.commit()
        return jsonify({'competitors': competitors})

    elif request.method == 'DELETE':
        data = request.get_json()
        name = (data.get('name', '') if data else '').strip()
        competitors = [c for c in competitors if c.lower() != name.lower()]
        user.watched_competitors = json.dumps(competitors)
        db.session.commit()
        return jsonify({'competitors': competitors})


# V12.26: Check for competitor matches in recent permits
@app.route('/api/competitors/matches')
def api_competitor_matches():
    """Get permits matching watched competitors."""
    if 'user_email' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    user = find_user_by_email(session['user_email'])
    if not user:
        return jsonify({'error': 'User not found'}), 404
    if user.plan != 'pro':
        return jsonify({'error': 'Competitor Watch is a Pro feature'}), 403

    competitors = json.loads(user.watched_competitors or '[]')
    if not competitors:
        return jsonify({'matches': [], 'message': 'No competitors being watched'})

    permits = load_permits()
    matches = []

    for permit in permits:
        contractor = (permit.get('contact_name', '') or '').lower()
        for comp in competitors:
            if comp.lower() in contractor:
                matches.append({
                    'permit': permit,
                    'matched_competitor': comp
                })
                break

    # Sort by filing date, most recent first
    matches.sort(key=lambda x: x['permit'].get('filing_date', ''), reverse=True)

    return jsonify({'matches': matches[:50]})  # Limit to 50 most recent


@app.route('/api/change-password', methods=['POST'])
def api_change_password():
    """Change user password."""
    if 'user_email' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    user = find_user_by_email(session['user_email'])
    if not user:
        return jsonify({'error': 'User not found'}), 404

    data = request.get_json()
    if not data:
        return jsonify({'error': 'Invalid request'}), 400

    current_password = data.get('current_password', '')
    new_password = data.get('new_password', '')

    if not check_password_hash(user.password_hash, current_password):
        return jsonify({'error': 'Current password is incorrect'}), 400

    if len(new_password) < 8:
        return jsonify({'error': 'New password must be at least 8 characters'}), 400

    user.password_hash = generate_password_hash(new_password)
    db.session.commit()
    return jsonify({'success': True, 'message': 'Password changed successfully'})


@app.route('/robots.txt')
def robots():
    """V12.11: Serve robots.txt for search engines."""
    content = f"""User-agent: *
Allow: /
Disallow: /admin/
Disallow: /api/
Disallow: /dashboard/
Disallow: /my-leads
Disallow: /early-intel
Disallow: /analytics
Disallow: /account
Disallow: /saved-leads
Disallow: /saved-searches
Disallow: /billing
Disallow: /onboarding
Disallow: /logout
Disallow: /reset-password
Disallow: /login
Disallow: /signup

# Crawl-delay for polite crawling
Crawl-delay: 1

Sitemap: {SITE_URL}/sitemap.xml
"""
    return Response(content, mimetype='text/plain')


# ===========================
# BLOG SYSTEM
# ===========================
import markdown
import re

BLOG_DIR = os.path.join(os.path.dirname(__file__), 'blog')


def parse_blog_post(filename):
    """Parse a markdown blog post with frontmatter."""
    filepath = os.path.join(BLOG_DIR, filename)
    if not os.path.exists(filepath):
        return None

    with open(filepath, 'r') as f:
        content = f.read()

    # Parse frontmatter (YAML between --- markers)
    meta = {}
    body = content
    if content.startswith('---'):
        parts = content.split('---', 2)
        if len(parts) >= 3:
            for line in parts[1].strip().split('\n'):
                if ':' in line:
                    key, val = line.split(':', 1)
                    meta[key.strip()] = val.strip().strip('"').strip("'")
            body = parts[2]

    # Convert markdown to HTML
    html = markdown.markdown(body, extensions=['fenced_code', 'tables'])

    # Extract excerpt (first 160 chars of text)
    text_only = re.sub(r'<[^>]+>', '', html)
    excerpt = text_only[:160].strip() + '...' if len(text_only) > 160 else text_only

    # V12.26: Parse FAQs if present in frontmatter (JSON array format)
    faqs = []
    if 'faqs' in meta:
        try:
            faqs = json.loads(meta['faqs'])
        except (json.JSONDecodeError, TypeError):
            pass

    return {
        'slug': filename.replace('.md', ''),
        'title': meta.get('title', 'Untitled'),
        'date': meta.get('date', ''),
        'author': meta.get('author', 'PermitGrab Team'),
        'excerpt': excerpt,
        'content': html,
        'keywords': meta.get('keywords', ''),
        'faqs': faqs,
    }


def get_all_blog_posts():
    """Get all blog posts sorted by date."""
    if not os.path.exists(BLOG_DIR):
        return []

    posts = []
    for filename in os.listdir(BLOG_DIR):
        if filename.endswith('.md'):
            post = parse_blog_post(filename)
            if post:
                posts.append(post)

    # Sort by date descending
    posts.sort(key=lambda x: x.get('date', ''), reverse=True)
    return posts


@app.route('/blog')
def blog_index():
    """Blog index page."""
    posts = get_all_blog_posts()
    return render_template('blog_index.html', posts=posts)


@app.route('/blog/<slug>')
def blog_post(slug):
    """Individual blog post page."""
    post = parse_blog_post(f"{slug}.md")
    if not post:
        return "Post not found", 404
    return render_template('blog_post.html', post=post)


# ===========================
# SCHEDULED DATA COLLECTION
# ===========================
def scheduled_collection():
    """Run data collection every 24 hours. Waits for initial collection first."""
    # V12.2: Wait for initial collection to finish (sleep 30 minutes on first boot)
    print(f"[{datetime.now()}] Scheduled collector waiting 30 minutes for initial collection...")
    time.sleep(1800)  # 30 minutes

    # Track when we last ran permit history (run weekly)
    last_history_run = None

    while True:
        try:
            print(f"[{datetime.now()}] Running scheduled data collection...")

            # Regular permit collection (daily)
            from collector import collect_all, collect_permit_history
            collect_all(days_back=60)
            print(f"[{datetime.now()}] Permit collection complete.")

            # Violation collection (daily)
            try:
                from violation_collector import collect_all_violations
                collect_all_violations(days_back=90)
                print(f"[{datetime.now()}] Violation collection complete.")
            except Exception as e:
                print(f"[{datetime.now()}] Violation collection error: {e}")

            # Signal collection (daily)
            try:
                from signal_collector import collect_all_signals
                collect_all_signals(days_back=90)
                print(f"[{datetime.now()}] Signal collection complete.")
            except Exception as e:
                print(f"[{datetime.now()}] Signal collection error: {e}")

            # Permit history collection (weekly or first run)
            now = datetime.now()
            if last_history_run is None or (now - last_history_run).days >= 7:
                try:
                    collect_permit_history(years_back=3)
                    last_history_run = now
                    print(f"[{datetime.now()}] Permit history collection complete.")
                except Exception as e:
                    print(f"[{datetime.now()}] Permit history collection error: {e}")

            # City health check (daily)
            try:
                from city_health import check_all_cities
                check_all_cities()
                print(f"[{datetime.now()}] City health check complete.")
            except Exception as e:
                print(f"[{datetime.now()}] City health check error: {e}")

            print(f"[{datetime.now()}] All collection tasks complete.")
        except Exception as e:
            print(f"[{datetime.now()}] Collection error: {e}")

        # Sleep 24 hours
        time.sleep(86400)


def run_initial_collection():
    """Run initial data collection on startup."""
    try:
        print(f"[{datetime.now()}] Running initial data collection...")

        # Regular permit collection
        # V12.4: Increased from 60 to 365 days to catch stale datasets
        from collector import collect_all, collect_permit_history
        collect_all(days_back=365)

        # Violation collection
        try:
            from violation_collector import collect_all_violations
            collect_all_violations(days_back=90)
        except Exception as e:
            print(f"[{datetime.now()}] Initial violation collection error: {e}")

        # Signal collection
        try:
            from signal_collector import collect_all_signals
            collect_all_signals(days_back=90)
        except Exception as e:
            print(f"[{datetime.now()}] Initial signal collection error: {e}")

        # Permit history collection (first run)
        try:
            collect_permit_history(years_back=3)
        except Exception as e:
            print(f"[{datetime.now()}] Initial history collection error: {e}")

        print(f"[{datetime.now()}] Initial collection complete.")
    except Exception as e:
        print(f"[{datetime.now()}] Initial collection error: {e}")


# ===========================
# ERROR HANDLERS
# ===========================
@app.errorhandler(404)
def page_not_found(e):
    """Handle 404 errors with a styled page."""
    footer_cities = get_cities_with_data()
    return render_template('404.html', footer_cities=footer_cities), 404


# ===========================
# STARTUP: DATA COLLECTION
# ===========================
# This runs when gunicorn imports the module (or when running directly).
# Use a flag to prevent multiple workers from each starting collectors.

_collector_started = False


def _test_outbound_connectivity():
    """V12.2: Quick test of outbound API access on startup."""
    import requests
    test_session = requests.Session()
    test_session.headers.update({
        "User-Agent": "PermitGrab/1.0 (permit lead aggregator; contact@permitgrab.com)",
        "Accept": "application/json",
    })
    test_urls = [
        "https://data.cityofnewyork.us/resource/rbx6-tga4.json?$limit=1",
        "https://data.cityofchicago.org/resource/ydr8-5enu.json?$limit=1",
        "https://data.lacity.org/resource/yv23-pmwf.json?$limit=1",
    ]
    print(f"[{datetime.now()}] Testing outbound API connectivity...")
    for url in test_urls:
        try:
            resp = test_session.get(url, timeout=15)
            print(f"  [NET TEST] {url[:50]}... → {resp.status_code} ({len(resp.content)} bytes)")
        except Exception as e:
            print(f"  [NET TEST] {url[:50]}... → FAILED: {type(e).__name__}: {str(e)[:80]}")


def start_collectors():
    """Start background data collection threads. Safe to call multiple times."""
    global _collector_started
    if _collector_started:
        return
    _collector_started = True

    os.makedirs(DATA_DIR, exist_ok=True)

    # V12.2: Test network connectivity before starting threads
    _test_outbound_connectivity()

    print(f"[{datetime.now()}] Starting background data collectors...")

    # Initial collection thread
    initial_thread = threading.Thread(target=run_initial_collection, daemon=True)
    initial_thread.start()

    # Scheduled daily collection thread
    collector_thread = threading.Thread(target=scheduled_collection, daemon=True)
    collector_thread.start()

    print(f"[{datetime.now()}] Collector threads started.")

# V12.12: Preload existing data from disk BEFORE starting collectors
# This ensures stale data is served immediately rather than showing 0 permits
preload_data_from_disk()

# Start collectors when module is loaded (works with gunicorn --preload)
start_collectors()


# ===========================
# MAIN
# ===========================
if __name__ == '__main__':
    # Local development only (gunicorn handles production)
    print("=" * 50)
    print("PermitGrab Server Starting")
    print(f"Dashboard: http://localhost:5000")
    print(f"API: http://localhost:5000/api/permits")
    print("=" * 50)

    app.run(host='0.0.0.0', port=5000, debug=True)
