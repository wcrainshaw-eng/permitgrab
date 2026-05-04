"""V471 PR2 (CODE_V471 Part 1B): api blueprint extracted from server.py.

Routes: 61 URLs across 61 handlers.

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

api_bp = Blueprint('api', __name__)


from server import _collector_started, _flask_login_user, _flask_logout_user, _get_top_contractors_for_city, _load_saved_contractors, _load_webhooks, _require_pro_api, _resolve_phone_for_profile, _save_saved_contractors, _save_webhooks

@api_bp.route('/api/violations/<city_slug>')
def api_violations(city_slug):
    """V162: Get recent violations for a city."""
    conn = permitdb.get_connection()
    prod_city = conn.execute(
        "SELECT id, city, state FROM prod_cities WHERE city_slug = ?", (city_slug,)
    ).fetchone()
    if not prod_city:
        return jsonify({'error': f'City not found: {city_slug}'}), 404

    city_name = prod_city['city']
    city_state = prod_city['state']
    pid = prod_city['id']

    # V162: Try prod_city_id first, fall back to city name
    try:
        total = conn.execute("SELECT COUNT(*) as cnt FROM violations WHERE prod_city_id = ?", (pid,)).fetchone()['cnt']
        rows = conn.execute("""
            SELECT violation_date, violation_type, violation_description, status, address, zip
            FROM violations WHERE prod_city_id = ?
            ORDER BY violation_date DESC LIMIT 100
        """, (pid,)).fetchall()
    except Exception:
        # Old schema fallback (no prod_city_id column)
        total = conn.execute("SELECT COUNT(*) as cnt FROM violations WHERE city = ? AND state = ?", (city_name, city_state)).fetchone()['cnt']
        rows = conn.execute("""
            SELECT violation_date, violation_type, COALESCE(description, '') as violation_description,
                   status, address, '' as zip
            FROM violations WHERE city = ? AND state = ?
            ORDER BY violation_date DESC LIMIT 100
        """, (city_name, city_state)).fetchall()

    return jsonify({
        'city': prod_city['city'], 'state': prod_city['state'],
        'count': len(rows), 'total': total,
        'violations': [dict(r) for r in rows],
    })


@api_bp.route('/api/violations/<city_slug>/stats')
def api_violations_stats(city_slug):
    """V162: Get violation summary stats for a city."""
    conn = permitdb.get_connection()
    prod_city = conn.execute(
        "SELECT id, city, state FROM prod_cities WHERE city_slug = ?", (city_slug,)
    ).fetchone()
    if not prod_city:
        return jsonify({'error': f'City not found: {city_slug}'}), 404

    pid = prod_city['id']
    total = conn.execute("SELECT COUNT(*) as cnt FROM violations WHERE prod_city_id = ?", (pid,)).fetchone()['cnt']
    last30 = conn.execute(
        "SELECT COUNT(*) as cnt FROM violations WHERE prod_city_id = ? AND violation_date >= date('now', '-30 days')",
        (pid,)
    ).fetchone()['cnt']
    open_count = conn.execute(
        "SELECT COUNT(*) as cnt FROM violations WHERE prod_city_id = ? AND LOWER(status) IN ('open','active','pending')",
        (pid,)
    ).fetchone()['cnt']
    top_types = conn.execute("""
        SELECT violation_type as type, COUNT(*) as count FROM violations
        WHERE prod_city_id = ? AND violation_type IS NOT NULL AND violation_type != ''
        GROUP BY violation_type ORDER BY count DESC LIMIT 10
    """, (pid,)).fetchall()

    return jsonify({
        'total_violations': total, 'last_30_days': last30, 'open_count': open_count,
        'top_types': [dict(r) for r in top_types],
    })


@api_bp.route('/api/permits/<city_slug>/export.csv')
def export_csv(city_slug):
    """V170 B3: Export permits for a city as CSV.

    V251 F3: gated to paid subscribers. Anonymous → redirect to signup with
    a return URL pointing at the same city page (matches the F1 gated-preview
    CTA pattern). Logged-in free-tier → 402 JSON with upgrade message. Pro
    users (including admin) → CSV as before.
    """
    if 'user_email' not in session:
        return redirect(f'/signup?next=/permits/{city_slug}&message=subscribe_to_export')
    _user = find_user_by_email(session['user_email'])
    if not _user or not is_pro(_user):
        return jsonify({
            'error': 'CSV export is a Pro feature. Subscribe for $149/mo to download contractor lead lists.',
            'upgrade_url': f'/pricing?next=/permits/{city_slug}',
        }), 402

    import csv, io
    conn = permitdb.get_connection()
    prod_city = conn.execute(
        "SELECT id, city, state FROM prod_cities WHERE city_slug = ?", (city_slug,)
    ).fetchone()
    if not prod_city:
        return jsonify({'error': f'City not found: {city_slug}'}), 404

    pid = prod_city['id']
    trade_filter = request.args.get('trade', '')
    tier_filter = request.args.get('tier', '')
    zip_filter = (request.args.get('zip') or '').strip()[:5]
    try:
        days_filter = int(request.args.get('days') or 0)
    except (TypeError, ValueError):
        days_filter = 0
    if days_filter not in (0, 7, 30, 90, 180, 365):
        days_filter = 0
    try:
        min_value_filter = int(request.args.get('min_value') or 0)
    except (TypeError, ValueError):
        min_value_filter = 0
    if min_value_filter not in (0, 10000, 50000, 100000, 500000, 1000000):
        min_value_filter = 0
    limit = min(int(request.args.get('limit', 10000)), 10000)

    query = "SELECT * FROM permits WHERE prod_city_id = ?"
    params = [pid]
    if trade_filter:
        query += " AND trade_category = ?"
        params.append(trade_filter)
    if zip_filter:
        query += " AND zip = ?"
        params.append(zip_filter)
    if days_filter:
        query += f" AND COALESCE(filing_date, issued_date, date) >= date('now', '-{days_filter} days')"
    if min_value_filter:
        query += f" AND estimated_cost >= {min_value_filter}"
    query += " ORDER BY filing_date DESC LIMIT ?"
    params.append(limit)

    # V366 (CODE_V363 Part E): stream CSV + JOIN contractor_profiles for phone
    # and property_owners for owner_mailing_address. Subscribers pay $149/mo for
    # actionable contact info, not just addresses.
    cursor = conn.execute(query, params)

    # Build a phone/trade lookup for this city's contractor profiles up-front.
    # source_city_key on profiles is the slug (hyphen format), and we match
    # case-insensitively on business_name vs permit.contractor_name.
    profile_rows = conn.execute(
        "SELECT business_name, phone, trade_category FROM contractor_profiles "
        "WHERE source_city_key = ?", (city_slug,)
    ).fetchall()
    profile_lookup = {}
    for pr in profile_rows:
        bn = (pr['business_name'] or '').strip().lower()
        if bn and bn not in profile_lookup:
            profile_lookup[bn] = (pr['phone'] or '', pr['trade_category'] or '')

    # Owner lookup keyed by normalized address. property_owners.address is
    # uppercase per V279 schema; permits.address may not be — normalize both.
    owner_rows = conn.execute(
        "SELECT address, owner_name, owner_mailing_address FROM property_owners "
        "WHERE city = ? OR city IS NULL OR city = ''",
        ((prod_city['city'] or '').strip(),)
    ).fetchall()
    owner_lookup = {}
    for orow in owner_rows:
        addr = (orow['address'] or '').strip().upper()
        if addr and addr not in owner_lookup:
            owner_lookup[addr] = (orow['owner_name'] or '', orow['owner_mailing_address'] or '')

    header = [
        'date', 'permit_number', 'address', 'type', 'description', 'value',
        'trade', 'contractor_name', 'contractor_phone',
        'owner_name', 'owner_mailing_address', 'status',
    ]

    def generate():
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(header)
        yield buf.getvalue()
        buf.seek(0); buf.truncate(0)
        for r in cursor:
            cname = r['contractor_name'] or r['contact_name'] or ''
            phone, trade = profile_lookup.get(cname.strip().lower(), ('', ''))
            trade = trade or (r['trade_category'] or '')
            addr_norm = (r['address'] or '').strip().upper()
            o_name, o_mail = owner_lookup.get(addr_norm, ('', ''))
            # Permit table also has owner_name as fallback
            o_name = o_name or (r['owner_name'] or '')
            w.writerow([
                r['filing_date'] or r['date'], r['permit_number'], r['address'],
                r['permit_type'], (r['description'] or '')[:200],
                r['estimated_cost'], trade, cname, phone,
                o_name, o_mail, r['status'],
            ])
            yield buf.getvalue()
            buf.seek(0); buf.truncate(0)

    return Response(
        generate(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename="{city_slug}-permits.csv"'}
    )


@api_bp.route('/api/reveal-status')
def api_reveal_status():
    """V254 Phase 1: return remaining free reveals + Pro status for the
    current session. Public — anon gets zero credits, is_pro=false,
    signup prompt. UI uses this to render the right CTA everywhere.
    """
    if 'user_email' not in session:
        return jsonify({'authenticated': False, 'is_pro': False,
                        'credits_remaining': 0, 'signup_url': '/signup'})
    u = find_user_by_email(session['user_email'])
    if not u:
        return jsonify({'authenticated': False, 'is_pro': False,
                        'credits_remaining': 0, 'signup_url': '/signup'})
    pro = is_pro({'plan': u.plan,
                  'stripe_subscription_status': getattr(u, 'stripe_subscription_status', None)})
    try:
        already = json.loads(u.revealed_profile_ids or '[]')
    except Exception:
        already = []
    return jsonify({
        'authenticated': True,
        'is_pro': bool(pro),
        'credits_remaining': 0 if pro else int(u.reveal_credits or 0),
        'revealed_count': len(already),
    })


@api_bp.route('/api/reveal-phone', methods=['POST'])
def api_reveal_phone():
    """V254 Phase 1: spend a free-tier credit to reveal a contractor phone.

    Pro/Enterprise: phone returned without decrementing.
    Free with credits > 0: decrement and return phone; track revealed id
    so a second reveal of the same contractor is idempotent (no charge).
    Free with credits == 0: 402 with upgrade_url.
    Anon: 401 with signup_url.
    """
    if 'user_email' not in session:
        return jsonify({'error': 'Sign up for 10 free reveals',
                        'signup_url': '/signup'}), 401
    data = request.get_json() or {}
    try:
        profile_id = int(data.get('profile_id'))
    except (TypeError, ValueError):
        return jsonify({'error': 'profile_id required'}), 400
    profile = _resolve_phone_for_profile(profile_id)
    if not profile:
        return jsonify({'error': 'Contractor not found'}), 404

    u = find_user_by_email(session['user_email'])
    if not u:
        return jsonify({'error': 'Not authenticated'}), 401

    pro = is_pro({'plan': u.plan,
                  'stripe_subscription_status': getattr(u, 'stripe_subscription_status', None)})
    if pro:
        return jsonify({'phone': profile.get('phone'),
                        'website': profile.get('website'),
                        'credits_remaining': None, 'is_pro': True})

    # Free tier — check idempotency + credits
    try:
        already = json.loads(u.revealed_profile_ids or '[]')
    except Exception:
        already = []
    if profile_id in already:
        return jsonify({'phone': profile.get('phone'),
                        'website': profile.get('website'),
                        'credits_remaining': int(u.reveal_credits or 0),
                        'is_pro': False, 'already_revealed': True})

    credits = int(u.reveal_credits or 0)
    if credits <= 0:
        return jsonify({
            'error': "You've used all your free reveals. Subscribe for unlimited access.",
            'upgrade_url': '/pricing',
            'credits_remaining': 0,
        }), 402

    already.append(profile_id)
    u.reveal_credits = credits - 1
    u.revealed_profile_ids = json.dumps(already[-500:])  # cap list length
    db.session.commit()
    return jsonify({'phone': profile.get('phone'),
                    'website': profile.get('website'),
                    'credits_remaining': u.reveal_credits,
                    'is_pro': False})


@api_bp.route('/api/v1/contractors')
def api_v1_contractors():
    """GET /api/v1/contractors?city=<slug>[&trade=][&limit=]

    Returns the same top-contractors payload the web UI renders, but
    unredacted (Pro-only). Useful for CRM ingestion.
    """
    err, status = _require_pro_api()
    if err:
        return err, status
    city_slug = (request.args.get('city') or '').strip()
    trade = (request.args.get('trade') or '').strip()
    try:
        limit = min(max(int(request.args.get('limit') or 25), 1), 500)
    except (TypeError, ValueError):
        limit = 25
    if not city_slug:
        return jsonify({'error': 'city query param required'}), 400
    contractors = _get_top_contractors_for_city(city_slug, limit=limit)
    if trade:
        contractors = [c for c in contractors if (c.get('primary_trade') or '') == trade]
    return jsonify({
        'city_slug': city_slug,
        'count': len(contractors),
        'contractors': contractors,
    })


@api_bp.route('/api/v1/permits')
def api_v1_permits():
    """GET /api/v1/permits?city=<slug>[&trade=][&zip=][&days=][&min_value=][&limit=]

    Recent permits for the city, honoring the same filter params as the
    web UI. Pro-gated.
    """
    err, status = _require_pro_api()
    if err:
        return err, status
    city_slug = (request.args.get('city') or '').strip()
    if not city_slug:
        return jsonify({'error': 'city query param required'}), 400
    conn = permitdb.get_connection()
    pc_row = conn.execute(
        "SELECT id FROM prod_cities WHERE city_slug = ?", (city_slug,)
    ).fetchone()
    if not pc_row:
        return jsonify({'error': f'city not found: {city_slug}'}), 404
    pid = pc_row[0] if not isinstance(pc_row, dict) else pc_row['id']

    trade = (request.args.get('trade') or '').strip()
    zip_f = (request.args.get('zip') or '').strip()[:5]
    try:
        days = int(request.args.get('days') or 0)
    except (TypeError, ValueError):
        days = 0
    if days not in (0, 7, 30, 90, 180, 365):
        days = 0
    try:
        min_value = int(request.args.get('min_value') or 0)
    except (TypeError, ValueError):
        min_value = 0
    if min_value not in (0, 10000, 50000, 100000, 500000, 1000000):
        min_value = 0
    try:
        limit = min(max(int(request.args.get('limit') or 100), 1), 1000)
    except (TypeError, ValueError):
        limit = 100

    clause = ""
    params = [pid]
    if trade:
        clause += " AND trade_category = ?"
        params.append(trade)
    if zip_f:
        clause += " AND zip = ?"
        params.append(zip_f)
    if days:
        clause += f" AND COALESCE(filing_date, issued_date, date) >= date('now', '-{days} days')"
    if min_value:
        clause += f" AND estimated_cost >= {min_value}"
    rows = conn.execute(f"""
        SELECT permit_number, filing_date, issued_date, date, permit_type,
               address, zip, description, estimated_cost, trade_category,
               contractor_name, contact_name, owner_name, status
        FROM permits
        WHERE prod_city_id = ?{clause}
        ORDER BY COALESCE(filing_date, issued_date, date) DESC
        LIMIT ?
    """, params + [limit]).fetchall()
    return jsonify({
        'city_slug': city_slug,
        'filters': {'trade': trade or None, 'zip': zip_f or None,
                    'days': days or None, 'min_value': min_value or None},
        'count': len(rows),
        'permits': [dict(r) for r in rows],
    })


@api_bp.route('/api/address-detail')
@limiter.limit("60 per minute")
def api_address_detail():
    """V310 (CODE_V280b PR2 / CODE_V310): address-centric detail card.

    Query: ?address=<permit.address>&city=<source_city_key>
    Returns JSON with all permits + violations + property-owner info for
    that address. Phone and owner mailing address are gated on auth —
    anonymous callers see null for those fields.

    Per Wes's architecture note: "The ADDRESS is the anchor." Everything
    stacks under the address. This endpoint is the server side of the
    expandable-row detail view that unlocks the dead grid.
    """
    raw_addr = (request.args.get('address') or '').strip()
    city_slug = (request.args.get('city') or request.args.get('city_slug') or '').strip()
    if not raw_addr or not city_slug:
        return jsonify({'error': 'address and city required'}), 400

    # Simple normalization: uppercase + collapse whitespace. Addresses in
    # permits/violations are stored as-ingested, so exact matching is
    # brittle. We use a LIKE with the shortest distinctive prefix
    # ("1234 N MAIN") to catch variants ("1234 N MAIN ST #3B",
    # "1234 North Main Street"). Cap the prefix length to keep the
    # LIKE selective enough to hit the index.
    upper = raw_addr.upper().strip()
    # Drop suite/unit markers so "1234 MAIN ST #3B" groups with "1234 MAIN ST".
    # V427 (CODE_V427 Phase 4): made the regex more permissive — also strips
    # `#` with no leading whitespace ("1234 MAIN ST#3B"), and any trailing
    # punctuation that breaks LIKE matching against differently-formatted
    # storage. Then trim the prefix to ~28 chars (street number + first
    # 1-2 words of the street name) so the LIKE is forgiving of street-type
    # variants ("ST" vs "STREET") without being so loose it catches
    # neighbors. Previously a 48-char prefix matched exactly nothing for
    # rows where stored address used a different street-type abbreviation.
    import re as _re
    upper = _re.sub(r'\s*(UNIT|APT|SUITE|STE|#)\s*\S+.*$', '', upper)
    upper = _re.sub(r'[^A-Z0-9 ]', ' ', upper)  # strip commas/periods/hyphens
    upper = _re.sub(r'\s+', ' ', upper).strip()
    like_prefix = upper[:28] + '%'  # street number + first ~2 words

    logged_in = bool(session.get('user_email'))
    conn = permitdb.get_connection()

    # Permits at this address. V440 (CODE_V440 C2): the prior SELECT
    # included `p.id`, which doesn't exist on permits — that silently
    # raised, was swallowed by the bare except, and produced an empty
    # detail card on every row click.
    permits = []
    try:
        rows = conn.execute("""
            SELECT p.filing_date, p.issued_date, p.date, p.permit_type,
                   p.description, p.estimated_cost, p.permit_number, p.status,
                   p.contractor_name, p.contact_name, p.contact_phone,
                   p.trade_category, p.address, p.source_city_key
            FROM permits p
            WHERE p.source_city_key = ?
              AND UPPER(p.address) LIKE ?
            ORDER BY COALESCE(p.filing_date, p.issued_date, p.date) DESC
            LIMIT 25
        """, (city_slug, like_prefix)).fetchall()
    except Exception as e:
        print(f"[V440] address-detail permits query error: {e}", flush=True)
        rows = []

    # Cache contractor_profiles lookups by (business_name, source_city_key)
    prof_cache = {}

    def _contractor_info(business_name):
        if not business_name:
            return None
        key = (business_name, city_slug)
        if key in prof_cache:
            return prof_cache[key]
        try:
            cp = conn.execute("""
                SELECT id, business_name, phone, trade_category,
                       total_permits, source_city_key
                FROM contractor_profiles
                WHERE source_city_key = ? AND business_name = ?
                LIMIT 1
            """, (city_slug, business_name)).fetchone()
        except Exception:
            cp = None
        out = None
        if cp:
            out = {
                # V367 (CODE_V363 Part C): expose profile id so the
                # expandable detail card can link the contractor name
                # to /contractor/<id> for the full dossier view.
                'profile_id': cp['id'],
                'business_name': cp['business_name'],
                'phone': cp['phone'] if logged_in else None,
                'trade_category': cp['trade_category'],
                'total_permits': cp['total_permits'] or 0,
            }
        prof_cache[key] = out
        return out

    for r in rows or []:
        contractor_name = r['contractor_name'] or r['contact_name'] or ''
        permits.append({
            'date': (r['filing_date'] or r['issued_date'] or r['date'] or '')[:10],
            'type': r['permit_type'] or '',
            'value': r['estimated_cost'],
            'permit_number': r['permit_number'],
            'status': r['status'],
            'description': r['description'] or '',
            'address': r['address'],
            'trade_category': r['trade_category'],
            'contractor': _contractor_info(contractor_name) or (
                {'business_name': contractor_name,
                 'phone': r['contact_phone'] if logged_in else None,
                 'trade_category': r['trade_category'],
                 'total_permits': None} if contractor_name else None
            ),
        })

    # Violations at this address (city+state scoped — violations use city/state, not slug)
    violations = []
    try:
        # Derive city name + state from the first permit's prod_cities row
        pc = conn.execute(
            "SELECT city, state FROM prod_cities WHERE city_slug = ? LIMIT 1",
            (city_slug,)
        ).fetchone()
        if pc:
            vrows = conn.execute("""
                SELECT violation_date, violation_description, status,
                       source_violation_id, violation_type, address
                FROM violations
                WHERE city = ? AND state = ? AND UPPER(address) LIKE ?
                ORDER BY violation_date DESC
                LIMIT 20
            """, (pc['city'], pc['state'], like_prefix)).fetchall()
            for v in vrows or []:
                violations.append({
                    'date': (v['violation_date'] or '')[:10],
                    'description': v['violation_description'] or v['violation_type'] or '',
                    'status': v['status'],
                    'case_number': v['source_violation_id'],
                    'address': v['address'],
                })
    except Exception:
        pass

    # V465 (CODE_V465 Phase 1): three-pass owner match — exact → permit-LIKE-owner
    # → owner-LIKE-permit. Many permit addresses include suite/apt suffixes
    # ("7535 SW 88 ST 1860") while assessor records use the bare street form
    # ("7535 SW 88 ST"). Bidirectional LIKE catches both directions.
    # Always scoped by state with city-rank tiebreaker (the V464 contribution).
    owner = None
    try:
        pc_owner = conn.execute(
            "SELECT city, state FROM prod_cities WHERE city_slug = ? LIMIT 1",
            (city_slug,)
        ).fetchone()
        if pc_owner and pc_owner['state']:
            _state = pc_owner['state']
            _city_full = pc_owner['city'] or ''
            _addr_clean = upper.strip()  # already normalized at line 10306-10319

            def _v465_lookup(where_addr_clause, params):
                return conn.execute(f"""
                    SELECT owner_name, owner_mailing_address, parcel_id, source, city, address
                      FROM property_owners
                     WHERE state = ?
                       AND {where_addr_clause}
                     ORDER BY
                       CASE
                         WHEN city = ?           THEN 0
                         WHEN ? LIKE city || ' %' THEN 1
                         ELSE 2
                       END,
                       last_updated DESC
                     LIMIT 1
                """, params).fetchone()

            # Pass 1: exact (after both sides upper+trim)
            orow = _v465_lookup(
                "UPPER(TRIM(address)) = ?",
                (_state, _addr_clean, _city_full, _city_full),
            )
            # Pass 2: permit address LIKE owner-prefix (existing behavior; catches
            # permit "7535 SW 88 ST" matching owner "7535 SW 88 ST"). like_prefix
            # is upper[:28] + '%'.
            if not orow:
                orow = _v465_lookup(
                    "UPPER(address) LIKE ?",
                    (_state, like_prefix, _city_full, _city_full),
                )
            # Pass 3: reverse — permit address starts with the full owner address.
            # Catches permit "7535 SW 88 ST 1860" matching owner "7535 SW 88 ST".
            if not orow:
                orow = _v465_lookup(
                    "? LIKE UPPER(address) || ' %'",
                    (_state, _addr_clean, _city_full, _city_full),
                )
            if orow:
                owner = {
                    'owner_name': orow['owner_name'],
                    'mailing_address': orow['owner_mailing_address'] if logged_in else None,
                    'parcel_id': orow['parcel_id'],
                    'source': orow['source'],
                    'matched_address': orow['address'],
                }
    except Exception as _v465_err:
        print(f"[V465] property_owners lookup error: {_v465_err}", flush=True)

    return jsonify({
        'address': upper,
        'city_slug': city_slug,
        'logged_in': logged_in,
        'permits': permits,
        'violations': violations,
        'property': owner,
    })


@api_bp.route('/api/permits')
@limiter.limit("60 per minute")
def api_permits():
    """
    GET /api/permits — V12.50: SQL-backed queries.
    Query params: city, trade, value, status, search, quality, page, per_page
    Returns paginated, filtered permit data with lead scores.

    FREEMIUM GATING: Non-Pro users see masked contact info on ALL permits.
    """
    # Parse filters — V174: accept both 'city' and 'city_slug' param names
    city = request.args.get('city', '')
    if city and city.lower() == 'all':
        city = ''  # V451 (CODE_V448 Phase 4): treat ?city=all as no-filter or request.args.get('city_slug', '')
    trade = request.args.get('trade', '')
    value = request.args.get('value', '')
    status_filter = request.args.get('status', '')
    quality = request.args.get('quality', '')
    search = request.args.get('search', '')
    # V313 (CODE_V280b Bug 15): cap per_page server-side at 10000 (the
    # /browse JS bulk-loads then paginates client-side; raising the cap
    # to 50000+ would OOM Render). The pagination strip on /browse
    # already shows "Showing N-M of T" — that's the visible artifact
    # Bug 15 asked for. The cap just keeps a runaway client query from
    # taking down the server.
    try:
        page = max(1, int(request.args.get('page', 1) or 1))
        per_page = max(1, min(10000, int(request.args.get('per_page', 50) or 50)))
    except (TypeError, ValueError):
        page, per_page = 1, 50

    # V32: Resolve city slug to name and state for cross-state filtering.
    # V202-4 + V203: Two-stage lookup with a shared alias map.
    # V217 T4: NYC permits have been normalized to city='New York City', so
    # the old ('New York City','NY')→'New York' alias is removed. Kept the
    # CHICAGO case because CITY_REGISTRY chicago_il still carries the
    # uppercase display name while permits rows use titlecase.
    PERMIT_CITY_ALIAS = {
        ('CHICAGO', 'IL'): 'Chicago',          # CITY_REGISTRY chicago_il uppercase
    }
    city_name = None
    city_state = None
    if city:
        city_key, city_config = get_city_by_slug(city)
        if city_config:
            city_name = city_config.get('name', city)
            city_state = city_config.get('state', '')
        else:
            try:
                conn_tmp = permitdb.get_connection()
                row = conn_tmp.execute(
                    "SELECT city, state FROM prod_cities WHERE city_slug = ? LIMIT 1",
                    (city,)
                ).fetchone()
                if row:
                    city_name = row['city'] if isinstance(row, dict) else row[0]
                    city_state = row['state'] if isinstance(row, dict) else row[1]
                else:
                    city_name = city  # Use as-is if not a valid slug
            except Exception:
                city_name = city
        # Apply alias after resolution — covers both registry and prod_cities paths.
        if city_name is not None:
            city_name = PERMIT_CITY_ALIAS.get((city_name, city_state), city_name)

    # Resolve trade slug to name if needed
    trade_name = None
    if trade and trade != 'all-trades':
        trade_config = get_trade(trade)
        if trade_config:
            trade_name = trade_config.get('name', trade)
        else:
            trade_name = trade  # Use as-is if not a valid slug

    # V13.2: SQL ORDER BY prioritizes data quality so "All Cities" default shows
    # best data first, not just Mesa permits with garbage dates (which sort high
    # lexicographically because "WROCCO" > "2026-03-24").
    #
    # Priority: high cost → valid date → has address → has contact → recent date
    # This ensures Austin (85 pts) and Chicago (72 pts) surface before Mesa (28 pts).
    data_quality_order = """
        CASE WHEN estimated_cost > 100000 THEN 0
             WHEN estimated_cost > 10000 THEN 1
             WHEN estimated_cost > 0 THEN 2
             ELSE 3 END,
        CASE WHEN filing_date GLOB '[0-9][0-9][0-9][0-9]-*' THEN 0 ELSE 1 END,
        CASE WHEN address IS NOT NULL AND address != '' THEN 0 ELSE 1 END,
        CASE WHEN contractor_name IS NOT NULL OR contact_phone IS NOT NULL THEN 0 ELSE 1 END,
        filing_date DESC
    """

    # V12.50: Query SQLite database (replaces loading 100K permits into memory)
    # V32: Pass state to prevent cross-state data pollution
    permits, total = permitdb.query_permits(
        city=city_name,
        state=city_state,
        trade=trade_name,
        value=value or None,
        status=status_filter or None,
        search=search or None,
        page=page,
        per_page=per_page,
        order_by=data_quality_order
    )

    # Add lead scores to page results
    permits = add_lead_scores(permits)

    # Sort by lead score (hot leads first) within page
    permits.sort(key=lambda x: x.get('lead_score', 0), reverse=True)

    # Quality filter (post-query since lead_score is computed)
    if quality:
        if quality == 'hot':
            permits = [p for p in permits if p.get('lead_quality') == 'hot']
        elif quality == 'warm':
            permits = [p for p in permits if p.get('lead_quality') in ('hot', 'warm')]

    # FREEMIUM GATING: Strip contact info for ALL permits for non-Pro users
    user = get_current_user()
    user_is_pro = is_pro(user)

    if not user_is_pro:
        for permit in permits:
            permit['contact_phone'] = None
            permit['contact_name'] = None
            permit['contact_email'] = None
            permit['owner_name'] = None
            permit['is_gated'] = True
    else:
        for permit in permits:
            permit['is_gated'] = False

    # V12.50: Aggregate stats from SQL (not from loading all permits!)
    stats_data = permitdb.get_permit_stats()
    collection_stats = load_stats()  # Keep this for collected_at timestamp

    return jsonify({
        'permits': permits,
        'total': total,
        'page': page,
        'per_page': per_page,
        'total_pages': (total + per_page - 1) // per_page,
        'user_is_pro': user_is_pro,
        'last_updated': collection_stats.get('collected_at', '') or datetime.now().isoformat(),
        'total_value': stats_data['total_value'],
        'high_value_count': stats_data['high_value_count'],
        'total_permits': stats_data['total_permits'],
    })


@api_bp.route('/api/stats')
def api_stats():
    """GET /api/stats — V12.50: SQL-backed stats."""
    stats_data = permitdb.get_permit_stats()
    collection_stats = load_stats()

    return jsonify({
        'total_permits': stats_data['total_permits'],
        'total_value': stats_data['total_value'],
        'high_value_count': stats_data['high_value_count'],
        'cities': stats_data['city_count'],
        'trade_breakdown': collection_stats.get('trade_breakdown', {}),
        'value_breakdown': collection_stats.get('value_breakdown', {}),
        'last_updated': collection_stats.get('collected_at', ''),
    })


@api_bp.route('/api/filters')
def api_filters():
    """GET /api/filters - Available filter options (V12.51: SQL-backed).

    V442 P1 (CODE_V442): the city list previously read DISTINCT city from
    permits, but a few misconfigured collectors (Pierce County WA among
    them) wrote addresses into permits.city — e.g. "11712 Houston Rd East".
    The garbage entries flooded the analytics dropdown and blocked the
    V440 preferred-city default match. Read from prod_cities instead so
    only real, active city names show up.
    """
    conn = permitdb.get_connection()

    cities = [r[0] for r in conn.execute(
        "SELECT DISTINCT city FROM prod_cities "
        "WHERE city IS NOT NULL AND city != '' AND status = 'active' "
        "ORDER BY city"
    ).fetchall()]

    trades = [r[0] for r in conn.execute(
        "SELECT DISTINCT trade_category FROM permits WHERE trade_category IS NOT NULL AND trade_category != '' ORDER BY trade_category"
    ).fetchall()]

    statuses = [r[0] for r in conn.execute(
        "SELECT DISTINCT status FROM permits WHERE status IS NOT NULL AND status != '' ORDER BY status"
    ).fetchall()]

    return jsonify({
        'cities': cities,
        'trades': trades,
        'statuses': statuses,
    })


@api_bp.route('/api/cities')
def api_cities():
    """GET /api/cities - Get all active cities with permit data.

    V90: Now reads from prod_cities table (database) instead of static CITY_REGISTRY.
    Only returns cities with actual permit data (total_permits > 0).
    """
    # Get cities with data from database
    cities = permitdb.get_prod_cities(status='active', min_permits=1)

    # Format for frontend compatibility
    formatted_cities = []
    for city in cities:
        formatted_cities.append({
            'name': city['name'],
            'state': city['state'],
            'slug': city['slug'],
            'permit_count': city['permit_count'],
            'active': city['active'],
        })

    return jsonify({
        'count': len(formatted_cities),
        'cities': formatted_cities,
    })


@api_bp.route('/api/city-health')
def api_city_health():
    """GET /api/city-health - Get city API health status."""
    health_file = os.path.join(DATA_DIR, 'city_health.json')
    if os.path.exists(health_file):
        with open(health_file) as f:
            return jsonify(json.load(f))
    return jsonify({'status': 'no health data available'})


@api_bp.route('/api/subscribe', methods=['POST'])
@limiter.limit("5 per minute")
def api_subscribe():
    """POST /api/subscribe - Add email alert subscriber.

    V12.53: Now uses User model instead of subscribers.json.
    Creates a lightweight User record for digest subscriptions.
    """
    data = request.get_json()
    if not data or not data.get('email'):
        return jsonify({'error': 'Email required'}), 400

    email = data['email'].strip().lower()
    # V218 T5B: server-side email format validation. The previous 'email
    # required' check let literal 'notanemail' land in the User table,
    # polluting subscriber lists and breaking downstream SendGrid calls.
    # RFC 5322 is more permissive than this but for our digest signup
    # the local@domain.tld shape is exactly what we want to enforce.
    import re as _re
    if not _re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', email) or len(email) > 254:
        return jsonify({'error': 'Please enter a valid email address'}), 400

    city = data.get('city', '').strip().title()  # V12.64: Normalize to titlecase
    trade = data.get('trade', '')
    # V251 F4: capture active-filter context so the digest narrows to what
    # the subscriber was actually viewing. Stored per-user (last wins), which
    # is a compromise — multi-city subscribers with different filters per
    # city need a proper user_city_subscriptions table. Fine for MVP.
    zip_filter = (data.get('zip') or '').strip()[:16]
    trade_filter = (data.get('trade') or '').strip()[:64]

    # Check if user already exists
    existing = find_user_by_email(email)
    if existing:
        # Update their digest settings
        cities = json.loads(existing.digest_cities or '[]')
        if city and city not in cities:
            cities.append(city)
            existing.digest_cities = json.dumps(cities)
        existing.digest_active = True
        if trade:
            existing.trade = trade
        # V251 F4: persist filters (clearing if empty, so the user can unset
        # by resubscribing without filters).
        existing.digest_zip_filter = zip_filter or None
        existing.digest_trade_filter = trade_filter or None
        db.session.commit()

        return jsonify({
            'message': f'Updated digest settings for {email}',
            'subscriber': {'email': email, 'city': city, 'trade': trade, 'zip': zip_filter},
        }), 200

    # Create new lightweight user for digest subscription
    import secrets
    try:
        new_user = User(
            email=email,
            name=data.get('name', ''),
            password_hash='',  # No password - digest-only user
            plan='free',
            digest_active=True,
            digest_cities=json.dumps([city]) if city else '[]',
            trade=trade,
            digest_zip_filter=zip_filter or None,
            digest_trade_filter=trade_filter or None,
            unsubscribe_token=secrets.token_urlsafe(32),
        )
        db.session.add(new_user)
        db.session.commit()
        print(f"[Subscribe] Created digest user: {email}")
    except IntegrityError:
        db.session.rollback()
        return jsonify({'error': 'Email already exists'}), 409

    # Track alert signup event
    analytics.track_event('alert_signup', event_data={
        'city': city,
        'trade': trade
    }, city_filter=city)

    return jsonify({
        'message': f'Successfully subscribed {email}',
        'subscriber': {'email': email, 'city': city, 'trade': trade, 'zip': zip_filter},
    }), 201


@api_bp.route('/api/subscribers')
def api_subscribers():
    """GET /api/subscribers - List all digest subscribers (admin endpoint).

    V12.53: Now queries User model instead of subscribers.json.
    """
    # Check admin authentication
    if not session.get('admin_authenticated'):
        return jsonify({'error': 'Admin authentication required'}), 401

    users = User.query.filter(User.digest_active == True).all()
    subs = []
    for u in users:
        subs.append({
            'email': u.email,
            'name': u.name,
            'city': json.loads(u.digest_cities or '[]'),
            'trade': u.trade,
            'plan': u.plan,
            'subscribed_at': u.created_at.isoformat() if u.created_at else None,
        })

    return jsonify({
        'total': len(subs),
        'subscribers': subs,
    })


@api_bp.route('/api/export')
def api_export():
    """GET /api/export - Export filtered permits as CSV with lead scores.

    PRO FEATURE: Non-Pro users cannot export and are redirected to pricing.

    V313 (CODE_V280b Bug 16): anonymous click → /signup; logged-in free
    user → 402 JSON pointing at /pricing. Both better than the old blanket
    403 which left anonymous browsers stuck.
    """
    if 'user_email' not in session:
        return redirect('/signup?next=/browse&message=subscribe_to_export')
    user = get_current_user()
    if not is_pro(user):
        return jsonify({
            'error': 'Export is a Pro feature. Subscribe for $149/mo to download contractor lead lists.',
            'upgrade_url': '/pricing'
        }), 402

    # V12.51: SQL-backed export
    city = request.args.get('city', '')
    if city and city.lower() == 'all':
        city = ''  # V451 (CODE_V448 Phase 4): treat ?city=all as no-filter
    trade = request.args.get('trade', '')
    quality = request.args.get('quality', '')

    permits, _ = permitdb.query_permits(
        city=city or None,
        trade=trade or None,
        page=1,
        per_page=50000,  # Export limit
        order_by='filing_date DESC'
    )
    permits = add_lead_scores(permits)

    # Quality filter (post-query since lead_score is computed)
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


@api_bp.route('/api/saved-leads', methods=['GET'])
def get_saved_leads():
    """GET /api/saved-leads - Get saved leads for logged-in user."""
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Login required'}), 401

    user_leads = get_user_saved_leads(user['email'])

    # V12.51: Enrich with permit data from SQLite
    all_permits, _ = permitdb.query_permits(page=1, per_page=100000)
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


@api_bp.route('/api/saved-leads', methods=['POST'])
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


@api_bp.route('/api/saved-leads/<permit_id>', methods=['PUT'])
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


@api_bp.route('/api/saved-leads/<permit_id>', methods=['DELETE'])
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


@api_bp.route('/api/saved-contractors', methods=['GET'])
def api_get_saved_contractors():
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Login required'}), 401
    if not is_pro(user):
        return jsonify({'error': 'Pro feature', 'upgrade_url': '/pricing'}), 402
    items = [i for i in _load_saved_contractors() if i.get('user_email') == user['email']]
    # Enrich with profile data so the UI can render business name / phone / trade
    conn = permitdb.get_connection()
    ids = [int(i['profile_id']) for i in items if str(i.get('profile_id', '')).isdigit()]
    profiles = {}
    if ids:
        ph = ','.join(['?'] * len(ids))
        rows = conn.execute(f"""
            SELECT id, contractor_name_raw, source_city_key, primary_trade,
                   phone, website, total_permits, permits_90d, last_permit_date
            FROM contractor_profiles WHERE id IN ({ph})
        """, ids).fetchall()
        profiles = {r['id']: dict(r) for r in rows}
    out = [{**i, 'profile': profiles.get(int(i.get('profile_id', 0)))} for i in items]
    return jsonify({'count': len(out), 'saved': out})


@api_bp.route('/api/saved-contractors', methods=['POST'])
def api_save_contractor():
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Login required'}), 401
    if not is_pro(user):
        return jsonify({'error': 'Pro feature', 'upgrade_url': '/pricing'}), 402
    data = request.get_json() or {}
    try:
        profile_id = int(data.get('profile_id'))
    except (TypeError, ValueError):
        return jsonify({'error': 'profile_id required'}), 400
    notes = (data.get('notes') or '').strip()[:500]
    all_items = _load_saved_contractors()
    existing = next(
        (i for i in all_items if i.get('user_email') == user['email']
         and int(i.get('profile_id', 0)) == profile_id),
        None,
    )
    if existing:
        existing['notes'] = notes
        existing['updated_at'] = datetime.utcnow().isoformat()
    else:
        all_items.append({
            'user_email': user['email'],
            'profile_id': profile_id,
            'notes': notes,
            'saved_at': datetime.utcnow().isoformat(),
        })
    _save_saved_contractors(all_items)
    return jsonify({'saved': True, 'profile_id': profile_id})


@api_bp.route('/api/saved-contractors/<int:profile_id>', methods=['DELETE'])
def api_unsave_contractor(profile_id):
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Login required'}), 401
    all_items = _load_saved_contractors()
    keep = [i for i in all_items
            if not (i.get('user_email') == user['email']
                    and int(i.get('profile_id', 0)) == profile_id)]
    _save_saved_contractors(keep)
    return jsonify({'removed': True})


@api_bp.route('/api/digest/preview/<city_slug>')
def api_digest_preview(city_slug):
    """V251 F16: Pro-gated preview of the weekly market report email for a city."""
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Login required'}), 401
    if not is_pro(user):
        return jsonify({'error': 'Pro feature', 'upgrade_url': '/pricing'}), 402
    report = build_weekly_market_report(city_slug)
    if not report:
        return jsonify({'error': 'No activity this week or city not active'}), 404
    return jsonify(report)


@api_bp.route('/api/reports/<city_slug>/monthly')
def api_monthly_report(city_slug):
    """V252 F4: monthly market report for a city. Enterprise-only.

    For MVP this returns the same HTML the shareable /report/<slug> page
    (F22) serves, which is print-friendly and PDF-able via the browser's
    print-to-PDF — sidestepping a ReportLab/WeasyPrint dependency.
    Redirect to the print-ready page so the user's browser handles the
    rendering; the Enterprise gate lives here.
    """
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Login required'}), 401
    if not is_enterprise(user):
        return jsonify({'error': 'Enterprise feature', 'upgrade_url': '/pricing'}), 402
    return redirect(f'/report/{city_slug}?print=1')


@api_bp.route('/api/webhooks', methods=['GET'])
def api_list_webhooks():
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Login required'}), 401
    if not is_enterprise(user):
        return jsonify({'error': 'Enterprise feature', 'upgrade_url': '/pricing'}), 402
    items = [w for w in _load_webhooks() if w.get('user_email') == user['email']]
    return jsonify({'webhooks': items, 'count': len(items)})


@api_bp.route('/api/webhooks', methods=['POST'])
def api_create_webhook():
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Login required'}), 401
    if not is_enterprise(user):
        return jsonify({'error': 'Enterprise feature', 'upgrade_url': '/pricing'}), 402
    data = request.get_json() or {}
    url = (data.get('url') or '').strip()
    city_slug = (data.get('city_slug') or '').strip()
    if not url.startswith(('http://', 'https://')):
        return jsonify({'error': 'Valid http(s) url required'}), 400
    if not city_slug:
        return jsonify({'error': 'city_slug required'}), 400
    import secrets as _sec
    wh = {
        'id': _sec.token_hex(8),
        'user_email': user['email'],
        'url': url,
        'city_slug': city_slug,
        'trade_filter': (data.get('trade_filter') or '').strip() or None,
        'min_value': int(data.get('min_value') or 0),
        'active': True,
        'created_at': datetime.utcnow().isoformat(),
    }
    items = _load_webhooks()
    # cap at 10 webhooks per user
    user_items = [w for w in items if w.get('user_email') == user['email']]
    if len(user_items) >= 10:
        return jsonify({'error': 'Max 10 webhooks per account'}), 400
    items.append(wh)
    _save_webhooks(items)
    return jsonify({'webhook': wh}), 201


@api_bp.route('/api/webhooks/<webhook_id>', methods=['DELETE'])
def api_delete_webhook(webhook_id):
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Login required'}), 401
    items = _load_webhooks()
    keep = [w for w in items
            if not (w.get('id') == webhook_id and w.get('user_email') == user['email'])]
    _save_webhooks(keep)
    return jsonify({'removed': True})


@api_bp.route('/api/saved-leads/export')
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
    # V12.51: SQL-backed
    all_permits, _ = permitdb.query_permits(page=1, per_page=100000)
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


@api_bp.route('/api/permit-history/<path:address>')
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


@api_bp.route('/api/contractors')
def api_contractors():
    """
    GET /api/contractors
    Query params: city, search, sort_by, sort_order, page, per_page

    V311 (CODE_V280b Bug 5): query contractor_profiles directly instead of
    loading 100K permits + aggregating in Python. The old path returned 0
    contractors on prod because permitdb.query_permits with per_page=100000
    timed out under the 512MB Render budget. Capped at 500 rows.
    """
    try:
        city = request.args.get('city', '').strip()
        if city.lower() == 'all':
            city = ''  # V451 (CODE_V448 Phase 4): treat ?city=all as no-filter
        search = request.args.get('search', '').strip().lower()
        # V447 P0: server-side default to most_recent_date so callers
        # that hit /api/contractors without ?sort_by also get fresh-first.
        # (Frontend already sends most_recent_date via V442 P2.)
        sort_by = request.args.get('sort_by', 'most_recent_date')
        sort_order = request.args.get('sort_order', 'desc')
        try:
            page = max(1, int(request.args.get('page', 1) or 1))
            per_page = max(1, min(100, int(request.args.get('per_page', 50) or 50)))
        except (TypeError, ValueError):
            page, per_page = 1, 50
        reverse = sort_order != 'asc'

        conn = permitdb.get_connection()
        conn.row_factory = sqlite3.Row

        # V423 (CODE_V422 Phase 5): exclude generic placeholder business
        # names that pollute the top-results.
        # V427 (CODE_V427 Phase 5): added utility companies + the SQL now
        # filters out the personal-name pattern that was surfacing Mesa AZ
        # inspectors as "top contractors". Heuristic: name has no LLC/INC/
        # CORP/CO suffix AND looks like exactly two whitespace-separated
        # words → likely a personal name; demoted unless they have many
        # cities or appear in our explicit business allowlist.
        _GARBAGE_NAMES = (
            'NOT GIVEN', 'OWNER-BUILDER', 'OWNER BUILDER', 'N/A', 'NA',
            'NONE', 'SELF', 'HOMEOWNER', 'HOME OWNER', 'OWNER',
            'NOT APPLICABLE', 'SAME', 'SAME AS ABOVE', 'SEE ABOVE',
            'VARIOUS', 'TBD', 'TBA', 'UNKNOWN', 'NOT AVAILABLE', 'PENDING',
            # V427: utility companies that pull tens of thousands of
            # permits but aren't contractor leads.
            'CENTERPOINT ENERGY RESOURCE CORP', 'CENTERPOINT ENERGY',
            'CENTERPOINT ENERGY RESOURCES', 'CENTERPOINT ENERGY HOUSTON',
            'ATMOS ENERGY', 'ATMOS ENERGY CORP',
            'PG&E', 'PACIFIC GAS AND ELECTRIC',
            'SOUTHERN CALIFORNIA EDISON', 'SOCAL EDISON',
            'CONSOLIDATED EDISON', 'CON ED', 'CON EDISON',
            'NATIONAL GRID', 'NATIONAL GRID USA',
            'COMED', 'COMMONWEALTH EDISON',
            'NICOR GAS', 'PEOPLES GAS', 'PEOPLES GAS LIGHT',
            'AT&T', 'VERIZON', 'COMCAST', 'SPECTRUM',
            'DUKE ENERGY', 'DOMINION ENERGY', 'DOMINION VIRGINIA POWER',
            'PSE&G', 'PSEG',
            'XCEL ENERGY',
        )
        _garbage_placeholders = ','.join(['?'] * len(_GARBAGE_NAMES))

        # V427 Phase 5: post-fetch filter — drop entries whose name looks
        # like a personal name (no business suffix) AND only appear in a
        # single city. Inspectors and applicants surface as top contractors
        # under that pattern (Annette Gordon / Pam Wilson / Stacy
        # Palfreyman in Mesa AZ). Real contractors typically have a
        # business suffix, multiple cities, or both.
        _BIZ_SUFFIX_TOKENS = (
            'INC', 'LLC', 'CORP', 'CO', 'CO.', 'COMPANY', 'CONSTRUCTION',
            'CONTRACTORS', 'CONTRACTING', 'BUILDERS', 'BUILDING', 'SERVICES',
            'GROUP', 'ENTERPRISES', 'ASSOC', 'ASSOCIATES', 'INDUSTRIES',
            'ENERGY', 'GAS', 'ELECTRIC', 'ELECTRICAL', 'PLUMBING', 'ROOFING',
            'HEATING', 'AIR', 'HVAC', 'SOLAR', 'MECHANICAL', 'HOMES',
            'HOLDINGS', 'PARTNERS', 'LP', 'LLP', 'LTD', '&',
        )

        # V447 P2 (CODE_V447): smart title-case for contractor names.
        # Many sources store names in ALL CAPS ("COLUMBUS/WORTHINGTON AIR").
        # Plain str.title() lowercases LLC→Llc, INC→Inc, HVAC→Hvac. This
        # keeps a small allowlist of business-suffix tokens uppercase.
        _SMART_TITLE_KEEP_UPPER = {
            'LLC', 'INC', 'CO', 'CORP', 'DBA', 'HVAC', 'AC', 'LP', 'LTD',
            'PC', 'PA', 'PLLC', 'LLP', 'USA', 'II', 'III', 'IV', 'NY',
            'NW', 'NE', 'SW', 'SE', 'US', 'AC/HEAT', 'A/C',
            # V455 P3A (CODE_V455 Phase 3A): handle "(Usa)" → "(USA)" via
            # parens-stripped variants (the str.title() upstream lowercases
            # everything inside parens too).
            '(USA)', '(LLC)', '(INC)', '(USA),', '(USA).',
        }

        def _smart_title(name):
            if not name:
                return name
            # If the name is already mixed-case, leave it alone — most data
            # sources that preserve case do so deliberately.
            if not name.isupper():
                return name
            words = name.title().split()
            return ' '.join(
                w.upper() if w.upper() in _SMART_TITLE_KEEP_UPPER else w
                for w in words
            )

        def _looks_personal(name, city_count):
            if not name:
                return False
            if (city_count or 0) >= 2:
                return False
            up = name.upper().strip()
            words = up.split()
            if len(words) > 3:
                return False  # 4+ words is rarely a personal name
            for tok in _BIZ_SUFFIX_TOKENS:
                if tok in words or up.endswith(tok):
                    return False
            return True

        # V427 Phase 5: lifted the LIMIT 500 cap. The page paginates
        # client-side; capping at 500 hid real contractors. Cap raised
        # to 5000 (still bounded for memory; covers any reasonable
        # contractor universe).
        if city:
            sql = f"""
                SELECT contractor_name_raw AS name,
                       primary_trade,
                       COALESCE(total_permits, 0) AS total_permits,
                       COALESCE(total_project_value, 0) AS total_value,
                       last_permit_date,
                       city, state
                FROM contractor_profiles
                WHERE source_city_key = ?
                  AND contractor_name_raw IS NOT NULL
                  AND contractor_name_raw != ''
                  AND LENGTH(contractor_name_raw) >= 3
                  AND UPPER(TRIM(contractor_name_raw)) NOT IN ({_garbage_placeholders})
                  AND contractor_name_raw GLOB '*[^0-9]*'
                  -- V446 P1: form-template scrape garbage from Sacramento County etc.
                  AND contractor_name_raw NOT LIKE '%SELECT EDIT%'
                  AND contractor_name_raw NOT LIKE '%ENTER NAME%'
                  AND contractor_name_raw NOT LIKE '****%'
                  AND contractor_name_raw NOT LIKE '%PHONE NUMBER%'
                ORDER BY total_permits DESC
                LIMIT 5000
            """
            rows = conn.execute(sql, (city, *_GARBAGE_NAMES)).fetchall()
            contractor_list = [{
                'name': _smart_title(r['name']),
                'total_permits': r['total_permits'] or 0,
                'total_value': r['total_value'] or 0,
                'cities': [f"{r['city']}, {r['state']}"] if r['city'] else [],
                'city_count': 1 if r['city'] else 0,
                'primary_trade': r['primary_trade'] or 'Other',
                'most_recent_date': r['last_permit_date'] or '',
            } for r in rows]
        else:
            sql = f"""
                SELECT contractor_name_normalized AS norm,
                       MAX(contractor_name_raw) AS name,
                       SUM(COALESCE(total_permits, 0)) AS total_permits,
                       SUM(COALESCE(total_project_value, 0)) AS total_value,
                       MAX(last_permit_date) AS last_permit_date,
                       MAX(primary_trade) AS primary_trade,
                       GROUP_CONCAT(city || ', ' || state, '|') AS city_blob,
                       COUNT(DISTINCT source_city_key) AS city_count
                FROM contractor_profiles
                WHERE contractor_name_raw IS NOT NULL
                  AND contractor_name_raw != ''
                  AND LENGTH(contractor_name_raw) >= 3
                  AND UPPER(TRIM(contractor_name_raw)) NOT IN ({_garbage_placeholders})
                  AND contractor_name_raw GLOB '*[^0-9]*'
                  -- V446 P1: form-template scrape garbage from Sacramento County etc.
                  AND contractor_name_raw NOT LIKE '%SELECT EDIT%'
                  AND contractor_name_raw NOT LIKE '%ENTER NAME%'
                  AND contractor_name_raw NOT LIKE '****%'
                  AND contractor_name_raw NOT LIKE '%PHONE NUMBER%'
                GROUP BY contractor_name_normalized
                ORDER BY total_permits DESC
                LIMIT 5000
            """
            rows = conn.execute(sql, _GARBAGE_NAMES).fetchall()
            contractor_list = []
            for r in rows:
                cities = sorted({c.strip() for c in (r['city_blob'] or '').split('|')
                                 if c and c.strip() and c.strip() != ', '})
                contractor_list.append({
                    'name': _smart_title(r['name']),
                    'total_permits': r['total_permits'] or 0,
                    'total_value': r['total_value'] or 0,
                    'cities': cities,
                    'city_count': r['city_count'] or 0,
                    'primary_trade': r['primary_trade'] or 'Other',
                    'most_recent_date': r['last_permit_date'] or '',
                })

        # V427 Phase 5: drop personal-name single-city entries (likely
        # inspectors/applicants, not contractors).
        contractor_list = [
            c for c in contractor_list
            if not _looks_personal(c.get('name'), c.get('city_count'))
        ]

        if search:
            contractor_list = [c for c in contractor_list if search in c['name'].lower()]

        if sort_by == 'name':
            contractor_list.sort(key=lambda x: x['name'].lower(), reverse=reverse)
        elif sort_by == 'total_value':
            contractor_list.sort(key=lambda x: x['total_value'] or 0, reverse=reverse)
        elif sort_by == 'most_recent_date':
            contractor_list.sort(key=lambda x: x['most_recent_date'] or '', reverse=reverse)
        else:
            contractor_list.sort(key=lambda x: x['total_permits'] or 0, reverse=reverse)

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
    except Exception as e:
        print(f"[ERROR] /api/contractors failed: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e), 'contractors': [], 'total': 0}), 500


@api_bp.route('/api/contractors/<path:name>')
def api_contractor_detail(name):
    """
    GET /api/contractors/<name>
    Returns permits for a specific contractor.

    V427 (CODE_V427 Phase 2): rewritten. The old V12.51 path loaded
    100,000 permits via query_permits() then filtered in Python on
    contact_name — this both timed out under memory pressure AND
    matched the wrong field (the /contractors page indexes by
    contractor_name_raw on contractor_profiles, not contact_name on
    permits, so the modal always showed "Failed to load"). Now does an
    indexed lookup on permits.contractor_name (case-insensitive),
    capped at 100 most-recent rows for the modal.
    """
    try:
        conn = permitdb.get_connection()
        conn.row_factory = sqlite3.Row

        # Match against contractor_name (the canonical field used by
        # contractor_profiles), falling back to contact_name for legacy rows
        # where V180 fallback hadn't promoted contact → contractor.
        sql = """
            SELECT permit_number, source_city_key, city, state,
                   contractor_name, contact_name, contact_phone,
                   address, trade_category, permit_type,
                   estimated_cost, filing_date, date, status
            FROM permits
            WHERE LOWER(COALESCE(contractor_name, contact_name, '')) = LOWER(?)
            ORDER BY COALESCE(filing_date, date, '') DESC
            LIMIT 100
        """
        rows = conn.execute(sql, (name,)).fetchall()
        contractor_permits = [dict(r) for r in rows]

        if not contractor_permits:
            return jsonify({'error': 'Contractor not found'}), 404

        # Aggregate stats from the slice (correct enough for the modal;
        # for total counts we read contractor_profiles).
        total_value = sum(p.get('estimated_cost') or 0 for p in contractor_permits)
        cities = sorted({p.get('city') or '' for p in contractor_permits if p.get('city')})
        trades = {}
        for p in contractor_permits:
            trade = p.get('trade_category') or 'Other'
            trades[trade] = trades.get(trade, 0) + 1

        # Pull canonical totals from contractor_profiles when available so
        # the modal matches the table count (e.g. 2,916 permits) instead of
        # reporting only the 100-row slice.
        try:
            prof_row = conn.execute("""
                SELECT SUM(COALESCE(total_permits, 0)) AS tp,
                       SUM(COALESCE(total_project_value, 0)) AS tv,
                       COUNT(DISTINCT source_city_key) AS cc
                FROM contractor_profiles
                WHERE LOWER(contractor_name_raw) = LOWER(?)
            """, (name,)).fetchone()
            if prof_row and prof_row['tp']:
                total_permits = int(prof_row['tp'])
                total_value = int(prof_row['tv'] or total_value)
                city_count = int(prof_row['cc'] or len(cities))
            else:
                total_permits = len(contractor_permits)
                city_count = len(cities)
        except Exception:
            total_permits = len(contractor_permits)
            city_count = len(cities)

        return jsonify({
            'name': name,
            'permits': contractor_permits,
            'total_permits': total_permits,
            'total_value': total_value,
            'cities': cities,
            'city_count': city_count,
            'trade_breakdown': trades,
        })
    except Exception as e:
        print(f"[V427] api_contractor_detail error for {name!r}: {e}", flush=True)
        return jsonify({'error': 'Failed to load contractor details'}), 500


@api_bp.route('/api/contractors/top')
def api_top_contractors():
    """
    GET /api/contractors/top
    Query params: city, limit
    Returns top contractors by permit volume.
    V12.51: SQL-backed
    """
    city = request.args.get('city', '')
    if city and city.lower() == 'all':
        city = ''  # V451 (CODE_V448 Phase 4): treat ?city=all as no-filter
    permits, _ = permitdb.query_permits(city=city or None, page=1, per_page=100000)

    limit = int(request.args.get('limit', 5))

    # V12.55: Aggregate by contractor with improved junk name filter
    JUNK_NAMES = {'n/a', 'unknown', 'none', 'na', 'tbd', 'tba', 'pending',
                  'various', 'multiple', 'owner', 'owner/builder', 'self',
                  'homeowner', 'not provided', 'not applicable', 'see plans',
                  'not listed', 'not available', 'exempt', '---', '--', '-'}

    contractors = {}
    for p in permits:
        name = (p.get('contact_name') or '').strip()
        if not name:
            continue
        name_lower = name.lower()

        # Skip exact junk matches
        if name_lower in JUNK_NAMES:
            continue

        # Skip names that START with common junk prefixes
        if name_lower.startswith(('none ', 'n/a ', 'unknown ', 'tbd ', 'owner ')):
            continue

        # Skip very short names (likely data artifacts)
        if len(name) < 3:
            continue

        if name not in contractors:
            contractors[name] = {'name': name, 'permits': 0, 'value': 0}

        contractors[name]['permits'] += 1
        contractors[name]['value'] += p.get('estimated_cost', 0) or 0

    # Sort by permit count
    top_list = sorted(contractors.values(), key=lambda x: x['permits'], reverse=True)[:limit]

    # V447 P3 (CODE_V447): expose the unsliced count so /analytics can
    # render "Active Contractors: 3,770" instead of always showing the
    # request limit (100). Frontend uses top_contractors.length today,
    # which capped Active Contractors at 100 forever.
    return jsonify({
        'top_contractors': top_list,
        'total_active_contractors': len(contractors),
        'city': city or 'All Cities',
    })


@api_bp.route('/api/forgot-password', methods=['POST'])
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
    html_body = render_template('emails/password_reset.html', reset_url=reset_url)

    try:
        from email_alerts import send_email
        send_email(email, "Reset Your PermitGrab Password", html_body)
    except Exception as e:
        print(f"Failed to send password reset email: {e}")
        # Still return success to prevent email enumeration

    return jsonify({'success': True, 'message': 'If that email exists, a reset link has been sent.'})


@api_bp.route('/api/reset-password', methods=['POST'])
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


@api_bp.route('/api/contact', methods=['POST'])
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


@api_bp.route('/api/onboarding', methods=['POST'])
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

        # V12.53: Update digest settings in User model instead of subscribers.json
        if daily_alerts and city:
            cities = json.loads(user_obj.digest_cities or '[]')
            if city not in cities:
                cities.append(city)
            user_obj.digest_cities = json.dumps(cities)
            user_obj.digest_active = True
        db.session.commit()

    # Track onboarding complete event
    analytics.track_event('onboarding_complete', event_data={
        'city': city,
        'trade': trade,
        'daily_alerts': daily_alerts
    }, city_filter=city, trade_filter=trade)

    return jsonify({'success': True})


@api_bp.route('/api/analytics/volume')
def api_analytics_volume():
    """
    GET /api/analytics/volume
    Query params: city, weeks (default 12)
    Returns weekly permit counts for trend analysis.

    V312 (CODE_V280b Bug 9): use COALESCE(filing_date, issued_date, date)
    so cities that only populate `date` (Phoenix, Miami-Dade, San Antonio)
    show up. Volume was empty before because the WHERE filter required
    filing_date to be non-null.
    """
    city = request.args.get('city', '')
    if city and city.lower() == 'all':
        city = ''  # V451 (CODE_V448 Phase 4): treat ?city=all as no-filter
    weeks = int(request.args.get('weeks', 12))

    conn = permitdb.get_connection()
    now = datetime.now()
    cutoff = (now - timedelta(weeks=weeks)).strftime('%Y-%m-%d')

    if city:
        cursor = conn.execute("""
            SELECT COALESCE(filing_date, issued_date, date) AS filing_date,
                   COUNT(*) AS cnt
            FROM permits
            WHERE city = ?
              AND COALESCE(filing_date, issued_date, date) IS NOT NULL
              AND COALESCE(filing_date, issued_date, date) >= ?
            GROUP BY COALESCE(filing_date, issued_date, date)
        """, (city, cutoff))
    else:
        cursor = conn.execute("""
            SELECT COALESCE(filing_date, issued_date, date) AS filing_date,
                   COUNT(*) AS cnt
            FROM permits
            WHERE COALESCE(filing_date, issued_date, date) IS NOT NULL
              AND COALESCE(filing_date, issued_date, date) >= ?
            GROUP BY COALESCE(filing_date, issued_date, date)
        """, (cutoff,))

    # Aggregate by week
    weekly_counts = {}
    for i in range(weeks):
        week_start = now - timedelta(weeks=i+1)
        week_key = week_start.strftime('%Y-%m-%d')
        weekly_counts[week_key] = 0

    for row in cursor:
        filing_date = row['filing_date']
        if not filing_date:
            continue
        try:
            filed = datetime.strptime(filing_date[:10], '%Y-%m-%d')
            weeks_ago = (now - filed).days // 7
            if 0 <= weeks_ago < weeks:
                week_start = now - timedelta(weeks=weeks_ago+1)
                week_key = week_start.strftime('%Y-%m-%d')
                if week_key in weekly_counts:
                    weekly_counts[week_key] += row['cnt']
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


@api_bp.route('/api/analytics/trades')
def api_analytics_trades():
    """
    GET /api/analytics/trades
    Query params: city
    Returns trade breakdown for the selected city.
    V12.51: Uses SQLite for efficient aggregation.
    """
    city = request.args.get('city', '')
    if city and city.lower() == 'all':
        city = ''  # V451 (CODE_V448 Phase 4): treat ?city=all as no-filter
    conn = permitdb.get_connection()

    if city:
        cursor = conn.execute("""
            SELECT COALESCE(trade_category, 'Other') as trade, COUNT(*) as cnt
            FROM permits
            WHERE city = ?
            GROUP BY trade_category
            ORDER BY cnt DESC
        """, (city,))
        total_row = conn.execute("SELECT COUNT(*) FROM permits WHERE city = ?", (city,)).fetchone()
    else:
        cursor = conn.execute("""
            SELECT COALESCE(trade_category, 'Other') as trade, COUNT(*) as cnt
            FROM permits
            GROUP BY trade_category
            ORDER BY cnt DESC
        """)
        total_row = conn.execute("SELECT COUNT(*) FROM permits").fetchone()

    trades = [{'trade': row['trade'] or 'Other', 'count': row['cnt']} for row in cursor]
    total = total_row[0] if total_row else 0

    return jsonify({
        'trades': trades,
        'total': total,
        'city': city or 'All Cities',
    })


@api_bp.route('/api/analytics/values')
def api_analytics_values():
    """
    GET /api/analytics/values
    Query params: city, weeks (default 12)
    Returns weekly average project values.
    V12.51: Uses SQLite for efficient aggregation.
    """
    city = request.args.get('city', '')
    if city and city.lower() == 'all':
        city = ''  # V451 (CODE_V448 Phase 4): treat ?city=all as no-filter
    weeks = int(request.args.get('weeks', 12))

    conn = permitdb.get_connection()
    now = datetime.now()
    cutoff = (now - timedelta(weeks=weeks)).strftime('%Y-%m-%d')

    # V312 (CODE_V280b Bug 9): same COALESCE date treatment as volume API.
    if city:
        cursor = conn.execute("""
            SELECT COALESCE(filing_date, issued_date, date) AS filing_date,
                   SUM(estimated_cost) AS total_value,
                   COUNT(*) AS cnt
            FROM permits
            WHERE city = ?
              AND COALESCE(filing_date, issued_date, date) IS NOT NULL
              AND COALESCE(filing_date, issued_date, date) >= ?
              AND estimated_cost > 0
            GROUP BY COALESCE(filing_date, issued_date, date)
        """, (city, cutoff))
    else:
        cursor = conn.execute("""
            SELECT COALESCE(filing_date, issued_date, date) AS filing_date,
                   SUM(estimated_cost) AS total_value,
                   COUNT(*) AS cnt
            FROM permits
            WHERE COALESCE(filing_date, issued_date, date) IS NOT NULL
              AND COALESCE(filing_date, issued_date, date) >= ?
              AND estimated_cost > 0
            GROUP BY COALESCE(filing_date, issued_date, date)
        """, (cutoff,))

    # Initialize week buckets
    weekly_values = {}
    weekly_counts = {}
    for i in range(weeks):
        week_start = now - timedelta(weeks=i+1)
        week_key = week_start.strftime('%Y-%m-%d')
        weekly_values[week_key] = 0
        weekly_counts[week_key] = 0

    # Aggregate by week
    for row in cursor:
        filing_date = row['filing_date']
        if not filing_date:
            continue
        try:
            filed = datetime.strptime(filing_date[:10], '%Y-%m-%d')
            weeks_ago = (now - filed).days // 7
            if 0 <= weeks_ago < weeks:
                week_start = now - timedelta(weeks=weeks_ago+1)
                week_key = week_start.strftime('%Y-%m-%d')
                if week_key in weekly_values:
                    weekly_values[week_key] += row['total_value'] or 0
                    weekly_counts[week_key] += row['cnt']
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


@api_bp.route('/api/signals')
def api_signals():
    """
    GET /api/signals
    Query params: city, type, status, page, per_page
    Returns pre-construction signals.
    """
    signals = load_signals()

    city = request.args.get('city', '')
    if city and city.lower() == 'all':
        city = ''  # V451 (CODE_V448 Phase 4): treat ?city=all as no-filter
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


@api_bp.route('/api/signals/<signal_id>')
def api_signal_detail(signal_id):
    """
    GET /api/signals/<signal_id>
    Returns a single signal with linked permits.
    V12.51: Uses SQLite for permit lookups.
    """
    signals = load_signals()
    signal = next((s for s in signals if s.get('signal_id') == signal_id), None)

    if not signal:
        return jsonify({'error': 'Signal not found'}), 404

    # Add lead potential
    signal['lead_potential'] = calculate_lead_potential(signal)

    # Load linked permits from SQLite
    linked_permits = []
    if signal.get('linked_permits'):
        conn = permitdb.get_connection()
        permit_numbers = signal['linked_permits']
        placeholders = ','.join('?' * len(permit_numbers))
        cursor = conn.execute(
            f"SELECT * FROM permits WHERE permit_number IN ({placeholders})",
            permit_numbers
        )
        linked_permits = [dict(row) for row in cursor]
        linked_permits = add_lead_scores(linked_permits)

    return jsonify({
        'signal': signal,
        'linked_permits': linked_permits,
    })


@api_bp.route('/api/signals/stats')
def api_signal_stats():
    """
    GET /api/signals/stats
    Query params: city
    Returns signal counts by type and status.
    """
    signals = load_signals()

    city = request.args.get('city', '')
    if city and city.lower() == 'all':
        city = ''  # V451 (CODE_V448 Phase 4): treat ?city=all as no-filter
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


@api_bp.route('/api/address-intel/<path:address>')
def api_address_intel(address):
    """
    GET /api/address-intel/<address>
    Returns ALL intelligence for an address: permits, signals, violations, history.
    V12.51: Uses SQLite for permits and history lookups.
    """
    normalized = normalize_address_for_lookup(address)

    if not normalized:
        return jsonify({'error': 'Address required'}), 400

    conn = permitdb.get_connection()

    # Find matching permits from SQLite (LIKE search on address)
    cursor = conn.execute(
        "SELECT * FROM permits WHERE LOWER(address) LIKE ?",
        (f"%{normalized}%",)
    )
    matching_permits = [dict(row) for row in cursor]
    matching_permits = add_lead_scores(matching_permits)

    # Signals and violations still use JSON (not in SQLite)
    signals = load_signals()
    violations = load_violations()

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

    # Find permit history from SQLite
    history_permits = permitdb.get_address_history(normalized)
    history_entry = {}
    if history_permits:
        history_entry = {
            'address': history_permits[0].get('address'),
            'city': history_permits[0].get('city'),
            'state': history_permits[0].get('state'),
            'permits': history_permits,
            'permit_count': len(history_permits),
        }

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


@api_bp.route('/api/create-checkout-session', methods=['POST'])
def create_checkout_session():
    """Create a Stripe Checkout Session for the requested plan.

    V211-2: If Stripe env vars aren't configured yet, return a graceful
    200 with a `fallback` mailto URL so the JS on /pricing can redirect
    the visitor to an email signup instead of alerting 'Stripe not
    configured'. Same behaviour when a plan's price id is missing.

    Plan-aware: accepts {plan: 'starter'|'pro'|'enterprise'}, looks up
    STRIPE_PRICE_STARTER / STRIPE_PRICE_PRO / STRIPE_PRICE_ENTERPRISE
    env vars first, falls back to the V12-era STRIPE_PRICE_ID + billing
    period for the existing Professional plan.
    """
    data = request.get_json() or {}
    plan = (data.get('plan') or '').strip().lower()
    customer_email = data.get('email')
    billing_period = data.get('billing_period', 'monthly')

    _v460_user = get_current_user()
    _v460_user_id = (_v460_user.get('id') if isinstance(_v460_user, dict)
                     else getattr(_v460_user, 'id', None))
    if not customer_email:
        customer_email = (_v460_user.get('email') if isinstance(_v460_user, dict)
                          else getattr(_v460_user, 'email', None))

    # V211-2: Graceful fallback — no Stripe keys means 'payments launching
    # soon' not 500 error. Let the pricing page JS direct to mailto.
    mailto_fallback = (
        f"mailto:wcrainshaw@gmail.com?subject=PermitGrab+"
        f"{plan.title() or 'Professional'}+Signup"
    )
    if not STRIPE_SECRET_KEY:
        return jsonify({
            'error': 'Payments launching soon!',
            'fallback': mailto_fallback,
        }), 200

    # Plan-aware price lookup (new path, V211-2)
    per_plan_price = {
        'starter': os.environ.get('STRIPE_PRICE_STARTER', ''),
        'pro': os.environ.get('STRIPE_PRICE_PRO', ''),
        'enterprise': os.environ.get('STRIPE_PRICE_ENTERPRISE', ''),
    }
    price_id = per_plan_price.get(plan)
    plan_name = plan + '_monthly' if plan else None

    # Legacy path: if no per-plan env var, use V12-era single-price flow
    if not price_id:
        if billing_period == 'annual' and STRIPE_ANNUAL_PRICE_ID:
            price_id = STRIPE_ANNUAL_PRICE_ID
            plan_name = 'professional_annual'
        elif STRIPE_PRICE_ID:
            price_id = STRIPE_PRICE_ID
            plan_name = 'professional_monthly'

    if not price_id:
        return jsonify({
            'error': 'Plan not fully configured yet',
            'fallback': mailto_fallback,
        }), 200

    stripe.api_key = STRIPE_SECRET_KEY

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
            # V218 T5D: route to the dedicated /success page so customers
            # land on a real confirmation with "what happens next" steps
            # instead of the generic homepage.
            success_url=f'{SITE_URL}/success?session_id={{CHECKOUT_SESSION_ID}}',
            cancel_url=f'{SITE_URL}/pricing?payment=cancelled',
            customer_email=customer_email,
            client_reference_id=str(_v460_user_id) if _v460_user_id else None,
            metadata={
                'plan': plan_name,
                'billing_period': billing_period,
                'user_id': str(_v460_user_id) if _v460_user_id else '',
            },
            allow_promotion_codes=True,
            # V253 P2 #6: 14-day free trial on all paid plans. Pricing
            # page already advertises "14 days free, no credit card
            # required" — actually honor it via Stripe trial_period_days
            # so Pro/Enterprise users aren't charged until day 14.
            # Matches the existing V251 F17 nav pill that says NEW.
            subscription_data={
                'trial_period_days': 14,
            },
        )
        return jsonify({'url': checkout_session.url})
    except stripe.error.StripeError as e:
        return jsonify({'error': str(e), 'fallback': mailto_fallback}), 400


@api_bp.route('/api/stripe-webhook', methods=['POST'])
def stripe_webhook():
    """Handle Stripe webhook events.

    V462 (CODE_V459 task 1): hardened against malformed inputs that
    previously raised uncaught exceptions in stripe.Webhook.construct_event
    (None sig_header, empty body) and bubbled to a 503. We now validate
    inputs explicitly, catch every exception class, and never let Stripe
    see a 5xx after the event is decoded — Stripe disables endpoints
    after 3 days of consecutive failures.
    """
    payload = request.get_data(as_text=True)
    sig_header = request.headers.get('Stripe-Signature')

    if not STRIPE_SECRET_KEY:
        print("[Stripe] STRIPE_SECRET_KEY not set — webhook cannot process", flush=True)
        return jsonify({'error': 'Stripe not configured'}), 500

    if not payload:
        return jsonify({'error': 'Empty payload'}), 400

    stripe.api_key = STRIPE_SECRET_KEY

    try:
        if STRIPE_WEBHOOK_SECRET:
            if not sig_header:
                return jsonify({'error': 'Missing Stripe-Signature header'}), 400
            event = stripe.Webhook.construct_event(
                payload, sig_header, STRIPE_WEBHOOK_SECRET
            )
        else:
            event = json.loads(payload)
    except ValueError:
        return jsonify({'error': 'Invalid payload'}), 400
    except stripe.error.SignatureVerificationError:
        return jsonify({'error': 'Invalid signature'}), 400
    except Exception as _construct_err:
        import traceback
        traceback.print_exc()
        print(f"[Stripe] event decode failed: {_construct_err}", flush=True)
        return jsonify({'error': 'Decode failed', 'detail': str(_construct_err)}), 400

    event_type = (event or {}).get('type') if isinstance(event, dict) else None
    event_id = (event or {}).get('id', '') if isinstance(event, dict) else ''
    if not event_type:
        return jsonify({'error': 'Event missing type field'}), 400
    print(f"[Stripe] Received event: {event_type} ({event_id})", flush=True)

    # V218 T5C: webhook idempotency. Stripe retries on delivery failure,
    # and the current handler would re-fire payment_success emails each
    # time. Track event IDs we've already processed and no-op on repeat.
    if event_id:
        try:
            # V229 addendum J2: table now lives in db.py init_database().
            # Previously the CREATE TABLE ran on every webhook event.
            _wh_conn = permitdb.get_connection()
            already = _wh_conn.execute(
                "SELECT 1 FROM stripe_webhook_events WHERE event_id = ?",
                (event_id,),
            ).fetchone()
            if already:
                print(f"[Stripe] event {event_id} already processed — skipping")
                return jsonify({'status': 'duplicate', 'event_id': event_id})
            _wh_conn.execute(
                "INSERT OR IGNORE INTO stripe_webhook_events (event_id, event_type) VALUES (?, ?)",
                (event_id, event_type),
            )
            _wh_conn.commit()
        except Exception as _e:
            # Don't fail the webhook over idempotency bookkeeping — better
            # to risk a duplicate email than 500 and let Stripe retry.
            print(f"[Stripe] idempotency check skipped (non-fatal): {_e}")

    # V462 (CODE_V459 task 1): wrap dispatch so a downstream provisioning
    # exception (DB write fail, email send fail, etc.) still returns 200 to
    # Stripe. The event_id is already persisted; we'd rather risk a missed
    # side-effect than have Stripe disable the webhook after 3 failed retries.
    try:
        # V12.53: Handle all subscription lifecycle events
        if event_type == 'checkout.session.completed':
            session_obj = event['data']['object']
            customer_email = session_obj.get('customer_email') or session_obj.get('customer_details', {}).get('email')
            plan = session_obj.get('metadata', {}).get('plan', 'professional')

            client_ref_id = session_obj.get('client_reference_id')
            metadata_user_id = (session_obj.get('metadata') or {}).get('user_id')
            user = None
            for _uid_candidate in (client_ref_id, metadata_user_id):
                if _uid_candidate:
                    try:
                        user = User.query.get(int(_uid_candidate))
                        if user:
                            break
                    except (ValueError, TypeError):
                        pass
            if not user and customer_email:
                user = find_user_by_email(customer_email)

            if user:
                user.plan = 'pro'
                user.stripe_customer_id = session_obj.get('customer')
                user.subscription_id = session_obj.get('subscription')
                user.trial_end_date = None
                user.trial_started_at = None
                db.session.commit()
                print(f"[Stripe] User id={user.id} email={user.email} upgraded to {plan}", flush=True)

                try:
                    from email_alerts import send_payment_success
                    send_payment_success(user, plan)
                except Exception as e:
                    print(f"[Stripe] Payment success email failed: {e}", flush=True)

                analytics.track_event('payment_success', event_data={
                    'plan': plan,
                    'stripe_customer_id': session_obj.get('customer')
                }, user_id_override=user.email)

            # V494: sync the SQLite subscribers table so the digest
            # scheduler actually reaches paid customers.
            #
            # Pre-V494 the webhook only updated the User model (Postgres
            # via Flask-SQLAlchemy). The subscribers SQLite table — the
            # actual source-of-truth for daily digest sends — was never
            # touched on checkout.session.completed. Result: every paid
            # customer since launch landed in subscribers-table-NULL
            # state and received no digest (Higgins May 2, Meyer May 1,
            # Gomes earlier — all hand-rescued by Wes).
            _wh_email = (
                customer_email
                or session_obj.get('customer_email')
                or (session_obj.get('customer_details') or {}).get('email')
                or ''
            ).strip().lower()
            if _wh_email:
                try:
                    import db as _permitdb_v494
                    _conn_v494 = _permitdb_v494.get_connection()
                    # 1. Sync plan on existing row(s)
                    _conn_v494.execute(
                        "UPDATE subscribers SET plan = ? "
                        "WHERE LOWER(email) = ?",
                        (plan, _wh_email)
                    )
                    # 2. Activate any pending row from /select-cities
                    _conn_v494.execute(
                        "UPDATE subscribers SET active = 1 "
                        "WHERE LOWER(email) = ? AND active = 0",
                        (_wh_email,)
                    )
                    # 3. Last-resort insert + alert if no row exists
                    _row_check = _conn_v494.execute(
                        "SELECT id, digest_cities FROM subscribers "
                        "WHERE LOWER(email) = ? LIMIT 1",
                        (_wh_email,)
                    ).fetchone()
                    if not _row_check:
                        _user_name_v494 = ''
                        try:
                            _user_name_v494 = (
                                (user.name if user else '')
                                or (session_obj.get('customer_details') or {}).get('name')
                                or ''
                            )
                        except Exception:
                            pass
                        _conn_v494.execute(
                            "INSERT INTO subscribers "
                            "(email, name, plan, digest_cities, active, created_at) "
                            "VALUES (?, ?, ?, '[]', 1, datetime('now'))",
                            (_wh_email, _user_name_v494, plan)
                        )
                        print(
                            f"[V494] paid customer {_wh_email} created "
                            f"with empty digest_cities — needs follow-up",
                            flush=True,
                        )
                    _conn_v494.commit()
                except Exception as _wh_e:
                    print(f"[V494] subscribers webhook sync failed: {_wh_e}",
                          flush=True)

        elif event_type == 'invoice.payment_failed':
            invoice = event['data']['object']
            customer_email = invoice.get('customer_email')
            if customer_email:
                user = find_user_by_email(customer_email)
                if user:
                    print(f"[Stripe] Payment failed for {customer_email}", flush=True)
                    try:
                        from email_alerts import send_payment_failed
                        send_payment_failed(user)
                    except Exception as e:
                        print(f"[Stripe] Payment failed email failed: {e}", flush=True)

        elif event_type == 'invoice.payment_succeeded':
            invoice = event['data']['object']
            customer_email = invoice.get('customer_email')
            billing_reason = invoice.get('billing_reason')
            if customer_email and billing_reason == 'subscription_cycle':
                user = find_user_by_email(customer_email)
                if user:
                    print(f"[Stripe] Subscription renewed for {customer_email}", flush=True)
                    try:
                        from email_alerts import send_subscription_renewed
                        send_subscription_renewed(user)
                    except Exception as e:
                        print(f"[Stripe] Renewal email failed: {e}", flush=True)

        elif event_type == 'customer.subscription.deleted':
            subscription = event['data']['object']
            customer_id = subscription.get('customer')
            user = User.query.filter_by(stripe_customer_id=customer_id).first()
            if user:
                user.plan = 'free'
                db.session.commit()
                print(f"[Stripe] Subscription cancelled for {user.email}", flush=True)
                try:
                    from email_alerts import send_subscription_cancelled
                    send_subscription_cancelled(user)
                except Exception as e:
                    print(f"[Stripe] Cancellation email failed: {e}", flush=True)
    except Exception as _dispatch_err:
        import traceback
        traceback.print_exc()
        print(f"[Stripe] dispatch exception (event {event_id} {event_type}): {_dispatch_err}", flush=True)

    return jsonify({'status': 'success'})


@api_bp.route('/api/webhooks/sendgrid', methods=['POST'])
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


@api_bp.route('/api/register', methods=['POST'])
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
        import secrets
        plan = data.get('plan', 'free')
        is_trial = plan == 'pro_trial'

        new_user = User(
            email=email,
            name=name,
            password_hash=generate_password_hash(password),
            plan='pro_trial' if is_trial else 'free',
            # V12.53: Email system fields
            unsubscribe_token=secrets.token_urlsafe(32),
            digest_active=True,
            trial_started_at=datetime.utcnow() if is_trial else None,
            trial_end_date=(datetime.utcnow() + timedelta(days=14)) if is_trial else None,
        )
        db.session.add(new_user)
        db.session.commit()
        print(f"[Register] User created in database: {email} (plan: {new_user.plan})")
    except IntegrityError:
        # Database UNIQUE constraint caught a race condition
        db.session.rollback()
        print(f"[Register] DUPLICATE BLOCKED (IntegrityError): {email}")
        return jsonify({'error': 'An account with this email already exists. Please log in instead.'}), 409

    # Log in the user
    # V377 (CODE_V363 P0 follow-up): mark the session permanent so it
    # survives until PERMANENT_SESSION_LIFETIME (Flask default 31 days)
    # instead of expiring whenever the browser closes. The directive
    # reported "user gets randomly logged out between page loads (nav
    # flips between 'Wes Crainshaw' and 'Log In / Sign Up')" — that's
    # the transient-session pattern. Mirrored on /api/login below.
    session.permanent = True
    session['user_email'] = email
    # V459 (CODE_V456): also drive flask-login's session so current_user
    # / @login_required see the logged-in state.
    try:
        _flask_login_user(new_user, remember=True)
    except Exception as _li_e:
        print(f"[V459] login_user failed (non-fatal): {_li_e}", flush=True)

    # Track signup event
    analytics.track_event('signup', event_data={'method': 'email', 'plan': new_user.plan})

    # V12.53: Send welcome email (async to not block registration)
    try:
        from email_alerts import send_welcome_free, send_welcome_pro_trial
        if new_user.plan == 'pro_trial':
            send_welcome_pro_trial(new_user)
            new_user.welcome_email_sent = True
            db.session.commit()
            print(f"[Register] Welcome Pro Trial email sent to {email}")
        else:
            send_welcome_free(new_user)
            new_user.welcome_email_sent = True
            db.session.commit()
            print(f"[Register] Welcome Free email sent to {email}")
    except Exception as e:
        print(f"[Register] Welcome email failed for {email}: {e}")

    # Return user without password hash
    return jsonify({
        'message': 'Registration successful',
        'user': {
            'email': new_user.email,
            'name': new_user.name,
            'plan': new_user.plan,
        }
    }), 201


@api_bp.route('/api/login', methods=['POST'])
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
    # V377: permanent session — see /api/register for full reasoning.
    session.permanent = True
    session['user_email'] = email
    # V459 (CODE_V456): mirror into flask-login session.
    try:
        _flask_login_user(user, remember=True)
    except Exception as _li_e:
        print(f"[V459] login_user failed (non-fatal): {_li_e}", flush=True)

    # V12.53: Update last_login_at timestamp
    user.last_login_at = datetime.utcnow()
    db.session.commit()

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


@api_bp.route('/api/logout', methods=['POST'])
def api_logout():
    """POST /api/logout - Log out the current user."""
    session.pop('user_email', None)
    # V459 (CODE_V456): also clear flask-login state.
    try:
        _flask_logout_user()
    except Exception:
        pass
    return jsonify({'message': 'Logged out'})


@api_bp.route('/api/me')
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


@api_bp.route('/api/unsubscribe')
def api_unsubscribe():
    """GET /api/unsubscribe?token=xxx - Unsubscribe from email alerts."""
    token = request.args.get('token', '')

    if not token:
        return render_template('unsubscribe_invalid.html',
            message='This unsubscribe link is invalid or has expired.'), 400

    # V12.53: Use User model instead of subscribers.json
    user = User.query.filter_by(unsubscribe_token=token).first()

    if not user:
        return render_template('unsubscribe_invalid.html',
            message='This unsubscribe link is invalid or has already been used.'), 404

    # Mark digest as inactive
    user.digest_active = False
    db.session.commit()

    return render_template('unsubscribe_success.html', email=user.email)


@api_bp.route('/api/collection-status')
def api_collection_status():
    """GET /api/collection-status - Check data collection status (admin only).
    V12.51: Uses SQLite for permit stats.
    """
    if not session.get('admin_authenticated'):
        return jsonify({'error': 'Admin authentication required'}), 401

    stats = load_stats()
    permit_stats = permitdb.get_permit_stats()

    # Check data directory (some JSON files still exist for signals/violations)
    data_files = {}
    for filename in ['violations.json', 'signals.json', 'city_health.json']:
        filepath = os.path.join(DATA_DIR, filename)
        if os.path.exists(filepath):
            data_files[filename] = {
                'exists': True,
                'size_kb': round(os.path.getsize(filepath) / 1024, 1),
                'modified': datetime.fromtimestamp(os.path.getmtime(filepath)).isoformat(),
            }
        else:
            data_files[filename] = {'exists': False}

    # Add SQLite database info
    if os.path.exists(permitdb.DB_PATH):
        data_files['permitgrab.db'] = {
            'exists': True,
            'size_kb': round(os.path.getsize(permitdb.DB_PATH) / 1024, 1),
            'modified': datetime.fromtimestamp(os.path.getmtime(permitdb.DB_PATH)).isoformat(),
        }

    return jsonify({
        'data_dir': DATA_DIR,
        'total_permits': permit_stats['total_permits'],
        'unique_cities': permit_stats['city_count'],
        'last_collection': stats.get('collected_at', 'Never'),
        'city_stats': stats.get('city_stats', {}),
        'data_files': data_files,
        'collector_started': _s._collector_started,
    })


@api_bp.route('/api/account', methods=['PUT'])
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


@api_bp.route('/api/saved-searches', methods=['GET'])
def list_saved_searches():
    """List user's saved searches."""
    if 'user_email' not in session:
        return jsonify({'error': 'Login required'}), 401
    user = find_user_by_email(session['user_email'])
    if not user:
        return jsonify({'error': 'User not found'}), 404
    searches = SavedSearch.query.filter_by(user_id=user.id).order_by(SavedSearch.created_at.desc()).all()
    return jsonify({'searches': [s.to_dict() for s in searches]})


