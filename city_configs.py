"""
PermitGrab - Modular City Configuration System
Each city is a configuration entry with platform-specific settings.
Supports: Socrata (SODA API), ArcGIS REST, CKAN, CARTO
"""

from datetime import datetime, timedelta

# ============================================================================
# CITY REGISTRY - All cities and their API configurations
# ============================================================================
# Each city config entry contains:
#   - name: Display name
#   - state: 2-letter state code
#   - slug: URL-safe city identifier
#   - platform: "socrata" | "arcgis" | "ckan" | "carto"
#   - endpoint: Full API endpoint URL
#   - dataset_id: Dataset identifier (for reference/debugging)
#   - field_map: Maps our standard fields to source API fields
#   - date_field: Field used for date filtering
#   - date_format: "iso" (default), "epoch", "none"
#   - active: Whether to include in collection
#   - notes: Any special notes about this source


# V471 PR3 (CODE_V471 Part 2A): CITY_REGISTRY and BULK_SOURCES moved to
# city_registry_data.py — re-imported here so all callers
# (server.py, collector.py, db.py, worker.py) keep working unchanged.
from city_registry_data import CITY_REGISTRY, BULK_SOURCES




# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def get_active_cities():
    """Return list of active city keys."""
    return [k for k, v in CITY_REGISTRY.items() if v.get("active", False)]

def get_city_config(city_key):
    """Get configuration for a specific city."""
    return CITY_REGISTRY.get(city_key)

def get_cities_by_platform(platform):
    """Get all active cities for a specific platform."""
    return [k for k, v in CITY_REGISTRY.items()
            if v.get("platform") == platform and v.get("active", False)]

def get_city_count():
    """Get count of active cities."""
    return len(get_active_cities())

def get_city_by_slug(slug):
    """Find city config by its URL slug."""
    for key, config in CITY_REGISTRY.items():
        if config.get("slug") == slug:
            return key, config
    return None, None

def get_all_cities_info():
    """Get basic info for all active cities (for display purposes)."""
    cities = []
    for key, config in CITY_REGISTRY.items():
        if config.get("active", False):
            cities.append({
                "key": key,
                "name": format_city_name(config["name"]),
                "state": config["state"],
                "slug": config["slug"],
                "platform": config["platform"],
                "active": True,
            })
    return sorted(cities, key=lambda x: x["name"])

# V12.9: City name overrides for proper capitalization
CITY_NAME_OVERRIDES = {
    "mckinney": "McKinney",
    "desoto": "DeSoto",
    "el paso": "El Paso",
    "las vegas": "Las Vegas",
    "los angeles": "Los Angeles",
    "san antonio": "San Antonio",
    "san diego": "San Diego",
    "san francisco": "San Francisco",
    "san jose": "San Jose",
    "san marcos": "San Marcos",
    "san rafael": "San Rafael",
    "san anselmo": "San Anselmo",
    "san geronimo": "San Geronimo",
    "san quentin": "San Quentin",
    "st paul": "St. Paul",
    "st. paul": "St. Paul",
    "ft collins": "Fort Collins",
    "fort collins": "Fort Collins",
    "pt reyes station": "Point Reyes Station",
    "point reyes station": "Point Reyes Station",
    "new york city": "New York City",
    "new orleans": "New Orleans",
    "new jersey": "New Jersey",
    "la mesa": "La Mesa",
    "la jolla": "La Jolla",
    "el cajon": "El Cajon",
    "del mar": "Del Mar",
    "mt airy": "Mount Airy",
    "mount airy": "Mount Airy",
    "glen echo": "Glen Echo",
    "chevy chase": "Chevy Chase",
    "takoma park": "Takoma Park",
    "sandy spring": "Sandy Spring",
    "cabin john": "Cabin John",
    "garrett park": "Garrett Park",
    "silver spring": "Silver Spring",
    "north bethesda": "North Bethesda",
    "north potomac": "North Potomac",
    "van alstyne": "Van Alstyne",
    "blue ridge": "Blue Ridge",
    "royse city": "Royse City",
    "forest knolls": "Forest Knolls",
    "stinson beach": "Stinson Beach",
    "dillon beach": "Dillon Beach",
    "muir beach": "Muir Beach",
    "marin city": "Marin City",
    "corte madera": "Corte Madera",
    "mill valley": "Mill Valley",
    "washington grove": "Washington Grove",
    "montgomery village": "Montgomery Village",
}

