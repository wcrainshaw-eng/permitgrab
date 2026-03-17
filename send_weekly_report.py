"""
PermitGrab — Weekly Metrics Report
Sends a weekly summary email to admin every Monday at 8am.

Run manually: python send_weekly_report.py
Schedule: Render Cron Job or APScheduler every Monday at 8:00 AM
"""

import os
import sys
from datetime import datetime, timedelta

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(__file__))

import analytics
from email_alerts import send_email, FROM_EMAIL

ADMIN_EMAIL = os.environ.get('ADMIN_EMAIL', 'wcrainshaw@gmail.com')
SITE_URL = os.environ.get('SITE_URL', 'https://permitgrab.com')


def get_week_metrics(days_ago_start, days_ago_end):
    """Get metrics for a specific week period."""
    events = analytics.load_user_events()
    start = (datetime.now() - timedelta(days=days_ago_start)).isoformat()
    end = (datetime.now() - timedelta(days=days_ago_end)).isoformat()

    # Filter events for the period
    period_events = [e for e in events if start <= e.get('created_at', '') <= end]

    # Count unique visitors (sessions with page_view)
    visitors = set()
    for e in period_events:
        if e.get('event_type') == 'page_view':
            visitors.add(e.get('session_id'))

    # Count signups
    signups = sum(1 for e in period_events if e.get('event_type') == 'signup')

    # Count active users (logged in users)
    active_users = set()
    for e in period_events:
        if e.get('user_id'):
            active_users.add(e.get('user_id'))

    # Count trial starts
    trials = sum(1 for e in period_events if e.get('event_type') == 'checkout_started')

    # Count payments
    payments = sum(1 for e in period_events if e.get('event_type') == 'payment_success')

    return {
        'visitors': len(visitors),
        'signups': signups,
        'active_users': len(active_users),
        'trials': trials,
        'payments': payments,
    }


def get_top_pages_for_week():
    """Get top pages for the last 7 days."""
    return analytics.get_top_pages(days=7, limit=5)


def get_funnel_rates():
    """Get conversion funnel percentages."""
    funnel = analytics.get_conversion_funnel(days=7)

    # Build a dict of step -> count
    steps = {step['step']: step['count'] for step in funnel}

    visitors = steps.get('Visitors', 0)
    signups = steps.get('Signups Completed', 0)
    onboarding = steps.get('Onboarding Completed', 0)
    checkout = steps.get('Checkout Started', 0)
    paid = steps.get('Payment Success', 0)

    return {
        'visitors_to_signup': round(signups / visitors * 100, 1) if visitors else 0,
        'signup_to_onboarding': round(onboarding / signups * 100, 1) if signups else 0,
        'onboarding_to_trial': round(checkout / onboarding * 100, 1) if onboarding else 0,
        'trial_to_paid': round(paid / checkout * 100, 1) if checkout else 0,
    }


def pct_change(current, previous):
    """Calculate percentage change with arrow indicator."""
    if previous == 0:
        if current > 0:
            return "+100%"
        return "0%"
    change = round((current - previous) / previous * 100, 0)
    arrow = "+" if change >= 0 else ""
    return f"{arrow}{int(change)}%"


