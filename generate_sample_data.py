"""
PermitGrab - Realistic Sample Data Generator
Generates permit data that mirrors real Socrata API output structures
"""

import json
import random
import os
from datetime import datetime, timedelta

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(DATA_DIR, exist_ok=True)

# Real street names by city
STREETS = {
    "new_york": [
        "Broadway", "5th Ave", "Park Ave", "Madison Ave", "Lexington Ave",
        "Amsterdam Ave", "Columbus Ave", "West End Ave", "Riverside Dr",
        "E 42nd St", "W 72nd St", "E 86th St", "W 110th St", "Canal St",
        "Flatbush Ave", "Atlantic Ave", "Court St", "Smith St", "4th Ave",
        "Metropolitan Ave", "Graham Ave", "Bedford Ave", "Myrtle Ave"
    ],
    "chicago": [
        "N Michigan Ave", "S State St", "W Madison St", "N Clark St",
        "W Armitage Ave", "N Halsted St", "S Ashland Ave", "W Division St",
        "N Milwaukee Ave", "W Fullerton Ave", "S Western Ave", "N Damen Ave",
        "W North Ave", "S Cottage Grove Ave", "N Lincoln Ave", "W Belmont Ave"
    ],
    "los_angeles": [
        "Wilshire Blvd", "Sunset Blvd", "Hollywood Blvd", "Venice Blvd",
        "Santa Monica Blvd", "Melrose Ave", "La Brea Ave", "Fairfax Ave",
        "Western Ave", "Vermont Ave", "Figueroa St", "Spring St",
        "Main St", "San Pedro St", "Crenshaw Blvd", "Sepulveda Blvd"
    ],
    "austin": [
        "Congress Ave", "S Lamar Blvd", "Guadalupe St", "E 6th St",
        "W 5th St", "S 1st St", "Barton Springs Rd", "E Riverside Dr",
        "N Burnet Rd", "E Martin Luther King Jr Blvd", "S Pleasant Valley Rd",
        "Manor Rd", "Airport Blvd", "E Cesar Chavez St", "Oltorf St"
    ],
    "denver": [
        "Colfax Ave", "Broadway", "17th St", "16th St Mall",
        "S Colorado Blvd", "Federal Blvd", "E Alameda Ave", "S University Blvd",
        "W 38th Ave", "Tennyson St", "S Pearl St", "E Evans Ave",
        "N Sheridan Blvd", "W 32nd Ave", "S Santa Fe Dr"
    ],
    "san_francisco": [
        "Market St", "Mission St", "Valencia St", "Geary Blvd",
        "Van Ness Ave", "Divisadero St", "Fillmore St", "Haight St",
        "Irving St", "Judah St", "Taraval St", "Ocean Ave",
        "Columbus Ave", "Grant Ave", "Powell St", "Bush St"
    ],
    "seattle": [
        "Pike St", "Pine St", "Broadway", "Madison St",
        "E Union St", "Rainier Ave S", "MLK Jr Way S", "Aurora Ave N",
        "Eastlake Ave E", "Fremont Ave N", "Ballard Ave NW", "15th Ave NW",
        "University Way NE", "Lake City Way NE", "35th Ave SW"
    ],
    "portland": [
        "SE Hawthorne Blvd", "NW 23rd Ave", "NE Alberta St", "SE Division St",
        "N Mississippi Ave", "SE Belmont St", "NW Burnside St", "SW Broadway",
        "NE Sandy Blvd", "SE Powell Blvd", "N Williams Ave", "SE Foster Rd",
        "NE Fremont St", "SW Macadam Ave", "SE Woodstock Blvd"
    ],
}

CITIES = {
    "new_york": {"name": "New York City", "state": "NY", "zips": ["10001", "10002", "10003", "10010", "10011", "10012", "10016", "10019", "10021", "10023", "10025", "10028", "10036", "10128", "11201", "11215", "11217", "11221", "11237", "11249"]},
    "chicago": {"name": "Chicago", "state": "IL", "zips": ["60601", "60605", "60607", "60608", "60610", "60613", "60614", "60618", "60622", "60625", "60626", "60630", "60637", "60647", "60657"]},
    "los_angeles": {"name": "Los Angeles", "state": "CA", "zips": ["90001", "90004", "90006", "90012", "90015", "90019", "90026", "90028", "90034", "90036", "90042", "90046", "90048", "90064", "90066"]},
    "austin": {"name": "Austin", "state": "TX", "zips": ["78701", "78702", "78703", "78704", "78705", "78721", "78722", "78723", "78741", "78745", "78751", "78752", "78756", "78757"]},
    "denver": {"name": "Denver", "state": "CO", "zips": ["80202", "80203", "80204", "80205", "80206", "80207", "80209", "80210", "80211", "80212", "80218", "80219", "80220", "80223"]},
    "san_francisco": {"name": "San Francisco", "state": "CA", "zips": ["94102", "94103", "94105", "94107", "94108", "94109", "94110", "94112", "94114", "94115", "94116", "94117", "94118", "94121", "94122"]},
    "seattle": {"name": "Seattle", "state": "WA", "zips": ["98101", "98102", "98103", "98104", "98105", "98107", "98109", "98112", "98115", "98116", "98117", "98118", "98122", "98125", "98133"]},
    "portland": {"name": "Portland", "state": "OR", "zips": ["97201", "97202", "97203", "97204", "97205", "97209", "97210", "97211", "97212", "97213", "97214", "97215", "97217", "97219", "97232"]},
}

