"""V471 PR2 (CODE_V471 Part 1B): city_pages blueprint extracted from server.py.

Routes: 32 URLs across 32 handlers.

Helpers/globals from server.py are accessed via `from server import *`,
which imports everything server.py defined before this blueprint was loaded
(blueprints are registered at the bottom of server.py, after all globals
are set). Underscored helpers are listed below explicitly because `import *`
skips names starting with `_`.

V471 PR2-prep moved daemon spawn + DB init out of module-level code, so
the worker can re-import server.py cleanly during fork or self-recycle
without racing this `from server import *` against the import lock.
"""
from flask import Blueprint, request, jsonify, render_template, session, redirect, abort, Response, g, url_for, send_from_directory
from datetime import datetime, timedelta
import os, json, time, re, threading, random, string, hashlib, hmac
from werkzeug.security import generate_password_hash, check_password_hash

# Pull in server.py's helpers, models, and globals.
from server import *
import server as _s

city_pages_bp = Blueprint('city_pages', __name__)


from server import _PERMIT_SLUG_ALIASES, _get_market_insights, _get_property_owners, _get_top_contractors_for_city

@city_pages_bp.route('/')
def index():
    """Serve the dashboard."""
    # V8: Redirect new users to onboarding
    # V9 Fix: Only redirect truly new users - existing users with preferences or Pro plan skip onboarding
    # V476 Bug 4: also skip the redirect if the visitor was already
    # bounced to /onboarding once this session — without that, a user
    # who clicks "Logo" / hits Back / re-types `/` after seeing
    # onboarding loops right back to /onboarding instead of seeing
    # the homepage they wanted. The redirect now fires AT MOST ONCE
    # per session for users who are still legitimately incomplete.
    if 'user_email' in session and not session.get('_onboarding_seen'):
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
                session['_onboarding_seen'] = True
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

    # V31/V160: City count = only cities with fresh data (last 30 days)
    city_count = get_total_city_count_auto()

    # V160: State count — only states with fresh data
    try:
        _sc = permitdb.get_connection()
        state_count = _sc.execute(
            "SELECT COUNT(DISTINCT state) as cnt FROM prod_cities WHERE newest_permit_date >= date('now', '-30 days') AND source_type IS NOT NULL AND status = 'active'"
        ).fetchone()['cnt']
    except Exception:
        state_count = 38

    # V13: Pass ALL cities for dropdown (sorted by state then city name)
    # This ensures dropdown shows all 555+ cities, not just those in the paginated API response
    all_dropdown_cities = get_cities_with_data()  # Now returns all cities from permits table

    # V13.7: Pass stats for server-side rendering (fixes H5: stat counters showing dashes)
    stats = permitdb.get_permit_stats()
    initial_stats = {
        'total_permits': stats.get('total_permits', 0),
        'total_value': stats.get('total_value', 0),
        'high_value_count': stats.get('high_value_count', 0),
    }

    # V366 (CODE_V363 Part F): browseable city directory grouped by state.
    cities_by_state = get_city_directory_stats()

    return render_template('dashboard.html', footer_cities=footer_cities,
                          default_city=default_city, default_trade=default_trade,
                          city_count=city_count, state_count=state_count,
                          all_dropdown_cities=all_dropdown_cities,
                          cities_by_state=cities_by_state,
                          initial_stats=initial_stats,
                          # V224 T1: hide the sticky filter bar and the 50-card
                          # permit grid on the homepage itself — they're from
                          # the dashboard view and inflate the homepage to
                          # 17,000+ px of mostly-locked cards that users never
                          # scroll through. Moved behind a CTA link to /browse
                          # so the homepage stays a marketing landing page.
                          is_homepage=True)


@city_pages_bp.route('/dashboard')
def dashboard_redirect():
    """V311 (CODE_V280b Bug 4): real personalized dashboard.

    Was a redirect to /browse which dumped logged-in users on the
    marketing homepage — per Wes: "A clean list of their cities with
    permit counts is 100x better than redirecting to the marketing
    homepage."

    Renders a user dashboard with:
      • welcome + plan status
      • top-line stats (ad-ready count, permits this week, etc.)
      • tracked cities table (for now: the 12 ad-ready + their profile
        phone/violation counts so the user sees what they're paying for)
      • recent permits across those cities
      • quick action buttons
    """
    if 'user_email' not in session:
        return redirect('/login?redirect=dashboard&message=login_required')

    user = find_user_by_email(session['user_email'])
    if not user:
        return redirect('/login')
    plan = get_user_plan(user)
    pro = plan in ('pro', 'professional', 'enterprise')

    # Ad-ready cities = what the user actually gets with their plan.
    conn = permitdb.get_connection()
    tracked = []
    try:
        rows = conn.execute("""
            SELECT cp.source_city_key AS slug, MIN(cp.city) AS name, MIN(cp.state) AS state,
                   COUNT(*) AS profiles,
                   SUM(CASE WHEN cp.phone IS NOT NULL AND cp.phone <> '' THEN 1 ELSE 0 END) AS phones
            FROM contractor_profiles cp
            GROUP BY cp.source_city_key
            HAVING COUNT(*) >= 100
               AND SUM(CASE WHEN cp.phone IS NOT NULL AND cp.phone <> '' THEN 1 ELSE 0 END) >= 50
            ORDER BY phones DESC
            LIMIT 20
        """).fetchall()
        for r in rows or []:
            # pull per-city permit + violation counts
            pc = conn.execute(
                "SELECT COUNT(*) AS n FROM permits WHERE source_city_key = ?",
                (r['slug'],)
            ).fetchone()
            vc = conn.execute(
                "SELECT COUNT(*) AS n FROM violations WHERE UPPER(city) = UPPER(?) AND UPPER(state) = UPPER(?)",
                (r['name'], r['state'])
            ).fetchone()
            tracked.append({
                'slug': r['slug'], 'name': r['name'], 'state': r['state'],
                'profiles': r['profiles'], 'phones': r['phones'],
                'permits': pc['n'] if pc else 0,
                'violations': vc['n'] if vc else 0,
            })
    except Exception:
        tracked = []

    ad_ready_count = len(tracked)
    total_cities = 0
    try:
        total_cities = conn.execute(
            "SELECT COUNT(*) AS n FROM prod_cities WHERE status = 'active'"
        ).fetchone()['n']
    except Exception:
        pass

    permits_week = 0
    try:
        permits_week = conn.execute(
            "SELECT COUNT(*) AS n FROM permits WHERE filing_date >= date('now', '-7 days')"
        ).fetchone()['n']
    except Exception:
        pass

    active_contractors = 0
    try:
        active_contractors = conn.execute(
            "SELECT COUNT(*) AS n FROM contractor_profiles"
        ).fetchone()['n']
    except Exception:
        pass

    violations_count = 0
    try:
        violations_count = conn.execute(
            "SELECT COUNT(*) AS n FROM violations"
        ).fetchone()['n']
    except Exception:
        pass

    # Recent permits across tracked cities. 20 max, ordered by date desc.
    recent_permits = []
    if tracked:
        slugs = [t['slug'] for t in tracked]
        placeholders = ','.join('?' * len(slugs))
        try:
            rows = conn.execute(f"""
                SELECT source_city_key, city, address, permit_type,
                       contractor_name, contact_name,
                       COALESCE(filing_date, issued_date, date) AS date
                FROM permits
                WHERE source_city_key IN ({placeholders})
                  AND COALESCE(filing_date, issued_date, date) IS NOT NULL
                ORDER BY date DESC
                LIMIT 20
            """, slugs).fetchall()
            for r in rows or []:
                recent_permits.append({
                    'source_city_key': r['source_city_key'],
                    'city': r['city'],
                    'address': r['address'],
                    'permit_type': r['permit_type'],
                    'contractor_name': r['contractor_name'],
                    'contact_name': r['contact_name'],
                    'date': (r['date'] or '')[:10],
                })
        except Exception:
            pass

    return render_template(
        'my_dashboard.html',
        user=user,
        is_pro=pro,
        tracked_cities=tracked,
        ad_ready_count=ad_ready_count,
        total_cities=total_cities,
        permits_week=permits_week,
        active_contractors=active_contractors,
        violations_count=violations_count,
        recent_permits=recent_permits,
    )


@city_pages_bp.route('/contractors/<slug>')
def contractors_by_city(slug):
    """V311 (CODE_V280b Bug 6): per-city contractor directory.

    The /contractors page is city-scoped under the hood but its UI
    doesn't expose deep links. Adding /contractors/<slug> lets us
    point directly to one city's contractor roster (useful for SEO,
    internal links from city pages, and the dashboard's Browse flow).
    """
    footer_cities = get_cities_with_data()
    return render_template('contractors.html', footer_cities=footer_cities,
                          default_city_slug=slug)


@city_pages_bp.route('/browse')
def browse_permits():
    """V224 T1: Full interactive permit grid — this is what was living at /
    (the homepage) and making it 17k px tall. Splitting the marketing
    landing page from the data-browse experience: / stays short, /browse
    is the filter-and-scroll dashboard."""
    footer_cities = get_cities_with_data()
    default_city = ''
    default_trade = ''
    if 'user_email' in session:
        user = find_user_by_email(session['user_email'])
        if user:
            default_city = user.city or ''
            default_trade = user.trade or ''
    city_count = get_total_city_count_auto()
    try:
        _sc = permitdb.get_connection()
        state_count = _sc.execute(
            "SELECT COUNT(DISTINCT state) as cnt FROM prod_cities WHERE newest_permit_date >= date('now', '-30 days') AND source_type IS NOT NULL AND status = 'active'"
        ).fetchone()['cnt']
    except Exception:
        state_count = 38
    all_dropdown_cities = get_cities_with_data()
    stats = permitdb.get_permit_stats()
    initial_stats = {
        'total_permits': stats.get('total_permits', 0),
        'total_value': stats.get('total_value', 0),
        'high_value_count': stats.get('high_value_count', 0),
    }
    return render_template('dashboard.html', footer_cities=footer_cities,
                          default_city=default_city, default_trade=default_trade,
                          city_count=city_count, state_count=state_count,
                          all_dropdown_cities=all_dropdown_cities,
                          initial_stats=initial_stats,
                          is_homepage=False)


@city_pages_bp.route('/alerts')
def alerts_redirect():
    """V30: Redirect to appropriate alerts page based on login status."""
    user = get_current_user()
    if user:
        return redirect('/account')
    return redirect('/get-alerts')


