"""
PermitGrab V12.53 — Complete Email System
Sends welcome emails, daily digests, trial lifecycle emails, and transactional emails.
Uses SendGrid SMTP and queries SQLite for permit data.
"""

import json
import os
import smtplib
import secrets
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta

# Import permitdb for SQLite access
import db as permitdb

# SendGrid SMTP configuration
SMTP_HOST = os.environ.get('SMTP_HOST', 'smtp.sendgrid.net')
SMTP_PORT = int(os.environ.get('SMTP_PORT', '587'))
SMTP_USER = os.environ.get('SMTP_USER', 'apikey')
SMTP_PASS = os.environ.get('SMTP_PASS', '')
FROM_EMAIL = os.environ.get('FROM_EMAIL', 'alerts@permitgrab.com')
SITE_URL = os.environ.get('SITE_URL', 'https://permitgrab.com')


# =============================================================================
# EMAIL SENDING CORE
# =============================================================================

def send_email(to_email, subject, html_body, text_body=None):
    """Send an email via SendGrid SMTP."""
    if not SMTP_PASS:
        print(f"  [DRY RUN] Would send to {to_email}: {subject}")
        return True

    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = FROM_EMAIL
    msg['To'] = to_email

    # Plain text fallback
    if not text_body:
        text_body = "View this email in HTML for the best experience."
    text_part = MIMEText(text_body, 'plain')
    html_part = MIMEText(html_body, 'html')

    msg.attach(text_part)
    msg.attach(html_part)

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(FROM_EMAIL, to_email, msg.as_string())
        print(f"  ✓ Sent to {to_email}: {subject}")
        return True
    except Exception as e:
        print(f"  ✗ Failed to send to {to_email}: {e}")
        return False


def generate_token():
    """Generate a secure random token for unsubscribe/verification links."""
    return secrets.token_urlsafe(32)


# =============================================================================
# BASE EMAIL TEMPLATE
# =============================================================================

def base_template(content, preheader="", show_upgrade_cta=False, unsubscribe_token=None):
    """
    Base HTML email template.
    All emails share this wrapper for consistent branding.
    """
    upgrade_section = ""
    if show_upgrade_cta:
        upgrade_section = f'''
        <div style="padding:20px 32px;text-align:center;background:#fef3c7;border-top:1px solid #fcd34d;">
          <div style="font-size:14px;color:#92400e;margin-bottom:8px;">
            <strong>Unlock full contractor contact info, CSV export, and more</strong>
          </div>
          <a href="{SITE_URL}/pricing" style="display:inline-block;padding:10px 24px;background:#f97316;color:white;text-decoration:none;border-radius:6px;font-weight:600;font-size:14px;">Start Free Professional Trial</a>
        </div>'''

    unsubscribe_link = ""
    if unsubscribe_token:
        unsubscribe_link = f'<a href="{SITE_URL}/api/unsubscribe?token={unsubscribe_token}" style="color:#9ca3af;">Unsubscribe from digest</a> · '

    return f'''<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1.0">
  <meta name="x-apple-disable-message-reformatting">
  <!--[if mso]><style>table,td,div,p{{font-family:Arial,sans-serif!important}}</style><![endif]-->
</head>
<body style="margin:0;padding:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;background:#f3f4f6;-webkit-font-smoothing:antialiased;">
  <!-- Preheader text (hidden) -->
  <div style="display:none;font-size:1px;color:#f3f4f6;line-height:1px;max-height:0px;max-width:0px;opacity:0;overflow:hidden;">
    {preheader}
  </div>

  <div style="max-width:600px;margin:0 auto;background:white;">
    <!-- Header -->
    <div style="background:linear-gradient(135deg,#111827,#1e3a5f);padding:24px 32px;text-align:center;">
      <a href="{SITE_URL}" style="text-decoration:none;">
        <div style="font-size:24px;font-weight:700;color:white;">Permit<span style="color:#f97316;">Grab</span></div>
      </a>
    </div>

    <!-- Main Content -->
    {content}

    {upgrade_section}

    <!-- Footer -->
    <div style="padding:24px 32px;text-align:center;font-size:12px;color:#9ca3af;border-top:1px solid #e5e7eb;background:#f9fafb;">
      <p style="margin:0 0 8px 0;">
        {unsubscribe_link}<a href="{SITE_URL}/account" style="color:#9ca3af;">Manage Preferences</a>
      </p>
      <p style="margin:0;color:#d1d5db;">
        PermitGrab · Construction Permit Intelligence<br>
        © {datetime.now().year} PermitGrab. All rights reserved.
      </p>
    </div>
  </div>
</body>
</html>'''