# Realistic work descriptions by trade
WORK_DESCRIPTIONS = {
    "Roofing": [
        "Complete roof replacement - tear off existing asphalt shingles, install new 30-year architectural shingles",
        "Re-roof residential dwelling with standing seam metal roofing system",
        "Repair storm damage to roof, replace damaged shingles and flashing",
        "Install new TPO membrane roofing system on commercial flat roof",
        "Roof replacement - remove 2 layers, install ice/water shield and new shingles",
        "Emergency roof repair following wind damage, replace ridge cap and vents",
        "Install new gutter system with leaf guards on residential property",
        "Commercial roof coating application and repair of ponding areas",
    ],
    "HVAC": [
        "Replace existing furnace and AC unit with high-efficiency system",
        "Install new ductless mini-split heating and cooling system (3 zones)",
        "Replace commercial rooftop HVAC unit (5 ton)",
        "Install new central air conditioning system in existing residential",
        "Replace gas furnace with heat pump system - whole house conversion",
        "New ductwork installation for basement finishing project",
        "Install commercial kitchen exhaust hood and makeup air system",
        "Upgrade to high-efficiency boiler system with zone controls",
    ],
    "Electrical": [
        "Electrical panel upgrade from 100A to 200A service",
        "Install residential solar photovoltaic system (8.5 kW)",
        "Rewire entire residential property - knob and tube removal",
        "Install EV charger (Level 2) in residential garage",
        "Commercial electrical buildout for new tenant space",
        "Install backup generator with automatic transfer switch",
        "Upgrade lighting to LED throughout commercial building",
        "New electrical service for residential addition",
    ],
    "Plumbing": [
        "Replace main sewer line from house to city connection",
        "Install tankless water heater replacing standard tank unit",
        "Bathroom rough-in plumbing for basement finishing project",
        "Replace all copper supply lines with PEX throughout residence",
        "Commercial kitchen plumbing - new grease trap and fixtures",
        "Install new water softener and filtration system",
        "Gas line installation for outdoor kitchen and fire pit",
        "Backflow preventer installation and testing",
    ],
    "General Construction": [
        "New single-family residential construction - 3 bed, 2.5 bath, 2400 sf",
        "Major home renovation - gut remodel of kitchen, bathrooms, and living areas",
        "Commercial tenant improvement for retail space (4,200 sf)",
        "Two-story addition to existing single family residence",
        "Convert garage to ADU (accessory dwelling unit) with kitchen and bath",
        "New construction - mixed use building, retail ground floor with 6 residential units",
        "Foundation repair and seismic retrofit for 1920s bungalow",
        "Complete interior renovation of commercial office space (8,500 sf)",
    ],
    "Demolition": [
        "Full demolition of existing commercial structure for new development",
        "Partial demolition - remove non-bearing walls for open floor plan",
        "Demolition of detached garage structure",
        "Asbestos abatement and removal in commercial building",
        "Interior demolition for tenant improvement project",
        "Remove existing pool and fill excavation",
    ],
    "Interior Renovation": [
        "Complete kitchen remodel with new cabinets, countertops, and appliances",
        "Master bathroom renovation - new tile, fixtures, vanity",
        "Hardwood flooring installation throughout main level (1,200 sf)",
        "Basement finishing with bedroom, bathroom, and living space",
        "Drywall repair and interior painting - whole house",
        "Custom built-in shelving and cabinetry installation",
    ],
    "Windows & Doors": [
        "Replace all windows (18 units) with energy-efficient double pane",
        "Install new commercial storefront entry system",
        "Sliding glass door replacement (3 units) and patio door",
        "Install skylights (2) in kitchen and master bedroom",
        "Window replacement and energy efficiency upgrade program",
    ],
    "Structural": [
        "Structural beam replacement and load-bearing wall modification",
        "Seismic retrofit of unreinforced masonry building",
        "Foundation underpinning and crack repair",
        "Steel moment frame installation for soft story retrofit",
        "Install new structural support beams for second floor addition",
    ],
    "Landscaping & Exterior": [
        "Construct new in-ground swimming pool with spa (16x32)",
        "Build new deck with covered pergola (400 sf)",
        "Install new 6ft cedar privacy fence - full property perimeter",
        "Construct retaining wall system for hillside property",
        "Build detached garage (24x30) with electrical",
        "New concrete patio and outdoor kitchen with gas line",
    ],
}