@city_pages_bp.route('/report/<city_slug>')
def city_report(city_slug):
    """V251 F22: Shareable city report — print-friendly single-page summary
    Pro users can send to their sales team. Publicly viewable (phones
    still gated). Designed to screenshot/PDF well — fixed max-width,
    branded footer, no nav dropdowns interfering with print.
    """
    conn = permitdb.get_connection()
    pc = conn.execute(
        "SELECT id, city, state, city_slug, newest_permit_date FROM prod_cities WHERE city_slug=?",
        (city_slug,),
    ).fetchone()
    if not pc:
        return render_template('404.html'), 404
    pid = pc['id']
    stats = conn.execute("""
        SELECT
          (SELECT COUNT(*) FROM permits WHERE prod_city_id=?) as total_permits,
          (SELECT COUNT(*) FROM permits WHERE prod_city_id=? AND COALESCE(filing_date,issued_date,date) >= date('now','-90 days')) as permits_90d,
          (SELECT COUNT(*) FROM permits WHERE prod_city_id=? AND COALESCE(filing_date,issued_date,date) >= date('now','-7 days')) as permits_7d,
          (SELECT COUNT(*) FROM contractor_profiles WHERE source_city_key=? AND is_active=1) as profiles,
          (SELECT COUNT(*) FROM contractor_profiles WHERE source_city_key=? AND phone IS NOT NULL AND phone != '') as phones,
          (SELECT COUNT(*) FROM violations WHERE prod_city_id=?) as violations,
          (SELECT COALESCE(SUM(estimated_cost),0) FROM permits WHERE prod_city_id=? AND COALESCE(filing_date,issued_date,date) >= date('now','-90 days')) as value_90d
    """, (pid, pid, pid, city_slug, city_slug, pid, pid)).fetchone()
    top_trades = conn.execute("""
        SELECT trade_category, COUNT(*) as n FROM permits
        WHERE prod_city_id=? AND trade_category IS NOT NULL AND trade_category != ''
        GROUP BY trade_category ORDER BY n DESC LIMIT 5
    """, (pid,)).fetchall()
    top_contractors = _get_top_contractors_for_city(city_slug, limit=10)
    return render_template(
        'city_report.html',
        city=dict(pc),
        stats=dict(stats),
        top_trades=[dict(r) for r in top_trades],
        top_contractors=top_contractors,
        current_date=datetime.now().strftime('%B %d, %Y'),
        canonical_url=f"{SITE_URL}/report/{city_slug}",
    )


@city_pages_bp.route('/<trade>/<city_slug>')
def trade_city_landing(trade, city_slug):
    """V252 F7 + V253: standalone trade-first URL.

    Originally 301'd to /permits/<city>/<trade>, which meant Google saw
    one canonical and /<trade>/<city> got zero ranking juice — defeating
    the SEO play. Now renders the same template directly and passes a
    self-canonical so the trade-first URL can rank for "solar leads
    chicago" / "roofing contractor phoenix" / etc. on its own.
    """
    if trade.lower() not in V252_TRADE_URL_MAP:
        abort(404)
    trade_slug = V252_TRADE_URL_MAP[trade.lower()]
    # Render through city_trade_landing (the existing /permits/<city>/<trade>
    # route handler) but override canonical_url via Flask request context.
    # Simplest: call the view function with a g-scoped canonical override
    # and have the template pick it up.
    g.canonical_url_override = f"{SITE_URL}/{trade.lower()}/{city_slug}"
    return city_trade_landing(city_slug, trade_slug)


@city_pages_bp.route('/leaderboard/<city_slug>')
def leaderboard(city_slug):
    """V252 F6: Public contractor-volume leaderboard for a city.
    Free tier — pure SEO play. "Top 50 Contractors in X by Permit Volume."
    Phones gated behind signup like everywhere else.
    """
    conn = permitdb.get_connection()
    pc = conn.execute(
        "SELECT id, city, state, city_slug FROM prod_cities WHERE city_slug=?",
        (city_slug,),
    ).fetchone()
    if not pc:
        return render_template('404.html'), 404
    # Reuse F21's scoring pipeline. limit=50 for the leaderboard.
    top = _get_top_contractors_for_city(city_slug, limit=50)
    # Tag badges per spec. "Rising Star" omitted — needs prev-period metric
    # we don't track; ship when that's added. "New Entrant" uses is_new
    # from F5 (first permit within 7d; spec says 30d — widen here).
    from datetime import date as _d, datetime as _dt
    today = _d.today()
    for i, c in enumerate(top):
        c['rank'] = i + 1
        c['badge_market_leader'] = (i < 3)
        fpd = c.get('last_permit_date')  # approximation field
        try:
            first_age = (today - _dt.strptime((c.get('first_permit_date') or fpd or '')[:10], '%Y-%m-%d').date()).days \
                        if (c.get('first_permit_date') or fpd) else None
        except Exception:
            first_age = None
        c['badge_new_entrant'] = first_age is not None and first_age <= 30
    now = datetime.now()
    return render_template(
        'leaderboard.html',
        city=dict(pc),
        contractors=top,
        current_month=now.strftime('%B %Y'),
        canonical_url=f"{SITE_URL}/leaderboard/{city_slug}",
    )


@city_pages_bp.route('/intel')
def intel_dashboard():
    """V251 F19: Pro-only multi-city intel dashboard.

    Grid of ad-ready cities + the user's digest_cities with per-city KPIs
    (permits 7d, new contractors, phone coverage, violations) so a
    franchise / regional buyer can scan their whole footprint.
    """
    user = get_current_user()
    if not user:
        return redirect('/login?redirect=/intel')
    if not is_pro(user):
        return redirect('/pricing?next=/intel')
    conn = permitdb.get_connection()

    # V369 (loop /CODE_V286 grind): seed list was 7 slugs, missing 6 of
    # the 13 ad-ready cities per CLAUDE.md (los-angeles, henderson, anaheim,
    # cleveland-oh, buffalo-ny, nashville-tn). Keeping this aligned with the
    # admin dashboard's dynamic ad-ready computation surfaces the right
    # cards for Pro subscribers landing on /intel without a digest set.
    ad_ready = [
        'san-antonio-tx', 'miami-dade-county', 'chicago-il', 'phoenix-az',
        'new-york-city', 'los-angeles', 'henderson', 'anaheim', 'cleveland-oh',
        'san-jose', 'buffalo-ny', 'nashville-tn', 'orlando-fl',
    ]
    # user.to_dict() already parses digest_cities into a list; handle both.
    dc = user.get('digest_cities') or []
    if isinstance(dc, str):
        try:
            digest_cities = json.loads(dc or '[]')
        except Exception:
            digest_cities = []
    else:
        digest_cities = dc
    # Resolve digest city names → slugs (best-effort join on prod_cities.city)
    slugs = list(ad_ready)
    if digest_cities:
        ph = ','.join(['?'] * len(digest_cities))
        rows = conn.execute(
            f"SELECT city_slug FROM prod_cities WHERE city IN ({ph}) AND status='active'",
            digest_cities,
        ).fetchall()
        for r in rows:
            s = r['city_slug'] if not isinstance(r, tuple) else r[0]
            if s not in slugs:
                slugs.append(s)

    cities = []
    for slug in slugs:
        row = conn.execute("""
            SELECT pc.id, pc.city, pc.state, pc.city_slug, pc.newest_permit_date
            FROM prod_cities pc WHERE pc.city_slug = ?
        """, (slug,)).fetchone()
        if not row:
            continue
        pid = row['id']
        stats = conn.execute("""
            SELECT
              (SELECT COUNT(*) FROM permits WHERE prod_city_id=? AND COALESCE(filing_date,issued_date,date) >= date('now','-7 days')) as permits_7d,
              (SELECT COUNT(*) FROM permits WHERE prod_city_id=? AND COALESCE(filing_date,issued_date,date) >= date('now','-30 days')) as permits_30d,
              (SELECT COUNT(*) FROM contractor_profiles WHERE source_city_key=? AND is_active=1) as profiles,
              (SELECT COUNT(*) FROM contractor_profiles WHERE source_city_key=? AND is_active=1 AND phone IS NOT NULL AND phone != '') as phones,
              (SELECT COUNT(*) FROM violations WHERE prod_city_id=?) as violations,
              (SELECT COUNT(*) FROM contractor_profiles WHERE source_city_key=? AND first_permit_date >= date('now','-7 days')) as new_contractors_7d
        """, (pid, pid, slug, slug, pid, slug)).fetchone()
        cities.append({
            'slug': row['city_slug'],
            'name': row['city'],
            'state': row['state'],
            'newest': row['newest_permit_date'],
            'permits_7d': stats['permits_7d'] or 0,
            'permits_30d': stats['permits_30d'] or 0,
            'profiles': stats['profiles'] or 0,
            'phones': stats['phones'] or 0,
            'violations': stats['violations'] or 0,
            'new_contractors_7d': stats['new_contractors_7d'] or 0,
        })
    footer_cities = get_cities_with_data()
    return render_template(
        'intel_dashboard.html',
        cities=cities,
        user=user,
        footer_cities=footer_cities,
    )


@city_pages_bp.route('/saved-contractors')
def saved_contractors_page():
    """V251 F15: Pro-only saved-contractors list page."""
    user = get_current_user()
    if not user:
        return redirect('/login?redirect=/saved-contractors')
    if not is_pro(user):
        return redirect('/pricing?next=/saved-contractors')
    footer_cities = get_cities_with_data()
    return render_template('saved_contractors.html', user=user, footer_cities=footer_cities)


@city_pages_bp.route('/contractors')
def contractors_page():
    """Render the Contractors Intelligence page."""
    footer_cities = get_cities_with_data()
    return render_template('contractors.html', footer_cities=footer_cities)


