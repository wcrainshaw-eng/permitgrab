"""
PermitGrab Custom Analytics Module
Tracks user events, page views, and business metrics.
Uses JSON file storage for simplicity (can migrate to PostgreSQL later).
"""

import json
import os
import hashlib
import uuid
from datetime import datetime, timedelta
from functools import wraps
from flask import request, session, g

# Storage directory
DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
USER_EVENTS_FILE = os.path.join(DATA_DIR, 'user_events.json')
HEALTH_CHECKS_FILE = os.path.join(DATA_DIR, 'health_checks.json')

# Ensure data directory exists
os.makedirs(DATA_DIR, exist_ok=True)


def load_user_events():
    """Load all user events from JSON file."""
    if os.path.exists(USER_EVENTS_FILE):
        try:
            with open(USER_EVENTS_FILE, 'r') as f:
                return json.load(f)
        except:
            return []
    return []


def save_user_events(events):
    """Save user events to JSON file."""
    # Keep only last 90 days of events to prevent unbounded growth
    cutoff = (datetime.now() - timedelta(days=90)).isoformat()
    events = [e for e in events if e.get('created_at', '') > cutoff]

    with open(USER_EVENTS_FILE, 'w') as f:
        json.dump(events, f, indent=2)


def load_health_checks():
    """Load health check data."""
    if os.path.exists(HEALTH_CHECKS_FILE):
        try:
            with open(HEALTH_CHECKS_FILE, 'r') as f:
                return json.load(f)
        except:
            return []
    return []


def save_health_checks(checks):
    """Save health check data."""
    # Keep only last 30 days of detailed checks
    cutoff = (datetime.now() - timedelta(days=30)).isoformat()
    checks = [c for c in checks if c.get('created_at', '') > cutoff]

    with open(HEALTH_CHECKS_FILE, 'w') as f:
        json.dump(checks, f, indent=2)


def get_session_id():
    """Get or create a session ID for tracking anonymous visitors."""
    if 'analytics_session_id' not in session:
        session['analytics_session_id'] = str(uuid.uuid4())
    return session.get('analytics_session_id')


def hash_ip(ip_address):
    """Hash an IP address for privacy-safe storage."""
    if not ip_address:
        return None
    return hashlib.sha256(ip_address.encode()).hexdigest()[:16]


def get_current_user_id():
    """Get the current user's ID from session if logged in."""
    try:
        from server import get_current_user
        user = get_current_user()
        return user.get('email') if user else None
    except:
        return None


def track_event(event_type, event_data=None, page=None, city_filter=None, trade_filter=None, user_id_override=None):
    """
    Log a user event. Call this from any route handler.
    Automatically captures: user_id (from session/auth), session_id,
    referrer, user_agent, ip_hash.

    This function should NEVER raise an exception that breaks the page.
    """
    try:
        # Build the event
        event = {
            'id': str(uuid.uuid4()),
            'user_id': user_id_override or get_current_user_id(),
            'session_id': get_session_id(),
            'event_type': event_type,
            'event_data': event_data or {},
            'page': page or request.path,
            'city_filter': city_filter,
            'trade_filter': trade_filter,
            'referrer': request.referrer[:500] if request.referrer else None,
            'user_agent': request.headers.get('User-Agent', '')[:500],
            'ip_hash': hash_ip(request.remote_addr),
            'created_at': datetime.now().isoformat(),
        }

        # Include UTM params if present in session
        if 'utm_params' in session:
            event['event_data']['utm'] = session['utm_params']

        # Load existing events and append
        events = load_user_events()
        events.append(event)
        save_user_events(events)

    except Exception as e:
        # Never break the page for analytics
        print(f"[Analytics] Error tracking event: {e}")