# Owner names
FIRST_NAMES = ["James", "Mary", "Robert", "Patricia", "John", "Jennifer", "Michael", "Linda",
               "David", "Elizabeth", "William", "Barbara", "Richard", "Susan", "Joseph", "Jessica",
               "Thomas", "Sarah", "Charles", "Karen", "Daniel", "Lisa", "Matthew", "Nancy",
               "Anthony", "Betty", "Mark", "Margaret", "Donald", "Sandra", "Steven", "Ashley",
               "Paul", "Kimberly", "Andrew", "Emily", "Joshua", "Donna", "Kenneth", "Michelle",
               "Kevin", "Carol", "Brian", "Amanda", "George", "Dorothy", "Timothy", "Melissa",
               "Wei", "Chen", "Hiroshi", "Yuki", "Carlos", "Maria", "Ahmed", "Fatima",
               "Raj", "Priya", "Oleg", "Natasha", "Juan", "Ana", "Hassan", "Aisha"]
LAST_NAMES = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis",
              "Rodriguez", "Martinez", "Hernandez", "Lopez", "Wilson", "Anderson", "Thomas",
              "Taylor", "Moore", "Jackson", "Martin", "Lee", "Perez", "Thompson", "White",
              "Harris", "Sanchez", "Clark", "Ramirez", "Lewis", "Robinson", "Walker",
              "Chen", "Wang", "Liu", "Kim", "Park", "Nguyen", "Patel", "Shah",
              "Singh", "Kumar", "Tanaka", "Yamamoto", "Müller", "Schmidt", "O'Brien"]

# Permit statuses
STATUSES = ["Issued", "Filed", "Approved", "In Review", "Completed", "Active"]
STATUS_WEIGHTS = [35, 20, 15, 15, 10, 5]

# Cost ranges by trade
COST_RANGES = {
    "Roofing": (3000, 45000),
    "HVAC": (2500, 35000),
    "Electrical": (1500, 80000),
    "Plumbing": (1000, 25000),
    "General Construction": (15000, 2500000),
    "Demolition": (5000, 150000),
    "Interior Renovation": (5000, 120000),
    "Windows & Doors": (2000, 40000),
    "Structural": (8000, 200000),
    "Landscaping & Exterior": (3000, 85000),
}

# Permit type names
PERMIT_TYPES = {
    "new_york": ["NB", "A1", "A2", "A3", "DM", "SG", "AL"],
    "chicago": ["PERMIT - NEW CONSTRUCTION", "PERMIT - RENOVATION/ALTERATION", "PERMIT - ELECTRIC WIRING", "PERMIT - EASY PERMIT PROCESS", "PERMIT - SCAFFOLDING"],
    "los_angeles": ["Bldg-New", "Bldg-Alter/Repair", "Bldg-Addition", "Elec", "Plumbing/Gas", "HVAC", "Grading", "Demolition"],
    "austin": ["Building (R)", "Building (C)", "Electrical (R)", "Electrical (C)", "Mechanical (R)", "Plumbing (R)", "Demolition"],
    "denver": ["Building Permit", "Electrical Permit", "Mechanical Permit", "Plumbing Permit", "Demolition Permit", "Roofing Permit"],
    "san_francisco": ["additions alterridge", "new construction", "demolitions", "sign - errect", "otc alterridge"],
    "seattle": ["Construction", "Demolition", "Grading", "Mechanical", "Building"],
    "portland": ["Commercial", "Residential", "Mechanical", "Electrical", "Plumbing", "Demolition"],
}


def generate_phone():
    return f"({random.randint(200,999)}) {random.randint(200,999)}-{random.randint(1000,9999)}"


