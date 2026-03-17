"""
PermitGrab - Comprehensive Health Monitoring System
Monitors: City APIs, data freshness, routes, and third-party services.
Records results in the analytics system for dashboard visualization.
"""

import os
import json
import time
import requests
from datetime import datetime, timedelta

import analytics
from city_configs import CITY_REGISTRY, get_active_cities, get_city_config
from city_health import test_city, check_all_cities

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
SITE_URL = os.environ.get('SITE_URL', 'https://permitgrab.com')


# ===========================
# CITY API HEALTH CHECKS
# ===========================

def check_city_api(city_key):
    """
    Check a city's API health and record the result.
    Uses the existing city_health.py test function.
    """
    try:
        result = test_city(city_key)

        if result.get('success'):
            status = 'ok'
        elif result.get('status') == 'warning':
            status = 'degraded'
        else:
            status = 'error'

        response_time = int(result.get('response_time', 0) * 1000)

        # Record in analytics
        analytics.record_health_check(
            check_type='api_fetch',
            target=city_key,
            status=status,
            response_time_ms=response_time,
            details={
                'city_name': result.get('city'),
                'platform': result.get('platform'),
                'sample_count': result.get('sample_count', 0),
                'error': result.get('error'),
                'warning': result.get('warning'),
            }
        )

        return {'status': status, 'response_time_ms': response_time, 'details': result}

    except Exception as e:
        analytics.record_health_check(
            check_type='api_fetch',
            target=city_key,
            status='error',
            response_time_ms=0,
            details={'error': str(e)}
        )
        return {'status': 'error', 'response_time_ms': 0, 'details': {'error': str(e)}}


# ===========================
# DATA FRESHNESS CHECKS
# ===========================

def check_city_data_freshness(city_key):
    """
    Check when the most recent permit was ingested for this city.
    Uses the permits.json file to determine data freshness.
    """
    try:
        permits_file = os.path.join(DATA_DIR, 'permits.json')
        if not os.path.exists(permits_file):
            return {'status': 'error', 'details': {'error': 'permits.json not found'}}

        with open(permits_file) as f:
            permits = json.load(f)

        config = get_city_config(city_key)
        city_name = config.get('name') if config else city_key

        # Filter permits for this city
        city_permits = [p for p in permits if p.get('city') == city_name]

        if not city_permits:
            analytics.record_health_check(
                check_type='data_freshness',
                target=city_key,
                status='error',
                response_time_ms=0,
                details={'error': 'No permits found for city', 'total_permits': 0}
            )
            return {'status': 'error', 'details': {'error': 'No permits found'}}

        # Find newest permit by filing_date
        dates = []
        for p in city_permits:
            date_str = p.get('filing_date', '')
            if date_str:
                try:
                    # Handle various date formats
                    if 'T' in date_str:
                        dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
                    else:
                        dt = datetime.strptime(date_str[:10], '%Y-%m-%d')
                    dates.append(dt)
                except Exception:
                    pass

        if not dates:
            analytics.record_health_check(
                check_type='data_freshness',
                target=city_key,
                status='degraded',
                response_time_ms=0,
                details={'error': 'No valid dates found', 'total_permits': len(city_permits)}
            )
            return {'status': 'degraded', 'details': {'error': 'No valid dates'}}

        newest = max(dates)
        now = datetime.now()
        hours_since_newest = (now - newest.replace(tzinfo=None)).total_seconds() / 3600

        # Count permits in time periods
        day_ago = now - timedelta(days=1)
        week_ago = now - timedelta(days=7)

        permits_24h = sum(1 for d in dates if d.replace(tzinfo=None) >= day_ago)
        permits_7d = sum(1 for d in dates if d.replace(tzinfo=None) >= week_ago)

        # Classify freshness
        if hours_since_newest <= 48:  # Within 2 days
            status = 'ok'
        elif hours_since_newest <= 96:  # 2-4 days
            status = 'degraded'
        else:  # 4+ days
            status = 'down'

        details = {
            'total_permits': len(city_permits),
            'newest_permit': newest.isoformat(),
            'hours_since_newest': round(hours_since_newest, 1),
            'permits_last_24h': permits_24h,
            'permits_last_7d': permits_7d,
        }

        analytics.record_health_check(
            check_type='data_freshness',
            target=city_key,
            status=status,
            response_time_ms=0,
            details=details
        )

        return {'status': status, 'response_time_ms': 0, 'details': details}

    except Exception as e:
        analytics.record_health_check(
            check_type='data_freshness',
            target=city_key,
            status='error',
            response_time_ms=0,
            details={'error': str(e)}
        )
        return {'status': 'error', 'response_time_ms': 0, 'details': {'error': str(e)}}