# =============================================================================
# EMAIL #1 — WELCOME (FREE)
# =============================================================================

def send_welcome_free(user):
    """Send welcome email to new free user."""
    name = user.name or 'there'

    content = f'''
    <div style="padding:32px;">
      <h1 style="margin:0 0 16px 0;font-size:24px;color:#111827;">Welcome to PermitGrab, {name}!</h1>

      <p style="font-size:16px;color:#4b5563;line-height:1.6;margin:0 0 24px 0;">
        You now have access to real-time building permit data from <strong>556+ cities</strong> across the US.
        Start finding quality contractor leads today.
      </p>

      <div style="background:#f9fafb;border-radius:8px;padding:20px;margin-bottom:24px;">
        <h3 style="margin:0 0 12px 0;font-size:16px;color:#111827;">What you can do on the Free plan:</h3>
        <ul style="margin:0;padding-left:20px;color:#4b5563;line-height:1.8;">
          <li>Browse all permits across all cities</li>
          <li>Filter by trade, value tier, and status</li>
          <li>See permit values and project descriptions</li>
          <li>Receive limited daily digest emails</li>
        </ul>
      </div>

      <div style="text-align:center;margin-bottom:24px;">
        <a href="{SITE_URL}/dashboard" style="display:inline-block;padding:14px 32px;background:#2563eb;color:white;text-decoration:none;border-radius:8px;font-weight:600;font-size:16px;">
          Explore Permits Now
        </a>
      </div>

      <div style="background:#fef3c7;border-radius:8px;padding:16px;border-left:4px solid #f97316;">
        <p style="margin:0;font-size:14px;color:#92400e;">
          <strong>Want full contractor contact info?</strong><br>
          Start a free 14-day Professional trial — no credit card required.
          <a href="{SITE_URL}/pricing" style="color:#ea580c;font-weight:600;">Upgrade now →</a>
        </p>
      </div>
    </div>'''

    html = base_template(content, preheader=f"Welcome to PermitGrab! Start finding contractor leads.")

    return send_email(
        user.email,
        "Welcome to PermitGrab — your permit intel starts now",
        html
    )


# =============================================================================
# EMAIL #2 — WELCOME (PRO TRIAL)
# =============================================================================