def record_health_check(check_type, target, status, response_time_ms=0, details=None):
    """
    Record a health check result.

    check_type: 'api_fetch', 'page_load', 'service', 'data_freshness', 'permit_trend'
    target: what was checked (city slug, URL, service name)
    status: 'ok', 'degraded', 'down', 'error'
    response_time_ms: how long the check took
    details: dict with additional info
    """
    try:
        check = {
            'id': str(uuid.uuid4()),
            'check_type': check_type,
            'target': target,
            'status': status,
            'response_time_ms': response_time_ms,
            'details': details or {},
            'created_at': datetime.now().isoformat(),
        }

        checks = load_health_checks()
        checks.append(check)
        save_health_checks(checks)

    except Exception as e:
        print(f"[Analytics] Error recording health check: {e}")


# ===========================
# ANALYTICS QUERIES
# ===========================

def get_visitors_today():
    """Count unique visitors (by session_id) today."""
    events = load_user_events()
    today = datetime.now().strftime('%Y-%m-%d')
    sessions = set()
    for e in events:
        if e.get('event_type') == 'page_view' and e.get('created_at', '').startswith(today):
            sessions.add(e.get('session_id'))
    return len(sessions)


def get_signups_this_week():
    """Count signups in the last 7 days."""
    events = load_user_events()
    week_ago = (datetime.now() - timedelta(days=7)).isoformat()
    return sum(1 for e in events
               if e.get('event_type') == 'signup' and e.get('created_at', '') >= week_ago)


def get_active_users_7d():
    """Count unique logged-in users in the last 7 days."""
    events = load_user_events()
    week_ago = (datetime.now() - timedelta(days=7)).isoformat()
    users = set()
    for e in events:
        if e.get('user_id') and e.get('created_at', '') >= week_ago:
            users.add(e.get('user_id'))
    return len(users)


def get_trial_starts_30d():
    """Count checkout_started events in the last 30 days."""
    events = load_user_events()
    month_ago = (datetime.now() - timedelta(days=30)).isoformat()
    return sum(1 for e in events
               if e.get('event_type') == 'checkout_started' and e.get('created_at', '') >= month_ago)


def get_daily_traffic(days=30):
    """Get daily page views and unique visitors for the last N days."""
    events = load_user_events()
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()

    # Group by day
    daily = {}
    for e in events:
        if e.get('event_type') == 'page_view' and e.get('created_at', '') >= cutoff:
            day = e['created_at'][:10]
            if day not in daily:
                daily[day] = {'views': 0, 'sessions': set()}
            daily[day]['views'] += 1
            daily[day]['sessions'].add(e.get('session_id'))

    # Convert to list
    result = []
    for day in sorted(daily.keys()):
        result.append({
            'day': day,
            'page_views': daily[day]['views'],
            'visitors': len(daily[day]['sessions'])
        })
    return result


def get_top_pages(days=7, limit=20):
    """Get top pages by views in the last N days."""
    events = load_user_events()
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()

    pages = {}
    for e in events:
        if e.get('event_type') == 'page_view' and e.get('created_at', '') >= cutoff:
            page = e.get('page', '/')
            if page not in pages:
                pages[page] = {'views': 0, 'sessions': set()}
            pages[page]['views'] += 1
            pages[page]['sessions'].add(e.get('session_id'))

    result = [
        {'page': p, 'views': data['views'], 'unique_visitors': len(data['sessions'])}
        for p, data in pages.items()
    ]
    result.sort(key=lambda x: x['views'], reverse=True)
    return result[:limit]


