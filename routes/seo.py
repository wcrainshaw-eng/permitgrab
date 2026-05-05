"""V471 PR2 (CODE_V471 Part 1B): seo blueprint extracted from server.py.

Routes: 10 URLs across 10 handlers.

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

seo_bp = Blueprint('seo', __name__)


from server import _generate_market_reports, _generate_sitemap_xml, _get_city_lastmod_map, _v467_render_seo_blog_post, _v469_render_faq_blog_post

@seo_bp.route('/google3ef154d70f8049a0.html')
def google_verification():
    return Response('google-site-verification: google3ef154d70f8049a0.html', mimetype='text/html')


@seo_bp.route('/blog')
def blog_index():
    """V79: Blog index page listing all posts by category.

    V318 (CODE_V280b Bug 20): inject a "Market Reports" category at the top
    with data-driven posts generated from real DB stats. Old setup had 67
    permit-cost guides all dated 2026-04-06 and looked auto-generated.
    """
    footer_cities = get_cities_with_data()

    market_reports = _generate_market_reports()

    # V488 IRONCLAD: surface buyer-persona + city-persona posts on the
    # blog index. Without this, the 5 V482 buyer posts and 10 V486+V487
    # city × persona posts are unreachable from /blog — Google had to
    # find them via sitemap-blog.xml alone, which works for indexing
    # but kills internal-link equity. Each post object on this page
    # gets converted to {slug,title,excerpt,date} the template expects.
    buyer_persona_index = []
    try:
        from buyer_persona_posts import BUYER_PERSONA_POSTS
        for slug, p in BUYER_PERSONA_POSTS.items():
            buyer_persona_index.append({
                'slug': slug,
                'title': p.get('h1') or p.get('title') or slug,
                'excerpt': (p.get('meta_description') or '')[:240],
                'date': p.get('meta_published') or '2026-04-15',
            })
    except Exception as _bpe:
        print(f"[V488] buyer_persona_posts import skipped: {_bpe}", flush=True)

    city_persona_index = []
    try:
        from city_persona_posts import CITY_PERSONA_POSTS
        for slug, p in CITY_PERSONA_POSTS.items():
            city_persona_index.append({
                'slug': slug,
                'title': p.get('h1') or p.get('title') or slug,
                'excerpt': (p.get('meta_description') or '')[:240],
                'city_name': p.get('city') or '',
                'date': p.get('meta_published') or '2026-05-01',
            })
    except Exception as _cpe:
        print(f"[V488] city_persona_posts import skipped: {_cpe}", flush=True)

    categories = {
        'market-reports': {
            'title': 'City Market Reports (Live Data)',
            'posts': market_reports,
        },
        # V488 new sections — placed BEFORE the cost/trade guides so the
        # high-intent buyer-facing content sits at the top of /blog.
        'buyer-personas': {
            'title': 'Lead-Generation Guides by Persona',
            'posts': buyer_persona_index,
        },
        'city-personas': {
            'title': 'City × Persona Lead Guides',
            'posts': city_persona_index,
        },
        'permit-costs': {
            'title': 'Permit Cost Guides',
            'posts': get_blog_posts_by_category('permit-costs')
        },
        'contractor-leads': {
            'title': 'Finding Construction Leads',
            'posts': get_blog_posts_by_category('contractor-leads')
        },
        'trade-guides': {
            'title': 'Trade-Specific Guides',
            'posts': get_blog_posts_by_category('trade-guides')
        }
    }

    return render_template('blog_index.html',
                           categories=categories,
                           all_posts=BLOG_POSTS,
                           footer_cities=footer_cities)


@seo_bp.route('/blog/<slug>')
def blog_post(slug):
    """V79: Individual blog post page.

    V318 (CODE_V280b Bug 20): if slug starts with market-report-, regenerate
    the live report on the fly so the numbers stay fresh on every load.
    V467: SEO blog posts for ad-ready cities — checked first, before the
    legacy BLOG_POSTS list lookup.
    V482 Part B3: buyer-persona blog posts (NOT city-specific) checked
    first. They're hand-written + pre-baked with real numbers and run zero
    DB queries at render time.
    """
    try:
        from buyer_persona_posts import BUYER_PERSONA_POSTS
        if slug in BUYER_PERSONA_POSTS:
            post = BUYER_PERSONA_POSTS[slug]
            related_posts = [(s, p) for s, p in BUYER_PERSONA_POSTS.items() if s != slug][:3]
            footer_cities = get_cities_with_data()
            return render_template(
                'blog/persona/post.html',
                slug=slug, post=post,
                related_posts=related_posts,
                footer_cities=footer_cities,
            )
    except Exception as _e:
        print(f"[V482] buyer persona post render failed for {slug}: {_e}", flush=True)

    # V486 Part C: city × persona long-tail blog posts. 10 entries
    # (phoenix-solar-installer-leads, miami-dade-insurance-agent-leads,
    # chicago-motivated-seller-leads, etc). Reuses the V482 shell template;
    # the extra city / city_slug / persona_slug fields are passed through
    # for the optional internal-link breadcrumb the template renders when
    # they're present.
    try:
        from city_persona_posts import CITY_PERSONA_POSTS
        if slug in CITY_PERSONA_POSTS:
            post = CITY_PERSONA_POSTS[slug]
            # Pick 3 related: prefer same persona (different city), then
            # same city (different persona), then anything else.
            same_persona = [(s, p) for s, p in CITY_PERSONA_POSTS.items()
                            if s != slug and p.get('persona_slug') == post.get('persona_slug')]
            same_city = [(s, p) for s, p in CITY_PERSONA_POSTS.items()
                         if s != slug and p.get('city_slug') == post.get('city_slug')
                         and (s, p) not in same_persona]
            others = [(s, p) for s, p in CITY_PERSONA_POSTS.items()
                      if s != slug and (s, p) not in same_persona and (s, p) not in same_city]
            related_posts = (same_persona + same_city + others)[:3]
            footer_cities = get_cities_with_data()
            return render_template(
                'blog/persona/post.html',
                slug=slug, post=post,
                related_posts=related_posts,
                footer_cities=footer_cities,
            )
    except Exception as _e:
        print(f"[V486] city persona post render failed for {slug}: {_e}", flush=True)

    _seo = _v467_render_seo_blog_post(slug)
    if _seo is not None:
        return _seo

    _faq = _v469_render_faq_blog_post(slug)
    if _faq is not None:
        return _faq

    if slug.startswith('market-report-'):
        for p in _generate_market_reports():
            if p['slug'] == slug:
                footer_cities = get_cities_with_data()
                return render_template('blog_post.html',
                                       post=p,
                                       related_posts=[],
                                       footer_cities=footer_cities)
        abort(404)

    post = next((p for p in BLOG_POSTS if p['slug'] == slug), None)
    if not post:
        abort(404)

    footer_cities = get_cities_with_data()
    related_posts = get_related_posts(slug, limit=3)

    return render_template('blog_post.html',
                           post=post,
                           related_posts=related_posts,
                           footer_cities=footer_cities)


@seo_bp.route('/sitemap.xml')
def sitemap_index():
    """V28: Sitemap index pointing to child sitemaps."""
    today = datetime.now().strftime('%Y-%m-%d')

    xml_parts = ['<?xml version="1.0" encoding="UTF-8"?>\n',
                 '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n']

    child_sitemaps = [
        ('sitemap-pages.xml', today),
        ('sitemap-cities.xml', today),
        ('sitemap-states.xml', today),  # V233 P1-3
        ('sitemap-trades.xml', today),
        ('sitemap-blog.xml', today),
    ]

    for sitemap_name, lastmod in child_sitemaps:
        xml_parts.append('  <sitemap>\n')
        xml_parts.append(f"    <loc>{SITE_URL}/{sitemap_name}</loc>\n")
        xml_parts.append(f"    <lastmod>{lastmod}</lastmod>\n")
        xml_parts.append('  </sitemap>\n')

    xml_parts.append('</sitemapindex>')
    return Response(''.join(xml_parts), mimetype='application/xml')


@seo_bp.route('/sitemap-pages.xml')
def sitemap_pages():
    """V28: Sitemap for static pages."""
    today = datetime.now().strftime('%Y-%m-%d')
    urls = [
        {'loc': SITE_URL, 'changefreq': 'daily', 'priority': '1.0', 'lastmod': today},
        {'loc': f"{SITE_URL}/pricing", 'changefreq': 'weekly', 'priority': '0.9', 'lastmod': today},
        {'loc': f"{SITE_URL}/contractors", 'changefreq': 'daily', 'priority': '0.8', 'lastmod': today},
        {'loc': f"{SITE_URL}/map", 'changefreq': 'daily', 'priority': '0.8', 'lastmod': today},
        {'loc': f"{SITE_URL}/get-alerts", 'changefreq': 'weekly', 'priority': '0.7', 'lastmod': today},
        {'loc': f"{SITE_URL}/blog", 'changefreq': 'weekly', 'priority': '0.7', 'lastmod': today},
        {'loc': f"{SITE_URL}/cities", 'changefreq': 'daily', 'priority': '0.9', 'lastmod': today},
        {'loc': f"{SITE_URL}/stats", 'changefreq': 'daily', 'priority': '0.7', 'lastmod': today},
        # V474 (CODE_V474_BUYER_PERSONAS): three persona landing pages.
        # Buyer-intent SEO; daily changefreq because the data they cite is
        # pulled fresh on every render.
        {'loc': f"{SITE_URL}/leads/real-estate-investors", 'changefreq': 'daily', 'priority': '0.9', 'lastmod': today},
        {'loc': f"{SITE_URL}/leads/contractors", 'changefreq': 'daily', 'priority': '0.9', 'lastmod': today},
        {'loc': f"{SITE_URL}/leads/home-services", 'changefreq': 'daily', 'priority': '0.9', 'lastmod': today},
        # V480: alias for the same audience under the solar-leaning slug.
        {'loc': f"{SITE_URL}/leads/solar-home-services", 'changefreq': 'daily', 'priority': '0.9', 'lastmod': today},
        # V482 Part B1+B2: two new buyer-persona landing pages.
        {'loc': f"{SITE_URL}/leads/insurance", 'changefreq': 'weekly', 'priority': '0.85', 'lastmod': today},
        {'loc': f"{SITE_URL}/leads/suppliers", 'changefreq': 'weekly', 'priority': '0.85', 'lastmod': today},
        {'loc': f"{SITE_URL}/about", 'changefreq': 'monthly', 'priority': '0.6', 'lastmod': today},
        {'loc': f"{SITE_URL}/contact", 'changefreq': 'monthly', 'priority': '0.5', 'lastmod': today},
        {'loc': f"{SITE_URL}/privacy", 'changefreq': 'monthly', 'priority': '0.3', 'lastmod': today},
        {'loc': f"{SITE_URL}/terms", 'changefreq': 'monthly', 'priority': '0.3', 'lastmod': today},
    ]
    return Response(_generate_sitemap_xml(urls), mimetype='application/xml')


@seo_bp.route('/sitemap-cities.xml')
def sitemap_cities():
    """V28: Sitemap for city pages and state hub pages.
    V77: Added city URLs in /permits/{state}/{city} format for SEO.
    """
    today = datetime.now().strftime('%Y-%m-%d')
    url_map = {}

    # V77: Create reverse mapping from state abbrev to state slug
    abbrev_to_state_slug = {v['abbrev']: k for k, v in STATE_CONFIG.items()}

    # State hub pages
    for state_slug in STATE_CONFIG.keys():
        url_map[f"{SITE_URL}/permits/{state_slug}"] = {
            'loc': f"{SITE_URL}/permits/{state_slug}",
            'changefreq': 'daily',
            'priority': '0.85',
            'lastmod': today
        }

    # Get cities with permits
    cities_with_data = get_cities_with_data()
    cities_with_permits = {c['name'] for c in cities_with_data}
    city_lastmod = _get_city_lastmod_map()
    state_slugs = set(STATE_CONFIG.keys())

    # V485 B1 (CODE_V485 SEO P0): the legacy 1-segment /permits/<slug>
    # entries that lived here have been DROPPED. They were duplicate-
    # canonicalizing every city: /permits/chicago-il and
    # /permits/illinois/chicago-il both went into the sitemap, each
    # self-canonicalizing, and Google saw ~716 city URLs (358 cities ×
    # 2 paths) — picked one form arbitrarily, dropped the other
    # unevenly. Result: only 2 of 3,115 pages indexed. The 1-segment
    # /permits/<slug> route now 301-redirects to
    # /permits/<state>/<slug> (see state_or_city_landing in
    # routes/city_pages.py), so external links still resolve and link
    # equity flows to the canonical form. The 2-segment loop below
    # is now the SOLE source of city URLs in this sitemap.

    # V77: Add city URLs in new /permits/{state}/{city} format
    # These are the SEO-optimized URLs targeting "[city] building permits" keywords
    try:
        conn = permitdb.get_connection()
        active_cities = conn.execute("""
            SELECT city_slug, state, last_collection, data_freshness, total_permits
            FROM prod_cities
            WHERE status = 'active'
              AND data_freshness != 'no_data'
              AND total_permits > 0
        """).fetchall()

        for city_row in active_cities:
            city_slug = city_row['city_slug']
            state_abbrev = city_row['state']
            last_collection = city_row['last_collection']

            # Get state slug from abbreviation
            state_slug = abbrev_to_state_slug.get(state_abbrev)
            if not state_slug:
                continue  # Skip if state not in our config

            # Format lastmod
            lastmod = last_collection[:10] if last_collection else today

            # New format: /permits/{state}/{city}
            loc = f"{SITE_URL}/permits/{state_slug}/{city_slug}"
            if loc not in url_map:
                url_map[loc] = {
                    'loc': loc,
                    'changefreq': 'daily',
                    'priority': '0.7',
                    'lastmod': lastmod
                }
    except Exception as e:
        print(f"[sitemap_cities] V77 city URLs error: {e}")

    return Response(_generate_sitemap_xml(url_map.values()), mimetype='application/xml')


@seo_bp.route('/sitemap-states.xml')
def sitemap_states():
    """V506: state-hub URLs only. /permits/{state}/{city} URLs already
    live in sitemap-cities.xml — duplicating them here just bloated the
    file and diluted state-hub crawl budget. State pages are the hierarchy
    Google should crawl most aggressively (priority 0.9, daily refresh)."""
    today = datetime.now().strftime('%Y-%m-%d')
    url_map = {}
    for state_slug in STATE_CONFIG.keys():
        loc = f"{SITE_URL}/permits/{state_slug}"
        url_map[loc] = {
            'loc': loc,
            'changefreq': 'weekly',
            'priority': '0.9',
            'lastmod': today,
        }
    return Response(_generate_sitemap_xml(url_map.values()), mimetype='application/xml')


@seo_bp.route('/sitemap-trades.xml')
def sitemap_trades():
    """V28: Sitemap for city × trade pages."""
    today = datetime.now().strftime('%Y-%m-%d')
    url_map = {}

    cities_with_data = get_cities_with_data()
    cities_with_permits = {c['name'] for c in cities_with_data}
    city_lastmod = _get_city_lastmod_map()
    state_slugs = set(STATE_CONFIG.keys())
    trade_slugs = [t for t in get_trade_slugs() if t != 'all-trades']

    all_discovered_cities = discover_cities_from_permits()
    for slug, city_info in all_discovered_cities.items():
        if slug in state_slugs:
            continue
        if city_info['name'] not in cities_with_permits:
            continue

        lastmod = city_lastmod.get(slug, today)
        for trade_slug in trade_slugs:
            loc = f"{SITE_URL}/permits/{slug}/{trade_slug}"
            if loc not in url_map:
                url_map[loc] = {
                    'loc': loc,
                    'changefreq': 'daily',
                    'priority': '0.7',
                    'lastmod': lastmod
                }

    return Response(_generate_sitemap_xml(url_map.values()), mimetype='application/xml')


@seo_bp.route('/sitemap-blog.xml')
def sitemap_blog():
    """V79: Sitemap for blog posts — uses BLOG_POSTS data structure.

    V467: also enumerates SEO_BLOG_POSTS (the 5 ad-ready-city long-form
    articles) at higher priority + daily changefreq so Google re-crawls
    them as the live numbers in the page change.
    """
    urls = []

    urls.append({
        'loc': f"{SITE_URL}/blog",
        'changefreq': 'weekly',
        'priority': '0.7',
        'lastmod': '2026-04-06'
    })

    try:
        from seo_blog_posts import SEO_BLOG_POSTS
        for _slug in SEO_BLOG_POSTS:
            urls.append({
                'loc': f"{SITE_URL}/blog/{_slug}",
                'changefreq': 'daily',
                'priority': '0.85',
                'lastmod': '2026-04-28',
            })
    except Exception as _seo_err:
        print(f"[V467] sitemap-blog SEO posts skipped: {_seo_err}", flush=True)

    try:
        from faq_blog_posts import FAQ_BLOG_POSTS
        for _slug in FAQ_BLOG_POSTS:
            urls.append({
                'loc': f"{SITE_URL}/blog/{_slug}",
                'changefreq': 'weekly',
                'priority': '0.8',
                'lastmod': '2026-04-29',
            })
    except Exception as _faq_err:
        print(f"[V469] sitemap-blog FAQ posts skipped: {_faq_err}", flush=True)

    # V482 Part B3: buyer-persona blog posts.
    try:
        from buyer_persona_posts import BUYER_PERSONA_POSTS
        for _slug, _post in BUYER_PERSONA_POSTS.items():
            urls.append({
                'loc': f"{SITE_URL}/blog/{_slug}",
                'changefreq': 'weekly',
                'priority': '0.85',
                'lastmod': _post.get('meta_published', '2026-05-01'),
            })
    except Exception as _bp_err:
        print(f"[V482] sitemap-blog persona posts skipped: {_bp_err}", flush=True)

    # V486 Part C: city × persona long-tail blog posts.
    try:
        from city_persona_posts import CITY_PERSONA_POSTS
        for _slug, _post in CITY_PERSONA_POSTS.items():
            urls.append({
                'loc': f"{SITE_URL}/blog/{_slug}",
                'changefreq': 'monthly',
                'priority': '0.7',
                'lastmod': _post.get('meta_published', '2026-05-02'),
            })
    except Exception as _cp_err:
        print(f"[V486] sitemap-blog city-persona posts skipped: {_cp_err}", flush=True)

    for post in BLOG_POSTS:
        urls.append({
            'loc': f"{SITE_URL}/blog/{post['slug']}",
            'changefreq': 'weekly',
            'priority': '0.6',
            'lastmod': post['date']
        })

    return Response(_generate_sitemap_xml(urls), mimetype='application/xml')


@seo_bp.route('/robots.txt')
def robots():
    """V506 update: explicit AI-crawler allows + drop Crawl-delay (Bing
    respects it and caps crawl rate too low for our 12K+ URL site)."""
    content = f"""# PermitGrab robots.txt

