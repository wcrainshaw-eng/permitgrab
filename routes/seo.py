"""V471 PR2 (CODE_V471 Part 1B): seo blueprint extracted from server.py.

Routes: 10 URLs across 10 handlers.

Helpers/globals from server.py are accessed via `from server import *`,
which imports everything server.py defined before this blueprint was loaded
(blueprints are registered at the bottom of server.py, after all globals
are set). Underscored helpers are listed below explicitly because `import *`
skips names starting with `_`.
"""
from flask import Blueprint, request, jsonify, render_template, session, redirect, abort, Response, g, url_for, send_from_directory
from datetime import datetime, timedelta
import os, json, time, re, threading, random, string, hashlib, hmac
from werkzeug.security import generate_password_hash, check_password_hash

# Pull in server.py's helpers, models, and globals (server is fully loaded
# by the time this blueprint module is imported because server.py registers
# blueprints at the very end of its module body).
from server import *
# Underscored helpers / module-level state that `import *` skips:
from server import _generate_market_reports, _generate_sitemap_xml, _get_city_lastmod_map, _v467_render_seo_blog_post, _v469_render_faq_blog_post
import server as _s

seo_bp = Blueprint('seo', __name__)


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
    categories = {
        'market-reports': {
            'title': 'City Market Reports (Live Data)',
            'posts': market_reports,
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
    """
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

    # City pages from discovered cities (old format: /permits/{city})
    all_discovered_cities = discover_cities_from_permits()
    for slug, city_info in all_discovered_cities.items():
        if slug in state_slugs:
            continue
        if city_info['name'] not in cities_with_permits:
            continue

        lastmod = city_lastmod.get(slug, today)
        loc = f"{SITE_URL}/permits/{slug}"
        if loc not in url_map:
            url_map[loc] = {
                'loc': loc,
                'changefreq': 'daily',
                'priority': '0.8',
                'lastmod': lastmod
            }

    # V58: Also include ALL active CITY_REGISTRY cities for SEO (even without data yet)
    for key, config in CITY_REGISTRY.items():
        if not config.get('active'):
            continue
        slug = config.get('slug', key.replace('_', '-'))
        loc = f"{SITE_URL}/permits/{slug}"
        if loc not in url_map and slug not in state_slugs:
            url_map[loc] = {
                'loc': loc,
                'changefreq': 'weekly',
                'priority': '0.6',
                'lastmod': today
            }

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
    """V233 P1-3: dedicated state-URL sitemap. State routes (both the
    state hub /permits/{state} and the /permits/{state}/{city} SEO URLs)
    were already emitted inside sitemap-cities.xml, but Cowork's audit
    showed Google wasn't surfacing them — the cities sitemap is enormous
    and state URLs got lost in the mix. Breaking them out into a
    dedicated sub-sitemap gives the state pattern its own crawl budget.
    """
    today = datetime.now().strftime('%Y-%m-%d')
    url_map = {}
    abbrev_to_state_slug = {v['abbrev']: k for k, v in STATE_CONFIG.items()}

    # State hub pages
    for state_slug in STATE_CONFIG.keys():
        loc = f"{SITE_URL}/permits/{state_slug}"
        url_map[loc] = {
            'loc': loc,
            'changefreq': 'daily',
            'priority': '0.85',
            'lastmod': today,
        }

    # /permits/{state}/{city} URLs for active cities with data
    try:
        conn = permitdb.get_connection()
        active_cities = conn.execute("""
            SELECT city_slug, state, last_collection
            FROM prod_cities
            WHERE status = 'active'
              AND data_freshness != 'no_data'
              AND total_permits > 0
        """).fetchall()
        for row in active_cities:
            state_slug = abbrev_to_state_slug.get(row['state'])
            if not state_slug:
                continue
            lastmod = row['last_collection'][:10] if row['last_collection'] else today
            loc = f"{SITE_URL}/permits/{state_slug}/{row['city_slug']}"
            if loc not in url_map:
                url_map[loc] = {
                    'loc': loc,
                    'changefreq': 'daily',
                    'priority': '0.7',
                    'lastmod': lastmod,
                }
    except Exception as e:
        print(f"[sitemap_states] state/city URL error: {e}")

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
# V383 (loop /CODE_V286 grind): disallow transient checkout-flow URLs.
# /start-checkout 303-redirects to a Stripe URL or back to /pricing —
# nothing for Google to index, and indexing the redirect dilutes
# crawl budget that should go to city pages. /success is the
# post-payment confirmation already noindex'd via meta tag, but
# robots disallow makes the rule canonical.
Disallow: /start-checkout
Disallow: /success
Disallow: /pricing?

# Crawl-delay for polite crawling
Crawl-delay: 1

Sitemap: {SITE_URL}/sitemap.xml
"""
    return Response(content, mimetype='text/plain')