def get_conversion_funnel(days=30):
    """Get conversion funnel metrics for the last N days."""
    events = load_user_events()
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()

    recent_events = [e for e in events if e.get('created_at', '') >= cutoff]

    # Count each funnel step
    visitors = len(set(e.get('session_id') for e in recent_events if e.get('event_type') == 'page_view'))
    signup_page_views = len(set(e.get('session_id') for e in recent_events
                                if e.get('event_type') == 'page_view' and e.get('page') == '/signup'))
    signups = sum(1 for e in recent_events if e.get('event_type') == 'signup')
    onboarding_complete = sum(1 for e in recent_events if e.get('event_type') == 'onboarding_complete')
    checkout_started = sum(1 for e in recent_events if e.get('event_type') == 'checkout_started')
    payment_success = sum(1 for e in recent_events if e.get('event_type') == 'payment_success')

    def pct(a, b):
        return round((a / b * 100), 1) if b > 0 else 0

    return [
        {'step': 'Visitors', 'count': visitors, 'pct': 100},
        {'step': 'Signup Page Viewed', 'count': signup_page_views, 'pct': pct(signup_page_views, visitors)},
        {'step': 'Signups Completed', 'count': signups, 'pct': pct(signups, signup_page_views) if signup_page_views else pct(signups, visitors)},
        {'step': 'Onboarding Completed', 'count': onboarding_complete, 'pct': pct(onboarding_complete, signups)},
        {'step': 'Checkout Started', 'count': checkout_started, 'pct': pct(checkout_started, onboarding_complete) if onboarding_complete else pct(checkout_started, signups)},
        {'step': 'Payment Success', 'count': payment_success, 'pct': pct(payment_success, checkout_started)},
    ]


def get_event_counts(days=7):
    """Get counts of non-page-view events in the last N days."""
    events = load_user_events()
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()

    counts = {}
    for e in events:
        if e.get('event_type') != 'page_view' and e.get('created_at', '') >= cutoff:
            event_type = e.get('event_type')
            if event_type not in counts:
                counts[event_type] = {'total': 0, 'users': set()}
            counts[event_type]['total'] += 1
            user_key = e.get('user_id') or e.get('session_id')
            counts[event_type]['users'].add(user_key)

    result = [
        {'event_type': et, 'count': data['total'], 'unique_users': len(data['users'])}
        for et, data in counts.items()
    ]
    result.sort(key=lambda x: x['count'], reverse=True)
    return result


def get_city_engagement(days=30):
    """Get engagement metrics by city."""
    events = load_user_events()
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()

    cities = {}
    for e in events:
        if e.get('created_at', '') < cutoff:
            continue

        city = None

        # Extract city from page path
        page = e.get('page', '')
        if page.startswith('/permits/'):
            parts = page.split('/')
            if len(parts) >= 3:
                city = parts[2]

        # Or from filter
        city = city or e.get('city_filter')

        if not city:
            continue

        if city not in cities:
            cities[city] = {'page_views': 0, 'filter_uses': 0, 'leads_saved': 0, 'alerts': 0}

        event_type = e.get('event_type')
        if event_type == 'page_view':
            cities[city]['page_views'] += 1
        elif event_type == 'filter_applied':
            cities[city]['filter_uses'] += 1
        elif event_type == 'lead_save':
            cities[city]['leads_saved'] += 1
        elif event_type == 'alert_signup':
            cities[city]['alerts'] += 1

    result = [
        {'city': city, **data}
        for city, data in cities.items()
    ]
    result.sort(key=lambda x: x['page_views'], reverse=True)
    return result


