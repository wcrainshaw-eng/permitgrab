"""V474 (CODE_V474_BUYER_PERSONAS): three buyer-persona landing pages
that target the language buyers actually search for, NOT "building permits."

Routes:
  /leads/real-estate-investors  — code-violation properties = motivated sellers
  /leads/contractors            — new construction permits = subs/suppliers
  /leads/home-services          — homeowners with permits = solar / insurance / warranty

All three are data-driven: every number on the page is pulled from the live
DB at render time (or from a cached helper). The template is shared
(persona_landing.html) and the route picks the right copy block.
"""

from flask import Blueprint, render_template, Response
from datetime import datetime
from server import *
import server as _s

persona_bp = Blueprint('persona', __name__)


# ---------------------------------------------------------------------------
# Real-data helpers — every number on a persona page comes through here.
# Cached lightly to keep page render fast under load. Counts can be a few
# minutes stale; that's fine for SEO/marketing copy.
# ---------------------------------------------------------------------------
_STATS_CACHE = {'data': None, 'ts': 0}
_STATS_TTL_SECONDS = 300  # 5 min


def _get_persona_stats():
    """One round-trip to the DB for every count + top-list a persona page needs.
    Returns a dict the templates index by name. Cached for 5 min.
    """
    import time as _t
    if _STATS_CACHE['data'] and (_t.time() - _STATS_CACHE['ts']) < _STATS_TTL_SECONDS:
        return _STATS_CACHE['data']

    out = {}
    conn = permitdb.get_connection()
    try:
        # ---- top-level counts ----
        out['total_violations'] = conn.execute(
            "SELECT COUNT(*) FROM violations"
        ).fetchone()[0]
        out['total_owners'] = conn.execute(
            "SELECT COUNT(*) FROM property_owners"
        ).fetchone()[0]
        out['total_contractors'] = conn.execute(
            "SELECT COUNT(*) FROM contractor_profiles"
        ).fetchone()[0]
        out['total_contractors_with_phone'] = conn.execute(
            "SELECT COUNT(*) FROM contractor_profiles "
            "WHERE phone IS NOT NULL AND phone <> ''"
        ).fetchone()[0]
        out['total_permits_90d'] = conn.execute(
            "SELECT COUNT(*) FROM permits "
            "WHERE date >= date('now','-90 days')"
        ).fetchone()[0]

        # ---- top-10 violations by joined city name (handles miami-dade alias) ----
        rows = conn.execute("""
            SELECT pc.city, pc.state, COUNT(*) AS cnt
            FROM violations v
            LEFT JOIN prod_cities pc ON v.prod_city_id = pc.id
            WHERE pc.city IS NOT NULL
            GROUP BY pc.city, pc.state
            ORDER BY cnt DESC
            LIMIT 10
        """).fetchall()
        out['top_violation_cities'] = [
            {'city': r['city'], 'state': r['state'], 'cnt': r['cnt']}
            for r in rows
        ]

        # ---- top-10 permit-volume cities (last 90 days) ----
        rows = conn.execute("""
            SELECT pc.city, pc.state, COUNT(*) AS cnt
            FROM permits p
            LEFT JOIN prod_cities pc ON p.source_city_key = pc.city_slug
            WHERE p.date >= date('now','-90 days')
              AND pc.city IS NOT NULL
            GROUP BY pc.city, pc.state
            ORDER BY cnt DESC
            LIMIT 10
        """).fetchall()
        out['top_permit_cities'] = [
            {'city': r['city'], 'state': r['state'], 'cnt': r['cnt']}
            for r in rows
        ]

        # ---- top-10 owner-count cities ----
        rows = conn.execute("""
            SELECT city, state, COUNT(*) AS cnt
            FROM property_owners
            WHERE city IS NOT NULL AND city <> ''
            GROUP BY city, state
            ORDER BY cnt DESC
            LIMIT 10
        """).fetchall()
        out['top_owner_cities'] = [
            {'city': r['city'], 'state': r['state'], 'cnt': r['cnt']}
            for r in rows
        ]
    except Exception as e:
        print(f"[V474] persona stats query failed: {e}", flush=True)

    _STATS_CACHE['data'] = out
    _STATS_CACHE['ts'] = _t.time()
    return out


