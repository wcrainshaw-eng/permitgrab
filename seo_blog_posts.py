"""V467 (CODE_V467 SEO blog posts): five long-form, data-rich articles for the
ad-ready cities. Bodies are Jinja-renderable so live DB stats can be injected.

Companion to /blog/<slug> route in server.py. Keep article text out of server.py
so its 19K-line size doesn't grow another 600 lines.
"""

# V471 PR1: body content lives in templates/blog/seo/<slug>.html — rendered
# via render_template() with `stats=...` and `city_slug=...` context, then
# safe-injected into templates/blog_post_seo.html.

SEO_BLOG_POSTS = {
    'chicago-building-permits-2026': {
        'city_slug': 'chicago-il',
        'title': 'Chicago Building Permits 2026: Contractors, Violations & Property Data',
        'meta_description': 'Track active contractors pulling building permits in Chicago. Real permit counts, code violations, and property owner records updated daily.',
        'h1': 'Chicago Building Permits 2026',
        'subject': 'Chicago',
        'target_keyword': 'chicago building permits 2026',
    },

    'miami-dade-solar-permits-2026': {
        'city_slug': 'miami-dade-county',
        'title': 'Miami-Dade Solar Permits 2026: HVHZ, Insurance & Cost Guide',
        'meta_description': 'Complete 2026 guide to Miami-Dade solar permits. HVHZ requirements, NOA certifications, insurance impact, and live contractor data.',
        'h1': 'Miami-Dade Solar Permits 2026',
        'subject': 'Miami-Dade',
        'target_keyword': 'miami dade solar permit',
    },

    'phoenix-code-violations-2026': {
        'city_slug': 'phoenix-az',
        'title': 'Phoenix Code Violations 2026: Fines, Deadlines & How to Resolve',
        'meta_description': 'Phoenix code violations carry $100-$2,500 fines and 30-day deadlines. Fine schedule, resolution steps, and live violation data.',
        'h1': 'Phoenix Code Violations 2026',
        'subject': 'Phoenix',
        'target_keyword': 'phoenix code violations',
    },

    'san-antonio-building-permits-2026': {
        'city_slug': 'san-antonio-tx',
        'title': 'San Antonio Building Permits 2026: Contractors, Violations, Property Data',
        'meta_description': 'Search San Antonio building permits, find licensed contractors, and view 5,000+ open code violations. Live data updated daily.',
        'h1': 'San Antonio Building Permits 2026',
        'subject': 'San Antonio',
        'target_keyword': 'san antonio building permits',
    },

    'nyc-building-violations-2026': {
        'city_slug': 'new-york-city',
        'title': 'NYC Building Violations 2026: DOB & HPD Search Guide',
        'meta_description': 'Search NYC DOB and HPD building violations by address. Class A/B/C explained, BIS vs DOB NOW, and live property data.',
        'h1': 'NYC Building Violations 2026',
        'subject': 'NYC',
        'target_keyword': 'nyc building violations',
    },
}