@city_pages_bp.route('/contractor/<int:contractor_id>')
def contractor_detail(contractor_id):
    """V251 F6: Contractor detail page.

    Shows: business name, velocity + recency, gated phone, full permit
    history, first-seen, violations-at-their-job-sites (address join).
    Non-Pro visitors see the name + velocity + permit count but the phone
    row is replaced with a "Subscribe to reveal" CTA matching F1/F3.
    """
    from contractor_profiles import is_license_number
    from datetime import date as _date, datetime as _dt
    conn = permitdb.get_connection()
    prof = conn.execute("""
        SELECT id, contractor_name_raw, contractor_name_normalized,
               source_city_key, city, state,
               total_permits, permits_90d, permits_30d, primary_trade,
               trade_breakdown, avg_project_value, max_project_value,
               total_project_value, primary_area, first_permit_date,
               last_permit_date, permit_frequency, phone, website, email,
               license_number, license_status, enrichment_status
        FROM contractor_profiles WHERE id = ?
    """, (contractor_id,)).fetchone()
    if not prof:
        return render_template('404.html'), 404

    raw = prof['contractor_name_raw']
    is_license = is_license_number(prof['contractor_name_normalized'])
    display_name = f"License #{raw}" if is_license else raw

    # V251 F5 velocity/recency (mirror of city-page computation)
    today = _date.today()
    def _days_since(s):
        if not s: return None
        try:
            return (today - _dt.strptime(s[:10], '%Y-%m-%d').date()).days
        except Exception:
            return None
    last_age = _days_since(prof['last_permit_date'])
    first_age = _days_since(prof['first_permit_date'])
    p30 = prof['permits_30d'] or 0
    if p30 >= 10:
        velocity_color = 'red'
    elif p30 >= 5:
        velocity_color = 'orange'
    elif p30 >= 1:
        velocity_color = 'blue'
    else:
        velocity_color = ''
    if last_age is not None and last_age <= 7:
        recency = 'green'
    elif last_age is not None and last_age <= 30:
        recency = 'yellow'
    else:
        recency = 'gray'
    is_new = first_age is not None and first_age <= 7

    # Permit history — per V250 Phase 1A finding, permits for a city are
    # spread across multiple source_city_key values (e.g. chicago,
    # chicago-il, chicago_il) all linked to the same prod_cities.id. Use
    # prod_city_id so we catch all of them.
    _profile_pc = conn.execute(
        "SELECT id FROM prod_cities WHERE city_slug = ?", (prof['source_city_key'],)
    ).fetchone()
    _profile_pc_id = _profile_pc[0] if _profile_pc else None
    if _profile_pc_id:
        # Exact-case match — permit data arrives pre-upper from Chicago and
        # most large cities. Avoids the UPPER() full-scan on 25k+ permits
        # which was timing out the detail page on big-city contractors.
        # Both raw + normalized are tried so DB-normalized entries still hit.
        permits = conn.execute("""
            SELECT filing_date, issued_date, date, permit_type, address,
                   description, estimated_cost, trade_category, status,
                   zip, source_city_key
            FROM permits
            WHERE prod_city_id = ?
              AND (contractor_name = ? OR contractor_name = ?
                   OR contact_name = ? OR contact_name = ?)
            ORDER BY COALESCE(filing_date, issued_date, date) DESC
            LIMIT 200
        """, (_profile_pc_id, raw, (prof['contractor_name_normalized'] or ''),
              raw, (prof['contractor_name_normalized'] or ''))).fetchall()
    else:
        permits = conn.execute("""
            SELECT filing_date, issued_date, date, permit_type, address,
                   description, estimated_cost, trade_category, status,
                   zip, source_city_key
            FROM permits
            WHERE source_city_key = ?
              AND (UPPER(contractor_name) = UPPER(?)
                   OR UPPER(contractor_name) = UPPER(?)
                   OR UPPER(contact_name) = UPPER(?))
            ORDER BY COALESCE(filing_date, issued_date, date) DESC
            LIMIT 200
        """, (prof['source_city_key'], raw, prof['contractor_name_normalized'], raw)).fetchall()

    # Cities this contractor is also active in
    other_cities = conn.execute("""
        SELECT source_city_key, COUNT(*) as n
        FROM permits
        WHERE UPPER(contractor_name) = UPPER(?)
          AND source_city_key != ?
        GROUP BY source_city_key ORDER BY n DESC LIMIT 5
    """, (raw, prof['source_city_key'])).fetchall()

    # V252 F1.5: Property-owner append — enterprise tier only.
    # Gracefully empty list until per-county ETL writes rows.
    property_owner_lookup = {}
    if permits:
        addrs = list({p['address'] for p in permits if p['address']})[:50]
        if addrs:
            placeholders = ','.join(['?'] * len(addrs))
            try:
                po_rows = conn.execute(f"""
                    SELECT address, owner_name, owner_mailing_address, parcel_id, source
                    FROM property_owners WHERE UPPER(address) IN ({placeholders})
                """, [a.upper() for a in addrs]).fetchall()
                property_owner_lookup = {
                    (r['address'] or '').upper(): dict(r) for r in po_rows
                }
            except Exception:
                pass

    # Violations at addresses this contractor worked. Join on normalized address.
    violations = []
    if permits:
        addrs = list({p['address'] for p in permits if p['address']})[:50]
        if addrs:
            placeholders = ','.join(['?'] * len(addrs))
            violations = conn.execute(f"""
                SELECT violation_date, violation_type, violation_description,
                       status, address
                FROM violations
                WHERE UPPER(address) IN ({placeholders})
                ORDER BY violation_date DESC LIMIT 25
            """, [a.upper() for a in addrs]).fetchall()

    # Pretty city name for the nav/breadcrumb
    city_name = prof['city'] or prof['source_city_key']

    return render_template(
        'contractor_detail.html',
        contractor={
            'id': prof['id'],
            'display_name': display_name,
            'is_license_number': is_license,
            'source_city_key': prof['source_city_key'],
            'city': city_name,
            'state': prof['state'],
            'total_permits': prof['total_permits'] or 0,
            'permits_90d': prof['permits_90d'] or 0,
            'permits_30d': p30,
            'primary_trade': prof['primary_trade'],
            'avg_project_value': prof['avg_project_value'],
            'max_project_value': prof['max_project_value'],
            'phone': prof['phone'],
            'website': prof['website'],
            'email': prof['email'],
            'license_number': prof['license_number'],
            'first_permit_date': prof['first_permit_date'],
            'last_permit_date': prof['last_permit_date'],
            'velocity_color': velocity_color,
            'recency': recency,
            'is_new': is_new,
        },
        permits=[dict(p) for p in permits],
        other_cities=[dict(c) for c in other_cities],
        violations=[dict(v) for v in violations],
        property_owner_lookup=property_owner_lookup,  # V252 F1.5
        canonical_url=f"{SITE_URL}/contractor/{contractor_id}",
    )


@city_pages_bp.route('/pricing')
def pricing_page():
    """Render the Pricing page. V12.51: SQL-backed"""
    user = get_current_user()
    cities = get_all_cities_info()
    city_count = get_total_city_count_auto()  # V31: Active cities only
    footer_cities = get_cities_with_data()
    # V12.51: Get permit count from SQLite
    stats = permitdb.get_permit_stats()
    permit_count = stats['total_permits']
    return render_template('pricing.html', user=user, cities=cities, city_count=city_count, footer_cities=footer_cities, permit_count=permit_count)


@city_pages_bp.route('/start-checkout')
def start_checkout():
    """V375 (CODE_V363 P0): unified entrypoint for the "Start Free Trial"
    button so it works the same for logged-out and logged-in visitors.

    - Logged-out: redirect to /signup?plan=<plan>&next=/start-checkout?plan=<plan>
      so the user creates an account first, then comes back here.
    - Logged-in already-paid: redirect to /dashboard with a friendly note.
    - Logged-in free: create a Stripe Checkout session and 302 to its URL.

    Falls back to /pricing on Stripe configuration errors so the user
    never lands on a broken page.
    """
    plan = (request.args.get('plan') or 'pro').strip().lower()
    if plan not in ('starter', 'pro', 'enterprise'):
        plan = 'pro'

    user = get_current_user()
    if not user:
        from urllib.parse import quote
        next_url = quote(f'/start-checkout?plan={plan}', safe='')
        return redirect(f'/signup?plan={plan}&next={next_url}')

    # Already paid — send them to the product they're paying for.
    if is_pro(user):
        return redirect('/dashboard?already_subscribed=1')

    # Logged-in free user — create the Stripe session inline.
    if not STRIPE_SECRET_KEY:
        # Stripe not configured — fall back to mailto so we at least
        # capture a sales lead instead of a 500.
        return redirect(
            'mailto:wcrainshaw@gmail.com?subject=PermitGrab+'
            f'{plan.title()}+Signup'
        )

    per_plan_price = {
        'starter': os.environ.get('STRIPE_PRICE_STARTER', ''),
        'pro': os.environ.get('STRIPE_PRICE_PRO', '') or STRIPE_PRICE_ID,
        'enterprise': os.environ.get('STRIPE_PRICE_ENTERPRISE', ''),
    }
    price_id = per_plan_price.get(plan) or STRIPE_PRICE_ID
    if not price_id:
        return redirect('/pricing?checkout=not_configured')

    stripe.api_key = STRIPE_SECRET_KEY
    try:
        # V460 (CODE_V456 Step 10): include client_reference_id and
        # metadata.user_id so the Stripe webhook can map a paid checkout
        # back to the local User row. Without this, the webhook handler
        # falls back to email matching which fails when the user paid
        # with a different email than the one on their account.
        _user_id = (user.get('id') if isinstance(user, dict)
                    else getattr(user, 'id', None))
        _user_email = (user.get('email') if isinstance(user, dict)
                       else getattr(user, 'email', None))
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{'price': price_id, 'quantity': 1}],
            mode='subscription',
            success_url=f'{SITE_URL}/success?session_id={{CHECKOUT_SESSION_ID}}',
            cancel_url=f'{SITE_URL}/pricing?payment=cancelled',
            customer_email=_user_email,
            client_reference_id=str(_user_id) if _user_id else None,
            metadata={
                'plan': f'{plan}_monthly',
                'billing_period': 'monthly',
                'user_id': str(_user_id) if _user_id else '',
            },
            allow_promotion_codes=True,
            subscription_data={'trial_period_days': 14},
        )
        analytics.track_event('checkout_started', event_data={
            'plan': f'{plan}_monthly', 'billing': 'monthly',
            'source': 'start-checkout',
        })
        return redirect(checkout_session.url, code=303)
    except stripe.error.StripeError as e:
        print(f'[V375] Stripe checkout create failed: {e}', flush=True)
        return redirect('/pricing?checkout=stripe_error')


@city_pages_bp.route('/get-alerts')
def get_alerts_page():
    """Render the Get Alerts page.

    V309 (CODE_V280b Bug 22): the UAT report flagged /get-alerts as a
    suspected 502 trigger. get_cities_with_data() can return 600+ rows;
    rendering them all into a <select> dropdown is fine memory-wise but
    puts every row through the Jinja loop. Cap at 200 (all ad-ready +
    active cities fit well under that) and pass a short footer list
    instead of the full city set — footer only renders the first 8 anyway.
    """
    all_cities = get_cities_with_data()
    cities = all_cities[:200]  # dropdown cap
    footer_cities = all_cities[:20]  # footer shows <=8
    return render_template('get_alerts.html', cities=cities, footer_cities=footer_cities)


@city_pages_bp.route('/privacy')
def privacy_page():
    """Render the Privacy Policy page."""
    footer_cities = get_cities_with_data()
    return render_template('privacy.html', footer_cities=footer_cities)


@city_pages_bp.route('/terms')
def terms_page():
    """Render the Terms of Service page."""
    footer_cities = get_cities_with_data()
    return render_template('terms.html', footer_cities=footer_cities)


@city_pages_bp.route('/about')
def about_page():
    """Render the About page. V13.6: Pass city_count for consistency."""
    footer_cities = get_cities_with_data()
    city_count = get_total_city_count_auto()
    return render_template('about.html', footer_cities=footer_cities, city_count=city_count)


@city_pages_bp.route('/stats')
def stats_page():
    """V12.51: Render building permit statistics page (SQL-backed)."""
    conn = permitdb.get_connection()
    footer_cities = get_cities_with_data()

    # Get totals from SQLite
    stats = permitdb.get_permit_stats()
    total_permits = stats['total_permits']
    total_value = stats['total_value']
    high_value_count = stats['high_value_count']

    # Top cities by permit count
    top_cities_rows = conn.execute("""
        SELECT city, state, COUNT(*) as permit_count, SUM(COALESCE(estimated_cost, 0)) as total_value
        FROM permits WHERE city IS NOT NULL AND city != ''
        GROUP BY city, state ORDER BY permit_count DESC LIMIT 10
    """).fetchall()
    top_cities = []
    for row in top_cities_rows:
        top_cities.append({
            'name': row['city'],
            'state': row['state'] or '',
            'slug': row['city'].lower().replace(' ', '-'),
            'permit_count': row['permit_count'],
            'total_value': row['total_value'] or 0,
            'avg_value': (row['total_value'] or 0) / row['permit_count'] if row['permit_count'] > 0 else 0
        })

    # Trade breakdown
    trade_rows = conn.execute("""
        SELECT trade_category, COUNT(*) as cnt FROM permits
        WHERE trade_category IS NOT NULL AND trade_category != ''
        GROUP BY trade_category ORDER BY cnt DESC
    """).fetchall()
    trade_breakdown = [
        {'name': row['trade_category'], 'count': row['cnt'],
         'percentage': (row['cnt'] / total_permits * 100) if total_permits > 0 else 0}
        for row in trade_rows
    ]

    return render_template('stats.html',
                           total_permits=total_permits,
                           total_value=total_value,
                           high_value_count=high_value_count,
                           city_count=get_total_city_count_auto(),
                           top_cities=top_cities,
                           trade_breakdown=trade_breakdown,
                           last_updated=datetime.now().strftime('%Y-%m-%d'),
                           footer_cities=footer_cities)