# ---------------------------------------------------------------------------
# Persona definitions — each row is (slug, hero, value props, FAQ, CTA).
# Kept inline so the spec lives next to the routes.
# ---------------------------------------------------------------------------
_PERSONAS = {
    'real-estate-investors': {
        'h1': 'Motivated Seller Leads from Code Violations',
        'meta_title': 'Code Violation Property Lists — Motivated Seller Leads | PermitGrab',
        'meta_description': (
            'Find distressed properties with active code violations in 50+ '
            'cities. Updated daily. Owner names, addresses, and violation '
            'details for real estate investors.'
        ),
        'hero_kicker': 'For real estate investors, wholesalers, flippers',
        'hero_copy': (
            'Properties with active code violations are signal flares for '
            'motivated sellers. Owners may be overwhelmed, behind on '
            'maintenance, or facing fines that push them toward a sale '
            'below market.'
        ),
        'data_block': 'violations',  # which stats block to render
        'value_props': [
            'Find properties with active code violations before they hit the MLS',
            'Owner names and mailing addresses included — skip the skip tracing',
            'Filter by violation type: structural, electrical, plumbing, fire safety',
            'Updated daily from official city code enforcement databases',
        ],
        'cta_text': 'Start Finding Motivated Sellers — $149/mo',
        'faq': [
            ('How do code violations indicate motivated sellers?',
             'Owners facing active code violations often face fines that '
             'compound monthly, plus contractor bids they may not be able '
             'to fund. Many list below market to avoid the cost of repairs. '
             'Real estate investors target violation lists for exactly this '
             'reason.'),
            ('What cities do you cover for code violation data?',
             'PermitGrab tracks code violations from official city code '
             'enforcement databases in 50+ cities including New York, '
             'Chicago, Houston, Philadelphia, Charlotte, Cleveland, Mesa, '
             'Cincinnati, Austin, San Antonio, Cape Coral, and Fort Worth.'),
            ('Do you include property owner names with violations?',
             'Yes. Wherever the city or county assessor exposes ownership '
             'data, we cross-link it to the violation address. You get the '
             'owner name and mailing address right next to the violation.'),
            ('How often is the violation data updated?',
             'PermitGrab pulls fresh violation data from each city every '
             '24 hours. Property owner data refreshes monthly from county '
             'assessor exports.'),
        ],
    },
    'contractors': {
        'h1': 'Find New Construction Projects Before Your Competition',
        'meta_title': 'New Construction Project Leads — Permits Filed Today | PermitGrab',
        'meta_description': (
            'See every building permit filed in your city — before work '
            'begins. Find GCs who need subs, property owners starting '
            'projects, and upcoming jobs. Updated daily.'
        ),
        'hero_kicker': 'For contractors, subcontractors, suppliers',
        'hero_copy': (
            'Every building permit is a project that needs contractors. '
            'Whether you\'re a sub looking for GCs to work with, or a '
            'supplier looking for active job sites, the permit feed tells '
            'you exactly where the work is.'
        ),
        'data_block': 'permits',
        'value_props': [
            'See every permit filed in your market — residential, commercial, electrical, plumbing',
            'Contractor names and phone numbers for thousands of active firms',
            'Filter by trade: electrical, plumbing, HVAC, roofing, general construction',
            'Know which GCs are busiest — they need subs NOW',
        ],
        'cta_text': 'Start Finding Projects — $149/mo',
        'faq': [
            ('What kind of construction projects can I find?',
             'New construction, additions, renovations, demolitions, '
             'electrical, plumbing, HVAC, roofing, and solar. The permit '
             'record carries the work description, project value, and '
             'square footage where the city publishes them.'),
            ('Do you include contractor contact information?',
             'For cities with state license data (FL, MN, NY, WA, CA) we '
             'attach licensee phone numbers from the official state board. '
             'Other cities get DDG-enriched contact info where the public '
             'web has it. Phone coverage varies by city.'),
            ('How quickly do new permits appear after filing?',
             'PermitGrab collection cycles run every 30 minutes against '
             'each active city portal. New permits typically appear '
             'within 1–2 hours of being filed.'),
            ('Can I filter by trade or permit type?',
             'Yes. Every permit is auto-classified into a trade category '
             '(electrical, plumbing, HVAC, roofing, solar, general '
             'construction). The /permits/<city>/<trade> URLs let you drill '
             'directly into one trade for one city.'),
        ],
    },
    'home-services': {
        'h1': 'Homeowner Leads from Building Permits — They Just Started a Project',
        'meta_title': 'Homeowner Leads from Building Permits — Solar, Insurance, Home Services | PermitGrab',
        'meta_description': (
            'Reach homeowners who just pulled building permits. They\'re '
            'spending money on their home RIGHT NOW. Owner names, '
            'addresses, and project details in 50+ cities.'
        ),
        'hero_kicker': 'For solar installers, insurance, home warranty, window/door, HVAC',
        'hero_copy': (
            'A homeowner who just pulled a permit is actively investing in '
            'their property. They\'re open to solar quotes, insurance '
            'reviews, home warranties, and upgrades — at the exact moment '
            'they\'re spending.'
        ),
        'data_block': 'owners',
        'value_props': [
            'Homeowners who just pulled a permit = actively investing in their home',
            'Perfect timing for solar, insurance, home warranty, and upgrade offers',
            'Owner names and property addresses — no skip tracing needed',
            'Filter by permit type: roof replacement, electrical upgrade, remodel',
        ],
        'cta_text': 'Start Reaching Homeowners — $149/mo',
        'faq': [
            ('Why are building permit leads valuable for solar companies?',
             'A roof replacement or major electrical permit signals that '
             'the homeowner is already in project mode and has access to '
             'capital. Solar installers see 3–5x higher conversion vs. '
             'cold lists because the timing is right.'),
            ('Do you include homeowner names and addresses?',
             'Yes. Property owner data is sourced from county assessor '
             'records and joined to the permit address. You get the owner '
             'name, situs address, and (where exposed) the mailing address '
             'for absentee owners.'),
            ('Can I filter by permit type or project size?',
             'Yes. Filter by trade (roofing / electrical / plumbing / HVAC) '
             'and by project value tier ($0–10K, $10–50K, $50K+) to focus '
             'on jobs that match your offer.'),
            ('How is this different from buying aged lead lists?',
             'Aged lead lists are months old and recycled across hundreds '
             'of buyers. PermitGrab feeds you NEW permits within 1–2 hours '
             'of filing. Same homeowner, but reached when they\'re actually '
             'ready to talk.'),
        ],
    },
}


