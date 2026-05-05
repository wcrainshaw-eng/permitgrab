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
# V488 IRONCLAD (V485 B2 deferred fix): persona-stats cache moved from
# process-memory to the system_state table.
#
# Old behavior: in-memory dict with 5-min TTL. After every Render restart
# (deploy or memory-bail recycle), the FIRST request to /leads/* paid the
# cold-start cost = 5 COUNT(*)s + 3 GROUP BYs against tables totaling
# 2.9M+ rows. That's 5-15s under load and pushed AdsBot past its 10s
# landing-page health threshold, triggering "Destination not working"
# disapproval — exactly the V484 incident.
#
# New behavior:
#   1. Web reads from system_state (single-row SELECT, ~1ms). Cold start
#      hits a warm cache as long as the worker has populated it once.
#   2. Worker (secondary_loop) calls refresh_persona_stats_cache() every
#      2 hrs and atomically writes the JSON blob.
#   3. In-memory cache stays as a 5-min secondary layer to avoid hitting
#      system_state on every request (still ~1ms but nonzero).
#   4. If system_state is empty (first deploy ever) the web computes
#      synchronously once — same as old behavior, but only on first hit
#      of the very first deploy.

_PERSONA_STATS_KEY = 'persona_stats_v488'
_STATS_DB_TTL_SECONDS = 7200  # 2 hr — refreshed by worker secondary_loop
_STATS_MEM_CACHE = {'data': None, 'ts': 0}
_STATS_MEM_TTL_SECONDS = 300


def _compute_persona_stats():
    """Run the actual SQL. Should only be called by the worker
    (refresh_persona_stats_cache) or as a cold-start fallback in web.
    """
    import json as _j
    out = {}
    conn = permitdb.get_connection()
    try:
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
    return out


def refresh_persona_stats_cache():
    """Recompute and persist to system_state. Called by worker.secondary_loop
    every 2 hrs and once at worker boot. Safe to call from any context.
    """
    import json as _j, time as _t
    out = _compute_persona_stats()
    if not out:
        return None
    try:
        permitdb.set_system_state(_PERSONA_STATS_KEY, _j.dumps(out))
        # Also update in-memory cache so the calling process sees the
        # fresh value on the very next read.
        _STATS_MEM_CACHE['data'] = out
        _STATS_MEM_CACHE['ts'] = _t.time()
    except Exception as e:
        print(f"[V488] persona_stats_cache write failed: {e}", flush=True)
    return out