def format_city_name(name):
    """V12.9: Properly capitalize city names with special handling for prefixes.

    Handles:
    - Mc/Mac prefixes (McKinney, MacArthur)
    - Multi-word names (San Francisco, New York)
    - Override dictionary for special cases
    """
    if not name:
        return name

    # Check override first
    name_lower = name.lower()
    if name_lower in CITY_NAME_OVERRIDES:
        return CITY_NAME_OVERRIDES[name_lower]

    # Apply title case first
    result = name.title()

    # Fix Mc/Mac prefixes (Mckinney -> McKinney)
    import re
    result = re.sub(r'\bMc([a-z])', lambda m: 'Mc' + m.group(1).upper(), result)
    result = re.sub(r'\bMac([a-z])', lambda m: 'Mac' + m.group(1).upper(), result)

    # Fix common prefixes that should stay lowercase
    result = re.sub(r'\bDe ([A-Z])', lambda m: 'De' + m.group(1), result)

    return result

# Trade classification keywords (moved from config.py for consolidation)
# Order matters: more specific trades should be checked before General Construction
TRADE_CATEGORIES = {
    "Electrical": [
        "electrical", "electric", "wiring", "panel", "circuit", "outlet", "lighting",
        "transformer", "switchgear", "conduit", "ampere", "amp service", "200 amp",
        "meter base", "electric service", "electric panel", "elec permit", "elec work",
        "service upgrade", "sub-panel", "subpanel", "light fixture", "receptacle",
        "low voltage", "generator", "temporary power", "temp power",
        # V182 T3 additions
        "security system", "access control", "data cable", "fiber optic",
        "intercom", "nurse call", "ev charging station", "tesla charger",
        "chargepoint", "surveillance camera",
    ],
    "Plumbing": [
        "plumbing", "plumb", "sewer", "drain", "water heater", "water line",
        "gas line", "backflow", "fixture", "pipe", "sprinkler system",
        "irrigation", "septic", "tankless", "water service", "sewer tap",
        "water tap", "sanitary", "waste line", "vent pipe", "hot water",
        "plbg", "gas permit", "gas piping", "rough plumb", "rough-in plumb"
    ],
    "HVAC": [
        "hvac", "heating", "cooling", "air conditioning", "a/c", "ac", "furnace",
        "ductwork", "heat pump", "boiler", "ventilation", "mini-split", "minisplit",
        "condensing unit", "mechanical", "ac unit", "thermostat", "air handler",
        "package unit", "split system", "mech permit", "mechanical permit",
        "exhaust fan", "rooftop unit", "rtu", "vrf", "chiller",
        # V182 T3 additions
        "insulation", "weatherization", "refrigeration", "walk-in cooler",
        "walk-in freezer", "makeup air", "energy recovery", "erv", "hrv",
    ],
    "Roofing": [
        "roof", "roofing", "re-roof", "reroof", "shingle", "membrane",
        "flashing", "gutter", "soffit", "fascia", "tpo", "epdm", "torch down",
        "roof repair", "roof replace", "new roof", "metal roof", "tile roof"
    ],
    "Solar": [
        "solar", "photovoltaic", "pv system", "pv panel", "solar panel",
        "ev charger", "battery storage", "solar electric", "net metering",
        "inverter", "solar thermal", "ev charging",
        # V182 T3 additions
        "powerwall", "tesla powerwall",
    ],
    "Interior Renovation": [
        "interior renovation", "interior remodel", "kitchen remodel",
        "bathroom remodel", "bath remodel", "tenant improvement", "t.i.",
        "finish out", "gut remodel", "buildout", "interior alteration",
        "remodel", "renovation", "kitchen", "bathroom", "interior build",
        "tenant build", "office build", "retail build",
        # V182 T3 additions
        "drywall", "sheetrock", "painting", "flooring", "tile", "tiling",
        "carpet", "hardwood", "laminate", "vinyl floor", "cabinet",
        "countertop", "counter top", "millwork", "trim", "baseboard",
        "crown molding", "plaster", "restroom renovation",
        "office renovation", "retail renovation", "commercial renovation",
    ],
    "Windows & Doors": [
        "window", "door", "glazing", "storefront", "curtain wall",
        "skylight", "glass replacement", "window replacement", "entry door",
        "overhead door", "garage door", "sliding door"
    ],
    "Demolition": [
        "demo", "demolition", "demolish", "tear down", "abatement",
        "strip out", "asbestos", "hazmat", "deconstruct", "raze",
        "partial demo", "interior demo", "full demolition"
    ],
    "Structural": [
        "structural", "foundation", "footing", "retaining wall", "steel",
        "concrete pour", "framing", "load-bearing", "beam", "column",
        "seismic retrofit", "masonry", "concrete slab", "footer",
        "pier", "caisson", "basement",
        # V182 T3 additions
        "concrete work", "foundation repair", "underpinning", "shoring",
    ],
    "Landscaping & Exterior": [
        "landscape", "landscaping", "fence", "deck", "patio", "driveway",
        "sidewalk", "grading", "pool", "pergola", "exterior", "carport",
        "garage", "spa", "hot tub", "outdoor kitchen", "porch", "awning",
        "retaining", "pavers", "concrete flatwork",
        # V182 T3 additions
        "siding", "vinyl siding", "hardie board", "exterior paint", "paving",
        "asphalt", "concrete driveway", "walkway",
    ],
    "Fire Protection": [
        "fire alarm", "fire sprinkler", "fire protection", "fire suppression",
        "smoke detector", "fire escape", "hood suppression", "ansul",
        "fire safety", "fire panel", "fa system", "fire alarm system",
        # V182 T3 additions
        "fire door", "fire damper", "fire stop", "firestopping",
        "hood system", "kitchen hood", "type i hood", "type ii hood",
        "fire watch",
    ],
    "Signage": [
        "sign", "signage", "monument sign", "pole sign", "wall sign",
        "pylon sign", "channel letter", "illuminated sign", "banner"
    ],
    "New Construction": [
        "new construction", "new building", "new commercial", "new residential",
        "new single family", "new multi family", "ground up", "spec home",
        "custom home", "new house",
        # V182 T3 additions
        "adu", "accessory dwelling", "accessory structure", "tiny house",
        "modular", "prefab", "manufactured home", "mobile home",
        "duplex", "triplex", "townhouse", "townhome",
        "apartment building", "mixed use", "mixed-use",
    ],
    "Addition": [
        "addition", "room addition", "home addition", "building addition",
        "add on", "expansion"
    ],
    "General Construction": [
        "alteration", "repair", "commercial", "residential", "building permit",
        "miscellaneous", "general permit", "minor work"
    ],
}

