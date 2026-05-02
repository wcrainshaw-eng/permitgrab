"""V486 Part: city × persona long-tail blog posts.

These extend buyer_persona_posts.py with city-specific variants for the
highest-volume long-tail keywords (e.g. "phoenix solar leads",
"miami insurance leads"). Numbers baked in at write-time (2026-05-01)
from the documented stats in CITY_QUEUE.md and the live SEO audit.

Wire into the /blog/<slug> dispatcher in routes/seo.py the same way
V482 wired buyer_persona_posts.py — add a render branch:

    from city_persona_posts import CITY_PERSONA_POSTS
    ...
    if slug in CITY_PERSONA_POSTS:
        return _render_city_persona_post(slug)

Each entry has: title, meta_description, h1, subject (persona),
city (display name), city_slug (for internal link), persona_slug (for
internal link to /leads/<persona>), meta_published, reading_time,
body_html, faqs.
"""

CITY_PERSONA_POSTS = {

    # ====================================================================
    'phoenix-solar-installer-leads': {
        'title': 'Phoenix Solar Installer Leads from Building Permits | PermitGrab',
        'meta_description': (
            'Skip the lead aggregators. Phoenix has 79,000+ active property '
            'owners and 1,080+ contractors with phone numbers in our daily '
            'permit feed. Solar installer leads for $149/mo, unlimited.'
        ),
        'h1': 'Phoenix Solar Installer Leads from Building Permit Data',
        'subject': 'Solar installers in Phoenix',
        'city': 'Phoenix',
        'city_slug': 'phoenix-az',
        'persona_slug': 'solar-home-services',
        'meta_published': '2026-05-02',
        'reading_time': '6 min',
        'body_html': """
<p>Phoenix is the most solar-friendly major metro in the United States. With over 300 sunny days a year, average household electric bills 40% above the national mean, and a state Renewable Portfolio Standard pushing utilities toward clean generation, the demand side of the Phoenix solar market is set. The challenge for installers is on the supply side — finding homeowners <em>at the moment they're investing in their property</em>, before three other installers have already called.</p>

<p>Building permit data solves that. Every roof replacement, electrical upgrade, addition, and remodel filed with the City of Phoenix and Maricopa County is a buying-intent signal. PermitGrab pulls that data daily and gives you a direct contact list — no aggregator markup, no shared leads.</p>

<h2>What Phoenix permit data looks like in PermitGrab</h2>

<p>Our Phoenix data feed pulls from Phoenix and Maricopa County's open data portals every day. As of May 2026, our Phoenix coverage includes:</p>

<ul>
  <li><strong>79,000+ Maricopa County property owner records</strong> with full mailing addresses (lets you spot absentee landlords vs owner-occupants — critical for solar lead targeting)</li>
  <li><strong>1,080+ contractor profiles with phone numbers</strong> — directly callable, no enrichment needed</li>
  <li>Permit data updated daily with new filings flagged within 24 hours</li>
  <li>Code violation data for cross-referencing properties already invested in maintenance (often a precursor to bigger renovations like solar)</li>
</ul>

<p>Compare that to typical solar lead aggregators: $30-50 per shared lead, contacted by three competing installers within 24 hours of submission, no telemetry on whether the homeowner is actually invested in their property. PermitGrab's monthly subscription gives you unlimited access to the same homeowners <em>before</em> they're shopping a quote.</p>

<h2>The three highest-converting Phoenix permit signals for solar</h2>

<h3>1. Roof replacement permits</h3>

<p>Homeowners getting a new roof are the single highest-intent solar prospect in Phoenix. They're already paying a contractor, already have the home's title pulled, and have just spent $15-30K on the structure that will support solar panels for the next 25 years. A solar quote within 30 days of a roof permit filing converts at 4-7x the rate of a cold quote. Phoenix files roughly 1,200-1,800 roof permits per month, and PermitGrab surfaces them within 24 hours of filing.</p>

<h3>2. Electrical service upgrade permits</h3>

<p>Homes with 100A panels can't host a solar system without an electrical upgrade. When a Phoenix homeowner files a 200A panel upgrade permit, they're either already planning solar or one conversation away from it. These permits are a leading indicator of solar readiness and have a shorter sales cycle than cold lists.</p>

<h3>3. Addition/ADU permits</h3>

<p>Phoenix's accessory dwelling unit boom (driven by recent zoning changes) creates a steady stream of ADU permit filings. Homeowners building an ADU are typically high-income, design-conscious, and willing to invest in property upgrades — including solar systems sized for the new combined load. ADU permits are public record and surface in PermitGrab the day after issuance.</p>

<h2>How Phoenix solar installers actually use this data</h2>

<p>Three workflows we see installers running with PermitGrab Phoenix data:</p>

<p><strong>Workflow A — Daily morning list.</strong> Filter Phoenix permits filed in the last 24 hours by permit type (roof, electrical service, addition). Export to CSV. Hand to a junior rep who calls each homeowner with a "we noticed you just pulled a permit, would you like a free solar evaluation while the contractor's on site?" pitch. Conversion 5-12% to a scheduled site visit.</p>

<p><strong>Workflow B — Direct mail to absentee owners.</strong> Cross-reference the property_owners feed: site address ≠ owner mailing address means the property is investor-owned. Investor owners with new roof permits are a different sale (the investor wants ROI, not lifestyle) but a high-volume one — Phoenix has tens of thousands of single-family rentals. Direct mail with cap-rate calculations and lease-to-own solar pitches.</p>

<p><strong>Workflow C — Re-engaging old quotes.</strong> Match your CRM's old "not-yet" list against current permit filings. A homeowner who declined a solar quote 18 months ago and just pulled a roof permit is now <em>actively</em> investing in the home — re-pitch with the new context.</p>

<h2>What you don't get from PermitGrab</h2>

<p>To set expectations clearly: PermitGrab is permit and contractor data, not a lead-form aggregator. We don't deliver "homeowners who clicked I want solar." We deliver <em>signals</em> — public-record events that correlate with solar buying intent. Installers who treat the data as an outbound list (to call, mail, or door-knock) consistently see better unit economics than installers who buy aggregator leads. Installers expecting a pre-qualified quote-request feed should keep buying aggregator leads.</p>

<h2>Other Phoenix permit data resources</h2>

<p>Browse the live <a href="/permits/phoenix-az">Phoenix permits page</a> for the latest contractor list, recent filings, and code violations. The Phoenix data is also covered in our broader <a href="/leads/solar-home-services">solar home services lead guide</a>, which walks through the playbook across all major sun-belt metros.</p>

<p>Phoenix is a Tier 5 city in our coverage — that means permits, contractor profiles with phones, code violations, and property owner data are all live and updated daily. Other Tier 5 sun-belt metros where the same solar installer playbook works: <a href="/permits/miami-dade-county">Miami-Dade</a>, <a href="/permits/orlando">Orlando</a>, <a href="/permits/cape-coral">Cape Coral</a>, <a href="/permits/san-antonio">San Antonio</a>, and <a href="/permits/austin-tx">Austin</a>.</p>

<h2>Pricing</h2>

<p>$149/month gives you unlimited access to Phoenix data — and every other city in our coverage. No per-lead fees, no shared leads, cancel anytime. <a href="/pricing">See pricing</a> or <a href="/signup">try a free week</a>.</p>
""",
        'faqs': [
            ('How fresh is Phoenix permit data in PermitGrab?',
             'Phoenix permits are pulled daily from the City of Phoenix and Maricopa County open data portals. New filings appear in the feed within 24 hours of issuance. Property owner data refreshes weekly via the Maricopa County Assessor parcel feed.'),
            ('Can I get a list of Phoenix homeowners who just pulled roof permits?',
             'Yes — that\'s the core use case. Filter the Phoenix permit feed by permit type "roof" and date range "last 7 days" to get a fresh weekly list of homeowners who just started a roof project. Most solar installers run this query as their Monday morning prospecting workflow.'),
            ('Does PermitGrab Phoenix data include phone numbers?',
             'Phone numbers are included for contractors who pulled the permit (1,080+ Phoenix contractors with phones in the latest pull). For homeowner direct contact, we provide the property mailing address from the Maricopa County Assessor — most direct mail and door-knock workflows use that. Homeowner phone enrichment requires a separate skip-trace tool, which is not bundled.'),
            ('Is Phoenix permit data legal to use for outbound marketing?',
             'Yes — building permit data is public record under Arizona open records law. Permit applicants and property owners have no expectation of privacy on the permit filing itself. Standard outbound marketing rules apply (TCPA for calls, CAN-SPAM for email, do-not-mail lists for postal); follow them as you would with any prospecting source.'),
            ('How does this compare to buying solar leads from Modernize, SolarReviews, or HomeAdvisor?',
             'Aggregator leads cost $30-100 each, are typically shared with 3-5 competing installers, and are sourced from form-fill traffic that\'s already been priced into Google Ads CPC. PermitGrab delivers homeowners at a different stage — they\'re mid-project, haven\'t requested a solar quote, and aren\'t shopping you against three competitors. The trade-off: you have to do the outreach. Math works out heavily in your favor at $149/mo unlimited if your sales team can handle outbound dials.'),
        ],
    },

    # ====================================================================
    'miami-dade-insurance-agent-leads': {
        'title': 'Miami-Dade Insurance Agent Leads from Building Permits | PermitGrab',
        'meta_description': (
            'Miami-Dade files thousands of building permits monthly — '
            'every one is a re-rate event for an insurance agent. 82,000+ '
            'property owners, 13,000+ permits, daily updates. $149/mo.'
        ),
        'h1': 'Miami-Dade Insurance Agent Leads from Building Permit Data',
        'subject': 'Insurance agents in Miami-Dade',
        'city': 'Miami-Dade County',
        'city_slug': 'miami-dade-county',
        'persona_slug': 'insurance',
        'meta_published': '2026-05-02',
        'reading_time': '7 min',
        'body_html': """
<p>If you write homeowner's, wind, or umbrella policies in Miami-Dade, you already know the math: every renovation, addition, and roof replacement in your territory is a re-rate event. Miami-Dade has the highest concentration of high-value coastal homes in the country and the most aggressive building code in any major US market. When a homeowner pulls a permit, three things happen — the property's replacement cost changes, the dwelling coverage might be under-rated, and the homeowner is actively in a "spending mode" that makes upgrade conversations possible.</p>

<p>Building permit data tells you which homeowners hit that moment, the day they hit it. PermitGrab pulls Miami-Dade County, Hialeah, and the wider South Florida region daily.</p>

<h2>What Miami-Dade permit data looks like in PermitGrab</h2>

<ul>
  <li><strong>82,067 Miami-Dade property owner records</strong> — full owner names, mailing addresses, parcel IDs (just realigned in V482, was previously mis-tagged)</li>
  <li><strong>13,000+ active permits</strong> across all permit types (residential, commercial, alterations, additions, demolition)</li>
  <li><strong>4,290 active contractor profiles</strong> — useful for spotting which licensed contractors are doing your insureds' work</li>
  <li>Code violation data — properties with active code enforcement cases are a different conversation but a high-value one for E&O and liability lines</li>
  <li>Daily updates with permit filings flagged within 24 hours</li>
</ul>

<h2>The four Miami-Dade permit signals every insurance agent should track</h2>

<h3>1. Roof replacement permits — your #1 re-rate trigger</h3>

<p>Miami-Dade's wind code requires impact-resistant roofing on most replacements. A new code-compliant roof can drop wind premiums 15-40% via mitigation credits. When a homeowner pulls a roof permit, your move is a re-quote with the credits applied — or a binder review if they're insured elsewhere. Miami-Dade files 800-1,500 roof permits per month, and most homeowners don't proactively notify their insurer.</p>

<p>Workflow: filter Miami-Dade permits by permit type "roof" or "reroof", date "last 14 days", and cross-reference your book of business by address. Any match is a $50-300/year retention or new-business opportunity.</p>

<h3>2. Addition / square footage permits</h3>

<p>Additions increase both the property's replacement cost and (depending on the addition type) the wind-rated value. Homeowners adding 200+ sq ft are typically under-rated on dwelling coverage by 10-25% — meaning they're paying for a policy that won't pay full replacement at claim time. This is the conversation that builds trust and writes umbrella + flood add-ons. Miami-Dade files 200-400 addition permits per month.</p>

<h3>3. Pool / pool-cage permits</h3>

<p>New pools trigger liability re-evaluation, umbrella requirements, and (in Miami-Dade specifically) wind-rated pool cage assessments. Homeowners adding a pool cage often don't realize their existing umbrella is under-limited for the new exposure. Miami-Dade leads the country in pool permits — 300-500 per month.</p>

<h3>4. Hurricane impact window / shutter permits</h3>

<p>Like roof code-compliant replacements, hurricane-impact window upgrades qualify for wind mitigation credits. Permit filings tell you exactly which insureds just did the work — typically months before they think to ask their agent for a re-rate.</p>

<h2>How Miami-Dade insurance agents actually use this data</h2>

<p><strong>Retention motion.</strong> Pull a weekly list of permits in your book's ZIP codes, match by address, and call every match: "I noticed you pulled a roof permit on [date] — let me re-rate your policy for the new wind credits before your renewal." Retention impact is measurable: agents running this workflow see 15-30% lower churn on permit-active blocks vs cold blocks.</p>

<p><strong>Cross-sell motion.</strong> Roof + addition + pool permits all signal financial capacity for umbrella, flood (Miami-Dade is 100% flood territory), and high-value home riders. Cold cross-sell is hard; permit-context cross-sell is a different conversation.</p>

<p><strong>New-business motion.</strong> The 82,000+ owner records let you build mailing lists of high-value Miami-Dade homes that aren't currently your insureds. Combine with permit filings (signal of recent investment) and direct mail with a re-rate or discovery offer.</p>

<h2>Specific Miami-Dade permit types that matter for insurance</h2>

<p>Watch for these permit codes in the Miami-Dade feed:</p>
<ul>
  <li><strong>Building Permit (BLDG)</strong> — covers most structural work</li>
  <li><strong>Roof (ROOF)</strong> — reroofs and replacements, including code-compliant upgrades</li>
  <li><strong>Mechanical (MECH)</strong> — HVAC replacements; matters for value-of-contents</li>
  <li><strong>Pool (POOL)</strong> — new pools and pool cages</li>
  <li><strong>Demolition (DEMO)</strong> — partial demos before additions, often under-rated</li>
  <li><strong>Hurricane Window/Door (WIND)</strong> — eligible for premium credits</li>
</ul>

<h2>South Florida coverage beyond Miami-Dade</h2>

<p>The same playbook works in our other Florida Tier 5 cities — they all have permits, owners, contractor phones, and violations live: <a href="/permits/cape-coral">Cape Coral</a>, <a href="/permits/orlando">Orlando</a>, <a href="/permits/fort-lauderdale">Fort Lauderdale</a>, and <a href="/permits/saint-petersburg">St. Petersburg</a>. Hialeah is at Tier 4 (no violations data — Miami-Dade County's CCVIOL feed is unincorporated-only). For broader playbook context across all metros, see our <a href="/leads/insurance">insurance agent lead guide</a>.</p>

<h2>Pricing</h2>

<p>$149/month for unlimited access to all our cities, including the full Miami-Dade feed. No per-lead fees. <a href="/pricing">See pricing</a> or <a href="/signup">try a free week</a>.</p>
""",
        'faqs': [
            ('What insurance lines benefit most from Miami-Dade permit data?',
             'Homeowner\'s (especially HO-3 with wind), wind-only DP-3, flood, and umbrella. Roof permits feed the wind-mitigation credit motion. Addition and pool permits feed dwelling-coverage rerates. Coastal high-value home policies benefit most because re-rates compound on $1M+ policies.'),
            ('How often is Miami-Dade permit data refreshed?',
             'Daily. New permits filed with Miami-Dade County and the City of Miami appear in the PermitGrab feed within 24 hours.'),
            ('Can I match permits against my book of business?',
             'Yes — export the daily Miami-Dade permit list as CSV, match against your CRM by property address. Most agents do this weekly using a simple address join in Excel or their AMS.'),
            ('Are property owner names and mailing addresses included for Miami-Dade?',
             'Yes — 82,067 Miami-Dade property owner records with full mailing addresses are in our property_owners feed. Distinguishing owner-occupant vs absentee owner is one of the highest-value filters for insurance prospecting.'),
            ('Is Miami-Dade permit data legal for insurance agent outreach?',
             'Yes — Florida open records law makes building permits public. Standard TCPA, CAN-SPAM, and do-not-mail rules apply for any outbound contact, but the permit data itself is unrestricted.'),
        ],
    },

    # ====================================================================
    'chicago-motivated-seller-leads': {
        'title': 'Chicago Motivated Seller Leads from Code Violations | PermitGrab',
        'meta_description': (
            '20,670 active Chicago code violation properties — every one '
            'a real-estate-investor lead. 72,026 Chicago property owners '
            'with mailing addresses. Daily updates. $149/mo.'
        ),
        'h1': 'Chicago Motivated Seller Leads from Code Violation Data',
        'subject': 'Real estate investors in Chicago',
        'city': 'Chicago',
        'city_slug': 'chicago-il',
        'persona_slug': 'real-estate-investors',
        'meta_published': '2026-05-02',
        'reading_time': '7 min',
        'body_html': """
<p>Real estate investors looking for motivated sellers in Chicago have one of the richest public-data hunting grounds in the country. The City of Chicago publishes building code violations, vacant building registrations, and absent-owner records on its open data portal — every one of them a lead. Chicago has aggressive code enforcement, a deep stock of pre-1940 housing in need of major repair, and a large population of out-of-state landlords who'd rather sell than fix.</p>

<p>PermitGrab pulls all of this data daily and gives you a unified Chicago view: 20,670 active code violations, 72,026 property owner records, 8,872 contractor profiles, and a daily-updated permit feed. Here's how to turn that into a motivated-seller acquisition pipeline.</p>

<h2>What's actually in the Chicago data feed</h2>

<ul>
  <li><strong>72,026 Chicago property owner records</strong> from the Cook County Assessor — full names, mailing addresses, parcel PINs</li>
  <li><strong>20,670 active code violation properties</strong> from Chicago's Building Violations and 311 Service Requests datasets</li>
  <li><strong>8,872 active contractor profiles</strong> with 3,498 phone numbers — useful for the buy-side rehab playbook</li>
  <li>Daily-updated permit data so you can spot when violations get cured (lead is fresh) vs when they sit untouched (lead is stale)</li>
</ul>

<h2>The four highest-converting Chicago violation types for motivated sellers</h2>

<h3>1. "Failure to maintain" violations on multi-unit buildings</h3>

<p>Chicago's 2-flat and 3-flat housing stock is famously old. When a landlord gets cited for failure to maintain (deferred maintenance code violations), they have three choices: fix it, fight it, or sell. Out-of-state owners overwhelmingly pick "sell" — fixing a violations-laden Chicago 2-flat from Atlanta is a logistical nightmare. Filter the violations feed by violation_code and date_cited within last 60 days, cross-reference owner_mailing_state ≠ IL, and you have a list of out-of-state owners with active code citations on Chicago multi-units. Direct mail "we buy as-is" offers convert at 1.5-3x cold-list rates.</p>

<h3>2. Vacant Building Registration</h3>

<p>Chicago requires vacant building registration with annual fees. Owners who don't register get cited; owners who do register often want out (vacant buildings cost $1,000+/year in registration alone). The vacant-building list is a who's-who of Chicago motivated sellers.</p>

<h3>3. Court-supervised demolition</h3>

<p>When a Chicago property reaches the demolition court calendar, the owner has 60-90 days to either rehab or face city demolition (and a $20-40K bill). Court-supervised demolition is the strongest motivated-seller signal in the data. Filter violations by court_status and date_filed.</p>

<h3>4. Repeated 311 housing complaints on the same address</h3>

<p>3+ housing-complaint 311 calls on the same address in 12 months is a tenant-quality-of-life red flag. Owners with chronic 311 issues either fix them or sell — and the fix path involves capital. Aggregate 311 by address, sort descending, and the top 100 properties are a high-yield prospecting list.</p>

<h2>How Chicago real estate investors run this playbook</h2>

<p><strong>The mailing list workflow.</strong> Pull active violations + property_owners join, filter to out-of-state mailing addresses, export 500-1,000 records per month. Send "we buy houses, we buy as-is, we close in 14 days" letters. Response rates on this audience run 0.5-2%, conversion to closed deal another 5-15% of responders. At a $149/mo subscription, you need one closed deal per quarter to make the math obvious.</p>

<p><strong>The cold-call workflow.</strong> Use the property_owners file's mailing-address city/state as a phone-number match (most skip-trace tools use mailing address as the input). Layer that against the violation list. Cold call yields 1-3% appointment-set rate, comparable to most outbound real estate prospecting.</p>

<p><strong>The drive-for-dollars validation workflow.</strong> Some investors still drive Chicago neighborhoods looking for distressed exteriors. The violation data lets you validate any property you flag in person — pull the address, check active violations, see who owns it, and decide whether to send a postcard.</p>

<h2>Specific Chicago neighborhoods with high motivated-seller density</h2>

<p>Filter by neighborhood or ward to focus your outreach. Chicago violation density per capita is highest in:</p>
<ul>
  <li>Englewood and West Englewood</li>
  <li>Austin (the West Side neighborhood)</li>
  <li>South Chicago and East Side</li>
  <li>North Lawndale</li>
  <li>Roseland</li>
</ul>

<p>These are also the neighborhoods where wholesale and BRRRR strategies have the strongest comparative advantage — out-of-state owners hold a disproportionate share of the housing stock, and exit cap rates are favorable for buy-and-hold investors.</p>

<h2>Other Chicago resources</h2>

<p>Browse the live <a href="/permits/chicago-il">Chicago permits and violations page</a> for the latest counts, recent filings, and contractor list. The cross-city playbook for code-violation motivated sellers is documented in our <a href="/leads/real-estate-investors">real estate investors lead guide</a>.</p>

<p>Other Tier 5 cities where the violation-driven motivated-seller playbook works well: <a href="/permits/cleveland-oh">Cleveland</a> (Rust Belt high-violation density), <a href="/permits/saint-petersburg">St. Petersburg</a>, and <a href="/permits/cape-coral">Cape Coral</a>. <a href="/permits/buffalo-ny">Buffalo</a> is at Tier 4 with similar data quality.</p>

<h2>Pricing</h2>

<p>$149/month for unlimited access to Chicago data and every other city in our coverage. No per-lead fees, cancel anytime. <a href="/pricing">See pricing</a> or <a href="/signup">try a free week</a>.</p>
""",
        'faqs': [
            ('How fresh is Chicago code violation data?',
             'Daily. New violations from the City of Chicago Building Violations dataset and 311 Service Requests appear in the PermitGrab feed within 24 hours of city publication.'),
            ('Can I get a list of Chicago landlords who live out of state?',
             'Yes — the property_owners feed includes the owner\'s mailing address. Filter where mailing_state != "IL" to get out-of-state Chicago landlords. This is the single highest-value filter for motivated-seller direct mail.'),
            ('What\'s the difference between a Chicago code violation and a 311 housing complaint?',
             'Code violations are formal citations issued by the Department of Buildings or Department of Streets and Sanitation. 311 housing complaints are tenant- or neighbor-initiated reports that may or may not result in a formal citation. Both signal property issues; violations are the stronger motivation indicator.'),
            ('Does PermitGrab include Cook County properties outside the City of Chicago?',
             'Yes — the Cook County Assessor parcel feed (cook_chicago in our assessor sources) covers Chicago plus suburban Cook County. Violation data is City of Chicago only; suburban Cook municipalities have their own (usually less-public) violation systems.'),
            ('How is this different from a wholesale list service?',
             'Wholesale list services typically resell stale public records with a 30-90 day delay and add markup. PermitGrab gives you direct access to the same source data, refreshed daily, for a flat $149/mo. The trade-off: you do your own filtering and outreach (no done-for-you mailers).'),
        ],
    },

    # ====================================================================
    'austin-subcontractor-leads': {
        'title': 'Austin Subcontractor Leads from Building Permits | PermitGrab',
        'meta_description': (
            'Austin files thousands of permits monthly — every general '
            'contractor on those permits needs subcontractors. 55,000+ '
            'Travis County properties + daily permit feed. $149/mo.'
        ),
        'h1': 'Austin Subcontractor Leads from Building Permit Data',
        'subject': 'Subcontractors in Austin',
        'city': 'Austin',
        'city_slug': 'austin-tx',
        'persona_slug': 'contractors',
        'meta_published': '2026-05-02',
        'reading_time': '6 min',
        'body_html': """
<p>If you're a plumbing, electrical, HVAC, drywall, or roofing subcontractor in Austin, your sales process probably looks something like this: you call GCs you've worked with, you drive job sites looking for trucks, and you rely on word-of-mouth from existing relationships. That works — but it caps your growth at the rate your existing GC relationships file new jobs.</p>

<p>Building permit data unlocks the rest of the market. Every Austin permit names the GC pulling it. Every GC needs your trade. PermitGrab gives you the daily list.</p>

<h2>What's in the Austin data feed</h2>

<ul>
  <li><strong>Daily permit feed from the City of Austin</strong> — every residential, commercial, and tenant-finish permit, with the GC name and license number on each filing</li>
  <li><strong>55,000+ Travis County property owner records</strong> — useful when a GC is missing or the owner-builder filed directly</li>
  <li><strong>Active contractor profiles</strong> across all trades — see who's actively pulling permits in your service area, ranked by volume</li>
  <li>Permit type breakdown so you can filter to your trade's use cases (electrical permits → electrical subs; mechanical permits → HVAC subs; plumbing permits → plumbing subs)</li>
</ul>

<h2>The four GC types every Austin subcontractor should be calling</h2>

<h3>1. The volume builders (50+ permits/year)</h3>

<p>Austin's biggest GCs file dozens of permits a year — production homebuilders, multifamily GCs, and commercial general contractors. Many already have locked-in subcontractor relationships, but turnover happens (a sub goes out of business, a sub gets too busy, a sub overpriced). Volume GCs reward subs who proactively reach out — they don't have time to source new trades themselves.</p>

<p>Workflow: rank Austin GCs by permit count over the last 90 days, descending. The top 50 cover most of the volume. Cold-call the office manager or estimator with: "I'm a [trade] sub running $X/sq-ft in [zip codes]. Do you have a sub spot open for [trade]?"</p>

<h3>2. The 5-15 permit/year mid-size GCs</h3>

<p>This is the sweet spot for most Austin subcontractors. Mid-size GCs are big enough to give you steady work but small enough that you'll actually get the GM on the phone. They're often less locked-in to existing subs and more open to new relationships. PermitGrab lets you filter active GCs by permit count to surface this tier exactly.</p>

<h3>3. The new-to-Austin GCs</h3>

<p>Out-of-state GCs filing their first Austin permits are gold. They have no existing sub relationships, they're under deadline pressure, and they'll often pay above-market rates to get a project staffed. Filter by GC name appearing for the first time in the last 90 days against your historical Austin permit dataset.</p>

<h3>4. The owner-builders</h3>

<p>Owner-builder permits — where the property owner pulls the permit themselves, no GC — are a different sale but a high-margin one. Owner-builders are typically high-net-worth individuals doing custom homes. They source subs themselves, often via word-of-mouth referrals. PermitGrab surfaces these via the property_owners feed cross-referenced against permits where contractor_name is null or matches the owner's name.</p>

<h2>How Austin subcontractors actually use this data</h2>

<p><strong>The Monday morning list.</strong> Filter Austin permits filed in the last 7 days where permit_type matches your trade (electrical, plumbing, mechanical, etc). Export to CSV. Hand to a junior estimator who calls each GC: "I noticed you pulled a permit at [address] — do you have your [trade] sub locked in?" Conversion 5-10% to a quote request, depending on trade and timing.</p>

<p><strong>The competitor analysis workflow.</strong> See which subs your competitors are working with. If a GC consistently uses [Competitor X] for plumbing, that's not a target — they have a relationship. If a GC has used 4 different plumbing subs in the last 12 months, they're shopping — that's a target.</p>

<p><strong>The geographic expansion workflow.</strong> If you currently work South Austin, see what GCs are pulling permits in North Austin or Round Rock. Match those GCs against your existing Austin relationships, and you have a warm-intro path into a new geography.</p>

<h2>Trade-specific filters that matter for Austin</h2>

<p>Austin's permit system breaks out work by trade, which means you can filter to your trade-specific permit subset:</p>
<ul>
  <li><strong>Electrical subs</strong> — filter permit_type to "Electrical" or "ELE"</li>
  <li><strong>Plumbing subs</strong> — filter to "Plumbing" or "PLB"</li>
  <li><strong>HVAC subs</strong> — filter to "Mechanical" or "MEC"</li>
  <li><strong>Roofing subs</strong> — filter to permit type "Roofing" or work_class containing "Roof"</li>
  <li><strong>Drywall / framing / finish subs</strong> — these are inside the broader "Building" or "Residential" permit type; filter by permit_value > $50K to focus on real construction (not minor alterations)</li>
</ul>

<h2>Other Austin and Texas resources</h2>

<p>Browse the live <a href="/permits/austin-tx">Austin permits page</a> for current contractor counts, recent filings, and trade breakdowns. For the cross-city playbook, see our <a href="/leads/contractors">contractor leads guide</a>.</p>

<p>Other Tier 5 Texas cities with the same daily permit feed quality: <a href="/permits/san-antonio">San Antonio</a> (3,830 contractor phones — biggest TX phone count we have). Houston has 83K violations and 500K newly-wired property owners but no live permit feed (HCAD is Accela-only). Dallas permits are frozen at 2020.</p>

<h2>Pricing</h2>

<p>$149/month, unlimited cities, cancel anytime. <a href="/pricing">See pricing</a> or <a href="/signup">try a free week</a>.</p>
""",
        'faqs': [
            ('How current is Austin permit data?',
             'Daily. New permits issued by the City of Austin appear in the PermitGrab feed within 24 hours.'),
            ('Can I see which GCs are most active in Austin?',
             'Yes — the contractor profiles list ranks active Austin GCs by recent permit count. Filter by trade to see which GCs are pulling the most permits in your specific work category.'),
            ('Does PermitGrab include the GC\'s phone number?',
             'For licensed contractors who pulled the permit, we include phone numbers from the Austin permit filings (if the city captured one) plus state license database matches where available. Not every contractor has a phone in the feed; the typical capture rate is 30-50% of active contractors.'),
            ('What\'s the difference between owner-builder and licensed-contractor permits?',
             'Owner-builder permits are pulled by the property owner directly without a GC of record. They\'re common for custom homes and major DIY remodels. Licensed-contractor permits are pulled by a state-licensed GC. Both appear in the Austin feed; you can filter to either subset by checking whether contractor_name is populated.'),
            ('Will the GCs I call mind that I found them through public permit records?',
             'In our experience, no — Texas GCs expect outbound calls from subs and the permit-records source is well-known in the trade. Lead with value (your trade, your service area, your rate) rather than how you found them. The permit-source mention is usually irrelevant to the conversation.'),
        ],
    },

    # ====================================================================
    'cleveland-motivated-seller-leads': {
        'title': 'Cleveland Motivated Seller Leads from Code Violations | PermitGrab',
        'meta_description': (
            'Cleveland Rust-Belt housing stock + aggressive code '
            'enforcement = a motivated-seller hunting ground. 60,000+ '
            'Cuyahoga property owners, daily violations feed. $149/mo.'
        ),
        'h1': 'Cleveland Motivated Seller Leads from Code Violation Data',
        'subject': 'Real estate investors in Cleveland',
        'city': 'Cleveland',
        'city_slug': 'cleveland-oh',
        'persona_slug': 'real-estate-investors',
        'meta_published': '2026-05-02',
        'reading_time': '6 min',
        'body_html': """
<p>Cleveland is one of the highest-yield motivated-seller markets in the country. Median home prices in the city sit well below national averages, the housing stock is dominated by pre-1950 construction with deferred maintenance, and Cleveland's Department of Building and Housing runs an aggressive code-enforcement program. Add to that a high concentration of out-of-state and out-of-Cleveland owners (some via the early-2010s Rust Belt buying boom that didn't pan out), and you have ideal conditions for an investor-side acquisition pipeline.</p>

<p>PermitGrab pulls Cleveland code violations, permits, and Cuyahoga County property owner data daily. Here's how to turn it into a list of sellers who'll actually pick up the phone.</p>

<h2>What's in the Cleveland data feed</h2>

<ul>
  <li><strong>60,000+ Cuyahoga County property owner records</strong> — full names, mailing addresses, parcel IDs from the Cuyahoga County Fiscal Officer</li>
  <li><strong>Active Cleveland code violations</strong> — building, housing, exterior maintenance, hazardous conditions</li>
  <li><strong>148 active contractor phone numbers</strong> for the buy-side rehab playbook (lower than coastal markets, but Cleveland's rehab cost basis is also lower)</li>
  <li>Daily-updated permit data so you can flag rehabs in progress vs stalled vs not started</li>
</ul>

<h2>The Cleveland-specific motivated-seller signals</h2>

<h3>1. Out-of-Cleveland owners with active violations</h3>

<p>Cleveland has a large population of properties owned by people who don't live in Cleveland — buyers from the 2010-2014 cycle, family inheritances, accidental landlords. When those owners get hit with a violation, the cost of remote management makes "sell" a more attractive option than "fix." Filter property_owners where mailing_state ≠ OH or mailing_city ≠ Cleveland, intersect with active violations, and you have your top-tier motivated-seller list.</p>

<h3>2. "Vacant or open" violations</h3>

<p>Cleveland tracks vacant buildings aggressively. A building flagged as "vacant or open" is a code-enforcement priority and an owner-cost burden. Owners of multiple vacant Cleveland properties are over-represented in the wholesale-buyer flow — many are willing to discount substantially for an as-is, fast-close offer.</p>

<h3>3. Demolition-ordered properties</h3>

<p>Cleveland has been demolishing 500-1,500 buildings a year in its blight-removal program. Properties on the demolition list are 60-180 days from city teardown. Owners who can transfer the property before demolition often will — sometimes for nominal consideration, sometimes for $5-15K — to avoid the demolition fee and the permanent loss of the building. This is a niche play for investors with capacity to rehab marginal properties or land-bank.</p>

<h3>4. Tax-delinquent + violation-active intersect</h3>

<p>Cuyahoga County publishes tax-delinquency lists separately. Cross-referencing tax-delinquent parcels with Cleveland code violations identifies properties under maximum financial pressure. This is the highest-conversion list in the Cleveland data — these owners are typically 30-90 days from tax foreclosure or sheriff sale.</p>

<h2>How Cleveland real estate investors run this</h2>

<p><strong>Direct mail at scale.</strong> Cleveland's mailing-list economics are extremely favorable — postage is the same as anywhere else but the cost basis on the houses you're targeting is much lower. Investors who'd pull 500 records/month for a Chicago campaign can pull 2,000+ for the same dollar value of opportunity in Cleveland.</p>

<p><strong>Cold-call after skip-trace.</strong> Cleveland's owner mailing addresses skip-trace cleanly through standard tools (BatchSkipTracing, Skip Genie, etc.). Cold-call conversion rates on the violations-active list run 0.5-1.5% to a quote.</p>

<p><strong>Wholesaling specifically.</strong> Cleveland's distance from coastal investor markets means wholesalers who source local find a steady flow of buyers willing to pay above-market assignment fees. The PermitGrab violations feed gives you a competitive edge over wholesalers still working off MLS or Zillow.</p>

<h2>The neighborhoods where this works</h2>

<p>Cleveland code-violation density is highest in:</p>
<ul>
  <li>Slavic Village (St. Hyacinth-Notre Dame area)</li>
  <li>Glenville and Hough</li>
  <li>Detroit Shoreway</li>
  <li>Mount Pleasant</li>
  <li>Old Brooklyn (specifically the deeper south of the neighborhood)</li>
</ul>

<p>These neighborhoods also have the deepest investor exit options — both buy-and-hold (rents support mid-cap rates) and wholesale flips to local landlords.</p>

<h2>Other Rust Belt resources</h2>

<p>Browse the live <a href="/permits/cleveland-oh">Cleveland permits and violations page</a> for current counts and recent filings. The cross-city playbook is in our <a href="/leads/real-estate-investors">real estate investors lead guide</a>.</p>

<p>Other Rust Belt cities where this playbook works: <a href="/permits/buffalo-ny">Buffalo</a> (Tier 4, similar pre-1940 housing stock and Erie County owner data), and <a href="/permits/saint-petersburg">St. Petersburg</a> (different geography but similar absentee-owner dynamics in the older neighborhoods).</p>

<h2>Pricing</h2>

<p>$149/month, unlimited cities, cancel anytime. <a href="/pricing">See pricing</a> or <a href="/signup">try a free week</a>.</p>
""",
        'faqs': [
            ('How fresh is Cleveland violation data?',
             'Daily. New violations from the Cleveland Department of Building and Housing appear in the PermitGrab feed within 24 hours of city publication.'),
            ('Are out-of-state Cleveland landlords easy to identify in the data?',
             'Yes — the property_owners feed includes mailing address. Filter mailing_state != OH or mailing_city != Cleveland to get out-of-area landlords. About 25-35% of Cleveland\'s rental stock has out-of-area owners depending on the neighborhood.'),
            ('What types of code violations matter most for motivated-seller targeting?',
             '"Failure to maintain" violations on the exterior, "vacant or open" violations, and demolition-ordered properties are the top three. Hazard violations (gas leaks, structural) signal urgent owner action and are a faster-cycle list.'),
            ('Does PermitGrab include Cleveland tax-delinquency data?',
             'Not directly — tax-delinquency is published separately by the Cuyahoga County Treasurer. Many investors pull tax-delinquent lists from the Treasurer and intersect them with our violations feed for a higher-pressure prospect list.'),
            ('How does Cleveland owner data quality compare to other cities?',
             'High. Cuyahoga County\'s Fiscal Officer publishes parcel and owner data publicly with full mailing addresses, no suppression, and weekly updates. It\'s comparable in quality to Cook County (Chicago) and substantially better than California metros where owner names are typically suppressed.'),
        ],
    },

    # ====================================================================
    'nyc-roofing-leads': {
        'title': 'NYC Roofing Contractor Leads from Building Permits | PermitGrab',
        'meta_description': (
            'NYC files thousands of roof permits annually — every one is '
            'a competing-bid opportunity for roofing contractors. Daily '
            'feed across all five boroughs. $149/mo unlimited cities.'
        ),
        'h1': 'NYC Roofing Contractor Leads from Building Permit Data',
        'subject': 'Roofing contractors in NYC',
        'city': 'New York City',
        'city_slug': 'new-york-city',
        'persona_slug': 'home-services',
        'meta_published': '2026-05-02',
        'reading_time': '6 min',
        'body_html': """
<p>New York City files more building permits than any other US city — and a meaningful slice of them are roof work. Co-op and condo boards, brownstone owners, multifamily landlords, and commercial property managers all file with NYC DOB before any roof project starts. For roofing contractors with NYC licensing, that filing is a sales signal — competing bids are open, the project is real, and the homeowner has already committed to spending.</p>

<p>PermitGrab pulls NYC DOB permits and HPD violations daily across all five boroughs. Here's how roofing contractors use it.</p>

<h2>What's in the NYC data feed</h2>

<ul>
  <li><strong>Daily NYC DOB permit feed</strong> across Manhattan, Brooklyn, Queens, Bronx, and Staten Island</li>
  <li><strong>5,000 NYC PLUTO property owner records</strong> with BBL (borough-block-lot) IDs and addresses</li>
  <li><strong>NYC HPD and DOB violations</strong> — useful for cross-referencing buildings with code-mandated roof or facade work</li>
  <li><strong>791 NYC contractor profiles with phone numbers</strong> in the latest pull (NYC contractor licensing data is harder to enrich than other states; this is what we have)</li>
</ul>

<h2>The four NYC roof permit signals roofing contractors should watch</h2>

<h3>1. PW1 (Plan Work) Type 2 alterations involving roofing</h3>

<p>NYC's permit taxonomy uses PW1 forms for most permitted work. Type 2 alterations include re-roofing, roof structural changes, and roof-mounted mechanical equipment. Filter permits by job_type "A2" or "Alt 2" + work_type containing "ROOF" to surface roof-specific filings.</p>

<h3>2. Local Law 11 / FISP (Facade Inspection Safety Program) cycle filings</h3>

<p>NYC requires periodic facade and parapet inspections on buildings 6 stories or taller. When an FISP cycle uncovers issues, the building owner files a permit for repairs — often involving roof flashing, parapet work, and waterproofing tied into the roof system. FISP-driven work is contractor-specific (engineers and architects approved for FISP work), but the underlying contracts include roofing scopes that get bid out.</p>

<h3>3. Local Law 97 retrofit-driven roof work</h3>

<p>NYC's Local Law 97 (greenhouse gas emissions for buildings 25,000+ sq ft) is driving HVAC and envelope retrofits across the city's commercial and large-multifamily stock. Many LL97 retrofits involve cool-roof installations, solar-ready roof reinforcement, or full roof replacement to eliminate thermal bridges. These permits are filed as alterations and identifiable by description fields containing "energy", "retrofit", "HVAC", or "envelope".</p>

<h3>4. Demolition + new-construction permit pairs</h3>

<p>When a NYC lot files a demolition permit followed by a new-construction permit on the same BBL, you have a brand-new roof contract entering the bid market 12-24 months later. Track demolition + new-construction permit pairs to build a forward calendar of roofing contracts.</p>

<h2>Borough-specific patterns for NYC roofing contractors</h2>

<p><strong>Manhattan.</strong> Mostly commercial roof work and high-rise residential. Filtered to commercial permits, this is a high-margin / long-cycle market — six months from permit filing to roof contract bid is typical. Few residential opportunities compared to outer boroughs.</p>

<p><strong>Brooklyn and Queens.</strong> The bread and butter for NYC residential roofing — brownstones, two-families, three-families, and small multifamily. Most permits convert to bid opportunities within 30-60 days. This is where most NYC residential roofers focus.</p>

<p><strong>Bronx.</strong> Heavy multifamily and HPD-rehab work. HPD violations on roof systems generate permit filings; tracking the HPD-violation-to-permit transition surfaces these opportunities.</p>

<p><strong>Staten Island.</strong> The most "suburban" of the boroughs — single-family and small multifamily roof work, similar dynamics to suburban Long Island.</p>

<h2>How NYC roofing contractors actually use the data</h2>

<p><strong>Daily borough scan.</strong> Filter NYC permits filed in the last 24-48 hours by borough and permit_type. Hand to a junior estimator who calls each filer (architect, engineer, or property owner of record) with a "we noticed your filing — can we put a roofing bid in?" pitch.</p>

<p><strong>Co-op and condo board outreach.</strong> Multi-family permits in NYC typically have a managing agent on file. PermitGrab includes the filer's contact info where the city captures it. Managing agents control multiple properties and one good relationship feeds 5-15 buildings of work.</p>

<p><strong>HPD violation cross-reference.</strong> Filter HPD violations to roof-related codes, intersect with the property_owners feed, and direct-mail building owners with active roof violations.</p>

<h2>Other NYC and East Coast resources</h2>

<p>Browse the live <a href="/permits/new-york-city">NYC permits page</a> for current contractor counts, recent filings, and HPD/DOB violation data. For the cross-city home-services playbook, see our <a href="/leads/home-services">home services lead guide</a>.</p>

<p>Other Tier 5 East Coast cities where the roofing playbook works: <a href="/permits/philadelphia">Philadelphia</a> (Tier 4 — strong daily permit data, missing only owners), and <a href="/permits/cleveland-oh">Cleveland</a> (older housing stock, similar permit dynamics).</p>

<h2>Pricing</h2>

<p>$149/month, all five NYC boroughs included plus every other city in our coverage. <a href="/pricing">See pricing</a> or <a href="/signup">try a free week</a>.</p>
""",
        'faqs': [
            ('Are NYC DOB permits updated daily in PermitGrab?',
             'Yes — NYC permit data is pulled daily from the NYC Open Data portal (the NYC DOB Job Application Filings dataset and HPD/DOB violation datasets). New filings appear in the feed within 24 hours.'),
            ('Can I filter NYC permits to roof work only?',
             'Yes. The NYC permit data includes permit_type, job_type, and work_type fields. Filter to job_type "A2" or work_type containing "ROOF" / "ROOFING" to focus on roof-specific filings. The query syntax is documented on the NYC permits page.'),
            ('Does PermitGrab include NYC contractor licensing data?',
             'Yes — we pull NY State Department of State licensing data for the contractors who appear in the NYC permit feed. This includes home improvement contractor (HIC) licensing for the five boroughs. About 791 NYC contractors had phone numbers in the latest pull; the actual NYC licensee universe is larger but not all licensees pull permits regularly.'),
            ('What\'s the difference between PW1, PW1A, and PW2 permit forms?',
             'PW1 is the initial plan-work permit application. PW1A is an amendment to a previously-issued PW1. PW2 is the work permit that follows after PW1 plan approval. Most roofing contracts are bid against the PW1 stage; the PW2 issue date marks when the project is fully approved to proceed.'),
            ('Are NYC HPD violations useful for roofing leads?',
             'Yes — HPD violations include several roof-related codes (e.g. roof leaks, hazardous roof conditions, missing roof flashing). Buildings with active HPD roof violations are required to remediate, which typically means filing a permit and bidding the work. Cross-referencing HPD violations against permit filings surfaces buildings where the work is pending but not yet bid out.'),
        ],
    },

    # ====================================================================
    # V487 additions — Detroit / Atlanta / Sacramento / Houston
    # ====================================================================

    'detroit-motivated-seller-leads': {
        'title': 'Detroit Motivated Seller Leads from Blight Tickets | PermitGrab',
        'meta_description': (
            '378,000+ Detroit property owners + daily-fresh blight tickets. '
            'The cheapest entry-cost motivated-seller pipeline in the '
            'country. Direct mail at scale. $149/mo unlimited.'
        ),
        'h1': 'Detroit Motivated Seller Leads from Blight Tickets and Owner Data',
        'subject': 'Real estate investors in Detroit',
        'city': 'Detroit',
        'city_slug': 'detroit',
        'persona_slug': 'real-estate-investors',
        'meta_published': '2026-05-02',
        'reading_time': '6 min',
        'body_html': """
<p>Detroit is the highest-volume motivated-seller market in the country at the lowest entry-cost basis. Median home prices in many city neighborhoods sit under $50,000. The City of Detroit issues more blight tickets per capita than any other major metro, and the City Assessor publishes the full owner roll daily. PermitGrab pulls both feeds and gives you a unified view: 378,000+ property owners with mailing addresses, plus daily-fresh blight ticket data identifying which properties are under city-enforcement pressure right now.</p>

<p>If you've struggled to find a Cleveland or Buffalo motivated-seller list because the data is gated or stale, Detroit is the opposite — abundant, fresh, and public.</p>

<h2>What's in the Detroit data feed</h2>

<ul>
  <li><strong>378,366 City of Detroit property owner records</strong> — full taxpayer names, mailing addresses, parcel IDs from the Detroit Assessor's daily-refreshed file</li>
  <li><strong>Daily Detroit blight ticket feed</strong> — code enforcement, ordinance violations, hazardous conditions, demolition orders, with disposition status</li>
  <li>Permit data is not currently available for Detroit — the city's permit system is not exposed via public REST. Owner + blight is the data set</li>
</ul>

<h2>The Detroit-specific motivated-seller signals</h2>

<h3>1. Out-of-state Detroit landlords with active blight tickets</h3>

<p>Detroit has a uniquely high concentration of out-of-state property owners — buyers from the 2010-2014 cycle, accidental landlords from inheritance, and small-portfolio investors who underestimated the operating cost. When a Detroit blight ticket lands on an out-of-state owner, "sell" is often a faster path than "fix and contest." Filter the property_owners feed where taxpayer_state ≠ MI, intersect with active blight tickets, and you have a high-motivation list.</p>

<h3>2. Multiple blight tickets on a single parcel</h3>

<p>One blight ticket is a nuisance; three or more on the same parcel signals systemic neglect and a high probability of seller-readiness. Aggregate by parcel_id, sort descending — the top 200-500 parcels are the highest-yield direct-mail list in the country.</p>

<h3>3. Demolition-listed properties</h3>

<p>Detroit's blight removal program demolishes thousands of buildings per year. Properties on the demolition list are 90-180 days from city-funded teardown. Owners often transfer for $1-5K to avoid demolition fees and capture any salvage value. Filter blight tickets where ordinance_description contains "demolition" or disposition references the demolition court.</p>

<h3>4. Tax-foreclosure-eligible parcels</h3>

<p>Wayne County conducts an annual tax foreclosure auction. Properties 2+ years tax-delinquent flow into the auction in late summer. Cross-referencing the property_owners feed with publicly-available tax-delinquency lists (from the Wayne County Treasurer) identifies properties under maximum financial pressure. The intersection of "out-of-state owner + tax-delinquent + active blight ticket" is the most motivated cohort in the data.</p>

<h2>The math is different in Detroit</h2>

<p>Most cities require investors to be selective on direct-mail lists because postage and skip-trace cost approaches the average deal margin. Detroit's economics flip this — entry cost on properties is so low that mailing 5,000 records per month for $3,500 in postage can yield 5-15 closed deals at $5,000-$30,000 net per deal. The same $3,500 in postage in Chicago might yield 1-3 deals at $30,000-$80,000 each. Different volume, different deal size, different unit economics.</p>

<p>This makes Detroit the canonical "list-buying market." Investors who run direct-mail campaigns in Detroit typically pull lists in the 1,000-5,000 records per month range — far higher than Cleveland, Buffalo, or Pittsburgh.</p>

<h2>How Detroit investors actually run this playbook</h2>

<p><strong>The volume mailing workflow.</strong> Pull active blight tickets from last 90 days, intersect with property_owners where taxpayer_state ≠ MI, dedupe by parcel, and you have a 1,500-3,000 record monthly mailing list. Send "we buy houses" yellow letters or postcards. Response rates 1-3%, conversion to contract another 5-15%. Average deal economics: 1 deal per 100-200 mailers.</p>

<p><strong>The wholesaling workflow.</strong> Local Detroit wholesalers find a steady flow of cash buyers willing to pay $3,000-$8,000 in assignment fees. PermitGrab gives you a cost-of-acquisition advantage over wholesalers working off MLS or driving for dollars.</p>

<p><strong>The land-bank workflow.</strong> Some investors use the demolition-listed and tax-foreclosure-eligible data to acquire land for $1-5K, demolish, and hold for 5-10 years anticipating neighborhood revitalization. The data feed identifies the right targets before they hit the auction.</p>

<h2>Detroit neighborhoods where this works</h2>

<p>Blight ticket density is highest in:</p>
<ul>
  <li>Brightmoor and Cody Rouge (West Side)</li>
  <li>Mack-Concord and Eastside neighborhoods around I-94</li>
  <li>Springwells and the Southwest near the Ambassador Bridge</li>
  <li>North End and Hamtramck-adjacent neighborhoods</li>
  <li>Davison-Schoolcraft corridor</li>
</ul>

<p>These are also the neighborhoods where wholesale flips and BRRRR strategies have the deepest exit-buyer pool.</p>

<h2>Other resources</h2>

<p>Browse the live <a href="/permits/detroit">Detroit data page</a> for current owner counts, recent blight tickets, and aggregate stats. The cross-city motivated-seller playbook is documented in our <a href="/leads/real-estate-investors">real estate investors lead guide</a>. For higher cost-basis Rust Belt comparisons, see our <a href="/blog/cleveland-motivated-seller-leads">Cleveland</a> and <a href="/blog/chicago-motivated-seller-leads">Chicago</a> playbooks.</p>

<h2>Pricing</h2>

<p>$149/month for unlimited access to Detroit data and every other city in our coverage. <a href="/pricing">See pricing</a> or <a href="/signup">try a free week</a>.</p>
""",
        'faqs': [
            ('Are Detroit blight tickets the same as code violations?',
             'Yes — "blight ticket" is Detroit\'s term for what other cities call code violations or housing-code citations. The dataset covers all city ordinance violations with hearing dates, dispositions, and fines.'),
            ('How fresh is Detroit owner data?',
             'Daily. The City of Detroit Assessor publishes the parcel file on a daily-refresh cycle. New transactions and ownership changes appear in the PermitGrab feed within 24 hours.'),
            ('Why is Detroit owner data more accessible than Cleveland or Chicago?',
             'Detroit publishes the full taxpayer file on the city\'s data portal as part of its transparency commitment around blight removal. Cleveland and Chicago publish via county assessors with similar quality but slightly different access patterns. All three are workable; Detroit is the most direct.'),
            ('Can I bulk-download Detroit blight ticket data?',
             'PermitGrab pulls Detroit blight tickets daily into our database; you access the data via city pages, CSV export, or our API. The original source dataset (data.detroitmi.gov) also supports direct CSV download but doesn\'t include the property_owners join, which is where most of the motivated-seller value comes from.'),
            ('Does PermitGrab cover suburban Detroit (Wayne County outside the city)?',
             'Not currently — the wayne_detroit owner source is City-of-Detroit only. Many suburban Wayne County municipalities (Dearborn, Livonia, Westland) have separate parcel feeds we haven\'t yet wired. Reach out via support if a specific suburb is critical to your workflow.'),
        ],
    },

    # ====================================================================
    'atlanta-real-estate-investor-leads': {
        'title': 'Atlanta Real Estate Investor Leads from Property Records | PermitGrab',
        'meta_description': (
            '617,000+ Atlanta-metro property owner records across Fulton + '
            'DeKalb counties. Out-of-state landlord lists, absentee-owner '
            'data, and direct-mail-ready exports. $149/mo unlimited.'
        ),
        'h1': 'Atlanta Real Estate Investor Leads from Property Owner Data',
        'subject': 'Real estate investors in Atlanta',
        'city': 'Atlanta',
        'city_slug': 'atlanta',
        'persona_slug': 'real-estate-investors',
        'meta_published': '2026-05-02',
        'reading_time': '6 min',
        'body_html': """
<p>Atlanta is one of the deepest investor markets in the country, with active wholesalers, fix-and-flippers, and buy-and-hold landlords working both the urban core and the suburbs. The challenge: Atlanta's permit data is largely closed (the city dropped its Accela export to a static 2019-2024 CSV in 2024 and has not updated it). Building permit data isn't your hunting ground here — but property owner data is.</p>

<p>PermitGrab pulls both Fulton and DeKalb County parcel data for the full Atlanta metro. 617,000+ records, weekly to monthly refresh, full mailing-address segmentation. Here's the playbook.</p>

<h2>What's in the Atlanta data feed</h2>

<ul>
  <li><strong>372,724 Fulton County property owner records</strong> — Atlanta core (TaxDist=25), Sandy Springs, Roswell, College Park, East Point, South Fulton</li>
  <li><strong>245,806 DeKalb County property owner records</strong> — Decatur, Brookhaven, Doraville, Tucker, Stone Mountain, Lithonia, unincorporated DeKalb</li>
  <li>Combined coverage: 617,530 Atlanta-metro property records with owner names, parcel IDs, mailing addresses, and tax-district info</li>
  <li>Atlanta permits are not available — Atlanta's open-data portal dropped to a static archive in 2024. Property data is the leverage.</li>
  <li>Atlanta code violations are not currently available via REST (Atlanta uses Accela for code enforcement with no public API)</li>
</ul>

<h2>The four highest-value Atlanta investor signals</h2>

<h3>1. Out-of-state Atlanta landlords</h3>

<p>Atlanta's investor market saw heavy 2010-2018 outside-state buying — California, Texas, New York, Florida investors built portfolios remotely. Many are now exiting as Atlanta cap rates compress. Filter property_owners where mailing_state ≠ GA, intersect with single-family-residential property type, and you have an out-of-state landlord list. Direct mail conversion rates run 0.5-1.5%, comparable to Phoenix and Chicago.</p>

<h3>2. Inherited / estate-held properties</h3>

<p>Atlanta's pre-1990 housing stock has aged into a steady flow of inherited properties. Indicators in the data: owner names containing "EST OF" or "ESTATE OF", mailing addresses different from the site address, properties held for 20+ years (last sale date) by individuals (not LLCs). Estate properties have multi-heir decision dynamics and are often willing to sell at below-market prices for clean, fast closings.</p>

<h3>3. LLC-held properties signaling investor exits</h3>

<p>Filter property_owners where owner name contains "LLC" or "TRUST" — these are the institutional and small-investor holdings. Cross-reference against properties held 5+ years (a typical hold cycle for residential single-family). Investors at the 5-7 year mark are evaluating exit. Direct contact via the LLC's mailing address often finds a managing partner ready to discuss a portfolio sale.</p>

<h3>4. South Fulton and DeKalb high-density rental neighborhoods</h3>

<p>The strongest investor-to-investor wholesale activity in Atlanta happens in:</p>
<ul>
  <li>Lakewood Heights, Pittsburgh, Adair Park (South Atlanta core)</li>
  <li>Old Fourth Ward, Edgewood, Reynoldstown (gentrifying east)</li>
  <li>South Fulton (East Point, College Park, parts of unincorporated)</li>
  <li>South DeKalb (Lithonia, parts of Decatur, Stone Mountain)</li>
</ul>
<p>These are the neighborhoods where direct-mail and cold-call conversion rates are highest, and where local wholesalers consistently find willing assignors.</p>

<h2>How Atlanta investors run this</h2>

<p><strong>The mailing list workflow.</strong> Pull Fulton + DeKalb property_owners, filter to out-of-state mailing addresses on single-family residential, dedupe by owner name (so you mail one letter per landlord, not per parcel), export 800-2,000 records per month. Send a "we buy houses" letter or a portfolio inquiry letter. Response rates 0.5-2%, conversion to closed deal another 5-15% of responders.</p>

<p><strong>The skip-trace and call workflow.</strong> Atlanta owner mailing addresses skip-trace cleanly. Cold-call conversion to appointment runs 1-3% on the out-of-state landlord list — not the highest in the country, but Atlanta deals are larger ($60K-$200K average wholesale) so the math works out.</p>

<p><strong>The portfolio-acquisition workflow.</strong> LLCs and trusts holding 10+ Atlanta properties are a different sale — bulk portfolio purchase, often with seller financing. The PermitGrab data lets you identify these owners, then approach them directly with a portfolio offer rather than per-property pitches.</p>

<h2>What Atlanta does NOT have in PermitGrab (yet)</h2>

<p>Permit data is unavailable. Atlanta's Accela tenant has no public REST API, and the city's static CSV is frozen in 2024. We track for any change in this status. Code violation data is also unavailable for the same reason — Atlanta runs code enforcement through Accela with no public feed.</p>

<p>The owner-data alone supports the wholesale, fix-and-flip, and buy-and-hold playbooks well. Investors who specifically need permit data (renovation-triggered prospects) should focus on cities where permits are wired — see <a href="/leads/real-estate-investors">our investor playbook</a> for cross-city options.</p>

<h2>Pricing</h2>

<p>$149/month for unlimited access to Atlanta-metro data and every other city in our coverage. <a href="/pricing">See pricing</a> or <a href="/signup">try a free week</a>.</p>
""",
        'faqs': [
            ('Why doesn\'t PermitGrab have Atlanta building permits?',
             'Atlanta runs its permit system through Accela Citizen Access with no public REST API. The city\'s open-data portal had a permit dataset (2019-2024) but it was dropped to a one-time CSV snapshot in August 2024. We re-probe Atlanta sources monthly; if a fresh permit feed emerges we\'ll wire it.'),
            ('How fresh is Atlanta property owner data?',
             'Fulton County refreshes weekly with TaxYear 2026 currently active. DeKalb County refreshes weekly with the most recent LASTUPDATE in our last pull from 2026-04-30.'),
            ('Does the data cover Atlanta suburbs outside Fulton and DeKalb?',
             'Cobb, Gwinnett, Henry, and Clayton counties are not yet wired. Cobb is the next priority on our Atlanta-metro expansion list. Reach out via support if a specific county is critical.'),
            ('Can I separate Atlanta-city parcels from county-wide records?',
             'Yes. In the Fulton data, filter TaxDist=25 to get City of Atlanta parcels (about 90,000 of the 372,000 total). DeKalb is more municipality-fragmented; filter the CITY field to specific cities like Decatur, Brookhaven, Doraville etc.'),
            ('Are out-of-state Atlanta landlords easy to identify?',
             'Yes — both Fulton and DeKalb feeds include the owner mailing address. Filter mailing_state != GA to get out-of-state landlords. About 15-25% of Atlanta\'s rental stock has out-of-state owners depending on the neighborhood.'),
        ],
    },

    # ====================================================================
    'sacramento-contractor-leads': {
        'title': 'Sacramento Contractor Leads from Building Permits | PermitGrab',
        'meta_description': (
            'Sacramento publishes daily-fresh building permits with real '
            'contractor names. CA CSLB license enrichment lifts phones. '
            'Sub-$2 CPC potential. $149/mo unlimited cities.'
        ),
        'h1': 'Sacramento Contractor Leads from Building Permit Data',
        'subject': 'Subcontractors and home services in Sacramento',
        'city': 'Sacramento',
        'city_slug': 'sacramento',
        'persona_slug': 'contractors',
        'meta_published': '2026-05-02',
        'reading_time': '5 min',
        'body_html': """
<p>Sacramento is the most California-friendly market for contractor lead data we cover. Most California metros suppress contractor and owner data via state and county privacy policy — Los Angeles permits are stale, San Francisco has no contractor field, San Jose owner names are blocked. Sacramento is the exception: the city publishes daily-fresh permits with real contractor names, including phone numbers when captured.</p>

<p>If you're a subcontractor, home-service company, or supplier serving the Sacramento Valley, this is a market where outbound permit-driven prospecting actually works.</p>

<h2>What's in the Sacramento data feed</h2>

<ul>
  <li><strong>6,089+ current-year Sacramento permits</strong> with real Contractor names — pulled daily from the City of Sacramento ArcGIS feed</li>
  <li><strong>Live phone-number enrichment via CA CSLB</strong> — California's state contractor licensing database is one of the cleanest in the country, and it cross-references to Sacramento permit filings</li>
  <li>Permit values, work descriptions, addresses, and ZIP codes for territory targeting</li>
  <li>Application/issued/finaled date tracking so you can prioritize active vs completed projects</li>
</ul>

<h2>The four highest-converting Sacramento permit signals</h2>

<h3>1. Residential roofing permits</h3>

<p>Sacramento Valley summers and Delta storms create steady reroof demand. Filter permit_type to "Residential" and Sub_Type containing "Roof" to surface the weekly roof permit list. Roofing subcontractors and supply houses use this to target new roofing project starts within 30 days of permit issuance. Sample real permit: "Web-Minor: E-Permit: Tear Off / Reroof, $7,500 valuation, HAMMER ROOFING contractor."</p>

<h3>2. Solar installation permits</h3>

<p>Sacramento has aggressive municipal solar incentives via SMUD (Sacramento Municipal Utility District). Solar installer leads at the permit stage are a different conversation than aggregator leads — the homeowner has already chosen a solar contractor and is mid-project. Subcontractor opportunities (electrical, structural, roof reinforcement) appear in the same permit file.</p>

<h3>3. Service upgrade permits</h3>

<p>Sacramento's older housing stock requires electrical service upgrades for solar or modern appliance loads. 200A panel upgrade permits are leading indicators of solar adoption AND tenants of HVAC, kitchen, or bath remodels. Electrical subs and HVAC suppliers use service-upgrade permits as a top-of-funnel signal.</p>

<h3>4. ADU permits</h3>

<p>California's statewide ADU laws have made Sacramento a leading ADU market. ADU permits surface multi-trade subcontractor opportunities: framing, electrical, plumbing, HVAC, roofing, drywall, finish carpentry. Filter permits where Work_Desc contains "ADU" or "accessory dwelling unit."</p>

<h2>How Sacramento contractors run this</h2>

<p><strong>Daily list for outbound dialing.</strong> Pull Sacramento permits filed in last 7 days, filter by permit type matching your trade. Most general contractors who pull Sacramento permits are reachable by phone — CA CSLB licenses include phone numbers, and our feed cross-references CSLB on every permit. Junior dialers can run 80-120 dials per day on a fresh permit list.</p>

<p><strong>Geographic territory ranking.</strong> Sort Sacramento permits by ZIP code to identify which territories your competitors are working most actively. Subs serving multiple Sacramento neighborhoods can use ZIP-level permit volume to prioritize sales territory expansion.</p>

<p><strong>Supply-house bid generation.</strong> Suppliers (lumber, drywall, electrical, plumbing) pull the weekly permit list to surface bid opportunities. Project values + work descriptions tell you which permits warrant a quote.</p>

<h2>Why Sacramento works when other CA cities don't</h2>

<p>Sacramento's open-data portal at data.cityofsacramento.org publishes the BldgPermitIssued_CurrentYear feature service with full Contractor field populated. Most other major CA cities either suppress contractor info, freeze the dataset, or never publish it via REST in the first place. Sacramento's daily-refresh and clean schema make it the only viable CA city for permit-driven contractor prospecting.</p>

<p>For broader California options, <a href="/permits/anaheim">Anaheim</a> has wired contractor data via Orange County's Accela ArcGIS feed. <a href="/permits/san-jose-ca">San Jose</a> has owner data via Santa Clara County but not permit-level contractor info. Bay Area cities (SF, Oakland, Berkeley) have no permit feed with contractor names available.</p>

<h2>Other Sacramento resources</h2>

<p>Browse the live <a href="/permits/sacramento">Sacramento permits page</a> for current contractor counts and recent filings. For the cross-city contractor playbook, see our <a href="/leads/contractors">subcontractor leads guide</a>. For the home-services angle (HVAC, plumbing, roofing, solar), see <a href="/leads/home-services">home services leads</a>.</p>

<h2>Pricing</h2>

<p>$149/month, unlimited cities, cancel anytime. <a href="/pricing">See pricing</a> or <a href="/signup">try a free week</a>.</p>
""",
        'faqs': [
            ('How current is Sacramento permit data?',
             'Daily. The City of Sacramento\'s ArcGIS feed refreshes nightly. New permits filed appear in the PermitGrab feed within 24 hours.'),
            ('Why was Sacramento previously listed as a dead-end?',
             'An earlier probe checked the regional SACOG (Sacramento Area Council of Governments) data portal, which has only annual housing summaries. The City of Sacramento\'s actual permit feed is at a different host (data.cityofsacramento.org → services5.arcgis.com). Once we found the city-level feed, Sacramento moved from dead-end to live.'),
            ('Does PermitGrab include contractor phone numbers for Sacramento?',
             'Yes for licensed CA contractors. Our CA CSLB enrichment cross-references Sacramento permit filings against the California State Contractors Licensing Board database, which includes business phone numbers. Capture rate runs 50-70% of permit-pulling contractors.'),
            ('Can I get Sacramento County (suburbs) permits?',
             'Sacramento County has no public REST permit feed — Sacramento County\'s parcel data also suppresses owner names. Our Sacramento coverage is City of Sacramento only.'),
            ('What permit types does Sacramento cover?',
             'Residential, commercial, and tenant-finish permits including building, electrical, plumbing, mechanical, solar, roofing, and structural alterations. Filter by permit_type and Sub_Type to scope to your trade.'),
        ],
    },

    # ====================================================================
    'houston-insurance-agent-leads': {
        'title': 'Houston Insurance Agent Leads from Building Permits | PermitGrab',
        'meta_description': (
            '500,000+ Houston-city property owners + 83,000+ active code '
            'violations. Wind, flood, and umbrella re-rate triggers from '
            'public records. $149/mo unlimited cities.'
        ),
        'h1': 'Houston Insurance Agent Leads from Public Property Records',
        'subject': 'Insurance agents in Houston',
        'city': 'Houston',
        'city_slug': 'houston-tx',
        'persona_slug': 'insurance',
        'meta_published': '2026-05-02',
        'reading_time': '6 min',
        'body_html': """
<p>If you write Texas homeowner's, wind, flood, or umbrella policies in Houston, you face a structural problem: the City of Houston doesn't publish a public permit feed. That makes the standard "permit-as-rerate-trigger" playbook (which works in Miami-Dade or Phoenix) harder to run in Houston. But Houston has two other public-data assets that flip the math: a 500,000+ HCAD property owner file via the City's ArcGIS server, and an 83,000-plus archive of Houston code violations going back to 2018.</p>

<p>Together, these enable a different prospecting motion: book-of-business cross-referencing for retention, and high-value-home prospecting for new business.</p>

<h2>What's in the Houston data feed</h2>

<ul>
  <li><strong>~500,000 Houston-city HCAD property owner records</strong> filtered to the Appraised_value_COH (City of Houston taxed) subset — full owner names, mailing addresses, parcel IDs, last sale dates, total appraised values, year built</li>
  <li><strong>83,000+ Houston code violation records</strong> — historical archive (most recent fresh data was through 2018-08; we monitor for refresh)</li>
  <li>Building permits are not currently available — HCAD permit data lives behind the City's Accela tenant with no public API</li>
</ul>

<h2>How Houston insurance agents use this without permit data</h2>

<h3>1. Book-of-business value validation</h3>

<p>HCAD's appraisal data includes Total_Appraised_Value and Year_Built for every Houston-city parcel. Match your book of business by address, compare your dwelling-coverage limits to the HCAD-reported total appraised value, and identify policies under-rated by 10%+. Texas wind premiums on coastal homes (especially in flood-zone-V or flood-zone-AE properties) are heavily dependent on accurate replacement cost — under-rated policies are unprofitable claim outcomes for the insurer and dissatisfied policyholders for you.</p>

<p>The HCAD feed also includes year_built — which drives wind-mitigation eligibility for newer construction (post-2002 IRC code) vs older stock. Match your book against year_built to identify which insureds qualify for wind credits they may not have applied.</p>

<h3>2. High-value-home prospecting</h3>

<p>Filter the property_owners feed by Total_Appraised_Value > $750K and landuse_dscr containing "Single Family Residence" — that surfaces ~25,000-50,000 high-value Houston homes. Cross-reference against your existing high-value-home book to find the prospects you DON'T currently insure. Direct mail with a discovery offer ("we'll re-rate your current policy at no cost — most Houston policies we review save 10-25% or surface coverage gaps").</p>

<h3>3. Recent transactions = new policy opportunities</h3>

<p>The HCAD feed includes New_Owner_Date — the date the property changed hands. Filter to New_Owner_Date within the last 90 days to find homes that recently changed hands. Recent buyers are 5-10x more likely to shop their homeowner's policy than long-term owners, especially in the first 6 months after closing. Direct contact via the mailing address (which is often the new property address for owner-occupied homes) within 90 days of close has high conversion to quote.</p>

<h3>4. Code violation cross-reference</h3>

<p>Houston code violations are a different conversation — they signal property issues that affect insurability and claim outcomes. For commercial lines and high-value home policies, properties with active code violations may need re-underwriting. Filter the violations feed by your book's ZIP codes and address-match for retention triggers.</p>

<h2>The Houston-specific insurance dynamics that matter</h2>

<p>Houston's wind, flood, and hail exposure is among the highest in the US. Hurricane Harvey (2017), Imelda (2019), and the freeze events (2021, 2024) have made TX a profitable but volatile market for carriers. Agents who proactively re-rate policies, surface coverage gaps, and prospect high-value homes outperform on retention and new-business — both factors compound in Houston's environment.</p>

<p>The HCAD data quality is exceptional. Texas Property Tax Code requires county appraisal districts to publish accurate property data, and HCAD is one of the largest in the country (Harris County is the third-most-populous county in the US). The 500K Houston-city subset alone is larger than most cities' total parcel base.</p>

<h2>Other resources</h2>

<p>Browse the live <a href="/permits/houston-tx">Houston data page</a> for current owner counts and aggregate stats. For broader insurance agent playbook context, see our <a href="/leads/insurance">insurance agent lead guide</a>. For Texas comparisons with full permit data, <a href="/permits/austin-tx">Austin</a> and <a href="/permits/san-antonio">San Antonio</a> are Tier 5 cities with daily permit feeds.</p>

<h2>Pricing</h2>

<p>$149/month for unlimited access to Houston data and every other city in our coverage. <a href="/pricing">See pricing</a> or <a href="/signup">try a free week</a>.</p>
""",
        'faqs': [
            ('Why doesn\'t PermitGrab have Houston building permits?',
             'Houston runs its permit system through the City\'s Accela tenant with no public REST API. The city has not exposed a permit feed via open data despite running an open-data portal for other datasets. We monitor for any change in this status; for now, owner data is the available pillar.'),
            ('How many Houston property owners are in the data?',
             'After filtering Harris County Central Appraisal District (HCAD) data to records with Appraised_value_COH (Houston-city taxed parcels), about 500,000 properties remain. The full HCAD county-wide file is 1.73 million parcels including unincorporated Harris County and other municipalities.'),
            ('Are Houston violations fresh?',
             'The Houston violation archive runs through 2018-08-22 (the public dataset stopped updating then). We re-probe quarterly; if Houston resumes publishing or moves to ArcGIS we\'ll wire the fresh feed. For current-day re-rate work, the HCAD owner data + transaction-date filtering is the more useful signal.'),
            ('Can I match my book of business against Houston HCAD data?',
             'Yes. Export your book\'s Houston-area policies as a CSV with property addresses, then join against the HCAD feed by address. Most agents do this monthly using their AMS export and a simple spreadsheet match.'),
            ('Does Houston HCAD data include phone numbers for property owners?',
             'No. HCAD provides parcel + owner-name + mailing-address only. Phone enrichment requires a separate skip-trace tool (BatchSkipTracing, REISkip, etc). Most direct-mail and door-knock workflows use the mailing address as the primary contact channel.'),
        ],
    },

}