@city_pages_bp.route('/map')
def map_page():
    """V12.26: Interactive permit heat map with Leaflet.js."""
    user = get_current_user()
    is_pro = user and user.plan == 'pro'
    cities = get_all_cities_info()
    footer_cities = get_cities_with_data()
    city_count = get_total_city_count_auto()  # V13.9: Pass for dynamic meta desc
    return render_template('map.html',
                           is_pro=is_pro,
                           cities=cities,
                           footer_cities=footer_cities,
                           city_count=city_count)


@city_pages_bp.route('/contact')
def contact_page():
    """Render the Contact page."""
    footer_cities = get_cities_with_data()
    return render_template('contact.html', footer_cities=footer_cities)


@city_pages_bp.route('/analytics')
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


@city_pages_bp.route('/early-intel')
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


@city_pages_bp.route('/success', methods=['GET'])
def checkout_success():
    """V218 T5D: Dedicated post-payment landing page. Stripe success_url
    redirects here with ?session_id=cs_xxx. We don't verify the session
    server-side (the webhook handler does that for real subscription
    state) — this page is purely the customer-facing confirmation."""
    return render_template('success.html')


@city_pages_bp.route('/my-leads')
def my_leads_page():
    """Render the My Leads CRM page."""
    user = get_current_user()
    if not user:
        # Redirect to login with message
        return redirect('/login?redirect=my-leads')

    footer_cities = get_cities_with_data()
    return render_template('my_leads.html', user=user, footer_cities=footer_cities)


@city_pages_bp.route('/saved-searches')
def saved_searches_page():
    """Render the Saved Searches page."""
    user = get_current_user()
    if not user:
        return redirect('/login?redirect=saved-searches')

    searches = get_user_saved_searches(user['email'])
    footer_cities = get_cities_with_data()
    return render_template('saved_searches.html', user=user, searches=searches, footer_cities=footer_cities)


@city_pages_bp.route('/permits/<state_slug>')
def state_or_city_landing(state_slug):
    """Route that handles both state hub pages and city landing pages."""
    # V476 Bug 1: /permits/cities was falling through the wildcard slug
    # handler and rendering the empty-city "Coming Soon" template — the
    # "Browse All Cities" button in nav/footer was effectively a dead
    # link. The real directory lives at /cities; 301 redirect there so
    # link equity flows and visitors land on the working page.
    if state_slug == 'cities':
        return redirect('/cities', code=301)
    # V309 (CODE_V280b Bug 23): slug alias → 301 redirect BEFORE state lookup
    # so that /permits/miami-dade → /permits/miami-dade-county, etc. SEO-safe
    # 301 so the old URL's link equity flows to the canonical one.
    if state_slug in _PERMIT_SLUG_ALIASES:
        return redirect(f'/permits/{_PERMIT_SLUG_ALIASES[state_slug]}', code=301)
    # Check if it's a state slug first
    if state_slug in STATE_CONFIG:
        state_data = get_state_data(state_slug)
        if state_data and state_data['cities']:
            footer_cities = get_cities_with_data()

            # V14.0: Find blog posts for cities in this state
            state_blog_posts = []
            state_abbrev = STATE_CONFIG[state_slug]['abbrev'].lower()
            blog_dir = os.path.join(os.path.dirname(__file__), 'blog')
            for city in state_data['cities'][:20]:  # Check top 20 cities
                city_slug = city.get('slug', city['name'].lower().replace(' ', '-').replace(',', ''))
                blog_slug = f"building-permits-{city_slug}-{state_abbrev}-contractor-guide"
                blog_path = os.path.join(blog_dir, f"{blog_slug}.md")
                if os.path.exists(blog_path):
                    state_blog_posts.append({
                        'name': city['name'],
                        'url': f"/blog/{blog_slug}"
                    })

            return render_template('state_landing.html',
                                   footer_cities=footer_cities,
                                   state_blog_posts=state_blog_posts,
                                   **state_data)

    # Otherwise, fall through to city landing page logic
    return city_landing_inner(state_slug)