def build_weekly_report_html():
    """Build the weekly metrics report email."""
    # Get this week vs last week
    this_week = get_week_metrics(7, 0)
    last_week = get_week_metrics(14, 7)

    # Top pages
    top_pages = get_top_pages_for_week()

    # Funnel rates
    funnel = get_funnel_rates()

    # Email performance
    email_7d = analytics.get_email_performance(7)

    # Health data
    health_summary = analytics.get_health_summary_for_report()

    # Date range for subject
    end_date = datetime.now().strftime('%b %d')
    start_date = (datetime.now() - timedelta(days=7)).strftime('%b %d')
    year = datetime.now().year

    # Build HTML
    html = f'''
    <!DOCTYPE html>
    <html>
    <head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"></head>
    <body style="margin:0;padding:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#f3f4f6;">
      <div style="max-width:600px;margin:0 auto;background:white;">

        <!-- Header -->
        <div style="background:#111827;padding:24px 32px;text-align:center;">
          <div style="font-size:22px;font-weight:700;color:white;">Permit<span style="color:#f97316;">Grab</span> Weekly Report</div>
          <div style="font-size:14px;color:rgba(255,255,255,.6);margin-top:4px;">{start_date} - {end_date}, {year}</div>
        </div>

        <!-- Key Metrics -->
        <div style="padding:24px 32px;">
          <h2 style="margin:0 0 16px;font-size:18px;color:#111827;">Key Metrics</h2>
          <table style="width:100%;border-collapse:collapse;">
            <tr>
              <td style="padding:12px 0;border-bottom:1px solid #e5e7eb;">
                <span style="color:#6b7280;">Visitors</span>
              </td>
              <td style="padding:12px 0;border-bottom:1px solid #e5e7eb;text-align:right;">
                <strong style="font-size:18px;color:#111827;">{this_week['visitors']:,}</strong>
                <span style="font-size:13px;color:{'#16a34a' if this_week['visitors'] >= last_week['visitors'] else '#dc2626'};margin-left:8px;">
                  {pct_change(this_week['visitors'], last_week['visitors'])}
                </span>
              </td>
            </tr>
            <tr>
              <td style="padding:12px 0;border-bottom:1px solid #e5e7eb;">
                <span style="color:#6b7280;">Signups</span>
              </td>
              <td style="padding:12px 0;border-bottom:1px solid #e5e7eb;text-align:right;">
                <strong style="font-size:18px;color:#111827;">{this_week['signups']}</strong>
                <span style="font-size:13px;color:{'#16a34a' if this_week['signups'] >= last_week['signups'] else '#dc2626'};margin-left:8px;">
                  {pct_change(this_week['signups'], last_week['signups'])}
                </span>
              </td>
            </tr>
            <tr>
              <td style="padding:12px 0;border-bottom:1px solid #e5e7eb;">
                <span style="color:#6b7280;">Active Users</span>
              </td>
              <td style="padding:12px 0;border-bottom:1px solid #e5e7eb;text-align:right;">
                <strong style="font-size:18px;color:#111827;">{this_week['active_users']}</strong>
                <span style="font-size:13px;color:{'#16a34a' if this_week['active_users'] >= last_week['active_users'] else '#dc2626'};margin-left:8px;">
                  {pct_change(this_week['active_users'], last_week['active_users'])}
                </span>
              </td>
            </tr>
            <tr>
              <td style="padding:12px 0;border-bottom:1px solid #e5e7eb;">
                <span style="color:#6b7280;">Trial Starts</span>
              </td>
              <td style="padding:12px 0;border-bottom:1px solid #e5e7eb;text-align:right;">
                <strong style="font-size:18px;color:#111827;">{this_week['trials']}</strong>
                <span style="font-size:13px;color:{'#16a34a' if this_week['trials'] >= last_week['trials'] else '#dc2626'};margin-left:8px;">
                  {pct_change(this_week['trials'], last_week['trials'])}
                </span>
              </td>
            </tr>
            <tr>
              <td style="padding:12px 0;">
                <span style="color:#6b7280;">Payments</span>
              </td>
              <td style="padding:12px 0;text-align:right;">
                <strong style="font-size:18px;color:#111827;">{this_week['payments']}</strong>
                <span style="font-size:13px;color:{'#16a34a' if this_week['payments'] >= last_week['payments'] else '#dc2626'};margin-left:8px;">
                  {pct_change(this_week['payments'], last_week['payments'])}
                </span>
              </td>
            </tr>
          </table>
        </div>

        <!-- Top Pages -->
        <div style="padding:0 32px 24px;">
          <h2 style="margin:0 0 16px;font-size:18px;color:#111827;">Top Pages</h2>
          <table style="width:100%;border-collapse:collapse;font-size:14px;">
            {''.join(f'''
            <tr>
              <td style="padding:8px 0;color:#374151;">{i+1}. {page['page']}</td>
              <td style="padding:8px 0;text-align:right;color:#6b7280;">{page['views']:,} views</td>
            </tr>
            ''' for i, page in enumerate(top_pages[:5]))}
          </table>
        </div>

        <!-- Conversion Funnel -->
        <div style="padding:0 32px 24px;">
          <h2 style="margin:0 0 16px;font-size:18px;color:#111827;">Conversion Funnel</h2>
          <table style="width:100%;border-collapse:collapse;font-size:14px;">
            <tr>
              <td style="padding:8px 0;color:#6b7280;">Visitors to Signup</td>
              <td style="padding:8px 0;text-align:right;font-weight:600;color:#111827;">{funnel['visitors_to_signup']}%</td>
            </tr>
            <tr>
              <td style="padding:8px 0;color:#6b7280;">Signup to Onboarding</td>
              <td style="padding:8px 0;text-align:right;font-weight:600;color:#111827;">{funnel['signup_to_onboarding']}%</td>
            </tr>
            <tr>
              <td style="padding:8px 0;color:#6b7280;">Onboarding to Trial</td>
              <td style="padding:8px 0;text-align:right;font-weight:600;color:#111827;">{funnel['onboarding_to_trial']}%</td>
            </tr>
            <tr>
              <td style="padding:8px 0;color:#6b7280;">Trial to Paid</td>
              <td style="padding:8px 0;text-align:right;font-weight:600;color:#111827;">{funnel['trial_to_paid']}%</td>
            </tr>
          </table>
        </div>

        <!-- Email Performance -->
        <div style="padding:0 32px 24px;">
          <h2 style="margin:0 0 16px;font-size:18px;color:#111827;">Email Performance (7 Days)</h2>
          <table style="width:100%;border-collapse:collapse;font-size:14px;">
            <tr>
              <td style="padding:8px 0;color:#6b7280;">Delivered</td>
              <td style="padding:8px 0;text-align:right;color:#111827;">{email_7d['delivered']}</td>
            </tr>
            <tr>
              <td style="padding:8px 0;color:#6b7280;">Open Rate</td>
              <td style="padding:8px 0;text-align:right;color:#111827;">{email_7d['open_rate']}%</td>
            </tr>
            <tr>
              <td style="padding:8px 0;color:#6b7280;">Click Rate</td>
              <td style="padding:8px 0;text-align:right;color:#111827;">{email_7d['click_rate']}%</td>
            </tr>
          </table>
        </div>

        <!-- System Uptime -->
        <div style="padding:0 32px 24px;">
          <h2 style="margin:0 0 16px;font-size:18px;color:#111827;">System Health (7 Days)</h2>
          <div style="display:flex;gap:20px;margin-bottom:16px;">
            <div style="background:#f3f4f6;padding:12px 20px;border-radius:8px;">
              <div style="font-size:24px;font-weight:700;color:#111827;">{health_summary['uptime_pct']}%</div>
              <div style="font-size:12px;color:#6b7280;">Uptime</div>
            </div>
            <div style="background:#f3f4f6;padding:12px 20px;border-radius:8px;">
              <div style="font-size:24px;font-weight:700;color:{'#16a34a' if health_summary['total_failures_7d'] == 0 else '#dc2626'};">{health_summary['total_failures_7d']}</div>
              <div style="font-size:12px;color:#6b7280;">Failures</div>
            </div>
          </div>
        </div>

        <!-- City Data Health -->
        <div style="padding:0 32px 24px;">
          <h2 style="margin:0 0 16px;font-size:18px;color:#111827;">City Data Health</h2>
          <table style="width:100%;border-collapse:collapse;font-size:14px;">
            {''.join(f"""
            <tr>
              <td style="padding:8px 0;color:#374151;">
                <span style="color:{'#16a34a' if city['overall_status'] == 'ok' else '#f97316' if city['overall_status'] == 'degraded' else '#dc2626'};">&#9679;</span>
                {city.get('city_name', city['city'])}
              </td>
              <td style="padding:8px 0;text-align:right;color:#6b7280;">
                {city['permits_7d']} permits
                {f"({'+' if city['pct_change'] and city['pct_change'] >= 0 else ''}{city['pct_change']}%)" if city['pct_change'] is not None else ''}
              </td>
            </tr>
            """ for city in health_summary['city_health'][:8])}
          </table>
        </div>

        {f"""
        <!-- Top Failures -->
        <div style="padding:0 32px 24px;">
          <h2 style="margin:0 0 16px;font-size:18px;color:#111827;">Issues This Week</h2>
          <ul style="margin:0;padding-left:20px;font-size:14px;color:#6b7280;">
            {''.join(f'<li style="margin-bottom:4px;">{count}x {check}</li>' for check, count in health_summary['top_failures'])}
          </ul>
        </div>
        """ if health_summary['top_failures'] else ''}

        <!-- CTA -->
        <div style="padding:24px 32px;text-align:center;background:#f9fafb;">
          <a href="{SITE_URL}/admin/analytics" style="display:inline-block;padding:12px 32px;background:#2563eb;color:white;text-decoration:none;border-radius:6px;font-weight:600;font-size:15px;">View Full Dashboard</a>
        </div>

        <!-- Footer -->
        <div style="padding:20px 32px;text-align:center;font-size:12px;color:#9ca3af;border-top:1px solid #e5e7eb;">
          <p>PermitGrab Weekly Report</p>
          <p>Sent automatically every Monday at 8:00 AM</p>
        </div>
      </div>
    </body>
    </html>'''

    return html


def send_weekly_report():
    """Send the weekly metrics report to admin."""
    print(f"\n{'='*50}")
    print(f"PermitGrab Weekly Report - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*50}")

    # Build email
    html = build_weekly_report_html()

    # Date range for subject
    end_date = datetime.now().strftime('%b %d')
    start_date = (datetime.now() - timedelta(days=7)).strftime('%b %d')
    year = datetime.now().year

    subject = f"PermitGrab Weekly Report — {start_date}-{end_date}, {year}"

    # Send to admin
    print(f"Sending to {ADMIN_EMAIL}...")
    success = send_email(ADMIN_EMAIL, subject, html)

    if success:
        print("Weekly report sent successfully!")
    else:
        print("Failed to send weekly report.")

    return success


if __name__ == '__main__':
    send_weekly_report()
