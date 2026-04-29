"""V469 (CODE_V467 FAQ blog posts): six buyer-segment FAQ pages targeting
solar / insurance / real-estate-investor / contractor / supplier / general-overview
keywords. Each post has FAQ schema (FAQPage JSON-LD) driven by the per-post
faqs list. Stats grid uses global DB totals (profiles, phones, violations,
owners) since these aren't city-specific.

Companion to /blog/<slug> route in server.py — the route checks SEO_BLOG_POSTS
first, then FAQ_BLOG_POSTS. Both use templates/blog_post_seo.html.
"""

# Each entry has:
#   title, meta_description, h1, subject (plain-language label for breadcrumbs),
#   target_keyword, faqs (list of {q, a} for the FAQ schema; q/a strings can
#   include {{ stats.X }} too).
# V471 PR1: body content lives in templates/blog/faq/<slug>.html — rendered
# via render_template() with `stats=...` and `post=...` context.

FAQ_BLOG_POSTS = {
    'solar-leads-from-building-permits': {
        'subject': 'Solar Leads',
        'title': 'Solar Leads from Building Permits: FAQ for Solar Installers',
        'meta_description': 'How solar companies use building permit data to find homeowners doing renovations. FAQ guide to permit-based solar leads in Chicago, Phoenix, Miami & more.',
        'h1': 'Solar Leads from Building Permits: FAQ',
        'target_keyword': 'solar leads from building permits',
        'faqs': [
            {
                'q': 'What are permit-based solar leads?',
                'a': 'When a homeowner pulls a building permit for a roof replacement, addition, or renovation, that\'s a strong signal they\'re actively spending money on their home — making them 5-10x more likely to consider solar than a cold list. PermitGrab captures these permits in real-time from city open data portals across {{ stats.cities }} cities, giving you the property owner\'s name, address, and project details.',
            },
            {
                'q': 'How do building permits help solar companies find customers?',
                'a': 'Roof replacement permits = perfect solar timing (new roof matches a 25-year solar warranty). Home addition permits = expanding home, growing energy bill. Electrical upgrade permits = often pre-cursor to PV installation. Plus our property-value data lets you target homes worth $300K+ where solar ROI makes sense.',
            },
            {
                'q': 'How fresh is the permit data?',
                'a': 'Updated every 30 minutes from city open data portals. Most permits appear in our system within 24-48 hours of being filed. Compare to aged-list lead-gen services where the permit happened weeks or months ago and the homeowner has already chosen a contractor.',
            },
            {
                'q': 'Do you provide homeowner contact information?',
                'a': 'Yes — property owner names from permit records and county assessor data, mailing addresses for direct mail, plus contractor phone numbers from state licensing databases (15,972 phones across our database).',
            },
            {
                'q': 'How much does it cost?',
                'a': '$149/month for unlimited access to permits, contractor profiles, property owner records, and code violations across every city we cover. Free trial available, no credit card to browse. Compare to solar lead costs of $20-50 per shared lead — at $149/mo you can pull thousands.',
            },
            {
                'q': 'Can I filter by permit type?',
                'a': 'Yes — filter by roofing, electrical, addition, renovation, etc. Most solar teams focus on roofing and electrical permits as their highest-yield triggers.',
            },
            {
                'q': 'How is this different from buying solar leads?',
                'a': 'You\'re not getting shared leads sold to 5 other companies. You\'re mining the public permit data yourself — exclusive, real-time, and you see the actual project details (permit type, value, date) instead of just a name and phone.',
            },
        ],
    },

    'insurance-leads-from-building-permits': {
        'subject': 'Insurance Leads',
        'title': 'Insurance Leads from Building Permits: FAQ for Insurance Agents',
        'meta_description': 'How insurance agents use building permit data to find homeowners with new renovations, code violations, and property changes. FAQ guide to permit-based insurance leads.',
        'h1': 'Insurance Leads from Building Permits: FAQ',
        'target_keyword': 'insurance leads from building permits',
        'faqs': [
            {
                'q': 'Why are building permits valuable for insurance agents?',
                'a': 'Every renovation changes a home\'s replacement cost and coverage needs. A $50K kitchen remodel that isn\'t reported to the carrier becomes a coverage gap and a denied claim later. Permits show you exactly who just spent money on what — these homeowners need a policy review.',
            },
            {
                'q': 'What types of permits should insurance agents look for?',
                'a': 'Roof replacement (wind/hail coverage), home additions (replacement cost goes up with square footage), electrical upgrades (lower fire risk = potential discount), plumbing (water damage risk reduction), and pool permits (liability coverage trigger).',
            },
            {
                'q': 'How do code violations help insurance agents?',
                'a': 'Properties with open code violations are higher risk, often underinsured, and frequently signal owners who haven\'t reviewed their policy in years. PermitGrab tracks {{ "{:,}".format(stats.violations) }} violations across {{ stats.violation_cities }} cities — perfect for proactive outreach before a claim happens.',
            },
            {
                'q': 'Do you provide property owner information?',
                'a': 'Yes — owner names, property addresses, and assessed values from county assessor records ({{ "{:,}".format(stats.owners) }} owners). Filter by assessed value to target high-value homes where premium upside is greatest.',
            },
            {
                'q': 'What cities do you cover?',
                'a': '{{ stats.cities }} cities collecting permits, with property owner data in {{ stats.owner_cities }} cities and violations in {{ stats.violation_cities }} cities. Top markets are listed below.',
            },
            {
                'q': 'How much does it cost?',
                'a': '$149/month — unlimited access. Insurance lead lists run $15-30 per record. If you close ONE renovation policy from permit data, the platform pays for itself for years.',
            },
            {
                'q': 'Can I use this for commercial insurance too?',
                'a': 'Yes — commercial building permits, tenant buildouts, and business renovation permits all show up in the same database. Contractor profiles include business names, license numbers, and trade categories.',
            },
        ],
    },

    'real-estate-investor-leads-building-permits': {
        'subject': 'Real Estate Investor Leads',
        'title': 'Building Permit Data for Real Estate Investors: FAQ Guide',
        'meta_description': 'How real estate investors use building permits, code violations, and property owner data to find deals. FAQ guide to permit-based real estate investing intelligence.',
        'h1': 'Building Permit Data for Real Estate Investors: FAQ',
        'target_keyword': 'real estate investor leads building permits',
        'faqs': [
            {
                'q': 'How do real estate investors use building permit data?',
                'a': 'To track where renovation activity is happening (gentrifying neighborhoods), find properties with code violations (motivated sellers), identify fix-and-flip projects by competitor investors, and see which contractors are most active in your target neighborhoods.',
            },
            {
                'q': 'What are "motivated seller leads" from code violations?',
                'a': 'Properties with open code violations often have owners who can\'t afford the repairs and may be willing to sell at a discount. PermitGrab tracks {{ "{:,}".format(stats.violations) }} violations across {{ stats.violation_cities }} cities — filter by violation type, date, and neighborhood to build a hyperlocal motivated-seller list.',
            },
            {
                'q': 'How can I find off-market deals using permit data?',
                'a': 'Properties with expired permits = stalled projects = potential deals. Properties with multiple violations = owner overwhelmed. New permits in transitioning neighborhoods = gentrification signal. Combined with property owner names + assessed values, you can target the right price range with the right pitch.',
            },
            {
                'q': 'Do you provide property owner contact information?',
                'a': 'Yes — owner names and mailing addresses from county assessor records, plus assessed values for filtering. Available in {{ stats.owner_cities }} cities ({{ "{:,}".format(stats.owners) }} owner records).',
            },
            {
                'q': 'Can I track competitor investor activity?',
                'a': 'Yes — see which contractors and investors are pulling permits, track renovation activity by neighborhood, and identify which blocks are getting the most investment. Top contractor leaderboards reveal who\'s active where.',
            },
            {
                'q': 'How much does it cost?',
                'a': '$149/month for permits + violations + property owners — all four data pillars. Compare to motivated-seller lead lists at $500-2000/month or skip-trace services at $0.10-0.50 per record.',
            },
            {
                'q': 'What cities have the best data for investors?',
                'a': 'Best coverage in Chicago, Miami-Dade, Phoenix, San Antonio, and NYC — each has all four data pillars (permits, contractor phones, violations, owners). Plus 80+ other cities with most pillars.',
            },
        ],
    },

    'contractor-leads-from-building-permits': {
        'subject': 'Contractor Leads',
        'title': 'Contractor Leads from Building Permits: FAQ for Home Service Companies',
        'meta_description': 'How roofing, HVAC, plumbing, and electrical companies use building permit data to find contractor leads and subcontracting opportunities. Complete FAQ guide.',
        'h1': 'Contractor Leads from Building Permits: FAQ',
        'target_keyword': 'contractor leads from building permits',
        'faqs': [
            {
                'q': 'What are permit-based contractor leads?',
                'a': 'When a general contractor pulls a permit, they often need subcontractors. PermitGrab shows you who is pulling permits, what type of work, and where — so you can pitch your services as a sub or target the homeowner directly.',
            },
            {
                'q': 'How do roofing companies use permit data?',
                'a': 'Track roof replacement permits to find homeowners mid-project. See which GCs are doing the most roofing work and pitch them as a sub. Monitor new construction permits — every new build needs a roofer. Code violations for roof issues = homeowners who need help now.',
            },
            {
                'q': 'How do HVAC companies use permit data?',
                'a': 'Mechanical/HVAC permits show who needs equipment and installation. New construction permits = HVAC rough-in. Renovation permits often include HVAC upgrades. Energy code violations = upgrade opportunities.',
            },
            {
                'q': 'How do plumbing companies use permit data?',
                'a': 'Plumbing permits for new construction and renovations, water heater replacements, bathroom and kitchen remodels (every one needs a plumber), and plumbing code violations = immediate need.',
            },
            {
                'q': 'Do you provide contractor phone numbers?',
                'a': 'Yes — {{ "{:,}".format(stats.phones) }} phone numbers across our database, sourced from state licensing records and web enrichment. Top cities: Miami-Dade 3,980, San Antonio 3,836, Chicago 3,499, Phoenix 1,083, NYC 793.',
            },
            {
                'q': 'Can I filter by trade?',
                'a': 'Yes — filter by electrical, plumbing, HVAC, roofing, general construction, demolition, etc. Each city page has a trade breakdown so you can see how the contractor base is distributed.',
            },
            {
                'q': 'How much does it cost?',
                'a': '$149/month — unlimited access to all cities, all trades. Compare to Angi or HomeAdvisor at $20-75 per lead. 10 leads/month at $30 each = $300; PermitGrab gives you unlimited access to thousands.',
            },
        ],
    },

    'construction-supplier-leads-building-permits': {
        'subject': 'Construction Supplier Leads',
        'title': 'Construction Supplier Leads from Building Permits: FAQ',
        'meta_description': 'How lumber yards, concrete suppliers, and building material distributors use permit data to find new construction projects and contractor customers.',
        'h1': 'Construction Supplier Leads from Building Permits: FAQ',
        'target_keyword': 'construction supplier leads building permits',
        'faqs': [
            {
                'q': 'How do building material suppliers use permit data?',
                'a': 'Track new construction permits for material packages. See which contractors are most active (your best repeat customers). Monitor permit values to estimate material needs. Target neighborhoods with the most construction activity for outside-sales rep routing.',
            },
            {
                'q': 'What types of permits matter for suppliers?',
                'a': 'New construction (full material package), additions and expansions (framing, roofing, concrete), renovation permits (finishes, fixtures, flooring), and commercial buildouts (high-volume orders).',
            },
            {
                'q': 'Can I see which contractors are pulling the most permits?',
                'a': 'Yes — top contractor leaderboards for every city, with contact info including {{ "{:,}".format(stats.phones) }} phone numbers across the database. Track contractor activity over time to identify your best prospects before they call you for quotes.',
            },
            {
                'q': 'How do I use this for outside sales?',
                'a': 'Route reps by neighborhood — see where permits are clustered. Know what projects are coming BEFORE the contractor calls for quotes. Show up with the right product catalog for their project type. Property owner data identifies the decision maker.',
            },
            {
                'q': 'What cities do you cover?',
                'a': '{{ stats.cities }} cities with active permit data. Top markets are Chicago (8,692 contractors), Miami-Dade, Phoenix, San Antonio, and NYC.',
            },
            {
                'q': 'How much does it cost?',
                'a': '$149/month — unlimited contractor data, permit data, property owner info. One new contractor relationship easily pays for a year of access.',
            },
            {
                'q': 'Can I export data to my CRM?',
                'a': 'Yes — Pro plans include CSV export of permits, contractors, and property owners. Filter the view, export, and load into your CRM or sales territory tool.',
            },
        ],
    },

    'lead-generation-building-permits-faq': {
        'subject': 'Lead Generation FAQ',
        'title': 'Lead Generation from Building Permits: The Complete FAQ Guide (2026)',
        'meta_description': 'Everything you need to know about using building permit data for lead generation. Covers solar, insurance, real estate, contractor, and supplier leads.',
        'h1': 'Lead Generation from Building Permits: The Complete FAQ',
        'target_keyword': 'lead generation building permits',
        'faqs': [
            {
                'q': 'What is building permit lead generation?',
                'a': 'Building permits are public records filed when construction work begins. They contain contractor names, property addresses, project types, and dollar values. Smart businesses mine this data to find warm leads — people who are actively spending money right now.',
            },
            {
                'q': 'Who uses building permit data for leads?',
                'a': 'Solar installers (target homeowners doing roof work), insurance agents (find coverage gaps from renovations), real estate investors (find motivated sellers via code violations), home service companies (find subcontracting opportunities), and building material suppliers (find active contractors and projects).',
            },
            {
                'q': 'What data is included?',
                'a': 'Permits ({{ "{:,}".format(stats.permits) }} records, last 180 days), contractor profiles ({{ "{:,}".format(stats.profiles) }} businesses with {{ "{:,}".format(stats.phones) }} phones), property owners ({{ "{:,}".format(stats.owners) }} owners across {{ stats.owner_cities }} cities), and code violations ({{ "{:,}".format(stats.violations) }} across {{ stats.violation_cities }} cities). All updated daily.',
            },
            {
                'q': 'How is this different from buying leads?',
                'a': 'Public-record data, not shared leads. Real-time updates every 30 minutes. You see full project context (permit type, value, contractor, date). Unlimited access — no per-lead pricing.',
            },
            {
                'q': 'Is building permit data legal to use?',
                'a': 'Yes — building permits are public records under freedom-of-information laws, available through city open data portals. PermitGrab aggregates what\'s already publicly available into one searchable platform.',
            },
            {
                'q': 'How many cities do you cover?',
                'a': '{{ stats.cities }} cities collecting permits as of today, with property owner data in {{ stats.owner_cities }} cities and violations in {{ stats.violation_cities }} cities. Growing every week.',
            },
            {
                'q': 'How much does PermitGrab cost?',
                'a': '$149/month for unlimited access. Free trial available. No long-term contracts.',
            },
            {
                'q': 'How do I get started?',
                'a': 'Sign up at /pricing, browse any city page for free, upgrade to Pro to see phone numbers and full contact details.',
            },
        ],
    },
}