# Value tiers for lead scoring
PERMIT_VALUE_TIERS = {
    "high": {
        "min_cost": 50000,
        "label": "High Value",
        "color": "#e74c3c",
    },
    "medium": {
        "min_cost": 10000,
        "label": "Medium Value",
        "color": "#f39c12",
    },
    "low": {
        "min_cost": 0,
        "label": "Standard",
        "color": "#27ae60",
    },
}

# ============================================================================
# BULK SOURCE HELPER FUNCTIONS (V12.31)
# ============================================================================

def get_active_bulk_sources():
    """Return list of active bulk source keys."""
    return [k for k, v in BULK_SOURCES.items() if v.get("active", False)]

def get_bulk_source_config(source_key):
    """Get configuration for a specific bulk source."""
    return BULK_SOURCES.get(source_key)

def get_all_bulk_sources_info():
    """Get basic info for all bulk sources (for display purposes).

    V471 PR3: rewritten — the previous version had ~1,050 lines of orphan
    CITY_REGISTRY entries accidentally inlined into the appended dict
    (likely from a past sed/find-replace that lost its anchor). The
    function was never called by any other module, so the bonus keys
    were dead weight.
    """
    sources = []
    for key, config in BULK_SOURCES.items():
        sources.append({
            "key": key,
            "name": config["name"],
            "state": config["state"],
            "platform": config["platform"],
            "city_field": config.get("city_field", ""),
            "active": config.get("active", False),
        })
    return sorted(sources, key=lambda x: x["name"])
