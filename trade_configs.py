"""
PermitGrab - Trade Type Registry for Programmatic SEO
Each trade has keywords for matching permits and SEO metadata.
"""

TRADE_REGISTRY = {
    "plumbing": {
        "slug": "plumbing",
        "name": "Plumbing",
        "keywords": ["plumb", "plumbing", "pipe", "sewer", "water heater", "drain", "backflow", "septic", "water line", "gas line"],
        "icon": "🔧",
        "color": "#2563EB",
        "description_template": "plumbing permits including pipe installation, water heater replacement, sewer line repair, and backflow prevention",
        "buyer_phrase": "plumbing contractors",
        "related_trades": ["hvac", "general-construction"],
    },
    "electrical": {
        "slug": "electrical",
        "name": "Electrical",
        "keywords": ["electric", "electrical", "wiring", "panel", "circuit", "lighting", "generator", "solar", "outlet", "transformer", "ev charger"],
        "icon": "⚡",
        "color": "#F59E0B",
        "description_template": "electrical permits including wiring, panel upgrades, lighting installation, and generator hookups",
        "buyer_phrase": "electrical contractors",
        "related_trades": ["solar", "hvac"],
    },
    "hvac": {
        "slug": "hvac",
        "name": "HVAC",
        "keywords": ["hvac", "heating", "cooling", "air condition", "furnace", "ductwork", "ventilation", "heat pump", "boiler", "ac unit", "mini split"],
        "icon": "❄️",
        "color": "#06B6D4",
        "description_template": "HVAC permits including heating system installation, air conditioning, ductwork, and ventilation upgrades",
        "buyer_phrase": "HVAC contractors",
        "related_trades": ["plumbing", "electrical"],
    },
    "roofing": {
        "slug": "roofing",
        "name": "Roofing",
        "keywords": ["roof", "roofing", "shingle", "gutter", "flashing", "skylight", "re-roof", "reroof", "membrane", "soffit", "fascia"],
        "icon": "🏠",
        "color": "#EF4444",
        "description_template": "roofing permits including roof replacement, shingle installation, gutter work, and skylight installation",
        "buyer_phrase": "roofing contractors",
        "related_trades": ["general-construction", "solar"],
    },
    "general-construction": {
        "slug": "general-construction",
        "name": "General Construction",
        "keywords": ["construction", "build", "addition", "remodel", "renovation", "alteration", "tenant improvement", "build out", "new building"],
        "icon": "🏗️",
        "color": "#8B5CF6",
        "description_template": "general construction permits including additions, remodels, renovations, and tenant improvements",
        "buyer_phrase": "general contractors",
        "related_trades": ["demolition", "concrete"],
    },
    "demolition": {
        "slug": "demolition",
        "name": "Demolition",
        "keywords": ["demolition", "demo", "tear down", "abatement", "deconstruction", "hazmat", "asbestos"],
        "icon": "💥",
        "color": "#DC2626",
        "description_template": "demolition permits including building teardown, abatement, and site clearing",
        "buyer_phrase": "demolition contractors",
        "related_trades": ["general-construction", "concrete"],
    },
    "fire-protection": {
        "slug": "fire-protection",
        "name": "Fire Protection",
        "keywords": ["fire", "sprinkler", "fire alarm", "fire suppression", "fire escape", "standpipe", "fire protection"],
        "icon": "🔥",
        "color": "#F97316",
        "description_template": "fire protection permits including sprinkler systems, fire alarms, and fire suppression installation",
        "buyer_phrase": "fire protection contractors",
        "related_trades": ["plumbing", "electrical"],
    },
    "painting": {
        "slug": "painting",
        "name": "Painting",
        "keywords": ["paint", "painting", "coating", "stain", "wallpaper", "finish", "interior paint", "exterior paint"],
        "icon": "🎨",
        "color": "#EC4899",
        "description_template": "painting permits including interior and exterior painting, coating, and finish work",
        "buyer_phrase": "painting contractors",
        "related_trades": ["general-construction"],
    },
    "concrete": {
        "slug": "concrete",
        "name": "Concrete & Masonry",
        "keywords": ["concrete", "foundation", "slab", "footing", "masonry", "brick", "block", "paving", "sidewalk", "driveway"],
        "icon": "🧱",
        "color": "#78716C",
        "description_template": "concrete and masonry permits including foundations, slabs, driveways, and brick work",
        "buyer_phrase": "concrete contractors",
        "related_trades": ["general-construction", "landscaping"],
    },
    "landscaping": {
        "slug": "landscaping",
        "name": "Landscaping",
        "keywords": ["landscape", "landscaping", "irrigation", "grading", "retaining wall", "fence", "deck", "patio", "pool", "pergola"],
        "icon": "🌿",
        "color": "#22C55E",
        "description_template": "landscaping permits including irrigation, fencing, decks, patios, and retaining walls",
        "buyer_phrase": "landscaping contractors",
        "related_trades": ["concrete", "general-construction"],
    },
    "solar": {
        "slug": "solar",
        "name": "Solar",
        "keywords": ["solar", "photovoltaic", "pv", "solar panel", "renewable", "solar installation"],
        "icon": "☀️",
        "color": "#FBBF24",
        "description_template": "solar permits including photovoltaic panel installation, solar arrays, and renewable energy systems",
        "buyer_phrase": "solar installers",
        "related_trades": ["electrical", "roofing"],
    },
    "new-construction": {
        "slug": "new-construction",
        "name": "New Construction",
        "keywords": ["new build", "new construction", "ground up", "new building", "new dwelling", "new home", "new commercial"],
        "icon": "🏢",
        "color": "#3B82F6",
        "description_template": "new construction permits including new residential and commercial buildings",
        "buyer_phrase": "builders and developers",
        "related_trades": ["general-construction", "concrete"],
    },
}


def get_trade(slug):
    """Get trade config by slug."""
    return TRADE_REGISTRY.get(slug)


def get_all_trades():
    """Get list of all trade configs."""
    return list(TRADE_REGISTRY.values())


def get_trade_slugs():
    """Get list of all trade slugs."""
    return list(TRADE_REGISTRY.keys())


def match_permit_to_trade(permit):
    """
    Match a permit to a trade based on description/type keywords.
    Returns the first matching trade slug, or None.
    """
    text = ""
    if permit.get("description"):
        text += permit["description"].lower() + " "
    if permit.get("permit_type"):
        text += permit["permit_type"].lower() + " "
    if permit.get("work_type"):
        text += permit["work_type"].lower()

    for slug, trade in TRADE_REGISTRY.items():
        for keyword in trade["keywords"]:
            if keyword.lower() in text:
                return slug
    return None