def send_welcome_pro_trial(user):
    """Send welcome email to new Pro trial user."""
    name = user.name or 'there'
    trial_end = user.trial_end_date.strftime('%B %d, %Y') if user.trial_end_date else '14 days from now'

    content = f'''
    <div style="padding:32px;">
      <div style="text-align:center;margin-bottom:24px;">
        <div style="display:inline-block;background:#dcfce7;color:#166534;padding:6px 16px;border-radius:20px;font-size:14px;font-weight:600;">
          🎉 Professional Trial Activated
        </div>
      </div>

      <h1 style="margin:0 0 16px 0;font-size:24px;color:#111827;text-align:center;">
        Welcome to PermitGrab Professional, {name}!
      </h1>

      <p style="font-size:16px;color:#4b5563;line-height:1.6;margin:0 0 24px 0;text-align:center;">
        Your 14-day free trial is now active. Here's everything you just unlocked:
      </p>

      <div style="background:#f0fdf4;border-radius:8px;padding:20px;margin-bottom:24px;border:1px solid #bbf7d0;">
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">
          <div style="display:flex;align-items:center;gap:8px;">
            <span style="color:#22c55e;">✓</span>
            <span style="color:#166534;">Full contractor contact info</span>
          </div>
          <div style="display:flex;align-items:center;gap:8px;">
            <span style="color:#22c55e;">✓</span>
            <span style="color:#166534;">Phone numbers & emails</span>
          </div>
          <div style="display:flex;align-items:center;gap:8px;">
            <span style="color:#22c55e;">✓</span>
            <span style="color:#166534;">Daily digest (25 permits)</span>
          </div>
          <div style="display:flex;align-items:center;gap:8px;">
            <span style="color:#22c55e;">✓</span>
            <span style="color:#166534;">CSV export</span>
          </div>
          <div style="display:flex;align-items:center;gap:8px;">
            <span style="color:#22c55e;">✓</span>
            <span style="color:#166534;">Save leads & notes</span>
          </div>
          <div style="display:flex;align-items:center;gap:8px;">
            <span style="color:#22c55e;">✓</span>
            <span style="color:#166534;">Contractor intelligence</span>
          </div>
        </div>
      </div>

      <p style="font-size:14px;color:#6b7280;text-align:center;margin-bottom:24px;">
        Your trial runs through <strong>{trial_end}</strong>. No credit card required.
      </p>

      <div style="text-align:center;margin-bottom:24px;">
        <a href="{SITE_URL}/dashboard" style="display:inline-block;padding:14px 32px;background:#2563eb;color:white;text-decoration:none;border-radius:8px;font-weight:600;font-size:16px;">
          Go to Your Dashboard
        </a>
      </div>

      <div style="text-align:center;">
        <a href="{SITE_URL}/account" style="color:#2563eb;font-size:14px;text-decoration:none;">
          Set up your daily digest cities →
        </a>
      </div>
    </div>'''

    html = base_template(content, preheader=f"Your 14-day Professional trial is live!")

    return send_email(
        user.email,
        "Your 14-day Professional trial is live — here's everything you unlocked",
        html
    )


# =============================================================================
# EMAIL #5/#6 — DAILY DIGEST (FREE/PRO)
# =============================================================================

def get_permits_for_digest(cities, since_date, limit=25):
    """
    Get recent permits for a user's digest from SQLite.
    Returns permits filed since since_date in the specified cities.
    """
    if not cities:
        return []

    conn = permitdb.get_connection()

    # Build city filter
    placeholders = ','.join('?' * len(cities))
    query = f"""
        SELECT * FROM permits
        WHERE city IN ({placeholders})
        AND (filing_date >= ? OR collected_at >= ?)
        ORDER BY estimated_cost DESC
        LIMIT ?
    """
    params = list(cities) + [since_date, since_date, limit]

    cursor = conn.execute(query, params)
    return [dict(row) for row in cursor]


