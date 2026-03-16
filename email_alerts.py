"""
PermitGrab — Email Alert System
Sends daily/weekly permit digest emails to subscribers
Uses free SMTP (Gmail, SendGrid free tier, or Mailgun free tier)
"""

import json
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')

# Configure with your SMTP provider
# Gmail: smtp.gmail.com:587 (use App Password, not regular password)
# SendGrid: smtp.sendgrid.net:587 (free tier: 100 emails/day)
# Mailgun: smtp.mailgun.org:587 (free tier: limited)
SMTP_HOST = os.environ.get('SMTP_HOST', 'smtp.gmail.com')
SMTP_PORT = int(os.environ.get('SMTP_PORT', '587'))
SMTP_USER = os.environ.get('SMTP_USER', '')
SMTP_PASS = os.environ.get('SMTP_PASS', '')
FROM_EMAIL = os.environ.get('FROM_EMAIL', 'alerts@permitgrab.com')
SITE_URL = os.environ.get('SITE_URL', 'https://permitgrab.com')


def load_permits():
    path = os.path.join(DATA_DIR, 'permits.json')
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return []


def load_subscribers():
    path = os.path.join(DATA_DIR, 'subscribers.json')
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return []


def get_matching_permits(subscriber, days_back=1):
    """Get permits matching subscriber's city and trade preferences."""
    permits = load_permits()
    since = (datetime.now() - timedelta(days=days_back)).strftime('%Y-%m-%d')

    matches = []
    for p in permits:
        # Date filter
        if p.get('filing_date', '') < since:
            continue

        # City filter
        if subscriber.get('city') and p.get('city') != subscriber['city']:
            continue

        # Trade filter
        if subscriber.get('trade') and p.get('trade_category') != subscriber['trade']:
            continue

        matches.append(p)

    # Sort by value (highest first)
    matches.sort(key=lambda x: x.get('estimated_cost', 0), reverse=True)

    return matches


def build_email_html(subscriber, permits):
    """Build a clean HTML email with permit leads."""
    name = subscriber.get('name', 'there')
    city = subscriber.get('city', 'your area')
    trade = subscriber.get('trade', 'all trades')

    # Value summary
    total_value = sum(p.get('estimated_cost', 0) for p in permits)
    high_value = len([p for p in permits if p.get('value_tier') == 'high'])

    permit_rows = ''
    for p in permits[:20]:  # Limit to 20 per email
        cost = f"${p.get('estimated_cost', 0):,.0f}" if p.get('estimated_cost') else 'N/A'
        value_color = '#dc2626' if p.get('value_tier') == 'high' else '#f97316' if p.get('value_tier') == 'medium' else '#6b7280'

        # Only show contact info for paid subscribers
        contact_section = ''
        if subscriber.get('plan') in ('professional', 'enterprise'):
            if p.get('contact_name'):
                contact_section += f'<div style="font-size:13px;color:#4b5563;">Contact: {p["contact_name"]}</div>'
            if p.get('contact_phone'):
                contact_section += f'<div style="font-size:13px;color:#2563eb;">Phone: {p["contact_phone"]}</div>'
        else:
            contact_section = '<div style="font-size:12px;color:#9ca3af;font-style:italic;">Upgrade to Pro to see contact info</div>'

        permit_rows += f'''
        <tr>
          <td style="padding:16px;border-bottom:1px solid #e5e7eb;">
            <div style="display:flex;justify-content:space-between;margin-bottom:4px;">
              <span style="font-size:12px;font-weight:600;padding:2px 8px;border-radius:12px;background:#dbeafe;color:#1e40af;">{p.get('trade_category', 'Other')}</span>
              <span style="font-weight:700;color:{value_color};">{cost}</span>
            </div>
            <div style="font-weight:600;font-size:15px;color:#111827;margin:4px 0;">{p.get('address', 'N/A')}</div>
            <div style="font-size:13px;color:#6b7280;margin-bottom:4px;">{p.get('city', '')}, {p.get('state', '')} {p.get('zip', '')}</div>
            <div style="font-size:14px;color:#4b5563;margin-bottom:8px;">{p.get('description', '')[:150]}</div>
            <div style="font-size:12px;color:#9ca3af;">Filed: {p.get('filing_date', 'N/A')} · Status: {p.get('status', 'N/A')} · #{p.get('permit_number', '')}</div>
            {contact_section}
          </td>
        </tr>'''

    html = f'''
    <!DOCTYPE html>
    <html>
    <head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"></head>
    <body style="margin:0;padding:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#f3f4f6;">
      <div style="max-width:600px;margin:0 auto;background:white;">

        <!-- Header -->
        <div style="background:linear-gradient(135deg,#111827,#1e3a5f);padding:24px 32px;text-align:center;">
          <div style="font-size:22px;font-weight:700;color:white;">Permit<span style="color:#f97316;">Flow</span></div>
          <div style="font-size:14px;color:rgba(255,255,255,.6);margin-top:4px;">Your Daily Permit Digest</div>
        </div>

        <!-- Summary -->
        <div style="padding:24px 32px;background:#f9fafb;border-bottom:1px solid #e5e7eb;">
          <div style="font-size:16px;color:#374151;">Hey {name},</div>
          <div style="font-size:14px;color:#6b7280;margin-top:8px;">
            We found <strong style="color:#111827;">{len(permits)} new permits</strong> matching your preferences
            ({city} · {trade}).
            {f'Including <strong style="color:#dc2626;">{high_value} high-value leads</strong> worth a total of <strong>${total_value:,.0f}</strong>.' if high_value > 0 else f'Total project value: <strong>${total_value:,.0f}</strong>.'}
          </div>
        </div>

        <!-- Permits -->
        <table style="width:100%;border-collapse:collapse;">
          {permit_rows}
        </table>

        {f'<div style="padding:16px 32px;text-align:center;font-size:13px;color:#9ca3af;">Showing top 20 of {len(permits)} matching permits</div>' if len(permits) > 20 else ''}

        <!-- CTA -->
        <div style="padding:24px 32px;text-align:center;background:#f9fafb;">
          <a href="{SITE_URL}" style="display:inline-block;padding:12px 32px;background:#2563eb;color:white;text-decoration:none;border-radius:6px;font-weight:600;font-size:15px;">View All Permits</a>
          <div style="margin-top:12px;font-size:13px;color:#9ca3af;">
            <a href="{SITE_URL}/#pricing" style="color:#f97316;text-decoration:none;">Upgrade to Pro</a> for contact info + phone numbers on every lead
          </div>
        </div>

        <!-- Footer -->
        <div style="padding:20px 32px;text-align:center;font-size:12px;color:#9ca3af;border-top:1px solid #e5e7eb;">
          <p>PermitGrab · Construction Permit Leads</p>
          <p><a href="#" style="color:#9ca3af;">Unsubscribe</a> · <a href="#" style="color:#9ca3af;">Manage Preferences</a></p>
        </div>
      </div>
    </body>
    </html>'''

    return html