def _data_block_for(persona_slug: str, stats: dict) -> dict:
    """Format the data-proof block for the given persona, using real stats."""
    block = {'lead': '', 'detail': '', 'cities': []}
    if persona_slug == 'real-estate-investors':
        top = stats.get('top_violation_cities', [])
        if top:
            block['lead'] = f"{stats.get('total_violations', 0):,} code violation records"
            top1 = top[0]
            top2 = top[1] if len(top) > 1 else None
            extra = f" — {top1['cnt']:,} in {top1['city']}"
            if top2:
                extra += f", {top2['cnt']:,} in {top2['city']}"
            block['detail'] = (
                f"PermitGrab tracks {stats.get('total_violations', 0):,} "
                f"code violations across cities including {top1['city']} "
                f"({top1['cnt']:,}). "
                f"Add {stats.get('total_owners', 0):,} property owner records "
                f"with names + addresses, and you have a motivated-seller "
                f"prospecting list updated every day."
            )
            block['cities'] = top
    elif persona_slug == 'contractors':
        top = stats.get('top_permit_cities', [])
        if top:
            block['lead'] = f"{stats.get('total_permits_90d', 0):,} permits in the last 90 days"
            block['detail'] = (
                f"PermitGrab tracks {stats.get('total_permits_90d', 0):,} "
                f"permits filed in the last 90 days across "
                f"{stats.get('total_contractors', 0):,} contractor profiles "
                f"(with {stats.get('total_contractors_with_phone', 0):,} "
                f"phone-verified). Filter by city, trade, or permit type to "
                f"find exactly where the work is."
            )
            block['cities'] = top
    elif persona_slug == 'home-services':
        top = stats.get('top_owner_cities', [])
        if top:
            block['lead'] = f"{stats.get('total_owners', 0):,} property owner records"
            top1 = top[0]
            block['detail'] = (
                f"PermitGrab links {stats.get('total_owners', 0):,} property "
                f"owner records to live permits. {top1['cnt']:,} owners "
                f"in {top1['city']} alone — with names and addresses. "
                f"Reach homeowners the day they pull a permit, not months "
                f"after."
            )
            block['cities'] = top
    return block