def build_digest_html(user, permits, is_pro=False):
    """Build the daily digest email HTML."""
    name = user.name or 'there'
    cities = json.loads(user.digest_cities or '[]')
    city_display = ', '.join(cities[:3]) + ('...' if len(cities) > 3 else '') if cities else 'your cities'

    # Calculate stats
    total_value = sum(p.get('estimated_cost', 0) or 0 for p in permits)
    high_value = len([p for p in permits if (p.get('estimated_cost') or 0) >= 100000])

    # Build permit rows
    permit_limit = 25 if is_pro else 10
    permit_rows = ''

    for p in permits[:permit_limit]:
        cost = f"${p.get('estimated_cost', 0):,.0f}" if p.get('estimated_cost') else 'N/A'
        value_color = '#dc2626' if (p.get('estimated_cost') or 0) >= 100000 else '#f97316' if (p.get('estimated_cost') or 0) >= 25000 else '#6b7280'

        # Contact info - visible for Pro, blurred for Free
        if is_pro:
            contact_section = ''
            if p.get('contact_name'):
                contact_section += f'<div style="font-size:13px;color:#111827;font-weight:500;">📞 {p["contact_name"]}</div>'
            if p.get('contact_phone'):
                contact_section += f'<div style="font-size:13px;color:#2563eb;">{p["contact_phone"]}</div>'
            if p.get('contact_email'):
                contact_section += f'<div style="font-size:13px;color:#2563eb;">{p["contact_email"]}</div>'
        else:
            contact_section = '''
            <div style="font-size:12px;color:#9ca3af;font-style:italic;background:#f3f4f6;padding:8px;border-radius:4px;margin-top:8px;">
              🔒 Contact info hidden — <a href="''' + SITE_URL + '''/pricing" style="color:#f97316;">Upgrade to Pro</a>
            </div>'''

        permit_rows += f'''
        <div style="padding:16px;border-bottom:1px solid #e5e7eb;">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
            <span style="font-size:11px;font-weight:600;padding:3px 10px;border-radius:12px;background:#dbeafe;color:#1e40af;">{p.get('trade_category', 'Other')}</span>
            <span style="font-weight:700;font-size:16px;color:{value_color};">{cost}</span>
          </div>
          <div style="font-weight:600;font-size:15px;color:#111827;margin-bottom:4px;">{p.get('address', 'N/A')}</div>
          <div style="font-size:13px;color:#6b7280;margin-bottom:8px;">{p.get('city', '')}, {p.get('state', '')} {p.get('zip', '')}</div>
          <div style="font-size:14px;color:#4b5563;margin-bottom:8px;">{(p.get('description') or '')[:150]}</div>
          <div style="font-size:12px;color:#9ca3af;">Filed: {p.get('filing_date', 'N/A')} · #{p.get('permit_number', '')}</div>
          {contact_section}
        </div>'''

    # Summary section
    if permits:
        summary = f'''
        <div style="padding:24px 32px;background:#f9fafb;">
          <p style="margin:0;font-size:16px;color:#374151;">
            Hey {name}, we found <strong style="color:#111827;">{len(permits)} new permits</strong> in {city_display}.
          </p>
          {f'<p style="margin:8px 0 0 0;font-size:14px;color:#6b7280;">Including <strong style="color:#dc2626;">{high_value} high-value leads</strong> (${total_value:,.0f} total).</p>' if high_value > 0 else ''}
        </div>'''
    else:
        summary = f'''
        <div style="padding:24px 32px;background:#f9fafb;">
          <p style="margin:0;font-size:16px;color:#374151;">
            Hey {name}, no new permits were filed yesterday in {city_display}.
          </p>
          <p style="margin:8px 0 0 0;font-size:14px;color:#6b7280;">
            We'll keep watching and notify you when new permits come in.
          </p>
        </div>'''

    content = f'''
    <div style="padding:0 0 16px 0;border-bottom:1px solid #e5e7eb;">
      <div style="padding:12px 32px;background:#111827;color:white;font-size:13px;text-align:center;">
        Daily Permit Digest · {datetime.now().strftime('%B %d, %Y')}
      </div>
    </div>

    {summary}

    <div>
      {permit_rows if permits else ''}
    </div>

    {f'<div style="padding:12px 32px;text-align:center;font-size:13px;color:#9ca3af;background:#f9fafb;">Showing top {min(len(permits), permit_limit)} of {len(permits)} matching permits</div>' if len(permits) > permit_limit else ''}

    <div style="padding:24px 32px;text-align:center;">
      <a href="{SITE_URL}/dashboard" style="display:inline-block;padding:14px 32px;background:#2563eb;color:white;text-decoration:none;border-radius:8px;font-weight:600;font-size:16px;">
        View All Permits
      </a>
    </div>'''

    return base_template(
        content,
        preheader=f"{len(permits)} new permits in {city_display}" if permits else f"Daily update for {city_display}",
        show_upgrade_cta=not is_pro,
        unsubscribe_token=user.unsubscribe_token
    )


def send_daily_digest_to_user(user):
    """Send daily digest to a single user."""
    cities = json.loads(user.digest_cities or '[]')

    if not cities:
        return False, "no_cities"

    # Get yesterday's permits
    yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
    is_pro = user.plan in ('professional', 'pro', 'enterprise')
    limit = 50 if is_pro else 20  # Fetch more, display limited

    permits = get_permits_for_digest(cities, yesterday, limit)

    # Build and send
    html = build_digest_html(user, permits, is_pro)

    # Build subject
    if len(cities) == 1:
        subject = f"PermitGrab Daily Digest — {cities[0]} — {datetime.now().strftime('%b %d')}"
    else:
        subject = f"PermitGrab Daily Digest — {len(cities)} Cities — {datetime.now().strftime('%b %d')}"

    success = send_email(user.email, subject, html)

    return success, len(permits)