# AI search engines — explicit allows for citation visibility
User-agent: OAI-SearchBot
Allow: /

User-agent: ChatGPT-User
Allow: /

User-agent: GPTBot
Allow: /

User-agent: PerplexityBot
Allow: /

User-agent: ClaudeBot
Allow: /

User-agent: anthropic-ai
Allow: /

User-agent: Google-Extended
Allow: /

User-agent: GoogleOther
Allow: /

User-agent: Applebot-Extended
Allow: /

User-agent: Amazonbot
Allow: /

User-agent: CCBot
Allow: /

# Standard search engines
User-agent: *
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
Disallow: /select-cities
Disallow: /start-checkout
Disallow: /success
Disallow: /pricing?

Sitemap: {SITE_URL}/sitemap.xml
"""
    return Response(content, mimetype='text/plain')


@seo_bp.route('/<key>.txt')
def indexnow_key_file(key):
    """V506 FIX 10: IndexNow protocol key-file. Microsoft Bing + Yandex
    fetch /<INDEXNOW_KEY>.txt to verify ownership before accepting URL
    pushes. The key value comes from the INDEXNOW_KEY env var.

    Generate the key once: python3 -c 'import secrets; print(secrets.token_hex(16))'
    Set as INDEXNOW_KEY on Render.
    """
    # Don't accidentally serve other .txt files (robots, security, llms).
    # Those have their own dedicated routes that take precedence.
    import os
    expected = os.environ.get('INDEXNOW_KEY') or ''
    if expected and key == expected:
        return Response(expected, mimetype='text/plain')
    from flask import abort
    abort(404)


@seo_bp.route('/.well-known/security.txt')
def security_txt():
    """V506 FIX 7: RFC 9116 security.txt — standard contact channel."""
    body = (
        "Contact: mailto:wes@permitgrab.com\n"
        "Contact: mailto:sales@permitgrab.com\n"
        "Expires: 2027-05-04T00:00:00.000Z\n"
        "Preferred-Languages: en\n"
        "Canonical: https://permitgrab.com/.well-known/security.txt\n"
    )
    return Response(body, mimetype='text/plain')


@seo_bp.route('/llms.txt')
def llms_txt():
    """V506 FIX 6: AI-search visibility signal. llms.txt is the emerging
    standard for sites to advertise themselves to LLM crawlers /
    citation engines (OpenAI search, Perplexity, Claude, Anthropic-ai,
    Google-Extended)."""
    try:
        import os
        path = os.path.join(os.path.dirname(__file__), '..', 'static', 'llms.txt')
        if os.path.exists(path):
            with open(path, 'r') as f:
                return Response(f.read(), mimetype='text/markdown')
    except Exception:
        pass
    # Fallback inline content if static file missing
    body = """# PermitGrab