def check_city_permit_trend(city_key):
    """
    Compare this week's permit count to last week's.
    A big drop (>50% decline) signals a data pipeline problem.
    """
    try:
        permits_file = os.path.join(DATA_DIR, 'permits.json')
        if not os.path.exists(permits_file):
            return {'status': 'error', 'details': {'error': 'permits.json not found'}}

        with open(permits_file) as f:
            permits = json.load(f)

        config = get_city_config(city_key)
        city_name = config.get('name') if config else city_key

        city_permits = [p for p in permits if p.get('city') == city_name]

        now = datetime.now()
        week_ago = now - timedelta(days=7)
        two_weeks_ago = now - timedelta(days=14)

        this_week = 0
        last_week = 0

        for p in city_permits:
            date_str = p.get('filing_date', '')
            if date_str:
                try:
                    if 'T' in date_str:
                        dt = datetime.fromisoformat(date_str.replace('Z', '+00:00')).replace(tzinfo=None)
                    else:
                        dt = datetime.strptime(date_str[:10], '%Y-%m-%d')

                    if dt >= week_ago:
                        this_week += 1
                    elif dt >= two_weeks_ago:
                        last_week += 1
                except Exception:
                    pass

        # Calculate percent change
        if last_week == 0:
            pct_change = 100 if this_week > 0 else 0
        else:
            pct_change = round(((this_week - last_week) / last_week) * 100, 1)

        # Classify trend
        if pct_change >= -20:
            status = 'ok'
        elif pct_change >= -50:
            status = 'degraded'
        else:
            status = 'down'

        details = {
            'permits_this_week': this_week,
            'permits_last_week': last_week,
            'pct_change': pct_change,
        }

        analytics.record_health_check(
            check_type='permit_trend',
            target=city_key,
            status=status,
            response_time_ms=0,
            details=details
        )

        return {'status': status, 'response_time_ms': 0, 'details': details}

    except Exception as e:
        analytics.record_health_check(
            check_type='permit_trend',
            target=city_key,
            status='error',
            response_time_ms=0,
            details={'error': str(e)}
        )
        return {'status': 'error', 'response_time_ms': 0, 'details': {'error': str(e)}}


# ===========================
# ROUTE HEALTH CHECKS
# ===========================

# Routes to check
ROUTES_TO_CHECK = [
    ('/', 200),
    ('/pricing', 200),
    ('/login', 200),
    ('/signup', 200),
    ('/get-alerts', 200),
    ('/contractors', 200),
    ('/early-intel', 200),
    ('/about', 200),
    ('/privacy', 200),
    ('/terms', 200),
    ('/contact', 200),
    ('/blog', 200),
    ('/robots.txt', 200),
    ('/health', 200),
]


def check_route_health(route, expected_status=200):
    """
    Hit a PermitGrab route and verify it returns the expected status.
    """
    try:
        start = time.time()
        resp = requests.get(
            f'{SITE_URL}{route}',
            timeout=15,
            allow_redirects=False,
            headers={'User-Agent': 'PermitGrab-HealthCheck/1.0'}
        )
        elapsed_ms = int((time.time() - start) * 1000)

        # For redirects (301, 302), check if it's expected
        if resp.status_code in (301, 302) and expected_status in (301, 302, 200):
            status = 'ok'
        elif resp.status_code == expected_status:
            status = 'ok'
        else:
            status = 'error'

        # Slow response warning
        if elapsed_ms > 3000:
            status = 'degraded' if status == 'ok' else status

        details = {
            'status_code': resp.status_code,
            'expected': expected_status,
            'content_length': len(resp.content),
        }

        analytics.record_health_check(
            check_type='page_load',
            target=route,
            status=status,
            response_time_ms=elapsed_ms,
            details=details
        )

        return {'status': status, 'response_time_ms': elapsed_ms, 'details': details}

    except requests.Timeout:
        analytics.record_health_check(
            check_type='page_load',
            target=route,
            status='down',
            response_time_ms=15000,
            details={'error': 'timeout'}
        )
        return {'status': 'down', 'response_time_ms': 15000, 'details': {'error': 'timeout'}}

    except Exception as e:
        analytics.record_health_check(
            check_type='page_load',
            target=route,
            status='error',
            response_time_ms=0,
            details={'error': str(e)}
        )
        return {'status': 'error', 'response_time_ms': 0, 'details': {'error': str(e)}}