def send_daily_digest():
    """
    Send daily digest to all eligible users.
    Called by scheduler at 7 AM ET.

    Eligible users:
    - digest_active = True
    - Has at least 1 city in digest_cities
    - (For now, skip email_verified check until verification is fully implemented)
    """
    # Import here to avoid circular imports
    from server import app, User, db as flask_db

    print(f"\n{'='*60}")
    print(f"PermitGrab Daily Digest - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*60}")

    with app.app_context():
        # Query eligible users
        users = User.query.filter(
            User.digest_active == True,
            User.digest_cities != '[]',
            User.digest_cities != None,
            User.digest_cities != ''
        ).all()

        print(f"Found {len(users)} eligible users")

        sent = 0
        failed = 0
        skipped = 0

        for user in users:
            try:
                success, result = send_daily_digest_to_user(user)
                if success:
                    sent += 1
                    # Update last sent timestamp
                    user.last_digest_sent_at = datetime.utcnow()
                elif result == "no_cities":
                    skipped += 1
                else:
                    failed += 1
            except Exception as e:
                print(f"  ✗ Error sending to {user.email}: {e}")
                failed += 1

        # Commit timestamp updates
        flask_db.session.commit()

        print(f"\nDigest complete: {sent} sent, {failed} failed, {skipped} skipped")
        return sent, failed


def send_test_digest(email):
    """
    Send a test digest to a specific email address (for admin testing).
    Creates a mock user object if needed.
    """
    from server import app, User

    with app.app_context():
        # Try to find the user
        user = User.query.filter_by(email=email).first()

        if user:
            success, result = send_daily_digest_to_user(user)
            return {'success': success, 'result': result}
        else:
            # Create a mock user for testing
            class MockUser:
                def __init__(self):
                    self.email = email
                    self.name = "Test User"
                    self.plan = "pro"
                    self.digest_cities = json.dumps(["Atlanta", "New York"])
                    self.unsubscribe_token = "test-token"

                def is_pro(self):
                    return True

            mock_user = MockUser()
            success, result = send_daily_digest_to_user(mock_user)
            return {'success': success, 'result': result, 'note': 'Used mock user (email not found)'}


# =============================================================================
# EMAIL #10 — TRIAL EXPIRED
# =============================================================================

def send_trial_expired(user):
    """Send trial expired email."""
    name = user.name or 'there'

    content = f'''
    <div style="padding:32px;">
      <h1 style="margin:0 0 16px 0;font-size:24px;color:#111827;">Your Professional trial has ended</h1>

      <p style="font-size:16px;color:#4b5563;line-height:1.6;margin:0 0 24px 0;">
        Hey {name}, your 14-day Professional trial ended yesterday. Your account has been moved to the Free plan.
      </p>

      <div style="background:#fef2f2;border-radius:8px;padding:20px;margin-bottom:24px;border:1px solid #fecaca;">
        <h3 style="margin:0 0 12px 0;font-size:16px;color:#991b1b;">What's changed:</h3>
        <ul style="margin:0;padding-left:20px;color:#7f1d1d;line-height:1.8;">
          <li>Contractor contact info is now hidden</li>
          <li>Daily digest limited to 10 permits</li>
          <li>CSV export disabled</li>
          <li>Saved leads no longer accessible</li>
        </ul>
      </div>

      <div style="background:#f9fafb;border-radius:8px;padding:20px;margin-bottom:24px;">
        <h3 style="margin:0 0 12px 0;font-size:16px;color:#111827;">What you still have:</h3>
        <ul style="margin:0;padding-left:20px;color:#4b5563;line-height:1.8;">
          <li>Browse all permits across 556+ cities</li>
          <li>Filter by trade, value, and status</li>
          <li>See permit values and descriptions</li>
          <li>Limited daily digest emails</li>
        </ul>
      </div>

      <div style="text-align:center;margin-bottom:16px;">
        <a href="{SITE_URL}/pricing" style="display:inline-block;padding:14px 32px;background:#2563eb;color:white;text-decoration:none;border-radius:8px;font-weight:600;font-size:16px;">
          Reactivate Professional — $149/mo
        </a>
      </div>

      <p style="text-align:center;font-size:14px;color:#6b7280;">
        Or save with annual billing: <a href="{SITE_URL}/pricing" style="color:#2563eb;">$129/mo →</a>
      </p>
    </div>'''

    html = base_template(content, preheader="Your trial ended. Here's what changed.")

    return send_email(
        user.email,
        "Your Professional trial has ended",
        html
    )