> Daily building permit data, contractor leads, and code violation feeds for US cities. Built for home-services contractors (roofers, solar, HVAC, plumbers, restoration), real-estate investors and wholesalers, and design-build general contractors. $149/mo unlimited access per customer with 14-day free trial.

## What we are

PermitGrab pulls building permit + property owner + code enforcement data from city, county, and state public records APIs daily. We surface contractor-shaped data points (business names, phones, permit values) and homeowner data points (addresses, mailing addresses for absentee detection, code citations) for $149/mo unlimited access.

## Coverage

17 fully-loaded metros (permits + violations + property owners): Fort Worth, Miami-Dade, Phoenix, Cincinnati, Chicago, Nashville, Cleveland, Austin, Philadelphia, Mesa, Raleigh, Scottsdale, NYC, Columbus, San Antonio, Buffalo, Orlando. ~98 active permit-collection cities, ~947K property owner records.

## Top customer archetypes

1. Storm-belt roofing contractors (TX/FL/OK/CO/GA/TN)
2. Solar installers / EPCs (CA/AZ/TX/NV/FL/NJ)
3. Design-build general contractors (top 25 metros)
4. HVAC contractors (Phoenix/SA/Vegas/Houston/Tampa)
5. Real estate wholesalers (Atlanta/Chicago/Houston/Detroit/Cleveland)
6. Insurance restoration contractors (hurricane belt + tornado alley)
7. Plumbing contractors (Chicago/Philly/NYC)
8. Window/door replacement specialists
9. Real estate agents specializing in off-market listings
10. Pest control + lawn service (high-end)

## Key landing pages

- /pricing — $149/mo, 14-day free trial, no charge for 14 days
- /cities — full directory of covered metros
- /leads/<persona> — persona-specific landing pages (15 archetypes)
- /permits/<state>/<city> — per-city permit + violation feeds
- /blog/<post> — long-form data-driven posts on lead-acquisition strategy
"""
    return Response(body, mimetype='text/markdown')


