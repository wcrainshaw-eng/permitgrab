"""
PermitGrab - Configuration
Free municipal data sources using Socrata SODA API
"""

# Socrata SODA API endpoints (all FREE, no API key required)
# Rate limit: 1000 requests/hour without app token
CITY_SOURCES = {
    "new_york": {
        "name": "New York City",
        "state": "NY",
        "endpoint": "https://data.cityofnewyork.us/resource/ipu4-2vj7.json",
        "description": "DOB NOW: Build – Job Application Filings",
        "field_map": {
            "permit_number": "job__",
            "permit_type": "job_type",
            "work_type": "work_type",
            "address": "house__",
            "street": "street_name",
            "borough": "borough",
            "zip": "zip_code",
            "owner_name": "owner_s_first_name",
            "owner_last": "owner_s_last_name",
            "owner_phone": "owner_s_phone__",
            "filing_date": "filing_date",
            "status": "filing_status",
            "estimated_cost": "initial_cost",
            "description": "job_description",
        },
        "date_field": "filing_date",
        "limit": 500,
    },
    "chicago": {
        "name": "Chicago",
        "state": "IL",
        "endpoint": "https://data.cityofchicago.org/resource/ydr8-5enu.json",
        "description": "Building Permits",
        "field_map": {
            "permit_number": "permit_",
            "permit_type": "permit_type",
            "work_type": "work_description",
            "address": "street_number",
            "street": "street_direction",
            "street_name": "street_name",
            "zip": "zip_code",
            "contact_name": "contact_1_name",
            "contact_phone": "contact_1_city",
            "filing_date": "issue_date",
            "status": "permit_status",
            "estimated_cost": "reported_cost",
            "description": "work_description",
        },
        "date_field": "issue_date",
        "limit": 500,
    },
    "los_angeles": {
        "name": "Los Angeles",
        "state": "CA",
        "endpoint": "https://data.lacity.org/resource/yv23-pmwf.json",
        "description": "Building and Safety Permit Information",
        "field_map": {
            "permit_number": "permit_nbr",
            "permit_type": "permit_type",
            "permit_sub_type": "permit_sub_type",
            "work_type": "work_desc",
            "address": "address_start",
            "street": "street_direction",
            "street_name": "street_name",
            "zip": "zip_code",
            "filing_date": "issue_date",
            "status": "status",
            "estimated_cost": "valuation",
            "description": "work_desc",
        },
        "date_field": "issue_date",
        "limit": 500,
    },
    "austin": {
        "name": "Austin",
        "state": "TX",
        "endpoint": "https://data.austintexas.gov/resource/3syk-w9eu.json",
        "description": "Issued Construction Permits",
        "field_map": {
            "permit_number": "permit_number",
            "permit_type": "permit_type_desc",
            "work_type": "work_class",
            "address": "original_address1",
            "filing_date": "issued_date",
            "status": "status_current",
            "estimated_cost": "total_valuation_remodel",
            "description": "description",
        },
        "date_field": "issued_date",
        "limit": 500,
    },
    "denver": {
        "name": "Denver",
        "state": "CO",
        "endpoint": "https://data.denvergov.org/resource/p2dh-phhj.json",
        "description": "Building Permits",
        "field_map": {
            "permit_number": "permit_number",
            "permit_type": "permit_type_name",
            "work_type": "work_type_name",
            "address": "address",
            "filing_date": "issue_date",
            "status": "status_name",
            "estimated_cost": "valuation",
            "description": "project_name",
        },
        "date_field": "issue_date",
        "limit": 500,
    },
    "san_francisco": {
        "name": "San Francisco",
        "state": "CA",
        "endpoint": "https://data.sfgov.org/resource/i98e-djp9.json",
        "description": "Building Permits",
        "field_map": {
            "permit_number": "permit_number",
            "permit_type": "permit_type_definition",
            "work_type": "proposed_use",
            "address": "street_number",
            "street": "street_name",
            "zip": "zipcode",
            "filing_date": "filed_date",
            "status": "status",
            "estimated_cost": "estimated_cost",
            "description": "description",
        },
        "date_field": "filed_date",
        "limit": 500,
    },
    "seattle": {
        "name": "Seattle",
        "state": "WA",
        "endpoint": "https://data.seattle.gov/resource/76t5-zbd6.json",
        "description": "Building Permits",
        "field_map": {
            "permit_number": "permitnum",
            "permit_type": "permitclass",
            "work_type": "permitclassmapped",
            "address": "originaladdress1",
            "filing_date": "issueddate",
            "status": "statuscurrent",
            "estimated_cost": "estprojectcost",
            "description": "description",
        },
        "date_field": "issueddate",
        "limit": 500,
    },
    "portland": {
        "name": "Portland",
        "state": "OR",
        "endpoint": "https://data.portland.gov/resource/g9fh-kir9.json",
        "description": "Building Permits",
        "field_map": {
            "permit_number": "permit_number",
            "permit_type": "type",
            "work_type": "work_type",
            "address": "address",
            "filing_date": "issue_date",
            "status": "status",
            "estimated_cost": "valuation",
            "description": "description",
        },
        "date_field": "issue_date",
        "limit": 500,
    },
}

# Trade classification keywords
TRADE_CATEGORIES = {
    "Roofing": [
        "roof", "roofing", "shingle", "membrane", "flashing", "gutter",
        "soffit", "fascia", "re-roof", "reroof"
    ],
    "HVAC": [
        "hvac", "heating", "cooling", "air condition", "furnace", "boiler",
        "ductwork", "ventilation", "heat pump", "ac unit", "a/c", "mini split",
        "thermostat", "refriger"
    ],
    "Electrical": [
        "electric", "wiring", "panel", "circuit", "outlet", "lighting",
        "solar", "photovoltaic", "pv system", "ev charger", "generator",
        "transformer", "switchgear"
    ],
    "Plumbing": [
        "plumb", "pipe", "sewer", "drain", "water heater", "fixture",
        "backflow", "septic", "gas line", "water line", "sprinkler"
    ],
    "General Construction": [
        "new building", "new construction", "addition", "alteration",
        "renovation", "remodel", "tenant improvement", "build out",
        "commercial build", "residential build", "foundation"
    ],
    "Demolition": [
        "demolition", "demo", "tear down", "abatement", "asbestos",
        "hazmat", "deconstruct"
    ],
    "Interior Renovation": [
        "interior", "kitchen", "bathroom", "flooring", "drywall",
        "painting", "cabinet", "countertop", "tile", "finish"
    ],
    "Windows & Doors": [
        "window", "door", "glazing", "storefront", "curtain wall",
        "skylight", "glass"
    ],
    "Structural": [
        "structural", "beam", "column", "load bearing", "seismic",
        "retrofit", "reinforc", "steel", "concrete"
    ],
    "Landscaping & Exterior": [
        "landscape", "fence", "deck", "patio", "retaining wall",
        "pool", "spa", "pergola", "awning", "carport", "garage"
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