@api_bp.route('/api/saved-searches', methods=['POST'])
def create_saved_search():
    """Create a new saved search."""
    if 'user_email' not in session:
        return jsonify({'error': 'Login required'}), 401
    user = find_user_by_email(session['user_email'])
    if not user:
        return jsonify({'error': 'User not found'}), 404
    data = request.get_json() or {}
    search = SavedSearch(
        user_id=user.id,
        name=data.get('name', 'Untitled Search'),
        city_slug=data.get('city_slug'),
        trade=data.get('trade'),
        tier=data.get('tier'),
        min_value=data.get('min_value'),
        frequency=data.get('frequency', 'daily'),
    )
    db.session.add(search)
    db.session.commit()
    return jsonify({'search': search.to_dict(), 'created': True}), 201


@api_bp.route('/api/saved-searches/<int:search_id>', methods=['PATCH'])
def update_saved_search(search_id):
    """Update a saved search (active/name)."""
    if 'user_email' not in session:
        return jsonify({'error': 'Login required'}), 401
    user = find_user_by_email(session['user_email'])
    search = SavedSearch.query.filter_by(id=search_id, user_id=user.id).first()
    if not search:
        return jsonify({'error': 'Not found'}), 404
    data = request.get_json() or {}
    if 'active' in data:
        search.active = int(data['active'])
    if 'name' in data:
        search.name = data['name']
    db.session.commit()
    return jsonify({'search': search.to_dict()})