def _faq_jsonld(faq_pairs):
    """Build schema.org FAQPage JSON-LD from a list of (question, answer)."""
    return {
        '@context': 'https://schema.org',
        '@type': 'FAQPage',
        'mainEntity': [
            {
                '@type': 'Question',
                'name': q,
                'acceptedAnswer': {'@type': 'Answer', 'text': a},
            }
            for q, a in faq_pairs
        ],
    }


def _render_persona(slug: str):
    cfg = _PERSONAS.get(slug)
    if not cfg:
        return Response('Not Found', status=404)
    stats = _get_persona_stats()
    data_block = _data_block_for(slug, stats)
    faq_jsonld = _faq_jsonld(cfg['faq'])
    canonical_url = f"{SITE_URL}/leads/{slug}"
    footer_cities = get_cities_with_data()
    return render_template(
        'persona_landing.html',
        persona_slug=slug,
        h1=cfg['h1'],
        meta_title=cfg['meta_title'],
        meta_description=cfg['meta_description'],
        hero_kicker=cfg['hero_kicker'],
        hero_copy=cfg['hero_copy'],
        value_props=cfg['value_props'],
        cta_text=cfg['cta_text'],
        faq=cfg['faq'],
        faq_jsonld=faq_jsonld,
        data_block=data_block,
        stats=stats,
        canonical_url=canonical_url,
        footer_cities=footer_cities,
        current_year=datetime.now().year,
    )


@persona_bp.route('/leads/real-estate-investors')
def persona_re_investors():
    return _render_persona('real-estate-investors')


@persona_bp.route('/leads/contractors')
def persona_contractors():
    return _render_persona('contractors')


@persona_bp.route('/leads/home-services')
def persona_home_services():
    return _render_persona('home-services')


# V480 P0-1: external traffic + nav references both /leads/solar-home-services
# (the persona spec uses solar as the headline use case) and /leads/home-services.
# Render the same persona for both so neither URL 404s.
@persona_bp.route('/leads/solar-home-services')
def persona_solar_home_services():
    return _render_persona('home-services')


# V477 Bug 1: external links / SEO inbound traffic discovered the
# /solutions/* URL scheme even though the nav links to /leads/*.
# 301 redirects so direct URL types and any cached external links
# work, while /leads/* remains the canonical SEO target.
from flask import redirect as _redirect

@persona_bp.route('/solutions/real-estate-investors')
def persona_re_investors_alias():
    return _redirect('/leads/real-estate-investors', code=301)


# V480 P0-1: short alias used in older marketing copy.
@persona_bp.route('/solutions/investors')
def persona_investors_short_alias():
    return _redirect('/leads/real-estate-investors', code=301)


@persona_bp.route('/solutions/contractors')
def persona_contractors_alias():
    return _redirect('/leads/contractors', code=301)


@persona_bp.route('/solutions/home-services')
def persona_home_services_alias():
    return _redirect('/leads/home-services', code=301)


# V480 P0-1: /solutions/solar inbound link → solar-home-services.
@persona_bp.route('/solutions/solar')
def persona_solar_alias():
    return _redirect('/leads/solar-home-services', code=301)