def get_traffic_sources(days=30):
    """Get traffic sources from UTM params and referrers."""
    events = load_user_events()
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()

    sources = {}

    for e in events:
        if e.get('created_at', '') < cutoff:
            continue

        # Get UTM params if present
        utm = e.get('event_data', {}).get('utm', {})
        source = utm.get('utm_source')
        medium = utm.get('utm_medium')

        # Fallback to referrer classification
        if not source:
            referrer = e.get('referrer', '') or ''
            if 'google' in referrer.lower():
                source, medium = 'google', 'organic'
            elif 'bing' in referrer.lower():
                source, medium = 'bing', 'organic'
            elif 'linkedin' in referrer.lower():
                source, medium = 'linkedin', 'social'
            elif 'facebook' in referrer.lower():
                source, medium = 'facebook', 'social'
            elif 'twitter' in referrer.lower() or 'x.com' in referrer.lower():
                source, medium = 'twitter', 'social'
            elif referrer:
                source, medium = 'referral', 'referral'
            else:
                source, medium = 'direct', 'none'

        key = f"{source}|{medium}"
        if key not in sources:
            sources[key] = {'source': source, 'medium': medium, 'visitors': set(), 'signups': 0}

        sources[key]['visitors'].add(e.get('session_id'))
        if e.get('event_type') == 'signup':
            sources[key]['signups'] += 1

    result = []
    for key, data in sources.items():
        visitor_count = len(data['visitors'])
        result.append({
            'source': data['source'],
            'medium': data['medium'],
            'visitors': visitor_count,
            'signups': data['signups'],
            'conversion': round(data['signups'] / visitor_count * 100, 1) if visitor_count else 0
        })

    result.sort(key=lambda x: x['visitors'], reverse=True)
    return result


# ===========================
# EMAIL ANALYTICS QUERIES
# ===========================

def get_email_performance(days=7):
    """Get email engagement metrics for the specified period."""
    events = load_user_events()
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()

    # Count email events by type
    counts = {
        'delivered': 0,
        'open': 0,
        'click': 0,
        'bounce': 0,
        'unsubscribe': 0,
        'spamreport': 0,
        'dropped': 0,
    }

    for e in events:
        if e.get('created_at', '') < cutoff:
            continue

        event_type = e.get('event_type', '')
        if event_type.startswith('email_'):
            sg_type = event_type.replace('email_', '')
            if sg_type in counts:
                counts[sg_type] += 1

    # Calculate rates
    sent = counts['delivered'] + counts['bounce'] + counts['dropped']
    result = {
        'sent': sent,
        'delivered': counts['delivered'],
        'opens': counts['open'],
        'clicks': counts['click'],
        'bounces': counts['bounce'],
        'unsubscribes': counts['unsubscribe'],
        'spam_reports': counts['spamreport'],
        'open_rate': round((counts['open'] / counts['delivered'] * 100), 1) if counts['delivered'] > 0 else 0,
        'click_rate': round((counts['click'] / counts['delivered'] * 100), 1) if counts['delivered'] > 0 else 0,
        'bounce_rate': round((counts['bounce'] / sent * 100), 1) if sent > 0 else 0,
        'unsubscribe_rate': round((counts['unsubscribe'] / counts['delivered'] * 100), 1) if counts['delivered'] > 0 else 0,
    }

    return result


# ===========================
# HEALTH CHECK QUERIES
# ===========================

def get_latest_health_status():
    """Get the most recent health check for each target."""
    checks = load_health_checks()

    latest = {}
    for c in checks:
        key = f"{c.get('check_type')}:{c.get('target')}"
        if key not in latest or c.get('created_at', '') > latest[key].get('created_at', ''):
            latest[key] = c

    return list(latest.values())


def get_health_checks_24h(check_type=None, target=None):
    """Get health checks from the last 24 hours, optionally filtered."""
    checks = load_health_checks()
    cutoff = (datetime.now() - timedelta(hours=24)).isoformat()

    result = []
    for c in checks:
        if c.get('created_at', '') < cutoff:
            continue
        if check_type and c.get('check_type') != check_type:
            continue
        if target and c.get('target') != target:
            continue
        result.append(c)

    return result


def get_health_failures_recent(limit=50):
    """Get the most recent health check failures."""
    checks = load_health_checks()

    failures = [c for c in checks if c.get('status') != 'ok']
    failures.sort(key=lambda x: x.get('created_at', ''), reverse=True)
    return failures[:limit]