# =============================================================================
# EMAIL #8 — TRIAL MIDPOINT (DAY 7)
# =============================================================================

def send_trial_midpoint(user):
    """Send trial midpoint reminder email."""
    name = user.name or 'there'
    days_left = user.days_until_trial_ends() or 7

    content = f'''
    <div style="padding:32px;">
      <div style="text-align:center;margin-bottom:24px;">
        <div style="display:inline-block;background:#fef3c7;color:#92400e;padding:6px 16px;border-radius:20px;font-size:14px;font-weight:600;">
          ⏰ {days_left} Days Left
        </div>
      </div>

      <h1 style="margin:0 0 16px 0;font-size:24px;color:#111827;text-align:center;">
        You're halfway through your Professional trial
      </h1>

      <p style="font-size:16px;color:#4b5563;line-height:1.6;margin:0 0 24px 0;text-align:center;">
        Hey {name}, just a heads up — your trial ends in {days_left} days.
      </p>

      <div style="background:#f0fdf4;border-radius:8px;padding:20px;margin-bottom:24px;border:1px solid #bbf7d0;">
        <h3 style="margin:0 0 12px 0;font-size:16px;color:#166534;">In {days_left} days, you'll lose access to:</h3>
        <ul style="margin:0;padding-left:20px;color:#166534;line-height:1.8;">
          <li>Full contractor contact info & phone numbers</li>
          <li>CSV export</li>
          <li>25-permit daily digest (drops to 10)</li>
          <li>Saved leads</li>
        </ul>
      </div>

      <div style="text-align:center;margin-bottom:16px;">
        <a href="{SITE_URL}/pricing" style="display:inline-block;padding:14px 32px;background:#2563eb;color:white;text-decoration:none;border-radius:8px;font-weight:600;font-size:16px;">
          Subscribe Now — $149/mo
        </a>
      </div>

      <p style="text-align:center;font-size:14px;color:#6b7280;">
        Save with annual billing: <a href="{SITE_URL}/pricing" style="color:#2563eb;">$129/mo →</a>
      </p>

      <p style="text-align:center;font-size:13px;color:#9ca3af;margin-top:24px;">
        No credit card was required for your trial, so nothing will be charged automatically.
      </p>
    </div>'''

    html = base_template(content, preheader=f"{days_left} days left on your Professional trial")

    return send_email(
        user.email,
        f"{days_left} days left on your Professional trial — here's what you'll lose",
        html
    )


# =============================================================================
# EMAIL #9 — TRIAL ENDING SOON (DAY 12)
# =============================================================================

def send_trial_ending_soon(user):
    """Send trial ending soon email."""
    name = user.name or 'there'
    days_left = user.days_until_trial_ends() or 2

    content = f'''
    <div style="padding:32px;">
      <div style="text-align:center;margin-bottom:24px;">
        <div style="display:inline-block;background:#fef2f2;color:#991b1b;padding:6px 16px;border-radius:20px;font-size:14px;font-weight:600;">
          ⚠️ {days_left} Days Left
        </div>
      </div>

      <h1 style="margin:0 0 16px 0;font-size:24px;color:#111827;text-align:center;">
        Don't lose access to contractor contacts
      </h1>

      <p style="font-size:16px;color:#4b5563;line-height:1.6;margin:0 0 24px 0;text-align:center;">
        Hey {name}, your Professional trial ends in just {days_left} days.
      </p>

      <div style="background:#fef2f2;border-radius:8px;padding:20px;margin-bottom:24px;border:1px solid #fecaca;">
        <h3 style="margin:0 0 12px 0;font-size:16px;color:#991b1b;">You're about to lose:</h3>
        <ul style="margin:0;padding-left:20px;color:#7f1d1d;line-height:1.8;">
          <li><strong>Contractor names, phone numbers, emails</strong></li>
          <li>CSV export capability</li>
          <li>Full 25-permit daily digest</li>
          <li>Your saved leads</li>
        </ul>
      </div>

      <div style="text-align:center;margin-bottom:16px;">
        <a href="{SITE_URL}/pricing" style="display:inline-block;padding:14px 32px;background:#dc2626;color:white;text-decoration:none;border-radius:8px;font-weight:600;font-size:16px;">
          Subscribe to Professional
        </a>
      </div>

      <p style="text-align:center;font-size:14px;color:#6b7280;">
        $149/mo or $129/mo with annual billing
      </p>
    </div>'''

    html = base_template(content, preheader=f"Your trial ends in {days_left} days!")

    return send_email(
        user.email,
        f"{days_left} days left — don't lose access to contractor contacts",
        html
    )