def send_email(to_email, subject, html_body):
    """Send an email via SMTP."""
    if not SMTP_USER or not SMTP_PASS:
        print(f"  [DRY RUN] Would send to {to_email}: {subject}")
        return True

    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = FROM_EMAIL
    msg['To'] = to_email

    # Plain text fallback
    text_part = MIMEText("View this email in HTML for the best experience.", 'plain')
    html_part = MIMEText(html_body, 'html')

    msg.attach(text_part)
    msg.attach(html_part)

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(FROM_EMAIL, to_email, msg.as_string())
        print(f"  ✓ Sent to {to_email}")
        return True
    except Exception as e:
        print(f"  ✗ Failed to send to {to_email}: {e}")
        return False


def send_daily_digest():
    """Send daily permit digest to all active subscribers."""
    subscribers = load_subscribers()
    active = [s for s in subscribers if s.get('active', True)]

    print(f"\n{'='*50}")
    print(f"PermitGrab Daily Digest - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"Sending to {len(active)} active subscribers")
    print(f"{'='*50}")

    sent = 0
    failed = 0

    for sub in active:
        permits = get_matching_permits(sub, days_back=1)

        if not permits:
            print(f"  [SKIP] {sub['email']} - no matching permits today")
            continue

        subject = f"🏗️ {len(permits)} New Permit Leads in {sub.get('city', 'Your Area')}"
        html = build_email_html(sub, permits)

        if send_email(sub['email'], subject, html):
            sent += 1
        else:
            failed += 1

    print(f"\nDone: {sent} sent, {failed} failed, {len(active) - sent - failed} skipped")
    return sent, failed


def send_test_email(to_email, city='New York City', trade=''):
    """Send a test digest email."""
    test_sub = {
        'email': to_email,
        'name': 'Test User',
        'city': city,
        'trade': trade,
        'plan': 'professional',  # Show contact info in test
    }

    permits = get_matching_permits(test_sub, days_back=60)[:15]

    if not permits:
        print("No permits found for test email")
        return

    subject = f"🏗️ [TEST] {len(permits)} Permit Leads in {city}"
    html = build_email_html(test_sub, permits)

    # Save test email as HTML file for preview
    preview_path = os.path.join(DATA_DIR, 'test_email_preview.html')
    with open(preview_path, 'w') as f:
        f.write(html)
    print(f"Test email preview saved to: {preview_path}")

    send_email(to_email, subject, html)


if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == 'test':
        email = sys.argv[2] if len(sys.argv) > 2 else 'test@example.com'
        send_test_email(email)
    else:
        send_daily_digest()