def get_city_health_summary():
    """
    Get comprehensive health summary for each city.
    Combines API fetch, data freshness, and permit trend checks.
    """
    checks = load_health_checks()

    # Group by city and check type, keeping only latest
    city_checks = {}
    for c in checks:
        check_type = c.get('check_type')
        target = c.get('target')

        if check_type not in ('api_fetch', 'data_freshness', 'permit_trend'):
            continue

        key = f"{target}:{check_type}"
        if key not in city_checks or c.get('created_at', '') > city_checks[key].get('created_at', ''):
            city_checks[key] = c

    # Build city summaries
    cities = {}
    for key, c in city_checks.items():
        target = c.get('target')
        check_type = c.get('check_type')

        if target not in cities:
            cities[target] = {
                'city': target,
                'city_name': c.get('details', {}).get('city_name', target),
                'api_status': 'unknown',
                'api_response_ms': 0,
                'freshness_status': 'unknown',
                'hours_since_newest': None,
                'permits_24h': 0,
                'permits_7d': 0,
                'trend_status': 'unknown',
                'pct_change': None,
                'overall_status': 'unknown',
                'last_check': c.get('created_at'),
            }

        details = c.get('details', {})

        if check_type == 'api_fetch':
            cities[target]['api_status'] = c.get('status')
            cities[target]['api_response_ms'] = c.get('response_time_ms', 0)
        elif check_type == 'data_freshness':
            cities[target]['freshness_status'] = c.get('status')
            cities[target]['hours_since_newest'] = details.get('hours_since_newest')
            cities[target]['permits_24h'] = details.get('permits_last_24h', 0)
            cities[target]['permits_7d'] = details.get('permits_last_7d', 0)
        elif check_type == 'permit_trend':
            cities[target]['trend_status'] = c.get('status')
            cities[target]['pct_change'] = details.get('pct_change')

        # Update last check time
        if c.get('created_at', '') > (cities[target].get('last_check') or ''):
            cities[target]['last_check'] = c.get('created_at')

    # Calculate overall status (worst of all checks)
    status_priority = {'down': 0, 'error': 1, 'degraded': 2, 'ok': 3, 'unknown': 4}
    for city_data in cities.values():
        statuses = [
            city_data['api_status'],
            city_data['freshness_status'],
            city_data['trend_status'],
        ]
        # Get the worst status
        worst = min(statuses, key=lambda s: status_priority.get(s, 5))
        city_data['overall_status'] = worst

    # Convert to list and sort by status (failures first)
    result = list(cities.values())
    result.sort(key=lambda x: (status_priority.get(x['overall_status'], 5), x['city']))

    return result


def get_route_health_summary():
    """Get health summary for monitored routes."""
    checks = load_health_checks()
    cutoff = (datetime.now() - timedelta(hours=24)).isoformat()

    # Filter to page_load checks in last 24h
    route_checks = [c for c in checks
                    if c.get('check_type') == 'page_load'
                    and c.get('created_at', '') >= cutoff]

    # Group by route
    routes = {}
    for c in route_checks:
        route = c.get('target')
        if route not in routes:
            routes[route] = {
                'route': route,
                'checks': [],
                'response_times': [],
            }
        routes[route]['checks'].append(c)
        if c.get('response_time_ms'):
            routes[route]['response_times'].append(c['response_time_ms'])

    # Calculate metrics
    result = []
    for route, data in routes.items():
        times = data['response_times']
        errors = sum(1 for c in data['checks'] if c.get('status') != 'ok')

        # Get latest status
        latest = max(data['checks'], key=lambda x: x.get('created_at', ''))

        if times:
            avg_ms = round(sum(times) / len(times))
            # P95 approximation
            sorted_times = sorted(times)
            p95_idx = int(len(sorted_times) * 0.95)
            p95_ms = sorted_times[min(p95_idx, len(sorted_times) - 1)]
        else:
            avg_ms = 0
            p95_ms = 0

        # Determine status
        if errors > 0:
            status = 'error'
        elif avg_ms > 3000:
            status = 'degraded'
        elif avg_ms > 1000:
            status = 'slow'
        else:
            status = 'ok'

        result.append({
            'route': route,
            'status': status,
            'avg_response_ms': avg_ms,
            'p95_response_ms': p95_ms,
            'error_count': errors,
            'check_count': len(data['checks']),
            'last_check': latest.get('created_at'),
        })

    # Sort by avg response time (slowest first)
    result.sort(key=lambda x: (-1 if x['status'] == 'error' else x['avg_response_ms']), reverse=True)

    return result