# ===========================
# THIRD-PARTY SERVICE CHECKS
# ===========================

def check_stripe():
    """Check Stripe API connectivity."""
    start = time.time()
    try:
        import stripe
        stripe.api_key = os.environ.get('STRIPE_SECRET_KEY')

        if not stripe.api_key:
            analytics.record_health_check(
                check_type='service',
                target='stripe',
                status='error',
                response_time_ms=0,
                details={'error': 'STRIPE_SECRET_KEY not configured'}
            )
            return {'status': 'error', 'details': {'error': 'not configured'}}

        # Light read-only call to verify the key works
        stripe.Balance.retrieve()
        elapsed_ms = int((time.time() - start) * 1000)

        analytics.record_health_check(
            check_type='service',
            target='stripe',
            status='ok',
            response_time_ms=elapsed_ms,
            details={}
        )

        return {'status': 'ok', 'response_time_ms': elapsed_ms, 'details': {}}

    except Exception as e:
        elapsed_ms = int((time.time() - start) * 1000)
        analytics.record_health_check(
            check_type='service',
            target='stripe',
            status='error',
            response_time_ms=elapsed_ms,
            details={'error': str(e)}
        )
        return {'status': 'error', 'response_time_ms': elapsed_ms, 'details': {'error': str(e)}}


def check_database():
    """Check local JSON data files are accessible."""
    start = time.time()
    try:
        # Check critical data files exist and are readable
        files_to_check = ['permits.json', 'subscribers.json', 'users.json']
        missing = []

        for filename in files_to_check:
            filepath = os.path.join(DATA_DIR, filename)
            if not os.path.exists(filepath):
                missing.append(filename)
            else:
                # Verify it's valid JSON
                with open(filepath) as f:
                    json.load(f)

        elapsed_ms = int((time.time() - start) * 1000)

        if missing:
            status = 'degraded' if len(missing) < len(files_to_check) else 'error'
            analytics.record_health_check(
                check_type='service',
                target='database',
                status=status,
                response_time_ms=elapsed_ms,
                details={'missing_files': missing}
            )
            return {'status': status, 'response_time_ms': elapsed_ms, 'details': {'missing': missing}}

        status = 'ok' if elapsed_ms < 500 else 'degraded'
        analytics.record_health_check(
            check_type='service',
            target='database',
            status=status,
            response_time_ms=elapsed_ms,
            details={'files_checked': files_to_check}
        )

        return {'status': status, 'response_time_ms': elapsed_ms, 'details': {}}

    except Exception as e:
        elapsed_ms = int((time.time() - start) * 1000)
        analytics.record_health_check(
            check_type='service',
            target='database',
            status='error',
            response_time_ms=elapsed_ms,
            details={'error': str(e)}
        )
        return {'status': 'error', 'response_time_ms': elapsed_ms, 'details': {'error': str(e)}}


# ===========================
# MAIN HEALTH CHECK RUNNER
# ===========================