# =============================================================================
# EMAIL #7 — ONBOARDING NUDGE (24H)
# =============================================================================

def send_onboarding_nudge(user):
    """Send onboarding nudge if user hasn't selected cities after 24h."""
    name = user.name or 'there'

    content = f'''
    <div style="padding:32px;">
      <h1 style="margin:0 0 16px 0;font-size:24px;color:#111827;">
        You're one step away from permit alerts
      </h1>

      <p style="font-size:16px;color:#4b5563;line-height:1.6;margin:0 0 24px 0;">
        Hey {name}, you signed up for PermitGrab but haven't selected any cities yet.
        Pick your cities and you'll start getting daily permit intel delivered to your inbox at 7 AM.
      </p>

      <div style="background:#f9fafb;border-radius:8px;padding:20px;margin-bottom:24px;">
        <h3 style="margin:0 0 12px 0;font-size:16px;color:#111827;">Popular cities to get you started:</h3>
        <div style="display:flex;flex-wrap:wrap;gap:8px;">
          <span style="background:#dbeafe;color:#1e40af;padding:6px 12px;border-radius:16px;font-size:13px;">Atlanta</span>
          <span style="background:#dbeafe;color:#1e40af;padding:6px 12px;border-radius:16px;font-size:13px;">Phoenix</span>
          <span style="background:#dbeafe;color:#1e40af;padding:6px 12px;border-radius:16px;font-size:13px;">Dallas</span>
          <span style="background:#dbeafe;color:#1e40af;padding:6px 12px;border-radius:16px;font-size:13px;">Denver</span>
          <span style="background:#dbeafe;color:#1e40af;padding:6px 12px;border-radius:16px;font-size:13px;">Miami</span>
        </div>
      </div>

      <div style="text-align:center;margin-bottom:24px;">
        <a href="{SITE_URL}/account" style="display:inline-block;padding:14px 32px;background:#2563eb;color:white;text-decoration:none;border-radius:8px;font-weight:600;font-size:16px;">
          Choose Your Cities
        </a>
      </div>

      <p style="font-size:14px;color:#6b7280;text-align:center;">
        Currently tracking <strong>118,000+</strong> permits across <strong>556</strong> cities.
      </p>
    </div>'''

    html = base_template(content, preheader="Pick your cities to start getting daily permit alerts")

    return send_email(
        user.email,
        "You're one step away from permit alerts — pick your cities",
        html
    )


# =============================================================================
# TRIAL LIFECYCLE CHECKER
# =============================================================================

