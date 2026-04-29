"""V467 (CODE_V467 SEO blog posts): five long-form, data-rich articles for the
ad-ready cities. Bodies are Jinja-renderable so live DB stats can be injected.

Companion to /blog/<slug> route in server.py. Keep article text out of server.py
so its 19K-line size doesn't grow another 600 lines.
"""

# Each entry's `body_template` is rendered with `stats=...` and `city_slug=...`
# context via render_template_string before being safe-injected into
# templates/blog_post_seo.html.

SEO_BLOG_POSTS = {
    'chicago-building-permits-2026': {
        'city_slug': 'chicago-il',
        'title': 'Chicago Building Permits 2026: Contractors, Violations & Property Data',
        'meta_description': 'Track active contractors pulling building permits in Chicago. Real permit counts, code violations, and property owner records updated daily.',
        'h1': 'Chicago Building Permits 2026',
        'subject': 'Chicago',
        'target_keyword': 'chicago building permits 2026',
        'body_template': """
<p>Every building project in Chicago starts with a permit. Whether you're renovating a kitchen, adding a deck, or doing a full gut rehab, the City of Chicago Department of Buildings requires a permit before work begins. This guide covers Chicago building permits in 2026 — plus how to search contractors, check code violations, and look up property owners using real, live data.</p>

<h2>What Work Requires a Building Permit in Chicago?</h2>
<p>A permit is required for new construction, major repairs, alterations, additions, renovations, and demolitions. You also need permits for fences, porches, decks, garages, and exterior work on Landmark buildings. The Department of Buildings issues specialty permits for elevators, demolition, sewer connections, signs, and water service.</p>
<p>Work that does NOT require a permit includes minor cosmetic repairs, painting, and certain small maintenance tasks. When in doubt, contact the Department of Buildings at 312-744-3449 or dob-info@cityofchicago.org.</p>

<h2>How to Get a Building Permit in Chicago</h2>
<p>Chicago offers two main pathways:</p>
<p>The <strong>Express Permit Program (EPP)</strong> is a fully web-enabled platform for most types of building repair work and small improvement projects. Qualifying projects like small rooftop solar PV systems (under 13.44kW) can get same-day approvals at reduced fees — $275 instead of $375, with the timeline cut from 30 days to one day.</p>
<p>For larger projects, you'll submit a plan-based building permit application through the city's Inspection, Permitting & Licensing Portal. You'll need architectural plans, details of the work, and the estimated project cost. The property owner must sign the application, though they can authorize a contractor or architect to submit it.</p>

<h2>How Many Contractors Are Pulling Permits in Chicago Right Now?</h2>
<p>Chicago is one of the most active construction markets in the country. PermitGrab tracks <strong>{{ '{:,}'.format(stats.profiles) }}</strong> active contractors pulling permits in Chicago — a live database updated daily from official permit records, not a static directory.</p>
<p>Of those, <strong>{{ '{:,}'.format(stats.phones) }}</strong> have direct phone numbers in our database, sourced from state license records and business registrations. If you're a building materials supplier, subcontractor, or home service company trying to connect with active contractors, this is the most current dataset available.</p>

<h2>Permits Filed in the Last 90 Days</h2>
<p>{{ '{:,}'.format(stats.permits_90d) }} building permits have been filed in Chicago in the last 90 days. Top permit types:</p>
{% if stats.top_permit_types %}
<table style="width: 100%; border-collapse: collapse; margin: 1rem 0;">
  <thead><tr style="border-bottom: 2px solid #dee2e6;"><th style="text-align: left; padding: 0.5rem;">Permit Type</th><th style="text-align: right; padding: 0.5rem;">Count</th></tr></thead>
  <tbody>
  {% for pt in stats.top_permit_types %}<tr style="border-bottom: 1px solid #eee;"><td style="padding: 0.5rem;">{{ pt.permit_type }}</td><td style="text-align: right; padding: 0.5rem;">{{ '{:,}'.format(pt.cnt) }}</td></tr>{% endfor %}
  </tbody>
</table>
{% endif %}

<h2>Most Active Contractors</h2>
{% if stats.top_contractors %}
<p>These contractors have pulled the most permits in the last 6 months:</p>
<table style="width: 100%; border-collapse: collapse; margin: 1rem 0;">
  <thead><tr style="border-bottom: 2px solid #dee2e6;"><th style="text-align: left; padding: 0.5rem;">Contractor</th><th style="text-align: right; padding: 0.5rem;">Permits</th></tr></thead>
  <tbody>
  {% for c in stats.top_contractors %}<tr style="border-bottom: 1px solid #eee;"><td style="padding: 0.5rem;">{{ c.business_name }}</td><td style="text-align: right; padding: 0.5rem;">{{ c.permits }}</td></tr>{% endfor %}
  </tbody>
</table>
<p style="font-size: 0.85rem; color: #666;">Phone numbers available for {{ '{:,}'.format(stats.phones) }} contractors. <a href="/permits/{{ city_slug }}">View the full database →</a></p>
{% endif %}

<h2>Chicago Code Violations</h2>
<p>The City of Chicago actively enforces building code compliance. PermitGrab tracks <strong>{{ '{:,}'.format(stats.violations) }}</strong> Chicago code violations on record. Each one represents a property where work needs to be done.</p>
<p>For contractors, this is a built-in lead list — properties that legally must be repaired. For real estate investors, properties with active violations often indicate motivated sellers or below-market opportunities.</p>
<p><a href="/permits/{{ city_slug }}">Search Chicago violations by address →</a></p>

<h2>Chicago Property Owner Data</h2>
<p>We've matched <strong>{{ '{:,}'.format(stats.owners) }}</strong> property owners to addresses in our Chicago database. When a permit is filed or a violation is issued, you can see who owns the property — including their mailing address from Cook County records.</p>

<h2>Solar Permits in Chicago</h2>
<p>Chicago's Easy Permit Process makes solar relatively straightforward. Small rooftop PV systems under 13.44kW qualify for expedited review — same-day approval at $275. You'll need a licensed electrical contractor with an Illinois Commerce Commission Distributed Generation Installer Certification. Structural drawings must be prepared by an Illinois-licensed architect or structural engineer.</p>
<p><strong>Important for 2026:</strong> the federal 30% residential solar tax credit was terminated by the One Big Beautiful Bill Act signed July 4, 2025. New installations completed in 2026 and beyond are no longer eligible. The Cook County Sun and Save program is still available through August 31, 2026, offering bulk-purchase discounts for single-family and small multi-family residences.</p>

<h2>Frequently Asked Questions</h2>
<h3>How often is the data updated?</h3>
<p>Our system pulls new permit data every day from official city records. The most recent permit in our database was filed on {{ stats.newest_permit }}.</p>
<h3>Where does the data come from?</h3>
<p>All data comes from official Chicago open data portals — the same records available at city hall, organized and searchable.</p>
<h3>Can I search by contractor name?</h3>
<p>Yes. Search by contractor name, property address, permit type, or date range on the <a href="/permits/{{ city_slug }}">Chicago permits page</a>.</p>
""",
    },

    'miami-dade-solar-permits-2026': {
        'city_slug': 'miami-dade-county',
        'title': 'Miami-Dade Solar Permits 2026: HVHZ, Insurance & Cost Guide',
        'meta_description': 'Complete 2026 guide to Miami-Dade solar permits. HVHZ requirements, NOA certifications, insurance impact, and live contractor data.',
        'h1': 'Miami-Dade Solar Permits 2026',
        'subject': 'Miami-Dade',
        'target_keyword': 'miami dade solar permit',
        'body_template': """
<p>Miami-Dade County has some of the strictest solar permitting requirements in the country, thanks to its High Velocity Hurricane Zone (HVHZ) designation. If you're a homeowner considering solar, a contractor installing systems, or a solar company prospecting in South Florida, this guide covers every requirement, cost, and insurance implication for 2026.</p>

<h2>What is the HVHZ and Why Does It Matter for Solar?</h2>
<p>The High Velocity Hurricane Zone is a Florida Building Code designation covering Miami-Dade and Broward counties. It enforces hurricane-resistant construction standards that go significantly beyond the rest of Florida. For solar installations, this means every component must carry a Miami-Dade Notice of Acceptance (NOA) — a local certification confirming the product passed HVHZ wind tunnel testing.</p>
<p>This includes individual panel models, racking systems, clamps, and flashing. Each needs a separate NOA. A solar installer that's never worked in HVHZ will frequently submit a plan that gets rejected on the first review because at least one component lacks a current NOA.</p>

<h2>How to Get a Solar Permit in Miami-Dade</h2>
<p>Solar PV projects require both a structural permit and an electrical permit. Miami-Dade allows electronic submission through their Portal, routing documentation to multiple departments simultaneously for faster review. Projects using renewable energy qualify for Green Building Expedited review under Section 8-6 of the County Code.</p>
<p>Standard review runs 10-15 business days. Most installers receive approval in 2-4 weeks with complete NOA documentation and a clean plan set. Projects using pre-approved flashing details benefit from reduced permit fees.</p>

<h2>Do Solar Panels Affect Your Homeowner Insurance?</h2>
<p>Yes — and most homeowners miss this. Adding solar panels almost always increases your premium by 10-20% because the replacement cost of the home rises. Florida also requires a Personal Liability Policy (PLP) of at least $1 million for Tier 2 solar systems — that runs about $500/year.</p>
<p>The bigger risk: if you install solar without notifying your insurer and later file a claim for panel damage, the insurer can deny the claim AND non-renew your policy for "material misrepresentation." Always notify your insurance company before installation begins.</p>

<h2>Miami-Dade by the Numbers</h2>
<p>PermitGrab tracks the Miami-Dade construction market in real time:</p>
<ul>
  <li><strong>{{ '{:,}'.format(stats.profiles) }}</strong> active contractors pulling building permits</li>
  <li><strong>{{ '{:,}'.format(stats.phones) }}</strong> with direct phone numbers</li>
  <li><strong>{{ '{:,}'.format(stats.violations) }}</strong> open code violations tracked from county enforcement</li>
  <li><strong>{{ '{:,}'.format(stats.owners) }}</strong> property owners matched to permit addresses</li>
</ul>
<p>This is the most comprehensive dataset available for anyone selling into the Miami-Dade construction market — solar companies, roofers, HVAC contractors, insurance agents, building material suppliers. Data comes from official Miami-Dade County records, updated daily.</p>

<h2>Permits Filed in the Last 90 Days</h2>
<p>{{ '{:,}'.format(stats.permits_90d) }} building permits filed in Miami-Dade in the last 90 days. Top permit types:</p>
{% if stats.top_permit_types %}
<table style="width: 100%; border-collapse: collapse; margin: 1rem 0;">
  <thead><tr style="border-bottom: 2px solid #dee2e6;"><th style="text-align: left; padding: 0.5rem;">Permit Type</th><th style="text-align: right; padding: 0.5rem;">Count</th></tr></thead>
  <tbody>
  {% for pt in stats.top_permit_types %}<tr style="border-bottom: 1px solid #eee;"><td style="padding: 0.5rem;">{{ pt.permit_type }}</td><td style="text-align: right; padding: 0.5rem;">{{ '{:,}'.format(pt.cnt) }}</td></tr>{% endfor %}
  </tbody>
</table>
{% endif %}

<h2>Finding Solar Contractors in Miami-Dade</h2>
<p>Solar United Neighbors runs the Miami-Dade Solar Purchasing Cooperative, helping homeowners get lower pricing through bulk equipment purchasing. But if you're a solar company looking for homeowner leads, PermitGrab shows you which properties are pulling renovation permits right now — homeowners already spending money on their house, making them ideal candidates for solar.</p>
<p>With <strong>{{ '{:,}'.format(stats.owners) }}</strong> property owners in the database, matched to permit and violation records, you can identify properties actively under renovation. A homeowner who just pulled a roof permit is your best solar prospect. <a href="/permits/{{ city_slug }}">Browse Miami-Dade contractors and permits →</a></p>

<h2>Frequently Asked Questions</h2>
<h3>How long does Miami-Dade solar permitting take?</h3>
<p>Standard review runs 10-15 business days; complete applications with full NOA documentation typically clear in 2-4 weeks. Most recent Miami-Dade permit in our system was filed on {{ stats.newest_permit }}.</p>
<h3>What's a Tier 2 PLP insurance requirement?</h3>
<p>Florida requires solar systems above the Tier 1 threshold to carry a $1 million Personal Liability Policy. Annual cost is approximately $500.</p>
<h3>Are federal solar tax credits still available in 2026?</h3>
<p>No. The 30% federal residential solar tax credit was repealed by the One Big Beautiful Bill Act effective for 2026 installations. Florida state and local incentives still apply.</p>
""",
    },

    'phoenix-code-violations-2026': {
        'city_slug': 'phoenix-az',
        'title': 'Phoenix Code Violations 2026: Fines, Deadlines & How to Resolve',
        'meta_description': 'Phoenix code violations carry $100-$2,500 fines and 30-day deadlines. Fine schedule, resolution steps, and live violation data.',
        'h1': 'Phoenix Code Violations 2026',
        'subject': 'Phoenix',
        'target_keyword': 'phoenix code violations',
        'body_template': """
<p>Getting a code violation notice from the City of Phoenix can feel overwhelming. This guide explains exactly what happens, how much it costs, and how to resolve it — plus how contractors and investors use violation data to find business opportunities.</p>

<h2>What Happens When You Get a Code Violation in Phoenix?</h2>
<p>The city issues a notice and gives the property owner a set number of days to correct the issue — typically 30 days. If the violation isn't fixed within that window, the city has authority to abate the problem themselves and place a lien against the property for the costs.</p>

<h2>Phoenix Code Violation Fines</h2>
<p>Fines escalate with repeat offenses within 36 months:</p>
<ul>
  <li><strong>First violation:</strong> $100 to $2,500</li>
  <li><strong>Second violation:</strong> minimum $250</li>
  <li><strong>Third violation:</strong> minimum $500</li>
</ul>
<p>Each day the violation continues counts as a separate offense. A $100/day minimum violation left unresolved for a month could result in a $3,000 fine — plus the cost of the actual repair.</p>

<h2>How to Fix a Code Violation in Phoenix</h2>
<p>Read the violation notice carefully twice. Call the code enforcement officer to discuss what's required — get their cell phone number and email. Ask for a time extension if you need it. Document everything. Get the work done before your court date to minimize fines.</p>
<p>If the work requires a contractor, make sure they're licensed through the Arizona Registrar of Contractors (ROC). Unpermitted repair work on a code violation can create additional violations.</p>

<h2>Phoenix by the Numbers</h2>
<p>PermitGrab tracks <strong>{{ '{:,}'.format(stats.violations) }}</strong> open code violations in Phoenix right now. Each one represents a property where work must be done — by law.</p>
<p>We also track:</p>
<ul>
  <li><strong>{{ '{:,}'.format(stats.profiles) }}</strong> active contractors pulling permits</li>
  <li><strong>{{ '{:,}'.format(stats.phones) }}</strong> with direct phone numbers</li>
  <li><strong>{{ '{:,}'.format(stats.owners) }}</strong> property owners matched to addresses (Maricopa County)</li>
</ul>

<h2>Why This Matters for Contractors and Investors</h2>
<p><strong>For contractors:</strong> every code violation is a property that needs your services. Roofing, electrical, plumbing, structural repair, property maintenance — these are properties where the city has said "fix this or else." That's not a cold lead. It's a legally mandated repair.</p>
<p><strong>For real estate investors:</strong> properties with active violations often indicate distressed ownership situations. The combination of violation data, property owner information, and assessed values gives you a complete picture of investment opportunities.</p>
<p><strong>For solar installers:</strong> Phoenix offers instant-permits solar through SolarAPP+ as of January 1, 2026. Arizona's 25% state solar tax credit (up to $1,000) is still active. Properties pulling renovation permits are already spending money on their house — add solar to the project.</p>

<h2>Solar Permits in Phoenix</h2>
<p>Arizona's HB2301 mandates all municipalities adopt instant permitting for home power installations as of January 2026. Phoenix uses SolarAPP+ for eligible residential PV systems. Maricopa County charges $300 for roof-mounted residential solar permits, including all inspections.</p>

<h2>Permits Filed in the Last 90 Days</h2>
<p>{{ '{:,}'.format(stats.permits_90d) }} permits filed in Phoenix in the last 90 days. Top types:</p>
{% if stats.top_permit_types %}
<table style="width: 100%; border-collapse: collapse; margin: 1rem 0;">
  <thead><tr style="border-bottom: 2px solid #dee2e6;"><th style="text-align: left; padding: 0.5rem;">Permit Type</th><th style="text-align: right; padding: 0.5rem;">Count</th></tr></thead>
  <tbody>
  {% for pt in stats.top_permit_types %}<tr style="border-bottom: 1px solid #eee;"><td style="padding: 0.5rem;">{{ pt.permit_type }}</td><td style="text-align: right; padding: 0.5rem;">{{ '{:,}'.format(pt.cnt) }}</td></tr>{% endfor %}
  </tbody>
</table>
{% endif %}
<p><a href="/permits/{{ city_slug }}">Search Phoenix permits and contractors →</a></p>

<h2>Frequently Asked Questions</h2>
<h3>How much can a Phoenix code violation cost?</h3>
<p>$100 to $2,500 per violation, with each day counting as a separate offense. Repeats within 36 months escalate the minimum to $250 and $500.</p>
<h3>How long do I have to fix a Phoenix code violation?</h3>
<p>Typically 30 days from the notice. After that the city can abate and lien the property for costs.</p>
<h3>Where can I see currently open Phoenix violations?</h3>
<p>Search by address on the <a href="/permits/{{ city_slug }}">Phoenix permits page</a>. Most recent permit in our system: {{ stats.newest_permit }}.</p>
""",
    },

    'san-antonio-building-permits-2026': {
        'city_slug': 'san-antonio-tx',
        'title': 'San Antonio Building Permits 2026: Contractors, Violations, Property Data',
        'meta_description': 'Search San Antonio building permits, find licensed contractors, and view 5,000+ open code violations. Live data updated daily.',
        'h1': 'San Antonio Building Permits 2026',
        'subject': 'San Antonio',
        'target_keyword': 'san antonio building permits',
        'body_template': """
<p>San Antonio is one of the fastest-growing cities in Texas, and its construction market reflects that. With <strong>{{ '{:,}'.format(stats.profiles) }}</strong> active contractors pulling permits and <strong>{{ '{:,}'.format(stats.violations) }}</strong> open code violations, there's significant building activity — and significant opportunity.</p>

<h2>Building Permits in San Antonio</h2>
<p>Building permits are issued by the Development Services Department (DSD) at 1901 South Alamo. You can apply in person or through the online portal. For questions, call (210) 207-1111.</p>
<p>New permit data — both applications submitted and permits issued — is available on the City's Open Data SA website. PermitGrab pulls this data automatically and organizes it by contractor, address, and permit type.</p>

<h2>How to Find a Licensed Contractor in San Antonio</h2>
<p>All contractors must register with the City of San Antonio before pulling permits. You can verify registration through the Contractors Connect Portal. For state-level verification of electrical and mechanical contractors, use the Texas Department of Licensing and Regulation (TDLR) Active License Data Search. For plumbing, check the Texas State Board of Plumbing Examiners (TSBPE).</p>
<p>Check the monthly Canceled, Suspended, and Registration Appeals Report to avoid contractors with current sanctions.</p>
<p>PermitGrab tracks <strong>{{ '{:,}'.format(stats.profiles) }}</strong> active contractors in San Antonio, with <strong>{{ '{:,}'.format(stats.phones) }}</strong> phone numbers. This isn't a static directory — it's updated daily based on actual permit filings, so you're seeing contractors who are actively working right now.</p>

<h2>Permits Filed in the Last 90 Days</h2>
<p>{{ '{:,}'.format(stats.permits_90d) }} San Antonio permits filed in the last 90 days. Top permit types:</p>
{% if stats.top_permit_types %}
<table style="width: 100%; border-collapse: collapse; margin: 1rem 0;">
  <thead><tr style="border-bottom: 2px solid #dee2e6;"><th style="text-align: left; padding: 0.5rem;">Permit Type</th><th style="text-align: right; padding: 0.5rem;">Count</th></tr></thead>
  <tbody>
  {% for pt in stats.top_permit_types %}<tr style="border-bottom: 1px solid #eee;"><td style="padding: 0.5rem;">{{ pt.permit_type }}</td><td style="text-align: right; padding: 0.5rem;">{{ '{:,}'.format(pt.cnt) }}</td></tr>{% endfor %}
  </tbody>
</table>
{% endif %}

<h2>Most Active Contractors</h2>
{% if stats.top_contractors %}
<table style="width: 100%; border-collapse: collapse; margin: 1rem 0;">
  <thead><tr style="border-bottom: 2px solid #dee2e6;"><th style="text-align: left; padding: 0.5rem;">Contractor</th><th style="text-align: right; padding: 0.5rem;">Permits</th></tr></thead>
  <tbody>
  {% for c in stats.top_contractors %}<tr style="border-bottom: 1px solid #eee;"><td style="padding: 0.5rem;">{{ c.business_name }}</td><td style="text-align: right; padding: 0.5rem;">{{ c.permits }}</td></tr>{% endfor %}
  </tbody>
</table>
{% endif %}

<h2>San Antonio's Open Code Violations</h2>
<p>San Antonio currently has <strong>{{ '{:,}'.format(stats.violations) }}</strong> open code violations on record. Each represents a property where work needs to be done — structural repairs, property maintenance, electrical upgrades, plumbing fixes.</p>
<p>For contractors, these are ready-made leads. The property owner either needs to hire someone to fix the violation or face escalating fines. For investors, a property with code violations may signal a motivated seller.</p>

<h2>Solar Permits in San Antonio</h2>
<p>Solar installations require both a building/electrical permit from DSD AND a utility interconnection agreement from CPS Energy — these are separate processes. DSD review takes about 2 weeks. CPS Energy interconnection can take 2-12 weeks after installation. Submit to both simultaneously.</p>
<p>Permit fees range from $275-$525. Texas allows homeowners to install their own solar, but hiring a licensed contractor is recommended for permitting compliance. As of September 2025, new consumer protection laws (SB 1036 & SB 1697) require solar retailer registration with TDLR.</p>

<h2>Property Owner Data</h2>
<p>We've matched <strong>{{ '{:,}'.format(stats.owners) }}</strong> property owners in San Antonio to permit addresses using Bexar County assessor records. When a permit is filed or a violation is issued, you can see who owns the property.</p>
<p><a href="/permits/{{ city_slug }}">Browse San Antonio contractors and permits →</a></p>

<h2>Frequently Asked Questions</h2>
<h3>How do I check if a contractor is licensed in San Antonio?</h3>
<p>Use the Contractors Connect Portal for city registration, TDLR for electrical/mechanical, and TSBPE for plumbing.</p>
<h3>How current is this data?</h3>
<p>Updated daily from City of San Antonio open data feeds. Most recent permit in our system was filed on {{ stats.newest_permit }}.</p>
<h3>Where can I see all open San Antonio violations?</h3>
<p>Search by address on the <a href="/permits/{{ city_slug }}">San Antonio permits page</a>.</p>
""",
    },

    'nyc-building-violations-2026': {
        'city_slug': 'new-york-city',
        'title': 'NYC Building Violations 2026: DOB & HPD Search Guide',
        'meta_description': 'Search NYC DOB and HPD building violations by address. Class A/B/C explained, BIS vs DOB NOW, and live property data.',
        'h1': 'NYC Building Violations 2026',
        'subject': 'NYC',
        'target_keyword': 'nyc building violations',
        'body_template': """
<p>New York City has two separate violation databases that every property owner, contractor, and investor needs to understand: the Department of Buildings (DOB) and Housing Preservation & Development (HPD). This guide shows how to search both, what the violations mean, and how to use this data.</p>

<h2>DOB vs. HPD: What's the Difference?</h2>
<p>DOB violations cover building code compliance — construction defects, unpermitted work, unsafe conditions, and building maintenance. HPD violations cover housing maintenance — heat and hot water issues, lead paint, pest infestations, and tenant habitability.</p>
<p>Both are public record and free to search. But they live in different systems, making it easy to miss violations if you only check one.</p>

<h2>How to Search NYC Building Violations</h2>
<p>NYC operates two DOB systems: BIS (Buildings Information System) holds historical records from 1985-2020. DOB NOW holds new filings from 2018 forward. A single building can have records in both systems. For HPD violations, search at hpdonline.nyc.gov.</p>
<p>You can search by address and find DOB violations and permits, HPD housing violations by class (A/B/C), FDNY fire safety records, 311 complaints, and stop work orders.</p>

<h2>HPD Violation Classes</h2>
<ul>
  <li><strong>Class A:</strong> Non-hazardous — conditions that don't threaten safety but need correction</li>
  <li><strong>Class B:</strong> Hazardous — must be corrected within 30 days</li>
  <li><strong>Class C:</strong> Immediately hazardous — must be corrected within 24 hours (no heat, no hot water, lead paint, vermin)</li>
</ul>
<p>Rent-impairing violations are flagged separately. These can give tenants grounds to withhold rent until repairs are made.</p>

<h2>NYC by the Numbers</h2>
<p>PermitGrab tracks the NYC construction market comprehensively:</p>
<ul>
  <li><strong>{{ '{:,}'.format(stats.profiles) }}</strong> active contractors pulling DOB permits</li>
  <li><strong>{{ '{:,}'.format(stats.phones) }}</strong> with direct phone numbers (growing weekly)</li>
  <li><strong>{{ '{:,}'.format(stats.violations) }}</strong> HPD and DOB violations tracked across all five boroughs</li>
  <li><strong>{{ '{:,}'.format(stats.owners) }}</strong> property owners matched via PLUTO database</li>
</ul>

<h2>Permits Filed in the Last 90 Days</h2>
<p>{{ '{:,}'.format(stats.permits_90d) }} DOB permits filed in NYC in the last 90 days. Top permit types:</p>
{% if stats.top_permit_types %}
<table style="width: 100%; border-collapse: collapse; margin: 1rem 0;">
  <thead><tr style="border-bottom: 2px solid #dee2e6;"><th style="text-align: left; padding: 0.5rem;">Permit Type</th><th style="text-align: right; padding: 0.5rem;">Count</th></tr></thead>
  <tbody>
  {% for pt in stats.top_permit_types %}<tr style="border-bottom: 1px solid #eee;"><td style="padding: 0.5rem;">{{ pt.permit_type }}</td><td style="text-align: right; padding: 0.5rem;">{{ '{:,}'.format(pt.cnt) }}</td></tr>{% endfor %}
  </tbody>
</table>
{% endif %}

<h2>Solar Permits in NYC</h2>
<p>NYC solar requires two separate permits: an electrical permit issued to a NYC-Licensed Master Electrician (LME) and a construction work permit issued to a NYC-licensed General Contractor. Applications are filed through DOB NOW. Complete applications are typically reviewed in 5-10 business days.</p>
<p>The federal 30% tax credit is gone for 2026 installations, but all New York state and local incentives remain: 25% state tax credit, NY-Sun rebates, NYC property tax abatement, the RPT 487 property tax exemption (15-year exemption on added home value), and net metering.</p>

<h2>Who Uses This Data?</h2>
<p><strong>Restoration contractors</strong> use violation data to find buildings that need immediate work. <strong>Solar companies</strong> use permit data to find homeowners already investing in their properties. <strong>Real estate investors</strong> use the combination of violation data, property owner records, and assessed values to identify distressed assets. <strong>Insurance adjusters</strong> use permit records to verify construction claims.</p>
<p><a href="/permits/{{ city_slug }}">Browse NYC contractors, permits, and violations →</a></p>

<h2>Frequently Asked Questions</h2>
<h3>What's the difference between BIS and DOB NOW?</h3>
<p>BIS holds NYC building records from 1985-2020. DOB NOW holds filings from 2018 forward. A building may have records in both systems.</p>
<h3>Are NYC violation searches free?</h3>
<p>Yes. Both DOB and HPD violation searches on city portals are free. PermitGrab consolidates them into a single searchable view.</p>
<h3>How current is this data?</h3>
<p>Updated daily from NYC Open Data. Most recent permit in our system was filed on {{ stats.newest_permit }}.</p>
""",
    },
}