def run_all_health_checks():
    """
    Run all health checks and record results in analytics.
    Returns a summary of all check results.
    """
    print(f"\n{'='*60}")
    print(f"PermitGrab Health Check - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*60}")

    checks = []
    active_cities = get_active_cities()

    # 1. City API checks
    print("\n[City API Checks]")
    for city_key in active_cities:
        config = get_city_config(city_key)
        print(f"  Testing {config.get('name', city_key)}...", end=" ")
        result = check_city_api(city_key)
        print(f"{result['status'].upper()} ({result['response_time_ms']}ms)")
        checks.append((f'api:{city_key}', result['status']))
        time.sleep(0.3)  # Rate limiting

    # 2. Data freshness checks
    print("\n[Data Freshness Checks]")
    for city_key in active_cities:
        config = get_city_config(city_key)
        print(f"  Checking {config.get('name', city_key)}...", end=" ")
        result = check_city_data_freshness(city_key)
        hours = result.get('details', {}).get('hours_since_newest', '?')
        print(f"{result['status'].upper()} ({hours}h since newest)")
        checks.append((f'freshness:{city_key}', result['status']))

    # 3. Permit trend checks
    print("\n[Permit Trend Checks]")
    for city_key in active_cities:
        config = get_city_config(city_key)
        print(f"  Checking {config.get('name', city_key)}...", end=" ")
        result = check_city_permit_trend(city_key)
        pct = result.get('details', {}).get('pct_change', '?')
        print(f"{result['status'].upper()} ({pct}%)")
        checks.append((f'trend:{city_key}', result['status']))

    # 4. Route health checks (only in production)
    if 'permitgrab.com' in SITE_URL or os.environ.get('RUN_ROUTE_CHECKS') == 'true':
        print("\n[Route Health Checks]")
        for route, expected in ROUTES_TO_CHECK:
            print(f"  Testing {route}...", end=" ")
            result = check_route_health(route, expected)
            print(f"{result['status'].upper()} ({result['response_time_ms']}ms)")
            checks.append((f'route:{route}', result['status']))
            time.sleep(0.2)
    else:
        print("\n[Route Health Checks] Skipped (not production)")

    # 5. Third-party services
    print("\n[Service Checks]")

    print("  Testing Stripe...", end=" ")
    stripe_result = check_stripe()
    print(f"{stripe_result['status'].upper()}")
    checks.append(('service:stripe', stripe_result['status']))

    print("  Testing Database...", end=" ")
    db_result = check_database()
    print(f"{db_result['status'].upper()}")
    checks.append(('service:database', db_result['status']))

    # Summary
    failures = [(name, status) for name, status in checks if status != 'ok']
    ok_count = len([c for c in checks if c[1] == 'ok'])
    degraded_count = len([c for c in checks if c[1] == 'degraded'])
    error_count = len([c for c in checks if c[1] in ('error', 'down')])

    print(f"\n{'='*60}")
    print("HEALTH CHECK COMPLETE")
    print(f"{'='*60}")
    print(f"OK: {ok_count} | Degraded: {degraded_count} | Errors: {error_count}")

    if failures:
        print("\nFAILURES:")
        for name, status in failures:
            print(f"  {status.upper()}: {name}")

        # Send alert email for failures
        send_health_alert(failures)

    return {
        'checked_at': datetime.now().isoformat(),
        'total': len(checks),
        'ok': ok_count,
        'degraded': degraded_count,
        'errors': error_count,
        'failures': failures,
    }


def send_health_alert(failures):
    """Send an email alert when health checks fail."""
    try:
        from email_alerts import send_email

        subject = f"PermitGrab Health Alert - {len(failures)} issue(s)"

        body = f'''
        <!DOCTYPE html>
        <html>
        <head><meta charset="UTF-8"></head>
        <body style="font-family: -apple-system, sans-serif; padding: 20px;">
            <h2 style="color: #dc2626;">Health Check Alert</h2>
            <p>The following health checks are failing:</p>
            <table style="border-collapse: collapse; margin: 20px 0;">
                {''.join(f'''
                <tr>
                    <td style="padding: 8px; border: 1px solid #e5e7eb; font-weight: bold; color: {"#dc2626" if s in ("error", "down") else "#f97316"};">{s.upper()}</td>
                    <td style="padding: 8px; border: 1px solid #e5e7eb;">{n}</td>
                </tr>
                ''' for n, s in failures)}
            </table>
            <p><a href="{SITE_URL}/admin/analytics#health" style="color: #2563eb;">View Full Dashboard</a></p>
            <p style="color: #6b7280; font-size: 12px;">Timestamp: {datetime.now().isoformat()}</p>
        </body>
        </html>
        '''

        admin_email = os.environ.get('ADMIN_EMAIL', 'wcrainshaw@gmail.com')
        send_email(admin_email, subject, body)
        print(f"Alert email sent to {admin_email}")

    except Exception as e:
        print(f"Failed to send alert email: {e}")


if __name__ == '__main__':
    run_all_health_checks()