def check_trial_lifecycle():
    """
    Check all users for trial lifecycle events.
    Called daily by scheduler.
    """
    from server import app, User, db as flask_db

    print(f"\n{'='*60}")
    print(f"Trial Lifecycle Check - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*60}")

    with app.app_context():
        now = datetime.utcnow()

        # Find trial users
        trial_users = User.query.filter(
            User.plan.in_(['professional', 'pro']),
            User.trial_end_date != None,
            User.stripe_subscription_status.is_(None)  # Not a paid subscriber
        ).all()

        print(f"Found {len(trial_users)} users on trial")

        midpoint_sent = 0
        ending_sent = 0
        expired_count = 0

        for user in trial_users:
            days_left = user.days_until_trial_ends()

            if days_left is None:
                continue

            # Trial expired
            if days_left <= 0 and not user.trial_expired_sent:
                print(f"  Trial expired: {user.email}")
                if send_trial_expired(user):
                    user.trial_expired_sent = True
                    user.plan = 'free'  # Downgrade
                    expired_count += 1

            # Trial ending soon (day 12 = 2 days left)
            elif days_left <= 2 and not user.trial_ending_sent:
                print(f"  Trial ending soon: {user.email} ({days_left} days)")
                if send_trial_ending_soon(user):
                    user.trial_ending_sent = True
                    ending_sent += 1

            # Trial midpoint (day 7 = 7 days left)
            elif days_left <= 7 and not user.trial_midpoint_sent:
                print(f"  Trial midpoint: {user.email} ({days_left} days)")
                if send_trial_midpoint(user):
                    user.trial_midpoint_sent = True
                    midpoint_sent += 1

        flask_db.session.commit()

        print(f"\nLifecycle check complete:")
        print(f"  - Midpoint emails: {midpoint_sent}")
        print(f"  - Ending soon emails: {ending_sent}")
        print(f"  - Expired/downgraded: {expired_count}")

        return midpoint_sent, ending_sent, expired_count


# =============================================================================
# ONBOARDING CHECK
# =============================================================================

def check_onboarding_nudges():
    """
    Check for users who signed up 24h ago but haven't selected cities.
    Called daily by scheduler.
    """
    from server import app, User, db as flask_db

    print(f"\n{'='*60}")
    print(f"Onboarding Nudge Check - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*60}")

    with app.app_context():
        # Users created 24-48 hours ago with no cities
        cutoff_start = datetime.utcnow() - timedelta(hours=48)
        cutoff_end = datetime.utcnow() - timedelta(hours=24)

        users = User.query.filter(
            User.created_at >= cutoff_start,
            User.created_at <= cutoff_end,
            (User.digest_cities == '[]') | (User.digest_cities == None) | (User.digest_cities == '')
        ).all()

        print(f"Found {len(users)} users without cities (24-48h old)")

        sent = 0
        for user in users:
            if send_onboarding_nudge(user):
                sent += 1

        print(f"Sent {sent} onboarding nudges")
        return sent


# =============================================================================
# TEST FUNCTIONS
# =============================================================================

def send_test_digest(to_email, cities=None):
    """Send a test digest email."""
    if cities is None:
        cities = ['Atlanta']

    # Get recent permits
    since = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
    permits = get_permits_for_digest(cities, since, 25)

    # Create mock user
    class MockUser:
        email = to_email
        name = 'Test User'
        digest_cities = json.dumps(cities)
        plan = 'professional'
        unsubscribe_token = 'test-token'

    html = build_digest_html(MockUser(), permits, is_pro=True)

    subject = f"[TEST] PermitGrab Daily Digest — {cities[0]} — {datetime.now().strftime('%b %d')}"

    # Save preview
    preview_path = '/tmp/test_digest_preview.html'
    try:
        with open(preview_path, 'w') as f:
            f.write(html)
        print(f"Preview saved to: {preview_path}")
    except Exception:
        pass

    return send_email(to_email, subject, html)


# =============================================================================
# MAIN (for testing)
# =============================================================================

if __name__ == '__main__':
    import sys

    if len(sys.argv) > 1:
        cmd = sys.argv[1]

        if cmd == 'digest':
            send_daily_digest()

        elif cmd == 'test':
            email = sys.argv[2] if len(sys.argv) > 2 else 'test@example.com'
            city = sys.argv[3] if len(sys.argv) > 3 else 'Atlanta'
            send_test_digest(email, [city])

        elif cmd == 'lifecycle':
            check_trial_lifecycle()

        elif cmd == 'nudges':
            check_onboarding_nudges()

        else:
            print("Usage: python email_alerts.py [digest|test|lifecycle|nudges]")
    else:
        print("Usage: python email_alerts.py [digest|test|lifecycle|nudges]")