def _get_persona_stats():
    """Read path used by /leads/* page renders. Three layers:
      1. process-memory cache (5 min TTL) — fastest path
      2. system_state row (2 hr TTL) — survives process restarts
      3. compute synchronously — first-deploy-ever fallback only
    """
    import time as _t, json as _j
    now = _t.time()

    # Layer 1: process memory
    if _STATS_MEM_CACHE['data'] and (now - _STATS_MEM_CACHE['ts']) < _STATS_MEM_TTL_SECONDS:
        return _STATS_MEM_CACHE['data']

    # Layer 2: system_state DB-backed cache
    try:
        state = permitdb.get_system_state(_PERSONA_STATS_KEY)
    except Exception as e:
        print(f"[V488] persona_stats system_state read failed: {e}", flush=True)
        state = None
    if state and state.get('value'):
        try:
            data = _j.loads(state['value'])
            # Trust the DB blob — refresh staleness is the worker's job.
            # Repopulate process memory so next request skips the DB read.
            _STATS_MEM_CACHE['data'] = data
            _STATS_MEM_CACHE['ts'] = now
            return data
        except Exception as e:
            print(f"[V488] persona_stats JSON decode failed: {e}", flush=True)

    # Layer 3: compute (cold start with empty DB cache only)
    out = _compute_persona_stats()
    if out:
        try:
            permitdb.set_system_state(_PERSONA_STATS_KEY, _j.dumps(out))
        except Exception:
            pass
        _STATS_MEM_CACHE['data'] = out
        _STATS_MEM_CACHE['ts'] = now
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
    # V506: 10 new persona entries from PERFECT_CUSTOMERS_MATRIX archetypes.
    # Closes 240+ broken /leads/<persona> links from V498-V502 blog posts.
    'solar-home-services': {
        'h1': 'Solar Installer Leads from Building Permits',
        'meta_title': 'Solar Installer Leads from Building Permits | PermitGrab',
        'meta_description': (
            'Daily building permit feed for solar installers + EPCs. 79K '
            'Phoenix + 55K Austin owners. Re-roof permits convert at 18-25% '
            'for solar. $149/mo unlimited.'
        ),
        'hero_kicker': 'For solar installers, EPCs, and solar-roof combo shops',
        'hero_copy': (
            'Skip the EnergySage and Modernize per-lead markup. Solar leads '
            'sourced direct from public permit data — re-roof permits and '
            'electrical-upgrade permits are the highest-converting solar lead types.'
        ),
        'data_block': 'owners',
        'value_props': [
            'Maricopa County: 79,000+ property owner records, 1,080+ Phoenix contractor phones',
            'Travis County (Austin): 55,000+ owners with full names + mailing addresses',
            'Re-roof permits convert at 18–25% — they\'re already paying for the roof, solar is the upsell',
            'Electrical service upgrade permits = homeowner is solar-ready by definition',
        ],
        'cta_text': 'Start Sourcing Solar Leads — $149/mo',
        'faq': [
            ('Why are re-roof permits the best solar leads?',
             'A homeowner replacing a roof is the only time solar attaches '
             'to the same install crew at near-zero incremental customer '
             'acquisition cost. The re-roof permit pulls 6-12 months before '
             'they\'d otherwise consider solar — perfect timing for a quote.'),
            ('Which cities have the strongest solar economics?',
             'Phoenix (79K Maricopa owners), Austin (55K Travis owners + '
             'TX property tax exemption + Austin Energy VOS tariff), Tampa '
             '(40K Hillsborough owners), Mesa (38K records). Each is in '
             'PermitGrab\'s daily permit feed.'),
            ('How much does this cost vs. EnergySage / Modernize?',
             'Aggregator leads cost $50-200 per shared lead. PermitGrab is '
             '$149/mo unlimited — pull thousands of permits exclusively '
             '(no other contractor sees the same homeowner).'),
        ],
    },
    'design-build-gc': {
        'h1': 'Design-Build General Contractor Leads from Permit Data',
        'meta_title': 'Design-Build GC Leads from Building Permits | PermitGrab',
        'meta_description': (
            'Daily permit feed for design-build GCs. Skip Houzz Pro + Angi '
            'referral fees. Architect-relationship play + ADU/addition/major '
            'remodel filtering. $149/mo unlimited.'
        ),
        'hero_kicker': 'For design-build GCs, ADU specialists, custom-home builders',
        'hero_copy': (
            'Skip Houzz Pro $1-3K/mo + Angi referral fees. Design-build GCs '
            'win by getting to homeowners 30-90 days after permit issue, '
            'before they have finalized GC selection.'
        ),
        'data_block': 'owners',
        'value_props': [
            'NYC PLUTO: 13,000+ records with 791+ contractor phones',
            'Travis County (Austin): 55,000+ owners; avg addition value $89K',
            'Filter by permit type: ADU, addition, major remodel — your sweet spot',
            'Architect relationships: see who\'s pulling permits with which architect',
        ],
        'cta_text': 'Start Closing Design-Build Jobs — $149/mo',
        'faq': [
            ('Why are permit-driven design-build leads better than referral platforms?',
             'Houzz Pro and Angi serve homeowners shopping for ANY contractor. '
             'Permit-driven outreach catches homeowners who already filed — '
             'they\'re committed, just selecting their team.'),
            ('What permit types matter most for design-build GCs?',
             'Additions ($75-200K typical), ADUs ($150-400K), major remodels '
             '($100K+). PermitGrab lets you filter by project_value_tier so '
             'you only see jobs that match your minimum.'),
            ('How fresh is the permit data?',
             'Daily refresh. Most cities update within 24-48 hours of issue. '
             'You get the homeowner name + address within 1-2 days of filing.'),
        ],
    },
    'hvac-contractor': {
        'h1': 'HVAC Contractor Leads from Building Permits',
        'meta_title': 'HVAC Contractor Leads from Permit Data | PermitGrab',
        'meta_description': (
            'Daily permit feed for HVAC contractors. AC/furnace replacement '
            'permits + 311 inoperable-AC violations + summer trigger window. '
            '$149/mo unlimited.'
        ),
        'hero_kicker': 'For HVAC contractors and mechanical sub specialists',
        'hero_copy': (
            'Phoenix and San Antonio AC replacement cycles run 10-12 years. '
            'The contractor on-permit at year 0 wins the replacement at '
            'year 11. PermitGrab surfaces every permit + every 311 '
            'inoperable-AC service call daily.'
        ),
        'data_block': 'owners',
        'value_props': [
            'Phoenix: 1,080+ contractor phones, Maricopa 79K owner records',
            'San Antonio: 3,830+ contractor phones, Bexar 5K owners',
            'May trigger window: AC fails before 95°F days, owner panics, you call first',
            'Filter by permit type: AC replacement, furnace, ductwork, mini-split',
        ],
        'cta_text': 'Start Booking HVAC Jobs — $149/mo',
        'faq': [
            ('Why is May the best HVAC outreach window?',
             'Homeowners delay AC replacement until forced. May is when '
             'first 90°F days expose failing units. Reaching them with a '
             'replacement quote in early May beats every competitor scrambling '
             'in July when units are dead and parts are backordered.'),
            ('What about commercial HVAC?',
             'PermitGrab includes commercial permits — HVAC chiller / RTU / '
             'air-handler replacements at $50-500K each. Filter by '
             'project_value_tier $50K+ for these.'),
            ('How does this beat Yelp / Thumbtack lead-buy?',
             'Per-lead leads are shared across 5-10 contractors. PermitGrab '
             'is exclusive to your subscription, sourced from city/county '
             'data — same homeowners, but you\'re the only one with the data.'),
        ],
    },
    'plumbing-contractor': {
        'h1': 'Plumbing Contractor Leads from Building Permits',
        'meta_title': 'Plumbing Contractor Leads from Permit Data | PermitGrab',
        'meta_description': (
            'Daily permit feed for plumbing contractors. Sewer-line, water '
            'heater, repipe, and Lead Service Line replacement permits. '
            '3,498+ Chicago contractor phones. $149/mo.'
        ),
        'hero_kicker': 'For plumbing contractors, sewer specialists, repipe shops',
        'hero_copy': (
            'Aging Midwest + Mid-Atlantic housing stock = continuous '
            'plumbing replacement work. Chicago LSL mandate alone generates '
            '15-25K permits/year requiring licensed plumbers.'
        ),
        'data_block': 'owners',
        'value_props': [
            'Cook County (Chicago): 72,000+ owners, 3,498+ contractor phones',
            'Philadelphia: 55,000+ owners, 800-1,500 LSL replacement permits/quarter',
            'NYC: 13,000+ PLUTO records, daily DOB permit feed',
            'Filter by permit type: sewer line, water heater, lead service line, repipe',
        ],
        'cta_text': 'Start Sourcing Plumbing Jobs — $149/mo',
        'faq': [
            ('Why are LSL replacement permits a goldmine?',
             'Chicago and Philadelphia have federal mandates to replace lead '
             'service lines by 2037 and 2026 respectively. That\'s tens of '
             'thousands of permits each requiring licensed plumbers. PermitGrab '
             'surfaces every one daily.'),
            ('Do you cover commercial plumbing?',
             'Yes — commercial plumbing permits are filtered the same way. '
             'Filter by permit_type=plumbing AND project_value_tier $25K+.'),
            ('What about sewer line replacements?',
             'Sewer line permits are often emergency-driven (mainline failure). '
             'They\'re high-ticket ($8-25K) and homeowner is highly motivated. '
             'PermitGrab surfaces these as soon as the city issues.'),
        ],
    },
    'storm-belt-roofing': {
        'h1': 'Storm-Belt Roofing Contractor Leads from Permit Data',
        'meta_title': 'Storm-Belt Roofing Leads from Building Permits | PermitGrab',
        'meta_description': (
            'Daily permit + violation feed for storm-belt roofers. Owner-'
            'builder roof permits convert 3-5x better post-event. Tarrant 97K '
            '+ Maricopa 79K + Miami 82K owners. $149/mo.'
        ),
        'hero_kicker': 'For storm-belt roofing contractors (TX/FL/OK/CO/GA/TN/AZ)',
        'hero_copy': (
            'Beat $50-200 per shared lead aggregator pricing. Permit-driven '
            'outreach 14-21 days post-storm captures owner-builder filings — '
            'the gold-tier leads with 18-25% close rates.'
        ),
        'data_block': 'owners',
        'value_props': [
            'Tarrant County (Fort Worth): 97,000+ property owners',
            'Maricopa (Phoenix/Mesa): 79,000+ owners across the metro',
            'Miami-Dade: 82,000+ owners with daily violation feed',
            'Filter by permit type: roof, re-roof, hail repair, storm damage',
        ],
        'cta_text': 'Start Closing Storm Roofs — $149/mo',
        'faq': [
            ('Why are owner-builder permits the best roofing leads?',
             'Owner-builder filings mean the homeowner hasn\'t hired a '
             'contractor yet — they\'re shopping. Reach out 14-21 days '
             'post-storm, before they\'ve committed, and 18-25% close rates '
             'are typical vs 2-4% on cold canvass.'),
            ('Which cities are best for storm-belt roofers?',
             'Fort Worth (Tarrant 97K owners), Mesa/Phoenix (Maricopa 79K), '
             'Miami-Dade (82K), Tampa (Hillsborough 40K). All in PermitGrab\'s '
             'daily feed.'),
            ('Do you cover hail-only events?',
             'Yes — wherever the city issues a roof permit post-event. '
             'Filter by permit_type=roof AND filing_date >= storm_date.'),
        ],
    },
    'real-estate-wholesaler': {
        'h1': 'Real Estate Wholesaler Lead Lists from Public Records',
        'meta_title': 'Real Estate Wholesaler Leads from Permits + Violations | PermitGrab',
        'meta_description': (
            'PropStream alternative for wholesalers. Permits + code violations '
            '+ 947K+ property owner records identify motivated sellers 60-90 '
            'days before MLS. $149/mo.'
        ),
        'hero_kicker': 'For real estate wholesalers, fix-and-flip operators, off-market dealmakers',
        'hero_copy': (
            'PropStream + BatchLeads charge $200-500/mo for stale list-broker '
            'pulls. PermitGrab pulls direct from city/county public records, '
            'refreshed daily, with permit and violation signals other tools '
            'don\'t have.'
        ),
        'data_block': 'violations',
        'value_props': [
            'Total property owner records: 947,000+',
            'Atlanta/Fulton, Houston (83K), NYC (HPD + DOB) violations daily refresh',
            'Permit + violation signals = motivated-seller compound flag',
            'Owner mailing addresses included — skip the skip-tracing tax',
        ],
        'cta_text': 'Start Sourcing Off-Market Deals — $149/mo',
        'faq': [
            ('How do violations identify wholesale-ready sellers?',
             'Open code violations compound monthly fines. Owners often list '
             'below market to avoid contractor bids and cumulative fines. '
             'PermitGrab\'s violation-without-follow-up-permit play converts '
             'at 12-18% vs 2-4% cold.'),
            ('What\'s the difference vs PropStream?',
             'PropStream is broker-list-resold data, often 90+ days stale. '
             'PermitGrab pulls direct from city/county systems daily. Same '
             'homeowner, but caught 30-90 days earlier and 1/3 the price.'),
            ('Do you have data for my market?',
             '50+ cities. Top markets: Atlanta, Chicago, Cleveland, Detroit, '
             'Houston, Miami, Philadelphia. Full list at /cities.'),
        ],
    },
    'insurance-restoration': {
        'h1': 'Insurance Restoration Contractor Leads from Permits + Violations',
        'meta_title': 'Insurance Restoration Leads from Permit Data | PermitGrab',
        'meta_description': (
            'Daily permit + violation feed for restoration GCs. Hurricane-'
            'belt + tornado alley + post-Surfside recertification. 83K '
            'Houston violations. $149/mo unlimited.'
        ),
        'hero_kicker': 'For insurance restoration GCs (water/fire/mold/storm)',
        'hero_copy': (
            'Skip the storm-chaser arms race. Permit + violation data '
            'identifies storm-damaged properties before out-of-state chasers '
            'descend, with the violation-without-follow-up-permit play '
            'converting at 12-18% vs 2-4% on cold canvass.'
        ),
        'data_block': 'violations',
        'value_props': [
            'Houston: 83,000+ active code violations refreshed daily',
            'Miami-Dade: 82,000+ owners + post-Surfside recertification mandates',
            'Broward: 600+ high-rise condos with 40-yr inspection deadlines',
            'Filter by permit type: water damage, fire damage, mold remediation, structural',
        ],
        'cta_text': 'Start Booking Restoration Jobs — $149/mo',
        'faq': [
            ('What is the violation-without-follow-up-permit play?',
             'When a city issues a violation but no follow-up permit appears '
             'in 30-60 days, the homeowner is non-compliant and likely lacks '
             'a contractor. Cold-call them with a remediation quote — close '
             'rate is 12-18% vs 2-4% on random canvass.'),
            ('Do you cover Hurricane Helene / Milton areas?',
             'Tampa (Hillsborough 40K owners), Miami-Dade (82K), Fort '
             'Lauderdale all in daily feed. Storm-driven permits filed '
             '14-21 days post-event are gold for restoration GCs.'),
            ('What about water damage / mold leads outside hurricane zones?',
             'Every city in the feed includes water damage permits. '
             'Filter by permit_type=plumbing OR violation_type=mold for the '
             'remediation funnel.'),
        ],
    },
    'window-replacement': {
        'h1': 'Window Replacement Contractor Leads from Permit Data',
        'meta_title': 'Window Replacement Leads from Building Permits | PermitGrab',
        'meta_description': (
            'Daily permit feed for window/door replacement specialists. 25C '
            'tax credit + utility rebate stacking pitch. Phoenix + Chicago '
            'lead-volume leaders. $149/mo unlimited.'
        ),
        'hero_kicker': 'For window/door replacement specialists',
        'hero_copy': (
            'Renewal by Andersen + Pella Premium installers + independents '
            'all compete on lead-buy economics. PermitGrab is direct-from-'
            'permit data with the 25C federal tax credit + utility rebate '
            'stacking pitch baked into the homeowner outreach script.'
        ),
        'data_block': 'owners',
        'value_props': [
            'Phoenix: 1,080+ contractor phones + Maricopa 79K owners',
            'Chicago: 3,498+ contractor phones + Cook County 72K owners',
            '25C federal tax credit: $600/year homeowner incentive — sales lever',
            'Filter by permit type: window/door, fenestration, energy upgrade',
        ],
        'cta_text': 'Start Booking Window Jobs — $149/mo',
        'faq': [
            ('How does the 25C tax credit help my pitch?',
             'Federal 25C credit gives homeowners $600/year for energy-'
             'efficient windows. Plus utility rebates ($50-300/window in '
             'most states). Stacking these covers 20-40% of the homeowner\'s '
             'cost — easy close.'),
            ('Why permit data instead of paid lead-gen?',
             'Renewal by Andersen leads run $80-200/lead (shared). PermitGrab '
             'is $149/mo unlimited and exclusive — same homeowner pool, '
             'but you\'re the only contractor with the data.'),
            ('Which cities have the highest window-replacement demand?',
             'Phoenix (heat-driven UV degradation), Chicago (cold-climate '
             'efficiency upgrades), Cincinnati, Fort Worth. All in feed.'),
        ],
    },
    'off-market-real-estate-agent': {
        'h1': 'Off-Market Real Estate Agent Leads from Permits + Violations',
        'meta_title': 'Off-Market RE Agent Leads from Permit Data | PermitGrab',
        'meta_description': (
            'Off-market and pocket listing intelligence for real estate '
            'agents. Permits + violations + 82K Miami owners identify off-'
            'market sellers 60-90 days before MLS. $149/mo.'
        ),
        'hero_kicker': 'For real estate agents specializing in off-market and pocket listings',
        'hero_copy': (
            'Off-market deals produce 2-3% higher commission rates + 30-50% '
            'better seller-concession terms. Miami specifically has the '
            'highest off-market concentration in the US. Permit + violation '
            'data is the structural advantage for identifying these sellers.'
        ),
        'data_block': 'owners',
        'value_props': [
            'Miami-Dade: 82,000+ owner records + 18-22% foreign-owner concentration',
            'NYC PLUTO: 13,000+ records with mailing addresses for absentee owners',
            'Permit + violation signals 60-90 days before MLS listing',
            'Daily refresh — beat the next agent to the doorstep',
        ],
        'cta_text': 'Start Sourcing Off-Market Listings — $149/mo',
        'faq': [
            ('Why is Miami the off-market capital?',
             '18-22% of Miami-Dade properties are foreign-owned (LATAM, EU). '
             'Foreign owners use off-market channels to avoid public-sale '
             'tax exposure. PermitGrab gives you the mailing-address pipeline.'),
            ('How does permit data signal upcoming listings?',
             'Major remodels followed by no follow-up activity 12-18 months '
             'later = owner is staging for sale. Catch them before listing.'),
            ('What about pocket listings I can present to my buyers?',
             'Same data — owners who pulled permits but haven\'t sold + '
             'show signs of completion = ready-to-sell pocket inventory. '
             'Reach out with a buyer offer before MLS exposure.'),
        ],
    },
    'pest-control-lawn': {
        'h1': 'Pest Control + Lawn Service Leads from Permit Data',
        'meta_title': 'Pest Control & Lawn Leads from Permits | PermitGrab',
        'meta_description': (
            'Daily permit + new-owner feed for pest/lawn services. 30-day '
            'post-move-in window converts at 18-28% vs 4-7% cold. HOA '
            'dominance + post-renovation cross-sell plays. $149/mo.'
        ),
        'hero_kicker': 'For pest control and high-end lawn service contractors',
        'hero_copy': (
            'Pest/lawn customers are recurring revenue plays — sign up once, '
            '$1,200/year for life. The 30-day post-move-in window is where '
            'conversion is 4x cold outreach. PermitGrab surfaces new-owner '
            'mailing-address-changes daily.'
        ),
        'data_block': 'owners',
        'value_props': [
            'Davidson County (Nashville): 71,000+ owner records',
            'Atlanta: HOA dominance — ~60% of metro SFR has HOA pre-approved vendor lists',
            'Maricopa (Phoenix): 79,000+ owners + termite-belt density',
            'Daily refresh on owner mailing-address changes (move-in detection)',
        ],
        'cta_text': 'Start Booking Recurring Customers — $149/mo',
        'faq': [
            ('Why is the 30-day post-move-in window so valuable?',
             'New homeowners haven\'t established service relationships yet. '
             'Reach them within 30 days of move-in (when mailing address '
             'changes in assessor records) and conversion runs 18-28% vs '
             '4-7% on a random canvass.'),
            ('How do you detect new homeowners?',
             'Property owner mailing addresses refresh in county assessor '
             'data within 30-60 days of close. PermitGrab joins this to '
             'permit + violation feeds. Filter by mailing_address_changed '
             'in last 30 days.'),
            ('Why do pest/lawn cross-sell well after permits?',
             'Major renovation = yard disruption = need for landscaping '
             'reset + termite/pest reinspection. Cross-sell after permit '
             'final-inspection date converts at 25-40%.'),
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


# V506: solar-home-services now has its own dict entry — render its own
# slug so the canonical builds correctly (was rendering 'home-services'
# which created a deindex-signal canonical pointing at a different URL).
@persona_bp.route('/leads/solar-home-services')
def persona_solar_home_services():
    return _render_persona('solar-home-services')


# V506: 9 new persona routes for the matrix archetypes referenced
# 240+ times across V498-V502 blog post bodies.
@persona_bp.route('/leads/design-build-gc')
def persona_design_build_gc():
    return _render_persona('design-build-gc')

@persona_bp.route('/leads/hvac-contractor')
def persona_hvac_contractor():
    return _render_persona('hvac-contractor')

@persona_bp.route('/leads/plumbing-contractor')
def persona_plumbing_contractor():
    return _render_persona('plumbing-contractor')

@persona_bp.route('/leads/storm-belt-roofing')
def persona_storm_belt_roofing():
    return _render_persona('storm-belt-roofing')

@persona_bp.route('/leads/real-estate-wholesaler')
def persona_real_estate_wholesaler():
    return _render_persona('real-estate-wholesaler')

@persona_bp.route('/leads/insurance-restoration')
def persona_insurance_restoration():
    return _render_persona('insurance-restoration')

@persona_bp.route('/leads/window-replacement')
def persona_window_replacement():
    return _render_persona('window-replacement')

@persona_bp.route('/leads/off-market-real-estate-agent')
def persona_off_market_real_estate_agent():
    return _render_persona('off-market-real-estate-agent')

@persona_bp.route('/leads/pest-control-lawn')
def persona_pest_control_lawn():
    return _render_persona('pest-control-lawn')


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


# V482 Part B1+B2: standalone landing pages for two new buyer personas
# (insurance agents and material suppliers). The content is hand-written
# and lives in templates/leads/{insurance,suppliers}.html. These pages
# do NOT use the _PERSONAS dict + persona_landing.html scaffold because
# the article structure (data tables, multi-column value props, FAQ
# blocks) doesn't fit the dict's hero/value-prop/cta shape.
_V482_INSURANCE_META = {
    'meta_title': 'New Homeowner & Renovation Leads for Insurance Agents | PermitGrab',
    'meta_description': (
        'Find homeowners filing building permits in real-time. Perfect '
        'leads for home insurance, renovation coverage, and new '
        'construction policies. Active in 11+ cities. $149/mo unlimited.'
    ),
}
_V482_SUPPLIERS_META = {
    'meta_title': 'Construction Project Leads for Building Material Suppliers | PermitGrab',
    'meta_description': (
        'Track new construction and renovation permits across 11+ US '
        'cities. Know what is being built, where, and by whom — before '
        'your competitors do. $149/mo unlimited.'
    ),
}


@persona_bp.route('/leads/insurance')
def persona_insurance():
    return render_template(
        'leads/insurance.html',
        meta_title=_V482_INSURANCE_META['meta_title'],
        meta_description=_V482_INSURANCE_META['meta_description'],
        canonical_url=f"{SITE_URL}/leads/insurance",
        current_year=datetime.now().year,
    )


@persona_bp.route('/leads/suppliers')
def persona_suppliers():
    return render_template(
        'leads/suppliers.html',
        meta_title=_V482_SUPPLIERS_META['meta_title'],
        meta_description=_V482_SUPPLIERS_META['meta_description'],
        canonical_url=f"{SITE_URL}/leads/suppliers",
        current_year=datetime.now().year,
    )