@api_bp.route('/api/saved-searches/<int:search_id>', methods=['DELETE'])
def delete_saved_search(search_id):
    """Delete a saved search."""
    if 'user_email' not in session:
        return jsonify({'error': 'Login required'}), 401
    user = find_user_by_email(session['user_email'])
    search = SavedSearch.query.filter_by(id=search_id, user_id=user.id).first()
    if not search:
        return jsonify({'error': 'Not found'}), 404
    db.session.delete(search)
    db.session.commit()
    return jsonify({'deleted': True})


@api_bp.route('/api/competitors/watch', methods=['GET', 'POST', 'DELETE'])
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


@api_bp.route('/api/competitors/matches')
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

    # V12.51: Use SQLite with LIKE queries for competitor matching
    conn = permitdb.get_connection()
    matches = []

    for comp in competitors:
        cursor = conn.execute("""
            SELECT * FROM permits
            WHERE LOWER(contact_name) LIKE ?
            ORDER BY filing_date DESC
            LIMIT 50
        """, (f"%{comp.lower()}%",))

        for row in cursor:
            matches.append({
                'permit': dict(row),
                'matched_competitor': comp
            })

    # Sort by filing date, most recent first
    matches.sort(key=lambda x: x['permit'].get('filing_date', ''), reverse=True)

    return jsonify({'matches': matches[:50]})  # Limit to 50 most recent


@api_bp.route('/api/change-password', methods=['POST'])
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