def get_service_health_status():
    """Get health status for third-party services."""
    checks = load_health_checks()

    # Get latest service checks
    services = {}
    for c in checks:
        if c.get('check_type') != 'service':
            continue

        target = c.get('target')
        if target not in services or c.get('created_at', '') > services[target].get('created_at', ''):
            services[target] = c

    return list(services.values())


# ===========================
# DATA RETENTION & CLEANUP
# ===========================

def cleanup_old_data():
    """
    Clean up old analytics data to prevent unbounded growth.
    - User events: Keep 90 days
    - Health checks: Keep 30 days

    Run this daily (e.g., 3am) via cron or scheduler.
    """
    print(f"[Analytics] Running data cleanup at {datetime.now().isoformat()}")

    # Cleanup user events
    events = load_user_events()
    events_before = len(events)
    cutoff = (datetime.now() - timedelta(days=90)).isoformat()
    events = [e for e in events if e.get('created_at', '') > cutoff]
    events_removed = events_before - len(events)

    if events_removed > 0:
        with open(USER_EVENTS_FILE, 'w') as f:
            json.dump(events, f, indent=2)
        print(f"[Analytics] Removed {events_removed} user events older than 90 days")

    # Cleanup health checks
    checks = load_health_checks()
    checks_before = len(checks)
    cutoff = (datetime.now() - timedelta(days=30)).isoformat()
    checks = [c for c in checks if c.get('created_at', '') > cutoff]
    checks_removed = checks_before - len(checks)

    if checks_removed > 0:
        with open(HEALTH_CHECKS_FILE, 'w') as f:
            json.dump(checks, f, indent=2)
        print(f"[Analytics] Removed {checks_removed} health checks older than 30 days")

    print(f"[Analytics] Cleanup complete. Remaining: {len(events)} events, {len(checks)} health checks")

    return {
        'events_removed': events_removed,
        'health_checks_removed': checks_removed,
        'events_remaining': len(events),
        'health_checks_remaining': len(checks),
    }


def get_health_summary_for_report():
    """Get health summary data formatted for the weekly report email."""
    city_health = get_city_health_summary()
    service_health = get_service_health_status()
    failures = get_health_failures_recent(50)

    # Count statuses
    status_counts = {'ok': 0, 'degraded': 0, 'error': 0, 'down': 0}
    for city in city_health:
        status = city.get('overall_status', 'unknown')
        if status in status_counts:
            status_counts[status] += 1

    # Calculate uptime percentage (simplified)
    checks = load_health_checks()
    week_ago = (datetime.now() - timedelta(days=7)).isoformat()
    recent_checks = [c for c in checks if c.get('created_at', '') >= week_ago]

    total_checks = len(recent_checks)
    ok_checks = sum(1 for c in recent_checks if c.get('status') == 'ok')
    uptime_pct = round((ok_checks / total_checks * 100), 1) if total_checks > 0 else 0

    # Count failures by type
    failure_counts = {}
    for f in failures:
        key = f"{f.get('check_type')}:{f.get('target')}"
        failure_counts[key] = failure_counts.get(key, 0) + 1

    # Get top 5 failures
    top_failures = sorted(failure_counts.items(), key=lambda x: x[1], reverse=True)[:5]

    return {
        'city_health': city_health,
        'service_health': service_health,
        'status_counts': status_counts,
        'uptime_pct': uptime_pct,
        'total_failures_7d': len(failures),
        'top_failures': top_failures,
    }