def generate_permits(count=2000):
    """Generate realistic permit records across all cities."""
    permits = []
    now = datetime.now()

    # Weight cities by population (NYC gets more permits)
    city_weights = {
        "new_york": 30, "chicago": 18, "los_angeles": 22,
        "austin": 8, "denver": 6, "san_francisco": 5,
        "seattle": 6, "portland": 5,
    }
    city_keys = list(city_weights.keys())
    weights = [city_weights[k] for k in city_keys]

    # Trade distribution (mirrors real permit filing patterns)
    trade_keys = list(WORK_DESCRIPTIONS.keys())
    trade_weights = [10, 12, 15, 10, 20, 5, 12, 8, 4, 4]  # General Construction most common

    for i in range(count):
        city_key = random.choices(city_keys, weights=weights, k=1)[0]
        city = CITIES[city_key]
        trade = random.choices(trade_keys, weights=trade_weights, k=1)[0]

        # Date within last 60 days, weighted toward more recent
        days_ago = int(random.triangular(0, 60, 5))
        filing_date = (now - timedelta(days=days_ago)).strftime("%Y-%m-%d")

        # Cost based on trade - realistic distribution
        cost_min, cost_max = COST_RANGES[trade]
        # Use triangular distribution weighted toward lower costs (realistic)
        mid = cost_min + (cost_max - cost_min) * 0.25
        cost = round(random.triangular(cost_min, cost_max, mid), -2)

        # Value tier
        if cost >= 50000:
            value_tier = "high"
        elif cost >= 10000:
            value_tier = "medium"
        else:
            value_tier = "low"

        street = random.choice(STREETS[city_key])
        house_num = random.randint(1, 9999)

        status = random.choices(STATUSES, weights=STATUS_WEIGHTS, k=1)[0]
        permit_type = random.choice(PERMIT_TYPES[city_key])

        first = random.choice(FIRST_NAMES)
        last = random.choice(LAST_NAMES)

        permit_num = f"{city_key[:3].upper()}-{random.randint(2024, 2026)}-{random.randint(100000, 999999)}"

        permits.append({
            "id": f"{city_key}_{permit_num}",
            "city": city["name"],
            "state": city["state"],
            "permit_number": permit_num,
            "permit_type": permit_type,
            "work_type": trade,
            "trade_category": trade,
            "address": f"{house_num} {street}",
            "zip": random.choice(city["zips"]),
            "filing_date": filing_date,
            "status": status,
            "estimated_cost": cost,
            "value_tier": value_tier,
            "description": random.choice(WORK_DESCRIPTIONS[trade]),
            "contact_name": f"{first} {last}",
            "contact_phone": generate_phone(),
            "borough": "",
            "source_city": city_key,
        })

    # Sort by date descending
    permits.sort(key=lambda x: x["filing_date"], reverse=True)

    return permits


def save_data(permits):
    """Save generated data and compute stats."""
    # Save permits
    output_file = os.path.join(DATA_DIR, "permits.json")
    with open(output_file, "w") as f:
        json.dump(permits, f, indent=2)

    # Compute stats
    trade_counts = {}
    city_counts = {}
    value_counts = {"high": 0, "medium": 0, "low": 0}
    total_value = 0

    for p in permits:
        trade_counts[p["trade_category"]] = trade_counts.get(p["trade_category"], 0) + 1
        city_counts[p["city"]] = city_counts.get(p["city"], 0) + 1
        value_counts[p["value_tier"]] += 1
        total_value += p["estimated_cost"]

    stats = {
        "collected_at": datetime.now().isoformat(),
        "days_back": 60,
        "total_permits": len(permits),
        "total_project_value": total_value,
        "city_stats": {k: {"city_name": k, "normalized": v, "raw": v} for k, v in city_counts.items()},
        "trade_breakdown": dict(sorted(trade_counts.items(), key=lambda x: -x[1])),
        "value_breakdown": value_counts,
    }

    stats_file = os.path.join(DATA_DIR, "collection_stats.json")
    with open(stats_file, "w") as f:
        json.dump(stats, f, indent=2)

    print(f"Generated {len(permits)} permits")
    print(f"Total project value: ${total_value:,.0f}")
    print(f"\nBy City:")
    for city, count in sorted(city_counts.items(), key=lambda x: -x[1]):
        print(f"  {city}: {count}")
    print(f"\nBy Trade:")
    for trade, count in sorted(trade_counts.items(), key=lambda x: -x[1]):
        print(f"  {trade}: {count}")
    print(f"\nBy Value:")
    print(f"  High ($50K+): {value_counts['high']}")
    print(f"  Medium ($10K-$50K): {value_counts['medium']}")
    print(f"  Standard: {value_counts['low']}")

    return stats


if __name__ == "__main__":
    permits = generate_permits(2000)
    save_data(permits)
