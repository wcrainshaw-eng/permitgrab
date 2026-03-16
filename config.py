"""
PermitGrab - Configuration
Free municipal data sources using Socrata SODA API and ArcGIS REST API
"""

# Municipal API endpoints (all FREE, no API key required)
# Socrata: Rate limit 1000 requests/hour without app token
# ArcGIS: Generally no rate limit for public services
# Set api_type="arcgis" for ArcGIS FeatureServer endpoints (default is "socrata")
CITY_SOURCES = {
    "new_york": {
        "name": "New York City",
        "state": "NY",
        "endpoint": "https://data.cityofnewyork.us/resource/rbx6-tga4.json",
        "description": "DOB NOW: Build – Approved Permits",
        "field_map": {
            "permit_number": "job_filing_number",
            "permit_type": "work_type",
            "work_type": "work_type",
            "address": "house_no",
            "street": "street_name",
            "borough": "borough",
            "zip": "zip_code",
            "owner_name": "owner_name",
            "filing_date": "issued_date",
            "status": "permit_status",
            "estimated_cost": "estimated_job_costs",
            "description": "job_description",
        },
        "date_field": "issued_date",
        "limit": 2000,
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
        "limit": 2000,
    },
    "los_angeles": {
        "name": "Los Angeles",
        "state": "CA",
        "endpoint": "https://data.lacity.org/resource/pi9x-tg5x.json",
        "description": "Building and Safety - Building Permits Issued from 2020 to Present",
        "field_map": {
            "permit_number": "permit_nbr",
            "permit_type": "permit_type",
            "permit_sub_type": "permit_sub_type",
            "work_type": "work_desc",
            "address": "primary_address",
            "zip": "zip_code",
            "filing_date": "issue_date",
            "status": "status_desc",
            "estimated_cost": "valuation",
            "description": "work_desc",
        },
        "date_field": "issue_date",
        "limit": 2000,
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
            "filing_date": "issue_date",
            "status": "status_current",
            "estimated_cost": "",
            "description": "description",
        },
        "date_field": "issue_date",
        "limit": 2000,
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
        "limit": 2000,
    },
    "seattle": {
        "name": "Seattle",
        "state": "WA",
        "endpoint": "https://data.seattle.gov/resource/76t5-zqzr.json",
        "description": "Building Permits",
        "field_map": {
            "permit_number": "permitnum",
            "permit_type": "permitclass",
            "work_type": "permitclassmapped",
            "address": "originaladdress1",
            "filing_date": "issueddate",
            "status": "statuscurrent",
            "estimated_cost": "",
            "description": "description",
        },
        "date_field": "issueddate",
        "limit": 2000,
    },
    "new_orleans": {
        "name": "New Orleans",
        "state": "LA",
        "endpoint": "https://data.nola.gov/resource/rcm3-fn58.json",
        "description": "Permits",
        "field_map": {
            "permit_number": "numstring",
            "permit_type": "type",
            "work_type": "code",
            "address": "address",
            "zip": "",
            "owner_name": "owner",
            "contact_name": "applicant",
            "filing_date": "issuedate",
            "status": "currentstatus",
            "estimated_cost": "constrval",
            "description": "description",
        },
        "date_field": "issuedate",
        "limit": 2000,
    },
    "baton_rouge": {
        "name": "Baton Rouge",
        "state": "LA",
        "endpoint": "https://data.brla.gov/resource/f3qw-nd5k.json",
        "description": "EBR Building Permits",
        "field_map": {
            "permit_number": "permitnumber",
            "permit_type": "permittype",
            "work_type": "designation",
            "address": "streetaddress",
            "zip": "zip",
            "contact_name": "contractorname",
            "filing_date": "issueddate",
            "status": "",
            "estimated_cost": "projectvalue",
            "description": "projectdescription",
        },
        "date_field": "issueddate",
        "limit": 2000,
    },
    "cincinnati": {
        "name": "Cincinnati",
        "state": "OH",
        "endpoint": "https://data.cincinnati-oh.gov/resource/cfkj-xb9y.json",
        "description": "Building Permits",
        "field_map": {
            "permit_number": "permitnum",
            "permit_type": "permittype",
            "work_type": "workclass",
            "address": "originaladdress1",
            "zip": "originalzip",
            "filing_date": "issueddate",
            "status": "statuscurrent",
            "estimated_cost": "estprojectcostdec",
            "description": "description",
        },
        "date_field": "issueddate",
        "limit": 2000,
    },
    # =========================================================================
    # ArcGIS REST API Sources (api_type="arcgis")
    # =========================================================================
    "atlanta": {
        "name": "Atlanta",
        "state": "GA",
        "api_type": "arcgis",
        "endpoint": "https://services5.arcgis.com/5RxyIIJ9boPdptdo/arcgis/rest/services/Building_Permit_latest/FeatureServer/0/query",
        "description": "Building Permits (2019-Present)",
        "field_map": {
            "permit_number": "RecordID",
            "permit_type": "TypeCombo",
            "work_type": "Subtype",
            "address": "Address",
            "zip": "",
            "filing_date": "Opend",
            "status": "Status_1",
            "estimated_cost": "JobValue",
            "description": "Name",
        },
        "date_field": "Opend",
        "limit": 2000,
    },
    "nashville": {
        "name": "Nashville",
        "state": "TN",
        "api_type": "arcgis",
        "date_format": "none",  # Fetch all, filter in Python (ArcGIS date query syntax issues)
        "endpoint": "https://services2.arcgis.com/HdTo6HJqh92wn4D8/arcgis/rest/services/Building_Permit_Applications_Feature_Layer_view/FeatureServer/0/query",
        "description": "Building Permit Applications",
        "field_map": {
            "permit_number": "Permit__",
            "permit_type": "Permit_Type_Description",
            "work_type": "Permit_Subtype_Description",
            "address": "Address",
            "zip": "ZIP",
            "contact_name": "Contact",
            "filing_date": "Date_Entered",
            "status": "",
            "estimated_cost": "Const_Cost",
            "description": "Purpose",
        },
        "date_field": "Date_Entered",
        "limit": 2000,
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