@city_pages_bp.route('/permits/<state_slug>/<city_slug>')
def state_city_landing(state_slug, city_slug):
    """V77: Render SEO-optimized city landing page with state/city URL format.

    URL format: /permits/{state}/{city} e.g., /permits/texas/fort-worth

    This is the primary city page route for SEO. Each city page targets
    "[city] building permits" keywords for contractors.
    """
    # V231 P1-9: state-slug aliases. STATE_CONFIG keys "New York" as
    # 'new-york-state' because the bare 'new-york' slug is reserved for
    # NYC in the 1-segment city route. But the 2-segment
    # /permits/<state>/<city> pattern sends 'new-york' as the state —
    # so without this alias every /permits/new-york/new-york-city hit
    # 404'd. Same family of aliases a human would naturally type.
    _STATE_SLUG_ALIASES = {
        'new-york': 'new-york-state',
        'ny': 'new-york-state',
    }
    state_slug_key = _STATE_SLUG_ALIASES.get(state_slug, state_slug)

    # Check if state_slug is a valid state
    if state_slug_key not in STATE_CONFIG:
        # Not a valid state — fall through to city/trade route
        # by calling city_trade_landing directly
        return city_trade_landing(state_slug, city_slug)

    state_abbrev = STATE_CONFIG[state_slug_key]['abbrev']
    state_name = STATE_CONFIG[state_slug_key]['name']

    # V156: Slug aliases for cities where URL slug differs from DB slug
    _SLUG_ALIASES = {
        'new-york': 'new-york-city',
        'chicago': 'chicago-il',
        'washington': 'washington-dc',
        'washington-dc': 'washington-dc',
        'little-rock': 'little-rock-ar',
        'mesa': 'mesa-az-accela',
    }
    city_slug = _SLUG_ALIASES.get(city_slug, city_slug)

    # Look up city in prod_cities first (authoritative source)
    conn = permitdb.get_connection()
    city_row = conn.execute("""
        SELECT id, city, state, city_slug, source_id, source_type, total_permits,
               newest_permit_date, last_collection, data_freshness, status
        FROM prod_cities
        WHERE city_slug = ? AND state = ?
    """, (city_slug, state_abbrev)).fetchone()

    if not city_row:
        # Try without state filter (some cities might not have state stored correctly)
        city_row = conn.execute("""
            SELECT id, city, state, city_slug, source_id, source_type, total_permits,
                   newest_permit_date, last_collection, data_freshness, status
            FROM prod_cities WHERE city_slug = ?
        """, (city_slug,)).fetchone()

    if not city_row:
        # Fall back to CITY_REGISTRY lookup
        city_key, city_config = get_city_by_slug_auto(city_slug)
        if not city_config:
            return render_city_not_found(city_slug)
        city_name = city_config['name']
        city_state = city_config.get('state', state_abbrev)
        total_permits = 0
        newest_permit_date = None
        last_collection = None
        data_freshness = 'no_data'
        is_active = city_config.get('active', False)
    else:
        city_name = city_row['city']
        city_state = city_row['state']
        total_permits = city_row['total_permits'] or 0
        newest_permit_date = city_row['newest_permit_date']
        last_collection = city_row['last_collection']
        data_freshness = city_row['data_freshness'] or 'no_data'
        is_active = city_row['status'] == 'active'

    # V160: Get recent permits using prod_city_id FK (not city name string match)
    prod_city_id = city_row['id'] if city_row else None
    if prod_city_id:
        permits_cursor = conn.execute("""
            SELECT * FROM permits
            WHERE prod_city_id = ?
            ORDER BY filing_date DESC, estimated_cost DESC
            LIMIT 50
        """, (prod_city_id,))
        recent_permits = [dict(row) for row in permits_cursor]
    else:
        recent_permits = []

    # Fallback: try city name match if FK returned nothing
    if not recent_permits:
        filter_name = city_name
        permits_cursor = conn.execute("""
            SELECT * FROM permits
            WHERE city = ? AND state = ?
            ORDER BY filing_date DESC, estimated_cost DESC
            LIMIT 50
        """, (filter_name, city_state))
        recent_permits = [dict(row) for row in permits_cursor]

    # V160: Get permit stats using prod_city_id
    if prod_city_id:
        stats_row = conn.execute("""
            SELECT COUNT(*) as permit_count,
                   MIN(filing_date) as earliest_date,
                   MAX(filing_date) as latest_date
            FROM permits WHERE prod_city_id = ?
        """, (prod_city_id,)).fetchone()
    else:
        filter_name = city_name
        stats_row = conn.execute("""
            SELECT COUNT(*) as permit_count,
                   MIN(filing_date) as earliest_date,
                   MAX(filing_date) as latest_date
            FROM permits WHERE city = ?
        """, (filter_name,)).fetchone()

    permit_count = stats_row['permit_count'] if stats_row else 0
    earliest_date = stats_row['earliest_date'] if stats_row else None
    latest_date = stats_row['latest_date'] if stats_row else None

    # V476 Bug 2: align freshness with city_landing_inner (the slug
    # route). prod_cities.newest_permit_date can be NULL or stale —
    # /permits/arizona/phoenix was showing "2025-2026" red and "Data
    # not yet available" while /permits/phoenix-az showed "Updated
    # recently 2026-04-25" green. Same data, different freshness.
    # Override with latest_date (= MAX(filing_date) from permits) when
    # it's newer than newest_permit_date, and refresh data_freshness
    # to match.
    if permit_count > 0 and latest_date:
        if not newest_permit_date or str(latest_date)[:10] > str(newest_permit_date)[:10]:
            newest_permit_date = latest_date
        # Recompute data_freshness from the corrected newest_permit_date.
        try:
            _age_days = (
                datetime.now().date()
                - datetime.strptime(str(newest_permit_date)[:10], '%Y-%m-%d').date()
            ).days
            if _age_days <= 7:
                data_freshness = 'fresh'
            elif _age_days <= 30:
                data_freshness = 'aging'
            else:
                data_freshness = 'stale'
        except Exception:
            pass

    # V160: Get permit types breakdown using prod_city_id
    if prod_city_id:
        types_cursor = conn.execute("""
            SELECT COALESCE(permit_type, 'Other') as ptype, COUNT(*) as cnt
            FROM permits WHERE prod_city_id = ?
            GROUP BY permit_type ORDER BY cnt DESC LIMIT 10
        """, (prod_city_id,))
    else:
        types_cursor = conn.execute("""
            SELECT COALESCE(permit_type, 'Other') as ptype, COUNT(*) as cnt
            FROM permits WHERE city = ?
            GROUP BY permit_type ORDER BY cnt DESC LIMIT 10
        """, (city_name,))
    permit_types = {row['ptype']: row['cnt'] for row in types_cursor}

    # Get nearby cities in same state for internal linking
    nearby_cities = conn.execute("""
        SELECT city_slug, city, total_permits
        FROM prod_cities
        WHERE state = ? AND city_slug != ? AND status = 'active' AND total_permits > 0
        ORDER BY total_permits DESC
        LIMIT 10
    """, (city_state, city_slug)).fetchall()

    # Format display name
    display_name = format_city_name(city_name)

    # V475 Bug 5: empty-city early exit. Mirrors the city_landing_inner
    # fix for the /permits/<state>/<city> route — without this, empty
    # cities (e.g. /permits/wisconsin/verona) run a dozen expensive
    # queries before producing nothing useful, and bot traffic was
    # piling these up behind the daemon's write lock.
    try:
        _row = conn.execute(
            "SELECT COUNT(*) FROM permits WHERE source_city_key = ? LIMIT 1",
            (city_slug,)
        ).fetchone()
        _v475_permit_count = _row[0] if _row else 0
        if _v475_permit_count == 0:
            _row = conn.execute(
                "SELECT COUNT(*) FROM contractor_profiles WHERE source_city_key = ? LIMIT 1",
                (city_slug,)
            ).fetchone()
            _v475_profile_count = _row[0] if _row else 0
            if _v475_profile_count == 0:
                from flask import make_response as _mk
                _resp = render_template(
                    'city_paused.html',
                    city_name=display_name,
                    state=city_state,
                    last_updated=None,
                    canonical_url=f"{SITE_URL}/permits/{state_slug_key}/{city_slug}",
                    robots="noindex, follow",
                    is_coming_soon=True,
                )
                _r = _mk(_resp)
                _r.headers['Cache-Control'] = 'public, max-age=86400'
                return _r
    except Exception as _e:
        print(f"[V475] empty-city early-exit check failed for {city_slug}: {_e}", flush=True)

    # V475 Bug 1: hoist violations_count + violations_data init to BEFORE
    # the V474 meta block. Previously these were initialized at line ~1368
    # (after the V474 read), so every render hit
    # UnboundLocalError on the violations_count reference. The actual
    # population query still runs further down — these are just defaults
    # so the V474 code path can read them safely.
    violations_count = 0
    violations_data = []

    # V474 (CODE_V474_BUYER_PERSONAS Section B+C): data-driven meta title +
    # description + FAQ JSON-LD. The V156 hard-coded SEO maps below are
    # kept as a fallback for the 19 anchor cities, but every city now
    # gets a data-aware meta title/description and FAQ schema based on
    # what data is actually present (profiles / phones / violations / owners).
    _v474_profiles_count = 0
    _v474_phones_count = 0
    _v474_owners_count = 0
    # V474b: positional access for safety across row_factory variants
    try:
        _row = conn.execute(
            "SELECT COUNT(*), "
            "SUM(CASE WHEN phone IS NOT NULL AND phone <> '' THEN 1 ELSE 0 END) "
            "FROM contractor_profiles WHERE source_city_key = ?",
            (city_slug,)
        ).fetchone()
        if _row:
            _v474_profiles_count = _row[0] or 0
            _v474_phones_count = _row[1] or 0
    except Exception as _e:
        print(f"[V474] profiles count failed for {city_slug}: {_e}", flush=True)
    try:
        _row = conn.execute(
            "SELECT COUNT(*) FROM property_owners "
            "WHERE LOWER(city) = LOWER(?) AND state = ?",
            (city_name, city_state)
        ).fetchone()
        _v474_owners_count = _row[0] if _row else 0
    except Exception as _e:
        print(f"[V474] owners count failed for {city_slug}: {_e}", flush=True)

    # V156: SEO-optimized meta for top cities, generic fallback for others
    _pc = f"{int(permit_count or 0):,}"
    # V231 P2-10: every title includes ", <state>" for SEO parity with
    # the CITY_SEO_CONFIG path. LA was showing a bare "Los Angeles"
    # title while NYC had ", NY" — inconsistent and hurts the
    # "<city> <state> building permits" search match.
    # V382 (loop /CODE_V286 grind): the previous map covered only 5 of
    # the 13 ad-ready cities (CLAUDE.md North Star). 8 cities — the ones
    # most likely to convert ad clicks because they have phones AND
    # violations AND fresh permits — were falling through to the
    # generic "Browse recent building permits in {city}" snippet that
    # tells Google nothing distinctive about the page. Filled in
    # Phoenix, Miami-Dade, Henderson, Anaheim, Cleveland, San Jose,
    # Buffalo, Nashville, Orlando.
    _SEO_TITLES = {
        ('New York City', 'NY'): 'New York City, NY Building Permits & Contractor Leads — Daily Updates | PermitGrab',
        ('Los Angeles', 'CA'): 'Los Angeles, CA Building Permits & Contractor Leads | PermitGrab',
        ('Chicago', 'IL'): 'Chicago, IL Building Permits & Contractor Leads | PermitGrab',
        ('Austin', 'TX'): 'Austin, TX Building Permits & Contractor Leads — Updated Daily | PermitGrab',
        ('San Antonio', 'TX'): 'San Antonio, TX Building Permits & Contractor Leads | PermitGrab',
        ('Mesa', 'AZ'): 'Mesa, AZ Building Permits & Contractor Leads | PermitGrab',
        ('Fort Worth', 'TX'): 'Fort Worth, TX Building Permits & Contractor Leads | PermitGrab',
        ('Washington', 'DC'): 'Washington, DC Building Permits & Contractor Leads | PermitGrab',
        ('Little Rock', 'AR'): 'Little Rock, AR Building Permits & Contractor Leads | PermitGrab',
        ('Cape Coral', 'FL'): 'Cape Coral, FL Building Permits & Contractor Leads | PermitGrab',
        ('Phoenix', 'AZ'): 'Phoenix, AZ Building Permits & Contractor Leads — Daily Updates | PermitGrab',
        ('Miami-Dade County', 'FL'): 'Miami-Dade Building Permits & Contractor Leads — Daily Updates | PermitGrab',
        ('Henderson', 'NV'): 'Henderson, NV Building Permits & Contractor Leads | PermitGrab',
        ('Anaheim', 'CA'): 'Anaheim, CA Building Permits & Contractor Leads | PermitGrab',
        ('Cleveland', 'OH'): 'Cleveland, OH Building Permits & Contractor Leads | PermitGrab',
        ('San Jose', 'CA'): 'San Jose, CA Building Permits & Contractor Leads | PermitGrab',
        ('Buffalo', 'NY'): 'Buffalo, NY Building Permits & Contractor Leads | PermitGrab',
        ('Nashville', 'TN'): 'Nashville, TN Building Permits & Contractor Leads | PermitGrab',
        ('Orlando', 'FL'): 'Orlando, FL Building Permits & Contractor Leads | PermitGrab',
    }
    _SEO_METAS = {
        ('New York City', 'NY'): f'Track {_pc}+ NYC building permits updated daily. Find DOB permits, code violations, and contractor leads by address. 14-day free trial.',
        ('Los Angeles', 'CA'): f'Search {_pc}+ LA building permits. Find LADBS permits, code enforcement cases, and construction leads daily. 14-day free trial.',
        ('Chicago', 'IL'): f'Track {_pc}+ Chicago building permits and code violations. Find new construction projects and contractor leads updated daily.',
        ('Austin', 'TX'): f'Search {_pc}+ Austin TX building permits. Track new construction, code enforcement cases, and find contractor leads. Free trial.',
        ('San Antonio', 'TX'): f'Track {_pc}+ San Antonio building permits updated daily. Find new construction projects and contractor leads by trade.',
        ('Mesa', 'AZ'): f'Search {_pc}+ Mesa building permits. Track new construction projects, code enforcement cases, and find leads daily.',
        ('Fort Worth', 'TX'): f'Track {_pc}+ Fort Worth building permits and code violations. Find new construction projects and leads updated daily.',
        ('Washington', 'DC'): f'Search {_pc}+ DC building permits updated daily. Find construction projects and contractor leads in the DMV area.',
        ('Little Rock', 'AR'): f'Track {_pc}+ Little Rock building permits. Find new construction projects and code enforcement leads updated daily.',
        ('Cape Coral', 'FL'): f'Search {_pc}+ Cape Coral FL building permits. Track new construction projects and find contractor leads daily.',
        ('Phoenix', 'AZ'): f'Track {_pc}+ Phoenix building permits, code violations, and contractor leads. Find new construction and remodels updated daily. 14-day free trial.',
        ('Miami-Dade County', 'FL'): f'Search {_pc}+ Miami-Dade building permits. Find permits with phone numbers, code violations, and property owner data. 14-day free trial.',
        ('Henderson', 'NV'): f'Track {_pc}+ Henderson NV building permits with contractor phone numbers inline. Find new construction and renovations daily.',
        ('Anaheim', 'CA'): f'Track {_pc}+ Anaheim building permits with contractor phone numbers, code enforcement cases, and CSLB-licensed contractors.',
        ('Cleveland', 'OH'): f'Search {_pc}+ Cleveland building permits. Find new construction, code violations, and contractor leads updated daily.',
        ('San Jose', 'CA'): f'Track {_pc}+ San Jose building permits, code violations, and CSLB-licensed contractors with phone numbers.',
        ('Buffalo', 'NY'): f'Track {_pc}+ Buffalo NY building permits and contractor leads. Phone numbers from NY DOL license database. Updated daily.',
        ('Nashville', 'TN'): f'Search {_pc}+ Nashville building permits and contractor leads. Find new construction and remodel projects updated daily.',
        ('Orlando', 'FL'): f'Track {_pc}+ Orlando FL building permits, code violations, and FL DBPR-licensed contractors with phone numbers.',
    }
    _key = (city_name, city_state)
    meta_title = _SEO_TITLES.get(_key, f"{display_name}, {state_name} Building Permits | PermitGrab")
    meta_description = _SEO_METAS.get(_key, f"Browse recent building permits in {display_name}, {state_name}. Track new construction, renovations, and remodeling permits updated daily. Built for contractors and builders.")

    # V474 Section B: pivot meta to buyer-intent language when the city has
    # the data to back it up. Picks one of four templates based on which
    # data dimensions are populated. Falls through to the V156 hard-coded
    # entries above for the 19 anchor cities (those have hand-tuned copy
    # we don't want to clobber). For every OTHER city, this is a buyer-
    # intent rewrite using real numbers.
    _v474_use_dynamic = _key not in _SEO_TITLES
    if _v474_use_dynamic:
        _has_profiles = _v474_profiles_count >= 100
        _has_phones = _v474_phones_count >= 50
        _has_violations = (violations_count or 0) > 0
        _has_owners = _v474_owners_count > 0
        _vp = f"{_v474_profiles_count:,}"
        _vh = f"{_v474_phones_count:,}"
        _vv = f"{(violations_count or 0):,}"
        _vo = f"{_v474_owners_count:,}"
        if _has_profiles and _has_violations and _has_owners:
            meta_title = (
                f"{display_name} Construction Leads — {_vp} Contractors, "
                f"{_vv} Violations | PermitGrab"
            )[:70]
            meta_description = (
                f"Access {_vp} contractor profiles{' with phone numbers' if _has_phones else ''}, "
                f"{_vv} code violation properties, and {_vo} property owner "
                f"records in {display_name}. Updated daily from official "
                f"sources. $149/mo."
            )[:200]
        elif _has_profiles and _has_phones and not _has_violations:
            meta_title = (
                f"{display_name} Contractor Leads — {_vp} Active "
                f"Contractors with Contact Info | PermitGrab"
            )[:70]
            meta_description = (
                f"Reach {_vp} active contractors in {display_name} — "
                f"{_vh} with verified phone numbers. Filter by trade, "
                f"see new permits daily. $149/mo."
            )[:200]
        elif _has_violations and not _has_profiles:
            meta_title = (
                f"{display_name} Code Violation Properties — {_vv} "
                f"Distressed Property Records | PermitGrab"
            )[:70]
            meta_description = (
                f"{_vv} code violation properties in {display_name} — find "
                f"motivated sellers and distressed properties{' with owner names' if _has_owners else ''}. "
                f"Updated daily from city code enforcement. $149/mo."
            )[:200]
        elif _has_profiles:
            meta_title = (
                f"{display_name} Building Permit Activity — {_vp} "
                f"Active Contractors | PermitGrab"
            )[:70]
            meta_description = (
                f"Track {_pc} building permits and {_vp} active "
                f"contractors in {display_name}. Updated daily from "
                f"official city data. $149/mo."
            )[:200]
        elif _has_owners:
            # V474b: owners-only cities — homeowner-lead angle.
            meta_title = (
                f"{display_name} Property Owner Records — {_vo} "
                f"Homeowner Leads | PermitGrab"
            )[:70]
            meta_description = (
                f"Reach {_vo} property owners in {display_name} with "
                f"names and addresses. Ideal homeowner leads for solar, "
                f"insurance, and home services. $149/mo."
            )[:200]
        # else: leave the V156 fallback strings as-is

    # V474 Section C: FAQ JSON-LD with buyer-intent questions. Only include
    # the question variants the city actually has data for.
    _v474_faq = []
    if _v474_profiles_count > 0:
        _v474_faq.append((
            f"How can I find contractors in {display_name}?",
            f"PermitGrab tracks {_v474_profiles_count:,} active contractors "
            f"in {display_name} who have pulled building permits. "
            f"{_v474_phones_count:,} have verified phone numbers. Filter by "
            f"trade: electrical, plumbing, HVAC, roofing, and more."
        ))
    if (violations_count or 0) > 0:
        _v474_faq.append((
            f"Where can I find code violation properties in {display_name}?",
            f"PermitGrab has {(violations_count or 0):,} code violation "
            f"records in {display_name} from official city code enforcement "
            f"databases. These properties may indicate motivated sellers — "
            f"owners facing violations are often willing to sell below "
            f"market value."
        ))
    if _v474_owners_count > 0:
        _v474_faq.append((
            f"How do I get homeowner leads from building permits in {display_name}?",
            f"PermitGrab provides {_v474_owners_count:,} property owner "
            f"records for {display_name} including owner names and "
            f"addresses. Homeowners who just pulled permits are actively "
            f"investing in their property — ideal leads for solar, "
            f"insurance, and home service companies."
        ))
    _v474_faq.append((
        f"How often is the {display_name} permit data updated?",
        f"PermitGrab collects new permit data for {display_name} every "
        f"30 minutes from official city open data portals. Violation and "
        f"property owner data is refreshed daily to monthly depending on "
        f"the source."
    ))
    _v474_faq_jsonld = {
        '@context': 'https://schema.org',
        '@type': 'FAQPage',
        'mainEntity': [
            {
                '@type': 'Question',
                'name': q,
                'acceptedAnswer': {'@type': 'Answer', 'text': a},
            } for q, a in _v474_faq
        ],
    }

    # Robots directive — V236 PR#6: thin-page suppression at <20 permits.
    robots_directive = "index, follow" if permit_count >= 20 and is_active else "noindex, follow"

    # Canonical URL — use the canonical state slug so /permits/new-york/..
    # and /permits/new-york-state/.. both point at the same canonical.
    canonical_url = f"{SITE_URL}/permits/{state_slug_key}/{city_slug}"

    footer_cities = get_cities_with_data()

    # V79: Get relevant blog posts for this city
    city_link = f"/permits/{state_slug}/{city_slug}"
    city_blog_posts = get_blog_posts_for_city(city_link)

    # V162: Get violation data for cities that have it
    violations_data = []
    violations_count = 0
    try:
        # Try prod_city_id first (V162 schema), fall back to city name (V156 schema)
        try:
            if prod_city_id:
                v_count = conn.execute("SELECT COUNT(*) as cnt FROM violations WHERE prod_city_id = ?", (prod_city_id,)).fetchone()
                violations_count = v_count['cnt'] if v_count else 0
        except Exception:
            pass
        if violations_count == 0:
            v_count = conn.execute("SELECT COUNT(*) as cnt FROM violations WHERE city = ? AND state = ?", (city_name, city_state)).fetchone()
            violations_count = v_count['cnt'] if v_count else 0
        if violations_count > 0:
            try:
                v_rows = conn.execute("""
                    SELECT violation_date, violation_type, COALESCE(violation_description, description, '') as violation_description,
                           status, address
                    FROM violations WHERE city = ? AND state = ?
                    ORDER BY violation_date DESC LIMIT 25
                """, (city_name, city_state)).fetchall()
                violations_data = [dict(r) for r in v_rows]
            except Exception:
                pass
    except Exception:
        pass

    # V182 PR2: top contractors (empty if city fails public filter)
    top_contractors = _get_top_contractors_for_city(city_slug, limit=25)

    # V229-hotfix: compute freshness_age_days for template (same calc as
    # city_landing_inner). Without this, city_landing_v77.html at line 672
    # throws UndefinedError and every /permits/<state>/<city> page 500s.
    _freshness_age_days = None
    if newest_permit_date:
        try:
            _freshness_age_days = (
                datetime.now().date()
                - datetime.strptime(str(newest_permit_date)[:10], '%Y-%m-%d').date()
            ).days
        except Exception:
            _freshness_age_days = None

    # V230 T1-T13: variable parity with city_landing_inner. Every kwarg
    # that route passes must appear here or the template falls through to
    # a broken/empty section (and one of them — freshness_age_days — was
    # 500ing before the V229 hotfix). Aliases and zero-defaults are fine
    # for the stats we don't compute here; the primary route can fill in
    # richer values.
    # V236 PR#5: data-driven insights paragraph.
    market_insights = _get_market_insights(
        prod_city_id=prod_city_id,
        city_name=city_name,
        city_state=city_state,
    )
    # V467 (CODE_V467 internal linking): if this city has a matching SEO blog
    # post, expose its slug so the template can render a "Read the report"
    # CTA. Internal-link parity boosts both pages in search rankings.
    _v467_blog_slugs = {
        'chicago-il': 'chicago-building-permits-2026',
        'miami-dade-county': 'miami-dade-solar-permits-2026',
        'phoenix-az': 'phoenix-code-violations-2026',
        'san-antonio-tx': 'san-antonio-building-permits-2026',
        'new-york-city': 'nyc-building-violations-2026',
    }
    _v467_seo_blog_slug = _v467_blog_slugs.get(city_slug)

    return render_template('city_landing_v77.html',
        property_owners=_get_property_owners(display_name, city_state, limit=10),  # V284
        seo_blog_slug=_v467_seo_blog_slug,
        city_name=display_name,
        city_slug=city_slug,
        state_abbrev=city_state,
        city_state=city_state,
        state_name=state_name,
        state_slug=state_slug,
        total_permits=total_permits,
        permit_count=permit_count,
        earliest_date=earliest_date,
        latest_date=latest_date,
        newest_permit_date=newest_permit_date,
        last_collection=last_collection,
        last_collected=last_collection,  # V230 T1: alias
        data_freshness=data_freshness,
        freshness_age_days=_freshness_age_days,  # V229-hotfix
        is_active=is_active,
        is_coming_soon=not is_active,  # V230 T11
        recent_permits=recent_permits,
        permits=recent_permits,  # V230: alias city_landing_inner uses
        top_contractors=top_contractors,  # V182 PR2
        permit_types=permit_types,
        trade_breakdown=permit_types,  # V230 T6: template references either
        nearby_cities=nearby_cities,
        other_cities=nearby_cities,  # V230 T7: alias
        meta_title=meta_title,
        meta_description=meta_description,
        seo_content=None,  # V230 T8: only computed by legacy city_landing_inner
        robots_directive=robots_directive,
        canonical_url=canonical_url,
        footer_cities=footer_cities,
        blog_posts=city_blog_posts,
        related_articles=city_blog_posts,  # V230 T10: alias
        violations=violations_data,
        violations_count=violations_count,
        # V230 T2-T5: stats the legacy route computes. Keeping these at 0
        # rather than running extra queries; if the template shows the
        # "Total Construction Value" card it'll read $0 which is accurate
        # until we wire per-city aggregates into this route.
        total_value=0,
        high_value_count=0,
        new_this_month=0,
        unique_contractors=0,
        # V230 T9: footer/copyright
        current_year=datetime.now().year,
        current_date=datetime.now().strftime('%Y-%m-%d'),
        current_week_start=(datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d'),  # V209-3: NEW badge
        # V230 T12/T13: safe defaults for optional sections
        top_neighborhoods=[],
        city_blog_url=None,
        top_trades=[],
        market_insights=market_insights,  # V236 PR#5
        v474_faq=_v474_faq,  # list of (question, answer) tuples for the
                             # rendered <details> FAQ section in the template
        v474_faq_jsonld=_v474_faq_jsonld,
    )


@city_pages_bp.route('/permits/<city_slug>/<trade_slug>')
def city_trade_landing(city_slug, trade_slug):
    """Render SEO-optimized city × trade landing page."""
    # V12.60: Use get_city_by_slug_auto() to match sitemap-generated URLs
    # (previously used get_city_by_slug which missed auto-discovered cities)
    city_key, city_config = get_city_by_slug_auto(city_slug)
    if not city_config:
        return render_city_not_found(city_slug)

    # Get trade from config
    trade = get_trade(trade_slug)
    if not trade:
        return render_city_not_found(trade_slug)

    # V12.51: Use SQLite for city permits
    conn = permitdb.get_connection()

    # V12.9: Format city name for display
    display_name = format_city_name(city_config['name'])

    # V14.1: Filter permits using SQL LIKE patterns for both city AND trade
    # This is more efficient than loading all city permits then filtering in Python
    city_name = city_config['name']
    matching_permits = []

    # V14.1: Get LIKE patterns for this trade from TRADE_MAPPING
    trade_patterns = TRADE_MAPPING.get(trade_slug, [f'%{trade_slug}%'])

    # V14.1: Build SQL query with trade patterns
    # Check description, permit_type, work_type, trade_category for trade keywords
    def query_trade_permits(city_filter, city_param):
        """Helper to query permits matching city filter AND trade patterns."""
        # Build OR conditions for trade patterns across multiple fields
        trade_conditions = []
        trade_params = []
        for pattern in trade_patterns:
            trade_conditions.append("""
                (LOWER(COALESCE(description, '')) LIKE ?
                 OR LOWER(COALESCE(permit_type, '')) LIKE ?
                 OR LOWER(COALESCE(work_type, '')) LIKE ?
                 OR LOWER(COALESCE(trade_category, '')) LIKE ?)
            """)
            trade_params.extend([pattern.lower(), pattern.lower(), pattern.lower(), pattern.lower()])

        trade_clause = " OR ".join(trade_conditions)
        sql = f"""
            SELECT * FROM permits
            WHERE {city_filter}
              AND ({trade_clause})
            ORDER BY filing_date DESC
            LIMIT 500
        """
        params = [city_param] + trade_params
        cursor = conn.execute(sql, params)
        return [dict(row) for row in cursor]

    # Strategy 1: Exact city match
    matching_permits = query_trade_permits("city = ?", city_name)

    # Strategy 2: Case-insensitive city match if no results
    if not matching_permits:
        matching_permits = query_trade_permits("LOWER(city) = LOWER(?)", city_name)

    # Strategy 3: Partial city match (without state suffix) if still no results
    if not matching_permits and ',' in city_name:
        base_name = city_name.split(',')[0].strip()
        matching_permits = query_trade_permits("LOWER(city) LIKE ?", f"%{base_name.lower()}%")

    # V14.1: Fallback to Python-side matching if SQL patterns didn't match
    # This handles edge cases where data format differs from expected patterns
    if not matching_permits:
        # Load city permits and filter with trade_configs keywords
        city_permits = []
        cursor = conn.execute(
            "SELECT * FROM permits WHERE LOWER(city) LIKE ? ORDER BY filing_date DESC LIMIT 2000",
            (f"%{city_name.split(',')[0].strip().lower()}%",)
        )
        city_permits = [dict(row) for row in cursor]

        trade_keywords = [kw.lower() for kw in trade['keywords']]
        for p in city_permits:
            text = ""
            for field in ['description', 'permit_type', 'work_type', 'trade_category']:
                if p.get(field):
                    text += p[field].lower() + " "
            if any(kw in text for kw in trade_keywords):
                matching_permits.append(p)
                if len(matching_permits) >= 500:
                    break

    # Results are already sorted by date from SQL

    # V30: Fallback — show recent city permits if no trade-specific matches found
    # This prevents empty trade pages which kill conversion and risk thin content penalties
    trade_fallback = False
    if not matching_permits:
        cursor = conn.execute(
            "SELECT * FROM permits WHERE LOWER(city) LIKE ? ORDER BY filing_date DESC LIMIT 20",
            (f"%{city_name.split(',')[0].strip().lower()}%",)
        )
        matching_permits = [dict(row) for row in cursor]
        trade_fallback = True

    # Calculate stats
    # V12.23: Use module-level datetime/timedelta imports
    now = datetime.now()
    month_ago = (now - timedelta(days=30)).isoformat()
    week_ago = (now - timedelta(days=7)).isoformat()

    monthly_count = len([p for p in matching_permits if (p.get('filing_date') or '') >= month_ago])
    weekly_count = len([p for p in matching_permits if (p.get('filing_date') or '') >= week_ago])

    values = [p.get('estimated_cost', 0) for p in matching_permits if p.get('estimated_cost')]
    avg_value = int(sum(values) / len(values)) if values else 0

    # Build city dict for template with formatted name
    city_dict = {
        "name": display_name,
        "state": city_config['state'],
        "slug": city_slug,
    }

    # V231 P0-8: pass avg_value as a number (or None). V230 changed the
    # template to do number formatting ($1.2M / $1,234) but the route was
    # still passing a pre-formatted string ("1,234" / "N/A"), which blew
    # up the new Jinja `avg_value > 0` comparison and 503'd every trade
    # page. Template now does the number formatting.
    stats = {
        "monthly_count": monthly_count or len(matching_permits),
        "weekly_count": weekly_count,
        "avg_value": avg_value if avg_value else None,
    }

    # Other trades for cross-linking (exclude current)
    other_trades = [t for t in get_all_trades() if t['slug'] != trade_slug]

    # Other cities for cross-linking (exclude current)
    other_cities = [{"name": c['name'], "slug": c['slug']} for c in ALL_CITIES if c['slug'] != city_slug]

    # V227 T9: Trade-page indexation policy.
    # Google's 2025-2026 core updates have been deindexing programmatic
    # pages that only differ by city/trade name in a data table. V222
    # dropped noindex from any page with >=1 permits; this tightens to
    # >=20 permits as the threshold for an indexable page (below that
    # the page is thin enough that Google treats it as template spam and
    # it hurts the site's overall crawl score). The remaining thin pages
    # stay reachable by internal link but carry noindex.
    _MIN_INDEX_PERMITS = 20
    robots_directive = (
        "noindex, follow"
        if (trade_fallback or len(matching_permits) < _MIN_INDEX_PERMITS)
        else "index, follow"
    )

    # V227 T9: Build an insights paragraph for the pages that ARE indexable.
    # Pure template pages with just a data table rank poorly; a prose
    # paragraph that can't exist on any other page is the cheapest way to
    # make Google treat this as real content.
    trade_insights = None
    if robots_directive == "index, follow" and len(matching_permits) >= _MIN_INDEX_PERMITS:
        try:
            _t_conn = permitdb.get_connection()
            # Last 30d vs prior 30d (for trend)
            _this_30 = _t_conn.execute(
                "SELECT COUNT(*) FROM permits WHERE source_city_key = ? "
                "AND trade_category = ? AND filing_date >= date('now','-30 days')",
                (city_slug, trade.get('name', ''))).fetchone()[0]
            _prior_30 = _t_conn.execute(
                "SELECT COUNT(*) FROM permits WHERE source_city_key = ? "
                "AND trade_category = ? AND filing_date >= date('now','-60 days') "
                "AND filing_date < date('now','-30 days')",
                (city_slug, trade.get('name', ''))).fetchone()[0]
            _six_mo = _t_conn.execute(
                "SELECT COUNT(*) FROM permits WHERE source_city_key = ? "
                "AND trade_category = ? AND filing_date >= date('now','-180 days')",
                (city_slug, trade.get('name', ''))).fetchone()[0]
            _top_contractors = [r[0] for r in _t_conn.execute(
                "SELECT contractor_name, COUNT(*) c FROM permits "
                "WHERE source_city_key = ? AND trade_category = ? "
                "AND contractor_name IS NOT NULL AND contractor_name != '' "
                "GROUP BY contractor_name ORDER BY c DESC LIMIT 3",
                (city_slug, trade.get('name', ''))).fetchall()]

            trend = "steady"
            if _prior_30 > 0:
                _delta = (_this_30 - _prior_30) / _prior_30 * 100
                if _delta > 20:
                    trend = "accelerating"
                elif _delta < -20:
                    trend = "slowing"
            trade_insights = {
                "trend": trend,
                "this_month": _this_30,
                "prior_month": _prior_30,
                "avg_monthly": round((_six_mo or 0) / 6.0, 1),
                "top_contractors": _top_contractors,
            }
        except Exception as e:
            print(f"[V227] trade_insights error: {e}")

    # V14.0: Get state info for internal linking
    state_abbrev = city_config.get('state', '')
    state_slug = None
    state_name = state_abbrev  # Fallback to abbrev
    for slug, info in STATE_CONFIG.items():
        if info['abbrev'] == state_abbrev:
            state_slug = slug
            state_name = info['name']
            break

    # V14.0: Check if city blog post exists
    city_blog_url = None
    if state_abbrev:
        blog_slug = f"building-permits-{city_slug}-{state_abbrev.lower()}-contractor-guide"
        blog_path = os.path.join(os.path.dirname(__file__), 'blog', f"{blog_slug}.md")
        if os.path.exists(blog_path):
            city_blog_url = f"/blog/{blog_slug}"

    return render_template(
        'city_trade_landing.html',
        city=city_dict,
        trade=trade,
        permits=matching_permits[:10],
        stats=stats,
        other_trades=other_trades,
        other_cities=other_cities,
        robots_directive=robots_directive,
        state_slug=state_slug,
        state_name=state_name,
        city_blog_url=city_blog_url,
        trade_fallback=trade_fallback,
        trade_insights=trade_insights,  # V227 T9: per-page prose data insights
        # V253: canonical override honored by the template so the V252 F7
        # trade-first URL (/solar/chicago-il) renders with its own canonical.
        canonical_url=getattr(g, 'canonical_url_override', None),
    )


@city_pages_bp.route('/search')
def search_page():
    """V28: Search page for SearchAction schema.
    Redirects to cities browse filtered by query, or shows permits filtered by query.
    """
    query = request.args.get('q', '').strip()
    if not query:
        return redirect('/cities')

    # Try to find a matching city
    all_cities = get_cities_with_data()
    query_lower = query.lower()

    # Direct city match (exact query == city name)
    for city in all_cities:
        city_name = city.get('name', '') or city.get('city', '')
        if city_name.lower() == query_lower:
            slug = city.get('slug', city_name.lower().replace(' ', '-'))
            return redirect(f'/permits/{slug}')

    # City name appears in query (e.g. "denver roofing" contains "denver")
    best_match = None
    best_len = 0
    for city in all_cities:
        city_name = (city.get('name', '') or city.get('city', '')).lower()
        if city_name and city_name in query_lower and len(city_name) > best_len:
            best_match = city
            best_len = len(city_name)
    if best_match:
        slug = best_match.get('slug', (best_match.get('name', '') or best_match.get('city', '')).lower().replace(' ', '-'))
        # Check if query has a trade keyword after the city name
        remainder = query_lower.replace((best_match.get('name', '') or best_match.get('city', '')).lower(), '').strip()
        if remainder:
            # Try to match a trade — redirect to city+trade page
            return redirect(f'/permits/{slug}/{remainder.replace(" ", "-")}')
        return redirect(f'/permits/{slug}')

    # Query appears in a city name (e.g. "den" matches "denver")
    matching_cities = [c for c in all_cities if query_lower in (c.get('name', '') or c.get('city', '')).lower()]
    if matching_cities:
        city = matching_cities[0]
        slug = city.get('slug', (city.get('name', '') or city.get('city', '')).lower().replace(' ', '-'))
        return redirect(f'/permits/{slug}')

    # No match - redirect to cities browse
    return redirect('/cities')


_V472_STATE_NAMES = {
    'AL': 'Alabama', 'AK': 'Alaska', 'AZ': 'Arizona', 'AR': 'Arkansas',
    'CA': 'California', 'CO': 'Colorado', 'CT': 'Connecticut', 'DE': 'Delaware',
    'DC': 'District of Columbia', 'FL': 'Florida', 'GA': 'Georgia', 'HI': 'Hawaii',
    'ID': 'Idaho', 'IL': 'Illinois', 'IN': 'Indiana', 'IA': 'Iowa',
    'KS': 'Kansas', 'KY': 'Kentucky', 'LA': 'Louisiana', 'ME': 'Maine',
    'MD': 'Maryland', 'MA': 'Massachusetts', 'MI': 'Michigan', 'MN': 'Minnesota',
    'MS': 'Mississippi', 'MO': 'Missouri', 'MT': 'Montana', 'NE': 'Nebraska',
    'NV': 'Nevada', 'NH': 'New Hampshire', 'NJ': 'New Jersey', 'NM': 'New Mexico',
    'NY': 'New York', 'NC': 'North Carolina', 'ND': 'North Dakota', 'OH': 'Ohio',
    'OK': 'Oklahoma', 'OR': 'Oregon', 'PA': 'Pennsylvania', 'RI': 'Rhode Island',
    'SC': 'South Carolina', 'SD': 'South Dakota', 'TN': 'Tennessee', 'TX': 'Texas',
    'UT': 'Utah', 'VT': 'Vermont', 'VA': 'Virginia', 'WA': 'Washington',
    'WV': 'West Virginia', 'WI': 'Wisconsin', 'WY': 'Wyoming',
    'AS': 'American Samoa', 'GU': 'Guam', 'MP': 'Northern Mariana Islands',
    'PR': 'Puerto Rico', 'VI': 'U.S. Virgin Islands',
}


# V475 Bug 2: module-level full-response cache for /cities. The route
# does ~5 large scans across prod_cities + property_owners +
# contractor_profiles, then ~30K Python iterations to build the badge
# state per city. Cold-cache renders were 19-30s and timing out under
# bot load. Render the page once every 10 min and serve the cached
# response for everyone in between.
_CITIES_BROWSE_CACHE = {'response': None, 'expires_at': 0}
_CITIES_BROWSE_TTL = 600  # 10 min


@city_pages_bp.route('/cities')
def cities_browse():
    """V17e + V472 + V473: Dedicated browse page for all cities, organized by state.

    V473 fixes the V472 bugs:
      1. Filters prod_cities to status='active' (~2,200 collection-target
         cities) instead of returning all 20,000+ rows. The 'paused' /
         'pending' rows are mostly seeded-from-us_cities entries that
         were never wired up — including them surfaced thousands of
         "0 permits" cards on the directory.
      2. Featured cards (hero section) require permits>=1000 AND
         profiles>=50, so bulk-misattribution junk like "Lindley NY
         40,966 permits / 0 profiles" no longer hijacks the top tier.
      3. Owner ("O") badge detection now also parses
         permit_record:<slug> entries in property_owners.source, on top
         of the (city, state) tuple match — broader coverage.
      4. Featured cards now show the full state name ("New York", "Texas")
         to match the section headers and jump-nav pills below.
    """
    # V475 Bug 2: serve cached response if we have one. The directory
    # listing changes slowly (cities are added once a week, badges
    # refresh once a day) so a 10-min cache is fine. Drops p99 from
    # 19-30s → <100ms.
    import time as _t
    _now = _t.time()
    if _CITIES_BROWSE_CACHE['response'] is not None and _now < _CITIES_BROWSE_CACHE['expires_at']:
        from flask import make_response
        _r = make_response(_CITIES_BROWSE_CACHE['response'])
        _r.headers['Cache-Control'] = 'public, max-age=600'
        _r.headers['X-PermitGrab-Cache'] = 'HIT'
        return _r
    # Footer ranking still uses the curated public list (population /
    # permit-volume filter applied) so we don't surface low-quality
    # bulk-misattribution cities in the global footer.
    footer_cities = get_cities_with_data()

    conn = permitdb.get_connection()
    # V473 Bug 1: filter to status='active' so the page only lists
    # cities the collector is actually wired up for.
    rows = conn.execute(
        "SELECT city, state, city_slug, total_permits, "
        "       has_enrichment, has_violations "
        "FROM prod_cities "
        "WHERE status = 'active' "
        "  AND city IS NOT NULL AND city != '' "
        "  AND state IS NOT NULL AND state != '' "
    ).fetchall()
    # V473 Bug 3: build owner-set from BOTH (city, state) tuples and
    # permit_record:<slug> source entries. assessor:<county_key> rows
    # already have city/state populated so they fall through the tuple
    # match — only the permit_record path needs explicit slug parsing.
    owner_tuple_rows = conn.execute(
        "SELECT DISTINCT LOWER(city) c, UPPER(state) s "
        "FROM property_owners "
        "WHERE city IS NOT NULL AND state IS NOT NULL"
    ).fetchall()
    owner_slug_rows = conn.execute(
        "SELECT DISTINCT source FROM property_owners "
        "WHERE source LIKE 'permit_record:%'"
    ).fetchall()
    # V473b: assessor sources are county-level — credit secondary cities
    # in the same county with the parent county's owner data. Without
    # this, e.g. Mesa AZ shows "no owners" even though Maricopa County
    # has 78K owner rows that include Mesa parcels (just tagged
    # 'Phoenix' by the source).
    assessor_county_rows = conn.execute(
        "SELECT DISTINCT source FROM property_owners "
        "WHERE source LIKE 'assessor:%'"
    ).fetchall()
    # V473 Bug 2: profile counts per city for the featured-card threshold.
    profile_rows = conn.execute(
        "SELECT source_city_key, COUNT(*) c FROM contractor_profiles "
        "WHERE source_city_key IS NOT NULL "
        "GROUP BY source_city_key"
    ).fetchall()
    conn.close()

    owner_tuple_set = {(r['c'], r['s']) for r in owner_tuple_rows}
    owner_slug_set = {
        r['source'].split(':', 1)[1]
        for r in owner_slug_rows
        if r['source'] and ':' in r['source']
    }
    profile_counts = {r['source_city_key']: r['c'] for r in profile_rows}

    # V473b: which counties have any assessor rows? — used to credit
    # sub-municipalities sharing a county with a wired assessor.
    assessor_county_keys = {
        r['source'].split(':', 1)[1]
        for r in assessor_county_rows
        if r['source'] and ':' in r['source']
    }
    # Map of (city.lower(), state.upper()) → assessor key the city
    # belongs to. Hard-coded because we don't have a county lookup
    # table; covers the top-100 cities that share counties with
    # already-wired assessors.
    _COUNTY_CITIES = {
        'maricopa':              [('mesa','AZ'), ('scottsdale','AZ'), ('tempe','AZ'),
                                  ('chandler','AZ'), ('gilbert','AZ'), ('glendale','AZ'),
                                  ('peoria','AZ'), ('surprise','AZ')],
        'clark_lasvegas':        [('henderson','NV'), ('paradise','NV'),
                                  ('north las vegas','NV')],
        'cook_chicago':          [('aurora','IL'), ('naperville','IL'), ('joliet','IL'),
                                  ('elgin','IL')],
        'miami_dade':            [('hialeah','FL'), ('miami beach','FL'),
                                  ('miami gardens','FL'), ('homestead','FL'),
                                  ('coral gables','FL'), ('north miami','FL')],
        'broward_ftlauderdale':  [('fort lauderdale','FL'), ('hollywood','FL'),
                                  ('pembroke pines','FL'), ('coral springs','FL'),
                                  ('miramar','FL'), ('davie','FL'), ('plantation','FL'),
                                  ('sunrise','FL'), ('pompano beach','FL'),
                                  ('deerfield beach','FL')],
        'hillsborough_tampa':    [('saint petersburg','FL')],   # actually Pinellas
        'hennepin_minneapolis':  [('saint paul','MN'), ('bloomington','MN'),
                                  ('brooklyn park','MN'), ('plymouth','MN')],
        'multnomah_portland':    [('gresham','OR')],
        'wake_raleigh':          [('cary','NC'), ('apex','NC'),
                                  ('wake forest','NC')],
        'travis_austin':         [('round rock','TX'), ('cedar park','TX')],
        'philadelphia_opa':      [],  # Philadelphia is co-extensive with the county
        'cuyahoga_cleveland':    [('parma','OH'), ('lakewood','OH')],
        'erie_buffalo':          [],  # Buffalo is the principal city
        'davidson_nashville':    [],  # Davidson = Nashville
        'bexar':                 [],  # Bexar = San Antonio core
        'tarrant_fortworth':     [('arlington','TX'), ('grand prairie','TX'),
                                  ('mansfield','TX')],
        'hamilton_cincinnati':   [],
        'franklin_columbus':     [],
        'marion_indianapolis':   [],
        'lee_capecoral':         [('cape coral','FL'), ('fort myers','FL')],
        'onondaga_syracuse':     [('clay','NY')],
        'nyc_pluto':             [('brooklyn','NY'), ('queens','NY'),
                                  ('bronx','NY'), ('staten island','NY'),
                                  ('manhattan','NY')],
    }
    county_owners_set = set()
    for county_key, city_list in _COUNTY_CITIES.items():
        if county_key in assessor_county_keys:
            for c, s in city_list:
                county_owners_set.add((c, s))

    all_cities = []
    for r in rows:
        city = r['city']
        state = (r['state'] or '').upper()
        slug = r['city_slug']
        has_owners = (
            (city.lower(), state) in owner_tuple_set
            or slug in owner_slug_set
            or (city.lower(), state) in county_owners_set
        )
        all_cities.append({
            'name': city,
            'slug': slug,
            'state': state,
            'permit_count': r['total_permits'] or 0,
            'profile_count': profile_counts.get(slug, 0),
            'has_enrichment': bool(r['has_enrichment']),
            'has_violations': bool(r['has_violations']),
            'has_owners': has_owners,
        })

    # Group by state abbreviation, sort by FULL state name, then sort
    # cities alphabetically by name within each state.
    states = {}
    no_state = []
    for c in all_cities:
        state = c['state']
        if state:
            states.setdefault(state, []).append(c)
        else:
            no_state.append(c)

    sorted_states = sorted(
        states.items(),
        key=lambda x: _V472_STATE_NAMES.get(x[0], x[0]),
    )
    for _abbr, cities in sorted_states:
        cities.sort(key=lambda c: (c.get('name') or '').lower())

    # V473 Bug 2: featured cards require permits>=1000 AND profiles>=50.
    # That filters out bulk-misattribution junk (e.g. Lindley NY with
    # 40K permits but 0 profiles, which is a state-aggregate row leaking
    # into a tiny municipality).
    top_cities = sorted(
        [c for c in all_cities
         if c['permit_count'] >= 1000 and c['profile_count'] >= 50],
        key=lambda c: -c['permit_count'],
    )[:20]

    total_cities = len(all_cities)
    total_states = len(states)

    _html = render_template('cities_browse.html',
        footer_cities=footer_cities,
        sorted_states=sorted_states,
        no_state_cities=no_state,
        top_cities=top_cities,
        total_cities=total_cities,
        total_states=total_states,
        canonical_url=f"{SITE_URL}/cities",
        STATE_NAMES=_V472_STATE_NAMES,
    )
    # V475 Bug 2: cache the rendered HTML for 10 minutes. The page is
    # purely public info — no per-user state — so a process-wide cache
    # is safe and lets bots hammer this URL without re-hitting the DB.
    _CITIES_BROWSE_CACHE['response'] = _html
    _CITIES_BROWSE_CACHE['expires_at'] = _t.time() + _CITIES_BROWSE_TTL
    from flask import make_response
    _r = make_response(_html)
    _r.headers['Cache-Control'] = 'public, max-age=600'
    _r.headers['X-PermitGrab-Cache'] = 'MISS'
    return _r


@city_pages_bp.route('/unsubscribe/<int:search_id>')
def unsubscribe_search(search_id):
    """V170 C3: Unsubscribe from a saved search alert."""
    search = SavedSearch.query.get(search_id)
    if search:
        search.active = 0
        db.session.commit()
    return "Unsubscribed. You won't receive more emails for this saved search.", 200


