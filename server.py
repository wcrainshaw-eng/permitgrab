"""
PermitGrab — Production Server
Flask app that serves the dashboard and API endpoints
Deploy to any VPS (DigitalOcean, Railway, Render, etc.)
"""

from flask import Flask, jsonify, request, send_from_directory, render_template_string, session, render_template, Response, redirect, abort, g
from difflib import SequenceMatcher
import json
import math
import os
import sqlite3
import threading
import time
import secrets
import uuid
from datetime import datetime, timedelta
import stripe
from werkzeug.security import generate_password_hash, check_password_hash
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from city_configs import get_all_cities_info, get_city_count, get_city_by_slug, CITY_REGISTRY, TRADE_CATEGORIES, format_city_name
from lifecycle import get_lifecycle_label
from trade_configs import TRADE_REGISTRY, get_trade, get_all_trades, get_trade_slugs
import analytics
import db as permitdb  # V12.50: SQLite database layer (renamed to avoid Flask-SQLAlchemy collision)

# V78: Global tracking for email digest daemon thread
DIGEST_STATUS = {
    'thread_started': None,
    'last_heartbeat': None,
    'last_digest_attempt': None,
    'last_digest_result': None,
    'last_digest_sent': 0,
    'last_digest_failed': 0,
    'thread_alive': False
}

# V14.1: TRADE_MAPPING - SQL LIKE patterns for matching permits to trades
# Used by city_trade_landing() to filter permits at database level
TRADE_MAPPING = {
    # V30: Broadened patterns — added hyphenated, compound, and ArcGIS-format variants
    'plumbing': ['%plumbing%', '%plumb%', '%pipe%', '%sewer%', '%water heater%', '%drain%', '%backflow%', '%water line%', '%gas line%', '%water service%', '%sewer line%', '%plbg%'],
    'electrical': ['%electrical%', '%electric%', '%wiring%', '%panel%', '%circuit%', '%generator%', '%outlet%', '%elec %', '% elec%', '%service upgrade%', '%meter%', '%transformer%', '%sub-panel%', '%subpanel%'],
    'hvac': ['%hvac%', '%heating%', '%air conditioning%', '%a/c%', '%furnace%', '%ductwork%', '%ventilation%', '%heat pump%', '%boiler%', '%mechanical%', '%mech %', '% mech%', '%mini split%', '%minisplit%', '%ac unit%', '%condenser%', '%air handler%', '%cooling%'],
    'roofing': ['%roofing%', '%roof%', '%reroof%', '%re-roof%', '%shingle%', '%gutter%', '%reroofing%', '%roof replacement%', '%roof repair%', '%residential-reroof%', '%commercial-reroof%', '%rooftop%', '%membrane%', '%flashing%', '%tpo%', '%epdm%'],
    'general-construction': ['%general%', '%alteration%', '%remodel%', '%renovation%', '%tenant improvement%', '%ti %', '% ti%', '%build out%', '%buildout%', '%commercial remodel%', '%residential remodel%', '%repair%', '%maintenance%'],
    'demolition': ['%demolition%', '%demo%', '%tear down%', '%abatement%', '%removal%', '%strip out%', '%gut%'],
    'fire-protection': ['%fire%', '%sprinkler%', '%fire alarm%', '%fire suppression%', '%fire protection%', '%hood suppression%', '%standpipe%'],
    'painting': ['%painting%', '%paint%', '%coating%', '%stucco%', '%exterior finish%'],
    'concrete': ['%concrete%', '%foundation%', '%slab%', '%footing%', '%masonry%', '%brick%', '%paving%', '%flatwork%', '%retaining wall%', '%block%', '%sidewalk%', '%driveway%', '%curb%'],
    'landscaping': ['%landscape%', '%landscaping%', '%irrigation%', '%fence%', '%deck%', '%patio%', '%pool%', '%pergola%', '%grading%', '%retaining%', '%hardscape%', '%sprinkler system%', '%gazebo%'],
    'solar': ['%solar%', '%photovoltaic%', '%pv %', '% pv%', '%pv system%', '%solar panel%', '%net meter%', '%battery storage%', '%solar electric%'],
    'new-construction': ['%new build%', '%new construction%', '%ground up%', '%new building%', '%new dwelling%', '%new home%', '%new single%', '%new multi%', '%new commercial%', '%new residential%', '%addition%', '%sfr%', '%single family%', '%new house%'],
    'interior-renovation': ['%interior%', '%interior renovation%', '%interior remodel%', '%fit out%', '%fitout%', '%tenant finish%', '%finish out%', '%interior alteration%', '%kitchen%', '%bathroom%', '%bath remodel%'],
    'windows-doors': ['%window%', '%door%', '%storefront%', '%glazing%', '%fenestration%', '%skylight%', '%sliding%', '%entry door%', '%garage door%'],
    'structural': ['%structural%', '%steel%', '%framing%', '%load bearing%', '%beam%', '%column%', '%truss%', '%joist%', '%header%', '%shear wall%', '%seismic%'],
    'addition': ['%addition%', '%add on%', '%extension%', '%expand%', '%enlarge%', '%bump out%', '%second story%', '%2nd story%', '%adu%', '%accessory dwelling%', '%guest house%'],
}

# V79: Blog posts data structure with pre-rendered HTML content
BLOG_POSTS = [
    {
        'slug': 'how-much-does-building-permit-cost-houston',
        'title': 'How Much Does a Building Permit Cost in Houston, TX? (2026 Guide)',
        'meta_description': 'Complete 2026 guide to Houston building permit costs. Fee schedules, plan review costs, and tips for contractors and builders.',
        'date': '2026-04-06',
        'category': 'permit-costs',
        'city_link': '/permits/texas/houston',
        'city_name': 'Houston',
        'excerpt': 'Houston building permit fees are generally calculated as a percentage of total project value. Here\'s what contractors need to know for 2026.',
        'content': '''
<p>If you're a contractor or builder working in Houston, understanding permit costs is essential for accurate bidding and project planning. Houston's permitting system has some unique quirks — including no traditional zoning — that affect how permits work and what you'll pay.</p>
<p>This guide breaks down everything you need to know about building permit costs in Houston for 2026.</p>

<h2>Houston Building Permit Costs at a Glance</h2>
<p>Houston building permit fees are generally calculated as a percentage of total project value, plus additional review fees depending on the scope of work.</p>
<p><strong>Residential permit costs:</strong></p>
<ul>
<li>Small projects (under $1,000 in value): approximately $40</li>
<li>Most residential projects: roughly 1% of total project cost</li>
<li>A $20,000 bathroom remodel would run about $200 in permit fees</li>
<li>Plan review and specialized work fees add $200–$500 on top</li>
</ul>
<p><strong>Commercial permit costs:</strong></p>
<ul>
<li>Range from $500 to $25,000+ depending on project size and complexity</li>
<li>Calculated based on project valuation and construction type</li>
<li>Fast Track Review is available for qualifying commercial projects</li>
</ul>
<p>These are base fees — your total cost may include plan review surcharges, inspection fees, and specialty permits (electrical, plumbing, mechanical).</p>

<h2>Where to Apply for a Building Permit in Houston</h2>
<p>All permits go through the <strong>Houston Permitting Center</strong>:</p>
<ul>
<li><strong>Address:</strong> 1002 Washington Avenue, Houston, TX 77002</li>
<li><strong>Hours:</strong> Monday–Friday, 8:00 AM – 5:00 PM (residential plan review intake closes at 3:00 PM)</li>
<li><strong>Online portal:</strong> iPermits is the primary method for 2026 submissions</li>
<li><strong>Fee calculator:</strong> Available on the Houston Permitting Center website to estimate your costs before applying</li>
</ul>
<p>Most permits can now be submitted online through the iPermits system, which has become the standard for 2026.</p>

<h2>How Long Does It Take to Get a Building Permit in Houston?</h2>
<p>Processing times vary significantly by project type:</p>
<ul>
<li><strong>Express/Fast Track Review:</strong> 5–10 business days for qualifying commercial projects</li>
<li><strong>Standard commercial plan review:</strong> 2–37 business days for the initial review cycle</li>
<li><strong>Typical timeline:</strong> 2–6 weeks, though revisions can extend this</li>
</ul>
<p>Plan for potential delays if your project involves floodplain areas, historic districts, or requires variance approvals.</p>

<h2>What Documents Do You Need?</h2>
<p>For a standard residential building permit in Houston, you'll typically need:</p>
<ul>
<li>Building Permit Application (submitted online through iPermits)</li>
<li>New Single Family Prerequisite Checklist (form CE-1301)</li>
<li>Deed Restrictions Declaration form</li>
<li>Architectural and engineering plans for applicable projects</li>
<li>Site plan showing the proposed work</li>
</ul>
<p>Commercial projects require additional documentation including structural engineering plans, MEP drawings, and fire safety plans.</p>

<h2>Special Requirements to Know About</h2>
<p><strong>No zoning:</strong> Houston is famously the only major US city without traditional zoning. Instead, you'll need to navigate deed restrictions, special districts, and overlay zones. This means the Deed Restrictions Declaration form is a required part of every residential permit application.</p>
<p><strong>Floodplain requirements:</strong> Given Houston's flood history, projects in floodplain areas face stricter building requirements and may need additional engineering documentation and elevation certificates.</p>
<p><strong>Historic districts:</strong> If your project is in a historic district, you'll need a Certificate of Appropriateness from the Houston Archaeological and Historical Commission before the building permit can be issued.</p>
<p><strong>TDLR Special Inspections:</strong> These run $40/hour plus travel time — factor this into your project budget.</p>
'''
    },
    {
        'slug': 'how-much-does-building-permit-cost-los-angeles',
        'title': 'How Much Does a Building Permit Cost in Los Angeles, CA? (2026 Guide)',
        'meta_description': 'LA building permit costs for 2026. Plan check fees, development impact fees, school fees, and what contractors need to know.',
        'date': '2026-04-06',
        'category': 'permit-costs',
        'city_link': '/permits/california/los-angeles',
        'city_name': 'Los Angeles',
        'excerpt': 'Los Angeles has some of the highest building permit costs in the country. Here\'s what contractors need to budget for in 2026.',
        'content': '''
<p>Los Angeles has some of the highest building permit costs in the country. Between plan check fees, development impact fees, school fees, and California's energy code surcharges, permit costs in LA can easily reach five figures for even modest projects. Here's what contractors and builders need to know for 2026.</p>

<h2>LA Building Permit Costs at a Glance</h2>
<p>Los Angeles permit fees were updated in February 2026 under Ordinance No. 188,796, so make sure any estimates you've seen are current.</p>
<p><strong>Residential permit costs:</strong></p>
<ul>
<li>Range from $10,000 to $50,000+ depending on project location and type</li>
<li>Includes both the Plan Check Fee (city's design review) and the Permit Issuance Fee</li>
<li>ADUs under 750 sq ft may qualify for fee exemptions under SB 543</li>
</ul>
<p><strong>Commercial permit costs:</strong></p>
<ul>
<li>Base fee starts at 0.5% of the first $50,000 in construction valuation, with decreasing rates for higher valuations</li>
<li>Plan check fee: 85% of the base permit fee (paid separately)</li>
<li>MEP permits: $150–$1,500</li>
<li>Fire safety system permits (sprinklers, alarms): $500–$5,000</li>
<li>Traffic impact studies for large developments: $5,000–$30,000</li>
</ul>
<p><strong>California-specific surcharges that add up fast:</strong></p>
<ul>
<li>Title 24 Energy Code compliance: 10% permit fee increase</li>
<li>Disabled access and adaptability (Title 24): 5% permit fee increase</li>
<li>School development fees: approximately $0.66 per square foot</li>
<li>California Solar Mandate: can add $8,000–$12,000 to residential projects</li>
</ul>

<h2>Where to Apply for a Building Permit in LA</h2>
<p>Permits in the City of Los Angeles go through the <strong>Los Angeles Department of Building and Safety (LADBS)</strong>. Unincorporated areas fall under LA County Building and Safety.</p>
<ul>
<li><strong>Online portals:</strong> ePlanLA or PermitLA (requires an Angeleno account)</li>
<li><strong>Contact:</strong> 311 or the LADBS customer call center</li>
<li><strong>Fee calculator:</strong> Available on the LADBS website — use this before bidding any project</li>
</ul>

<h2>How Long Does It Take to Get a Building Permit in LA?</h2>
<p>This is where LA's reputation earns itself. Processing times are significantly longer than most cities:</p>
<ul>
<li><strong>Standard projects:</strong> 4–8 months is typical</li>
<li><strong>Room additions and second-story additions:</strong> Longer due to structural review requirements</li>
<li><strong>Pre-approved ADU plans:</strong> 30–60 days (a major improvement thanks to SB 543)</li>
</ul>

<h2>Special Requirements to Know About</h2>
<p><strong>Fire zones:</strong> Properties in Very High Fire Hazard Severity Zones face additional requirements for fire-resistant materials, defensible space, and enhanced building standards.</p>
<p><strong>ADU rules (2026):</strong> California has significantly streamlined ADU permitting. Units under 750 sq ft get fee exemptions, and units under 500 sq ft get additional benefits under SB 543.</p>
'''
    },
    {
        'slug': 'how-much-does-building-permit-cost-chicago',
        'title': 'How Much Does a Building Permit Cost in Chicago, IL? (2026 Guide)',
        'meta_description': 'Chicago building permit costs in 2026. Formula-based fees, fast-track options, and cost breakdowns for contractors.',
        'date': '2026-04-06',
        'category': 'permit-costs',
        'city_link': '/permits/illinois/chicago',
        'city_name': 'Chicago',
        'excerpt': 'Chicago\'s building permit system is formula-based with same-day express permits available. Here\'s the full breakdown for 2026.',
        'content': '''
<p>Chicago's building permit system is formula-based, which means your costs depend on construction type, occupancy classification, square footage, and project scope. The upside is that the city offers several fast-track programs that can get simple permits issued same-day. Here's the full breakdown for 2026.</p>

<h2>Chicago Building Permit Costs at a Glance</h2>
<p>Unlike cities with flat fee schedules, Chicago calculates permit fees using a formula that accounts for multiple project variables.</p>
<p><strong>Residential permit costs:</strong></p>
<ul>
<li>Minimum fee: $302 (this applies even for small projects)</li>
<li>Actual cost is calculated based on construction type, occupancy type, square footage, and scope</li>
<li>Use the city's online fee calculator to get a project-specific estimate before bidding</li>
</ul>
<p><strong>Commercial permit costs:</strong></p>
<ul>
<li>Also formula-based with a $302 minimum</li>
<li>Higher-value and more complex projects incur progressively higher fees</li>
<li>Large commercial developments can run into the thousands</li>
</ul>
<p><strong>Fee waiver:</strong> Long-term senior homeowners (65+) who meet income criteria are exempt from permit fees for repairs and alterations to 1–3 unit residential buildings.</p>

<h2>Where to Apply for a Building Permit in Chicago</h2>
<p>Permits are handled by the <strong>Department of Buildings, City of Chicago</strong>.</p>
<ul>
<li><strong>Online portal:</strong> Inspection, Permitting & Licensing Portal (IPI)</li>
<li><strong>Email:</strong> dob-info@cityofchicago.org</li>
<li><strong>Fee calculator:</strong> Available on the city's website — essential for estimating costs</li>
</ul>

<h2>How Long Does It Take to Get a Building Permit in Chicago?</h2>
<p>Chicago offers some of the fastest permitting options of any major city:</p>
<ul>
<li><strong>Express permits:</strong> Same-day issuance for qualifying simple repairs and improvements</li>
<li><strong>Self-Certified Permit Program:</strong> 2–3 weeks for eligible projects with architect/engineer certification</li>
<li><strong>Simple projects:</strong> 7–10 business days</li>
<li><strong>Complex projects:</strong> 10–14 business days or longer</li>
</ul>

<h2>Special Requirements to Know About</h2>
<p><strong>High-rise requirements:</strong> Buildings over 80 feet must include enhanced sprinkler systems, stairway pressurization, and fire command centers.</p>
<p><strong>Express Permit Program:</strong> Qualifying projects can be permitted same-day through the city's web-enabled system.</p>
<p><strong>Self-Certified Permit Program:</strong> If you work with an enrolled architect or engineer, eligible projects can be permitted in 2–3 weeks.</p>
'''
    },
    {
        'slug': 'how-much-does-building-permit-cost-phoenix',
        'title': 'How Much Does a Building Permit Cost in Phoenix, AZ? (2026 Guide)',
        'meta_description': 'Phoenix building permit costs for 2026. Electronic plan review, over-the-counter permits, and fee schedules for contractors.',
        'date': '2026-04-06',
        'category': 'permit-costs',
        'city_link': '/permits/arizona/phoenix',
        'city_name': 'Phoenix',
        'excerpt': 'Phoenix uses a tiered fee structure with same-day over-the-counter permits for simple projects. Here\'s what to expect in 2026.',
        'content': '''
<p>Phoenix is one of the fastest-growing construction markets in the country, and the city's permitting system reflects that with electronic plan review and over-the-counter permits for simple projects. Here's what contractors and builders need to know about permit costs in Phoenix for 2026.</p>

<h2>Phoenix Building Permit Costs at a Glance</h2>
<p>Phoenix uses a tiered fee structure based on project valuation. The Building Valuation Table was most recently updated on January 20, 2026.</p>
<p><strong>Residential permit costs:</strong></p>
<ul>
<li>Projects under $1,000 in value: $150 minimum</li>
<li>Projects $1,001–$10,000: $150 plus $9.00 per additional $1,000 in value</li>
<li>Example: A $50,000 remodel would cost approximately $551 in base permit fees</li>
<li>Plan review fee: 80–100% of the building permit fee (separate charge)</li>
<li>Swimming pool permits: $180 minimum plus a $30 aquatics program surcharge</li>
</ul>
<p><strong>Commercial permit costs:</strong></p>
<ul>
<li>New construction: $5,000–$50,000+ depending on project size</li>
<li>Tenant improvements: $1,500–$10,000 depending on scope</li>
<li>Development impact fees: $2,000–$20,000+ depending on location and building size</li>
</ul>

<h2>Where to Apply for a Building Permit in Phoenix</h2>
<p>Permits go through the <strong>City of Phoenix Planning and Development Department (PDD)</strong>.</p>
<ul>
<li><strong>Online system:</strong> ePlans electronic plan review system</li>
<li>Over-the-counter permits available for simple residential projects</li>
</ul>

<h2>How Long Does It Take to Get a Building Permit in Phoenix?</h2>
<ul>
<li><strong>Simple residential projects:</strong> Same-day issuance available over the counter</li>
<li><strong>Standard residential:</strong> 2–4 weeks</li>
<li><strong>Commercial projects:</strong> 8–14 weeks total (including typical 2–3 correction cycles)</li>
</ul>

<h2>Special Requirements to Know About</h2>
<p><strong>Swimming pool permits</strong> carry an additional $30 aquatics program surcharge.</p>
<p><strong>Reinspection fees</strong> are $150 per inspection for projects under $1,000 that require more than 2 inspections.</p>
<p><strong>Development impact fees</strong> can be $2,000 to $20,000+ depending on location and building size.</p>
'''
    },
    {
        'slug': 'how-much-does-building-permit-cost-san-antonio',
        'title': 'How Much Does a Building Permit Cost in San Antonio, TX? (2026 Guide)',
        'meta_description': 'San Antonio building permit costs in 2026. What contractors and builders need to know about fees and timelines.',
        'date': '2026-04-06',
        'category': 'permit-costs',
        'city_link': '/permits/texas/san-antonio',
        'city_name': 'San Antonio',
        'excerpt': 'San Antonio\'s permit fees are calculated based on project type, square footage, and complexity. Here\'s the 2026 guide.',
        'content': '''
<p>San Antonio is one of the fastest-growing cities in Texas, and with that growth comes a lot of construction activity. Whether you're a general contractor working on new residential builds or a commercial builder tackling tenant improvements, here's what you need to know about building permit costs in San Antonio for 2026.</p>

<h2>San Antonio Building Permit Costs at a Glance</h2>
<p>San Antonio's permit fees are calculated based on project type, square footage, and complexity. The city uses a fee estimator tool rather than publishing flat rates.</p>
<p><strong>Residential permit costs:</strong></p>
<ul>
<li>Fees vary by project type and size</li>
<li>Calculated based on square footage and scope of work</li>
<li>Use the city's online fee estimator for accurate quotes</li>
</ul>
<p><strong>Commercial permit costs:</strong></p>
<ul>
<li>Based on project valuation, square footage, building type, and location</li>
<li>Projects in historic districts incur additional review fees</li>
<li>Multi-departmental review (Building, Fire, Planning) can add fees</li>
</ul>

<h2>Where to Apply for a Building Permit in San Antonio</h2>
<p>Permits are handled by the <strong>Development Services Department (DSD)</strong> at the Cliff Morton Development and Business Services Center.</p>
<ul>
<li><strong>Address:</strong> 1901 S. Alamo St., San Antonio, TX 78204</li>
<li><strong>Online portal:</strong> San Antonio Permits Portal</li>
</ul>

<h2>How Long Does It Take to Get a Building Permit in San Antonio?</h2>
<ul>
<li><strong>Minor repairs and simple projects:</strong> Same-day review available</li>
<li><strong>Standard projects:</strong> Multi-departmental review required</li>
<li><strong>Expedited review:</strong> Available for an additional fee</li>
</ul>
<p>Permits are valid for 180 days from issuance.</p>

<h2>Special Requirements to Know About</h2>
<p><strong>Historic districts:</strong> San Antonio has extensive historic districts. Projects in these areas face additional review requirements from the Historic Preservation Office.</p>
<p><strong>Multi-departmental review:</strong> San Antonio routes permits through multiple departments (Building, Fire, Planning, and potentially Health, Historic, and Storm Water).</p>
'''
    },
    {
        'slug': 'find-construction-leads-houston',
        'title': 'How to Find New Construction Leads in Houston Before Your Competition (2026)',
        'meta_description': 'How Houston subcontractors find new construction leads before the competition. Permit monitoring, bid boards, and outreach strategies.',
        'date': '2026-04-06',
        'category': 'contractor-leads',
        'city_link': '/permits/texas/houston',
        'city_name': 'Houston',
        'excerpt': 'Stop fighting over the same Angi leads. Here\'s how smart Houston contractors find projects before anyone else.',
        'content': '''
<p>Every subcontractor in Houston is fighting over the same leads on Angi and HomeAdvisor. By the time you see the job, five other companies have already submitted quotes. There's a better way — and the smartest contractors in Houston have been using it for years.</p>

<h2>The Problem with Traditional Lead Services</h2>
<p>Services like Angi, Thumbtack, and HomeAdvisor charge $30-75 per lead, and they sell the same lead to 3-5 contractors simultaneously. You're bidding against companies willing to lowball just to win, and you're paying whether you close the deal or not.</p>
<p>The bigger players use ConstructConnect or Dodge Data, but those start at $500/month and focus on large commercial projects. If you're a specialty sub doing residential and light commercial work, most of what those services show you is irrelevant.</p>

<h2>What Smart Contractors Do Instead</h2>
<p>The City of Houston processes roughly 8,000-10,000 building permits every month. Every single one of those permits is a project that needs subcontractors. And the permit filing is public record — it tells you the project address, the scope of work, the general contractor's name, and often the estimated project value.</p>
<p>The contractors who see that permit filing on Day 1 and pick up the phone have a massive advantage over everyone who finds out about it two weeks later through word of mouth or a job board.</p>

<h2>How Building Permit Monitoring Works</h2>
<p>Here's what a typical workflow looks like for an electrical subcontractor in Houston:</p>
<ol>
<li><strong>Set up alerts</strong> for new commercial and large residential permits in your service area</li>
<li><strong>Filter by trade</strong> — focus on permits that include electrical work</li>
<li><strong>Review daily</strong> — every morning, check the 5-10 new permits that match your criteria</li>
<li><strong>Make the call</strong> — contact the GC listed on the permit to introduce your company</li>
<li><strong>Track results</strong> — log which contacts led to bid invitations and which projects you won</li>
</ol>

<h2>What Houston Permit Data Tells You</h2>
<p>Houston is unique in the construction world — it's the largest U.S. city without a traditional zoning code. A Houston building permit typically includes the project address, the type of work, the permit category, the contractor of record, and in many cases the estimated project value and square footage.</p>

<h2>The Numbers That Matter</h2>
<p>Houston's construction market is one of the most active in the country. In any given week, there are hundreds of new commercial permits filed. Compare paying $50 per shared lead on HomeAdvisor to permit monitoring which gives you 10-20x the volume at a fraction of the cost.</p>
'''
    },
    {
        'slug': 'find-commercial-bid-opportunities-atlanta',
        'title': 'The Subcontractor\'s Guide to Finding Commercial Bid Opportunities in Atlanta (2026)',
        'meta_description': 'Guide for Atlanta subcontractors finding commercial bid opportunities. Beltline projects, county monitoring, and data center builds.',
        'date': '2026-04-06',
        'category': 'contractor-leads',
        'city_link': '/permits/georgia/atlanta',
        'city_name': 'Atlanta',
        'excerpt': 'Atlanta\'s construction market is booming. Here\'s how subcontractors can find commercial projects before the competition.',
        'content': '''
<p>Atlanta's construction market is booming. Between the continued expansion around the Beltline, massive mixed-use developments in Midtown, and a wave of data center and logistics facility construction across the metro, there's more work available in the Atlanta market right now than at any point in the last decade.</p>

<h2>Atlanta's Construction Landscape in 2026</h2>
<p>Metro Atlanta consistently ranks in the top 5 nationally for construction activity. The city is experiencing growth across every sector: multifamily residential along the Beltline corridor, office-to-residential conversions in Downtown and Buckhead, industrial and warehouse development along I-85 and I-20, and institutional projects.</p>

<h2>Why Most Subs Miss the Best Opportunities</h2>
<p>The typical subcontractor relies on word of mouth, plan room services, and cold calls. The problem with plan rooms is timing — by the time a project hits BuildingConnected or iSqFt, the GC has already selected their preferred subcontractors for most trades.</p>

<h2>The Permit Advantage</h2>
<p>Building permits solve the timing problem. In Atlanta, when a GC files for a building permit, the project is real. That 2-4 week window between permit filing and plan room posting is the most valuable window for subcontractor outreach.</p>

<h2>How to Use Permit Data for Business Development</h2>
<p><strong>Morning routine (15 minutes):</strong> Review new permits matching your trade and project size criteria.</p>
<p><strong>Outreach (30 minutes):</strong> Call or email the GC on each flagged permit. Your pitch: "I saw you just filed for the tenant improvement at 200 Peachtree — we're a local mechanical contractor and we'd love to be on the bid list."</p>
<p><strong>Follow-up:</strong> Track which GCs responded and which sent you plans.</p>

<h2>Atlanta-Specific Tips</h2>
<p><strong>Watch the Beltline and Westside:</strong> These areas are generating a disproportionate share of permit filings.</p>
<p><strong>Don't ignore the counties:</strong> Cobb, Gwinnett, and DeKalb each have more construction activity than many mid-size cities.</p>
<p><strong>Data centers are the hidden opportunity:</strong> North Georgia has become a major data center market with massive $50M-500M projects.</p>
'''
    },
    {
        'slug': 'permitgrab-vs-constructconnect',
        'title': 'PermitGrab vs. ConstructConnect: Which Is Right for Your Contracting Business?',
        'meta_description': 'PermitGrab vs ConstructConnect comparison for contractors. Features, pricing ($150 vs $500-2000+), and which is right for your business.',
        'date': '2026-04-06',
        'category': 'contractor-leads',
        'city_link': None,
        'city_name': None,
        'excerpt': 'An honest comparison of permit monitoring vs. plan room services for contractors at different stages of growth.',
        'content': '''
<p>If you're a contractor looking for construction lead services, you've probably come across ConstructConnect (formerly iSqFt and CMD). It's one of the biggest names in construction data. But at $500-2,000+ per month, it's priced for mid-to-large firms with dedicated estimating departments.</p>

<h2>What ConstructConnect Does Well</h2>
<p>ConstructConnect is a comprehensive construction intelligence platform. It provides access to project plans and specifications, bid management tools, takeoff software, and a massive database of commercial projects. If you're a GC or large subcontractor bidding on $1M+ commercial projects, ConstructConnect is purpose-built for your workflow.</p>

<h2>Where ConstructConnect Falls Short for Small Subs</h2>
<p><strong>Price:</strong> At $500-2,000/month, that's $6,000-24,000/year. For a sub doing $1-3M in annual revenue, that's a significant line item.</p>
<p><strong>Project mix:</strong> ConstructConnect focuses heavily on commercial and institutional projects. If your bread and butter is residential and light commercial, most of the database isn't relevant to you.</p>
<p><strong>Timing:</strong> Small and mid-size commercial projects often don't get posted to plan rooms at all.</p>

<h2>What Permit Monitoring Offers Instead</h2>
<p><strong>Coverage of small and mid-size projects:</strong> The residential renovation, the 3,000 sqft office build-out — these projects require building permits but don't show up in plan rooms.</p>
<p><strong>Speed:</strong> Permit filings happen at the start of a project's construction phase.</p>
<p><strong>Price:</strong> At $150/month, permit monitoring is 70-90% less expensive than ConstructConnect.</p>

<h2>Head-to-Head Comparison</h2>
<p><strong>Best for large commercial subs ($5M+ revenue):</strong> ConstructConnect.</p>
<p><strong>Best for specialty subs doing residential and light commercial ($500K-5M revenue):</strong> Permit monitoring.</p>

<h2>Cost Per Lead Comparison</h2>
<p><strong>ConstructConnect</strong> at $1,000/month surfaces 30-50 relevant projects per month = $20-33 per lead.</p>
<p><strong>Permit monitoring</strong> at $150/month surfaces 100-300 relevant permits per month = $0.50-1.50 per lead.</p>
<p>The difference in cost per lead is 20-60x.</p>
'''
    },
    {
        'slug': 'hvac-contractors-find-commercial-clients',
        'title': '5 Ways HVAC Contractors Find New Commercial Clients (Beyond Angi and HomeAdvisor)',
        'meta_description': '5 strategies HVAC contractors use to find commercial clients beyond Angi and HomeAdvisor. GC relationships, permit monitoring, and more.',
        'date': '2026-04-06',
        'category': 'trade-guides',
        'city_link': None,
        'city_name': None,
        'excerpt': 'Stop relying on platforms designed for homeowners replacing a furnace. Here are five strategies for finding commercial HVAC work.',
        'content': '''
<p>If you're an HVAC contractor still relying on Angi or HomeAdvisor for your commercial pipeline, you're leaving money on the table. Those platforms are designed for homeowners replacing a furnace, not for GCs awarding $200K mechanical contracts.</p>

<h2>1. Building Permit Monitoring</h2>
<p>This is the most underused lead generation strategy in the mechanical trades. Every commercial construction project starts with a building permit filing — and that filing is public record.</p>
<p>The math works: a mid-size metro might have 50-100 commercial permits filed per week. If 20% involve significant HVAC scope, that's 10-20 potential leads per week. At a 10% conversion rate, that's 1-2 new bid opportunities per week.</p>

<h2>2. GC Relationship Building (The Long Game)</h2>
<p>Identify the 20-30 most active GCs in your metro area, then systematically introduce yourself. Don't lead with a sales pitch — lead with value.</p>
<p>Most successful commercial HVAC companies get 60-80% of their revenue from fewer than 10 GC relationships.</p>

<h2>3. Plan Rooms and Bid Boards</h2>
<p>For larger commercial projects, plans are posted in digital plan rooms like iSqFt, BuildingConnected, and PlanHub. These run $200-800/month and are worth it if you're pursuing projects over $1M.</p>

<h2>4. MCA and SMACNA</h2>
<p>Your local Mechanical Contractors Association or SMACNA chapter hosts bid-letting events, provides labor market data, and connects subs with GCs specifically looking for qualified mechanical contractors.</p>

<h2>5. Owner-Direct Marketing for Service and Retrofit</h2>
<p>Not all HVAC revenue comes from new construction. Target commercial property owners with energy efficiency proposals. Permit data helps identify buildings with recent renovations but no corresponding mechanical permits — likely candidates for retrofit work.</p>

<h2>The Common Thread</h2>
<p>All five strategies are proactive. You're identifying specific projects, specific GCs, and specific buildings where your services are needed — and reaching out before the competition knows the opportunity exists.</p>
'''
    },
    {
        'slug': 'roofing-companies-permit-data-pipeline',
        'title': 'How Smart Roofing Companies Use Permit Data to Fill Their Pipeline',
        'meta_description': 'How smart roofing companies use building permit data to fill their pipeline. New construction, re-roofing, and renovation permits.',
        'date': '2026-04-06',
        'category': 'trade-guides',
        'city_link': None,
        'city_name': None,
        'excerpt': 'The roofing companies pulling away from the pack are competing on information, not just price.',
        'content': '''
<p>Roofing is one of the most competitive trades in residential construction. If you're running a roofing business in 2026, competing on price alone is a race to the bottom. The companies pulling away from the pack are the ones competing on information.</p>

<h2>The Roofing Lead Problem</h2>
<p>Storm chasing is feast-or-famine. Door-to-door canvassing has 2-5% conversion rates. Google Ads cost $50-150 per click in competitive markets. Referrals are unpredictable. What all these channels have in common is that you're reaching homeowners who may or may not need a roof right now.</p>

<h2>What If You Knew Exactly Who Needs a Roof?</h2>
<p><strong>New construction permits</strong> mean a house being built that will need a roof in 60-90 days.</p>
<p><strong>Re-roofing permits</strong> tell you a homeowner has already decided to replace their roof.</p>
<p><strong>Renovation and addition permits</strong> signal homeowners investing in their property — often revealing aging roofs that need replacement.</p>

<h2>The Builder Relationship Play</h2>
<p>In any metro, 80% of new homes are built by 20-30 production builders. When a builder pulls permits for 15 new lots, that's 15 roofs they need in the next 3-6 months. Time your outreach to when they're making subcontracting decisions.</p>

<h2>Competitive Intelligence</h2>
<p>Permit data tells you what your competitors are doing. If a competitor is listed on 30 permits this month, you know they're growing — and you can see which builders they work with.</p>

<h2>The Numbers</h2>
<p>A residential roofing company monitoring permits will identify 50-100 relevant leads per month. At 5-10% conversion and $12,000-18,000 average job value, that's $30,000-180,000 monthly revenue from permit-sourced leads. The subscription cost is $150/month — a 200:1 ROI at the low end.</p>
'''
    },
    {
        'slug': 'solar-installers-building-permit-alerts',
        'title': 'The Solar Installer\'s Secret Weapon: Building Permit Alerts',
        'meta_description': 'How solar installers use building permit alerts to find qualified leads at $0.15-0.19 per lead vs $75-150 for Google Ads.',
        'date': '2026-04-06',
        'category': 'trade-guides',
        'city_link': None,
        'city_name': None,
        'excerpt': 'There\'s a free, public data source that identifies the most qualified solar prospects — and almost nobody in the industry is using it.',
        'content': '''
<p>The solar industry spends an obscene amount of money acquiring customers. The average cost per lead ranges from $50 to $200, and the average cost to acquire a customer is north of $3,000. Meanwhile, there's a free, public data source that identifies the most qualified solar prospects — and almost nobody is using it.</p>

<h2>Why Roofing Permits Are the Best Solar Leads</h2>
<p>A homeowner who just got a new roof has eliminated the #1 objection to solar: "my roof is too old." Their roof is brand new, structurally sound, and warranty-intact. The close rate on new-roof homeowners is dramatically higher than cold leads from any other source.</p>

<h2>Beyond Roofing: Other Permit Types</h2>
<p><strong>New construction permits:</strong> Homeowners making all their major systems decisions right now. Solar is easiest during construction.</p>
<p><strong>Addition permits:</strong> Someone adding square footage isn't planning to sell next year — and their electricity bill is about to increase.</p>
<p><strong>Electrical panel upgrades:</strong> Often a prerequisite for solar. If someone is already upgrading, the marginal cost of adding solar drops significantly.</p>
<p><strong>Pool permits:</strong> Pool equipment can add $100-200/month to an electric bill — making solar an easy sell.</p>

<h2>The Economics</h2>
<p>Phoenix averages 400-600 roofing permits per month. Add new construction, additions, and electrical upgrades: 800-1,000 relevant permits per month.</p>
<p>Permit monitoring costs $150/month = roughly $0.15-0.19 per lead. Compare to:</p>
<ul>
<li>Google Ads solar leads: $75-150 per lead</li>
<li>Lead aggregators: $50-100 per lead</li>
<li>Door-to-door: $30-60 per lead</li>
</ul>
<p>Even if only 10% of permit leads are reachable and interested, your effective cost per qualified lead is $1.50-1.90. That's 30-50x cheaper than Google Ads.</p>

<h2>What Your Competitors Are Doing</h2>
<p>Large national solar companies already monitor permit filings with automated systems. But they have massive overhead and slow response times. A local installer who calls homeowners the same week their roof is completed will beat a national company every time.</p>
'''
    },
    # V80: 20 new blog posts added below (10 permit-cost guides, 10 contractor-leads)
    {
        'slug': 'how-much-does-building-permit-cost-new-york',
        'title': 'How Much Does a Building Permit Cost in New York City? (2026 Guide)',
        'meta_description': 'Complete 2026 guide to NYC building permit costs. DOB NOW fees, plan examination, Pro Cert benefits, and tips for contractors.',
        'date': '2026-04-06',
        'category': 'permit-costs',
        'city_link': '/permits/new-york-state/new-york',
        'city_name': 'New York City',
        'excerpt': 'NYC building permit fees are calculated using filing fees, plan examination fees, and per-square-foot charges. Here\'s the 2026 breakdown.',
        'content': '''
<p>If you're a contractor or builder working in New York City, permit costs are one of the most complex line items in any project budget. NYC's Department of Buildings (DOB) uses a layered fee structure based on project type, building class, and square footage — and the numbers changed again in January 2026.</p>
<p>This guide breaks down everything you need to know about building permit costs in NYC for 2026.</p>

<h2>NYC Building Permit Costs at a Glance</h2>
<p>NYC building permit fees are calculated using a combination of filing fees, plan examination fees, and per-square-foot charges. As of January 26, 2026, the minimum filing fee increased to $130 under Local Law 128 of 2024.</p>
<p><strong>Residential permit costs:</strong></p>
<ul>
<li>Minimum filing fee: $130</li>
<li>Alteration Type 2 (minor alterations): $130–$300 for most residential projects</li>
<li>New building permits: calculated at $0.26 per square foot for the first 10,000 sq ft, with reduced rates above that</li>
<li>Plan examination fees: $425–$1,600+ depending on project complexity</li>
<li>Total cost for a typical apartment renovation: $500–$2,000</li>
</ul>
<p><strong>Commercial permit costs:</strong></p>
<ul>
<li>Filing fees: $130–$280 for most commercial work</li>
<li>New building permits: $0.26/sq ft base rate, scaling with building size</li>
<li>Plan examination for new buildings: $1,600+</li>
<li>Large commercial projects: $5,000–$25,000+ in total permit fees</li>
</ul>
<p><strong>Payment structure varies by work type:</strong></p>
<ul>
<li>Electrical work: 50% due at filing (minimum $130), remainder before inspection</li>
<li>Work requiring Certificate of Occupancy change: 50% upfront, remainder before permit issuance</li>
<li>All other work: 100% due at filing</li>
</ul>

<h2>Where to Apply for a Building Permit in NYC</h2>
<p>All permit applications go through the <strong>DOB NOW</strong> portal:</p>
<ul>
<li><strong>Online portal:</strong> DOB NOW at a810-dobnow.nyc.gov — this is now the primary (and often required) method for all filings</li>
<li><strong>Manhattan borough office:</strong> 280 Broadway, New York, NY 10007</li>
<li><strong>Hours:</strong> Monday–Friday, 8:00 AM – 4:30 PM for in-person inquiries</li>
<li><strong>Phone:</strong> 311 or 212-NEW-YORK</li>
</ul>
<p>DOB NOW handles new building applications, alterations, demolitions, electrical permits, plumbing permits, and sign applications.</p>

<h2>How Long Does It Take to Get a Building Permit in NYC?</h2>
<p>Processing times in NYC vary dramatically based on filing type and whether you use professional certification:</p>
<ul>
<li><strong>Alt2 (minor alterations, standard filing):</strong> 4–6 weeks</li>
<li><strong>Alt1 (major alterations, standard filing):</strong> 3–4 months</li>
<li><strong>Alt1 with Professional Certification:</strong> 3–4 weeks</li>
<li><strong>New Building (standard):</strong> 4–12 weeks depending on complexity</li>
<li><strong>Minor permits (plumbing, electrical):</strong> 1–4 weeks</li>
</ul>
<p><strong>Professional Certification (Pro Cert)</strong> is the single biggest time-saver in NYC permitting. When a licensed Registered Architect or Professional Engineer certifies the work, it bypasses standard DOB plan examination — cutting timelines by 50–75%.</p>

<h2>What Documents Do You Need?</h2>
<p>For a standard alteration permit in NYC, you'll typically need:</p>
<ul>
<li>Completed DOB NOW application with project details</li>
<li>Architectural drawings prepared by a licensed RA or PE</li>
<li>Structural analysis (for work affecting building structure)</li>
<li>Energy code compliance documentation (NYC Energy Conservation Code)</li>
<li>Asbestos Investigation Report (ACP-5) for buildings built before 2008</li>
<li>Proof of insurance for the general contractor</li>
</ul>

<h2>Special Requirements to Know About</h2>
<p><strong>Landmark buildings:</strong> If your project is in or adjacent to a designated landmark or historic district, you'll need approval from the Landmarks Preservation Commission (LPC) before DOB will process the permit.</p>
<p><strong>Flood zones:</strong> Properties in flood hazard areas require flood zone compliance plans sealed by a registered design professional.</p>
<p><strong>Asbestos:</strong> Any building constructed before 2008 undergoing alteration or demolition requires an asbestos investigation before permits can be issued. Budget $1,000–$5,000 for the investigation depending on building size.</p>
<p><strong>Site safety:</strong> For buildings over 15 stories or projects with certain construction methods, NYC requires a licensed Site Safety Manager or Coordinator on-site.</p>

<h2>Pro Tips for Contractors</h2>
<p><strong>File as Professional Certification whenever possible.</strong> The time savings alone justify the cost — a 3-week turnaround vs. 3 months can make or break a project timeline.</p>
<p><strong>Budget for the ACP-5.</strong> Asbestos investigation is required on virtually every pre-2008 building in the city.</p>
<p><strong>Use DOB NOW's status tracking.</strong> The portal shows real-time status of your application, including examiner comments and objections.</p>
'''
    },
    {
        'slug': 'how-much-does-building-permit-cost-philadelphia',
        'title': 'How Much Does a Building Permit Cost in Philadelphia? (2026 Guide)',
        'meta_description': 'Philadelphia building permit costs for 2026. L&I fee schedules, tax clearance requirements, and historic district rules for contractors.',
        'date': '2026-04-06',
        'category': 'permit-costs',
        'city_link': '/permits/pennsylvania/philadelphia',
        'city_name': 'Philadelphia',
        'excerpt': 'Philadelphia permit fees are based on estimated project cost, with different filing fees for residential and commercial work.',
        'content': '''
<p>If you're a contractor or builder working in Philadelphia, understanding permit costs from the Department of Licenses & Inspections (L&I) is essential for accurate project budgeting. Philly's fee structure is relatively straightforward compared to some major cities, but there are a few wrinkles that can catch you off guard — especially around historic districts and the city's tax clearance requirements.</p>
<p>This guide breaks down everything you need to know about building permit costs in Philadelphia for 2026.</p>

<h2>Philadelphia Building Permit Costs at a Glance</h2>
<p>Philadelphia calculates building permit fees based on estimated project cost, with different filing fees for residential and commercial work.</p>
<p><strong>Residential permit costs (1-2 family homes):</strong></p>
<ul>
<li>Filing fee: $25</li>
<li>Permit fee: starts at $155 for the first $1,000 of work, plus $30 for each additional $1,000</li>
<li>A $20,000 bathroom remodel: approximately $725 in permit fees</li>
<li>A $50,000 addition: approximately $1,625 in permit fees</li>
<li>Trade permits (electrical, plumbing, HVAC) are separate and range from $25–$200+</li>
</ul>
<p><strong>Commercial permit costs:</strong></p>
<ul>
<li>Filing fee: $100</li>
<li>Permit fee: same formula — $155 base plus $30 per additional $1,000 of project value</li>
<li>A $200,000 tenant build-out: approximately $6,125 in permit fees</li>
<li>Accelerated review adds $350; full expedited processing costs $2,000</li>
</ul>
<p>Note: a 2.10% credit card surcharge (minimum $1.50) applies to all card payments.</p>

<h2>Where to Apply for a Building Permit in Philadelphia</h2>
<p>Permits are processed through Philadelphia's Department of Licenses & Inspections:</p>
<ul>
<li><strong>In-person:</strong> Permit and License Center, 1401 JFK Blvd., Municipal Services Building, Public Service Concourse</li>
<li><strong>Hours:</strong> Monday–Friday, 8:00 AM – 3:30 PM</li>
<li><strong>Online portal:</strong> eCLIPSE at eclipse.phila.gov</li>
<li><strong>Phone:</strong> 311 or 215-686-8686</li>
</ul>

<h2>How Long Does It Take to Get a Building Permit in Philadelphia?</h2>
<ul>
<li><strong>Residential permits (1-2 family homes):</strong> 15 business days</li>
<li><strong>Commercial permits:</strong> 20 business days</li>
<li><strong>Accelerated applications:</strong> 5 business days (additional $350 fee)</li>
<li><strong>Expedited processing:</strong> priority handling ($2,000 total)</li>
</ul>

<h2>What Documents Do You Need?</h2>
<p>For a standard building permit in Philadelphia, you'll typically need:</p>
<ul>
<li>Completed application with full scope of work description</li>
<li>Current owner information and proof of ownership</li>
<li>Detailed construction plans with estimated project cost</li>
<li>Structural Design Criteria form (required for commercial)</li>
<li>Contractor information — all contractors must be licensed and current on City taxes</li>
</ul>

<h2>Special Requirements to Know About</h2>
<p><strong>Tax clearance is mandatory.</strong> The property must be current on ALL City taxes before any new construction permit will be released.</p>
<p><strong>Licensed contractor requirement.</strong> A licensed Philadelphia contractor must perform all work, except minor projects on existing 1-2 family homes.</p>
<p><strong>Historic districts are serious.</strong> If the property is on the Philadelphia Register of Historic Places, demolition and significant exterior alterations require Philadelphia Historical Commission review BEFORE L&I will process the permit.</p>
<p><strong>Asbestos inspection for older buildings.</strong> Any demolition or alteration exceeding $50,000 requires an asbestos inspection report.</p>

<h2>Pro Tips for Contractors</h2>
<p><strong>Clear the property's tax status early.</strong> Don't wait until you're ready to pull the permit to discover the owner has outstanding obligations.</p>
<p><strong>Use accelerated review for time-sensitive jobs.</strong> The $350 fee to cut review time from 20 days to 5 days is worth it on most commercial projects.</p>
'''
    },
    {
        'slug': 'how-much-does-building-permit-cost-san-diego',
        'title': 'How Much Does a Building Permit Cost in San Diego? (2026 Guide)',
        'meta_description': 'San Diego building permit costs in 2026. DSD fees, coastal zone CDPs, ADU exemptions, and expedited review options for contractors.',
        'date': '2026-04-06',
        'category': 'permit-costs',
        'city_link': '/permits/california/san-diego',
        'city_name': 'San Diego',
        'excerpt': 'San Diego calculates permit fees based on project valuation and square footage, with over 50% of permits issued same-day.',
        'content': '''
<p>If you're a contractor or builder working in San Diego, understanding the Development Services Department (DSD) fee structure is critical for accurate bidding. San Diego has made major strides in streamlining its permitting process — over 50% of permits are now issued same-day — but the fees and coastal zone requirements still require careful planning.</p>
<p>This guide breaks down everything you need to know about building permit costs in San Diego for 2026.</p>

<h2>San Diego Building Permit Costs at a Glance</h2>
<p>San Diego calculates building permit fees based on project valuation and square footage, with plan check fees set at 65% of the building permit fee.</p>
<p><strong>Residential permit costs:</strong></p>
<ul>
<li>Small residential projects (under $5,000): approximately $200–$400</li>
<li>Typical home remodel ($25,000–$50,000): approximately $1,200–$2,500</li>
<li>ADUs under 750 sq ft: may qualify for fee exemptions</li>
<li>New single-family home construction: $5,000–$15,000+ depending on size and location</li>
</ul>
<p><strong>Commercial permit costs:</strong></p>
<ul>
<li>Base permit fees range from $3,000 to $8,000+ depending on project type</li>
<li>Retail/hotel projects: approximately $0.84 per square foot</li>
<li>Large commercial: $10,000–$50,000+ in total permit and plan check fees</li>
</ul>
<p><strong>Expedited options:</strong></p>
<ul>
<li>Express Plan Check: $740.28 administration fee plus 1.5x the regular plan check fee</li>
<li>Designated Project Manager: $164.87 per hour</li>
</ul>

<h2>Where to Apply for a Building Permit in San Diego</h2>
<ul>
<li><strong>Online portal:</strong> Accela Citizen Access — all permits required online since January 2018</li>
<li><strong>In-person assistance:</strong> Development Services Department, 1222 First Avenue</li>
</ul>

<h2>How Long Does It Take to Get a Building Permit in San Diego?</h2>
<ul>
<li><strong>Over-the-counter permits:</strong> Same day — over 50% of all permits are issued same-day</li>
<li><strong>61% of all permits</strong> are approved within one week</li>
<li><strong>ADUs:</strong> 4–12 weeks (2–3 weeks with pre-approved plan sets)</li>
<li><strong>Coastal Development Permits:</strong> add 2–6 months to any project timeline</li>
</ul>

<h2>Special Requirements to Know About</h2>
<p><strong>Coastal zone projects require a Coastal Development Permit (CDP).</strong> If your project is within San Diego's Coastal Overlay Zone, you'll need a CDP in addition to the standard building permit. This adds 2–6 months to your timeline.</p>
<p><strong>ADU regulations are contractor-friendly.</strong> Units under 750 sq ft may qualify for fee exemptions, and the city offers pre-approved ADU plan sets.</p>
<p><strong>Title 24 energy compliance is mandatory.</strong> Every permit in California requires Title 24 energy compliance documentation.</p>

<h2>Pro Tips for Contractors</h2>
<p><strong>Use pre-approved ADU plans if possible.</strong> The time and cost savings are substantial — 2–3 weeks vs. 4–12 weeks.</p>
<p><strong>Check the Coastal Overlay Zone map early.</strong> A project inside vs. outside the zone boundary can mean a 6-month difference in timeline.</p>
'''
    },
    {
        'slug': 'how-much-does-building-permit-cost-dallas',
        'title': 'How Much Does a Building Permit Cost in Dallas, TX? (2026 Guide)',
        'meta_description': 'Dallas building permit costs for 2026. DallasNow portal fees, valuation-based calculations, and tips for contractors and builders.',
        'date': '2026-04-06',
        'category': 'permit-costs',
        'city_link': '/permits/texas/dallas',
        'city_name': 'Dallas',
        'excerpt': 'Dallas uses a valuation-based fee structure with the new DallasNow unified portal for all permit applications.',
        'content': '''
<p>If you're a contractor or builder working in Dallas, understanding the Building Inspection Division's fee structure is essential for accurate project bids. Dallas recently overhauled its entire permitting system with the launch of DallasNow in May 2025, consolidating 15 separate systems into one portal.</p>
<p>This guide breaks down everything you need to know about building permit costs in Dallas for 2026.</p>

<h2>Dallas Building Permit Costs at a Glance</h2>
<p>Dallas uses a valuation-based fee structure. Fees are calculated based on construction project valuation, building square footage, and the type of work being performed.</p>
<p><strong>Residential permit costs:</strong></p>
<ul>
<li>Small residential projects (minor repairs, under $5,000): approximately $100–$250</li>
<li>Typical home remodel ($20,000–$50,000): approximately $400–$1,200</li>
<li>New single-family home construction: $2,000–$8,000+ depending on size and valuation</li>
</ul>
<p><strong>Commercial permit costs:</strong></p>
<ul>
<li>Per-square-foot rates ranging from $0.004 to $0.012 per square foot</li>
<li>Small commercial tenant improvements: $500–$3,000</li>
<li>Large commercial construction ($1M+): $10,000–$50,000+</li>
</ul>
<p><strong>Important change for 2026:</strong> Dallas adopted an early-fee collection approach in October 2025, meaning fees are collected upfront at time of application.</p>

<h2>Where to Apply for a Building Permit in Dallas</h2>
<ul>
<li><strong>Online portal:</strong> DallasNow (Accela Citizen Access) — as of May 2024, all commercial applications must be submitted online</li>
<li><strong>In-person:</strong> Oak Cliff Municipal Center, 320 E. Jefferson Blvd.</li>
<li><strong>Phone:</strong> 214-948-4480</li>
</ul>

<h2>How Long Does It Take to Get a Building Permit in Dallas?</h2>
<ul>
<li><strong>Minor residential permits:</strong> 1–5 business days</li>
<li><strong>Standard residential new construction:</strong> 2–4 weeks</li>
<li><strong>Commercial tenant improvements:</strong> 3–6 weeks</li>
<li><strong>New commercial construction:</strong> 4–12 weeks</li>
</ul>

<h2>Special Requirements to Know About</h2>
<p><strong>Texas state licensing requirements apply.</strong> Plans for most projects must be sealed by a licensed engineer or architect.</p>
<p><strong>All commercial applications must be filed online.</strong> Since May 2024, in-person commercial filing is no longer accepted.</p>
<p><strong>Online inspection scheduling is available 24/7.</strong> Schedule through DallasNow or by calling 214-670-5313.</p>

<h2>Pro Tips for Contractors</h2>
<p><strong>Get familiar with DallasNow.</strong> Spend time setting up your account and navigating the interface before you need to file.</p>
<p><strong>Submit complete applications the first time.</strong> Incomplete submissions are the #1 cause of delays.</p>
<p><strong>Pay fees upfront.</strong> With the early-fee collection policy, having payment ready keeps your application moving.</p>
'''
    },
    {
        'slug': 'how-much-does-building-permit-cost-jacksonville',
        'title': 'How Much Does a Building Permit Cost in Jacksonville, FL? (2026 Guide)',
        'meta_description': 'Jacksonville building permit costs in 2026. JaxEPICS fees, private inspector discounts, flood zone requirements for contractors.',
        'date': '2026-04-06',
        'category': 'permit-costs',
        'city_link': '/permits/florida/jacksonville',
        'city_name': 'Jacksonville',
        'excerpt': 'Jacksonville calculates fees based on size, type, and valuation of construction, with 31% discounts for private inspectors.',
        'content': '''
<p>If you're a contractor or builder working in Jacksonville, understanding the Building Inspection Division's fee structure is key to accurate project budgeting. Jacksonville is the largest city by land area in the contiguous U.S., and its permitting system reflects the diversity of its construction landscape.</p>
<p>This guide breaks down everything you need to know about building permit costs in Jacksonville for 2026.</p>

<h2>Jacksonville Building Permit Costs at a Glance</h2>
<p>Jacksonville calculates building permit fees based on the size, type, and valuation of construction. Trade permits inspected by a private inspector receive a 31% reduction.</p>
<p><strong>Residential permit costs:</strong></p>
<ul>
<li>Small accessory buildings (12 ft or less): no charge</li>
<li>Accessory buildings under 401 sq ft: $200</li>
<li>Accessory buildings 401 sq ft and greater: $275</li>
<li>Typical home remodel: approximately $300–$800</li>
<li>New single-family home construction: $1,500–$5,000+ depending on size</li>
</ul>
<p><strong>Commercial permit costs:</strong></p>
<ul>
<li>Non-residential accessory buildings: $0.27 per sq ft (minimum $325)</li>
<li>Small commercial tenant improvements: $500–$2,000</li>
<li>Large commercial construction: $10,000–$40,000+</li>
</ul>
<p><strong>Private inspector discount:</strong> Mechanical, electrical, plumbing, roofing, and mobile home permit fees are reduced by 31% when inspected by a private inspector.</p>

<h2>Where to Apply for a Building Permit in Jacksonville</h2>
<ul>
<li><strong>Online portal:</strong> JaxEPICS at jaxepics.coj.net — required method for all submissions</li>
<li><strong>In-person assistance:</strong> Edward Ball Building, 2nd Floor, 214 North Hogan Street</li>
<li><strong>Phone:</strong> 904-255-8500</li>
</ul>

<h2>How Long Does It Take to Get a Building Permit in Jacksonville?</h2>
<ul>
<li><strong>First permit review:</strong> 25–30 business days</li>
<li><strong>Resubmissions after corrections:</strong> 10 business days or less</li>
<li><strong>Permit validity:</strong> 180 days — each passed inspection extends it another 180 days</li>
</ul>

<h2>Special Requirements to Know About</h2>
<p><strong>Flood zone requirements are serious.</strong> Projects in flood zones require Elevation Certificates at two stages and design to withstand wave action in VE Zones.</p>
<p><strong>Florida Building Code compliance.</strong> All permits must comply with the current Florida Building Code with specific requirements for wind resistance and hurricane protection.</p>
<p><strong>Contractor registration is required.</strong> All contractors must be registered with Jacksonville's Building Inspection Division.</p>

<h2>Pro Tips for Contractors</h2>
<p><strong>Get your Elevation Certificates early.</strong> In flood zones, you'll need them at two inspection stages.</p>
<p><strong>Use the private inspector discount.</strong> The 31% fee reduction can save hundreds or thousands on large projects.</p>
<p><strong>File the Notice of Commencement.</strong> Florida law requires this for projects over $2,500.</p>
'''
    },
    {
        'slug': 'how-much-does-building-permit-cost-san-jose',
        'title': 'How Much Does a Building Permit Cost in San Jose, CA? (2026 Guide)',
        'meta_description': 'San Jose building permit costs for 2026. PBCE hourly fees, plan review at $325/hr, Title 24 requirements for contractors.',
        'date': '2026-04-06',
        'category': 'permit-costs',
        'city_link': '/permits/california/san-jose',
        'city_name': 'San Jose',
        'excerpt': 'San Jose uses an hourly-rate fee structure with plan review at $325/hour and permit processing at $227/hour.',
        'content': '''
<p>If you're a contractor or builder working in San Jose, the permit fee structure from Planning, Building and Code Enforcement (PBCE) is one of the most complex in California. Fees are calculated on an hourly basis for plan review, with additional construction taxes and processing charges that add up fast.</p>
<p>This guide breaks down everything you need to know about building permit costs in San Jose for 2026.</p>

<h2>San Jose Building Permit Costs at a Glance</h2>
<p>San Jose uses an hourly-rate fee structure rather than a simple percentage of project value. Plan review is billed at $325/hour and permit processing at $227/hour.</p>
<p><strong>Residential permit costs:</strong></p>
<ul>
<li>Small residential alterations (under 2,250 sq ft): starting around $400–$800</li>
<li>Residential alterations over 2,250 sq ft: starting at $633</li>
<li>A typical $50,000 kitchen remodel: approximately $1,500–$3,000 in total permit fees</li>
<li>New single-family home: $5,000–$15,000+ depending on size and complexity</li>
<li>Development construction tax: $0.28 per $1,000 of project valuation</li>
</ul>
<p><strong>Commercial permit costs:</strong></p>
<ul>
<li>Tenant improvements: $1,000–$5,000+</li>
<li>Major new commercial construction: $20,000–$50,000+</li>
<li>Fire District clearance fees are separate for structures over 500 sq ft</li>
</ul>
<p><strong>Payment notes:</strong></p>
<ul>
<li>Credit card surcharge: 2.4% in person, 2.66% online</li>
<li>Online permits get a 50% reduction on the processing fee</li>
</ul>

<h2>Where to Apply for a Building Permit in San Jose</h2>
<ul>
<li><strong>SJPermits.org</strong> — primary online portal for standard permit applications</li>
<li><strong>SJePlans</strong> — electronic plan submittal system for projects requiring plan review</li>
<li><strong>In-person:</strong> Development Services Permit Center, San José City Hall, 3rd Floor</li>
</ul>

<h2>How Long Does It Take to Get a Building Permit in San Jose?</h2>
<ul>
<li><strong>Small projects:</strong> 1–3 weeks</li>
<li><strong>Standard residential alterations:</strong> 4–6 weeks</li>
<li><strong>New commercial construction:</strong> 15–25 weeks for initial review</li>
<li><strong>Express plan check (third-party):</strong> 2–3 days for qualifying projects</li>
</ul>

<h2>Special Requirements to Know About</h2>
<p><strong>2025 Building Codes took effect January 1, 2026.</strong> All projects must comply with the updated California Building Code.</p>
<p><strong>Title 24 energy compliance is mandatory.</strong> This is the single most common reason for plan review corrections.</p>
<p><strong>Fire District clearance adds time and cost.</strong> Any structure or addition exceeding 500 square feet requires Fire Marshal review.</p>
<p><strong>Hourly billing means scope creep costs real money.</strong> Submitting complete, code-compliant plans the first time is the best way to control costs.</p>

<h2>Pro Tips for Contractors</h2>
<p><strong>Submit clean plans the first time.</strong> At $325/hour for plan review, every correction cycle costs hundreds.</p>
<p><strong>Use express plan check for commercial projects.</strong> Third-party review in 2–3 days vs. 15–25 weeks of city review.</p>
<p><strong>File online for the processing fee discount.</strong> Online permits get a 50% reduction on processing fees.</p>
'''
    },
    {
        'slug': 'how-much-does-building-permit-cost-austin',
        'title': 'How Much Does a Building Permit Cost in Austin, TX? (2026 Guide)',
        'meta_description': 'Austin building permit costs in 2026. DSD fees, AB+C portal, enhanced energy code, and processing times for contractors.',
        'date': '2026-04-06',
        'category': 'permit-costs',
        'city_link': '/permits/texas/austin',
        'city_name': 'Austin',
        'excerpt': 'Austin calculates permit fees as a percentage of project valuation, with plan review fees at 65% of the building permit fee.',
        'content': '''
<p>If you're a contractor or builder working in Austin, the Development Services Department (DSD) fee structure has changed significantly in recent years as the city tries to keep up with explosive growth. Austin's permitting system has gotten faster — median processing dropped from 45 days to 37 days as of February 2026 — but fees remain a significant line item.</p>
<p>This guide breaks down everything you need to know about building permit costs in Austin for 2026.</p>

<h2>Austin Building Permit Costs at a Glance</h2>
<p>Austin calculates building permit fees as a percentage of project valuation, with plan review fees typically set at 65% of the building permit fee. The current fee schedule became effective October 1, 2025.</p>
<p><strong>Residential permit costs:</strong></p>
<ul>
<li>Application processing fee: $91.73 (applies to all permit types)</li>
<li>Small projects (decks, pools, fences): $50–$300</li>
<li>Typical home remodel ($20,000–$40,000): approximately $500–$1,500</li>
<li>New 2,000 sq ft single-family home: $3,200–$4,300 total (including plan review)</li>
</ul>
<p><strong>Commercial permit costs:</strong></p>
<ul>
<li>Application processing fee: $91.73</li>
<li>Tenant improvements: $500–$5,000+</li>
<li>New commercial construction ($1M+): $25,000+</li>
<li>Fees typically range 1%–3% of overall construction cost</li>
</ul>
<p>Credit card service fee of 2.35% applies to all card payments.</p>

<h2>Where to Apply for a Building Permit in Austin</h2>
<ul>
<li><strong>Online portal:</strong> Austin Build + Connect (AB+C) at abc.austintexas.gov</li>
<li><strong>In-person:</strong> Permitting and Development Center, 6310 Wilhelmina Delco Drive</li>
<li><strong>Phone:</strong> 512-978-4504 for scheduling</li>
</ul>

<h2>How Long Does It Take to Get a Building Permit in Austin?</h2>
<ul>
<li><strong>Initial plan review:</strong> 15–25 business days depending on project type</li>
<li><strong>Resubmittals after corrections:</strong> approximately 10 business days</li>
<li><strong>Overall timeline:</strong> 6–12 weeks from application to permit issuance</li>
<li><strong>Median processing time (February 2026):</strong> 37 days</li>
</ul>

<h2>Special Requirements to Know About</h2>
<p><strong>Austin's energy code is more stringent than the state baseline.</strong> The city uses the 2024 IECC with local amendments. Plans that pass in Dallas or Houston may not pass in Austin without modifications.</p>
<p><strong>Texas Accessibility Standards (TAS) apply to all commercial projects.</strong></p>
<p><strong>Environmental review near waterways.</strong> Projects near creeks, the Colorado River, or within the Barton Springs Zone face additional environmental review.</p>
<p><strong>Contractor registration is mandatory.</strong> All contractors must be registered with the City of Austin before pulling permits.</p>

<h2>Pro Tips for Contractors</h2>
<p><strong>Register your AB+C portal account early.</strong> Both the general contractor and all trade contractors need portal accounts.</p>
<p><strong>Budget extra time for Barton Springs Zone projects.</strong> Environmental review can add weeks or months.</p>
<p><strong>Get energy compliance right the first time.</strong> Austin's enhanced energy code is the #1 source of plan review corrections.</p>
'''
    },
    {
        'slug': 'how-much-does-building-permit-cost-fort-worth',
        'title': 'How Much Does a Building Permit Cost in Fort Worth, TX? (2026 Guide)',
        'meta_description': 'Fort Worth building permit costs for 2026. Fee schedules, 7-day review times, third-party review discounts up to 70%.',
        'date': '2026-04-06',
        'category': 'permit-costs',
        'city_link': '/permits/texas/fort-worth',
        'city_name': 'Fort Worth',
        'excerpt': 'Fort Worth offers third-party review discounts of up to 70% and some of the fastest standard review times in DFW.',
        'content': '''
<p>If you're a contractor or builder working in Fort Worth, the Development Services Department offers one of the more straightforward permitting processes in the DFW metro. Fort Worth's fee structure is valuation-based with some notable cost-saving options — including third-party review discounts of up to 70%.</p>
<p>This guide breaks down everything you need to know about building permit costs in Fort Worth for 2026.</p>

<h2>Fort Worth Building Permit Costs at a Glance</h2>
<p>Fort Worth calculates building permit fees based on project valuation and square footage. The current fee schedule became effective October 1, 2024.</p>
<p><strong>Residential permit costs:</strong></p>
<ul>
<li>Residential remodels: starting at $112 (varies by number of trades involved)</li>
<li>New residential construction: starts at $96.84 for the first 30 sq ft</li>
<li>Typical home remodel: approximately $300–$800</li>
<li>New single-family home: $1,500–$5,000+ depending on size</li>
</ul>
<p><strong>Commercial permit costs:</strong></p>
<ul>
<li>New commercial construction: starts at $96.84 for the first $2,000 of value</li>
<li>Projects valued at $1M: approximately $7,000–$8,000 for the building permit alone</li>
<li>Large commercial projects ($5M+): $15,000–$40,000+ in total fees</li>
</ul>
<p><strong>Third-party review discounts (residential projects):</strong></p>
<ul>
<li>35% fee reduction: when both plan review and field inspections are performed by third parties</li>
<li>70% fee reduction: when plan review is by third party with city field inspections</li>
<li>55% fee reduction: when plan review is by city staff with third-party field inspections</li>
</ul>

<h2>Where to Apply for a Building Permit in Fort Worth</h2>
<ul>
<li><strong>Online portal:</strong> Accela Citizen Access at aca-prod.accela.com/CFW</li>
<li><strong>CFW Permit Assist:</strong> cfwpermit.fortworthtexas.gov</li>
<li><strong>Inspection scheduling:</strong> (817) 392-6370 (automated, available 24/7)</li>
</ul>

<h2>How Long Does It Take to Get a Building Permit in Fort Worth?</h2>
<ul>
<li><strong>Standard residential permits:</strong> 7 business days</li>
<li><strong>Standard commercial permits:</strong> 7 business days (initial review)</li>
<li><strong>Complex commercial projects:</strong> 4–8 weeks depending on complexity</li>
</ul>
<p>The 7-business-day standard review for both residential and commercial is notably fast compared to peer cities.</p>

<h2>Special Requirements to Know About</h2>
<p><strong>Third-party review can dramatically reduce costs.</strong> Fort Worth's 35–70% fee reduction for using third-party plan review and inspections is one of the most generous discount structures of any major Texas city.</p>
<p><strong>Concurrent multi-department review.</strong> Commercial applications are reviewed simultaneously by building, fire, zoning, and other departments.</p>
<p><strong>Digital submission is the standard.</strong> All plans must be submitted digitally through Accela.</p>

<h2>Pro Tips for Contractors</h2>
<p><strong>Use third-party review for residential volume work.</strong> The 70% fee reduction is enormous if you're building multiple homes per year.</p>
<p><strong>Submit your plat with the application.</strong> A certified copy of the property's recorded plat is required.</p>
<p><strong>Take advantage of the 7-day standard review.</strong> Fort Worth's fast turnaround is a competitive advantage.</p>
'''
    },
    {
        'slug': 'how-much-does-building-permit-cost-columbus',
        'title': 'How Much Does a Building Permit Cost in Columbus, OH? (2026 Guide)',
        'meta_description': 'Columbus building permit costs in 2026. Hourly plan review fees, Citizen Access Portal, and the Intel construction boom impact.',
        'date': '2026-04-06',
        'category': 'permit-costs',
        'city_link': '/permits/ohio/columbus',
        'city_name': 'Columbus',
        'excerpt': 'Columbus uses hourly plan review charges — $225/hour for residential, $500/hour for commercial — with a 30-day review commitment.',
        'content': '''
<p>If you're a contractor or builder working in Columbus, Ohio, the Building and Zoning Services department runs a relatively streamlined permitting process compared to coastal cities. Columbus updated its fee schedule in January 2026, and while the costs are moderate, the hourly plan review structure means submitting clean plans the first time makes a real difference.</p>
<p>This guide breaks down everything you need to know about building permit costs in Columbus for 2026.</p>

<h2>Columbus Building Permit Costs at a Glance</h2>
<p>Columbus uses a combination of flat fees and hourly plan review charges. The 2026 Combined Development Related Fee Schedule became effective January 22, 2026.</p>
<p><strong>Residential permit costs (1-3 family dwellings):</strong></p>
<ul>
<li>Plan review: $225 for the first hour, $125 for each additional hour</li>
<li>Certificate of Occupancy: $300</li>
<li>Small residential projects: $300–$800</li>
<li>Typical home remodel: approximately $500–$1,500</li>
<li>New single-family home: $2,000–$6,000+ depending on size and complexity</li>
</ul>
<p><strong>Commercial and multi-family permit costs (4+ units):</strong></p>
<ul>
<li>Plan review: $500 for the first hour, $500 for each additional hour</li>
<li>Certificate of Occupancy: $700</li>
<li>Small commercial tenant improvements: $1,000–$3,000</li>
<li>Large commercial construction ($1M+): $15,000–$50,000+</li>
</ul>
<p>All fees are non-refundable. Fees must be paid before review begins.</p>

<h2>Where to Apply for a Building Permit in Columbus</h2>
<ul>
<li><strong>Online portal:</strong> Citizen Access Portal at portal.columbus.gov (available 24/7)</li>
<li><strong>In-person:</strong> Building and Zoning Services, 111 N. Front Street</li>
<li><strong>Phone:</strong> 614-645-7562</li>
</ul>

<h2>How Long Does It Take to Get a Building Permit in Columbus?</h2>
<ul>
<li><strong>Plan review (once assigned):</strong> 30 days to issue approval or corrections</li>
<li><strong>Permit issuance (after full plan approval):</strong> 7–10 days</li>
<li><strong>Minor residential projects:</strong> 2–4 weeks total</li>
<li><strong>Commercial projects:</strong> 6–12 weeks depending on complexity</li>
</ul>
<p>The 30-day review window is a firm commitment.</p>

<h2>Special Requirements to Know About</h2>
<p><strong>Fee payment timing is unique.</strong> Columbus verifies your application first and then sends payment instructions via email. Review doesn't begin until fees are paid.</p>
<p><strong>Columbus is experiencing a construction boom.</strong> Intel's $20B+ chip fabrication complex has supercharged the regional construction market.</p>
<p><strong>Ohio does not require state contractor licensing for general contractors.</strong> However, Columbus may require specific local registrations depending on the type of work.</p>

<h2>Pro Tips for Contractors</h2>
<p><strong>Pay the invoice immediately.</strong> Review doesn't start until fees are paid.</p>
<p><strong>Use the Plan Review Adequacy Checklist.</strong> The city provides this specifically to help you submit complete applications.</p>
<p><strong>Submit complete plans the first time.</strong> At $500/hour for commercial plan review, every revision cycle is expensive.</p>
'''
    },
    {
        'slug': 'how-much-does-building-permit-cost-charlotte',
        'title': 'How Much Does a Building Permit Cost in Charlotte, NC? (2026 Guide)',
        'meta_description': 'Charlotte building permit costs for 2026. Per-trade fees, LUESA requirements, 7-9 day review times for contractors.',
        'date': '2026-04-06',
        'category': 'permit-costs',
        'city_link': '/permits/north-carolina/charlotte',
        'city_name': 'Charlotte',
        'excerpt': 'Charlotte uses a per-trade fee structure with separate permits for building, electrical, mechanical, and plumbing.',
        'content': '''
<p>If you're a contractor or builder working in Charlotte, the permitting process is handled by Mecklenburg County's Code Enforcement division (LUESA) rather than the city directly. The fee structure uses per-trade base fees plus construction-value-based calculations, and the review times are some of the fastest of any major Southeast city.</p>
<p>This guide breaks down everything you need to know about building permit costs in Charlotte for 2026.</p>

<h2>Charlotte Building Permit Costs at a Glance</h2>
<p>Charlotte/Mecklenburg County charges fees on a per-trade basis. Each trade (building, electrical, mechanical, plumbing) requires its own permit with its own fees.</p>
<p><strong>Residential permit costs:</strong></p>
<ul>
<li>Base permit fee: $60 per trade</li>
<li>Plan review fee: $45 per trade (where plan review is required)</li>
<li>Inspection fee: $45 per visit</li>
<li>A typical home remodel involving building + electrical + plumbing: approximately $300–$800</li>
<li>New single-family home (all trades): $1,500–$4,000+ depending on scope</li>
</ul>
<p><strong>Commercial permit costs:</strong></p>
<ul>
<li>Base permit fee: $60 per trade</li>
<li>Plan review fee: $45 per trade</li>
<li>Additional construction-value-based fees calculated per the LUESA Fee Ordinance</li>
<li>Large commercial construction ($1M+): $10,000–$30,000+</li>
</ul>
<p><strong>Fee estimator tool:</strong> Use the WebPermit Fee Estimator at webpermit.mecklenburgcountync.gov</p>

<h2>Where to Apply for a Building Permit in Charlotte</h2>
<ul>
<li><strong>Online portal:</strong> Accela Citizen Access (ACA) at aca-prod.accela.com/CHARLOTTE</li>
<li><strong>In-person:</strong> LUESA offices at 2145 Suttle Ave</li>
<li><strong>Phone:</strong> 704-336-8000 or 980-314-CODE (2633)</li>
</ul>
<p>All plans must be submitted as PDF files through Accela. File size limit is 40MB.</p>

<h2>How Long Does It Take to Get a Building Permit in Charlotte?</h2>
<ul>
<li><strong>Application completeness review:</strong> 1–2 business days</li>
<li><strong>Residential plan review:</strong> 7–9 business days from plan acceptance</li>
<li><strong>Commercial plan review:</strong> 9–10 business days (concurrent review)</li>
<li><strong>Larger commercial (10,000+ sq ft):</strong> approximately 15 days</li>
</ul>
<p><strong>North Carolina law requires residential reviews to begin within 15 business days, or fee refunds apply.</strong> Charlotte consistently beats this requirement.</p>

<h2>Special Requirements to Know About</h2>
<p><strong>Each trade requires a separate permit.</strong> Unlike some cities that issue a single permit, Charlotte requires separate permits for building, electrical, mechanical, and plumbing.</p>
<p><strong>Homeowner self-performance rules.</strong> Homeowners can pull permits for their own trade work with a Homeowner Trade Work Certificate of Ownership form.</p>
<p><strong>Projects under $40,000 may skip plan review — with exceptions.</strong> But work involving structural modifications, plumbing, HVAC, electrical, or roofing still needs plan review.</p>

<h2>Pro Tips for Contractors</h2>
<p><strong>Use the WebPermit Fee Estimator before bidding.</strong> Get exact fee calculations for your specific project.</p>
<p><strong>Compile all plans into one PDF.</strong> The 40MB single-file requirement is strict.</p>
<p><strong>Pull all trade permits at once.</strong> Coordinate with your subs to get all permit applications submitted together.</p>
<p><strong>Take advantage of the fast review times.</strong> Charlotte's 7–9 day residential review is faster than most peer cities.</p>
'''
    },
    # V80: Contractor leads posts (10 new)
    {
        'slug': 'find-construction-leads-new-york',
        'title': 'How to Find New Construction Leads in New York City Before Your Competition (2026)',
        'meta_description': 'How NYC subcontractors find new construction leads before the competition. DOB NOW monitoring, borough filtering, and outreach strategies.',
        'date': '2026-04-06',
        'category': 'contractor-leads',
        'city_link': '/permits/new-york-state/new-york',
        'city_name': 'New York City',
        'excerpt': 'In the most competitive construction market in America, the contractors who win consistently are the ones who find opportunities first.',
        'content': '''
<p>New York City is the most competitive construction market in America. There are over 65,000 licensed contractors in the five boroughs, and every one of them is chasing the same projects. The contractors who win consistently aren't the ones with the biggest ad budgets — they're the ones who find opportunities first.</p>

<h2>The Problem with Traditional Lead Services in NYC</h2>
<p>If you're a subcontractor in New York relying on Angi, HomeAdvisor, or Thumbtack, you already know the math doesn't work. A shared lead in Manhattan costs $75–150, and you're bidding against 4–5 other companies who got the same notification.</p>
<p>The bigger commercial players use Dodge Data or ConstructConnect, starting at $500–$2,000/month. Those services are built for firms chasing $10M+ projects — if you're doing $1–10M/year in revenue, most of what they show you is irrelevant or already spoken for.</p>

<h2>What the Smartest NYC Contractors Do Instead</h2>
<p>New York City's Department of Buildings processes tens of thousands of building permits every month across the five boroughs. Every permit filing is public record — it includes the project address, scope of work, building type, contractor of record, and estimated project value.</p>
<p>When a GC files a permit for a $5M gut renovation of a commercial floor in Midtown, that project needs mechanical, electrical, plumbing, fire protection, and a dozen other specialty trades. The sub who sees that permit on Day 1 and calls the GC has a massive advantage.</p>

<h2>How Building Permit Monitoring Works in NYC</h2>
<p>NYC's DOB NOW system records every permit application in real time. Here's what a typical workflow looks like:</p>
<ol>
<li><strong>Set up alerts</strong> for new commercial and large residential permits in your target boroughs</li>
<li><strong>Filter by trade</strong> — focus on permits that include your specialty work</li>
<li><strong>Filter by project size</strong> — set a minimum threshold</li>
<li><strong>Review daily</strong> — every morning, check the 10–20 new permits matching your criteria</li>
<li><strong>Make the call</strong> — contact the GC or owner's rep listed on the permit</li>
</ol>
<p>The key is speed. Being first to call puts you in the conversation before the bid list is finalized.</p>

<h2>What NYC Permit Data Tells You</h2>
<p>A typical DOB NOW filing includes the building address, work type (new building, alteration type 1, alteration type 2, demolition), the owner and applicant of record, the filing representative (usually an architect or engineer), the estimated job cost, and the building's existing and proposed use.</p>
<p>Compare that information to a shared lead on Angi that says "homeowner in Brooklyn needs AC repair" — and charges you $100 for the privilege of competing against four other companies.</p>

<h2>How to Get Started</h2>
<p>You can manually search DOB NOW's public portal, but the interface is designed for individual property lookups, not bulk lead generation. Building permit monitoring services like <a href="/permits/new-york-state/new-york">PermitGrab</a> aggregate NYC permit data, let you filter by borough, project type, and size, and send daily email alerts.</p>
<p>The ROI is straightforward: if permit monitoring helps you land one additional project per quarter that you wouldn't have found otherwise, the subscription pays for itself dozens of times over.</p>
'''
    },
    {
        'slug': 'find-construction-leads-philadelphia',
        'title': 'How to Find New Construction Leads in Philadelphia Before Your Competition (2026)',
        'meta_description': 'How Philadelphia subcontractors find new construction leads. L&I permit monitoring, relationship-driven market strategies.',
        'date': '2026-04-06',
        'category': 'contractor-leads',
        'city_link': '/permits/pennsylvania/philadelphia',
        'city_name': 'Philadelphia',
        'excerpt': 'Philadelphia\'s construction market is booming. Here\'s how smart contractors find projects before everyone else does.',
        'content': '''
<p>Philadelphia's construction market is booming. Between Center City high-rise renovations, University City institutional projects, and the wave of residential development spreading through neighborhoods like Fishtown, Brewerytown, and Point Breeze, there's more work available than most subcontractors realize. The problem isn't a lack of projects — it's finding them before everyone else does.</p>

<h2>The Problem with Traditional Lead Services in Philly</h2>
<p>If you're an HVAC, electrical, or plumbing contractor in Philadelphia relying on Angi or HomeAdvisor, you're fighting over the same shared leads as every other sub in the metro area. Those platforms charge $30–75 per lead and sell each one to 3–5 contractors.</p>
<p>For commercial work, ConstructConnect and Dodge Data start at $500–$2,000/month and focus on large institutional projects. If you're doing $1–5M/year in residential and light commercial work, most of those listings are irrelevant.</p>

<h2>What Growing Philly Contractors Do Instead</h2>
<p>Philadelphia's Department of Licenses & Inspections processes thousands of building permits every month. Every permit is public record and includes the project address, scope of work, estimated value, and contractor information.</p>
<p>When a general contractor pulls a permit for a $1.5M restaurant build-out in Rittenhouse Square, that project needs HVAC, electrical, plumbing, fire suppression, and probably several other trades. The sub who sees that permit filing the day it's issued and picks up the phone has a window of opportunity that closes fast.</p>

<h2>How Building Permit Monitoring Works in Philadelphia</h2>
<ol>
<li><strong>Set up alerts</strong> for new commercial and large residential permits in your service area</li>
<li><strong>Filter by trade</strong> — focus on permits that include your specialty</li>
<li><strong>Set a value threshold</strong> — focus on $50K+ projects worth your time</li>
<li><strong>Review daily</strong> — check the 5–15 new permits matching your criteria</li>
<li><strong>Make the call</strong> — contact the GC or owner listed on the permit</li>
</ol>
<p>Speed matters. Philadelphia's contractor community is smaller and more connected than NYC or LA, which means word travels fast and bid lists fill up quickly.</p>

<h2>The Philadelphia Advantage</h2>
<p>Philly has something that works in contractors' favor: the market is large enough to generate consistent volume but small enough that relationships matter. When you call a GC the day their permit is filed, you're not a faceless name — you're a local contractor who's paying attention.</p>
<p>The city's tax clearance requirement also means every permitted project has a property owner who is current on their taxes — a decent indicator that the project has real funding behind it.</p>

<h2>How to Get Started</h2>
<p>You can manually check L&I's eCLIPSE portal for new filings, but the system isn't designed for bulk lead generation. Building permit monitoring services like <a href="/permits/pennsylvania/philadelphia">PermitGrab</a> aggregate Philadelphia permit data, let you filter by project type and value, and send daily email alerts.</p>
'''
    },
    {
        'slug': 'find-construction-leads-san-diego',
        'title': 'How to Find New Construction Leads in San Diego Before Your Competition (2026)',
        'meta_description': 'How San Diego subcontractors find new construction leads. ADU boom, biotech construction, permit monitoring strategies.',
        'date': '2026-04-06',
        'category': 'contractor-leads',
        'city_link': '/permits/california/san-diego',
        'city_name': 'San Diego',
        'excerpt': 'San Diego\'s construction market is driven by military bases, biotech, ADUs, and coastal renovations. Here\'s how to find the best projects.',
        'content': '''
<p>San Diego's construction market is driven by a unique mix: military base expansions, biotech campus build-outs, a massive ADU boom, and coastal renovation projects that never seem to slow down. For subcontractors, there's consistent work across every trade — but the contractors who win the best projects are the ones who find them first.</p>

<h2>The Problem with Traditional Lead Services in San Diego</h2>
<p>Angi, HomeAdvisor, and Thumbtack charge $30–75 per shared lead, and they sell each one to multiple contractors. You're competing on price before you've had a real conversation.</p>
<p>For commercial work, Dodge Data and ConstructConnect start at $500+/month and focus on large-scale projects. If you're a specialty sub doing residential and light commercial — the bread and butter of the San Diego market — most of what those services offer isn't relevant.</p>

<h2>What Smart San Diego Contractors Do Instead</h2>
<p>San Diego's Development Services Department processes thousands of building permits every month. Every filing is public record and includes the project address, scope of work, permit type, contractor, and project valuation.</p>
<p>When a GC pulls a permit for a $2M lab build-out in Sorrento Valley or a $500K restaurant renovation in the Gaslamp Quarter, that project needs HVAC, electrical, plumbing, fire protection, and specialty trades.</p>

<h2>The San Diego ADU Opportunity</h2>
<p>San Diego's ADU market deserves special attention. The city processes hundreds of ADU permits per month, and each one represents an electrical, plumbing, and HVAC scope. For trade contractors, ADU permits represent a steady pipeline of right-sized jobs.</p>
<p>The property owner is already committed to the project (they've pulled the permit), the scope is well-defined, and the timeline is usually 3–6 months. Monitoring ADU permits specifically can fill your residential pipeline without spending a dollar on advertising.</p>

<h2>How Building Permit Monitoring Works in San Diego</h2>
<ol>
<li><strong>Set up alerts</strong> for new commercial, residential, and ADU permits in your service area</li>
<li><strong>Filter by trade</strong> — focus on permits that include your specialty</li>
<li><strong>Set a value threshold</strong> — skip the $1,000 jobs and focus on $25K+ projects</li>
<li><strong>Review daily</strong> — check the 10–20 new permits matching your criteria</li>
<li><strong>Make the call</strong> — contact the GC or property owner listed on the permit</li>
</ol>
<p>San Diego's data quality is excellent — the city was an early adopter of online permitting, and the Accela system captures detailed information that makes lead qualification fast and reliable.</p>

<h2>How to Get Started</h2>
<p>You can search San Diego's OpenDSD portal for individual permits, but the system is designed for property lookups, not lead generation. Building permit monitoring services like <a href="/permits/california/san-diego">PermitGrab</a> aggregate San Diego permit data and send daily email alerts.</p>
'''
    },
    {
        'slug': 'find-construction-leads-dallas',
        'title': 'How to Find New Construction Leads in Dallas Before Your Competition (2026)',
        'meta_description': 'How Dallas subcontractors find new construction leads. DFW metro monitoring, DallasNow data, corporate relocation pipeline.',
        'date': '2026-04-06',
        'category': 'contractor-leads',
        'city_link': '/permits/texas/dallas',
        'city_name': 'Dallas',
        'excerpt': 'Dallas is one of the hottest construction markets in America. Here\'s how to find the right projects before your competitors.',
        'content': '''
<p>Dallas is one of the hottest construction markets in America. Corporate relocations, data center builds, residential sprawl in every direction, and a commercial real estate market that shows no signs of slowing down — there's more work available for subcontractors than in almost any other U.S. metro. The challenge isn't finding work. It's finding the <em>right</em> work before your competitors do.</p>

<h2>The Problem with Traditional Lead Services in Dallas</h2>
<p>If you're relying on Angi or HomeAdvisor, you're paying $30–75 per lead that's simultaneously sent to 3–5 other companies. In a market this competitive, that means you're in a bidding war before you've even picked up the phone.</p>
<p>What's missing is the middle: the $100K–$2M commercial build-outs, tenant improvements, and new construction projects that represent the sweet spot for specialty subcontractors. Those projects show up in the city's building permit filings.</p>

<h2>What Growing Dallas Contractors Do Instead</h2>
<p>The Dallas Building Inspection Division processes thousands of building permits every month. Every filing is public record and includes the project address, permit type, contractor name, and project details.</p>
<p>When a general contractor files a permit for a $1.5M restaurant build-out in Deep Ellum or a $3M office renovation in Uptown, that project needs a full slate of subcontractors. The sub who sees that permit the day it's filed and makes the call has a significant timing advantage.</p>

<h2>How Building Permit Monitoring Works in Dallas</h2>
<ol>
<li><strong>Set up alerts</strong> for new commercial and large residential permits across the DFW metro</li>
<li><strong>Filter by trade</strong> — focus on permits that include your specialty</li>
<li><strong>Set a value threshold</strong> — focus on projects above $50K</li>
<li><strong>Review daily</strong> — check the 10–25 new permits matching your criteria each morning</li>
<li><strong>Make the call</strong> — contact the GC listed on the permit</li>
</ol>
<p>The key advantage is volume and timing. Dallas processes so many permits that even filtering to your specific trade and project size range, you're looking at dozens of qualified leads per week.</p>

<h2>The DFW Advantage</h2>
<p>The DFW metro's sprawling geography actually works in favor of permit-savvy contractors. Permit monitoring lets you see every project across the entire metro — from downtown Dallas to Plano to Fort Worth. GCs working on projects in less-established areas often struggle to find quality subs willing to travel, which means less competition for those bids.</p>

<h2>How to Get Started</h2>
<p>You can search the DallasNow portal for individual permits, but the system is built for applications and status tracking, not bulk lead generation. Building permit monitoring services like <a href="/permits/texas/dallas">PermitGrab</a> aggregate Dallas permit data and deliver daily email alerts matching your criteria.</p>
'''
    },
    {
        'slug': 'find-construction-leads-jacksonville',
        'title': 'How to Find New Construction Leads in Jacksonville Before Your Competition (2026)',
        'meta_description': 'How Jacksonville subcontractors find construction leads. 875 sq mi coverage, military/logistics projects, permit monitoring.',
        'date': '2026-04-06',
        'category': 'contractor-leads',
        'city_link': '/permits/florida/jacksonville',
        'city_name': 'Jacksonville',
        'excerpt': 'Jacksonville is the largest city by land area in the contiguous U.S. Here\'s how to find construction leads across all 875 square miles.',
        'content': '''
<p>Jacksonville is the largest city by land area in the contiguous United States, and its construction market reflects that scale. Military base projects at NAS Jacksonville and Mayport, massive logistics and warehouse development along the I-95 corridor, downtown revitalization, and a residential market that keeps pushing outward in every direction — there's consistent work for subcontractors across every trade.</p>

<h2>The Problem with Traditional Lead Services in Jacksonville</h2>
<p>Angi and HomeAdvisor charge $30–75 per shared lead, selling each one to 3–5 contractors. The close rate in a mid-size market like Jacksonville is typically 15–25% — which means you're paying $200–$500 per actual job when you factor in the leads you lose.</p>
<p>For commercial work, the big data services focus on large-scale projects. Jacksonville has its share of big projects, but the volume of mid-market work — $100K–$2M commercial build-outs — is where most specialty subs make their money.</p>

<h2>What Jacksonville's Best Contractors Do Instead</h2>
<p>Jacksonville's Building Inspection Division processes thousands of permits every month through the JaxEPICS system. Every filing is public record and includes the project address, scope of work, contractor of record, and permit type.</p>
<p>The contractor who sees that filing on Day 1 and makes the call is ahead of every other sub who's waiting for a bid invitation or hoping for a referral.</p>

<h2>Jacksonville's Geographic Advantage</h2>
<p>Jacksonville's 875 square miles of territory means projects are spread across a huge area. Most contractors focus on one or two areas of the city and miss projects in other zones. Permit monitoring gives you visibility across the entire city — Westside industrial, Southside commercial, Beaches residential, downtown mixed-use, and the suburban growth corridors.</p>
<p>GCs working on projects in less-trafficked parts of Jacksonville often struggle to find subs willing to travel. If you're monitoring permits across the full metro, you can pick up projects that other subs ignore simply because they don't know about them.</p>

<h2>How Building Permit Monitoring Works in Jacksonville</h2>
<ol>
<li><strong>Set up alerts</strong> for new commercial, multi-family, and large residential permits across Duval County</li>
<li><strong>Filter by trade</strong> — focus on permits involving your specialty</li>
<li><strong>Set a value floor</strong> — focus on $25K+ projects</li>
<li><strong>Review daily</strong> — check the 5–15 new permits matching your criteria</li>
<li><strong>Make the call</strong> — reach out to the GC or developer listed on the permit</li>
</ol>

<h2>How to Get Started</h2>
<p>You can search JaxEPICS for individual permits, but the system is designed for application tracking, not lead generation. Building permit monitoring services like <a href="/permits/florida/jacksonville">PermitGrab</a> aggregate Jacksonville permit data and send daily email alerts matching your criteria.</p>
'''
    },
    {
        'slug': 'find-construction-leads-san-jose',
        'title': 'How to Find New Construction Leads in San Jose Before Your Competition (2026)',
        'meta_description': 'How San Jose subcontractors find construction leads in Silicon Valley. High-value permits, tech campus builds, ADU pipeline.',
        'date': '2026-04-06',
        'category': 'contractor-leads',
        'city_link': '/permits/california/san-jose',
        'city_name': 'San Jose',
        'excerpt': 'San Jose sits at the heart of Silicon Valley, where even modest home values mean substantial project budgets.',
        'content': '''
<p>San Jose sits at the heart of Silicon Valley, and the construction market reflects it. Tech campus expansions, data center builds, high-density housing development, and a residential renovation market fueled by some of the highest home values in America — there's serious work for subcontractors across every trade.</p>

<h2>The Problem with Traditional Lead Services in San Jose</h2>
<p>The San Jose market has a unique problem: the average project value is much higher than the national average, but the shared lead platforms don't distinguish between a $5,000 bathroom refresh and a $200,000 whole-home renovation. You're paying $50–100 per shared lead in a market where the gap between a small job and a significant project is enormous.</p>
<p>For commercial work, Dodge Data and ConstructConnect cover the big tech campus expansions, but if you're focused on the $100K–$3M commercial and residential market, those platforms have limited coverage of the projects that actually match your business.</p>

<h2>What Smart Bay Area Contractors Do Instead</h2>
<p>San Jose's PBCE department processes thousands of permits every month. Every filing is public record with the project address, scope, permit type, contractor of record, and often the project valuation.</p>
<p>In San Jose specifically, the high home values mean even residential permit filings represent substantial project values. A kitchen remodel in Almaden Valley or an addition in Cambrian Park routinely runs $150,000–$400,000. These aren't leads you find on HomeAdvisor.</p>

<h2>The Silicon Valley Factor</h2>
<p>San Jose's proximity to the tech industry creates a unique dynamic. Corporate campus expansions, lab build-outs, and data center construction represent steady commercial demand. When tech companies are expanding, the commercial permit pipeline is deep. When the residential market is hot, ADUs and home renovations fill the gaps.</p>
<p>This diversification means permit monitoring in San Jose generates leads across a broader range of project types than most cities.</p>

<h2>How Building Permit Monitoring Works in San Jose</h2>
<ol>
<li><strong>Set up alerts</strong> for new commercial and residential permits in Santa Clara County</li>
<li><strong>Filter by trade</strong> — focus on permits involving your specialty</li>
<li><strong>Set a value threshold</strong> — in the Bay Area, set this higher than elsewhere ($50K+ is reasonable)</li>
<li><strong>Review daily</strong> — check the 10–20 new permits matching your criteria</li>
<li><strong>Make the call</strong> — reach out to the GC, architect, or property owner</li>
</ol>

<h2>How to Get Started</h2>
<p>You can search individual permits on SJPermits.org, but the system is designed for property-specific lookups. Building permit monitoring services like <a href="/permits/california/san-jose">PermitGrab</a> aggregate San Jose permit data and deliver daily email alerts.</p>
'''
    },
    {
        'slug': 'find-construction-leads-austin',
        'title': 'How to Find New Construction Leads in Austin Before Your Competition (2026)',
        'meta_description': 'How Austin subcontractors find new construction leads. Fastest-growing market, AB+C permit data, suburban expansion pipeline.',
        'date': '2026-04-06',
        'category': 'contractor-leads',
        'city_link': '/permits/texas/austin',
        'city_name': 'Austin',
        'excerpt': 'Austin\'s construction market has been on a tear for the better part of a decade. Here\'s how to find projects first.',
        'content': '''
<p>Austin's construction market has been on a tear for the better part of a decade, and 2026 shows no signs of slowing down. Corporate relocations, the tech sector buildout, downtown residential towers, and suburban sprawl stretching from Georgetown to San Marcos — the volume of work available for subcontractors is enormous.</p>

<h2>The Problem with Traditional Lead Services in Austin</h2>
<p>Austin's rapid growth has attracted contractors from across Texas, which means the competition for shared leads is fierce. Angi and HomeAdvisor charge $30–75 per lead and sell each one to 3–5 contractors. In a market flooded with new entrants, your shared lead close rate is probably lower than it was five years ago.</p>
<p>For commercial work, Dodge Data and ConstructConnect cover the headline projects, but the sweet spot for most Austin subs is the $100K–$3M project range. Those projects don't show up on Dodge.</p>

<h2>What Growing Austin Contractors Do Instead</h2>
<p>Austin's Development Services Department processes thousands of building permits every month through the Austin Build + Connect (AB+C) portal. Every permit filing is public record and includes the project address, scope of work, contractor information, and permit type.</p>
<p>Austin's explosive growth means the volume is there. On any given week, there are hundreds of new permit filings across the metro.</p>

<h2>The Austin Growth Advantage</h2>
<p>Austin's growth creates a specific advantage for permit-savvy contractors: there are constantly new GCs entering the market who don't have established sub relationships. A general contractor from Houston or Dallas who just landed their first Austin project needs local subs — and the sub who calls them the day their permit is filed is the one they remember.</p>
<p>The suburban expansion adds another dimension. Permits in Cedar Park, Round Rock, Georgetown, Kyle, and Buda represent additional pipeline that Austin-based subs can service.</p>

<h2>How Building Permit Monitoring Works in Austin</h2>
<ol>
<li><strong>Set up alerts</strong> for new commercial, multi-family, and large residential permits across Travis and Williamson counties</li>
<li><strong>Filter by trade</strong> — focus on permits involving your specialty</li>
<li><strong>Set a value threshold</strong> — focus on $50K+ projects</li>
<li><strong>Review daily</strong> — check the 10–20 new permits matching your criteria</li>
<li><strong>Make the call</strong> — contact the GC or developer listed on the permit</li>
</ol>

<h2>How to Get Started</h2>
<p>You can search individual permits on the AB+C portal, but the system is built for application filing and status tracking, not bulk lead generation. Building permit monitoring services like <a href="/permits/texas/austin">PermitGrab</a> aggregate Austin permit data and send daily email alerts.</p>
'''
    },
    {
        'slug': 'find-construction-leads-fort-worth',
        'title': 'How to Find New Construction Leads in Fort Worth Before Your Competition (2026)',
        'meta_description': 'How Fort Worth subcontractors find construction leads. Alliance Corridor, 7-day permits, DFW dual-market monitoring strategy.',
        'date': '2026-04-06',
        'category': 'contractor-leads',
        'city_link': '/permits/texas/fort-worth',
        'city_name': 'Fort Worth',
        'excerpt': 'Fort Worth has quietly become one of the most active construction markets in Texas. Here\'s how to find leads across DFW.',
        'content': '''
<p>Fort Worth has quietly become one of the most active construction markets in Texas. While Dallas gets most of the headlines, Fort Worth's combination of downtown revitalization, massive logistics development along I-35W, residential growth in Alliance and Walsh Ranch, and the Panther Island project creates a construction pipeline that's deep, diverse, and growing.</p>

<h2>The Problem with Traditional Lead Services in Fort Worth</h2>
<p>The DFW metro has one of the highest concentrations of contractors in the country, which makes shared lead platforms expensive and inefficient. Angi and HomeAdvisor charge $30–75 per lead that's sent to 3–5 other contractors.</p>
<p>For commercial work, Dodge Data and ConstructConnect focus on large-scale DFW projects. But the bread and butter of Fort Worth's construction market — the $100K–$2M tenant improvements, warehouse conversions, restaurant build-outs — rarely shows up on those platforms.</p>

<h2>What Fort Worth's Best Contractors Do Instead</h2>
<p>Fort Worth's Development Services Department processes thousands of permits every month. Every filing is public record and includes the project address, permit type, scope of work, and contractor information.</p>
<p>Fort Worth's 7-business-day standard review means projects move fast. A permit filed on Monday could be issued by the following Monday. Contractors who are monitoring filings in real time can reach out to GCs before the permits are even approved.</p>

<h2>Fort Worth vs. Dallas: Why Monitor Both</h2>
<p>Fort Worth and Dallas are separate markets with distinct construction cultures, but they're close enough that most subs can work both. Monitoring permits across both cities doubles your pipeline without adding significant travel time. A mechanical contractor based in Mid-Cities can bid projects in downtown Fort Worth and Uptown Dallas with equal ease.</p>
<p>GCs also frequently work both markets, so a relationship built on a Fort Worth project can lead to invitations on Dallas work and vice versa.</p>

<h2>How Building Permit Monitoring Works in Fort Worth</h2>
<ol>
<li><strong>Set up alerts</strong> for new commercial and large residential permits across Tarrant County</li>
<li><strong>Filter by trade</strong> — focus on permits involving your specialty</li>
<li><strong>Set a value threshold</strong> — focus on $50K+ projects</li>
<li><strong>Review daily</strong> — check the 10–20 new permits matching your criteria</li>
<li><strong>Make the call</strong> — contact the GC listed on the permit</li>
</ol>

<h2>How to Get Started</h2>
<p>You can search individual permits on Fort Worth's Accela portal, but the system is designed for application filing and tracking, not lead generation. Building permit monitoring services like <a href="/permits/texas/fort-worth">PermitGrab</a> aggregate Fort Worth permit data and deliver daily alerts.</p>
'''
    },
    {
        'slug': 'find-construction-leads-columbus',
        'title': 'How to Find New Construction Leads in Columbus Before Your Competition (2026)',
        'meta_description': 'How Columbus subcontractors find construction leads. Intel boom, data center pipeline, Midwest market permit monitoring.',
        'date': '2026-04-06',
        'category': 'contractor-leads',
        'city_link': '/permits/ohio/columbus',
        'city_name': 'Columbus',
        'excerpt': 'Columbus is in the middle of the biggest construction boom in its history, driven by Intel and data center development.',
        'content': '''
<p>Columbus, Ohio is in the middle of the biggest construction boom in its history. Intel's $20B+ chip fabrication complex, billions in data center development, a downtown residential renaissance, and Ohio State University's continuous campus expansion have created a construction pipeline that's attracting contractors from across the Midwest.</p>

<h2>The Problem with Traditional Lead Services in Columbus</h2>
<p>Columbus has traditionally been a mid-market city where word of mouth and established relationships drove most subcontracting work. But the Intel-driven construction surge has changed the game. National contractors are flooding in, shared lead platforms have gotten more expensive and competitive, and the volume of available work has outgrown the old referral networks.</p>
<p>Angi and HomeAdvisor charge $30–75 per shared lead, with declining close rates as the market gets more crowded.</p>

<h2>What Growing Columbus Contractors Do Instead</h2>
<p>Columbus's Building and Zoning Services department processes thousands of permits every month. Every filing is public record and includes the project address, scope of work, contractor information, and permit type.</p>
<p>Columbus's construction boom has also created a unique dynamic: many GCs working in the market are from out of state and don't have established local sub relationships. They need reliable local trades — and the sub who calls first wins.</p>

<h2>The Intel Boom Effect</h2>
<p>The Intel construction boom deserves special attention. The scale of the New Albany fab complex is creating a ripple effect across central Ohio: housing permits for the construction workforce, commercial permits for restaurants and services near the site, infrastructure permits for roads and utilities, and data center permits from Intel's supply chain partners.</p>
<p>For subs positioned in the right trades — electrical is especially in demand — the Intel ecosystem represents years of sustained work.</p>

<h2>How Building Permit Monitoring Works in Columbus</h2>
<ol>
<li><strong>Set up alerts</strong> for new commercial, multi-family, and large residential permits across Franklin County</li>
<li><strong>Filter by trade</strong> — focus on permits involving your specialty</li>
<li><strong>Set a value threshold</strong> — focus on $50K+ projects</li>
<li><strong>Review daily</strong> — check the 5–15 new permits matching your criteria</li>
<li><strong>Make the call</strong> — contact the GC or developer listed on the permit</li>
</ol>

<h2>How to Get Started</h2>
<p>You can search individual permits on Columbus's Citizen Access Portal, but the system is designed for property lookups and application tracking, not lead generation. Building permit monitoring services like <a href="/permits/ohio/columbus">PermitGrab</a> aggregate Columbus permit data and deliver daily email alerts.</p>
'''
    },
    {
        'slug': 'find-construction-leads-charlotte',
        'title': 'How to Find New Construction Leads in Charlotte Before Your Competition (2026)',
        'meta_description': 'How Charlotte subcontractors find construction leads. Financial sector expansion, per-trade permits, Southeast market monitoring.',
        'date': '2026-04-06',
        'category': 'contractor-leads',
        'city_link': '/permits/north-carolina/charlotte',
        'city_name': 'Charlotte',
        'excerpt': 'Charlotte is the fastest-growing major city in the Southeast. Here\'s how contractors find leads before the competition.',
        'content': '''
<p>Charlotte is the fastest-growing major city in the Southeast that isn't named Nashville. Financial sector expansion, a healthcare construction boom driven by Atrium Health and Novant, residential development radiating outward from Uptown, and a commercial market fueled by corporate relocations — there's more subcontracting work available than most contractors realize.</p>

<h2>The Problem with Traditional Lead Services in Charlotte</h2>
<p>Charlotte's growth has attracted contractors from across the Carolinas, making the shared lead market more competitive every year. Angi and HomeAdvisor charge $30–75 per lead that's simultaneously sent to 3–5 other contractors.</p>
<p>For commercial work, Dodge Data and ConstructConnect cover the large institutional projects, but the mid-market work that most specialty subs depend on isn't their focus.</p>

<h2>What Charlotte's Best Contractors Do Instead</h2>
<p>Mecklenburg County's Code Enforcement division processes thousands of building permits every month. Every filing is public record and includes the project address, permit type, scope of work, and contractor information.</p>
<p>Charlotte's fast review times (7–9 business days for residential, 9–10 for commercial) mean projects move from permit to construction quickly. The subs who are watching new filings in real time have an even bigger timing advantage than in slower-moving markets.</p>

<h2>The Charlotte Advantage</h2>
<p>Charlotte has several factors that make permit-based lead generation especially effective. The per-trade permit system means the data is granular — you can see exactly which trades are needed on each project. The fast review times mean projects move quickly from filing to construction. And the market's growth means there are always new GCs entering who need local sub relationships.</p>
<p>The surrounding counties — Cabarrus, Union, Iredell, Gaston, York (SC) — represent additional pipeline that Charlotte-based subs can service.</p>

<h2>How Building Permit Monitoring Works in Charlotte</h2>
<ol>
<li><strong>Set up alerts</strong> for new commercial, multi-family, and large residential permits across Mecklenburg County</li>
<li><strong>Filter by trade</strong> — focus on permits involving your specialty</li>
<li><strong>Set a value threshold</strong> — focus on $25K+ projects</li>
<li><strong>Review daily</strong> — check the 5–15 new permits matching your criteria</li>
<li><strong>Make the call</strong> — contact the GC or developer listed on the permit</li>
</ol>

<h2>How to Get Started</h2>
<p>You can search individual permits on the Accela Citizen Access portal, but the system is designed for application filing and tracking, not lead generation. Building permit monitoring services like <a href="/permits/north-carolina/charlotte">PermitGrab</a> aggregate Charlotte permit data and deliver daily email alerts.</p>
'''
    },
    # V81: 35 new blog posts (Batch 4-6 cities + gap-fills)
    # Batch 4: Indianapolis, San Francisco, Seattle, Denver, Washington DC (permit costs + leads)
    {
        'slug': 'how-much-does-building-permit-cost-indianapolis',
        'title': 'How Much Does a Building Permit Cost in Indianapolis? (2026 Guide)',
        'meta_description': 'Indianapolis building permit costs for 2026. DBNS fees, Accela portal, valuation-based calculations, and tips for contractors.',
        'date': '2026-04-06',
        'category': 'permit-costs',
        'city_link': '/permits/indiana/indianapolis',
        'city_name': 'Indianapolis',
        'excerpt': 'Indianapolis calculates permit fees based on project valuation and construction type through the DBNS Accela portal.',
        'content': '''
<p>If you're a contractor or builder working in Indianapolis, the Department of Business & Neighborhood Services (DBNS) handles all building permits through a valuation-based fee system. Indy's fees are currently lower than most peer cities — though increases are proposed for 2026 — and the Accela online portal has made filing significantly easier than the old paper process.</p>

<h2>Indianapolis Building Permit Costs at a Glance</h2>
<p>Indianapolis calculates permit fees based on project valuation and construction type.</p>
<p><strong>Residential permit costs:</strong></p>
<ul>
<li>Small residential projects (minor repairs): $200–$500</li>
<li>Typical home remodel ($20,000–$40,000): approximately $400–$1,200</li>
<li>New single-family home construction: $2,000–$6,000+ depending on size</li>
<li>Trade permits (electrical, plumbing, mechanical) are separate</li>
</ul>
<p><strong>Commercial permit costs:</strong></p>
<ul>
<li>Class 1 initial plan review: $478 (includes 3 hours of review)</li>
<li>Accelerated plan review option: $448 (includes 1 hour of review)</li>
<li>Tenant improvements: $2,000–$15,000 in total city fees</li>
<li>Large commercial projects: $10,000–$25,000+</li>
</ul>
<p><strong>2026 fee increases:</strong> Indianapolis has proposed raising permitting fees as part of its 2026 budget. Current fees are acknowledged as "well below those of peer cities."</p>

<h2>Where to Apply for a Building Permit in Indianapolis</h2>
<ul>
<li><strong>Online portal:</strong> Accela Citizen Access at aca-prod.accela.com/indy</li>
<li><strong>In-person:</strong> 1200 Madison Ave, Suite 100, Indianapolis, IN 46225</li>
<li><strong>Phone:</strong> 317-327-8700</li>
<li><strong>Email:</strong> permitquestions@indy.gov</li>
</ul>

<h2>How Long Does It Take to Get a Building Permit in Indianapolis?</h2>
<ul>
<li><strong>Initial plan review:</strong> 15–20 business days (3–4 weeks)</li>
<li><strong>Simpler projects:</strong> 2–3 weeks</li>
<li><strong>Full city plan review (commercial):</strong> 3–6 weeks</li>
<li><strong>Complex commercial projects:</strong> 4–12 weeks with multiple review cycles</li>
</ul>

<h2>Pro Tips for Contractors</h2>
<p><strong>Plan for 2–3 correction cycles on commercial work.</strong> Most commercial projects in Indy go through multiple rounds of review comments.</p>
<p><strong>Get your Marion County registration current.</strong> This is the #1 holdback for contractors new to the Indy market.</p>
'''
    },
    {
        'slug': 'how-much-does-building-permit-cost-san-francisco',
        'title': 'How Much Does a Building Permit Cost in San Francisco? (2026 Guide)',
        'meta_description': 'San Francisco building permit costs for 2026. DBI fees, development impact fees, seismic retrofit requirements, and tips for contractors.',
        'date': '2026-04-06',
        'category': 'permit-costs',
        'city_link': '/permits/california/san-francisco',
        'city_name': 'San Francisco',
        'excerpt': 'San Francisco permit costs can run 6–9% of your construction budget due to DBI fees, development impact fees, and historic preservation requirements.',
        'content': '''
<p>If you're a contractor or builder working in San Francisco, brace yourself: the permitting process is one of the most complex and expensive in the country. Between the Department of Building Inspection (DBI), SF Planning, development impact fees, and historic preservation requirements, total permit costs can run 6–9% of your construction budget.</p>

<h2>San Francisco Building Permit Costs at a Glance</h2>
<p>San Francisco uses a tiered, valuation-based fee structure calculated using Table 1A-A of the SF Building Code.</p>
<p><strong>Residential permit costs:</strong></p>
<ul>
<li>Base permit fee: starts at $182 for projects up to $2,000 in value</li>
<li>Typical home remodel ($50,000–$100,000): approximately $1,500–$4,000 in DBI fees alone</li>
<li>New residential construction: $10,000–$50,000+ in combined DBI and Planning fees</li>
<li>Lead Safe Work Program surcharge: 1% for buildings constructed before 1979</li>
</ul>
<p><strong>Commercial permit costs:</strong></p>
<ul>
<li>Base permit fee starts at $833+ depending on project valuation</li>
<li>Total permit costs typically run 6–9% of total building costs</li>
<li>Large commercial projects ($1M+): $50,000–$500,000+ in combined permit and impact fees</li>
</ul>

<h2>Where to Apply for a Building Permit in San Francisco</h2>
<ul>
<li><strong>SF Permit Center:</strong> 49 South Van Ness Ave, 2nd Floor</li>
<li><strong>PermitSF Portal:</strong> sf.gov/permitsf</li>
<li><strong>DBI Online Permit System:</strong> sfdbi.org/onlinepermit</li>
</ul>

<h2>How Long Does It Take to Get a Building Permit in San Francisco?</h2>
<ul>
<li><strong>Over-the-counter permits:</strong> Same day for qualifying projects</li>
<li><strong>Projects requiring neighborhood notification:</strong> 2–4 months</li>
<li><strong>Average approval timeline:</strong> approximately 280 days — down from 605 days in early 2024</li>
</ul>

<h2>Special Requirements to Know About</h2>
<p><strong>Seismic retrofit requirements are mandatory for certain buildings.</strong> The Mandatory Soft-Story Retrofit Program requires seismic upgrades for wood-frame buildings with soft stories.</p>
<p><strong>Development impact fees are substantial.</strong> New construction triggers fees for transit, parks, schools, and affordable housing that can add tens of thousands to the project budget.</p>
'''
    },
    {
        'slug': 'how-much-does-building-permit-cost-seattle',
        'title': 'How Much Does a Building Permit Cost in Seattle? (2026 Guide)',
        'meta_description': 'Seattle building permit costs for 2026. SDCI 18% fee increase, $292/hour rates, seismic retrofit discounts, and tips for contractors.',
        'date': '2026-04-06',
        'category': 'permit-costs',
        'city_link': '/permits/washington/seattle',
        'city_name': 'Seattle',
        'excerpt': 'Seattle raised permit fees 18% for 2026, with an hourly rate of $292. Understanding this system is critical for accurate bidding.',
        'content': '''
<p>If you're a contractor or builder working in Seattle, prepare for some of the highest permit fees on the West Coast. The Seattle Department of Construction and Inspections (SDCI) raised construction permit and Master Use Permit fees by 18% for 2026, with most other fees up 6.5%.</p>

<h2>Seattle Building Permit Costs at a Glance</h2>
<p>Seattle uses a valuation-based fee structure with an hourly rate of $292 for 2026. You'll pay approximately 75% of estimated fees upfront.</p>
<p><strong>Residential permit costs:</strong></p>
<ul>
<li>Small residential projects (bathroom remodel, deck): $500–$1,500</li>
<li>500 sq ft backyard cottage (DADU): approximately $3,400+ in permit and plan review fees</li>
<li>New single-family home: $8,000–$20,000+ depending on size</li>
</ul>
<p><strong>Commercial permit costs:</strong></p>
<ul>
<li>Small commercial tenant improvements: $3,000–$8,000</li>
<li>35-unit apartment building: approximately $48,741 total fees</li>
<li>Large commercial construction ($1M+): $40,000–$100,000+</li>
</ul>

<h2>Where to Apply for a Building Permit in Seattle</h2>
<ul>
<li><strong>Online portal:</strong> Seattle Services Portal — start with a Building & Land Use Pre-Application</li>
<li><strong>SDCI main office:</strong> 700 5th Avenue, Suite 2000</li>
<li><strong>Phone:</strong> 206-684-8600</li>
</ul>

<h2>Special Requirements to Know About</h2>
<p><strong>The 18% fee increase is the headline.</strong> Construction permit and Master Use Permit fees jumped 18% year-over-year.</p>
<p><strong>Seismic retrofit discount.</strong> Seattle offers a 50% fee reduction for projects contributing to seismic retrofit of unreinforced masonry (URM) buildings — new for 2026.</p>
<p><strong>75% upfront payment.</strong> Unlike most cities, Seattle requires approximately 75% of estimated fees when you submit plans.</p>
'''
    },
    {
        'slug': 'how-much-does-building-permit-cost-denver',
        'title': 'How Much Does a Building Permit Cost in Denver? (2026 Guide)',
        'meta_description': 'Denver building permit costs for 2026. 180-day approval guarantee, e-Permits portal fees, green building requirements, and tips for contractors.',
        'date': '2026-04-06',
        'category': 'permit-costs',
        'city_link': '/permits/colorado/denver',
        'city_name': 'Denver',
        'excerpt': 'Denver offers a 180-day permit approval guarantee — if they miss the deadline, you get up to $10,000 back.',
        'content': '''
<p>If you're a contractor or builder working in Denver, the Community Planning and Development department handles all building permits through a valuation-based fee system. Denver made headlines in 2025 with a new 180-day permit approval guarantee — if they miss the deadline, you get up to $10,000 back.</p>

<h2>Denver Building Permit Costs at a Glance</h2>
<p>Denver calculates permit fees based on overall construction project value.</p>
<p><strong>Residential permit costs:</strong></p>
<ul>
<li>Small residential projects (minor repairs): $200–$600</li>
<li>Typical home remodel ($20,000–$50,000): approximately $500–$2,000</li>
<li>New single-family home: $3,000–$12,000+ depending on size</li>
<li>Soils report required for new dwellings (additional $1,000–$3,000)</li>
</ul>
<p><strong>Commercial permit costs:</strong></p>
<ul>
<li>Small tenant improvements: $1,000–$5,000</li>
<li>Large commercial construction ($1M+): $20,000–$60,000+</li>
</ul>

<h2>Where to Apply for a Building Permit in Denver</h2>
<ul>
<li><strong>Online portal:</strong> Denver e-Permits at aca-prod.accela.com/DENVER</li>
<li><strong>In-person:</strong> 201 W. Colfax Ave, Dept. 205</li>
<li><strong>Phone:</strong> 720-865-2700</li>
</ul>

<h2>The 180-Day Guarantee</h2>
<p>If Denver doesn't approve your permit within 180 days from submittal, eligible projects receive up to $10,000 in refunds. The city publishes an interactive dashboard updated daily showing actual review times.</p>

<h2>Special Requirements to Know About</h2>
<p><strong>Green building requirements for large commercial.</strong> Commercial buildings over 25,000 gross sq ft must comply with green building programs and install cool roofs.</p>
<p><strong>Soils reports are required for new residential construction.</strong> Denver's expansive clay soils make geotechnical investigation mandatory.</p>
'''
    },
    {
        'slug': 'how-much-does-building-permit-cost-washington-dc',
        'title': 'How Much Does a Building Permit Cost in Washington, DC? (2026 Guide)',
        'meta_description': 'Washington DC building permit costs for 2026. DOB fees, historic district rules, ProjectDox system, and tips for contractors.',
        'date': '2026-04-06',
        'category': 'permit-costs',
        'city_link': '/permits/washington-dc/washington',
        'city_name': 'Washington DC',
        'excerpt': 'DC calculates permit fees based on signed construction contract cost, with unique rules for historic districts covering much of the city.',
        'content': '''
<p>If you're a contractor or builder working in Washington, DC, the Department of Buildings (DOB) handles all construction permits through a valuation-based fee system. DC's permitting process has unique complications — historic districts covering large swaths of the city, federal overlay zones, and Commission of Fine Arts review.</p>

<h2>Washington DC Building Permit Costs at a Glance</h2>
<p>DC calculates permit fees based on the signed construction contract cost (not just the estimate).</p>
<p><strong>Residential permit costs:</strong></p>
<ul>
<li>Projects $1,001–$1,000,000: $37 for the first $1,000, plus $18.50 for each additional $1,000</li>
<li>A $50,000 kitchen remodel: approximately $944 in permit fees</li>
<li>Historic district properties: reduced rate of $36.30 sole permit fee</li>
</ul>
<p><strong>Commercial permit costs:</strong></p>
<ul>
<li>Projects over $1,000,000: $18,648 for the first $1M, plus $15.50 for each additional $1,000</li>
<li>A $2M commercial project: approximately $34,148 in permit fees</li>
</ul>

<h2>Where to Apply for a Building Permit in DC</h2>
<ul>
<li><strong>Online portal:</strong> ProjectDox at dob.dc.gov/projectdox</li>
<li><strong>In-person payment:</strong> Office of Tax and Revenue, 1101 4th Street SW</li>
<li><strong>Phone:</strong> 202-442-4400</li>
</ul>

<h2>Special Requirements to Know About</h2>
<p><strong>Historic districts cover a huge portion of DC.</strong> Georgetown, Capitol Hill, Dupont Circle, and dozens of other neighborhoods are designated historic districts requiring design review.</p>
<p><strong>The reduced historic permit fee is a silver lining.</strong> Properties in historic districts pay a flat $36.30 permit fee instead of the standard valuation-based calculation.</p>
<p><strong>Federal overlay zones add another layer.</strong> Projects near the U.S. Capitol or major federal buildings may require Commission of Fine Arts review.</p>
'''
    },
    {
        'slug': 'find-construction-leads-indianapolis',
        'title': 'How to Find New Construction Leads in Indianapolis Before Your Competition (2026)',
        'meta_description': 'How Indianapolis subcontractors find construction leads. Logistics boom, Eli Lilly expansion, permit monitoring strategies.',
        'date': '2026-04-06',
        'category': 'contractor-leads',
        'city_link': '/permits/indiana/indianapolis',
        'city_name': 'Indianapolis',
        'excerpt': 'Indianapolis is one of the most underrated construction markets in the Midwest. Here\'s how to find projects first.',
        'content': '''
<p>Indianapolis is one of the most underrated construction markets in the Midwest. Downtown revitalization, massive logistics and distribution center development along the I-65 and I-70 corridors, Eli Lilly's continued campus expansion, and a residential market that's growing steadily — there's consistent work for subcontractors across every trade.</p>

<h2>The Problem with Traditional Lead Services in Indianapolis</h2>
<p>The Indianapolis market is large enough to generate real volume but small enough that shared leads create fierce competition. Angi and HomeAdvisor charge $30–75 per lead sent to 3–5 contractors simultaneously.</p>

<h2>What Growing Indianapolis Contractors Do Instead</h2>
<p>The Department of Business & Neighborhood Services (DBNS) processes thousands of permits every month through the Accela portal. Every filing is public record with the project address, scope, contractor, and permit type.</p>
<p>Indianapolis is also seeing an influx of out-of-state GCs attracted by the logistics construction boom. These GCs need local subs — and the contractor who calls them first gets the relationship.</p>

<h2>How Building Permit Monitoring Works in Indianapolis</h2>
<ol>
<li><strong>Set up alerts</strong> for new commercial, multi-family, and large residential permits across Marion County</li>
<li><strong>Filter by trade</strong> — focus on permits involving your specialty</li>
<li><strong>Set a value threshold</strong> — focus on $25K+ projects</li>
<li><strong>Review daily</strong> — check the 5–15 new permits matching your criteria</li>
<li><strong>Make the call</strong> — contact the GC listed on the permit</li>
</ol>

<h2>How to Get Started</h2>
<p>Building permit monitoring services like <a href="/permits/indiana/indianapolis">PermitGrab</a> aggregate Indianapolis permit data, filter by project type and value, and deliver daily email alerts.</p>
'''
    },
    {
        'slug': 'find-construction-leads-san-francisco',
        'title': 'How to Find New Construction Leads in San Francisco Before Your Competition (2026)',
        'meta_description': 'How San Francisco subcontractors find construction leads. High-value permits, seismic retrofits, contractor shortage advantage.',
        'date': '2026-04-06',
        'category': 'contractor-leads',
        'city_link': '/permits/california/san-francisco',
        'city_name': 'San Francisco',
        'excerpt': 'San Francisco offers project values 3–4x the national norm. Here\'s how to find the best opportunities first.',
        'content': '''
<p>San Francisco's construction market is unique: extremely high project values, one of the most complex permitting processes in America, and a chronic shortage of skilled trades. For subcontractors who can navigate the city's requirements, the reward is substantial — average project values 3–4x the national norm.</p>

<h2>The Problem with Traditional Lead Services in San Francisco</h2>
<p>San Francisco's construction market is small in volume compared to sprawling sunbelt metros, but massive in value. Shared leads cost $75–150 in this market, and they're sent to 3–5 competitors simultaneously.</p>

<h2>What Smart SF Contractors Do Instead</h2>
<p>The Department of Building Inspection (DBI) processes thousands of permits every month. Every filing is public record with the project address, scope, valuation, and contractor information.</p>
<p>SF's high project values make each lead extraordinarily valuable. A single commercial HVAC scope in San Francisco can be worth $200,000–$1,000,000 in revenue.</p>

<h2>The SF Advantage</h2>
<p>The mandatory seismic retrofit program creates a steady stream of structural and MEP work. The contractor shortage works in your favor — GCs regularly struggle to find quality subs willing to work in the city.</p>

<h2>How to Get Started</h2>
<p>Building permit monitoring services like <a href="/permits/california/san-francisco">PermitGrab</a> aggregate SF permit data and deliver daily email alerts. In a market where one commercial project can represent $200K–$1M in subcontract revenue, the ROI on permit monitoring is higher here than almost anywhere.</p>
'''
    },
    {
        'slug': 'find-construction-leads-seattle',
        'title': 'How to Find New Construction Leads in Seattle Before Your Competition (2026)',
        'meta_description': 'How Seattle subcontractors find construction leads. Tech campus builds, ADU boom, 18% fee increase filtering for quality projects.',
        'date': '2026-04-06',
        'category': 'contractor-leads',
        'city_link': '/permits/washington/seattle',
        'city_name': 'Seattle',
        'excerpt': 'Seattle\'s 18% permit fee increase for 2026 means projects that clear the hurdle are well-funded and serious.',
        'content': '''
<p>Seattle's construction market is driven by tech campus expansions, a massive housing push to address affordability, infrastructure investment, and the commercial renovation wave transforming neighborhoods from South Lake Union to Ballard. The 18% permit fee increase for 2026 means projects that clear the permitting hurdle are even more serious and well-funded.</p>

<h2>The Problem with Traditional Lead Services in Seattle</h2>
<p>Seattle has one of the most competitive contractor markets on the West Coast. Shared leads cost $50–100+ and are sent to 3–5 other contractors.</p>

<h2>What Growing Seattle Contractors Do Instead</h2>
<p>SDCI processes thousands of permits every month. Every filing is public record with the project address, scope, permit type, and contractor information.</p>
<p>Seattle's strict energy code and seismic requirements mean permitted projects are well-documented — you can qualify leads from the filing data alone.</p>

<h2>The Seattle Advantage</h2>
<p>The ADU and DADU boom generates hundreds of permits per month. The seismic retrofit incentives (50% fee reduction for URM buildings) create a new pipeline of structural and MEP work. The 18% fee increase is actually good news for lead quality — projects that clear the higher fee barrier are better funded.</p>

<h2>How to Get Started</h2>
<p>Building permit monitoring services like <a href="/permits/washington/seattle">PermitGrab</a> aggregate Seattle permit data and deliver daily email alerts.</p>
'''
    },
    {
        'slug': 'find-construction-leads-denver',
        'title': 'How to Find New Construction Leads in Denver Before Your Competition (2026)',
        'meta_description': 'How Denver subcontractors find construction leads. 180-day permit guarantee, green building demand, timeline predictability.',
        'date': '2026-04-06',
        'category': 'contractor-leads',
        'city_link': '/permits/colorado/denver',
        'city_name': 'Denver',
        'excerpt': 'Denver\'s 180-day permit guarantee means you can predict timelines and plan crew availability months in advance.',
        'content': '''
<p>Denver's construction market is powered by population growth, a booming tech sector, marijuana industry commercial build-outs, and one of the most active residential markets in the Mountain West. The city's new 180-day permit approval guarantee signals that Denver is serious about keeping construction moving.</p>

<h2>The Problem with Traditional Lead Services in Denver</h2>
<p>Denver's rapid growth has attracted contractors from across Colorado and neighboring states. Angi and HomeAdvisor charge $30–75 per lead sold to 3–5 competitors.</p>

<h2>What Growing Denver Contractors Do Instead</h2>
<p>Denver's Community Planning and Development department processes thousands of permits every month through the e-Permits system. Every filing is public record.</p>
<p>Denver's 180-day permit guarantee means you can predict timelines better — monitor when permits are filed and estimate when construction will start.</p>

<h2>The Denver Advantage</h2>
<p>Denver's green building requirements for commercial projects over 25,000 sq ft create demand for contractors with sustainability expertise. The city's interactive dashboard showing real-time review times helps with project planning.</p>

<h2>How to Get Started</h2>
<p>Building permit monitoring services like <a href="/permits/colorado/denver">PermitGrab</a> aggregate Denver permit data and deliver daily email alerts.</p>
'''
    },
    {
        'slug': 'find-construction-leads-washington-dc',
        'title': 'How to Find New Construction Leads in Washington, DC Before Your Competition (2026)',
        'meta_description': 'How DC subcontractors find construction leads. Federal adjacency, embassy projects, institutional sector, high-value renovation market.',
        'date': '2026-04-06',
        'category': 'contractor-leads',
        'city_link': '/permits/washington-dc/washington',
        'city_name': 'Washington DC',
        'excerpt': 'DC\'s compact 68 square miles offers high-value federal, embassy, and institutional work across every trade.',
        'content': '''
<p>Washington, DC's construction market runs on a different engine than most American cities. Federal government adjacency, embassy construction and renovation, a massive institutional sector, and a residential renovation market fueled by some of the highest home values east of San Francisco.</p>

<h2>The Problem with Traditional Lead Services in DC</h2>
<p>The DC market has high project values but a relatively small, well-connected contractor community. Shared leads cost $50–100+ and pit you against 3–5 other contractors.</p>

<h2>What DC's Best Contractors Do Instead</h2>
<p>The Department of Buildings (DOB) processes thousands of permits every month through ProjectDox. Every filing is public record.</p>
<p>DC's historic district coverage means a significant percentage of projects involve complex renovation work — exactly the kind of high-value, skill-intensive projects that premium subs should be targeting.</p>

<h2>The DC Advantage</h2>
<p>DC's compact geography (68 square miles) means every permit is within a reasonable service radius. The institutional sector (embassies, universities, hospitals, museums) generates steady commercial demand that doesn't fluctuate with the residential market.</p>

<h2>How to Get Started</h2>
<p>Building permit monitoring services like <a href="/permits/washington-dc/washington">PermitGrab</a> aggregate DC permit data and deliver daily email alerts.</p>
'''
    },
    # Gap-fill: Leads posts for cities that only had permit-cost posts in V79
    {
        'slug': 'find-construction-leads-los-angeles',
        'title': 'How to Find New Construction Leads in Los Angeles Before Your Competition (2026)',
        'meta_description': 'How LA subcontractors find construction leads. Seismic retrofits, ADU boom, entertainment industry builds, permit monitoring strategies.',
        'date': '2026-04-06',
        'category': 'contractor-leads',
        'city_link': '/permits/california/los-angeles',
        'city_name': 'Los Angeles',
        'excerpt': 'LA is the second-largest construction market in America by volume. Here\'s how to tap into that pipeline.',
        'content': '''
<p>Los Angeles is the second-largest construction market in America by volume. Seismic retrofit mandates, entertainment industry studio expansions, ADU development, and a commercial renovation market spanning from DTLA to the Westside — for subcontractors, LA offers a pipeline that's both massive and diverse.</p>

<h2>The Problem with Traditional Lead Services in LA</h2>
<p>LA's contractor market is enormous and fiercely competitive. Shared leads cost $50–100+ and are sent to 3–5 other contractors. Close rates hover around 10–15%.</p>

<h2>What Smart LA Contractors Do Instead</h2>
<p>LADBS processes tens of thousands of permits every month across the city's 469 square miles. Every filing is public record with project address, scope, valuation, and contractor information.</p>
<p>LA's sheer volume means even niche trade specialties can find dozens of relevant leads per week.</p>

<h2>The LA Advantage</h2>
<p>Mandatory seismic retrofit programs generate a steady pipeline of structural and MEP work. The ADU boom continues with thousands of permits per month. The entertainment industry creates unique commercial demand — sound stages, production offices, post-production facilities.</p>

<h2>How to Get Started</h2>
<p>Building permit monitoring services like <a href="/permits/california/los-angeles">PermitGrab</a> aggregate LA permit data and deliver daily email alerts. In the second-largest construction market in America, the information advantage is enormous.</p>
'''
    },
    {
        'slug': 'find-construction-leads-chicago',
        'title': 'How to Find New Construction Leads in Chicago Before Your Competition (2026)',
        'meta_description': 'How Chicago subcontractors find construction leads. O\'Hare modernization, industrial conversions, Socrata data quality, permit monitoring.',
        'date': '2026-04-06',
        'category': 'contractor-leads',
        'city_link': '/permits/illinois/chicago',
        'city_name': 'Chicago',
        'excerpt': 'Chicago is the construction capital of the Midwest and one of the top 5 markets nationally. Here\'s how to find the best projects.',
        'content': '''
<p>Chicago is the construction capital of the Midwest — and one of the top 5 markets nationally by volume. Downtown high-rise development, O'Hare modernization, a massive industrial/logistics boom on the South Side, and neighborhood-by-neighborhood residential renovation create a deep, diverse pipeline.</p>

<h2>The Problem with Traditional Lead Services in Chicago</h2>
<p>Chicago's contractor market is large and competitive. Shared leads cost $40–100 and are sent to 3–5 contractors. Union and non-union shops compete fiercely.</p>

<h2>What Chicago's Best Contractors Do Instead</h2>
<p>The Chicago Department of Buildings processes thousands of permits every month. Every filing is public record with project address, scope, valuation, and contractor information.</p>
<p>Chicago's neighborhood diversity means permit data is especially valuable. A project in River North has a very different scope than one in Bridgeport.</p>

<h2>The Chicago Advantage</h2>
<p>Chicago's permit data quality is excellent, driven by the Socrata-based open data platform. The industrial conversion trend generates complex, high-value projects. The O'Hare modernization program represents billions in construction over the next decade.</p>

<h2>How to Get Started</h2>
<p>Building permit monitoring services like <a href="/permits/illinois/chicago">PermitGrab</a> aggregate Chicago permit data and deliver daily email alerts.</p>
'''
    },
    {
        'slug': 'find-construction-leads-phoenix',
        'title': 'How to Find New Construction Leads in Phoenix Before Your Competition (2026)',
        'meta_description': 'How Phoenix subcontractors find construction leads. TSMC semiconductor, data centers, same-day permits, fastest-growing market.',
        'date': '2026-04-06',
        'category': 'contractor-leads',
        'city_link': '/permits/arizona/phoenix',
        'city_name': 'Phoenix',
        'excerpt': 'Phoenix has the fastest permit processing of any major U.S. metro. Here\'s how to find projects in this booming market.',
        'content': '''
<p>Phoenix is one of the fastest-growing construction markets in America. TSMC's semiconductor fab, massive data center development, a residential market pushing further into the Valley, and a commercial renovation wave across Scottsdale, Tempe, and downtown Phoenix create enormous opportunity.</p>

<h2>The Problem with Traditional Lead Services in Phoenix</h2>
<p>The Phoenix metro has attracted contractors from across the country. Shared leads cost $30–75 and competition is fierce.</p>

<h2>What Growing Phoenix Contractors Do Instead</h2>
<p>Phoenix's Planning and Development Department processes thousands of permits every month. Every filing is public record.</p>
<p>Phoenix's over-the-counter permit system means many projects move from filing to issuance in a single day. Contractors monitoring permits in real time can reach out before some projects even officially start.</p>

<h2>The Phoenix Advantage</h2>
<p>Phoenix has the fastest permit processing times of any major U.S. metro — many permits issued same day. The semiconductor and data center boom (TSMC, Intel, scores of data center operators) generates years of sustained demand.</p>

<h2>How to Get Started</h2>
<p>Building permit monitoring services like <a href="/permits/arizona/phoenix">PermitGrab</a> aggregate Phoenix permit data and deliver daily email alerts.</p>
'''
    },
    {
        'slug': 'find-construction-leads-san-antonio',
        'title': 'How to Find New Construction Leads in San Antonio Before Your Competition (2026)',
        'meta_description': 'How San Antonio subcontractors find construction leads. Military base projects, healthcare construction, relationship-driven market.',
        'date': '2026-04-06',
        'category': 'contractor-leads',
        'city_link': '/permits/texas/san-antonio',
        'city_name': 'San Antonio',
        'excerpt': 'San Antonio\'s military and healthcare-driven market offers steady, well-funded construction work.',
        'content': '''
<p>San Antonio is Texas's second-largest city and one of the most active mid-market construction markets in the country. Military base expansions at JBSA, healthcare campus construction driven by the South Texas Medical Center, downtown revitalization, and a residential market expanding north and west.</p>

<h2>The Problem with Traditional Lead Services in San Antonio</h2>
<p>San Antonio's contractor market is competitive, with established local firms and an influx from Austin and Houston. Shared leads cost $30–75 per lead.</p>

<h2>What Smart San Antonio Contractors Do Instead</h2>
<p>San Antonio's Development Services Department processes thousands of permits every month. Every filing is public record.</p>
<p>The military connection is especially valuable. JBSA-related construction generates a steady stream of commercial permits with reliable funding and predictable timelines.</p>

<h2>The San Antonio Advantage</h2>
<p>The military base complex generates billions in federally-funded construction. The South Texas Medical Center is one of the largest medical complexes in the world. San Antonio's lower cost of living means more projects pencil out economically.</p>

<h2>How to Get Started</h2>
<p>Building permit monitoring services like <a href="/permits/texas/san-antonio">PermitGrab</a> aggregate San Antonio permit data and deliver daily email alerts.</p>
'''
    },
    # Gap-fill: Permit cost post for Atlanta (had leads post in V79, missing cost post)
    {
        'slug': 'how-much-does-building-permit-cost-atlanta',
        'title': 'How Much Does a Building Permit Cost in Atlanta? (2026 Guide)',
        'meta_description': 'Atlanta building permit costs for 2026. Office of Buildings fees, tree ordinance costs, historic district requirements, and tips for contractors.',
        'date': '2026-04-06',
        'category': 'permit-costs',
        'city_link': '/permits/georgia/atlanta',
        'city_name': 'Atlanta',
        'excerpt': 'Atlanta\'s permit fees are moderate, but the tree ordinance, sewer capacity fees, and historic district requirements can significantly increase total costs.',
        'content': '''
<p>Atlanta's construction market is one of the most active in the Southeast — and the city's Office of Buildings has been modernizing its permitting process to match. Understanding Atlanta's permit fee structure helps you bid accurately and avoid costly surprises — especially the tree ordinance.</p>

<h2>Atlanta Building Permit Costs at a Glance</h2>
<p>Atlanta's Office of Buildings uses a valuation-based fee schedule.</p>
<p><strong>Residential permit costs:</strong></p>
<ul>
<li>Base permit fee for projects under $2,000: approximately $75</li>
<li>For a $500K commercial project: approximately $3,000–$5,000</li>
<li>Plan review fees: 65–80% of the building permit fee</li>
</ul>
<p><strong>Trade-specific permit fees:</strong></p>
<ul>
<li>Mechanical permits: $200–$600 for commercial HVAC</li>
<li>Electrical permits: $250–$700 for commercial tenant improvements</li>
<li>Plumbing permits: $250–$600 for restaurant build-outs</li>
</ul>

<h2>What Catches Contractors Off Guard in Atlanta</h2>
<p><strong>Tree ordinance.</strong> Atlanta's tree protection ordinance is among the most aggressive in the country. Tree-related fees can add $2,000–$10,000+ to a project.</p>
<p><strong>Historic district requirements.</strong> Atlanta has multiple historic districts where Urban Design Commission review is required, adding 30–60+ days.</p>
<p><strong>Sewer capacity fees.</strong> Atlanta's Department of Watershed Management charges fees that can be substantial for restaurants — $5,000–$20,000+.</p>

<h2>Pro Tips for Contractors</h2>
<p>For a typical $1M commercial project, budget $10,000–$20,000 for all permits, reviews, and associated fees. The contractors who win in Atlanta account for the full cost picture — especially the tree ordinance.</p>
'''
    },
    # Batch 5: Nashville, Oklahoma City, El Paso, Boston, Portland (permit costs + leads)
    {
        'slug': 'how-much-does-building-permit-cost-nashville',
        'title': 'How Much Does a Building Permit Cost in Nashville? (2026 Guide)',
        'meta_description': 'Nashville building permit costs for 2026. Metro Codes fees, stormwater requirements, historic overlay rules, and tips for contractors.',
        'date': '2026-04-06',
        'category': 'permit-costs',
        'city_link': '/permits/tennessee/nashville',
        'city_name': 'Nashville',
        'excerpt': 'Nashville uses a valuation-based formula with plan review at 65% of the building permit fee.',
        'content': '''
<p>Nashville's construction market is booming — and the Metro Codes Department has modernized its permitting to keep pace. Understanding Nashville's permit fee structure helps you bid accurately and avoid surprises.</p>

<h2>Nashville Building Permit Costs at a Glance</h2>
<p>Nashville uses the Metro Codes Department for all building permits, with fees calculated using a valuation-based formula.</p>
<p><strong>Residential permit costs:</strong></p>
<ul>
<li>Projects valued under $2,000: approximately $75</li>
<li>Plan review fees: 65% of the building permit fee</li>
<li>For a $500K commercial project: roughly $3,500–$5,500 combined</li>
</ul>
<p><strong>Trade-specific permit fees:</strong></p>
<ul>
<li>Mechanical permits: $150–$500 for commercial HVAC</li>
<li>Electrical permits: $200–$600 for commercial tenant improvements</li>
<li>Plumbing permits: $300–$700 for restaurant build-outs</li>
</ul>

<h2>What Catches Contractors Off Guard in Nashville</h2>
<p><strong>Stormwater fees.</strong> Nashville's stormwater management requirements are aggressive. Projects disturbing more than one acre require a grading permit starting around $500.</p>
<p><strong>Historic overlay districts.</strong> Projects in Germantown, Lockeland Springs, Hillsboro Village and others require Metro Historic Zoning Commission review, adding 30–60 days.</p>

<h2>Pro Tips for Contractors</h2>
<p>For a typical $1M commercial project, budget $8,000–$15,000 for all permits and reviews combined.</p>
'''
    },
    {
        'slug': 'how-much-does-building-permit-cost-oklahoma-city',
        'title': 'How Much Does a Building Permit Cost in Oklahoma City? (2026 Guide)',
        'meta_description': 'Oklahoma City building permit costs for 2026. Development Center fees, affordable rates, floodplain requirements, and tips for contractors.',
        'date': '2026-04-06',
        'category': 'permit-costs',
        'city_link': '/permits/oklahoma/oklahoma-city',
        'city_name': 'Oklahoma City',
        'excerpt': 'OKC\'s permit fees are notably lower than comparable metros — roughly 40–60% of what you\'d pay in Dallas or Denver.',
        'content': '''
<p>Oklahoma City's construction market has quietly become one of the most active in the central U.S. — and one of the most contractor-friendly when it comes to permitting. Fees are straightforward and relatively affordable compared to coastal markets.</p>

<h2>Oklahoma City Building Permit Costs at a Glance</h2>
<p>OKC uses a valuation-based fee schedule aligned with ICC building valuation data.</p>
<p><strong>Fee structure:</strong></p>
<ul>
<li>Projects valued at $1–$500: approximately $30</li>
<li>For a $500K commercial project: approximately $2,500–$3,500</li>
<li>For a $1M project: typically $4,000–$6,000</li>
<li>Plan review: 65% of the building permit fee for commercial</li>
</ul>
<p>OKC's fees are notably lower than comparable metros — roughly 40–60% of Dallas or Denver.</p>

<h2>What Catches Contractors Off Guard in OKC</h2>
<p><strong>Floodplain review.</strong> Oklahoma City has extensive FEMA-mapped floodplains. Projects in flood zones require additional permits ($100–$300+) and 2–4 weeks extra time.</p>
<p><strong>Wind load requirements.</strong> OKC is in a high-wind zone with strict design requirements that exceed minimum IBC standards.</p>

<h2>Pro Tips for Contractors</h2>
<p>For a typical $500K commercial project, budget $4,000–$7,000 all-in. The low fee structure means permits represent a smaller percentage of project costs than in most markets.</p>
'''
    },
    {
        'slug': 'how-much-does-building-permit-cost-el-paso',
        'title': 'How Much Does a Building Permit Cost in El Paso? (2026 Guide)',
        'meta_description': 'El Paso building permit costs for 2026. Development Services fees, Fort Bliss requirements, water conservation rules, and tips for contractors.',
        'date': '2026-04-06',
        'category': 'permit-costs',
        'city_link': '/permits/texas/el-paso',
        'city_name': 'El Paso',
        'excerpt': 'El Paso\'s permit fees are among the most affordable in Texas — roughly 30–50% below Austin or Dallas.',
        'content': '''
<p>El Paso sits at the intersection of two countries, three states, and a construction market that's steadily growing. Fort Bliss expansion, cross-border logistics facilities, healthcare construction, and a residential market stretching into the Upper Valley and Far East.</p>

<h2>El Paso Building Permit Costs at a Glance</h2>
<p>El Paso uses a valuation-based fee schedule administered by the Development Services Department.</p>
<p><strong>Fee structure:</strong></p>
<ul>
<li>Projects valued under $2,000: approximately $50</li>
<li>For a $500K commercial project: roughly $2,000–$3,500</li>
<li>For a $1M project: typically $3,500–$5,500</li>
<li>Plan review: 65% of the building permit fee</li>
</ul>
<p>El Paso's fees are among the most affordable in Texas — roughly 30–50% below Austin or Dallas.</p>

<h2>What Catches Contractors Off Guard in El Paso</h2>
<p><strong>Fort Bliss adjacency.</strong> Projects near Fort Bliss may trigger additional review requirements related to airfield safety zones and military compatibility.</p>
<p><strong>Water conservation requirements.</strong> El Paso Water Utilities reviews projects for water conservation compliance, and landscape plans must meet xeriscaping requirements.</p>

<h2>Pro Tips for Contractors</h2>
<p>For a typical $500K commercial project, budget $4,000–$7,000 all-in. Don't let low permit costs make you forget to budget for water conservation and wind design engineering costs.</p>
'''
    },
    {
        'slug': 'how-much-does-building-permit-cost-boston',
        'title': 'How Much Does a Building Permit Cost in Boston? (2026 Guide)',
        'meta_description': 'Boston building permit costs for 2026. ISD fees at $15 per $1,000, BPDA review, sheet metal permits, and tips for contractors.',
        'date': '2026-04-06',
        'category': 'permit-costs',
        'city_link': '/permits/massachusetts/boston',
        'city_name': 'Boston',
        'excerpt': 'Boston\'s permit fees at $15 per $1,000 make it one of the most expensive cities in the country for permitting.',
        'content': '''
<p>Boston's construction market is one of the most expensive and heavily regulated in America. Between ISD, BPDA, and extensive historic district requirements, getting permits in Boston requires understanding a multi-layered system.</p>

<h2>Boston Building Permit Costs at a Glance</h2>
<p>Boston's ISD uses a valuation-based fee schedule at approximately $15 per $1,000 of construction value.</p>
<p><strong>Fee structure:</strong></p>
<ul>
<li>Minimum fee: approximately $100</li>
<li>For a $500K commercial project: approximately $7,500</li>
<li>For a $1M project: roughly $15,000 in building permit fees alone</li>
</ul>
<p><strong>Trade-specific permit fees:</strong></p>
<ul>
<li>Mechanical permits: $300–$800 for commercial HVAC</li>
<li>Electrical permits: $400–$1,000 for commercial tenant improvements</li>
<li>Sheet metal permits (ductwork): $150–$500 — unusual requirement</li>
</ul>

<h2>What Catches Contractors Off Guard in Boston</h2>
<p><strong>The sheet metal permit requirement.</strong> Boston is one of the few cities that requires a separate sheet metal permit for ductwork.</p>
<p><strong>Zoning relief prevalence.</strong> A remarkably high percentage of Boston projects require ZBA relief, adding 2–4 months.</p>
<p><strong>Historic district scope.</strong> Back Bay, Beacon Hill, the North End, Charlestown require Landmarks Commission review.</p>

<h2>Pro Tips for Contractors</h2>
<p>For a typical $1M commercial project, budget $20,000–$35,000 for all permits, reviews, and associated fees. Underestimating permit costs on a Boston project can wipe out your margin.</p>
'''
    },
    {
        'slug': 'how-much-does-building-permit-cost-portland',
        'title': 'How Much Does a Building Permit Cost in Portland? (2026 Guide)',
        'meta_description': 'Portland building permit costs for 2026. BDS fees, Systems Development Charges, seismic retrofit requirements, and tips for contractors.',
        'date': '2026-04-06',
        'category': 'permit-costs',
        'city_link': '/permits/oregon/portland',
        'city_name': 'Portland',
        'excerpt': 'Portland\'s permit fees are moderate, but Systems Development Charges can make total costs among the highest in the country for new construction.',
        'content': '''
<p>Portland's construction market is driven by aggressive green building mandates, a residential density push, seismic retrofit requirements, and a commercial renovation market concentrated in the Central City and inner eastside neighborhoods.</p>

<h2>Portland Building Permit Costs at a Glance</h2>
<p>Portland's BDS uses a valuation-based fee schedule with rates that step down at higher tiers.</p>
<p><strong>Fee structure:</strong></p>
<ul>
<li>Projects valued under $2,000: flat fee of approximately $100</li>
<li>For a $500K commercial project: approximately $4,500–$6,500</li>
<li>For a $1M project: typically $7,500–$10,000</li>
<li>Plan review: 65% of the building permit fee</li>
<li>State of Oregon surcharges: approximately 12% of the building permit fee</li>
</ul>

<h2>Systems Development Charges (SDCs)</h2>
<p>This is the single biggest cost surprise for contractors new to Portland. SDCs fund infrastructure and can reach $15,000–$30,000+ for a single-family home and $50,000–$200,000+ for commercial projects.</p>

<h2>What Catches Contractors Off Guard in Portland</h2>
<p><strong>Green building requirements.</strong> Portland's energy code exceeds state minimums with requirements for solar-ready zones, EV charging, and bird-safe glazing.</p>
<p><strong>Seismic retrofit mandates.</strong> The URM building retrofit mandate requires hundreds of buildings to undergo seismic upgrades on a mandatory timeline.</p>

<h2>Pro Tips for Contractors</h2>
<p>For a $500K commercial tenant improvement (no SDCs), budget $7,000–$12,000 all-in. For new construction, add SDCs on top.</p>
'''
    },
    {
        'slug': 'find-construction-leads-nashville',
        'title': 'How to Find New Construction Leads in Nashville Before Your Competition (2026)',
        'meta_description': 'How Nashville subcontractors find construction leads. Healthcare sector, entertainment venues, relationship-driven market, permit monitoring.',
        'date': '2026-04-06',
        'category': 'contractor-leads',
        'city_link': '/permits/tennessee/nashville',
        'city_name': 'Nashville',
        'excerpt': 'Nashville\'s healthcare, entertainment, and corporate relocation boom creates constant construction demand.',
        'content': '''
<p>Nashville's construction market has transformed from a steady regional market into one of the fastest-growing in the Southeast. The Gulch, SoBro, and East Nashville are in constant development, healthcare construction driven by HCA and Vanderbilt continues to expand, and the residential market is pushing into surrounding counties.</p>

<h2>The Problem with Traditional Lead Services in Nashville</h2>
<p>Nashville's contractor pool has grown dramatically. Shared leads cost $40–80 and close rates keep declining as new contractors arrive monthly.</p>

<h2>What Nashville's Best Contractors Do Instead</h2>
<p>The Metro Codes Department processes thousands of permits every month. Every filing is public record.</p>
<p>Nashville's rapid growth means the pipeline refreshes constantly. GCs filing permits are often juggling multiple projects and actively looking for reliable subs.</p>

<h2>The Nashville Advantage</h2>
<p>The healthcare sector generates steady medical office construction. The entertainment and hospitality industry drives restaurant, hotel, and venue construction at a pace few cities match. Corporate relocations (Amazon, Oracle, AllianceBernstein) create rippling office build-out demand.</p>

<h2>How to Get Started</h2>
<p>Building permit monitoring services like <a href="/permits/tennessee/nashville">PermitGrab</a> aggregate Nashville permit data and deliver daily email alerts.</p>
'''
    },
    {
        'slug': 'find-construction-leads-oklahoma-city',
        'title': 'How to Find New Construction Leads in Oklahoma City Before Your Competition (2026)',
        'meta_description': 'How Oklahoma City subcontractors find construction leads. MAPS 4 investment, aerospace growth, 620 sq mi monitoring advantage.',
        'date': '2026-04-06',
        'category': 'contractor-leads',
        'city_link': '/permits/oklahoma/oklahoma-city',
        'city_name': 'Oklahoma City',
        'excerpt': 'OKC\'s low permit costs, fast processing, and massive 620 sq mi footprint create unique monitoring advantages.',
        'content': '''
<p>Oklahoma City's construction market is one of the best-kept secrets in the central U.S. Low permit costs, fast processing times, a diversifying economy beyond oil and gas, and major infrastructure investments create steady work without coastal competition.</p>

<h2>The Problem with Traditional Lead Services in OKC</h2>
<p>OKC's contractor market is smaller than Dallas or Denver, but shared leads still underperform. Angi and HomeAdvisor charge $30–60 per lead.</p>

<h2>What Growing OKC Contractors Do Instead</h2>
<p>The Development Center processes thousands of permits every month across Oklahoma City's 620+ square miles. Every filing is public record.</p>
<p>OKC's enormous geographic footprint means there are always projects filing in areas competitors aren't watching.</p>

<h2>The OKC Advantage</h2>
<p>The city's low permit costs and fast processing times mean projects move quickly. The MAPS 4 initiative ($978M) is generating public construction demand for years — convention center, transit, parks, housing projects.</p>

<h2>How to Get Started</h2>
<p>Building permit monitoring services like <a href="/permits/oklahoma/oklahoma-city">PermitGrab</a> aggregate OKC permit data and deliver daily email alerts.</p>
'''
    },
    {
        'slug': 'find-construction-leads-el-paso',
        'title': 'How to Find New Construction Leads in El Paso Before Your Competition (2026)',
        'meta_description': 'How El Paso subcontractors find construction leads. Fort Bliss projects, cross-border logistics, healthcare expansion, permit monitoring.',
        'date': '2026-04-06',
        'category': 'contractor-leads',
        'city_link': '/permits/texas/el-paso',
        'city_name': 'El Paso',
        'excerpt': 'El Paso\'s military-driven, healthcare-backed, logistics-fueled market offers steady federally-funded demand.',
        'content': '''
<p>El Paso's construction market operates differently from the rest of Texas. Fort Bliss drives billions in military-adjacent construction, cross-border logistics facilities are expanding, healthcare systems are building to serve a growing regional population.</p>

<h2>The Problem with Traditional Lead Services in El Paso</h2>
<p>El Paso's contractor market is smaller and more relationship-driven than Dallas or Houston. Breaking in through shared lead platforms is especially difficult.</p>

<h2>What Smart El Paso Contractors Do Instead</h2>
<p>The Development Services Department processes permits for all construction across the city. Every filing is public record.</p>
<p>El Paso's border location creates a unique dynamic. Major projects often have connections to cross-border supply chains, military operations, or federal agencies.</p>

<h2>The El Paso Advantage</h2>
<p>Fort Bliss is one of the largest U.S. military installations with ongoing modernization. The healthcare sector is expanding. The logistics and warehousing sector is booming with cross-border trade.</p>

<h2>How to Get Started</h2>
<p>Building permit monitoring services like <a href="/permits/texas/el-paso">PermitGrab</a> aggregate El Paso permit data and deliver daily email alerts.</p>
'''
    },
    {
        'slug': 'find-construction-leads-boston',
        'title': 'How to Find New Construction Leads in Boston Before Your Competition (2026)',
        'meta_description': 'How Boston subcontractors find construction leads. Life sciences labs, institutional projects, brownstone renovations, high-value market.',
        'date': '2026-04-06',
        'category': 'contractor-leads',
        'city_link': '/permits/massachusetts/boston',
        'city_name': 'Boston',
        'excerpt': 'Boston\'s life sciences sector, institutional construction, and brownstone market offer premium work at premium margins.',
        'content': '''
<p>Boston's construction market is one of the highest-value in America. Life sciences lab build-outs in Cambridge and the Seaport, institutional construction at universities and hospitals, and a brownstone renovation market with some of the highest per-square-foot values in the country.</p>

<h2>The Problem with Traditional Lead Services in Boston</h2>
<p>Boston's contractor community is established and competitive. Shared leads cost $50–100+ and competing on shared leads means racing to the bottom on price.</p>

<h2>What Boston's Best Contractors Do Instead</h2>
<p>The Inspectional Services Department (ISD) processes thousands of permits every month. Every filing is public record.</p>
<p>Boston's compact geography (89 square miles) means every permit is within reasonable service distance. Extending monitoring to Cambridge, Somerville, and Brookline captures the life sciences and university construction.</p>

<h2>The Boston Advantage</h2>
<p>The life sciences sector generates billions in annual construction. The institutional sector (Harvard, MIT, Mass General Brigham) creates a pipeline that doesn't fluctuate with the commercial market. A single commercial scope can represent $200,000–$1,000,000 in subcontract revenue.</p>

<h2>How to Get Started</h2>
<p>Building permit monitoring services like <a href="/permits/massachusetts/boston">PermitGrab</a> aggregate Boston permit data and deliver daily email alerts.</p>
'''
    },
    {
        'slug': 'find-construction-leads-portland',
        'title': 'How to Find New Construction Leads in Portland Before Your Competition (2026)',
        'meta_description': 'How Portland subcontractors find construction leads. Seismic retrofits, green building mandates, ADU boom, specialized demand.',
        'date': '2026-04-06',
        'category': 'contractor-leads',
        'city_link': '/permits/oregon/portland',
        'city_name': 'Portland',
        'excerpt': 'Portland\'s seismic mandates and green building codes create specialized, high-margin work for knowledgeable contractors.',
        'content': '''
<p>Portland's construction market is shaped by mandatory seismic retrofits, aggressive green building codes, an ADU boom, and a commercial renovation market concentrated in the Pearl District and Central Eastside. For subcontractors, Portland offers specialized, high-margin work for those who can find it.</p>

<h2>The Problem with Traditional Lead Services in Portland</h2>
<p>Portland's contractor market is competitive with union and non-union shops. Shared leads cost $40–80 and competing on price undercuts the specialized value you bring.</p>

<h2>What Portland's Best Contractors Do Instead</h2>
<p>The Bureau of Development Services (BDS) processes thousands of permits every month. Every filing is public record.</p>
<p>Portland's seismic retrofit mandate is especially valuable. The city has identified hundreds of URM buildings that must be retrofitted on a mandatory timeline — a predictable pipeline.</p>

<h2>The Portland Advantage</h2>
<p>The URM seismic retrofit mandate creates a multi-year pipeline. Portland's green building requirements mean every significant project needs contractors who understand solar-ready design, EV infrastructure, and energy efficiency. The ADU market is one of the most active in the country.</p>

<h2>How to Get Started</h2>
<p>Building permit monitoring services like <a href="/permits/oregon/portland">PermitGrab</a> aggregate Portland permit data and deliver daily email alerts.</p>
'''
    }
]

# V79: Helper function to get blog posts by category
def get_blog_posts_by_category(category):
    return [p for p in BLOG_POSTS if p['category'] == category]

# V79: Helper function to get blog posts for a specific city
def get_blog_posts_for_city(city_link):
    return [p for p in BLOG_POSTS if p.get('city_link') == city_link]

# V79: Helper function to get related posts (same category, excluding current)
def get_related_posts(current_slug, limit=3):
    current = next((p for p in BLOG_POSTS if p['slug'] == current_slug), None)
    if not current:
        return []
    same_category = [p for p in BLOG_POSTS if p['category'] == current['category'] and p['slug'] != current_slug]
    if len(same_category) < limit:
        # Add posts from other categories
        other = [p for p in BLOG_POSTS if p['category'] != current['category'] and p['slug'] != current_slug]
        same_category.extend(other[:limit - len(same_category)])
    return same_category[:limit]

# V12.17: static_url_path='' serves static files from root (needed for GSC verification)
app = Flask(__name__, static_folder='static', static_url_path='', template_folder='templates')
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))

# V68: WSGI middleware to bypass ALL Flask processing for /api/health
# This ensures health checks ALWAYS return 200, even during pool exhaustion
class HealthCheckMiddleware:
    def __init__(self, wsgi_app):
        self.app = wsgi_app

    def __call__(self, environ, start_response):
        if environ.get('PATH_INFO') in ('/api/health', '/health'):
            import json
            status = '200 OK'
            response_headers = [('Content-Type', 'application/json')]
            start_response(status, response_headers)
            body = json.dumps({
                'status': 'ok',
                'version': 'V70',
                'message': 'Health check bypasses Flask entirely'
            })
            return [body.encode('utf-8')]
        return self.app(environ, start_response)

# Apply the middleware
app.wsgi_app = HealthCheckMiddleware(app.wsgi_app)

# V69: SCORCHED EARTH — ALL background work DISABLED until server is stable
# The server can serve ALL web requests from SQLite alone.
_startup_done = False
_collectors_manually_started = False

@app.before_request
def _deferred_startup():
    """V69: Mark startup done but DO NOT start any background threads.
    V93: Email scheduler is now auto-started (doesn't need Postgres)."""
    global _startup_done
    if _startup_done:
        return
    _startup_done = True
    # V70: NO background threads. NO Postgres pool. SQLite only. Just serve requests.
    print(f"[{datetime.now()}] V70: Server starting — Postgres DISABLED, SQLite only")
    print(f"[{datetime.now()}] V70: POST /api/admin/enable-postgres to enable Postgres pool")
    print(f"[{datetime.now()}] V70: POST /api/admin/start-collectors to start background threads")

    # V106: Phase A — Fast startup (blocks until done, keeps it quick)
    # Only sync config — no heavy DB operations
    try:
        print(f"[{datetime.now()}] V98b: Auto-syncing CITY_REGISTRY → prod_cities...")
        sync_city_registry_to_prod_cities()
        print(f"[{datetime.now()}] V98b: Registry sync complete")
    except Exception as e:
        print(f"[{datetime.now()}] V98b: Registry sync error (non-fatal): {e}")

    # V106: Phase B — Heavy maintenance in background thread
    # Server is ready to serve requests while this runs
    def _run_background_maintenance():
        try:
            print(f"[{datetime.now()}] [V106] Background maintenance starting...")
            from db import relink_orphaned_permits
            from collector import (update_total_permits_from_actual, update_all_city_health,
                                   activate_bulk_covered_cities, cleanup_balance_of_entries)

            relink_orphaned_permits()
            update_total_permits_from_actual()
            activate_bulk_covered_cities()
            cleanup_balance_of_entries()
            update_all_city_health()

            print(f"[{datetime.now()}] [V106] Background maintenance complete")
        except Exception as e:
            print(f"[{datetime.now()}] [V106] Background maintenance error: {e}")
            import traceback
            traceback.print_exc()

    maintenance_thread = threading.Thread(target=_run_background_maintenance, name='v106_maintenance', daemon=True)
    maintenance_thread.start()
    print(f"[{datetime.now()}] [V106] Background maintenance thread started — server ready to serve")

    # V93: Start email scheduler thread automatically (uses JSON file + SMTP, no Postgres needed)
    try:
        email_thread = threading.Thread(target=schedule_email_tasks, name='email_scheduler', daemon=True)
        email_thread.start()
        print(f"[{datetime.now()}] V93: Email scheduler thread started automatically")
    except Exception as e:
        print(f"[{datetime.now()}] [ERROR] Email scheduler failed to start: {e}")


# V13.1: Jinja filter for human-readable date formatting
@app.template_filter('format_date')
def format_date_filter(date_str):
    """Format date string to human-readable format: Mar 24, 2026"""
    if not date_str:
        return 'Date not available'
    try:
        # Handle ISO format dates
        if isinstance(date_str, str):
            # Check if it starts with a digit (valid date format)
            if not date_str[0].isdigit():
                return 'Date not available'
            date_obj = datetime.strptime(date_str[:10], '%Y-%m-%d')
        else:
            date_obj = date_str
        return date_obj.strftime('%b %d, %Y')  # Mar 24, 2026
    except (ValueError, TypeError):
        return 'Date not available'


@app.template_filter('clean_address')
def clean_address_filter(val):
    """V12.60/V21: Clean raw GeoJSON/Socrata JSON from address fields at display time.
    V21 FIX #13: Return 'Address pending' instead of empty/N/A for missing addresses."""
    if not val:
        return 'Address pending'
    s = str(val).strip()
    # V21: Check for placeholder values
    if s.lower() in ('', 'n/a', 'address not provided', 'none', 'null'):
        return 'Address pending'
    # Quick check — if no curly brace, it's already clean
    if '{' not in s:
        return s
    # Contains JSON — run through parse_address_value
    from collector import parse_address_value
    cleaned = parse_address_value(s)
    return cleaned if cleaned else 'Address pending'


# V12.17: Google Search Console verification - MUST be registered first before any catch-alls
@app.route('/google3ef154d70f8049a0.html')
def google_verification():
    return Response('google-site-verification: google3ef154d70f8049a0.html', mimetype='text/html')


# ===========================
# V12.19: ADMIN ENDPOINTS FOR DATA RECOVERY
# ===========================

def check_admin_key():
    """V12.58: Validate admin key without hardcoded fallback. Returns (is_valid, error_response)."""
    secret = request.headers.get('X-Admin-Key')
    expected = os.environ.get('ADMIN_KEY')
    if not expected:
        return False, (jsonify({'error': 'Admin key not configured'}), 503)
    if secret != expected:
        return False, (jsonify({'error': 'Unauthorized'}), 401)
    return True, None


@app.route('/api/admin/reset-permits', methods=['POST'])
def admin_reset_permits():
    """Delete corrupted permits.json so next collection writes clean data."""
    valid, error = check_admin_key()
    if not valid:
        return error

    # DATA_DIR is defined later, use the same logic
    data_dir = '/var/data' if os.path.isdir('/var/data') else os.path.join(os.path.dirname(__file__), 'data')
    filepath = os.path.join(data_dir, 'permits.json')
    deleted = False
    if os.path.exists(filepath):
        os.remove(filepath)
        deleted = True
        print(f"[Admin] Deleted corrupted permits.json at {filepath}")

    return jsonify({
        'deleted': deleted,
        'path': filepath,
        'message': 'File deleted. Next collection cycle will write clean data.'
    })


@app.route('/api/admin/fix-addresses', methods=['POST'])
def admin_fix_addresses():
    """V12.55c: Fix Socrata location objects stored as raw JSON in address field."""
    valid, error = check_admin_key()
    if not valid:
        return error

    def run_fix():
        try:
            _fix_socrata_addresses()
        except Exception as e:
            print(f"[Admin] Address fix error: {e}")
            import traceback
            traceback.print_exc()

    thread = threading.Thread(target=run_fix, daemon=True)
    thread.start()
    return jsonify({'message': 'Address cleanup started in background'})


def _fix_socrata_addresses():
    """V12.57: Find and fix permits with raw JSON in address or description fields.
    Handles Socrata location objects, GeoJSON Points, and regenerates descriptions."""
    import ast
    from collector import parse_address_value

    try:
        conn = sqlite3.connect('/var/data/permitgrab.db')
        conn.row_factory = sqlite3.Row
    except Exception as e:
        print(f"[V12.57] Address cleanup: cannot connect to DB: {e}", flush=True)
        return

    # Find permits with JSON in address field
    cursor = conn.execute(
        "SELECT permit_number, address, zip, description, display_description FROM permits "
        "WHERE address LIKE '%{%' OR display_description LIKE '%{%'"
    )
    rows = cursor.fetchall()
    if not rows:
        print("[V12.57] No bad addresses/descriptions found — nothing to fix.", flush=True)
        return
    print(f"[V12.57] Found {len(rows)} permits with JSON in address/description. Fixing...", flush=True)

    fixed_addr = 0
    fixed_desc = 0
    for row in rows:
        pn = row['permit_number']
        raw_addr = row['address'] or ''
        existing_zip = row['zip'] or ''
        desc = row['description'] or ''
        disp_desc = row['display_description'] or ''

        updates = {}

        # Fix address if it contains JSON
        if '{' in raw_addr:
            try:
                clean_addr = parse_address_value(raw_addr)
                if clean_addr != raw_addr:
                    updates['address'] = clean_addr or ''
                    fixed_addr += 1
                    # Also try to extract zip from Socrata location
                    if not existing_zip:
                        try:
                            parsed = ast.literal_eval(raw_addr)
                            if isinstance(parsed, dict):
                                human = parsed.get('human_address', '')
                                if isinstance(human, str):
                                    import json as _json
                                    human = _json.loads(human)
                                if isinstance(human, dict):
                                    updates['zip'] = human.get('zip', '')
                        except Exception:
                            pass
            except Exception:
                pass

        # Fix description/display_description if it contains JSON
        for field in ['description', 'display_description']:
            val = row[field] or ''
            if '{' in val and ('human_address' in val or 'latitude' in val or "'type': 'Point'" in val or 'coordinates' in val):
                # Strip out the JSON portion from the description
                import re
                cleaned = re.sub(r"\{[^}]*'(?:human_address|latitude|type)'[^}]*\}", '', val)
                cleaned = re.sub(r'\s+', ' ', cleaned).strip()
                cleaned = cleaned.replace('at  ', 'at ').replace('at [', '[').strip()
                if cleaned != val:
                    updates[field] = cleaned
                    fixed_desc += 1

        if updates:
            set_clause = ', '.join(f"{k} = ?" for k in updates)
            values = list(updates.values()) + [pn]
            conn.execute(f"UPDATE permits SET {set_clause} WHERE permit_number = ?", values)

    conn.commit()
    # V12.60: Do NOT close thread-local SQLite connection — it poisons the pool
    print(f"[V12.57] Fixed {fixed_addr} addresses, {fixed_desc} descriptions.", flush=True)


@app.route('/api/admin/force-collection', methods=['POST'])
def admin_force_collection():
    """V64: Force collection — runs ALL platforms, supports filtering.

    JSON body:
      days_back: int (default 7, max 90)
      platform: str (optional — filter to one platform: socrata, arcgis, ckan, carto, accela)
      city_slug: str (optional — run a single city only)
      include_scrapers: bool (default false — run Accela/Playwright scrapers too)
    """
    valid, error = check_admin_key()
    if not valid:
        return error

    data = request.json or {}
    days_back = min(int(data.get('days_back', 7)), 90)
    platform_filter = data.get('platform')
    city_slug = data.get('city_slug')
    include_scrapers = data.get('include_scrapers', True)  # V74: Default to True so Accela/CKAN get collected

    if city_slug:
        # Synchronous single-city mode (fast enough)
        try:
            from collector import collect_single_city
            result = collect_single_city(city_slug, days_back=days_back)
            return jsonify({
                'mode': 'single_city',
                'city_slug': city_slug,
                'days_back': days_back,
                'result': result
            })
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    else:
        # Background thread for full/filtered collection
        def run_collection():
            try:
                from collector import collect_refresh
                print(f"[Admin] Starting REFRESH collection (platform={platform_filter}, scrapers={include_scrapers})...")
                collect_refresh(
                    days_back=days_back,
                    platform_filter=platform_filter,
                    include_scrapers=include_scrapers
                )
                print("[Admin] Refresh collection complete.")
            except Exception as e:
                print(f"[Admin] Collection error: {e}")
                import traceback
                traceback.print_exc()

        thread = threading.Thread(target=run_collection, daemon=True)
        thread.start()

        return jsonify({
            'message': 'REFRESH collection started',
            'mode': 'background',
            'days_back': days_back,
            'platform_filter': platform_filter,
            'include_scrapers': include_scrapers,
            'note': 'V64: Supports all platforms, check logs for progress'
        })


@app.route('/api/admin/full-collection', methods=['POST'])
def admin_full_collection():
    """V12.50: Trigger FULL collection (rebuild SQLite)."""
    valid, error = check_admin_key()
    if not valid:
        return error

    def run_collection():
        try:
            from collector import collect_full
            print("[Admin] Starting FULL collection (rebuild mode)...")
            collect_full(days_back=365)
            print("[Admin] Full collection complete.")
        except Exception as e:
            print(f"[Admin] Full collection error: {e}")

    thread = threading.Thread(target=run_collection, daemon=True)
    thread.start()

    return jsonify({
        'message': 'FULL collection started (rebuild mode)',
        'note': 'V12.50: Rebuilds SQLite database. Takes 30-60 minutes.'
    })


@app.route('/api/admin/add-source', methods=['POST'])
def admin_add_source():
    """V12.50: Add a single source and upsert to SQLite."""
    valid, error = check_admin_key()
    if not valid:
        return error

    source_key = request.args.get('source')
    source_type = request.args.get('type', 'bulk')  # 'bulk' or 'city'

    if not source_key:
        return jsonify({'error': 'Missing source parameter. Usage: ?source=nj_statewide&type=bulk'}), 400

    def run_collection():
        try:
            from collector import collect_single_source
            print(f"[Admin] Adding single source: {source_key} ({source_type})...")
            collect_single_source(source_key, source_type)
            print(f"[Admin] Source {source_key} added successfully.")
        except Exception as e:
            print(f"[Admin] Add source error: {e}")

    thread = threading.Thread(target=run_collection, daemon=True)
    thread.start()

    return jsonify({
        'message': f'Adding source: {source_key} ({source_type})',
        'note': 'V12.50: Data written directly to SQLite'
    })


@app.route('/api/admin/collection-status')
def admin_collection_status():
    """V12.29: Get last collection run status for debugging."""
    valid, error = check_admin_key()
    if not valid:
        return error

    stats_file = os.path.join(DATA_DIR, "collection_stats.json")
    if not os.path.exists(stats_file):
        return jsonify({'error': 'No collection stats found', 'path': stats_file}), 404

    try:
        with open(stats_file) as f:
            stats = json.load(f)

        # Calculate summary
        city_stats = stats.get('city_stats', {})
        total_cities = len(city_stats)
        cities_with_permits = sum(1 for s in city_stats.values() if s.get('normalized', 0) > 0)
        cities_empty = sum(1 for s in city_stats.values() if s.get('status') == 'success_empty')
        cities_errored = sum(1 for s in city_stats.values() if 'error' in str(s.get('status', '')))
        cities_timeout = sum(1 for s in city_stats.values() if 'timeout' in str(s.get('status', '').lower()))

        # Get list of failed cities
        failed_cities = [
            {'city': k, 'name': v.get('city_name', k), 'status': v.get('status', 'unknown')}
            for k, v in city_stats.items()
            if 'error' in str(v.get('status', '')) or 'timeout' in str(v.get('status', '').lower())
        ]

        return jsonify({
            'collected_at': stats.get('collected_at'),
            'total_permits': stats.get('total_permits', 0),
            'summary': {
                'total_cities_attempted': total_cities,
                'cities_with_permits': cities_with_permits,
                'cities_empty': cities_empty,
                'cities_errored': cities_errored,
                'cities_timeout': cities_timeout,
            },
            'failed_cities': failed_cities[:50],  # Limit to 50 to avoid huge response
            'trade_breakdown': stats.get('trade_breakdown', {}),
        })
    except Exception as e:
        return jsonify({'error': f'Failed to read stats: {str(e)}'}), 500


@app.route('/api/admin/start-collectors', methods=['POST'])
def admin_start_collectors():
    """V69: Manually start background threads after server is stable.

    Since V69 disables all automatic background threads on startup,
    use this endpoint to manually trigger them when ready.
    """
    valid, error = check_admin_key()
    if not valid:
        return error

    global _collectors_manually_started

    try:
        if _collectors_manually_started:
            return jsonify({'status': 'already_running', 'message': 'Collectors already started'}), 200

        import threading

        def _run_collectors():
            print(f"[{datetime.now()}] V69: Manual start_collectors triggered via API")
            start_collectors()

        t = threading.Thread(target=_run_collectors, daemon=True)
        t.start()
        _collectors_manually_started = True

        return jsonify({
            'status': 'started',
            'message': 'Background collectors started in separate thread'
        }), 200

    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/admin/recount-permits', methods=['POST'])
def admin_recount_permits():
    """V100: Recount total_permits from actual permits table."""
    valid, error = check_admin_key()
    if not valid:
        return error
    try:
        from collector import update_total_permits_from_actual
        updated = update_total_permits_from_actual()
        return jsonify({'status': 'ok', 'updated': updated}), 200
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/admin/activate-bulk-cities', methods=['POST'])
def admin_activate_bulk_cities():
    """V104: Activate pending cities in states covered by bulk sources."""
    valid, error = check_admin_key()
    if not valid:
        return error
    try:
        from collector import activate_bulk_covered_cities
        activated = activate_bulk_covered_cities()
        return jsonify({'status': 'ok', 'activated': activated}), 200
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/admin/city-health')
def admin_city_health():
    """V100: City health dashboard data."""
    valid, error = check_admin_key()
    if not valid:
        return error
    try:
        conn = permitdb.get_connection()

        summary = conn.execute("""
            SELECT health_status, COUNT(*) as cnt,
                   SUM(total_permits) as permits,
                   AVG(days_since_new_data) as avg_days_stale
            FROM prod_cities
            WHERE status = 'active'
            GROUP BY health_status
            ORDER BY cnt DESC
        """).fetchall()

        stale = conn.execute("""
            SELECT city, state, city_slug, total_permits, latest_permit_date,
                   days_since_new_data, last_failure_reason, source_type
            FROM prod_cities
            WHERE status = 'active' AND health_status = 'stale'
            ORDER BY total_permits DESC
            LIMIT 50
        """).fetchall()

        never_worked = conn.execute("""
            SELECT source_type, COUNT(*) as cnt
            FROM prod_cities
            WHERE status = 'active' AND health_status = 'never_worked'
            GROUP BY source_type
            ORDER BY cnt DESC
        """).fetchall()

        # SQLite rows support index access; convert to dicts
        summary_list = []
        for r in summary:
            summary_list.append({
                'health_status': r[0], 'cnt': r[1],
                'permits': r[2], 'avg_days_stale': round(r[3], 1) if r[3] else None
            })

        stale_list = []
        for r in stale:
            stale_list.append({
                'city': r[0], 'state': r[1], 'city_slug': r[2],
                'total_permits': r[3], 'latest_permit_date': r[4],
                'days_since_new_data': r[5], 'last_failure_reason': r[6],
                'source_type': r[7]
            })

        nw_list = []
        for r in never_worked:
            nw_list.append({'source_type': r[0], 'cnt': r[1]})

        return jsonify({
            'summary': summary_list,
            'stale_cities': stale_list,
            'never_worked_by_platform': nw_list
        }), 200
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/admin/enable-postgres', methods=['POST'])
def admin_enable_postgres():
    """V70: Manually enable Postgres pool after server is confirmed stable."""
    valid, error = check_admin_key()
    if not valid:
        return error

    try:
        from db_engine import enable_pg_pool, is_pg_pool_enabled
        if is_pg_pool_enabled():
            return jsonify({'status': 'already_enabled', 'message': 'Postgres pool already active'}), 200

        success = enable_pg_pool()
        if success:
            return jsonify({'status': 'enabled', 'message': 'Postgres pool created'}), 200
        else:
            return jsonify({'status': 'failed', 'message': 'Failed to create pool - check logs'}), 500
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/admin/pg-status')
def admin_pg_status():
    """V70: Check if Postgres pool is active."""
    valid, error = check_admin_key()
    if not valid:
        return error

    try:
        from db_engine import _pg_pool, _pg_pool_enabled, is_pg_pool_enabled
        return jsonify({
            'pool_enabled': is_pg_pool_enabled(),
            'pool_exists': _pg_pool is not None,
            '_pg_pool_enabled_flag': _pg_pool_enabled,
        }), 200
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/admin/validation-results')
def admin_validation_results():
    """V12.31: Get endpoint validation results for applying fixes."""
    valid, error = check_admin_key()
    if not valid:
        return error

    validation_file = os.path.join(DATA_DIR, "endpoint_validation.json")
    if not os.path.exists(validation_file):
        return jsonify({'error': 'No validation results found. Run validate_endpoints.py first.'}), 404

    try:
        with open(validation_file) as f:
            data = json.load(f)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': f'Failed to read validation results: {str(e)}'}), 500


@app.route('/api/admin/suggested-fixes')
def admin_suggested_fixes():
    """V12.31: Get suggested fixes for broken endpoints."""
    valid, error = check_admin_key()
    if not valid:
        return error

    fixes_file = os.path.join(DATA_DIR, "suggested_fixes.json")
    if not os.path.exists(fixes_file):
        return jsonify({'error': 'No suggested fixes found. Run validate_endpoints.py --fix first.'}), 404

    try:
        with open(fixes_file) as f:
            data = json.load(f)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': f'Failed to read suggested fixes: {str(e)}'}), 500


@app.route('/api/admin/coverage')
def admin_coverage():
    """V12.33/V31: Get coverage statistics - which cities/states have data.
    V31: Distinguishes active cities (with live data sources) from historical
    cities that only appear in permit data from bulk sources.
    """
    valid, error = check_admin_key()
    if not valid:
        return error

    # V31: Active cities = prod_cities with status='active' (these are being pulled)
    active_city_count = 0
    active_cities_list = []
    try:
        if permitdb.prod_cities_table_exists():
            active_cities_list = permitdb.get_prod_cities(status='active')
            active_city_count = len(active_cities_list)
    except Exception:
        pass

    # Also get counts by status for a full breakdown
    status_breakdown = {}
    try:
        conn = permitdb.get_connection()
        rows = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM prod_cities GROUP BY status"
        ).fetchall()
        status_breakdown = {r['status']: r['cnt'] for r in rows}
    except Exception:
        pass

    # V34: Analyze coverage from SQLite DB (not permits.json which is deprecated)
    try:
        conn = permitdb.get_connection()

        # Get total permits
        total_row = conn.execute("SELECT COUNT(*) as cnt FROM permits").fetchone()
        total_permits = total_row['cnt'] if total_row else 0

        # Analyze by city and state
        city_rows = conn.execute("""
            SELECT city, state, COUNT(*) as cnt
            FROM permits GROUP BY city, state ORDER BY cnt DESC
        """).fetchall()

        city_counts = {}
        state_counts = {}
        for r in city_rows:
            city_key = f"{r['city']}, {r['state']}"
            city_counts[city_key] = r['cnt']
            state_counts[r['state'] or 'Unknown'] = state_counts.get(r['state'] or 'Unknown', 0) + r['cnt']

        top_cities = list(city_counts.items())[:50]
        states_covered = sorted(state_counts.items(), key=lambda x: -x[1])

        all_states = ['AL', 'AK', 'AZ', 'AR', 'CA', 'CO', 'CT', 'DE', 'FL', 'GA',
                      'HI', 'ID', 'IL', 'IN', 'IA', 'KS', 'KY', 'LA', 'ME', 'MD',
                      'MA', 'MI', 'MN', 'MS', 'MO', 'MT', 'NE', 'NV', 'NH', 'NJ',
                      'NM', 'NY', 'NC', 'ND', 'OH', 'OK', 'OR', 'PA', 'RI', 'SC',
                      'SD', 'TN', 'TX', 'UT', 'VT', 'VA', 'WA', 'WV', 'WI', 'WY']
        states_missing = [s for s in all_states if s not in state_counts]

        # V34: Get verified active city count (cities with actual data)
        verified_count = permitdb.get_prod_city_count()

        return jsonify({
            'active_cities': active_city_count,
            'verified_active_with_data': verified_count,
            'prod_cities_by_status': status_breakdown,
            'distinct_cities_in_permits': len(city_counts),
            'total_permits': total_permits,
            'total_states_with_data': len(state_counts),
            'states_covered': states_covered,
            'states_missing': states_missing,
            'top_50_cities': top_cities,
        })

    except Exception as e:
        return jsonify({'error': f'Failed to analyze coverage: {str(e)}'}), 500


# ===========================
# V34: ADMIN AUDIT & CLEANUP
# ===========================

@app.route('/api/admin/audit')
def admin_audit_cities():
    """V34: Comprehensive audit of all active cities vs actual permit data.
    Returns detailed report showing which cities have data, which don't,
    and recommendations for cleanup.
    """
    valid, error = check_admin_key()
    if not valid:
        return error

    try:
        results = permitdb.audit_prod_cities()
        return jsonify(results)
    except Exception as e:
        return jsonify({'error': f'Audit failed: {str(e)}'}), 500


@app.route('/api/admin/reactivate-paused', methods=['POST'])
def admin_reactivate_paused():
    """V35: Lightweight endpoint to reactivate paused cities that have permit data.
    Only does one fast UPDATE — no heavy cleanup."""
    valid, error = check_admin_key()
    if not valid:
        return error

    try:
        conn = permitdb.get_connection()
        # First sync counts for paused cities only
        paused = conn.execute(
            "SELECT id, city, state FROM prod_cities WHERE status = 'paused'"
        ).fetchall()
        updated_counts = 0
        for row in paused:
            actual = conn.execute(
                "SELECT COUNT(*) as cnt FROM permits WHERE LOWER(city) = LOWER(?) AND state = ?",
                (row['city'], row['state'])
            ).fetchone()['cnt']
            if actual > 0:
                conn.execute(
                    "UPDATE prod_cities SET total_permits = ?, status = 'active' WHERE id = ?",
                    (actual, row['id'])
                )
                updated_counts += 1
        conn.commit()

        # Get the updated list
        reactivated = conn.execute(
            "SELECT city, state, total_permits FROM prod_cities WHERE status = 'active' ORDER BY total_permits DESC"
        ).fetchall()

        return jsonify({
            'reactivated_count': updated_counts,
            'total_active': len(reactivated),
            'message': f'Reactivated {updated_counts} paused cities with data'
        })
    except Exception as e:
        return jsonify({'error': f'Reactivation failed: {str(e)}'}), 500


@app.route('/api/admin/scraper-history')
def admin_scraper_history():
    """V35: Per-city collection history from scraper_runs table.
    Shows last collection result for every city to identify broken vs working endpoints.
    """
    valid, error = check_admin_key()
    if not valid:
        return error

    try:
        conn = permitdb.get_connection()
        # Get the most recent run for each city
        runs = conn.execute("""
            SELECT city_slug, source_name, city, state,
                   permits_found, permits_inserted, status, error_message,
                   duration_ms, run_started_at,
                   ROW_NUMBER() OVER (PARTITION BY city_slug ORDER BY run_started_at DESC) as rn
            FROM scraper_runs
        """).fetchall()

        # Filter to most recent per city
        latest = {}
        for r in runs:
            if r['city_slug'] not in latest or r['rn'] == 1:
                if r['rn'] == 1:
                    latest[r['city_slug']] = dict(r)

        # Categorize
        working = []  # returned permits
        empty = []    # success but 0 permits
        errored = []  # error status
        for slug, r in latest.items():
            entry = {
                'slug': slug,
                'name': r.get('source_name') or r.get('city') or slug,
                'state': r.get('state', ''),
                'permits_found': r.get('permits_found', 0),
                'status': r.get('status', ''),
                'error': r.get('error_message', ''),
                'last_run': r.get('run_started_at', ''),
                'duration_ms': r.get('duration_ms', 0),
            }
            if r.get('status') == 'error' or (r.get('error_message') and r.get('error_message') != ''):
                errored.append(entry)
            elif r.get('permits_found', 0) > 0:
                working.append(entry)
            else:
                empty.append(entry)

        return jsonify({
            'total_cities': len(latest),
            'working': len(working),
            'empty': len(empty),
            'errored': len(errored),
            'working_cities': sorted(working, key=lambda x: -x['permits_found']),
            'empty_cities': sorted(empty, key=lambda x: x['name']),
            'errored_cities': sorted(errored, key=lambda x: x['name']),
        })
    except Exception as e:
        return jsonify({'error': f'Scraper history failed: {str(e)}'}), 500


@app.route('/api/admin/test-and-backfill', methods=['POST'])
def admin_test_and_backfill():
    """V35: Test an endpoint, backfill 6 months of data, and activate the source.

    POST body: {"city_key": "phoenix"} — uses existing CITY_REGISTRY config
    OR: {"city_key": "new_city", "config": {...}} — provide full config

    Steps:
    1. Test: fetch 5 records to verify the endpoint works
    2. Backfill: fetch 180 days of historical data
    3. Normalize and insert into DB
    4. Activate: set city_source status='active', create/update prod_city
    5. Report results

    This ensures we KNOW the collector will succeed before activating.
    """
    valid, error = check_admin_key()
    if not valid:
        return error

    try:
        data = request.get_json() or {}
        city_key = data.get('city_key')
        if not city_key:
            return jsonify({'error': 'city_key is required'}), 400

        days_back = data.get('days_back', 180)

        # Import collection functions
        from collector import fetch_permits, normalize_permit
        from city_source_db import get_city_config

        # Get config (from request body or existing registry)
        config = data.get('config')
        if not config:
            config = get_city_config(city_key)
        if not config:
            return jsonify({'error': f'No config found for {city_key}. Provide config in request body.'}), 404

        # Force active for testing
        config['active'] = True

        # Step 1: TEST — fetch a small sample to verify endpoint works
        from collector import fetch_socrata, fetch_arcgis, fetch_ckan, fetch_carto
        try:
            from accela_scraper import fetch_accela
            _accela_available = True
        except ImportError:
            _accela_available = False
        platform = config.get('platform', 'socrata')
        test_config = dict(config)
        test_config['limit'] = 5  # Just 5 records for testing

        test_config['limit'] = 10  # Small sample for freshness check
        try:
            # Test with 30-day window — if there's no data in the last 30 days,
            # the source is stale and not worth activating for leads
            if platform == 'socrata':
                test_raw = fetch_socrata(test_config, 30)
            elif platform == 'arcgis':
                test_raw = fetch_arcgis(test_config, 30)
            elif platform == 'ckan':
                test_raw = fetch_ckan(test_config, 30)
            elif platform == 'carto':
                test_raw = fetch_carto(test_config, 30)
            elif platform == 'accela':
                if not _accela_available:
                    return jsonify({'error': 'Accela scraper not available (Playwright not installed)'}), 400
                test_raw = fetch_accela(test_config, 30)
            else:
                return jsonify({'error': f'Unsupported platform: {platform}'}), 400
        except Exception as e:
            return jsonify({
                'status': 'FAILED',
                'step': 'test',
                'error': str(e),
                'message': f'Endpoint test failed for {city_key}. Do NOT activate.'
            }), 400

        if not test_raw:
            return jsonify({
                'status': 'FAILED',
                'step': 'test',
                'error': 'No permits in last 30 days',
                'message': f'{city_key} has no data in the last 30 days. Stale source — do NOT activate.'
            }), 400

        # Step 2: BACKFILL — fetch full historical data
        config['limit'] = config.get('limit', 2000)  # Restore normal limit
        try:
            if platform == 'socrata':
                raw = fetch_socrata(config, days_back)
            elif platform == 'arcgis':
                raw = fetch_arcgis(config, days_back)
            elif platform == 'ckan':
                raw = fetch_ckan(config, days_back)
            elif platform == 'carto':
                raw = fetch_carto(config, days_back)
            elif platform == 'accela':
                raw = fetch_accela(config, days_back)
        except Exception as e:
            return jsonify({
                'status': 'FAILED',
                'step': 'backfill_fetch',
                'error': str(e),
                'test_passed': True,
                'test_records': len(test_raw),
            }), 500

        # Step 3: NORMALIZE — convert raw records to our schema
        normalized = []
        for record in raw:
            try:
                permit = normalize_permit(record, city_key)
                if permit and permit.get('permit_number'):
                    normalized.append(permit)
            except Exception:
                continue

        if not normalized:
            return jsonify({
                'status': 'WARNING',
                'step': 'normalize',
                'raw_fetched': len(raw),
                'normalized': 0,
                'message': f'Got {len(raw)} raw records but 0 normalized. Check field_map config.'
            }), 400

        # Step 4: INSERT into DB
        inserted = permitdb.upsert_permits(normalized, source_city_key=city_key)

        # Step 5: ACTIVATE — update city_sources and prod_cities
        conn = permitdb.get_connection()

        # Activate in city_sources
        existing = conn.execute(
            "SELECT source_key FROM city_sources WHERE source_key = ?", (city_key,)
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE city_sources SET status = 'active' WHERE source_key = ?",
                (city_key,)
            )
        # No else — if it's not in city_sources, the CITY_REGISTRY dict entry is used

        # Create/update prod_city
        city_name = config.get('name', city_key.replace('_', ' ').title())
        state = config.get('state', '')
        from db import normalize_city_slug
        city_slug = normalize_city_slug(city_name)
        # Look up by city+state OR by slug (handles cases where slug exists from prior attempt)
        existing_prod = conn.execute(
            "SELECT id FROM prod_cities WHERE (city = ? AND state = ?) OR city_slug = ?",
            (city_name, state, city_slug)
        ).fetchone()
        if existing_prod:
            conn.execute("""
                UPDATE prod_cities SET status = 'active', total_permits = ?, source_id = ?,
                    city = ?, state = ?
                WHERE id = ?
            """, (len(normalized), city_key, city_name, state, existing_prod['id']))
        else:
            conn.execute("""
                INSERT INTO prod_cities (city, state, city_slug, source_id, status, total_permits)
                VALUES (?, ?, ?, ?, 'active', ?)
            """, (city_name, state, city_slug, city_key, len(normalized)))

        conn.commit()

        return jsonify({
            'status': 'SUCCESS',
            'city_key': city_key,
            'city_name': city_name,
            'state': state,
            'platform': platform,
            'test_records': len(test_raw),
            'raw_fetched': len(raw),
            'normalized': len(normalized),
            'inserted': inserted,
            'days_back': days_back,
            'message': f'✓ {city_name} is live. {len(normalized)} permits backfilled. Collector will pick it up next run.'
        })

    except Exception as e:
        return jsonify({'error': f'Test and backfill failed: {str(e)}'}), 500


@app.route('/api/admin/discover-and-activate', methods=['POST'])
def admin_discover_and_activate():
    """V35: Auto-discover fresh endpoints for stale cities, test, backfill, and activate.

    POST body (all optional):
      {"cities": ["milwaukee", "sacramento"]}  — specific cities to process
      If omitted, processes ALL stale cities from the discovery module.

    For each city:
    1. Search Socrata Discovery API, ArcGIS Hub, CKAN catalogs
    2. Test each discovered endpoint for 30-day freshness
    3. Build field mapping from sample data
    4. Backfill 180 days of historical data
    5. Normalize, insert, and activate

    Returns detailed results for each city.
    """
    valid, error = check_admin_key()
    if not valid:
        return error

    try:
        from discover_fresh_endpoints import discover_all, STALE_CITIES
        from collector import normalize_permit, fetch_socrata, fetch_arcgis, fetch_ckan, fetch_carto
        from city_source_db import get_city_config
        from db import normalize_city_slug

        data = request.get_json() or {}
        target_cities = data.get('cities')  # None = all stale cities
        days_back = data.get('days_back', 180)
        dry_run = data.get('dry_run', False)

        # Step 1: Discover fresh endpoints
        discovery_results = discover_all(target_cities)

        # Step 2: For each FOUND city, run test-and-backfill
        activation_results = {}
        for city_key, disc in discovery_results.items():
            if disc["status"] not in ("FOUND", "EXISTING_WORKS"):
                activation_results[city_key] = {
                    "status": disc["status"],
                    "message": f"No fresh endpoint found: {disc['status']}",
                }
                continue

            if dry_run:
                activation_results[city_key] = {
                    "status": "DRY_RUN",
                    "config": disc["config"],
                    "freshness": disc["freshness"],
                    "message": f"Would activate with {disc['config']['platform']} endpoint",
                }
                continue

            config = disc["config"]
            platform = config.get("platform", "socrata")

            try:
                # Backfill: fetch 180 days
                config["active"] = True
                if platform == "socrata":
                    raw = fetch_socrata(config, days_back)
                elif platform == "arcgis":
                    raw = fetch_arcgis(config, days_back)
                elif platform == "ckan":
                    raw = fetch_ckan(config, days_back)
                elif platform == "carto":
                    raw = fetch_carto(config, days_back)
                else:
                    activation_results[city_key] = {"status": "ERROR", "error": f"Unknown platform: {platform}"}
                    continue

                if not raw:
                    activation_results[city_key] = {"status": "ERROR", "error": "Backfill returned 0 records"}
                    continue

                # Normalize — we need a config in the registry or city_sources for normalize_permit to work.
                # Use the discovered config's field_map directly.
                normalized = []
                fmap = config.get("field_map", {})
                city_name = config.get("name", city_key)
                state = config.get("state", "")

                for record in raw:
                    try:
                        # Manual normalization using discovered field_map
                        import re as _re
                        def _get(field_name):
                            raw_key = fmap.get(field_name, "")
                            if not raw_key:
                                return ""
                            return str(record.get(raw_key, "")).strip()

                        permit_number = _get("permit_number")
                        if not permit_number:
                            continue

                        # Parse date
                        date_str = _get("filing_date") or _get("date") or _get("issued_date")
                        parsed_date = ""
                        if date_str:
                            if str(date_str).isdigit() and len(str(date_str)) >= 10:
                                try:
                                    parsed_date = datetime.fromtimestamp(int(date_str) / 1000).strftime("%Y-%m-%d")
                                except (ValueError, OSError):
                                    pass
                            if not parsed_date:
                                for fmt in ["%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%m/%d/%Y"]:
                                    try:
                                        parsed_date = datetime.strptime(str(date_str)[:26], fmt).strftime("%Y-%m-%d")
                                        break
                                    except ValueError:
                                        continue
                            if not parsed_date and '/' in str(date_str):
                                try:
                                    parts = str(date_str).split()[0].split('/')
                                    if len(parts) == 3:
                                        m, d, y = int(parts[0]), int(parts[1]), int(parts[2])
                                        parsed_date = f"{y:04d}-{m:02d}-{d:02d}"
                                except (ValueError, IndexError):
                                    pass
                            if not parsed_date:
                                parsed_date = str(date_str)[:10]

                        # Parse cost
                        cost_str = _get("estimated_cost")
                        try:
                            cost = float(_re.sub(r'[^\d.]', '', cost_str)) if cost_str else 0
                        except (ValueError, TypeError):
                            cost = 0
                        if cost > 50_000_000:
                            cost = 50_000_000

                        address = _get("address") or "Address not provided"
                        description = _get("description") or _get("work_type") or ""

                        normalized.append({
                            "permit_number": permit_number,
                            "permit_type": _get("permit_type") or "Building Permit",
                            "work_type": _get("work_type") or "",
                            "address": address,
                            "city": city_name,
                            "state": state,
                            "zip": _get("zip") or "",
                            "filing_date": parsed_date,
                            "status": _get("status") or "",
                            "estimated_cost": cost,
                            "description": description,
                            "owner_name": _get("owner_name") or "",
                            "contact_name": _get("contact_name") or "",
                        })
                    except Exception:
                        continue

                if not normalized:
                    activation_results[city_key] = {
                        "status": "ERROR",
                        "error": f"Got {len(raw)} raw records but 0 normalized. Field map may be wrong.",
                        "config": config,
                    }
                    continue

                # Insert
                inserted = permitdb.upsert_permits(normalized, source_city_key=city_key)

                # Activate in city_sources
                conn = permitdb.get_connection()
                existing = conn.execute(
                    "SELECT source_key FROM city_sources WHERE source_key = ?", (city_key,)
                ).fetchone()

                if existing:
                    # Update existing source with new endpoint info
                    conn.execute("""
                        UPDATE city_sources SET
                            status = 'active',
                            endpoint = ?,
                            platform = ?,
                            date_field = ?,
                            field_map = ?
                        WHERE source_key = ?
                    """, (
                        config["endpoint"],
                        platform,
                        config.get("date_field", ""),
                        json.dumps(config.get("field_map", {})),
                        city_key,
                    ))
                else:
                    # Insert new city_source
                    conn.execute("""
                        INSERT INTO city_sources (source_key, name, state, platform, endpoint,
                            date_field, field_map, status, mode)
                        VALUES (?, ?, ?, ?, ?, ?, ?, 'active', 'city')
                    """, (
                        city_key,
                        city_name,
                        state,
                        platform,
                        config["endpoint"],
                        config.get("date_field", ""),
                        json.dumps(config.get("field_map", {})),
                    ))

                # Create/update prod_city (lookup by slug too to avoid UNIQUE constraint)
                city_slug = normalize_city_slug(city_name)
                existing_prod = conn.execute(
                    "SELECT id FROM prod_cities WHERE (city = ? AND state = ?) OR city_slug = ?",
                    (city_name, state, city_slug)
                ).fetchone()
                if existing_prod:
                    conn.execute("""
                        UPDATE prod_cities SET status = 'active', total_permits = ?, source_id = ?,
                            city = ?, state = ?
                        WHERE id = ?
                    """, (len(normalized), city_key, city_name, state, existing_prod['id']))
                else:
                    conn.execute("""
                        INSERT INTO prod_cities (city, state, city_slug, source_id, status, total_permits)
                        VALUES (?, ?, ?, ?, 'active', ?)
                    """, (city_name, state, city_slug, city_key, len(normalized)))

                conn.commit()

                activation_results[city_key] = {
                    "status": "ACTIVATED",
                    "raw_fetched": len(raw),
                    "normalized": len(normalized),
                    "inserted": inserted,
                    "platform": platform,
                    "endpoint": config["endpoint"],
                    "date_field": config.get("date_field"),
                    "newest_date": disc["freshness"].get("newest_date"),
                    "message": f"✓ {city_name} is live. {len(normalized)} permits backfilled.",
                }

            except Exception as e:
                activation_results[city_key] = {
                    "status": "ERROR",
                    "error": str(e),
                    "config": config,
                }

        # Summary
        activated = [k for k, v in activation_results.items() if v.get("status") == "ACTIVATED"]
        failed = [k for k, v in activation_results.items() if v.get("status") == "ERROR"]
        not_found = [k for k, v in activation_results.items() if v.get("status") in ("NOT_FOUND", "STALE")]

        return jsonify({
            "summary": {
                "activated": len(activated),
                "failed": len(failed),
                "not_found": len(not_found),
                "dry_run": dry_run,
            },
            "activated_cities": activated,
            "failed_cities": failed,
            "not_found_cities": not_found,
            "details": activation_results,
            "discovery": {k: {
                "status": v["status"],
                "endpoints_found": len(v.get("all_discovered", [])),
                "endpoints_tested": len(v.get("all_tested", [])),
            } for k, v in discovery_results.items()},
        })

    except Exception as e:
        import traceback
        return jsonify({'error': f'Discovery failed: {str(e)}', 'traceback': traceback.format_exc()}), 500


@app.route('/api/admin/pause-empty', methods=['POST'])
def admin_pause_empty_cities():
    """V34: Pause all active prod_cities that have 0 actual permits in DB.
    This cleans up cities that are marked active but have never successfully collected data.
    """
    valid, error = check_admin_key()
    if not valid:
        return error

    try:
        paused = permitdb.pause_cities_without_data()
        return jsonify({
            'paused_count': len(paused),
            'paused_cities': paused,
            'message': f'Paused {len(paused)} cities with no permit data'
        })
    except Exception as e:
        return jsonify({'error': f'Pause operation failed: {str(e)}'}), 500


@app.route('/api/admin/cleanup-data', methods=['POST'])
def admin_cleanup_data():
    """V35: Run comprehensive data cleanup — fix wrong states, remove garbage records.
    This is safe to run multiple times (idempotent).
    """
    valid, error = check_admin_key()
    if not valid:
        return error

    try:
        conn = permitdb.get_connection()
        before = conn.execute("SELECT COUNT(*) as cnt FROM permits").fetchone()['cnt']

        # Step 1: Clean prod_cities names first (so state lookups work)
        prod_all = conn.execute("SELECT id, city, state FROM prod_cities").fetchall()
        name_fixes = 0
        for row in prod_all:
            cleaned = permitdb.clean_city_name_for_prod(row['city'], row['state'])
            if cleaned != row['city']:
                # Check if cleaned name already exists (avoid UNIQUE constraint violation)
                existing = conn.execute(
                    "SELECT id FROM prod_cities WHERE city = ? AND state = ?",
                    (cleaned, row['state'])
                ).fetchone()
                if existing:
                    conn.execute("DELETE FROM prod_cities WHERE id = ?", (row['id'],))
                else:
                    conn.execute("UPDATE prod_cities SET city = ? WHERE id = ?", (cleaned, row['id']))
                name_fixes += 1
        conn.commit()

        # Step 2: Fix wrong states using cleaned prod_cities as truth
        prod_rows = conn.execute(
            "SELECT city, state FROM prod_cities WHERE state IS NOT NULL AND state != ''"
        ).fetchall()
        state_fixes = 0
        for row in prod_rows:
            result = conn.execute(
                "UPDATE permits SET state = ? WHERE (city = ? OR LOWER(city) = LOWER(?)) AND state != ?",
                (row['state'], row['city'], row['city'], row['state'])
            )
            if result.rowcount > 0:
                state_fixes += result.rowcount
        conn.commit()

        # Step 3: Run V34/V35 data cleanup (garbage deletion, casing fixes)
        cleanup_fixed = permitdb._run_v34_data_cleanup(conn)

        # Step 4: Sync permit counts
        permitdb._sync_prod_city_counts(conn)

        after = conn.execute("SELECT COUNT(*) as cnt FROM permits").fetchone()['cnt']

        return jsonify({
            'prod_city_names_fixed': name_fixes,
            'state_assignments_fixed': state_fixes,
            'cleanup_records_affected': cleanup_fixed,
            'permits_before': before,
            'permits_after': after,
            'permits_removed': before - after,
        })
    except Exception as e:
        return jsonify({'error': f'Cleanup failed: {str(e)}'}), 500


@app.route('/api/admin/query', methods=['POST'])
def admin_query():
    """V34: Run a read-only SQL query for diagnostics.
    Body: {"sql": "SELECT ...", "limit": 100}
    Only SELECT statements allowed.
    """
    valid, error = check_admin_key()
    if not valid:
        return error

    try:
        data = request.get_json() or {}
        sql = data.get('sql', '').strip()
        limit = min(data.get('limit', 100), 1000)

        # V66: Safety check — only allow SELECT queries
        # Use word boundaries to avoid false positives on column names like 'last_update'
        import re
        sql_upper = sql.upper()
        if not sql_upper.startswith('SELECT'):
            return jsonify({'error': 'Only SELECT queries allowed'}), 400

        # Check for forbidden keywords as standalone words (not within column names)
        forbidden_keywords = ['INSERT', 'UPDATE', 'DELETE', 'DROP', 'ALTER', 'CREATE', 'ATTACH', 'TRUNCATE']
        for forbidden in forbidden_keywords:
            # \b = word boundary — won't match 'last_update' for 'UPDATE'
            if re.search(rf'\b{forbidden}\b', sql_upper):
                return jsonify({'error': f'Forbidden keyword: {forbidden}'}), 400

        conn = permitdb.get_connection()
        try:
            rows = conn.execute(sql).fetchmany(limit)
            result = [dict(r) for r in rows]
            return jsonify({'rows': result, 'count': len(result)})
        finally:
            conn.close()  # V66: Fix connection leak
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/fix-states', methods=['POST'])
def admin_fix_states():
    """V34b: Targeted state fix — fix specific city+wrong_state → correct_state.
    Body: {"fixes": [["city_name", "wrong_state", "correct_state"], ...]}
    Or use {"auto": true} to fix all known misattributions.
    """
    valid, error = check_admin_key()
    if not valid:
        return error

    try:
        data = request.get_json() or {}
        conn = permitdb.get_connection()
        total_fixed = 0
        details = []

        if data.get('auto'):
            # Auto-fix: use prod_cities state as truth for all permits
            prod_rows = conn.execute(
                "SELECT city, state FROM prod_cities WHERE state IS NOT NULL AND state != ''"
            ).fetchall()
            for row in prod_rows:
                city = row['city']
                correct_state = row['state']
                # Fix exact match and LOWER match
                result = conn.execute(
                    "UPDATE permits SET state = ? WHERE (city = ? OR LOWER(city) = LOWER(?)) AND state != ?",
                    (correct_state, city, city, correct_state)
                )
                if result.rowcount > 0:
                    details.append(f"{city} → {correct_state}: {result.rowcount} fixed")
                    total_fixed += result.rowcount
            conn.commit()
        else:
            fixes = data.get('fixes', [])
            for city, wrong_state, correct_state in fixes:
                result = conn.execute(
                    "UPDATE permits SET state = ? WHERE LOWER(city) = LOWER(?) AND state = ?",
                    (correct_state, city, wrong_state)
                )
                if result.rowcount > 0:
                    details.append(f"{city} {wrong_state}→{correct_state}: {result.rowcount}")
                    total_fixed += result.rowcount
            conn.commit()

        return jsonify({'total_fixed': total_fixed, 'details': details})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/fix-prod-cities', methods=['POST'])
def admin_fix_prod_cities():
    """V34b: Fix prod_cities table — clean city names, remove state from city names."""
    valid, error = check_admin_key()
    if not valid:
        return error

    try:
        conn = permitdb.get_connection()
        fixes = []

        # Fix city names that have state appended (e.g., "Norfolk Va" → "Norfolk")
        rows = conn.execute("SELECT id, city, state FROM prod_cities").fetchall()
        for row in rows:
            city = row['city']
            state = row['state'] or ''
            original = city

            # Remove state abbreviation from end (e.g., "Norfolk Va" → "Norfolk")
            import re
            # Match " XX" or " Xx" at end where XX matches state
            if state and len(state) == 2:
                pattern = rf'\s+{re.escape(state)}$'
                cleaned = re.sub(pattern, '', city, flags=re.IGNORECASE)
                if cleaned != city:
                    city = cleaned

            # Fix "Prince George'South County Md" → "Prince George's County"
            city = city.replace("George'South", "George's")

            # Fix "Saint." → "St."
            city = city.replace("Saint.", "St.")

            # Fix "Little Rock Ar Metro" → "Little Rock"
            city = re.sub(r'\s+(Metro|Area|Region)$', '', city, flags=re.IGNORECASE)

            # Fix double state references
            if city != original:
                conn.execute("UPDATE prod_cities SET city = ? WHERE id = ?", (city, row['id']))
                fixes.append(f"{original} → {city}")

        conn.commit()

        # Also sync the counts after name fixes
        permitdb._sync_prod_city_counts(conn)

        return jsonify({'fixes': fixes, 'count': len(fixes)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ===========================
# V91: ORPHANED PERMITS CLEANUP
# ===========================

@app.route('/api/admin/purge-orphaned-permits', methods=['POST'])
def admin_purge_orphaned_permits():
    """V91: Delete permits from sources that no longer exist in CITY_REGISTRY or BULK_SOURCES.

    This fixes the issue where historical bulk sources collected permits, then the sources
    were removed from configs, but the permits remained and got incorrectly linked to cities.

    Body (optional): {"dry_run": true} to preview without deleting
    """
    valid, error = check_admin_key()
    if not valid:
        return error

    try:
        from city_configs import CITY_REGISTRY, BULK_SOURCES

        data = request.get_json() or {}
        dry_run = data.get('dry_run', False)

        # Build set of all valid source keys
        valid_sources = set(CITY_REGISTRY.keys()) | set(BULK_SOURCES.keys())

        conn = permitdb.get_connection()

        # Find orphaned source_city_key values
        orphaned = conn.execute("""
            SELECT DISTINCT source_city_key, COUNT(*) as cnt
            FROM permits
            WHERE source_city_key IS NOT NULL
              AND source_city_key != ''
            GROUP BY source_city_key
        """).fetchall()

        orphaned_sources = []
        total_orphaned_permits = 0
        for row in orphaned:
            source_key = row['source_city_key']
            count = row['cnt']
            if source_key not in valid_sources:
                orphaned_sources.append({'source': source_key, 'permits': count})
                total_orphaned_permits += count

        if dry_run:
            return jsonify({
                'dry_run': True,
                'orphaned_sources': orphaned_sources,
                'total_permits_to_delete': total_orphaned_permits,
                'valid_sources_count': len(valid_sources),
            })

        # Delete orphaned permits
        deleted = 0
        for item in orphaned_sources:
            result = conn.execute(
                "DELETE FROM permits WHERE source_city_key = ?",
                (item['source'],)
            )
            deleted += result.rowcount

        conn.commit()

        # Re-run V86 city linking for any remaining unlinked permits
        permitdb._run_v86_city_linking(conn)

        # Re-sync counts
        permitdb._sync_prod_city_counts(conn)

        # Get updated stats
        cities_with_data = conn.execute(
            "SELECT COUNT(*) as cnt FROM prod_cities WHERE total_permits > 0"
        ).fetchone()['cnt']
        total_permits = conn.execute("SELECT COUNT(*) FROM permits").fetchone()[0]

        return jsonify({
            'deleted': deleted,
            'orphaned_sources_cleaned': len(orphaned_sources),
            'orphaned_sources': orphaned_sources,
            'cities_with_data_now': cities_with_data,
            'total_permits_remaining': total_permits,
        })
    except Exception as e:
        import traceback
        return jsonify({'error': str(e), 'traceback': traceback.format_exc()}), 500


@app.route('/api/admin/v93-cleanup', methods=['POST'])
def admin_v93_cleanup():
    """V93: Comprehensive data cleanup - fix state corruption and create missing cities.

    This endpoint runs the full V93 cleanup:
    1. Fix TX/OK/LA state corruption in existing permits
    2. Create prod_cities entries for cities in permits but not in prod_cities
    3. Re-link permits to prod_city_ids
    4. Sync prod_city counts

    Expected result: City count should jump from ~1,268 to ~1,800+
    """
    valid, error = check_admin_key()
    if not valid:
        return error

    try:
        # Run the full V93 cleanup
        stats = permitdb.run_v93_cleanup()

        # Get final stats
        conn = permitdb.get_connection()
        cities_with_data = conn.execute(
            "SELECT COUNT(*) FROM prod_cities WHERE total_permits > 0"
        ).fetchone()[0]
        total_cities = conn.execute("SELECT COUNT(*) FROM prod_cities").fetchone()[0]
        total_permits = conn.execute("SELECT COUNT(*) FROM permits").fetchone()[0]
        linked_permits = conn.execute("SELECT COUNT(*) FROM permits WHERE prod_city_id IS NOT NULL").fetchone()[0]

        # State breakdown
        state_breakdown = conn.execute("""
            SELECT state, COUNT(*) as cities
            FROM prod_cities
            WHERE total_permits > 0
            GROUP BY state
            ORDER BY COUNT(*) DESC
            LIMIT 20
        """).fetchall()

        return jsonify({
            'status': 'success',
            'cleanup_stats': stats,
            'final_stats': {
                'cities_with_data': cities_with_data,
                'total_cities': total_cities,
                'total_permits': total_permits,
                'linked_permits': linked_permits,
                'link_rate': f"{linked_permits/total_permits*100:.1f}%" if total_permits > 0 else "0%",
                'top_states': [{'state': r[0], 'cities': r[1]} for r in state_breakdown],
            }
        })

    except Exception as e:
        import traceback
        return jsonify({'error': str(e), 'traceback': traceback.format_exc()}), 500


@app.route('/api/admin/v93-harvest-cities', methods=['POST'])
def admin_v93_harvest_cities():
    """V93: Lightweight endpoint to harvest cities from permits into prod_cities.

    Only creates prod_cities entries for cities that exist in permits but not in prod_cities.
    Much faster than full V93 cleanup - no state fixing or permit relinking.
    """
    valid, error = check_admin_key()
    if not valid:
        return error

    try:
        conn = permitdb.get_connection()

        # Find cities in permits that don't have prod_cities entries
        missing = conn.execute("""
            SELECT p.city, p.state, COUNT(*) as permit_count
            FROM permits p
            LEFT JOIN prod_cities pc ON LOWER(p.city) = LOWER(pc.city) AND p.state = pc.state
            WHERE pc.id IS NULL
              AND p.city IS NOT NULL AND p.city != ''
              AND p.state IS NOT NULL AND p.state != ''
              AND LENGTH(p.city) >= 2
            GROUP BY p.city, p.state
            HAVING COUNT(*) >= 5
            ORDER BY COUNT(*) DESC
            LIMIT 500
        """).fetchall()

        created = 0
        import re
        for row in missing:
            city_name = row['city'] if hasattr(row, 'keys') else row[0]
            state = row['state'] if hasattr(row, 'keys') else row[1]
            permit_count = row['permit_count'] if hasattr(row, 'keys') else row[2]

            # Skip garbage
            if not city_name or any(x in city_name.lower() for x in ['test', 'unknown', 'n/a', 'none', 'null']):
                continue

            # Generate slug
            slug = re.sub(r'[^a-z0-9]+', '-', city_name.lower()).strip('-')
            slug_with_state = f"{slug}-{state.lower()}"

            # Check if slug exists
            existing = conn.execute(
                "SELECT id FROM prod_cities WHERE city_slug = ? OR city_slug = ?",
                (slug, slug_with_state)
            ).fetchone()

            if not existing:
                conn.execute("""
                    INSERT INTO prod_cities (city, state, city_slug, status, source_type, source_id, added_by, total_permits)
                    VALUES (?, ?, ?, 'active', 'bulk', 'v93_harvest', 'v93_harvest_endpoint', ?)
                """, (city_name, state, slug_with_state, permit_count))
                created += 1

        conn.commit()

        # Get updated counts
        total = conn.execute("SELECT COUNT(*) FROM prod_cities").fetchone()[0]
        with_data = conn.execute("SELECT COUNT(*) FROM prod_cities WHERE total_permits > 0").fetchone()[0]

        return jsonify({
            'status': 'success',
            'cities_found': len(missing),
            'cities_created': created,
            'total_prod_cities': total,
            'cities_with_data': with_data
        })

    except Exception as e:
        import traceback
        return jsonify({'error': str(e), 'traceback': traceback.format_exc()}), 500


# ===========================
# V12.53: ADMIN EMAIL ENDPOINTS
# ===========================

@app.route('/api/admin/send-digest', methods=['POST'])
@app.route('/api/admin/test-digest', methods=['POST'])  # V12.58: Route alias
def admin_send_digest():
    """V73: Manually trigger daily digest with diagnostics."""
    valid, error = check_admin_key()
    if not valid:
        return error

    # V12.58: Read email from both query string and request body
    email = request.args.get('email') or (request.json or {}).get('email', '')
    diagnose = request.args.get('diagnose', '').lower() == 'true'

    diagnostics = {}

    # V73: Always run diagnostics if requested
    if diagnose or not email:
        try:
            # Check SMTP config
            smtp_pass = os.environ.get('SMTP_PASS', '')
            diagnostics['smtp_configured'] = bool(smtp_pass)
            diagnostics['smtp_host'] = os.environ.get('SMTP_HOST', 'smtp.sendgrid.net')

            # Check subscribers file
            from pathlib import Path
            subscribers_path = Path("/var/data/subscribers.json")
            if not subscribers_path.exists():
                subscribers_path = Path(os.path.dirname(__file__)) / "data" / "subscribers.json"

            diagnostics['subscribers_path'] = str(subscribers_path)
            diagnostics['subscribers_exists'] = subscribers_path.exists()

            if subscribers_path.exists():
                import json
                with open(subscribers_path) as f:
                    subs = json.load(f)
                diagnostics['total_subscribers'] = len(subs)
                diagnostics['active_subscribers'] = len([s for s in subs if s.get('active', False)])

            # Check email_alerts import
            try:
                from email_alerts import send_daily_digest, send_test_digest, load_subscribers
                diagnostics['email_alerts_import'] = 'OK'
                diagnostics['loaded_subscribers'] = len(load_subscribers())
            except ImportError as e:
                diagnostics['email_alerts_import'] = f'FAILED: {e}'

        except Exception as e:
            diagnostics['diagnostic_error'] = str(e)

    try:
        from email_alerts import send_daily_digest, send_test_digest
        if email:
            # Send to specific email for testing
            result = send_test_digest(email)
            return jsonify({'status': 'sent', 'to': email, 'result': result, 'diagnostics': diagnostics})
        else:
            # Send to all subscribers
            sent, failed = send_daily_digest()
            return jsonify({'status': 'done', 'sent': sent, 'failed': failed, 'diagnostics': diagnostics})
    except Exception as e:
        import traceback
        return jsonify({
            'error': f'Digest failed: {str(e)}',
            'traceback': traceback.format_exc(),
            'diagnostics': diagnostics
        }), 500


@app.route('/api/admin/data-freshness', methods=['GET'])
def admin_data_freshness():
    """V12.58: Return data freshness stats for all cities. Useful for monitoring stale sources."""
    valid, error = check_admin_key()
    if not valid:
        return error

    conn = permitdb.get_connection()
    cursor = conn.execute("""
        SELECT city, state, COUNT(*) as total_permits, MAX(filing_date) as newest_date
        FROM permits
        WHERE filing_date IS NOT NULL AND filing_date != ''
        GROUP BY city, state
        ORDER BY newest_date ASC
    """)

    results = []
    now = datetime.now()
    for row in cursor:
        newest = row['newest_date']
        days_stale = None
        if newest:
            try:
                newest_dt = datetime.strptime(newest[:10], '%Y-%m-%d')
                days_stale = (now - newest_dt).days
            except (ValueError, TypeError):
                pass
        results.append({
            'city': row['city'],
            'state': row['state'],
            'total_permits': row['total_permits'],
            'newest_filing_date': newest,
            'days_stale': days_stale
        })

    # Also get cities with NULL dates
    null_dates = conn.execute("""
        SELECT city, state, COUNT(*) as count
        FROM permits
        WHERE filing_date IS NULL OR filing_date = ''
        GROUP BY city, state
        ORDER BY count DESC
    """).fetchall()

    return jsonify({
        'cities': results,
        'cities_with_null_dates': [dict(r) for r in null_dates],
        'total_cities': len(results),
        'stale_count': len([r for r in results if r['days_stale'] and r['days_stale'] > 30])
    })


@app.route('/api/admin/stale-cities', methods=['GET'])
def admin_stale_cities():
    """V18: Get stale cities review queue and freshness summary."""
    valid, error = check_admin_key()
    if not valid:
        return error

    try:
        # Get freshness summary
        summary = permitdb.get_freshness_summary()

        # Get review queue
        review_queue = permitdb.get_review_queue()

        # Get currently stale cities (active but stale)
        stale = permitdb.get_stale_cities()

        return jsonify({
            'summary': summary,
            'review_queue': review_queue,
            'currently_stale': stale,
            'thresholds': {
                'stale_days': permitdb.FRESHNESS_STALE_DAYS,
                'very_stale_days': permitdb.FRESHNESS_VERY_STALE_DAYS,
            }
        })
    except Exception as e:
        return jsonify({'error': f'Failed to get stale cities: {str(e)}'}), 500


@app.route('/api/admin/send-welcome', methods=['POST'])
def admin_send_welcome():
    """V12.53: Send welcome email to a specific user."""
    valid, error = check_admin_key()
    if not valid:
        return error

    # V12.59: Read from both query string and JSON body
    email = request.args.get('email') or (request.json or {}).get('email', '')
    email_type = request.args.get('type') or (request.json or {}).get('type', 'free')

    if not email:
        return jsonify({'error': 'Email parameter required'}), 400

    user = find_user_by_email(email)
    if not user:
        return jsonify({'error': f'User {email} not found'}), 404

    try:
        from email_alerts import send_welcome_free, send_welcome_pro_trial
        if email_type == 'pro':
            send_welcome_pro_trial(user)
        else:
            send_welcome_free(user)
        return jsonify({'status': 'sent', 'to': email, 'type': email_type})
    except Exception as e:
        return jsonify({'error': f'Welcome email failed: {str(e)}'}), 500


@app.route('/api/admin/run-trial-check', methods=['POST'])
def admin_run_trial_check():
    """V12.53: Manually run trial lifecycle check."""
    valid, error = check_admin_key()
    if not valid:
        return error

    try:
        from email_alerts import check_trial_lifecycle
        results = check_trial_lifecycle()
        return jsonify({'status': 'done', 'results': results})
    except Exception as e:
        return jsonify({'error': f'Trial check failed: {str(e)}'}), 500


@app.route('/api/admin/run-onboarding-check', methods=['POST'])
def admin_run_onboarding_check():
    """V12.53: Manually run onboarding nudge check."""
    valid, error = check_admin_key()
    if not valid:
        return error

    try:
        from email_alerts import check_onboarding_nudges
        sent = check_onboarding_nudges()
        return jsonify({'status': 'done', 'sent': sent})
    except Exception as e:
        return jsonify({'error': f'Onboarding check failed: {str(e)}'}), 500


@app.route('/api/admin/email-stats')
def admin_email_stats():
    """V12.53: Get email system statistics."""
    valid, error = check_admin_key()
    if not valid:
        return error

    try:
        users = User.query.all()

        stats = {
            'total_users': len(users),
            'digest_active': sum(1 for u in users if u.digest_active),
            'email_verified': sum(1 for u in users if u.email_verified),
            'welcome_sent': sum(1 for u in users if u.welcome_email_sent),
            'pro_trial_users': sum(1 for u in users if u.plan == 'pro_trial'),
            'pro_users': sum(1 for u in users if u.plan == 'pro'),
            'free_users': sum(1 for u in users if u.plan == 'free'),
            'trial_midpoint_sent': sum(1 for u in users if u.trial_midpoint_sent),
            'trial_ending_sent': sum(1 for u in users if u.trial_ending_sent),
            'trial_expired_sent': sum(1 for u in users if u.trial_expired_sent),
        }
        return jsonify(stats)
    except Exception as e:
        return jsonify({'error': f'Stats failed: {str(e)}'}), 500


@app.route('/api/admin/email-status')
def admin_email_status():
    """V73: Comprehensive email system health check.
    V78: Added digest daemon thread status tracking."""
    diagnostics = {
        'timestamp': datetime.now().isoformat(),
        'checks': {}
    }

    # V78: Include daemon thread status
    diagnostics['digest_daemon'] = DIGEST_STATUS.copy()

    # Check 1: SMTP environment variables
    try:
        smtp_pass = os.environ.get('SMTP_PASS', '')
        smtp_host = os.environ.get('SMTP_HOST', 'smtp.sendgrid.net')
        smtp_port = os.environ.get('SMTP_PORT', '587')
        diagnostics['checks']['smtp'] = {
            'pass_configured': bool(smtp_pass),
            'pass_length': len(smtp_pass) if smtp_pass else 0,
            'host': smtp_host,
            'port': smtp_port,
            'status': 'OK' if smtp_pass else 'MISSING SMTP_PASS'
        }
    except Exception as e:
        diagnostics['checks']['smtp'] = {'status': 'ERROR', 'error': str(e)}

    # Check 2: email_alerts.py import
    try:
        from email_alerts import SMTP_PASS, SUBSCRIBERS_FILE, load_subscribers, send_email
        diagnostics['checks']['email_alerts_import'] = {
            'status': 'OK',
            'smtp_pass_in_module': bool(SMTP_PASS)
        }
    except ImportError as e:
        diagnostics['checks']['email_alerts_import'] = {'status': 'FAILED', 'error': str(e)}
        return jsonify(diagnostics), 500

    # Check 3: Subscribers file
    try:
        diagnostics['checks']['subscribers_file'] = {
            'path': str(SUBSCRIBERS_FILE),
            'exists': SUBSCRIBERS_FILE.exists(),
            'status': 'OK' if SUBSCRIBERS_FILE.exists() else 'MISSING'
        }
        if SUBSCRIBERS_FILE.exists():
            with open(SUBSCRIBERS_FILE) as f:
                raw_subs = json.load(f)
            diagnostics['checks']['subscribers_file']['total'] = len(raw_subs)
    except Exception as e:
        diagnostics['checks']['subscribers_file'] = {'status': 'ERROR', 'error': str(e)}

    # Check 4: Load subscribers function
    try:
        subs = load_subscribers()
        diagnostics['checks']['load_subscribers'] = {
            'status': 'OK',
            'active_count': len(subs),
            'sample_emails': [s.get('email', '?')[:3] + '***' for s in subs[:5]]
        }
    except Exception as e:
        diagnostics['checks']['load_subscribers'] = {'status': 'ERROR', 'error': str(e)}

    # Overall status
    all_ok = all(
        c.get('status') == 'OK'
        for c in diagnostics['checks'].values()
    )
    diagnostics['overall_status'] = 'HEALTHY' if all_ok else 'ISSUES_FOUND'

    return jsonify(diagnostics)


@app.route('/api/admin/sync-registry', methods=['POST'])
def admin_sync_registry():
    """V73: Batched sync CITY_REGISTRY to city_sources and prod_cities.

    Returns JSON with: added, updated, skipped, errors, sources_synced
    """
    valid, error = check_admin_key()
    if not valid:
        return error
    try:
        from collector import sync_city_registry_to_prod
        result = sync_city_registry_to_prod()
        # V73: Result is now a dict, not tuple
        if isinstance(result, dict):
            return jsonify(result)
        else:
            # Backwards compat if somehow old version runs
            sources, cities = result
            return jsonify({
                'sources_synced': sources,
                'added': cities,
                'updated': 0,
                'skipped': 0,
                'errors': 0
            })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'Sync failed: {str(e)}'}), 500


# ===========================
# V12.54: AUTONOMY ENGINE ADMIN ROUTES
# ===========================

@app.route('/api/admin/autonomy-status')
def admin_autonomy_status():
    """V12.54: Get autonomy engine status."""
    admin_key = request.headers.get('X-Admin-Key') or request.args.get('key')
    expected = os.environ.get('ADMIN_KEY', 'permitgrab-reset-2026')
    if admin_key != expected:
        return jsonify({"error": "unauthorized"}), 401
    try:
        from city_source_db import get_autonomy_status
        return jsonify(get_autonomy_status())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/admin/us-cities')
def admin_us_cities():
    """V12.54: List cities with filters."""
    admin_key = request.headers.get('X-Admin-Key') or request.args.get('key')
    expected = os.environ.get('ADMIN_KEY', 'permitgrab-reset-2026')
    if admin_key != expected:
        return jsonify({"error": "unauthorized"}), 401
    status = request.args.get('status')
    state = request.args.get('state')
    limit = int(request.args.get('limit', 50))
    offset = int(request.args.get('offset', 0))
    try:
        import db as permitdb
        conn = permitdb.get_connection()
        query = "SELECT * FROM us_cities WHERE 1=1"
        params = []
        if status:
            query += " AND status=?"
            params.append(status)
        if state:
            query += " AND state=?"
            params.append(state)
        query += " ORDER BY priority ASC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = conn.execute(query, params).fetchall()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/admin/us-counties')
def admin_us_counties():
    """V12.54: List counties with filters."""
    admin_key = request.headers.get('X-Admin-Key') or request.args.get('key')
    expected = os.environ.get('ADMIN_KEY', 'permitgrab-reset-2026')
    if admin_key != expected:
        return jsonify({"error": "unauthorized"}), 401
    status = request.args.get('status')
    limit = int(request.args.get('limit', 50))
    try:
        import db as permitdb
        conn = permitdb.get_connection()
        query = "SELECT * FROM us_counties WHERE 1=1"
        params = []
        if status:
            query += " AND status=?"
            params.append(status)
        query += " ORDER BY priority ASC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/admin/city-sources')
def admin_city_sources():
    """V12.54: List all data sources."""
    admin_key = request.headers.get('X-Admin-Key') or request.args.get('key')
    expected = os.environ.get('ADMIN_KEY', 'permitgrab-reset-2026')
    if admin_key != expected:
        return jsonify({"error": "unauthorized"}), 401
    try:
        import db as permitdb
        conn = permitdb.get_connection()
        rows = conn.execute("SELECT * FROM city_sources ORDER BY last_collected_at DESC LIMIT 200").fetchall()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/admin/bulk-sources')
def admin_bulk_sources():
    """V87: List bulk sources (county/state level)."""
    admin_key = request.headers.get('X-Admin-Key') or request.args.get('key')
    expected = os.environ.get('ADMIN_KEY', 'permitgrab-reset-2026')
    if admin_key != expected:
        return jsonify({"error": "unauthorized"}), 401
    try:
        import db as permitdb
        conn = permitdb.get_connection()
        rows = conn.execute("""
            SELECT * FROM bulk_sources ORDER BY total_permits_collected DESC LIMIT 100
        """).fetchall()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/admin/architecture-stats')
def admin_architecture_stats():
    """V87: Get clean architecture statistics."""
    admin_key = request.headers.get('X-Admin-Key') or request.args.get('key')
    expected = os.environ.get('ADMIN_KEY', 'permitgrab-reset-2026')
    if admin_key != expected:
        return jsonify({"error": "unauthorized"}), 401
    try:
        import db as permitdb
        conn = permitdb.get_connection()

        # Core counts
        total_cities = conn.execute("SELECT COUNT(*) FROM prod_cities").fetchone()[0]
        cities_with_data = conn.execute("SELECT COUNT(*) FROM prod_cities WHERE total_permits > 0").fetchone()[0]
        total_permits = conn.execute("SELECT COUNT(*) FROM permits").fetchone()[0]
        linked_permits = conn.execute("SELECT COUNT(*) FROM permits WHERE prod_city_id IS NOT NULL").fetchone()[0]

        # Source counts
        city_sources = conn.execute("SELECT COUNT(*) FROM city_sources WHERE status = 'active'").fetchone()[0]
        city_sources_linked = conn.execute("SELECT COUNT(*) FROM city_sources WHERE status = 'active' AND prod_city_id IS NOT NULL").fetchone()[0]

        # Bulk sources (may not exist yet)
        try:
            bulk_sources = conn.execute("SELECT COUNT(*) FROM bulk_sources WHERE status = 'active'").fetchone()[0]
        except Exception:
            bulk_sources = 0

        return jsonify({
            'prod_cities': {
                'total': total_cities,
                'with_data': cities_with_data,
                'without_data': total_cities - cities_with_data
            },
            'permits': {
                'total': total_permits,
                'linked': linked_permits,
                'unlinked': total_permits - linked_permits,
                'link_rate': f"{100 * linked_permits // total_permits}%" if total_permits > 0 else "0%"
            },
            'sources': {
                'city_sources': city_sources,
                'city_sources_linked': city_sources_linked,
                'bulk_sources': bulk_sources
            },
            'architecture': 'V87 - Clean FK-based relationships'
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route('/api/admin/discovery-log')
def admin_discovery_log():
    """V12.54: Recent discovery runs."""
    admin_key = request.headers.get('X-Admin-Key') or request.args.get('key')
    expected = os.environ.get('ADMIN_KEY', 'permitgrab-reset-2026')
    if admin_key != expected:
        return jsonify({"error": "unauthorized"}), 401
    try:
        import db as permitdb
        conn = permitdb.get_connection()
        rows = conn.execute("SELECT * FROM discovery_runs ORDER BY id DESC LIMIT 20").fetchall()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/admin/new-cities')
def admin_new_cities():
    """V17: Get recently activated cities for SEO tracking."""
    admin_key = request.headers.get('X-Admin-Key') or request.args.get('key')
    expected = os.environ.get('ADMIN_KEY', 'permitgrab-reset-2026')
    if admin_key != expected:
        return jsonify({"error": "unauthorized"}), 401

    days = int(request.args.get('days', 7))

    try:
        # Get recent activations
        activations = permitdb.get_recent_activations(days=days)

        # Get totals
        conn = permitdb.get_connection()
        total_active = conn.execute(
            "SELECT COUNT(*) as cnt FROM prod_cities WHERE status = 'active'"
        ).fetchone()['cnt']

        # Enrich with permit counts and page URLs
        enriched = []
        for a in activations:
            enriched.append({
                'city': a.get('city_name'),
                'state': a.get('state'),
                'slug': a.get('city_slug'),
                'activated_at': a.get('activated_at'),
                'permits': a.get('initial_permits', 0),
                'seo_status': a.get('seo_status', 'needs_content'),
                'source': a.get('source'),
                'page_url': f"https://permitgrab.com/permits/{a.get('city_slug')}"
            })

        return jsonify({
            'new_cities': enriched,
            'total_active': total_active,
            'activated_this_week': len([a for a in activations
                if a.get('activated_at', '') >= (datetime.now() - timedelta(days=7)).isoformat()])
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/admin/tracker')
def admin_tracker():
    """V64: Master city tracker — 20K rows, one per US city with coverage and freshness.

    Query params:
      state=TX — filter by state
      status=active — filter by coverage status (active/no_source)
      stale=true — only show stale/no_data cities
      limit=500 — limit rows (default 500, max 5000)
      offset=0 — pagination offset
      sort=population — sort field (population, last_permit_date, city)
    """
    valid, error = check_admin_key()
    if not valid:
        return error

    state = request.args.get('state')
    status = request.args.get('status')
    stale_only = request.args.get('stale') == 'true'
    limit = min(int(request.args.get('limit', 500)), 5000)
    offset = int(request.args.get('offset', 0))
    sort = request.args.get('sort', 'population')

    conn = permitdb.get_connection()
    try:
        # Build the tracker query
        # Join us_cities with prod_cities and scraper_runs for comprehensive view
        query = """
            SELECT
                uc.city_name,
                uc.state,
                uc.population,
                uc.slug as city_slug,
                uc.county,
                uc.covered_by_source,
                uc.status as discovery_status,
                -- Coverage info from prod_cities
                pc.status as coverage_status,
                pc.source_id,
                pc.source_type as platform,
                pc.source_scope,
                -- Freshness from prod_cities
                pc.newest_permit_date as last_permit_date,
                pc.last_collection as last_pull_date,
                pc.total_permits,
                pc.data_freshness,
                pc.consecutive_failures,
                pc.last_error
            FROM us_cities uc
            LEFT JOIN prod_cities pc ON (
                pc.city_slug = uc.slug
                OR pc.city_slug = REPLACE(uc.slug, '-', '_')
                OR pc.source_id = REPLACE(uc.slug, '-', '_')
            )
        """

        # Add WHERE clauses
        conditions = []
        params = []
        if state:
            conditions.append("uc.state = ?")
            params.append(state)
        if status == 'active':
            conditions.append("pc.status = 'active'")
        elif status == 'no_source':
            conditions.append("pc.city_slug IS NULL")
        if stale_only:
            conditions.append("(pc.data_freshness IN ('stale', 'very_stale', 'no_data') OR pc.city_slug IS NULL)")

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        # Sort
        sort_map = {
            'population': 'uc.population DESC',
            'last_permit_date': 'pc.newest_permit_date DESC',
            'city': 'uc.city_name ASC',
        }
        query += f" ORDER BY {sort_map.get(sort, 'uc.population DESC')}"
        query += " LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = conn.execute(query, params).fetchall()

        # Get total count for pagination
        count_query = "SELECT COUNT(*) FROM us_cities uc"
        if conditions:
            count_query += " LEFT JOIN prod_cities pc ON pc.city_slug = uc.slug OR pc.city_slug = REPLACE(uc.slug, '-', '_')"
            count_query += " WHERE " + " AND ".join(conditions)
        total = conn.execute(count_query, params[:-2] if len(params) > 2 else []).fetchone()[0]

        # Summary stats
        summary = {
            'total_us_cities': conn.execute("SELECT COUNT(*) FROM us_cities").fetchone()[0],
            'active_in_prod': conn.execute("SELECT COUNT(*) FROM prod_cities WHERE status='active'").fetchone()[0],
            'with_permits': conn.execute("SELECT COUNT(DISTINCT city) FROM permits WHERE city IS NOT NULL").fetchone()[0],
            'stale_count': conn.execute("SELECT COUNT(*) FROM prod_cities WHERE data_freshness IN ('stale', 'very_stale')").fetchone()[0],
        }

        return jsonify({
            'summary': summary,
            'tracker': [dict(row) for row in rows],
            'pagination': {'limit': limit, 'offset': offset, 'total': total}
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/freshness')
def admin_freshness():
    """V64: Run freshness classification and return results.

    Shows which cities are fresh, stale, broken, or have no data.
    """
    valid, error = check_admin_key()
    if not valid:
        return error

    from collector import classify_city_freshness
    try:
        result = classify_city_freshness()
        return jsonify(result)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/refresh-freshness', methods=['POST'])
def admin_refresh_freshness():
    """V71: Recalculate prod_cities freshness from actual permits table.

    Fixes the issue where 431 cities show 'no_data' despite having real permits.
    The root cause is that newest_permit_date was never populated for these cities.
    """
    valid, error = check_admin_key()
    if not valid:
        return error

    from datetime import datetime, timedelta

    try:
        today = datetime.now().strftime('%Y-%m-%d')
        thirty_days_ago = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')

        conn = permitdb.get_connection()

        # Get all active prod_cities
        cities = conn.execute(
            "SELECT city_slug, source_id, city FROM prod_cities WHERE status='active'"
        ).fetchall()

        updated = 0
        freshness_counts = {'fresh': 0, 'aging': 0, 'stale': 0, 'no_data': 0}

        for row in cities:
            city_slug = row['city_slug'] if isinstance(row, dict) else row[0]
            source_id = row['source_id'] if isinstance(row, dict) else row[1]
            city_name = row['city'] if isinstance(row, dict) else row[2]

            newest = None
            recent = 0

            # Try source_city_key match first (primary join strategy)
            if source_id:
                result = conn.execute(
                    "SELECT MAX(date) as newest, COUNT(CASE WHEN date >= ? THEN 1 END) as recent "
                    "FROM permits WHERE source_city_key = ?",
                    (thirty_days_ago, source_id)
                ).fetchone()
                if result:
                    newest = result['newest'] if isinstance(result, dict) else result[0]
                    recent = (result['recent'] if isinstance(result, dict) else result[1]) or 0

            # Fallback: try city name match
            if not newest and city_name:
                result = conn.execute(
                    "SELECT MAX(date) as newest, COUNT(CASE WHEN date >= ? THEN 1 END) as recent "
                    "FROM permits WHERE city = ?",
                    (thirty_days_ago, city_name)
                ).fetchone()
                if result:
                    newest = result['newest'] if isinstance(result, dict) else result[0]
                    recent = (result['recent'] if isinstance(result, dict) else result[1]) or 0

            # Calculate freshness
            if newest:
                try:
                    days_old = (datetime.now() - datetime.strptime(newest, '%Y-%m-%d')).days
                    if days_old <= 14:
                        freshness = 'fresh'
                    elif days_old <= 30:
                        freshness = 'aging'
                    elif days_old <= 90:
                        freshness = 'stale'
                    else:
                        freshness = 'no_data'
                except Exception:
                    freshness = 'no_data'
            else:
                freshness = 'no_data'

            # Update prod_cities
            conn.execute(
                "UPDATE prod_cities SET newest_permit_date=?, permits_last_30d=?, data_freshness=? "
                "WHERE city_slug=?",
                (newest, recent, freshness, city_slug)
            )

            freshness_counts[freshness] = freshness_counts.get(freshness, 0) + 1
            updated += 1

        conn.commit()

        return jsonify({
            'status': 'success',
            'updated': updated,
            'freshness': freshness_counts
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/activate-city-sources', methods=['POST'])
def admin_activate_city_sources():
    """V71: Activate all inactive city_sources that have matching active prod_cities entries.

    Fixes the issue where 329 city_sources are 'inactive' despite having active prod_cities.
    """
    valid, error = check_admin_key()
    if not valid:
        return error

    try:
        conn = permitdb.get_connection()

        # First: activate city_sources where there's a matching active prod_city
        conn.execute("""
            UPDATE city_sources SET status='active'
            WHERE status='inactive'
            AND source_key IN (SELECT source_id FROM prod_cities WHERE status='active')
        """)
        # Can't get rowcount reliably from all db backends, so we'll count after

        # Count how many are now active vs inactive
        active_result = conn.execute("SELECT COUNT(*) as cnt FROM city_sources WHERE status='active'").fetchone()
        inactive_result = conn.execute("SELECT COUNT(*) as cnt FROM city_sources WHERE status='inactive'").fetchone()

        active_count = active_result['cnt'] if isinstance(active_result, dict) else active_result[0]
        inactive_count = inactive_result['cnt'] if isinstance(inactive_result, dict) else inactive_result[0]

        conn.commit()

        return jsonify({
            'status': 'success',
            'city_sources_active': active_count,
            'city_sources_inactive': inactive_count,
            'message': 'Activated city_sources matching active prod_cities'
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/reactivate-from-configs', methods=['POST'])
def admin_reactivate_from_configs():
    """V84: Reactivate city_sources based on active configs in city_configs.py.

    This fixes the issue where sources were mass-deactivated by V35 but have
    valid active configs. It reactivates sources where:
    1. The source_key matches a key in CITY_REGISTRY or BULK_SOURCES
    2. The config has active=True (or no active field, defaulting to True)
    """
    valid, error = check_admin_key()
    if not valid:
        return error

    try:
        from city_configs import CITY_REGISTRY, BULK_SOURCES

        # Build set of active config keys
        active_config_keys = set()
        for key, cfg in CITY_REGISTRY.items():
            if cfg.get('active', True):  # Default True if not specified
                active_config_keys.add(key)
        for key, cfg in BULK_SOURCES.items():
            if cfg.get('active', True):
                active_config_keys.add(key)

        conn = permitdb.get_connection()

        # Get current inactive sources
        inactive_sources = conn.execute("""
            SELECT source_key FROM city_sources WHERE status = 'inactive'
        """).fetchall()
        inactive_keys = {r['source_key'] for r in inactive_sources}

        # Find which ones should be reactivated
        to_reactivate = inactive_keys & active_config_keys

        if to_reactivate:
            # Reactivate them
            placeholders = ','.join(['?' for _ in to_reactivate])
            conn.execute(f"""
                UPDATE city_sources
                SET status = 'active',
                    last_failure_reason = 'v84_reactivated_from_config',
                    consecutive_failures = 0
                WHERE source_key IN ({placeholders})
            """, list(to_reactivate))
            conn.commit()

        # Get final counts
        active_count = conn.execute("SELECT COUNT(*) as cnt FROM city_sources WHERE status='active'").fetchone()
        inactive_count = conn.execute("SELECT COUNT(*) as cnt FROM city_sources WHERE status='inactive'").fetchone()

        return jsonify({
            'status': 'success',
            'reactivated': len(to_reactivate),
            'active_configs_count': len(active_config_keys),
            'city_sources_active': active_count['cnt'] if isinstance(active_count, dict) else active_count[0],
            'city_sources_inactive': inactive_count['cnt'] if isinstance(inactive_count, dict) else inactive_count[0],
            'message': f'Reactivated {len(to_reactivate)} sources from active configs'
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/reset-failures', methods=['POST'])
def admin_reset_failures():
    """V72: Reset consecutive_failures for a city or all cities.

    POST body: {"city_slug": "kansas-city"} to reset one city
    POST body: {} or {"city_slug": "all"} to reset all cities
    """
    valid, error = check_admin_key()
    if not valid:
        return error

    try:
        data = request.get_json() or {}
        city_slug = data.get('city_slug', 'all')

        conn = permitdb.get_connection()

        if city_slug == 'all':
            conn.execute("UPDATE prod_cities SET consecutive_failures=0, consecutive_no_new=0")
            result = conn.execute("SELECT COUNT(*) as cnt FROM prod_cities").fetchone()
            count = result['cnt'] if isinstance(result, dict) else result[0]
            conn.commit()
            return jsonify({
                'status': 'success',
                'message': f'Reset consecutive_failures for all {count} cities'
            })
        else:
            conn.execute(
                "UPDATE prod_cities SET consecutive_failures=0, consecutive_no_new=0 WHERE city_slug=?",
                (city_slug,)
            )
            conn.commit()
            return jsonify({
                'status': 'success',
                'message': f'Reset consecutive_failures for {city_slug}'
            })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


def fix_known_broken_configs():
    """V75: Fix known broken city_sources configs via SQL UPDATE statements.

    This function runs SQL updates against the SQLite database to fix:
    1. High-failure cities (reset consecutive_failures, reactivate)
    2. Platform mismatches (e.g., Rochester listed as socrata but actually accela)
    3. Slug/key mismatches between prod_cities and city_sources

    IMPORTANT: Python dict changes (BULK_SOURCES, CITY_REGISTRY) do NOT affect
    runtime behavior. The collector reads from city_sources table in SQLite.
    """
    try:
        conn = permitdb.get_connection()
        fixes_applied = []

        # =================================================================
        # FIX 1: Reset high-failure cities in city_sources
        # =================================================================
        high_failure_cities = [
            'san_antonio', 'pittsburgh', 'fort_worth', 'washington_dc',
            'atlanta', 'bloomington_in', 'honolulu', 'kansas_city', 'round_rock'
        ]

        for city_key in high_failure_cities:
            result = conn.execute(
                "SELECT source_key FROM city_sources WHERE source_key = ?",
                (city_key,)
            ).fetchone()
            if result:
                conn.execute("""
                    UPDATE city_sources
                    SET consecutive_failures = 0,
                        last_failure_reason = NULL,
                        status = 'active'
                    WHERE source_key = ?
                """, (city_key,))
                fixes_applied.append(f"Reset failures for {city_key} in city_sources")

        # =================================================================
        # FIX 2: Rochester - update to Accela platform
        # =================================================================
        # Rochester NY uses Accela, not Socrata. Update both city_sources and prod_cities.
        conn.execute("""
            UPDATE city_sources
            SET platform = 'accela',
                endpoint = 'https://aca-prod.accela.com/ROCHESTER/Cap/CapHome.aspx?module=Building&TabName=Building',
                consecutive_failures = 0,
                last_failure_reason = NULL,
                status = 'active'
            WHERE source_key = 'rochester_ny'
        """)
        fixes_applied.append("Updated rochester_ny to Accela platform in city_sources")

        # Also update prod_cities source_type
        conn.execute("""
            UPDATE prod_cities
            SET source_type = 'accela'
            WHERE city_slug = 'rochester' AND source_type = 'socrata'
        """)
        fixes_applied.append("Updated rochester source_type to accela in prod_cities")

        # =================================================================
        # FIX 2b: V76 - Fix platform mismatches for 3 major cities
        # =================================================================
        # These cities had wrong platform labels causing the wrong fetcher to be used.
        # fort_worth: endpoint is ArcGIS FeatureServer, not Socrata
        conn.execute("""
            UPDATE city_sources
            SET platform = 'arcgis'
            WHERE source_key = 'fort_worth' AND platform != 'arcgis'
        """)
        fixes_applied.append("Fixed fort_worth platform to arcgis")

        # san_antonio: endpoint is CKAN API, not Socrata
        conn.execute("""
            UPDATE city_sources
            SET platform = 'ckan'
            WHERE source_key = 'san_antonio' AND platform != 'ckan'
        """)
        fixes_applied.append("Fixed san_antonio platform to ckan")

        # washington_dc: endpoint is ArcGIS FeatureServer, not Socrata
        conn.execute("""
            UPDATE city_sources
            SET platform = 'arcgis'
            WHERE source_key = 'washington_dc' AND platform != 'arcgis'
        """)
        fixes_applied.append("Fixed washington_dc platform to arcgis")

        # =================================================================
        # FIX 2c: V76 - Sync prod_cities source_type for these 3 cities
        # =================================================================
        # Note: prod_cities uses hyphens, city_sources uses underscores
        conn.execute("UPDATE prod_cities SET source_type = 'arcgis' WHERE city_slug = 'fort-worth'")
        conn.execute("UPDATE prod_cities SET source_type = 'ckan' WHERE city_slug = 'san-antonio'")
        conn.execute("UPDATE prod_cities SET source_type = 'arcgis' WHERE city_slug = 'washington-dc'")
        fixes_applied.append("Synced prod_cities source_type for fort-worth, san-antonio, washington-dc")

        # =================================================================
        # FIX 3: Ensure slug/key mappings work
        # =================================================================
        # For cities where prod_cities uses hyphen (kansas-city) but city_sources
        # uses underscore (kansas_city), ensure there's a covers_cities mapping
        # or rename the source_key. For now, add covers_cities entries.

        # Check if kansas_city exists in city_sources
        kc_result = conn.execute(
            "SELECT source_key, covers_cities FROM city_sources WHERE source_key = 'kansas_city'"
        ).fetchone()
        if kc_result:
            # Add kansas-city to covers_cities if not already there
            covers = kc_result[1] if kc_result[1] else ''
            if 'kansas-city' not in covers:
                new_covers = f"{covers},kansas-city" if covers else "kansas-city"
                conn.execute(
                    "UPDATE city_sources SET covers_cities = ? WHERE source_key = 'kansas_city'",
                    (new_covers,)
                )
                fixes_applied.append("Added kansas-city to covers_cities for kansas_city")

        # =================================================================
        # FIX 4: Reset all consecutive_failures in prod_cities for affected cities
        # =================================================================
        affected_slugs = [
            'san-antonio', 'pittsburgh', 'fort-worth', 'washington-dc',
            'atlanta', 'bloomington-in', 'honolulu', 'kansas-city', 'round-rock',
            'rochester', 'milwaukee', 'indianapolis', 'oklahoma-city'
        ]
        conn.execute(f"""
            UPDATE prod_cities
            SET consecutive_failures = 0, consecutive_no_new = 0
            WHERE city_slug IN ({','.join('?' for _ in affected_slugs)})
        """, affected_slugs)
        fixes_applied.append(f"Reset failures in prod_cities for {len(affected_slugs)} cities")

        conn.commit()

        # =================================================================
        # FIX 5: V76 - Run platform audit with auto-fix
        # =================================================================
        # This catches any remaining platform/endpoint mismatches we didn't
        # explicitly handle above
        try:
            audit_result = audit_platform_mismatches(auto_fix=True)
            if audit_result.get('auto_fixed'):
                fixes_applied.append(f"Auto-fixed {len(audit_result['auto_fixed'])} platform mismatches: {audit_result['auto_fixed']}")
        except Exception as audit_err:
            print(f"[V76] Platform audit error (non-fatal): {audit_err}")

        print(f"[V76] fix_known_broken_configs applied {len(fixes_applied)} fixes:")
        for fix in fixes_applied:
            print(f"  - {fix}")

        return fixes_applied

    except Exception as e:
        import traceback
        print(f"[V75] fix_known_broken_configs error: {e}")
        traceback.print_exc()
        return [f"ERROR: {str(e)}"]


@app.route('/api/admin/fix-broken-configs', methods=['POST'])
def admin_fix_broken_configs():
    """V75: Apply known fixes to broken city_sources configs.

    This endpoint runs SQL UPDATE statements to fix:
    - High-failure cities (reset failures, reactivate)
    - Platform mismatches (e.g., Rochester socrata -> accela)
    - Slug/key mismatches

    POST body: {} (no parameters needed)
    """
    valid, error = check_admin_key()
    if not valid:
        return error

    try:
        fixes = fix_known_broken_configs()
        return jsonify({
            'status': 'success',
            'fixes_applied': fixes,
            'count': len(fixes)
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


def audit_platform_mismatches(auto_fix=False):
    """V76: Audit city_sources for platform/endpoint mismatches.

    Checks if the platform field matches the endpoint URL pattern:
    - "arcgis.com" or "FeatureServer" or "MapServer" → should be "arcgis"
    - "/api/3/action/" → should be "ckan"
    - "accela.com" → should be "accela"
    - ".json" with socrata-like domain → likely "socrata"

    Returns a report of mismatches and optionally auto-fixes them.
    """
    try:
        conn = permitdb.get_connection()
        mismatches = []
        fixed = []

        rows = conn.execute("""
            SELECT source_key, platform, endpoint FROM city_sources
            WHERE endpoint IS NOT NULL AND endpoint != ''
        """).fetchall()

        for row in rows:
            source_key = row[0]
            current_platform = row[1] or ''
            endpoint = row[2] or ''
            endpoint_lower = endpoint.lower()

            detected_platform = None

            # Detect platform from endpoint URL
            if 'arcgis.com' in endpoint_lower or 'featureserver' in endpoint_lower or 'mapserver' in endpoint_lower:
                detected_platform = 'arcgis'
            elif '/api/3/action/' in endpoint_lower:
                detected_platform = 'ckan'
            elif 'accela.com' in endpoint_lower:
                detected_platform = 'accela'
            elif endpoint_lower.endswith('.json') and '.gov' in endpoint_lower:
                detected_platform = 'socrata'

            # Check for mismatch
            if detected_platform and current_platform != detected_platform:
                mismatch = {
                    'source_key': source_key,
                    'current_platform': current_platform,
                    'detected_platform': detected_platform,
                    'endpoint': endpoint[:80] + '...' if len(endpoint) > 80 else endpoint
                }
                mismatches.append(mismatch)

                if auto_fix:
                    conn.execute(
                        "UPDATE city_sources SET platform = ? WHERE source_key = ?",
                        (detected_platform, source_key)
                    )
                    fixed.append(source_key)

        if auto_fix and fixed:
            conn.commit()

        return {
            'total_checked': len(rows),
            'mismatches_found': len(mismatches),
            'mismatches': mismatches,
            'auto_fixed': fixed if auto_fix else []
        }

    except Exception as e:
        import traceback
        traceback.print_exc()
        return {'error': str(e)}


@app.route('/api/admin/audit-platforms', methods=['POST'])
def admin_audit_platforms():
    """V76: Audit city_sources for platform/endpoint mismatches.

    POST body:
    {
        "auto_fix": true  // optional, default false - automatically fix mismatches
    }
    """
    valid, error = check_admin_key()
    if not valid:
        return error

    try:
        data = request.get_json() or {}
        auto_fix = data.get('auto_fix', False)

        report = audit_platform_mismatches(auto_fix=auto_fix)
        return jsonify({
            'status': 'success',
            **report
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/update-source', methods=['POST'])
def admin_update_source():
    """V75: Update a city_sources row via SQL.

    This endpoint allows runtime updates to city_sources without redeploying.
    Useful for fixing broken configs, updating endpoints, resetting failures, etc.

    POST body:
    {
        "source_key": "kansas_city",
        "updates": {
            "endpoint": "https://data.kcmo.org/resource/NEW_ID.json",
            "dataset_id": "NEW_ID",
            "status": "active",
            "consecutive_failures": 0,
            "last_failure_reason": null
        }
    }

    Allowed fields to update:
    - endpoint, dataset_id, platform, date_field, field_map
    - status, consecutive_failures, last_failure_reason
    - covers_cities, limit_per_page
    """
    valid, error = check_admin_key()
    if not valid:
        return error

    try:
        data = request.get_json() or {}
        source_key = data.get('source_key')
        updates = data.get('updates', {})

        if not source_key:
            return jsonify({'error': 'source_key is required'}), 400

        if not updates:
            return jsonify({'error': 'updates object is required'}), 400

        # Whitelist of allowed fields to update
        allowed_fields = {
            'endpoint', 'dataset_id', 'platform', 'date_field', 'field_map',
            'status', 'consecutive_failures', 'last_failure_reason',
            'covers_cities', 'limit_per_page', 'name', 'state'
        }

        # Filter to only allowed fields
        filtered_updates = {k: v for k, v in updates.items() if k in allowed_fields}
        if not filtered_updates:
            return jsonify({'error': f'No valid fields to update. Allowed: {allowed_fields}'}), 400

        conn = permitdb.get_connection()

        # Check if source exists
        existing = conn.execute(
            "SELECT source_key FROM city_sources WHERE source_key = ?",
            (source_key,)
        ).fetchone()

        if not existing:
            return jsonify({'error': f'Source {source_key} not found in city_sources'}), 404

        # Build UPDATE query
        set_clauses = []
        values = []
        for field, value in filtered_updates.items():
            set_clauses.append(f"{field} = ?")
            # Handle None/null for last_failure_reason
            values.append(value if value is not None else None)

        values.append(source_key)

        query = f"UPDATE city_sources SET {', '.join(set_clauses)} WHERE source_key = ?"
        conn.execute(query, values)
        conn.commit()

        return jsonify({
            'status': 'success',
            'source_key': source_key,
            'fields_updated': list(filtered_updates.keys())
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/cleanup-prod-cities', methods=['POST'])
def admin_cleanup_prod_cities():
    """V75: Clean up inflated prod_cities entries.

    This removes or deactivates prod_cities entries that:
    1. Have never been collected (last_collection IS NULL)
    2. Have 0 total_permits
    3. Don't have a matching active city_sources entry

    POST body:
    {
        "mode": "deactivate"  // or "delete" (default: deactivate)
    }
    """
    valid, error = check_admin_key()
    if not valid:
        return error

    try:
        data = request.get_json() or {}
        mode = data.get('mode', 'deactivate')

        conn = permitdb.get_connection()

        if mode == 'delete':
            # Delete entries that have never collected and have 0 permits
            result = conn.execute("""
                DELETE FROM prod_cities
                WHERE last_collection IS NULL
                AND total_permits = 0
                AND source_id NOT IN (SELECT source_key FROM city_sources WHERE status='active')
            """)
            action = 'deleted'
        else:
            # V76: Use 'paused' instead of 'inactive' — CHECK constraint only allows
            # 'active', 'paused', 'failed', 'pending'
            conn.execute("""
                UPDATE prod_cities SET status = 'paused'
                WHERE last_collection IS NULL
                AND total_permits = 0
                AND status = 'active'
                AND source_id NOT IN (SELECT source_key FROM city_sources WHERE status='active')
            """)
            action = 'paused'

        # Get counts
        active_count = conn.execute(
            "SELECT COUNT(*) FROM prod_cities WHERE status='active'"
        ).fetchone()[0]
        paused_count = conn.execute(
            "SELECT COUNT(*) FROM prod_cities WHERE status='paused'"
        ).fetchone()[0]
        no_data_count = conn.execute(
            "SELECT COUNT(*) FROM prod_cities WHERE status='active' AND total_permits=0"
        ).fetchone()[0]

        conn.commit()

        return jsonify({
            'status': 'success',
            'action': action,
            'prod_cities_active': active_count,
            'prod_cities_paused': paused_count,
            'no_data_count': no_data_count
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/activate-paused-cities', methods=['POST'])
def admin_activate_paused_cities():
    """V77: Bulk-activate paused cities that have valid CITY_REGISTRY configs.

    This activates the 529+ cities that were synced from CITY_REGISTRY but
    inserted as status='paused' and never collected.

    For each paused city:
    1. Check if source_id has valid config in city_sources (active) OR in CITY_REGISTRY (active=True)
    2. If yes: activate in prod_cities AND activate matching city_sources entry
    3. Return count of activated cities

    POST body: {} (no parameters needed)
    """
    valid, error = check_admin_key()
    if not valid:
        return error

    try:
        from city_configs import CITY_REGISTRY

        conn = permitdb.get_connection()
        activated = []
        skipped = []
        city_sources_activated = []

        # Get all paused cities
        paused_cities = conn.execute("""
            SELECT city_slug, source_id, city, state FROM prod_cities WHERE status = 'paused'
        """).fetchall()

        for row in paused_cities:
            city_slug = row[0]
            source_id = row[1]
            city_name = row[2]
            state = row[3]

            # Check if source_id has valid config
            has_valid_config = False

            # Check 1: city_sources table
            cs_row = conn.execute(
                "SELECT source_key, status FROM city_sources WHERE source_key = ?",
                (source_id,)
            ).fetchone()

            if cs_row:
                has_valid_config = True
                # Also activate city_sources if it's inactive
                if cs_row[1] != 'active':
                    conn.execute(
                        "UPDATE city_sources SET status = 'active' WHERE source_key = ?",
                        (source_id,)
                    )
                    city_sources_activated.append(source_id)

            # Check 2: CITY_REGISTRY dict (if not found in city_sources)
            if not has_valid_config and source_id in CITY_REGISTRY:
                if CITY_REGISTRY[source_id].get('active', False):
                    has_valid_config = True

            # Check 3: Try hyphen-to-underscore conversion
            if not has_valid_config:
                underscore_id = source_id.replace('-', '_')
                if underscore_id in CITY_REGISTRY:
                    if CITY_REGISTRY[underscore_id].get('active', False):
                        has_valid_config = True

            if has_valid_config:
                # Activate in prod_cities
                conn.execute("""
                    UPDATE prod_cities SET status = 'active', notes = 'V77: Bulk activated from paused'
                    WHERE city_slug = ?
                """, (city_slug,))
                activated.append({'city_slug': city_slug, 'source_id': source_id})
            else:
                skipped.append({'city_slug': city_slug, 'source_id': source_id, 'reason': 'no valid config'})

        conn.commit()

        # Get final counts
        active_count = conn.execute("SELECT COUNT(*) FROM prod_cities WHERE status='active'").fetchone()[0]
        paused_count = conn.execute("SELECT COUNT(*) FROM prod_cities WHERE status='paused'").fetchone()[0]

        return jsonify({
            'status': 'success',
            'activated_count': len(activated),
            'skipped_count': len(skipped),
            'city_sources_activated': len(city_sources_activated),
            'prod_cities_active': active_count,
            'prod_cities_paused': paused_count,
            'activated': activated[:50],  # Limit response size
            'skipped': skipped[:50]
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/trigger-search', methods=['POST'])
def admin_trigger_search():
    """V12.54: Manually trigger search for a city or county."""
    admin_key = request.headers.get('X-Admin-Key') or request.args.get('key')
    expected = os.environ.get('ADMIN_KEY', 'permitgrab-reset-2026')
    if admin_key != expected:
        return jsonify({"error": "unauthorized"}), 401
    data = request.get_json() or {}
    slug = data.get('slug')
    fips = data.get('fips')
    try:
        if fips:
            from city_source_db import update_county_status
            update_county_status(fips, 'not_started')
            return jsonify({"status": "ok", "message": f"County {fips} reset to not_started"})
        elif slug:
            from city_source_db import update_city_status
            update_city_status(slug, 'not_started')
            return jsonify({"status": "ok", "message": f"City {slug} reset to not_started"})
        return jsonify({"error": "provide slug or fips"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/admin/traffic', methods=['GET'])
def admin_traffic():
    """V12.59b: Query persistent page view data from PostgreSQL."""
    admin_key = request.headers.get('X-Admin-Key', '')
    if admin_key != os.environ.get('ADMIN_KEY', ''):
        return jsonify({'error': 'Unauthorized'}), 401

    try:
        import psycopg2
        conn = psycopg2.connect(os.environ.get('DATABASE_URL', ''))
        cur = conn.cursor()

        hours = int(request.args.get('hours', 24))

        # Total page views
        cur.execute("SELECT COUNT(*) FROM page_views WHERE created_at > NOW() - INTERVAL '%s hours'", (hours,))
        total_views = cur.fetchone()[0]

        # Unique IPs (proxy for unique visitors)
        cur.execute("SELECT COUNT(DISTINCT ip_address) FROM page_views WHERE created_at > NOW() - INTERVAL '%s hours'", (hours,))
        unique_ips = cur.fetchone()[0]

        # Views by path
        cur.execute("""
            SELECT path, COUNT(*) as hits
            FROM page_views WHERE created_at > NOW() - INTERVAL '%s hours'
            GROUP BY path ORDER BY hits DESC LIMIT 20
        """, (hours,))
        paths = [{'path': r[0], 'hits': r[1]} for r in cur.fetchall()]

        # Views by user agent type
        cur.execute("""
            SELECT
                CASE
                    WHEN user_agent ILIKE '%%googlebot%%' THEN 'Googlebot'
                    WHEN user_agent ILIKE '%%bingbot%%' THEN 'Bingbot'
                    WHEN user_agent ILIKE '%%curl%%' THEN 'curl'
                    WHEN user_agent ILIKE '%%python%%' THEN 'Python'
                    WHEN user_agent ILIKE '%%chrome%%' THEN 'Chrome'
                    WHEN user_agent ILIKE '%%firefox%%' THEN 'Firefox'
                    WHEN user_agent ILIKE '%%safari%%' AND user_agent NOT ILIKE '%%chrome%%' THEN 'Safari'
                    WHEN user_agent ILIKE '%%bot%%' OR user_agent ILIKE '%%spider%%' OR user_agent ILIKE '%%crawl%%' THEN 'Other Bot'
                    ELSE 'Other'
                END as agent_type,
                COUNT(*) as hits
            FROM page_views WHERE created_at > NOW() - INTERVAL '%s hours'
            GROUP BY agent_type ORDER BY hits DESC
        """, (hours,))
        agents = [{'agent': r[0], 'hits': r[1]} for r in cur.fetchall()]

        # Recent views (last 10)
        cur.execute("""
            SELECT path, user_agent, ip_address, created_at::text
            FROM page_views ORDER BY created_at DESC LIMIT 10
        """)
        recent = [{'path': r[0], 'user_agent': r[1][:80] if r[1] else '', 'ip': r[2], 'time': r[3]} for r in cur.fetchall()]

        # Hourly breakdown (last 24h)
        cur.execute("""
            SELECT date_trunc('hour', created_at)::text as hour, COUNT(*) as hits
            FROM page_views WHERE created_at > NOW() - INTERVAL '24 hours'
            GROUP BY hour ORDER BY hour
        """)
        hourly = [{'hour': r[0], 'hits': r[1]} for r in cur.fetchall()]

        cur.close()
        conn.close()

        return jsonify({
            'period_hours': hours,
            'total_views': total_views,
            'unique_visitors': unique_ips,
            'paths': paths,
            'user_agents': agents,
            'recent': recent,
            'hourly': hourly
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ===========================
# DATABASE SETUP (V7)
# ===========================
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.exc import IntegrityError

# Configure PostgreSQL database (Render provides DATABASE_URL)
database_url = os.environ.get('DATABASE_URL', '')
# Render uses 'postgres://' but SQLAlchemy needs 'postgresql://'
if database_url.startswith('postgres://'):
    database_url = database_url.replace('postgres://', 'postgresql://', 1)

if database_url:
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
    print(f"[Database] Using PostgreSQL database")
else:
    # Fallback to SQLite for local development
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///permitgrab.db'
    print(f"[Database] Using SQLite (local development)")

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
# V11 Hotfix: pool_pre_ping verifies connections, pool_recycle prevents stale connections
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_pre_ping': True,
    'pool_recycle': 300,
}

# V12.24: Google Analytics and Search Console from env vars
app.config['GOOGLE_ANALYTICS_ID'] = os.environ.get('GOOGLE_ANALYTICS_ID', '')
app.config['GOOGLE_SITE_VERIFICATION'] = os.environ.get('GOOGLE_SITE_VERIFICATION', '')
# V30: Remarketing pixel IDs — set these env vars on Render to activate
app.config['GOOGLE_ADS_ID'] = os.environ.get('GOOGLE_ADS_ID', '')  # e.g. AW-XXXXXXXXX
app.config['META_PIXEL_ID'] = os.environ.get('META_PIXEL_ID', '')   # Facebook/Meta pixel ID

db = SQLAlchemy(app)


class User(db.Model):
    """User model for PostgreSQL storage (V7 - replaces JSON file)."""
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    name = db.Column(db.String(255), nullable=False, default='')
    password_hash = db.Column(db.String(255), nullable=False)
    plan = db.Column(db.String(50), default='free')
    city = db.Column(db.String(255))
    trade = db.Column(db.String(255))
    daily_alerts = db.Column(db.Boolean, default=False)
    onboarding_completed = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    stripe_customer_id = db.Column(db.String(255))
    stripe_subscription_id = db.Column(db.String(255))
    stripe_subscription_status = db.Column(db.String(50))
    # V12.26: Competitor Watch - JSON list of competitor names to track
    watched_competitors = db.Column(db.Text, default='[]')
    # V12.26: Weekly digest city subscriptions - JSON list of city names
    digest_cities = db.Column(db.Text, default='[]')

    # V12.53: Email system fields
    email_verified = db.Column(db.Boolean, default=False)
    email_verified_at = db.Column(db.DateTime)
    email_verification_token = db.Column(db.String(64))
    unsubscribe_token = db.Column(db.String(64))
    digest_active = db.Column(db.Boolean, default=True)  # Can receive digest emails
    last_login_at = db.Column(db.DateTime)
    last_digest_sent_at = db.Column(db.DateTime)
    last_reengagement_sent_at = db.Column(db.DateTime)
    # Trial tracking
    trial_started_at = db.Column(db.DateTime)
    trial_end_date = db.Column(db.DateTime)
    trial_midpoint_sent = db.Column(db.Boolean, default=False)
    trial_ending_sent = db.Column(db.Boolean, default=False)
    trial_expired_sent = db.Column(db.Boolean, default=False)
    # Welcome email tracking
    welcome_email_sent = db.Column(db.Boolean, default=False)

    def to_dict(self):
        """Convert to dictionary for JSON responses."""
        return {
            'id': self.id,
            'email': self.email,
            'name': self.name,
            'plan': self.plan,
            'city': self.city,
            'trade': self.trade,
            'daily_alerts': self.daily_alerts,
            'onboarding_completed': self.onboarding_completed,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'stripe_customer_id': self.stripe_customer_id,
            'stripe_subscription_id': self.stripe_subscription_id,
            'stripe_subscription_status': self.stripe_subscription_status,
            # V12.26: Competitor Watch and Digest Cities
            'watched_competitors': json.loads(self.watched_competitors or '[]'),
            'digest_cities': json.loads(self.digest_cities or '[]'),
            # V12.53: Email system fields
            'email_verified': self.email_verified,
            'digest_active': self.digest_active,
            'trial_end_date': self.trial_end_date.isoformat() if self.trial_end_date else None,
        }

    def is_pro(self):
        """Check if user has Pro access (paid or trial)."""
        if self.plan in ('professional', 'pro', 'enterprise'):
            # Check if trial has expired
            if self.trial_end_date and datetime.utcnow() > self.trial_end_date:
                return False
            return True
        return False

    def days_until_trial_ends(self):
        """Get days remaining in trial, or None if not on trial."""
        if not self.trial_end_date:
            return None
        delta = self.trial_end_date - datetime.utcnow()
        return max(0, delta.days)


# Create tables on startup
with app.app_context():
    db.create_all()

    # V12.57: Auto-migrate missing columns — db.create_all() only creates new tables,
    # it won't add columns to existing tables. This fixes the daily digest crash
    # caused by users.watched_competitors not existing in Postgres.
    migration_columns = [
        ("watched_competitors", "TEXT DEFAULT '[]'"),
        ("digest_cities", "TEXT DEFAULT '[]'"),
        ("email_verified", "BOOLEAN DEFAULT FALSE"),
        ("email_verified_at", "TIMESTAMP"),
        ("email_verification_token", "VARCHAR(64)"),
        ("unsubscribe_token", "VARCHAR(64)"),
        ("digest_active", "BOOLEAN DEFAULT TRUE"),
        ("last_login_at", "TIMESTAMP"),
        ("last_digest_sent_at", "TIMESTAMP"),
        ("last_reengagement_sent_at", "TIMESTAMP"),
        ("trial_started_at", "TIMESTAMP"),
        ("trial_end_date", "TIMESTAMP"),
        ("trial_midpoint_sent", "BOOLEAN DEFAULT FALSE"),
        ("trial_ending_sent", "BOOLEAN DEFAULT FALSE"),
        ("trial_expired_sent", "BOOLEAN DEFAULT FALSE"),
        ("welcome_email_sent", "BOOLEAN DEFAULT FALSE"),
    ]
    try:
        for col_name, col_type in migration_columns:
            db.session.execute(db.text(
                f"ALTER TABLE users ADD COLUMN IF NOT EXISTS {col_name} {col_type}"
            ))
        db.session.commit()
        print("[Database] Tables created/verified, columns migrated")
    except Exception as e:
        db.session.rollback()
        print(f"[Database] Tables created, migration warning: {e}")


# Rate limiter setup
limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=["200 per day", "50 per hour"],
    storage_uri="memory://",
)

# Use Render persistent disk if available, otherwise local data directory
# Render disk is mounted at /var/data and persists across deploys
if os.path.isdir('/var/data'):
    DATA_DIR = '/var/data'
    print("[Server] Using Render persistent disk at /var/data")
else:
    DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
    print(f"[Server] Using local data directory at {DATA_DIR}")

# V11 Hotfix: Diagnostic logging for data directory
print(f"[Server] /var/data exists: {os.path.isdir('/var/data')}")
print(f"[Server] /var/data contents: {os.listdir('/var/data') if os.path.isdir('/var/data') else 'N/A'}")
print(f"[Server] DATA_DIR = {DATA_DIR}")
print(f"[Server] DATA_DIR exists: {os.path.isdir(DATA_DIR)}")
if os.path.isdir(DATA_DIR):
    print(f"[Server] DATA_DIR contents: {os.listdir(DATA_DIR)}")

# V12.1: Removed _sanitize_permits_file() - raw byte stripping corrupted JSON structure
# The correct approach is parse-then-rewrite in load_permits() using strict=False

# ============================================================================
# V12.32: AUTO-DISCOVER CITIES FROM PERMIT DATA
# ============================================================================
# Bulk sources create permits for cities not in CITY_REGISTRY. This module
# scans permit data to discover all cities and enables routing for them.

import re
_discovered_cities_cache = {}
_discovered_cities_timestamp = 0

def slugify_for_lookup(city_name, state):
    """Generate a URL slug from city name and state."""
    if not city_name:
        return None
    name = city_name.strip()
    # Remove common suffixes
    for suffix in [" City", " Township", " Borough", " Town", " Village"]:
        if name.endswith(suffix):
            name = name[:-len(suffix)]
    slug = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')
    return f"{slug}-{state.lower()}" if slug else None


def normalize_city_key(city_name, state):
    """V12.36: Create normalized key for deduplication (case-insensitive, trimmed)."""
    if not city_name or not state:
        return None
    # Normalize: lowercase, strip whitespace, collapse multiple spaces
    name = ' '.join(city_name.lower().split())
    return (name, state.upper())


def discover_cities_from_permits():
    """
    V12.32: Scan permit data to discover all cities including bulk-sourced ones.
    Returns dict of {slug: city_config} for all discovered cities.
    Caches results for 5 minutes to avoid repeated scans.
    V12.36: Fixed deduplication - merges cities by normalized (name, state).
    """
    global _discovered_cities_cache, _discovered_cities_timestamp

    # Check cache validity (5 minute TTL)
    cache_age = time.time() - _discovered_cities_timestamp
    if _discovered_cities_cache and cache_age < 300:
        return _discovered_cities_cache

    print("[V12.36] Discovering cities from permit data (with dedup)...")

    # V12.36: Track by normalized (name, state) key to prevent duplicates
    all_cities = {}
    seen_normalized = {}  # Maps normalized_key -> slug for dedup

    # Start with explicit configs from CITY_REGISTRY
    for key, config in CITY_REGISTRY.items():
        if config.get('active', False):
            slug = config.get('slug', key)
            name = config.get('name', key)
            state = config.get('state', '')

            # Track by normalized key for dedup
            norm_key = normalize_city_key(name, state)
            if norm_key and norm_key not in seen_normalized:
                seen_normalized[norm_key] = slug
                all_cities[slug] = {
                    'key': key,
                    'name': name,
                    'state': state,
                    'slug': slug,
                    'configured': True,
                    'active': True,
                }

    # Scan permits for additional cities
    permits_path = os.path.join(DATA_DIR, 'permits.json')
    if os.path.exists(permits_path):
        try:
            with open(permits_path) as f:
                permits = json.load(f, strict=False)

            # Find unique (city, state) pairs from permits
            permit_cities = set()
            for permit in permits:
                city_name = permit.get('city', '').strip()
                state = permit.get('state', '').strip()
                if city_name and state:
                    permit_cities.add((city_name, state))

            # Add cities not already tracked (by normalized key)
            added_count = 0
            for city_name, state in permit_cities:
                norm_key = normalize_city_key(city_name, state)
                if norm_key and norm_key not in seen_normalized:
                    slug = slugify_for_lookup(city_name, state)
                    if slug:
                        seen_normalized[norm_key] = slug
                        all_cities[slug] = {
                            'key': slug,
                            'name': city_name,
                            'state': state,
                            'slug': slug,
                            'configured': False,  # Auto-discovered from bulk permit data
                            'active': False,  # V31: Not actively pulled — just has historical permit data
                            'source_bulk': True,
                        }
                        added_count += 1

            print(f"[V12.36] Found {len(all_cities)} unique cities "
                  f"({len(permit_cities)} in permits, {added_count} new)")

        except Exception as e:
            print(f"[V12.36] Error scanning permits: {e}")

    _discovered_cities_cache = all_cities
    _discovered_cities_timestamp = time.time()
    return all_cities


def get_city_by_slug_auto(slug):
    """
    V12.32: Look up city config by slug, checking CITY_REGISTRY,
    auto-discovered cities from bulk source data, and prod_cities.
    V32: Added prod_cities fallback for bulk source cities whose slugs
    include state suffixes (e.g., 'lakewood-nj' for URL 'lakewood').
    Returns (city_key, city_config) or (None, None) if not found.
    """
    # First try explicit registry (faster, has full config)
    city_key, city_config = get_city_by_slug(slug)
    if city_config:
        return city_key, city_config

    # Try auto-discovered cities
    discovered = discover_cities_from_permits()
    if slug in discovered:
        city_info = discovered[slug]
        # Build a minimal config compatible with existing code
        return city_info['key'], {
            'name': city_info['name'],
            'state': city_info['state'],
            'slug': slug,
            'active': True,
            'auto_discovered': True,
        }

    # V32: Check prod_cities table (handles bulk source slugs like 'lakewood-nj')
    try:
        city_name, state, prod_slug = permitdb.lookup_prod_city_by_slug(slug)
        if city_name:
            return prod_slug, {
                'name': city_name,
                'state': state,
                'slug': prod_slug,
                'active': True,
                'auto_discovered': True,
                'from_prod_cities': True,
            }
    except Exception as e:
        print(f"[V32] Error looking up prod_city for slug '{slug}': {e}")

    return None, None


def get_cities_by_state_auto(state_abbrev):
    """
    V12.32: Get all cities for a state, including auto-discovered ones.
    Returns list of city info dicts.
    """
    state_abbrev = state_abbrev.upper()
    discovered = discover_cities_from_permits()

    cities = []
    for slug, info in discovered.items():
        if info.get('state', '').upper() == state_abbrev:
            cities.append(info)

    return sorted(cities, key=lambda x: x.get('name', ''))


def get_total_city_count_auto():
    """V15/V31: Get total count of actively collected cities.

    V31: Only counts cities with live data collection (prod_cities status='active').
    Does NOT include historical bulk-source cities that aren't being actively pulled.
    Falls back to get_cities_with_data() heuristics if prod_cities is empty.
    """
    try:
        # V15: Try prod_cities first (collector redesign)
        if permitdb.prod_cities_table_exists():
            count = permitdb.get_prod_city_count()
            if count > 0:
                return count

        # Fall back to heuristics (pre-V15 behavior)
        filtered_cities = get_cities_with_data()
        return len(filtered_cities)
    except Exception as e:
        print(f"[V15] Error getting city count: {e}")
        return 160  # Fallback


# V12.53: DEPRECATED - subscribers now stored in User model with digest_cities field
# These constants and functions are kept for backward compatibility but not used
SUBSCRIBERS_FILE = os.path.join(DATA_DIR, 'subscribers.json')  # DEPRECATED
USERS_FILE = os.path.join(DATA_DIR, 'users.json')  # DEPRECATED - use PostgreSQL User model

# V12.12: Startup data loading state
# Track whether initial data has been loaded from disk
_initial_data_loaded = False
_collection_in_progress = False
# V16: Track last successful collection run for health monitoring
_last_collection_run = None

# V12.51: Removed V12.49 cache code (_permits_cache, _permits_cache_mtime, _permits_cache_lock)
# SQLite handles all permit storage now - no JSON file caching needed

def preload_data_from_disk():
    """V12.51: Initialize SQLite database on startup.

    V12.50 migrated from JSON files to SQLite. This function now just
    initializes the database and reports the current permit count.
    """
    global _initial_data_loaded

    permitdb.init_db()

    # V13.2: Clean up invalid date fields (e.g., Mesa permits with reviewer names)
    permitdb.cleanup_invalid_dates()

    stats = permitdb.get_permit_stats()
    print(f"[Server] V12.51: SQLite ready - {stats['total_permits']} permits, {stats['city_count']} cities")
    _initial_data_loaded = True

def is_data_loading():
    """V12.51: Check if we're in a loading state (no data available)."""
    if _initial_data_loaded:
        return False
    # Check SQLite for data
    try:
        stats = permitdb.get_permit_stats()
        return stats['total_permits'] == 0
    except Exception:
        return True


def sync_city_registry_to_prod_cities():
    """V97: Complete sync — CITY_REGISTRY + BULK_SOURCES → prod_cities + city_sources.

    V97 FIXES (replaces broken V95/V96 logic):
    - Builds THREE lookups upfront: by_source, by_slug, by_citystate
    - Uses config.get('slug', city_key) for slug — NOT normalize_city_slug()
    - Direct SQL INSERT instead of upsert_prod_city() which was failing silently
    - Tracks already_active to avoid unnecessary updates
    - NO DELETES: Never delete prod_cities rows.

    Runs on every startup. Must be idempotent and fast (< 30 seconds).
    """
    from city_configs import CITY_REGISTRY, BULK_SOURCES
    from city_source_db import upsert_city_source

    result = {
        'already_active': 0,
        'prod_activated': 0,
        'prod_created': 0,
        'prod_updated': 0,
        'cs_created': 0,
        'errors': 0
    }
    conn = None

    try:
        print(f"[V97] Starting registry sync...")
        conn = permitdb.get_connection()

        # =================================================================
        # STEP 1: CITY_REGISTRY → city_sources
        # =================================================================
        print(f"[V97] Phase 1: Syncing CITY_REGISTRY → city_sources...")
        for city_key, config in CITY_REGISTRY.items():
            if not config.get('active', False):
                continue
            try:
                upsert_city_source({
                    'source_key': city_key,
                    'name': config.get('name', city_key),
                    'state': config.get('state', ''),
                    'platform': config.get('platform', ''),
                    'mode': 'city',
                    'endpoint': config.get('endpoint', ''),
                    'dataset_id': config.get('dataset_id', ''),
                    'field_map': config.get('field_map', {}),
                    'date_field': config.get('date_field', ''),
                    'city_field': config.get('city_field', ''),
                    'limit_per_page': config.get('limit', 2000),
                    'status': 'active'
                })
                result['cs_created'] += 1
            except Exception as e:
                print(f"  [V97] WARN: city_sources upsert failed for {city_key}: {e}")
                result['errors'] += 1

        # =================================================================
        # STEP 2: BULK_SOURCES → city_sources
        # =================================================================
        print(f"[V97] Phase 2: Syncing BULK_SOURCES → city_sources...")
        for source_key, config in BULK_SOURCES.items():
            if not config.get('active', True):
                continue
            try:
                upsert_city_source({
                    'source_key': source_key,
                    'name': config.get('name', source_key),
                    'state': config.get('state', ''),
                    'platform': config.get('platform', ''),
                    'mode': 'bulk',
                    'endpoint': config.get('endpoint', ''),
                    'dataset_id': config.get('dataset_id', ''),
                    'field_map': config.get('field_map', {}),
                    'date_field': config.get('date_field', ''),
                    'city_field': config.get('city_field', ''),
                    'limit_per_page': config.get('limit', 50000),
                    'status': 'active'
                })
                result['cs_created'] += 1
            except Exception as e:
                print(f"  [V97] WARN: city_sources upsert failed for bulk {source_key}: {e}")
                result['errors'] += 1

        # =================================================================
        # STEP 3: CITY_REGISTRY → prod_cities (V98 FIX)
        # =================================================================
        print(f"[V97] Phase 3: Syncing CITY_REGISTRY → prod_cities...")

        # V98: Re-acquire connection — upsert_city_source() in Phase 1/2
        # closes the thread-local conn (V66 conn.close()), so the original
        # conn from line 6102 is dead by now.
        conn = permitdb.get_connection()

        # V97: Build THREE lookups upfront for fast matching
        by_source = {}
        by_slug = {}
        by_citystate = {}
        for row in conn.execute("SELECT id, city_slug, source_id, city, state, status FROM prod_cities"):
            row_dict = dict(row) if hasattr(row, 'keys') else {
                'id': row[0], 'city_slug': row[1], 'source_id': row[2],
                'city': row[3], 'state': row[4], 'status': row[5]
            }
            if row_dict['source_id']:
                by_source[row_dict['source_id']] = row_dict
            if row_dict['city_slug']:
                by_slug[row_dict['city_slug']] = row_dict
            city_lower = row_dict['city'].lower() if row_dict['city'] else ''
            state_val = row_dict['state'] or ''
            by_citystate[(city_lower, state_val)] = row_dict

        # Process each active CITY_REGISTRY entry
        for city_key, config in CITY_REGISTRY.items():
            if not config.get('active', False):
                continue

            name = config.get('name', '')
            state = config.get('state', '')
            platform = config.get('platform', '')
            # V97: Use slug from config, fallback to city_key — NOT normalize_city_slug()
            slug = config.get('slug', city_key)

            if not name or not state:
                continue

            # Match 1: by source_id (most reliable)
            if city_key in by_source:
                row = by_source[city_key]
                if row['status'] == 'active':
                    result['already_active'] += 1
                else:
                    conn.execute(
                        "UPDATE prod_cities SET status = ?, source_type = ? WHERE id = ?",
                        ('active', platform, row['id'])
                    )
                    result['prod_activated'] += 1
                continue

            # Match 2: by slug (V102: also verify state matches to prevent cross-state mislinks)
            if slug in by_slug:
                row = by_slug[slug]
                row_state = row.get('state', '')
                if row_state and state and row_state != state:
                    pass  # V102: State mismatch — don't link (e.g., long_beach_nj vs Long Beach CA)
                else:
                    conn.execute(
                        "UPDATE prod_cities SET status = ?, source_id = ?, source_type = ? WHERE id = ?",
                        ('active', city_key, platform, row['id'])
                    )
                    result['prod_activated'] += 1
                    by_source[city_key] = row  # prevent double-match
                    continue

            # Match 3: by city+state
            cs_key = (name.lower(), state)
            if cs_key in by_citystate:
                row = by_citystate[cs_key]
                # V102: Don't overwrite source_id if already set to a different active entry
                existing_source = row.get('source_id', '')
                if existing_source and existing_source != city_key and existing_source in by_source:
                    # Already linked to another source — skip to avoid overwrite
                    result['already_active'] += 1
                    continue
                conn.execute(
                    "UPDATE prod_cities SET status = ?, source_id = ?, source_type = ?, city_slug = ? WHERE id = ?",
                    ('active', city_key, platform, slug, row['id'])
                )
                result['prod_activated'] += 1
                by_source[city_key] = row
                continue

            # Match 4: INSERT new city (V97: direct SQL, not upsert_prod_city)
            try:
                conn.execute("""
                    INSERT INTO prod_cities (city, state, city_slug, source_id, source_type, status, added_by)
                    VALUES (?, ?, ?, ?, ?, 'active', 'v97_sync')
                """, (name, state, slug, city_key, platform))
                result['prod_created'] += 1
                by_source[city_key] = {'id': -1}  # prevent double-match
                by_citystate[cs_key] = {'id': -1}
            except Exception as e:
                print(f"  [V97] ERROR: Failed to insert {city_key} ({name}, {state}): {e}")
                result['errors'] += 1

        # =================================================================
        # STEP 4: Deactivate inactive CITY_REGISTRY entries (no deletes)
        # V102: Also check by slug and city+state, not just by_source
        # =================================================================
        for city_key, config in CITY_REGISTRY.items():
            if config.get('active', False):
                continue
            # V102: Find the matching prod_cities row by source_id, slug, or city+state
            row = None
            if city_key in by_source:
                row = by_source[city_key]
            else:
                slug = config.get('slug', city_key)
                if slug in by_slug:
                    row = by_slug[slug]
                else:
                    name = config.get('name', '')
                    state = config.get('state', '')
                    if name and state:
                        cs_key = (name.lower(), state)
                        if cs_key in by_citystate:
                            row = by_citystate[cs_key]
            if row and row.get('status') == 'active':
                # V103: Don't pause if the row's source_id points to a DIFFERENT active entry
                row_source = row.get('source_id', '')
                if row_source and row_source != city_key and row_source in by_source:
                    # This row is linked to another (possibly active) source — don't pause
                    continue
                conn.execute(
                    "UPDATE prod_cities SET status = 'paused' WHERE id = ?",
                    (row['id'],)
                )
                result['prod_updated'] += 1

        conn.commit()

        # V97: Log actual count for verification
        actual_active = conn.execute(
            "SELECT COUNT(*) FROM prod_cities WHERE status = 'active'"
        ).fetchone()[0]

        print(f"[V97] Sync complete: "
              f"already_active={result['already_active']}, activated={result['prod_activated']}, "
              f"created={result['prod_created']}, paused={result['prod_updated']}, errors={result['errors']} | "
              f"city_sources={result['cs_created']} | "
              f"ACTUAL ACTIVE: {actual_active}")

        return result

    except Exception as e:
        print(f"[V95] Sync error: {e}")
        import traceback
        traceback.print_exc()
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        return result
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


# ===========================
# LEAD SCORING ENGINE
# ===========================
# V7: FORCED LINEAR SPREAD - exact implementation from spec
# Output range: 40-99, guaranteed by linear mapping.

import hashlib


def calculate_lead_score(permit):
    """
    V13.1: ABSOLUTE lead scoring with WIDER SPREAD for better differentiation.
    Returns integer 0-100. No normalization across dataset.

    Score breakdown (max 100 points):
      A: Project value     0-35 pts (absolute brackets)
      B: Recency          0-30 pts (days since filed)
      C: Address quality  0-15 pts (has street number)
      D: Contact info     0-15 pts (phone/email/names)
      E: Status           0-5 pts  (issued > pending > other)
    """
    score = 0.0

    # A: Project value (0-35 pts) — ABSOLUTE brackets with wider spread
    value = 0.0
    for key in ['estimated_cost', 'project_value', 'value']:
        v = permit.get(key)
        if v is not None:
            try:
                value = float(str(v).replace('$', '').replace(',', ''))
                break
            except (ValueError, TypeError):
                pass

    if value <= 0:
        score += 0    # V13.1: Missing = 0 (creates bigger gap)
    elif value < 10000:
        score += 5
    elif value < 50000:
        score += 10
    elif value < 100000:
        score += 16
    elif value < 200000:
        score += 22
    elif value < 500000:
        score += 28
    else:
        score += 35   # $500K+ = max

    # B: Recency (0-30 pts) — V13.1: Invalid dates = 0, not default
    recency_added = False
    for key in ['filing_date', 'issued_date', 'date']:
        d = permit.get(key)
        if d:
            try:
                if isinstance(d, str):
                    # V13.1: Must start with digit to be a valid date
                    if not d[0].isdigit():
                        continue  # Skip non-date strings like "WROCCO"
                    d = datetime.strptime(d[:10], '%Y-%m-%d')
                days_old = (datetime.now() - d).days
                if days_old < 0:
                    score += 0    # Future date = bad data
                elif days_old <= 7:
                    score += 30
                elif days_old <= 30:
                    score += 24
                elif days_old <= 90:
                    score += 18
                elif days_old <= 180:
                    score += 12
                elif days_old <= 365:
                    score += 6
                else:
                    score += 0    # V13.1: Older than 1 year = 0
                recency_added = True
                break
            except (ValueError, TypeError):
                pass
    # V13.1: No valid date = 0 (not 8), creates bigger differentiation
    # recency_added stays False, score += 0 implied

    # C: Address quality (0-15 pts) — V13.1: Increased weight
    # V19: Explicitly exclude placeholder addresses from scoring
    address = str(permit.get('address', '')).strip()
    address_lower = address.lower()
    is_placeholder = (
        not address or
        address_lower in ('address not provided', 'not provided', 'n/a', 'na', 'none', 'unknown', 'tbd', '-') or
        address_lower.startswith('address not')
    )
    if is_placeholder:
        score += 0    # V19: No address = 0 points (keeps out of Best Leads)
    elif any(c.isdigit() for c in address):
        score += 15   # Has street number = full points
    elif len(address) > 5:
        score += 7    # Has name but no number
    # else 0

    # D: Contact info (0-15 pts) — V13.1: More granular
    has_phone = bool(permit.get('contact_phone'))
    has_email = bool(permit.get('contact_email'))
    has_contractor = bool(permit.get('contractor_name'))
    has_owner = bool(permit.get('owner_name'))

    if has_phone and has_email:
        score += 15
    elif has_phone or has_email:
        score += 12
    elif has_contractor:
        score += 8
    elif has_owner:
        score += 5
    # else 0

    # E: Status (0-5 pts) — V13.1: Reduced weight, least important
    status = str(permit.get('status', '')).lower().strip()
    if status in ('issued', 'approved', 'active', 'permitted', 'finaled'):
        score += 5
    elif status in ('pending', 'in review', 'under review', 'plan review', 'filed', 'submitted'):
        score += 3
    # else 0

    return max(0, min(100, round(score)))


def add_lead_scores(permits):
    """
    V13.1: Apply absolute lead scoring with wider spread.
    Also assigns lead_quality tier based on score.
    """
    if not permits:
        return permits

    for p in permits:
        score = calculate_lead_score(p)
        p['lead_score'] = score

        # V13.1: Adjusted thresholds for wider score distribution
        if score >= 60:
            p['lead_quality'] = 'hot'
        elif score >= 40:
            p['lead_quality'] = 'warm'
        else:
            p['lead_quality'] = 'standard'

    return permits


# ===========================
# TRADE CLASSIFICATION
# ===========================
def classify_trade(text):
    """Classify a permit into a trade category based on description text."""
    if not text:
        return "General Construction"

    text_lower = text.lower()
    scores = {}

    for trade, keywords in TRADE_CATEGORIES.items():
        score = sum(1 for kw in keywords if kw in text_lower)
        if score > 0:
            scores[trade] = score

    if not scores:
        return "General Construction"

    # Priority order for ties
    priority_trades = [
        "Electrical", "Plumbing", "HVAC", "Roofing", "Solar", "Fire Protection",
        "Demolition", "Signage", "Windows & Doors", "Structural",
        "Interior Renovation", "Landscaping & Exterior",
        "New Construction", "Addition", "General Construction"
    ]

    specific_matches = {t: s for t, s in scores.items() if t != "General Construction"}

    if specific_matches:
        max_score = max(specific_matches.values())
        top_matches = [t for t, s in specific_matches.items() if s == max_score]

        if len(top_matches) == 1:
            return top_matches[0]

        for trade in priority_trades:
            if trade in top_matches:
                return trade

        return top_matches[0]

    return "General Construction"


def reclassify_permit(permit):
    """Re-classify a permit's trade category based on its description and type fields."""
    text_parts = [
        permit.get('description', ''),
        permit.get('work_type', ''),
        permit.get('permit_type', '')
    ]
    text = ' '.join(filter(None, text_parts))
    permit['trade_category'] = classify_trade(text)
    return permit


def generate_permit_description(permit):
    """
    Generate a unique, factual description based on actual permit data.
    Falls back to building a description from permit fields if no real description exists,
    or if the description appears templated (same as others).
    """
    existing_desc = permit.get('description', '')

    # Build a factual description from permit data
    parts = []

    # Permit type
    permit_type = permit.get('permit_type', '') or permit.get('work_type', '')
    if permit_type:
        parts.append(permit_type.strip())

    # Trade category
    trade = permit.get('trade_category', '')
    if trade and trade not in ['General Construction', 'Other']:
        if not any(trade.lower() in p.lower() for p in parts):
            parts.append(f"({trade})")

    # Address — V12.57: Clean raw JSON/GeoJSON before displaying
    address = permit.get('address', '')
    if address and ('{' in str(address)):
        # Address contains JSON — try to parse it
        from collector import parse_address_value
        address = parse_address_value(address)
    if address:
        parts.append(f"at {address}")

    # Value - V12.27: Skip if at $50M cap (unreliable data)
    cost = permit.get('estimated_cost', 0) or 0
    MAX_REASONABLE_COST = 50_000_000
    if cost > 0 and cost != MAX_REASONABLE_COST:
        if cost >= 1000000:
            parts.append(f"— ${cost/1000000:.1f}M project")
        elif cost >= 1000:
            parts.append(f"— ${cost/1000:.0f}K project")
        else:
            parts.append(f"— ${cost:,.0f}")

    # Status
    status = permit.get('status', '')
    if status:
        parts.append(f"[{status}]")

    # Permit number for uniqueness
    permit_num = permit.get('permit_number', '')
    if permit_num:
        parts.append(f"(Permit #{permit_num})")

    # Combine parts
    generated_desc = ' '.join(parts)

    # Return existing description if it's substantial and unique-looking
    # (has actual address or permit number in it), otherwise use generated
    if existing_desc and len(existing_desc) > 30:
        # Check if existing description contains unique identifiers
        has_address = address and address[:10] in existing_desc
        has_permit_num = permit_num and permit_num in existing_desc
        if has_address or has_permit_num:
            return existing_desc

    return generated_desc if generated_desc else existing_desc


# ===========================
# DATA LOADING
# ===========================
def format_permit_address(permit):
    """V12.11: Format address field appropriately.

    For county datasets, addresses may be location/area names (no street number).
    Detect these and label them as "Location:" instead of pretending they're street addresses.
    """
    address = permit.get('address', '') or ''
    if not address.strip():
        permit['display_address'] = 'Address not provided'
        permit['address_type'] = 'none'
        return

    address_clean = address.strip()

    # Check if it looks like a real street address (has a number at the start)
    import re
    has_street_number = bool(re.match(r'^\d+\s', address_clean))

    # Common area/location-only patterns (no street number, short, all caps)
    is_location_only = (
        not has_street_number and
        len(address_clean) < 30 and
        (address_clean.isupper() or
         address_clean.upper() in ['MONTGOMERY', 'ROCK SPRING', 'BETHESDA', 'SILVER SPRING',
                                   'ROCKVILLE', 'WHEATON', 'GERMANTOWN', 'GAITHERSBURG',
                                   'POTOMAC', 'CHEVY CHASE', 'TAKOMA PARK', 'KENSINGTON'])
    )

    if is_location_only:
        permit['display_address'] = f"Area: {address_clean.title()}"
        permit['address_type'] = 'location'
    elif not has_street_number and len(address_clean.split()) <= 3:
        # Short address without number - likely a location name
        permit['display_address'] = f"Location: {address_clean.title()}"
        permit['address_type'] = 'location'
    else:
        permit['display_address'] = address_clean
        permit['address_type'] = 'street'


def validate_permit_dates(permit):
    """V12.9: Validate and relabel future-dated permits.

    If filing_date is >30 days in the future, it's likely an expiration date,
    not a filing date. Relabel it appropriately.
    """
    filing_date_str = permit.get('filing_date', '')
    if not filing_date_str:
        return

    try:
        filing_date = datetime.strptime(str(filing_date_str)[:10], '%Y-%m-%d')
        days_from_now = (filing_date - datetime.now()).days

        if days_from_now > 30:
            # This is likely an expiration/completion date, not a filing date
            permit['expiration_date'] = filing_date_str
            permit['date_label'] = 'Expires'
            # Try to find an alternative filing date
            for alt_key in ['issued_date', 'issue_date', 'created_date', 'application_date']:
                alt_date = permit.get(alt_key)
                if alt_date:
                    try:
                        alt_parsed = datetime.strptime(str(alt_date)[:10], '%Y-%m-%d')
                        if (alt_parsed - datetime.now()).days <= 30:
                            permit['filing_date'] = str(alt_date)[:10]
                            permit['date_label'] = 'Filed'
                            return
                    except:
                        pass
            # No alternative found, keep expiration date but mark it
            permit['filing_date'] = filing_date_str
            permit['date_label'] = 'Expires'
        else:
            permit['date_label'] = 'Filed'
    except (ValueError, TypeError):
        permit['date_label'] = 'Filed'  # Default label


# V12.51: Removed _load_permits_from_disk() and load_permits()
# All permit data now comes from SQLite via permitdb.query_permits()
# This eliminates the JSON file parsing that caused OOM crashes

def load_stats():
    """Load collection stats. V12.51: Falls back to SQLite if JSON not found."""
    path = os.path.join(DATA_DIR, 'collection_stats.json')
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f, strict=False)
        except Exception as e:
            print(f"[Server] ERROR: Failed to parse collection_stats.json: {e}")
            # Fall through to SQLite fallback

    # V12.51: SQLite fallback
    try:
        return permitdb.get_collection_stats()
    except Exception:
        return {}

def get_cities_with_data():
    """V15/V34: Get cities with VERIFIED data, sorted by permit volume.

    V34: Now filters out cities with 0 actual permits in the DB.
    Only returns cities that genuinely have permit data, regardless of
    what prod_cities.total_permits says (that column can be stale).

    V15: Uses prod_cities table if available (collector redesign).
    Falls back to heuristics-based filtering if prod_cities is empty.
    """
    # V15/V34: Try prod_cities first (collector redesign)
    # V34: total_permits is synced with actual DB counts on startup,
    # so we can trust it for filtering. No expensive JOIN needed per-request.
    try:
        if permitdb.prod_cities_table_exists():
            # min_permits=1 filters out cities with 0 real permits
            prod_cities = permitdb.get_prod_cities(status='active', min_permits=1)
            if prod_cities:
                return prod_cities
    except Exception as e:
        print(f"[V15] Error getting prod_cities: {e}")

    # Fall back to heuristics (pre-V15 behavior)
    # V13.2: Valid US state/territory codes - filter out Canadian provinces etc.
    VALID_US_STATES = {
        'AL', 'AK', 'AZ', 'AR', 'CA', 'CO', 'CT', 'DE', 'DC', 'FL', 'GA',
        'HI', 'ID', 'IL', 'IN', 'IA', 'KS', 'KY', 'LA', 'ME', 'MD', 'MA',
        'MI', 'MN', 'MS', 'MO', 'MT', 'NE', 'NV', 'NH', 'NJ', 'NM', 'NY',
        'NC', 'ND', 'OH', 'OK', 'OR', 'PA', 'RI', 'SC', 'SD', 'TN', 'TX',
        'UT', 'VT', 'VA', 'WA', 'WV', 'WI', 'WY',
        'AS', 'GU', 'MP', 'PR', 'VI'  # territories
    }

    # V13.2: US state names to filter out as city entries
    US_STATE_NAMES = {
        'alabama', 'alaska', 'arizona', 'arkansas', 'california', 'colorado',
        'connecticut', 'delaware', 'florida', 'georgia', 'hawaii', 'idaho',
        'illinois', 'indiana', 'iowa', 'kansas', 'kentucky', 'louisiana',
        'maine', 'maryland', 'massachusetts', 'michigan', 'minnesota',
        'mississippi', 'missouri', 'montana', 'nebraska', 'nevada',
        'new hampshire', 'new jersey', 'new mexico', 'new york',
        'north carolina', 'north dakota', 'ohio', 'oklahoma', 'oregon',
        'pennsylvania', 'rhode island', 'south carolina', 'south dakota',
        'tennessee', 'texas', 'utah', 'vermont', 'virginia', 'washington',
        'west virginia', 'wisconsin', 'wyoming', 'district of columbia'
    }

    # V13.3: Garbage patterns - dataset names, permit types, and other junk
    # V13.9: Added 'building:' for "Building: Addition/Alteration" entries
    GARBAGE_PATTERNS = [
        'dob now', 'build –', 'build-', 'applications', 'certificate',
        'permits table', 'data_wfl', 'epic-la', 'bureau of', '_wgs84',
        'inspections', 'case history', 'building and safety',
        'development permits', 'sewer data', 'engineering permit',
        'permit information', 'county permit', 'limited alteration',
        'building:'
    ]

    # Get city counts from SQLite - this has ALL cities with permits
    city_rows = permitdb.get_cities_with_permits()

    # Get static registry for cities that have extra config
    all_cities = get_all_cities_info()  # Active only - for display
    city_lookup = {c['name']: c for c in all_cities}
    city_lookup_lower = {c['name'].lower(): c for c in all_cities}

    # V13.4: Build registry lookup from ALL configs (including inactive)
    # This fixes Houston OK -> TX (Houston's config is inactive but has state="TX")
    registry_state_by_city = {}
    for key, cfg in CITY_REGISTRY.items():
        city_lower = cfg.get('name', '').lower()
        state = cfg.get('state', '').upper()
        if city_lower and state:
            # If city appears multiple times, prefer active config's state
            if city_lower not in registry_state_by_city or cfg.get('active'):
                registry_state_by_city[city_lower] = state

    # V13.3: Build registry lookup by (city_lower, state_upper) for state priority
    registry_by_city_state = {}
    for c in all_cities:
        key = (c['name'].lower(), c.get('state', '').upper())
        registry_by_city_state[key] = c

    # Known city name corrections (partial names -> full names)
    CITY_NAME_FIXES = {
        'orleans': 'New Orleans',
        'york': 'New York',
    }

    # PASS 1: Group by normalized key (lowercase city + state) to deduplicate
    city_groups = {}
    for row in city_rows:
        name = row['city']
        state = row.get('state', '')
        permit_count = row.get('permit_count', 0)

        if not name or not name.strip():
            continue

        # Normalize the city name first (needed for registry lookup)
        name_lower = name.lower().strip()

        # V13.4: Use registry to correct state (fixes Houston OK -> TX)
        registry_state = registry_state_by_city.get(name_lower)
        if registry_state:
            state = registry_state

        # V13.5: Fix state corruption - reassign misassigned cities
        # The DB has cities incorrectly tagged from past bulk runs
        state_upper = state.upper() if state else ''

        KNOWN_OK_CITIES = {
            'oklahoma city', 'tulsa', 'norman', 'broken arrow', 'edmond',
            'lawton', 'moore', 'midwest city', 'enid', 'stillwater',
            'muskogee', 'bartlesville', 'owasso', 'shawnee', 'ponca city',
            'ardmore', 'duncan', 'del city', 'bixby', 'sapulpa', 'altus',
            'bethany', 'sand springs', 'yukon', 'mustang', 'claremore'
        }
        if state_upper == 'OK' and name_lower not in KNOWN_OK_CITIES:
            state = 'TX'

        # V13.6: Fix NV state corruption - ~98 Texas towns tagged as NV
        KNOWN_NV_CITIES = {
            'las vegas', 'henderson', 'reno', 'north las vegas', 'sparks',
            'carson city', 'elko', 'mesquite', 'boulder city', 'fernley',
            'fallon', 'winnemucca', 'west wendover', 'ely', 'yerington'
        }
        if state_upper == 'NV' and name_lower not in KNOWN_NV_CITIES:
            state = 'TX'

        # V13.6: Fix IN state corruption - ~25 Florida cities tagged as IN
        KNOWN_IN_CITIES = {
            'indianapolis', 'fort wayne', 'evansville', 'south bend', 'carmel',
            'fishers', 'bloomington', 'hammond', 'gary', 'lafayette', 'muncie',
            'terre haute', 'kokomo', 'anderson', 'noblesville', 'greenwood',
            'elkhart', 'mishawaka', 'lawrence', 'jeffersonville', 'columbus'
        }
        if state_upper == 'IN' and name_lower not in KNOWN_IN_CITIES:
            state = 'FL'

        # V13.6: Fix LA state corruption - ~70 LA (Los Angeles) cities tagged as LA (Louisiana)
        KNOWN_LA_CITIES = {
            'new orleans', 'baton rouge', 'shreveport', 'lafayette', 'lake charles',
            'kenner', 'bossier city', 'monroe', 'alexandria', 'houma', 'slidell',
            'metairie', 'new iberia', 'laplace', 'central', 'ruston', 'sulphur',
            'hammond', 'natchitoches', 'gretna', 'opelousas', 'zachary', 'thibodaux'
        }
        if state_upper == 'LA' and name_lower not in KNOWN_LA_CITIES:
            state = 'CA'

        # V13.4: Require valid US state (eliminates "Other Locations" garbage)
        if not state or state.upper() not in VALID_US_STATES:
            continue

        # V13.2: Filter out state names appearing as city names
        if name_lower in US_STATE_NAMES:
            continue

        # V13.3: Filter garbage city names (dataset names, permit types, etc.)
        if any(p in name_lower for p in GARBAGE_PATTERNS):
            continue

        # V13.6: Filter county names and abbreviations
        # V13.8: Added 'general', 'electrical', 'roof' per UAT Round 7 (trade names)
        if 'county' in name_lower or name_lower in ('uninc', 'unincorporated', 'general', 'electrical', 'roof'):
            continue

        # V13.6: Skip very short names (likely abbreviations or garbage)
        if len(name) < 3:
            continue

        # V13.3: Skip names that are too long (real city names are rarely >35 chars)
        if len(name) > 35:
            continue

        # Apply known fixes for partial names
        if name_lower in CITY_NAME_FIXES:
            name = CITY_NAME_FIXES[name_lower]
            name_lower = name.lower()

        # Create dedup key (city + state)
        key = (name_lower, state.upper() if state else '')

        if key not in city_groups:
            city_groups[key] = {
                'names': [],
                'state': state,
                'permit_count': 0
            }

        city_groups[key]['names'].append(name)
        city_groups[key]['permit_count'] += permit_count

    # PASS 2: Cross-state dedup - merge same city name across different states
    # Group by city name only, then pick the state with highest permit count
    name_only_groups = {}
    for (name_lower, state_code), group in city_groups.items():
        if name_lower not in name_only_groups:
            name_only_groups[name_lower] = []
        name_only_groups[name_lower].append({
            'state_code': state_code,
            'state': group['state'],
            'names': group['names'],
            'permit_count': group['permit_count']
        })

    # Build final city list - for each city name, pick best state
    cities_with_counts = []
    for name_lower, state_entries in name_only_groups.items():
        # Sum ALL permit counts across all states for this city
        total_count = sum(e['permit_count'] for e in state_entries)

        # V13.3: Prioritize registry state over permit count
        # First check if any (city, state) combo is in the registry
        registry_entry = None
        registry_state_entry = None
        for entry in state_entries:
            key = (name_lower, entry['state_code'])
            if key in registry_by_city_state:
                registry_entry = registry_by_city_state[key]
                registry_state_entry = entry
                break

        # If registry match found, use that; otherwise use highest permit count
        if registry_entry:
            city_info = registry_entry.copy()
            city_info['permit_count'] = total_count
            cities_with_counts.append(city_info)
            continue

        # Also check registry by name only (case-insensitive)
        if name_lower in city_lookup_lower:
            registry_city = city_lookup_lower[name_lower]
            city_info = registry_city.copy()
            city_info['permit_count'] = total_count
            cities_with_counts.append(city_info)
            continue

        # Not in registry - pick state with highest permit count
        best_entry = max(state_entries, key=lambda x: x['permit_count'])

        # Pick best display name from variants
        best_name = None
        for n in best_entry['names']:
            if n == n.title():
                best_name = n
                break
        if not best_name:
            best_name = best_entry['names'][0].title()

        state = best_entry['state']
        slug = best_name.lower().replace(' ', '-').replace(',', '').replace('.', '')

        city_info = {
            'name': best_name,
            'state': state,
            'slug': slug,
            'permit_count': total_count,
            'active': True
        }
        cities_with_counts.append(city_info)

    # V13.7: Filter out cities with very few permits (reduces TX from 1,170 to ~100)
    # Cities with <10 permits aren't useful leads and inflate the city count
    MIN_PERMIT_THRESHOLD = 10
    cities_with_counts = [c for c in cities_with_counts if c.get('permit_count', 0) >= MIN_PERMIT_THRESHOLD]

    # Sort by permit count descending (top cities first)
    cities_with_counts.sort(key=lambda x: x.get('permit_count', 0), reverse=True)
    return cities_with_counts


def get_suggested_cities(searched_slug, limit=6):
    """V12.9: Get similar city suggestions for 404 page using fuzzy matching."""
    all_cities = get_all_cities_info()
    active_cities = [c for c in all_cities if c.get('active', True)]

    # Calculate similarity scores
    suggestions = []
    searched_lower = searched_slug.lower().replace('-', ' ')

    for city in active_cities:
        slug_lower = city['slug'].lower().replace('-', ' ')
        name_lower = city['name'].lower()

        # Check multiple matching criteria
        slug_score = SequenceMatcher(None, searched_lower, slug_lower).ratio()
        name_score = SequenceMatcher(None, searched_lower, name_lower).ratio()

        # Boost if searched term is contained in name
        contains_boost = 0.3 if searched_lower in name_lower or name_lower in searched_lower else 0

        best_score = max(slug_score, name_score) + contains_boost
        if best_score > 0.3:  # Only include if somewhat similar
            suggestions.append((city, best_score))

    # Sort by score, take top matches
    suggestions.sort(key=lambda x: -x[1])
    return [s[0] for s in suggestions[:limit]]


def get_popular_cities(limit=12):
    """V12.51: Get popular cities for 404 page (SQL-backed)."""
    conn = permitdb.get_connection()
    rows = conn.execute("""
        SELECT city, COUNT(*) as cnt FROM permits
        WHERE city IS NOT NULL AND city != ''
        GROUP BY city ORDER BY cnt DESC LIMIT ?
    """, (limit * 2,)).fetchall()  # Fetch extra in case some aren't in city_lookup

    all_cities = get_all_cities_info()
    city_lookup = {c['name']: c for c in all_cities}

    popular = []
    for row in rows:
        name = row['city']
        if name in city_lookup:
            city_info = city_lookup[name].copy()
            popular.append(city_info)
            if len(popular) >= limit:
                break

    return popular


def render_city_not_found(searched_slug):
    """V12.9: Render branded 404 page with city suggestions."""
    suggestions = get_suggested_cities(searched_slug)
    popular_cities = get_popular_cities()
    footer_cities = get_cities_with_data()

    return render_template(
        '404.html',
        searched_slug=searched_slug,
        suggestions=suggestions,
        popular_cities=popular_cities,
        footer_cities=footer_cities,
        show_city_suggestions=True,
    ), 404


# V12.53: DEPRECATED - Use User model with digest_cities and digest_active fields
def load_subscribers():
    """DEPRECATED: Load subscriber list from JSON file.
    V12.53: Use User.query.filter(User.digest_active == True) instead.
    """
    if os.path.exists(SUBSCRIBERS_FILE):
        with open(SUBSCRIBERS_FILE) as f:
            return json.load(f)
    return []


def save_subscribers(subs):
    """DEPRECATED: Save subscriber list to JSON file.
    V12.53: Use User model with db.session.commit() instead.
    """
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(SUBSCRIBERS_FILE, 'w') as f:
        json.dump(subs, f, indent=2)


# ===========================
# USER DATABASE FUNCTIONS (V7)
# ===========================
# All user operations now use PostgreSQL instead of JSON files


def find_user_by_email(email):
    """Find a user by email (case-insensitive). Returns User object or None."""
    if not email:
        return None
    email_lower = email.lower().strip()
    return User.query.filter(db.func.lower(User.email) == email_lower).first()


def get_current_user():
    """Get the currently logged-in user from session. Returns dict for backward compatibility."""
    user_email = session.get('user_email')
    if not user_email:
        return None
    user = find_user_by_email(user_email)
    if user:
        return user.to_dict()
    return None


def get_current_user_object():
    """Get the currently logged-in user as User object (for database operations)."""
    user_email = session.get('user_email')
    if not user_email:
        return None
    return find_user_by_email(user_email)


# ===========================
# BACKWARD COMPATIBILITY SHIMS (V7)
# ===========================
# These functions provide backward compatibility with code that expects
# the old dict-based user storage while actually using the database.

def load_users():
    """Load all users from database as list of dicts (backward compatibility)."""
    users = User.query.all()
    return [u.to_dict() for u in users]


def save_users(users):
    """Save users to database (backward compatibility - DEPRECATED).
    This is a no-op shim. Individual user updates should use db.session.commit().
    Kept for backward compatibility with code that still calls this.
    """
    # No-op: database operations should be done directly
    # Individual updates use db.session.commit()
    pass


def update_user_by_email(email, updates):
    """Update a user's fields by email (V7 helper)."""
    user = find_user_by_email(email)
    if user:
        for key, value in updates.items():
            if hasattr(user, key):
                setattr(user, key, value)
        db.session.commit()
        return True
    return False


def get_user_plan(user):
    """
    Returns 'pro', 'free', or 'anonymous'.
    Centralizes all plan checking logic to avoid inconsistencies.
    Recognizes Pro status from:
    - user.plan == 'pro'
    - user.plan == 'professional' (Stripe)
    - user.plan == 'enterprise'
    - user.stripe_subscription_status == 'active'
    """
    if not user:
        return 'anonymous'

    plan = (user.get('plan') or '').lower()

    # Check admin-set or Stripe-set plans
    if plan in ('pro', 'professional', 'enterprise'):
        return 'pro'

    # Check Stripe subscription status
    if user.get('stripe_subscription_status') == 'active':
        return 'pro'

    return 'free'


def is_pro(user):
    """Returns True if user has Pro access."""
    return get_user_plan(user) == 'pro'


# V69: COMPLETELY STATIC nav context — NO database access whatsoever
@app.context_processor
def inject_nav_context():
    """V69: Return static empty data. NO DB calls until server is stable."""
    return {
        'user': None,
        'user_plan': 'anonymous',
        'is_pro': False,
        'nav_cities': []
    }


# ===========================
# V29: SEO — www to non-www redirect + trailing slash normalization
# ===========================

@app.before_request
def seo_redirects():
    """V29: Redirect www.permitgrab.com → permitgrab.com (301) and normalize trailing slashes."""
    # www → non-www redirect
    if request.host.startswith('www.'):
        return redirect(request.url.replace('://www.', '://', 1), code=301)
    # Remove trailing slashes (except root)
    if request.path != '/' and request.path.endswith('/'):
        return redirect(request.url.replace(request.path, request.path.rstrip('/')), code=301)


# ===========================
# ANALYTICS HOOKS
# ===========================

@app.before_request
def analytics_before_request():
    """Capture UTM parameters and ensure session ID exists."""
    try:
        # Ensure analytics session ID
        if 'analytics_session_id' not in session:
            session['analytics_session_id'] = str(uuid.uuid4())

        # Capture UTM parameters
        utm_params = {}
        for key in ['utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content']:
            val = request.args.get(key)
            if val:
                utm_params[key] = val
        if utm_params:
            session['utm_params'] = utm_params
    except Exception:
        pass  # Never break the request


@app.after_request
def analytics_track_page_view(response):
    """Track page views for all successful page loads."""
    try:
        if response.status_code < 400 and request.endpoint:
            # Don't track static files, API calls, or health endpoint
            skip_prefixes = ('/static', '/api/', '/health', '/favicon', '/robots', '/sitemap')
            if not any(request.path.startswith(p) for p in skip_prefixes):
                analytics.track_event(
                    event_type='page_view',
                    page=request.path,
                    event_data={
                        'status_code': response.status_code,
                        'method': request.method
                    }
                )
                # V12.59b: Persistent page view logging to PostgreSQL
                try:
                    import psycopg2
                    pg_conn = psycopg2.connect(os.environ.get('DATABASE_URL', ''))
                    pg_cur = pg_conn.cursor()
                    pg_cur.execute(
                        """INSERT INTO page_views (path, method, status_code, user_agent, ip_address, referrer, session_id, user_id)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                        (
                            request.path,
                            request.method,
                            response.status_code,
                            request.headers.get('User-Agent', '')[:500],
                            request.headers.get('X-Forwarded-For', request.remote_addr or ''),
                            request.headers.get('Referer', ''),
                            request.cookies.get('session_id', ''),
                            getattr(g, 'user_id', None) if hasattr(g, 'user_id') else None
                        )
                    )
                    pg_conn.commit()
                    pg_cur.close()
                    pg_conn.close()
                except Exception:
                    pass  # Never break the page for analytics
    except Exception:
        pass  # Never break the response
    return response


def generate_unsubscribe_token():
    """Generate a unique unsubscribe token."""
    return secrets.token_urlsafe(32)


SAVED_LEADS_FILE = os.path.join(DATA_DIR, 'saved_leads.json')
PERMIT_HISTORY_FILE = os.path.join(DATA_DIR, 'permit_history.json')
VIOLATIONS_FILE = os.path.join(DATA_DIR, 'violations.json')
SIGNALS_FILE = os.path.join(DATA_DIR, 'signals.json')


def load_permit_history():
    """Load permit history index from JSON file."""
    if os.path.exists(PERMIT_HISTORY_FILE):
        with open(PERMIT_HISTORY_FILE) as f:
            return json.load(f)
    return {}


def load_violations():
    """Load code violations from JSON file."""
    if os.path.exists(VIOLATIONS_FILE):
        try:
            with open(VIOLATIONS_FILE) as f:
                return json.load(f, strict=False)
        except Exception as e:
            print(f"[Server] ERROR: Failed to parse violations.json: {e}")
            return []
    return []


def load_signals():
    """Load pre-construction signals from JSON file."""
    if os.path.exists(SIGNALS_FILE):
        try:
            with open(SIGNALS_FILE) as f:
                return json.load(f, strict=False)
        except Exception as e:
            print(f"[Server] ERROR: Failed to parse signals.json: {e}")
            return []
    return []


def normalize_address_for_lookup(address):
    """Normalize an address for lookup (matches collector.py logic)."""
    import re
    if not address:
        return ""
    addr = address.lower().strip()
    addr = re.sub(r'\s+', ' ', addr)
    replacements = [
        (r'\bstreet\b', 'st'),
        (r'\bavenue\b', 'ave'),
        (r'\bboulevard\b', 'blvd'),
        (r'\bdrive\b', 'dr'),
        (r'\broad\b', 'rd'),
        (r'\blane\b', 'ln'),
        (r'\bcourt\b', 'ct'),
        (r'\bplace\b', 'pl'),
        (r'\bapartment\b', 'apt'),
        (r'\bsuite\b', 'ste'),
        (r'\bnorth\b', 'n'),
        (r'\bsouth\b', 's'),
        (r'\beast\b', 'e'),
        (r'\bwest\b', 'w'),
    ]
    for pattern, replacement in replacements:
        addr = re.sub(pattern, replacement, addr)
    addr = re.sub(r'[^\w\s#-]', '', addr)
    return addr


def load_saved_leads():
    """Load saved leads from JSON file."""
    if os.path.exists(SAVED_LEADS_FILE):
        with open(SAVED_LEADS_FILE) as f:
            return json.load(f)
    return []


def save_saved_leads(leads):
    """Save saved leads to JSON file."""
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(SAVED_LEADS_FILE, 'w') as f:
        json.dump(leads, f, indent=2)


def get_user_saved_leads(user_email):
    """Get saved leads for a specific user."""
    all_leads = load_saved_leads()
    return [l for l in all_leads if l.get('user_email') == user_email]


# ===========================
# API ROUTES
# ===========================

@app.route('/')
def index():
    """Serve the dashboard."""
    # V8: Redirect new users to onboarding
    # V9 Fix: Only redirect truly new users - existing users with preferences or Pro plan skip onboarding
    if 'user_email' in session:
        user = find_user_by_email(session['user_email'])
        if user and not user.onboarding_completed:
            # Existing users who already have preferences or are Pro don't need onboarding
            has_preferences = user.city or user.trade
            is_pro = user.plan == 'pro'
            if has_preferences or is_pro:
                # Mark as completed so we don't check again
                user.onboarding_completed = True
                db.session.commit()
            else:
                return redirect('/onboarding')
    footer_cities = get_cities_with_data()

    # V9 Fix 5: Pass user preferences as default filters
    default_city = ''
    default_trade = ''
    if 'user_email' in session:
        user = find_user_by_email(session['user_email'])
        if user:
            default_city = user.city or ''
            default_trade = user.trade or ''

    # V31: City count = only actively pulled cities (not historical bulk data)
    city_count = get_total_city_count_auto()

    # V13: Pass ALL cities for dropdown (sorted by state then city name)
    # This ensures dropdown shows all 555+ cities, not just those in the paginated API response
    all_dropdown_cities = get_cities_with_data()  # Now returns all cities from permits table

    # V13.7: Pass stats for server-side rendering (fixes H5: stat counters showing dashes)
    stats = permitdb.get_permit_stats()
    initial_stats = {
        'total_permits': stats.get('total_permits', 0),
        'total_value': stats.get('total_value', 0),
        'high_value_count': stats.get('high_value_count', 0),
    }

    return render_template('dashboard.html', footer_cities=footer_cities,
                          default_city=default_city, default_trade=default_trade,
                          city_count=city_count, all_dropdown_cities=all_dropdown_cities,
                          initial_stats=initial_stats)


# V9 Fix 9: /dashboard redirects to homepage (V13.7: redirect to login if not authenticated)
@app.route('/dashboard')
def dashboard_redirect():
    """Redirect /dashboard to / for authenticated users, /login for unauthenticated."""
    if 'user_email' not in session:
        return redirect('/login?redirect=dashboard&message=login_required')
    return redirect('/')


# V10 Fix 5: /alerts redirects to account page
@app.route('/alerts')
def alerts_redirect():
    """V30: Redirect to appropriate alerts page based on login status."""
    user = get_current_user()
    if user:
        return redirect('/account')
    return redirect('/get-alerts')


@app.route('/health')
@app.route('/api/health')
def health_check():
    """
    V12.51: Health check endpoint with SQLite data availability check.
    V67: Always return 200 during startup to prevent Render restart loop.
    """
    # V67: During startup, return healthy without touching DB
    # This prevents pool exhaustion from killing health checks
    if not _startup_done:
        return jsonify({
            'status': 'starting',
            'timestamp': datetime.now().isoformat(),
            'message': 'V67: Background init in progress, service is alive'
        }), 200

    # After startup, do the full health check
    try:
        stats = permitdb.get_permit_stats()
        permit_count = stats['total_permits']
    except Exception as e:
        # V67: Return degraded (still 200!) if DB is temporarily unavailable
        return jsonify({
            'status': 'degraded',
            'timestamp': datetime.now().isoformat(),
            'message': f'DB temporarily unavailable: {str(e)[:100]}',
            'data_loaded': _initial_data_loaded
        }), 200

    if permit_count == 0 and is_data_loading():
        # No data and we're in a loading state - still return 200 but indicate loading
        return jsonify({
            'status': 'loading',
            'timestamp': datetime.now().isoformat(),
            'message': 'Data collection in progress',
            'permit_count': 0
        }), 200  # V67: Changed from 503 to 200 to prevent restart loop

    # V16: Collection health tracking
    collection_status = 'never'
    hours_since_collection = None
    if _last_collection_run:
        hours_since_collection = (datetime.now() - _last_collection_run).total_seconds() / 3600
        if hours_since_collection > 12:
            collection_status = 'stale'  # Warning: collection hasn't run recently
        else:
            collection_status = 'healthy'

    return jsonify({
        'status': 'ok',
        'timestamp': datetime.now().isoformat(),
        'permit_count': permit_count,
        'data_loaded': _initial_data_loaded,
        'collection_status': collection_status,
        'last_collection_run': _last_collection_run.isoformat() if _last_collection_run else None,
        'hours_since_collection': round(hours_since_collection, 1) if hours_since_collection else None
    }), 200


@app.route('/api/permits')
@limiter.limit("60 per minute")
def api_permits():
    """
    GET /api/permits — V12.50: SQL-backed queries.
    Query params: city, trade, value, status, search, quality, page, per_page
    Returns paginated, filtered permit data with lead scores.

    FREEMIUM GATING: Non-Pro users see masked contact info on ALL permits.
    """
    # Parse filters
    city = request.args.get('city', '')
    trade = request.args.get('trade', '')
    value = request.args.get('value', '')
    status_filter = request.args.get('status', '')
    quality = request.args.get('quality', '')
    search = request.args.get('search', '')
    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 50))

    # V32: Resolve city slug to name and state for cross-state filtering
    city_name = None
    city_state = None
    if city:
        city_key, city_config = get_city_by_slug(city)
        if city_config:
            city_name = city_config.get('name', city)
            city_state = city_config.get('state', '')
        else:
            city_name = city  # Use as-is if not a valid slug

    # Resolve trade slug to name if needed
    trade_name = None
    if trade and trade != 'all-trades':
        trade_config = get_trade(trade)
        if trade_config:
            trade_name = trade_config.get('name', trade)
        else:
            trade_name = trade  # Use as-is if not a valid slug

    # V13.2: SQL ORDER BY prioritizes data quality so "All Cities" default shows
    # best data first, not just Mesa permits with garbage dates (which sort high
    # lexicographically because "WROCCO" > "2026-03-24").
    #
    # Priority: high cost → valid date → has address → has contact → recent date
    # This ensures Austin (85 pts) and Chicago (72 pts) surface before Mesa (28 pts).
    data_quality_order = """
        CASE WHEN estimated_cost > 100000 THEN 0
             WHEN estimated_cost > 10000 THEN 1
             WHEN estimated_cost > 0 THEN 2
             ELSE 3 END,
        CASE WHEN filing_date GLOB '[0-9][0-9][0-9][0-9]-*' THEN 0 ELSE 1 END,
        CASE WHEN address IS NOT NULL AND address != '' THEN 0 ELSE 1 END,
        CASE WHEN contractor_name IS NOT NULL OR contact_phone IS NOT NULL THEN 0 ELSE 1 END,
        filing_date DESC
    """

    # V12.50: Query SQLite database (replaces loading 100K permits into memory)
    # V32: Pass state to prevent cross-state data pollution
    permits, total = permitdb.query_permits(
        city=city_name,
        state=city_state,
        trade=trade_name,
        value=value or None,
        status=status_filter or None,
        search=search or None,
        page=page,
        per_page=per_page,
        order_by=data_quality_order
    )

    # Add lead scores to page results
    permits = add_lead_scores(permits)

    # Sort by lead score (hot leads first) within page
    permits.sort(key=lambda x: x.get('lead_score', 0), reverse=True)

    # Quality filter (post-query since lead_score is computed)
    if quality:
        if quality == 'hot':
            permits = [p for p in permits if p.get('lead_quality') == 'hot']
        elif quality == 'warm':
            permits = [p for p in permits if p.get('lead_quality') in ('hot', 'warm')]

    # FREEMIUM GATING: Strip contact info for ALL permits for non-Pro users
    user = get_current_user()
    user_is_pro = is_pro(user)

    if not user_is_pro:
        for permit in permits:
            permit['contact_phone'] = None
            permit['contact_name'] = None
            permit['contact_email'] = None
            permit['owner_name'] = None
            permit['is_gated'] = True
    else:
        for permit in permits:
            permit['is_gated'] = False

    # V12.50: Aggregate stats from SQL (not from loading all permits!)
    stats_data = permitdb.get_permit_stats()
    collection_stats = load_stats()  # Keep this for collected_at timestamp

    return jsonify({
        'permits': permits,
        'total': total,
        'page': page,
        'per_page': per_page,
        'total_pages': (total + per_page - 1) // per_page,
        'user_is_pro': user_is_pro,
        'last_updated': collection_stats.get('collected_at', ''),
        'total_value': stats_data['total_value'],
        'high_value_count': stats_data['high_value_count'],
        'total_permits': stats_data['total_permits'],
    })

@app.route('/api/stats')
def api_stats():
    """GET /api/stats — V12.50: SQL-backed stats."""
    stats_data = permitdb.get_permit_stats()
    collection_stats = load_stats()

    return jsonify({
        'total_permits': stats_data['total_permits'],
        'total_value': stats_data['total_value'],
        'high_value_count': stats_data['high_value_count'],
        'cities': stats_data['city_count'],
        'trade_breakdown': collection_stats.get('trade_breakdown', {}),
        'value_breakdown': collection_stats.get('value_breakdown', {}),
        'last_updated': collection_stats.get('collected_at', ''),
    })

@app.route('/api/filters')
def api_filters():
    """GET /api/filters - Available filter options (V12.51: SQL-backed)."""
    conn = permitdb.get_connection()

    cities = [r[0] for r in conn.execute(
        "SELECT DISTINCT city FROM permits WHERE city IS NOT NULL AND city != '' ORDER BY city"
    ).fetchall()]

    trades = [r[0] for r in conn.execute(
        "SELECT DISTINCT trade_category FROM permits WHERE trade_category IS NOT NULL AND trade_category != '' ORDER BY trade_category"
    ).fetchall()]

    statuses = [r[0] for r in conn.execute(
        "SELECT DISTINCT status FROM permits WHERE status IS NOT NULL AND status != '' ORDER BY status"
    ).fetchall()]

    return jsonify({
        'cities': cities,
        'trades': trades,
        'statuses': statuses,
    })


@app.route('/api/cities')
def api_cities():
    """GET /api/cities - Get all active cities with permit data.

    V90: Now reads from prod_cities table (database) instead of static CITY_REGISTRY.
    Only returns cities with actual permit data (total_permits > 0).
    """
    # Get cities with data from database
    cities = permitdb.get_prod_cities(status='active', min_permits=1)

    # Format for frontend compatibility
    formatted_cities = []
    for city in cities:
        formatted_cities.append({
            'name': city['name'],
            'state': city['state'],
            'slug': city['slug'],
            'permit_count': city['permit_count'],
            'active': city['active'],
        })

    return jsonify({
        'count': len(formatted_cities),
        'cities': formatted_cities,
    })


@app.route('/api/city-health')
def api_city_health():
    """GET /api/city-health - Get city API health status."""
    health_file = os.path.join(DATA_DIR, 'city_health.json')
    if os.path.exists(health_file):
        with open(health_file) as f:
            return jsonify(json.load(f))
    return jsonify({'status': 'no health data available'})


@app.route('/api/subscribe', methods=['POST'])
@limiter.limit("5 per minute")
def api_subscribe():
    """POST /api/subscribe - Add email alert subscriber.

    V12.53: Now uses User model instead of subscribers.json.
    Creates a lightweight User record for digest subscriptions.
    """
    data = request.get_json()
    if not data or not data.get('email'):
        return jsonify({'error': 'Email required'}), 400

    email = data['email'].strip().lower()
    city = data.get('city', '').strip().title()  # V12.64: Normalize to titlecase
    trade = data.get('trade', '')

    # Check if user already exists
    existing = find_user_by_email(email)
    if existing:
        # Update their digest settings
        cities = json.loads(existing.digest_cities or '[]')
        if city and city not in cities:
            cities.append(city)
            existing.digest_cities = json.dumps(cities)
        existing.digest_active = True
        if trade:
            existing.trade = trade
        db.session.commit()

        return jsonify({
            'message': f'Updated digest settings for {email}',
            'subscriber': {'email': email, 'city': city, 'trade': trade},
        }), 200

    # Create new lightweight user for digest subscription
    import secrets
    try:
        new_user = User(
            email=email,
            name=data.get('name', ''),
            password_hash='',  # No password - digest-only user
            plan='free',
            digest_active=True,
            digest_cities=json.dumps([city]) if city else '[]',
            trade=trade,
            unsubscribe_token=secrets.token_urlsafe(32),
        )
        db.session.add(new_user)
        db.session.commit()
        print(f"[Subscribe] Created digest user: {email}")
    except IntegrityError:
        db.session.rollback()
        return jsonify({'error': 'Email already exists'}), 409

    # Track alert signup event
    analytics.track_event('alert_signup', event_data={
        'city': city,
        'trade': trade
    }, city_filter=city)

    return jsonify({
        'message': f'Successfully subscribed {email}',
        'subscriber': {'email': email, 'city': city, 'trade': trade},
    }), 201


@app.route('/api/subscribers')
def api_subscribers():
    """GET /api/subscribers - List all digest subscribers (admin endpoint).

    V12.53: Now queries User model instead of subscribers.json.
    """
    # Check admin authentication
    if not session.get('admin_authenticated'):
        return jsonify({'error': 'Admin authentication required'}), 401

    users = User.query.filter(User.digest_active == True).all()
    subs = []
    for u in users:
        subs.append({
            'email': u.email,
            'name': u.name,
            'city': json.loads(u.digest_cities or '[]'),
            'trade': u.trade,
            'plan': u.plan,
            'subscribed_at': u.created_at.isoformat() if u.created_at else None,
        })

    return jsonify({
        'total': len(subs),
        'subscribers': subs,
    })

@app.route('/api/export')
def api_export():
    """GET /api/export - Export filtered permits as CSV with lead scores.

    PRO FEATURE: Non-Pro users cannot export and are redirected to pricing.
    """
    # Check if user is Pro - exports are a Pro feature
    user = get_current_user()
    if not is_pro(user):
        return jsonify({
            'error': 'Export is a Pro feature',
            'upgrade_url': '/pricing'
        }), 403

    # V12.51: SQL-backed export
    city = request.args.get('city', '')
    trade = request.args.get('trade', '')
    quality = request.args.get('quality', '')

    permits, _ = permitdb.query_permits(
        city=city or None,
        trade=trade or None,
        page=1,
        per_page=50000,  # Export limit
        order_by='filing_date DESC'
    )
    permits = add_lead_scores(permits)

    # Quality filter (post-query since lead_score is computed)
    if quality:
        if quality == 'hot':
            permits = [p for p in permits if p.get('lead_quality') == 'hot']
        elif quality == 'warm':
            permits = [p for p in permits if p.get('lead_quality') in ('hot', 'warm')]

    # Sort by lead score
    permits.sort(key=lambda x: x.get('lead_score', 0), reverse=True)

    # Build CSV
    if not permits:
        return "No permits match your filters", 404

    headers = ['address', 'city', 'state', 'zip', 'trade_category', 'estimated_cost',
               'status', 'lifecycle_stage', 'filing_date', 'contact_name', 'contact_phone', 'description',
               'lead_score', 'lead_quality']

    lines = [','.join(headers)]
    for p in permits:
        # Build row with lifecycle stage
        row = []
        for h in headers:
            if h == 'lifecycle_stage':
                row.append(get_lifecycle_label(p))
            else:
                row.append(str(p.get(h, '')).replace(',', ';').replace('"', "'").replace('\n', ' ')[:200])
        lines.append(','.join(f'"{v}"' for v in row))

    csv_content = '\n'.join(lines)

    # Track CSV export event
    analytics.track_event('csv_export', event_data={
        'row_count': len(permits),
        'filters': {'city': city, 'trade': trade, 'quality': quality}
    }, city_filter=city, trade_filter=trade)

    return Response(
        csv_content,
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename=permitgrab_leads_{datetime.now().strftime("%Y%m%d")}.csv'}
    )


# ===========================
# SAVED LEADS / CRM API
# ===========================

@app.route('/api/saved-leads', methods=['GET'])
def get_saved_leads():
    """GET /api/saved-leads - Get saved leads for logged-in user."""
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Login required'}), 401

    user_leads = get_user_saved_leads(user['email'])

    # V12.51: Enrich with permit data from SQLite
    all_permits, _ = permitdb.query_permits(page=1, per_page=100000)
    permits = add_lead_scores(all_permits)
    permit_map = {p.get('permit_number'): p for p in permits}

    enriched_leads = []
    for lead in user_leads:
        permit = permit_map.get(lead.get('permit_id'), {})
        enriched_leads.append({
            **lead,
            'permit': permit,
        })

    # Calculate stats
    total_value = sum(l['permit'].get('estimated_cost', 0) for l in enriched_leads if l.get('permit'))
    status_counts = {}
    for l in enriched_leads:
        status = l.get('status', 'new')
        status_counts[status] = status_counts.get(status, 0) + 1

    return jsonify({
        'leads': enriched_leads,
        'total': len(enriched_leads),
        'total_value': total_value,
        'status_counts': status_counts,
    })


@app.route('/api/saved-leads', methods=['POST'])
def save_lead():
    """POST /api/saved-leads - Save a lead for the logged-in user.

    PRO FEATURE: Only Pro users can save leads.
    """
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Login required'}), 401

    # Save Lead is a Pro feature
    if not is_pro(user):
        return jsonify({
            'error': 'Save Lead is a Pro feature',
            'upgrade_url': '/pricing'
        }), 403

    data = request.get_json()
    if not data or not data.get('permit_id'):
        return jsonify({'error': 'permit_id required'}), 400

    all_leads = load_saved_leads()

    # Check if already saved
    existing = next((l for l in all_leads if l['user_email'] == user['email'] and l['permit_id'] == data['permit_id']), None)
    if existing:
        return jsonify({'error': 'Lead already saved'}), 409

    new_lead = {
        'permit_id': data['permit_id'],
        'user_email': user['email'],
        'status': 'new',
        'notes': '',
        'date_saved': datetime.now().isoformat(),
    }

    all_leads.append(new_lead)
    save_saved_leads(all_leads)

    # Track lead save event
    analytics.track_event('lead_save', event_data={
        'permit_id': data['permit_id'],
        'permit_value': data.get('permit_value', 0)
    })

    return jsonify({'message': 'Lead saved', 'lead': new_lead}), 201


@app.route('/api/saved-leads/<permit_id>', methods=['PUT'])
def update_saved_lead(permit_id):
    """PUT /api/saved-leads/<permit_id> - Update status/notes for a saved lead."""
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Login required'}), 401

    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400

    all_leads = load_saved_leads()
    lead = next((l for l in all_leads if l['user_email'] == user['email'] and l['permit_id'] == permit_id), None)

    if not lead:
        return jsonify({'error': 'Lead not found'}), 404

    # Update fields
    if 'status' in data:
        lead['status'] = data['status']
    if 'notes' in data:
        lead['notes'] = data['notes']
    lead['updated_at'] = datetime.now().isoformat()

    save_saved_leads(all_leads)

    return jsonify({'message': 'Lead updated', 'lead': lead})


@app.route('/api/saved-leads/<permit_id>', methods=['DELETE'])
def delete_saved_lead(permit_id):
    """DELETE /api/saved-leads/<permit_id> - Remove a saved lead."""
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Login required'}), 401

    all_leads = load_saved_leads()
    original_count = len(all_leads)
    all_leads = [l for l in all_leads if not (l['user_email'] == user['email'] and l['permit_id'] == permit_id)]

    if len(all_leads) == original_count:
        return jsonify({'error': 'Lead not found'}), 404

    save_saved_leads(all_leads)

    return jsonify({'message': 'Lead removed'})


@app.route('/api/saved-leads/export')
def export_saved_leads():
    """GET /api/saved-leads/export - Export saved leads as CSV.

    PRO FEATURE: Only Pro users can export.
    """
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Login required'}), 401

    if not is_pro(user):
        return jsonify({
            'error': 'Export is a Pro feature',
            'upgrade_url': '/pricing'
        }), 403

    user_leads = get_user_saved_leads(user['email'])
    # V12.51: SQL-backed
    all_permits, _ = permitdb.query_permits(page=1, per_page=100000)
    permits = add_lead_scores(all_permits)
    permit_map = {p.get('permit_number'): p for p in permits}

    if not user_leads:
        return "No saved leads to export", 404

    headers = ['address', 'city', 'state', 'zip', 'trade_category', 'estimated_cost',
               'permit_status', 'lifecycle_stage', 'filing_date', 'contact_name', 'contact_phone', 'description',
               'lead_score', 'lead_quality', 'crm_status', 'notes', 'date_saved']

    lines = [','.join(headers)]
    for lead in user_leads:
        permit = permit_map.get(lead.get('permit_id'), {})
        row = [
            str(permit.get('address', '')).replace(',', ';').replace('"', "'"),
            str(permit.get('city', '')),
            str(permit.get('state', '')),
            str(permit.get('zip', '')),
            str(permit.get('trade_category', '')),
            str(permit.get('estimated_cost', '')),
            str(permit.get('status', '')),
            get_lifecycle_label(permit),
            str(permit.get('filing_date', '')),
            str(permit.get('contact_name', '')).replace(',', ';'),
            str(permit.get('contact_phone', '')),
            str(permit.get('description', '')).replace(',', ';').replace('"', "'").replace('\n', ' ')[:150],
            str(permit.get('lead_score', '')),
            str(permit.get('lead_quality', '')),
            str(lead.get('status', '')),
            str(lead.get('notes', '')).replace(',', ';').replace('"', "'").replace('\n', ' ')[:100],
            str(lead.get('date_saved', ''))[:10],
        ]
        lines.append(','.join(f'"{v}"' for v in row))

    csv_content = '\n'.join(lines)

    return Response(
        csv_content,
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename=permitgrab_my_leads_{datetime.now().strftime("%Y%m%d")}.csv'}
    )


# ===========================
# SAVED SEARCHES API
# ===========================

SAVED_SEARCHES_FILE = os.path.join(DATA_DIR, 'saved_searches.json')

def load_saved_searches():
    """Load all saved searches from JSON file."""
    if os.path.exists(SAVED_SEARCHES_FILE):
        try:
            with open(SAVED_SEARCHES_FILE, 'r') as f:
                return json.load(f)
        except:
            return []
    return []

def save_saved_searches(searches):
    """Save all saved searches to JSON file."""
    with open(SAVED_SEARCHES_FILE, 'w') as f:
        json.dump(searches, f, indent=2)

def get_user_saved_searches(email):
    """Get saved searches for a specific user."""
    all_searches = load_saved_searches()
    return [s for s in all_searches if s.get('user_email') == email]

@app.route('/api/saved-searches', methods=['GET'])
def get_saved_searches():
    """GET /api/saved-searches - Get user's saved searches."""
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Login required'}), 401

    searches = get_user_saved_searches(user['email'])
    return jsonify({'searches': searches})

@app.route('/api/saved-searches', methods=['POST'])
def create_saved_search():
    """POST /api/saved-searches - Create a new saved search.

    PRO FEATURE: Only Pro users can save searches.
    """
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Login required'}), 401

    # Save Search is a Pro feature
    if not is_pro(user):
        return jsonify({
            'error': 'Save Search is a Pro feature',
            'upgrade_url': '/pricing'
        }), 403

    data = request.get_json()
    if not data:
        return jsonify({'error': 'Missing search data'}), 400

    # Create the saved search object
    search = {
        'id': str(uuid.uuid4()),
        'user_email': user['email'],
        'city': data.get('city', ''),
        'trade': data.get('trade', ''),
        'value_tier': data.get('value_tier', ''),
        'status': data.get('status', ''),
        'quality': data.get('quality', ''),
        'search_text': data.get('search_text', ''),
        'daily_alerts': True,  # Default to daily alerts enabled
        'created_at': datetime.now().isoformat(),
    }

    # Build a human-readable name for the search
    parts = []
    if search['city']:
        parts.append(search['city'])
    if search['trade']:
        parts.append(search['trade'])
    if search['value_tier']:
        parts.append(f"Value: {search['value_tier']}")
    search['name'] = ' | '.join(parts) if parts else 'All Permits'

    all_searches = load_saved_searches()
    all_searches.append(search)
    save_saved_searches(all_searches)

    return jsonify({'message': 'Search saved', 'search': search})

@app.route('/api/saved-searches/<search_id>', methods=['DELETE'])
def delete_saved_search(search_id):
    """DELETE /api/saved-searches/<id> - Delete a saved search."""
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Login required'}), 401

    all_searches = load_saved_searches()
    original_count = len(all_searches)
    all_searches = [s for s in all_searches if not (s['user_email'] == user['email'] and s['id'] == search_id)]

    if len(all_searches) == original_count:
        return jsonify({'error': 'Search not found'}), 404

    save_saved_searches(all_searches)
    return jsonify({'message': 'Search deleted'})

@app.route('/api/saved-searches/<search_id>', methods=['PUT'])
def update_saved_search(search_id):
    """PUT /api/saved-searches/<id> - Update a saved search (e.g., toggle alerts)."""
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Login required'}), 401

    data = request.get_json()
    all_searches = load_saved_searches()

    for search in all_searches:
        if search['id'] == search_id and search['user_email'] == user['email']:
            if 'daily_alerts' in data:
                search['daily_alerts'] = data['daily_alerts']
            if 'name' in data:
                search['name'] = data['name']
            save_saved_searches(all_searches)
            return jsonify({'message': 'Search updated', 'search': search})

    return jsonify({'error': 'Search not found'}), 404


# ===========================
# PERMIT HISTORY API
# ===========================

@app.route('/api/permit-history/<path:address>')
def api_permit_history(address):
    """
    GET /api/permit-history/<address>
    Returns historical permits at the given address.
    """
    history = load_permit_history()

    # Normalize the input address for lookup
    normalized_addr = normalize_address_for_lookup(address)

    if not normalized_addr:
        return jsonify({'error': 'Address required'}), 400

    # Look up in history index
    entry = history.get(normalized_addr)

    if not entry:
        # Try partial match
        for key, value in history.items():
            if normalized_addr in key or key in normalized_addr:
                entry = value
                break

    if not entry:
        return jsonify({
            'address': address,
            'permits': [],
            'permit_count': 0,
            'is_repeat_renovator': False,
        })

    permit_count = len(entry.get('permits', []))

    return jsonify({
        'address': entry.get('address', address),
        'city': entry.get('city', ''),
        'state': entry.get('state', ''),
        'permits': entry.get('permits', []),
        'permit_count': permit_count,
        'is_repeat_renovator': permit_count >= 3,
    })


# ===========================
# CODE VIOLATIONS API
# ===========================

@app.route('/api/violations')
def api_violations():
    """
    GET /api/violations
    Query params: city
    Returns recent code violations, flagged as pre-leads if no matching permit.
    """
    violations = load_violations()
    # V12.51: SQL-backed permits
    permits, _ = permitdb.query_permits(page=1, per_page=100000)

    city = request.args.get('city', '')

    if city:
        violations = [v for v in violations if v.get('city') == city]

    # Build set of permit addresses for cross-reference
    permit_addresses = set()
    for p in permits:
        addr = normalize_address_for_lookup(p.get('address', ''))
        if addr:
            permit_addresses.add(addr)

    # Mark violations as pre-leads if no matching permit
    for v in violations:
        v_addr = normalize_address_for_lookup(v.get('address', ''))
        v['has_matching_permit'] = v_addr in permit_addresses
        v['is_pre_lead'] = not v['has_matching_permit']

    # Sort: pre-leads first, then by date
    violations.sort(key=lambda x: (not x.get('is_pre_lead', False), x.get('violation_date', '') or ''), reverse=True)

    # Stats
    pre_lead_count = sum(1 for v in violations if v.get('is_pre_lead'))
    cities = sorted(set(v.get('city', '') for v in load_violations() if v.get('city')))

    return jsonify({
        'violations': violations[:200],  # Limit response size
        'total': len(violations),
        'pre_lead_count': pre_lead_count,
        'cities': cities,
    })


@app.route('/api/violations/<path:address>')
def api_violations_by_address(address):
    """
    GET /api/violations/<address>
    Returns violations at a specific address.
    """
    violations = load_violations()
    normalized_addr = normalize_address_for_lookup(address)

    if not normalized_addr:
        return jsonify({'violations': [], 'count': 0})

    # Find violations at this address
    matching = []
    for v in violations:
        v_addr = normalize_address_for_lookup(v.get('address', ''))
        if normalized_addr == v_addr or normalized_addr in v_addr or v_addr in normalized_addr:
            matching.append(v)

    return jsonify({
        'violations': matching,
        'count': len(matching),
        'has_active_violations': any(v.get('status', '').lower() in ('open', 'active', 'pending') for v in matching),
    })


# ===========================
# CONTRACTOR INTELLIGENCE API
# ===========================

@app.route('/api/contractors')
def api_contractors():
    """
    GET /api/contractors
    Query params: city, search, sort_by, sort_order, page, per_page
    Returns aggregated contractor data from permits.
    V12.51: SQL-backed, V13.5: Added error handling
    """
    try:
        city = request.args.get('city', '')
        permits, _ = permitdb.query_permits(city=city or None, page=1, per_page=100000)

        # Aggregate by contractor name
        contractors = {}
        for p in permits:
            name = p.get('contact_name', '').strip()
            if not name or name.lower() in ('n/a', 'unknown', 'none', ''):
                continue

            if name not in contractors:
                contractors[name] = {
                    'name': name,
                    'total_permits': 0,
                    'total_value': 0,
                    'cities': set(),
                    'trades': {},
                    'most_recent_date': '',
                    'permits': [],
                }

            contractors[name]['total_permits'] += 1
            contractors[name]['total_value'] += p.get('estimated_cost', 0) or 0
            contractors[name]['cities'].add(p.get('city', ''))

            trade = p.get('trade_category', 'Other')
            contractors[name]['trades'][trade] = contractors[name]['trades'].get(trade, 0) + 1

            filing_date = p.get('filing_date', '')
            if filing_date > contractors[name]['most_recent_date']:
                contractors[name]['most_recent_date'] = filing_date

            contractors[name]['permits'].append(p.get('permit_number'))

        # Convert to list and determine primary trade
        contractor_list = []
        for name, data in contractors.items():
            primary_trade = max(data['trades'].items(), key=lambda x: x[1])[0] if data['trades'] else 'Unknown'
            contractor_list.append({
                'name': data['name'],
                'total_permits': data['total_permits'],
                'total_value': data['total_value'],
                'cities': sorted(list(data['cities'])),
                'city_count': len(data['cities']),
                'primary_trade': primary_trade,
                'most_recent_date': data['most_recent_date'],
                'permit_ids': data['permits'][:50],
            })

        # Search filter
        search = request.args.get('search', '').lower()
        if search:
            contractor_list = [c for c in contractor_list if search in c['name'].lower()]

        # Sorting
        sort_by = request.args.get('sort_by', 'total_permits')
        sort_order = request.args.get('sort_order', 'desc')
        reverse = sort_order == 'desc'

        if sort_by == 'name':
            contractor_list.sort(key=lambda x: x['name'].lower(), reverse=reverse)
        elif sort_by == 'total_value':
            contractor_list.sort(key=lambda x: x['total_value'], reverse=reverse)
        elif sort_by == 'most_recent_date':
            contractor_list.sort(key=lambda x: x['most_recent_date'] or '', reverse=reverse)
        else:
            contractor_list.sort(key=lambda x: x['total_permits'], reverse=reverse)

        # Pagination
        page = int(request.args.get('page', 1))
        per_page = int(request.args.get('per_page', 50))
        total = len(contractor_list)
        start = (page - 1) * per_page
        page_contractors = contractor_list[start:start + per_page]

        return jsonify({
            'contractors': page_contractors,
            'total': total,
            'page': page,
            'per_page': per_page,
            'total_pages': (total + per_page - 1) // per_page,
        })
    except Exception as e:
        print(f"[ERROR] /api/contractors failed: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e), 'contractors': [], 'total': 0}), 500


@app.route('/api/contractors/<path:name>')
def api_contractor_detail(name):
    """
    GET /api/contractors/<name>
    Returns all permits for a specific contractor.
    V12.51: SQL-backed
    """
    permits, _ = permitdb.query_permits(page=1, per_page=100000)
    permits = add_lead_scores(permits)

    # Find permits by contractor name (case-insensitive)
    contractor_permits = [p for p in permits if p.get('contact_name', '').lower() == name.lower()]

    if not contractor_permits:
        return jsonify({'error': 'Contractor not found'}), 404

    # Calculate stats
    total_value = sum(p.get('estimated_cost', 0) or 0 for p in contractor_permits)
    cities = sorted(set(p.get('city', '') for p in contractor_permits))
    trades = {}
    for p in contractor_permits:
        trade = p.get('trade_category', 'Other')
        trades[trade] = trades.get(trade, 0) + 1

    return jsonify({
        'name': name,
        'permits': contractor_permits,
        'total_permits': len(contractor_permits),
        'total_value': total_value,
        'cities': cities,
        'trade_breakdown': trades,
    })


@app.route('/api/contractors/top')
def api_top_contractors():
    """
    GET /api/contractors/top
    Query params: city, limit
    Returns top contractors by permit volume.
    V12.51: SQL-backed
    """
    city = request.args.get('city', '')
    permits, _ = permitdb.query_permits(city=city or None, page=1, per_page=100000)

    limit = int(request.args.get('limit', 5))

    # V12.55: Aggregate by contractor with improved junk name filter
    JUNK_NAMES = {'n/a', 'unknown', 'none', 'na', 'tbd', 'tba', 'pending',
                  'various', 'multiple', 'owner', 'owner/builder', 'self',
                  'homeowner', 'not provided', 'not applicable', 'see plans',
                  'not listed', 'not available', 'exempt', '---', '--', '-'}

    contractors = {}
    for p in permits:
        name = (p.get('contact_name') or '').strip()
        if not name:
            continue
        name_lower = name.lower()

        # Skip exact junk matches
        if name_lower in JUNK_NAMES:
            continue

        # Skip names that START with common junk prefixes
        if name_lower.startswith(('none ', 'n/a ', 'unknown ', 'tbd ', 'owner ')):
            continue

        # Skip very short names (likely data artifacts)
        if len(name) < 3:
            continue

        if name not in contractors:
            contractors[name] = {'name': name, 'permits': 0, 'value': 0}

        contractors[name]['permits'] += 1
        contractors[name]['value'] += p.get('estimated_cost', 0) or 0

    # Sort by permit count
    top_list = sorted(contractors.values(), key=lambda x: x['permits'], reverse=True)[:limit]

    return jsonify({
        'top_contractors': top_list,
        'city': city or 'All Cities',
    })


# ===========================
# V79: BLOG SYSTEM
# ===========================

@app.route('/blog')
def blog_index():
    """V79: Blog index page listing all posts by category."""
    footer_cities = get_cities_with_data()

    # Group posts by category
    categories = {
        'permit-costs': {
            'title': 'Permit Cost Guides',
            'posts': get_blog_posts_by_category('permit-costs')
        },
        'contractor-leads': {
            'title': 'Finding Construction Leads',
            'posts': get_blog_posts_by_category('contractor-leads')
        },
        'trade-guides': {
            'title': 'Trade-Specific Guides',
            'posts': get_blog_posts_by_category('trade-guides')
        }
    }

    return render_template('blog_index.html',
                           categories=categories,
                           all_posts=BLOG_POSTS,
                           footer_cities=footer_cities)


@app.route('/blog/<slug>')
def blog_post(slug):
    """V79: Individual blog post page."""
    post = next((p for p in BLOG_POSTS if p['slug'] == slug), None)
    if not post:
        abort(404)

    footer_cities = get_cities_with_data()
    related_posts = get_related_posts(slug, limit=3)

    return render_template('blog_post.html',
                           post=post,
                           related_posts=related_posts,
                           footer_cities=footer_cities)


@app.route('/contractors')
def contractors_page():
    """Render the Contractors Intelligence page."""
    footer_cities = get_cities_with_data()
    return render_template('contractors.html', footer_cities=footer_cities)


@app.route('/pricing')
def pricing_page():
    """Render the Pricing page. V12.51: SQL-backed"""
    user = get_current_user()
    cities = get_all_cities_info()
    city_count = get_total_city_count_auto()  # V31: Active cities only
    footer_cities = get_cities_with_data()
    # V12.51: Get permit count from SQLite
    stats = permitdb.get_permit_stats()
    permit_count = stats['total_permits']
    return render_template('pricing.html', user=user, cities=cities, city_count=city_count, footer_cities=footer_cities, permit_count=permit_count)


@app.route('/signup')
def signup_page():
    """Render the Sign Up page."""
    # Redirect if already logged in
    if get_current_user():
        return redirect('/')
    footer_cities = get_cities_with_data()
    return render_template('signup.html', footer_cities=footer_cities)


@app.route('/login')
def login_page():
    """Render the Login page."""
    # Redirect if already logged in
    if get_current_user():
        return redirect('/')
    footer_cities = get_cities_with_data()
    # V13.7: Handle redirect messages (e.g., from /dashboard redirect)
    message = request.args.get('message', '')
    login_message = None
    if message == 'login_required':
        login_message = 'Please log in to access your dashboard.'
    return render_template('login.html', footer_cities=footer_cities, login_message=login_message)


# ===========================
# PASSWORD RESET
# ===========================
PASSWORD_RESET_FILE = os.path.join(DATA_DIR, 'password_reset_tokens.json')


def load_reset_tokens():
    """Load password reset tokens from JSON file."""
    if os.path.exists(PASSWORD_RESET_FILE):
        with open(PASSWORD_RESET_FILE) as f:
            return json.load(f)
    return {}


def save_reset_tokens(tokens):
    """Save password reset tokens to JSON file."""
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(PASSWORD_RESET_FILE, 'w') as f:
        json.dump(tokens, f, indent=2)


def generate_reset_token():
    """Generate a secure random token for password reset."""
    return secrets.token_urlsafe(32)


def cleanup_expired_tokens():
    """Remove expired reset tokens."""
    tokens = load_reset_tokens()
    now = datetime.now().isoformat()
    valid_tokens = {k: v for k, v in tokens.items() if v.get('expires', '') > now}
    save_reset_tokens(valid_tokens)
    return valid_tokens


@app.route('/forgot-password')
def forgot_password_page():
    """Render the Forgot Password page."""
    footer_cities = get_cities_with_data()
    return render_template('forgot_password.html', footer_cities=footer_cities)


@app.route('/api/forgot-password', methods=['POST'])
@limiter.limit("5 per minute")
def api_forgot_password():
    """
    POST /api/forgot-password - Request a password reset email.
    Body: { email: string }
    """
    data = request.get_json()
    if not data or not data.get('email'):
        return jsonify({'error': 'Email is required'}), 400

    email = data['email'].lower().strip()

    # Check if user exists
    users = load_users()
    user = next((u for u in users if u['email'].lower() == email), None)

    # Always return success to prevent email enumeration
    if not user:
        return jsonify({'success': True, 'message': 'If that email exists, a reset link has been sent.'})

    # Generate token with 1-hour expiry
    token = generate_reset_token()
    expires = (datetime.now() + timedelta(hours=1)).isoformat()

    # Save token
    tokens = load_reset_tokens()
    tokens[token] = {
        'email': email,
        'expires': expires,
        'used': False
    }
    save_reset_tokens(tokens)

    # Send reset email
    reset_url = f"https://permitgrab.com/reset-password/{token}"
    html_body = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; color: #333; }}
            .container {{ max-width: 600px; margin: 0 auto; padding: 40px 20px; }}
            .logo {{ font-size: 24px; font-weight: 700; color: #111; margin-bottom: 24px; }}
            .logo span {{ color: #f97316; }}
            .btn {{ display: inline-block; background: #2563eb; color: white; padding: 14px 28px; border-radius: 8px; text-decoration: none; font-weight: 600; }}
            .footer {{ margin-top: 32px; font-size: 13px; color: #666; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="logo">Permit<span>Grab</span></div>
            <h2>Reset Your Password</h2>
            <p>We received a request to reset your password. Click the button below to create a new password:</p>
            <p><a href="{reset_url}" class="btn">Reset Password</a></p>
            <p>Or copy and paste this link into your browser:</p>
            <p style="word-break: break-all; color: #2563eb;">{reset_url}</p>
            <p><strong>This link expires in 1 hour.</strong></p>
            <p>If you didn't request this, you can safely ignore this email.</p>
            <div class="footer">
                <p>&copy; 2026 PermitGrab. All rights reserved.</p>
            </div>
        </div>
    </body>
    </html>
    """

    try:
        from email_alerts import send_email
        send_email(email, "Reset Your PermitGrab Password", html_body)
    except Exception as e:
        print(f"Failed to send password reset email: {e}")
        # Still return success to prevent email enumeration

    return jsonify({'success': True, 'message': 'If that email exists, a reset link has been sent.'})


@app.route('/reset-password/<token>')
def reset_password_page(token):
    """Render the Reset Password page."""
    # Validate token
    cleanup_expired_tokens()
    tokens = load_reset_tokens()

    if token not in tokens:
        return render_template('reset_password.html', error='Invalid or expired reset link. Please request a new one.', token=None)

    token_data = tokens[token]
    if token_data.get('used'):
        return render_template('reset_password.html', error='This reset link has already been used.', token=None)

    now = datetime.now().isoformat()
    if token_data.get('expires', '') < now:
        return render_template('reset_password.html', error='This reset link has expired. Please request a new one.', token=None)

    footer_cities = get_cities_with_data()
    return render_template('reset_password.html', token=token, error=None, footer_cities=footer_cities)


@app.route('/api/reset-password', methods=['POST'])
@limiter.limit("10 per minute")
def api_reset_password():
    """
    POST /api/reset-password - Reset password with valid token.
    Body: { token: string, password: string }
    """
    data = request.get_json()
    if not data or not data.get('token') or not data.get('password'):
        return jsonify({'error': 'Token and password are required'}), 400

    token = data['token']
    new_password = data['password']

    # Validate password length
    if len(new_password) < 8:
        return jsonify({'error': 'Password must be at least 8 characters'}), 400

    # Validate token
    cleanup_expired_tokens()
    tokens = load_reset_tokens()

    if token not in tokens:
        return jsonify({'error': 'Invalid or expired reset link'}), 400

    token_data = tokens[token]
    if token_data.get('used'):
        return jsonify({'error': 'This reset link has already been used'}), 400

    now = datetime.now().isoformat()
    if token_data.get('expires', '') < now:
        return jsonify({'error': 'This reset link has expired'}), 400

    email = token_data['email']

    # Update user password (V7: direct database update)
    user = find_user_by_email(email)
    if not user:
        return jsonify({'error': 'User not found'}), 400

    user.password_hash = generate_password_hash(new_password)
    db.session.commit()

    # Mark token as used
    tokens[token]['used'] = True
    save_reset_tokens(tokens)

    return jsonify({'success': True, 'message': 'Password has been reset. You can now log in.'})


@app.route('/get-alerts')
def get_alerts_page():
    """Render the Get Alerts page."""
    cities = get_cities_with_data()  # Only show cities with data
    footer_cities = cities
    return render_template('get_alerts.html', cities=cities, footer_cities=footer_cities)


@app.route('/privacy')
def privacy_page():
    """Render the Privacy Policy page."""
    footer_cities = get_cities_with_data()
    return render_template('privacy.html', footer_cities=footer_cities)


@app.route('/terms')
def terms_page():
    """Render the Terms of Service page."""
    footer_cities = get_cities_with_data()
    return render_template('terms.html', footer_cities=footer_cities)


@app.route('/about')
def about_page():
    """Render the About page. V13.6: Pass city_count for consistency."""
    footer_cities = get_cities_with_data()
    city_count = get_total_city_count_auto()
    return render_template('about.html', footer_cities=footer_cities, city_count=city_count)


@app.route('/stats')
def stats_page():
    """V12.51: Render building permit statistics page (SQL-backed)."""
    conn = permitdb.get_connection()
    footer_cities = get_cities_with_data()

    # Get totals from SQLite
    stats = permitdb.get_permit_stats()
    total_permits = stats['total_permits']
    total_value = stats['total_value']
    high_value_count = stats['high_value_count']

    # Top cities by permit count
    top_cities_rows = conn.execute("""
        SELECT city, state, COUNT(*) as permit_count, SUM(COALESCE(estimated_cost, 0)) as total_value
        FROM permits WHERE city IS NOT NULL AND city != ''
        GROUP BY city, state ORDER BY permit_count DESC LIMIT 10
    """).fetchall()
    top_cities = []
    for row in top_cities_rows:
        top_cities.append({
            'name': row['city'],
            'state': row['state'] or '',
            'slug': row['city'].lower().replace(' ', '-'),
            'permit_count': row['permit_count'],
            'total_value': row['total_value'] or 0,
            'avg_value': (row['total_value'] or 0) / row['permit_count'] if row['permit_count'] > 0 else 0
        })

    # Trade breakdown
    trade_rows = conn.execute("""
        SELECT trade_category, COUNT(*) as cnt FROM permits
        WHERE trade_category IS NOT NULL AND trade_category != ''
        GROUP BY trade_category ORDER BY cnt DESC
    """).fetchall()
    trade_breakdown = [
        {'name': row['trade_category'], 'count': row['cnt'],
         'percentage': (row['cnt'] / total_permits * 100) if total_permits > 0 else 0}
        for row in trade_rows
    ]

    return render_template('stats.html',
                           total_permits=total_permits,
                           total_value=total_value,
                           high_value_count=high_value_count,
                           city_count=get_total_city_count_auto(),
                           top_cities=top_cities,
                           trade_breakdown=trade_breakdown,
                           last_updated=datetime.now().strftime('%Y-%m-%d'),
                           footer_cities=footer_cities)


@app.route('/map')
def map_page():
    """V12.26: Interactive permit heat map with Leaflet.js."""
    user = get_current_user()
    is_pro = user and user.plan == 'pro'
    cities = get_all_cities_info()
    footer_cities = get_cities_with_data()
    city_count = get_total_city_count_auto()  # V13.9: Pass for dynamic meta desc
    return render_template('map.html',
                           is_pro=is_pro,
                           cities=cities,
                           footer_cities=footer_cities,
                           city_count=city_count)


@app.route('/contact')
def contact_page():
    """Render the Contact page."""
    footer_cities = get_cities_with_data()
    return render_template('contact.html', footer_cities=footer_cities)


@app.route('/api/contact', methods=['POST'])
def api_contact():
    """Handle contact form submissions."""
    data = request.get_json()
    if not data or not data.get('email') or not data.get('message'):
        return jsonify({'error': 'Email and message required'}), 400

    # Store contact message (in production, would email this)
    contact_file = os.path.join(DATA_DIR, 'contact_messages.json')
    messages = []
    if os.path.exists(contact_file):
        with open(contact_file) as f:
            messages = json.load(f)

    messages.append({
        'name': data.get('name', ''),
        'email': data['email'],
        'subject': data.get('subject', 'general'),
        'message': data['message'],
        'timestamp': datetime.now().isoformat()
    })

    with open(contact_file, 'w') as f:
        json.dump(messages, f, indent=2)

    return jsonify({'success': True})


@app.route('/onboarding')
def onboarding_page():
    """Render the post-signup onboarding flow."""
    # Require login
    user = get_current_user()
    if not user:
        return redirect('/signup')
    # V9 Fix 10: Only show cities with actual permit data (not all 300+ cities)
    cities = get_cities_with_data()
    trades = get_all_trades()
    return render_template('onboarding.html', cities=cities, trades=trades)


@app.route('/api/onboarding', methods=['POST'])
def api_onboarding():
    """Save user onboarding preferences (city, trade, alerts)."""
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Not logged in'}), 401

    data = request.get_json()
    if not data:
        return jsonify({'error': 'Invalid request'}), 400

    city = data.get('city', '')
    trade = data.get('trade', '')
    daily_alerts = data.get('daily_alerts', False)

    # Update user preferences (V7: direct database update)
    user_obj = find_user_by_email(user['email'])
    if user_obj:
        user_obj.city = city
        user_obj.trade = trade
        user_obj.daily_alerts = daily_alerts
        user_obj.onboarding_completed = True

        # V12.53: Update digest settings in User model instead of subscribers.json
        if daily_alerts and city:
            cities = json.loads(user_obj.digest_cities or '[]')
            if city not in cities:
                cities.append(city)
            user_obj.digest_cities = json.dumps(cities)
            user_obj.digest_active = True
        db.session.commit()

    # Track onboarding complete event
    analytics.track_event('onboarding_complete', event_data={
        'city': city,
        'trade': trade,
        'daily_alerts': daily_alerts
    }, city_filter=city, trade_filter=trade)

    return jsonify({'success': True})


@app.route('/register')
def register_redirect():
    """Redirect /register to /signup."""
    return redirect('/signup', code=301)


# ===========================
# TREND ANALYTICS API
# ===========================

@app.route('/api/analytics/volume')
def api_analytics_volume():
    """
    GET /api/analytics/volume
    Query params: city, weeks (default 12)
    Returns weekly permit counts for trend analysis.
    V12.51: Uses SQLite for efficient aggregation.
    """
    city = request.args.get('city', '')
    weeks = int(request.args.get('weeks', 12))

    conn = permitdb.get_connection()
    now = datetime.now()
    cutoff = (now - timedelta(weeks=weeks)).strftime('%Y-%m-%d')

    # Build query with optional city filter
    if city:
        cursor = conn.execute("""
            SELECT filing_date, COUNT(*) as cnt
            FROM permits
            WHERE city = ? AND filing_date >= ? AND filing_date IS NOT NULL
            GROUP BY filing_date
        """, (city, cutoff))
    else:
        cursor = conn.execute("""
            SELECT filing_date, COUNT(*) as cnt
            FROM permits
            WHERE filing_date >= ? AND filing_date IS NOT NULL
            GROUP BY filing_date
        """, (cutoff,))

    # Aggregate by week
    weekly_counts = {}
    for i in range(weeks):
        week_start = now - timedelta(weeks=i+1)
        week_key = week_start.strftime('%Y-%m-%d')
        weekly_counts[week_key] = 0

    for row in cursor:
        filing_date = row['filing_date']
        if not filing_date:
            continue
        try:
            filed = datetime.strptime(filing_date[:10], '%Y-%m-%d')
            weeks_ago = (now - filed).days // 7
            if 0 <= weeks_ago < weeks:
                week_start = now - timedelta(weeks=weeks_ago+1)
                week_key = week_start.strftime('%Y-%m-%d')
                if week_key in weekly_counts:
                    weekly_counts[week_key] += row['cnt']
        except (ValueError, TypeError):
            continue

    # Convert to sorted list
    volume_data = sorted(weekly_counts.items())

    # Calculate trend
    if len(volume_data) >= 2:
        recent_avg = sum(v for _, v in volume_data[-4:]) / min(4, len(volume_data))
        older_avg = sum(v for _, v in volume_data[:4]) / min(4, len(volume_data))
        if older_avg > 0:
            trend_pct = ((recent_avg - older_avg) / older_avg) * 100
        else:
            trend_pct = 0
        trend_direction = 'up' if trend_pct > 0 else 'down' if trend_pct < 0 else 'flat'
    else:
        trend_pct = 0
        trend_direction = 'flat'

    return jsonify({
        'volume': [{'week': k, 'count': v} for k, v in volume_data],
        'total': sum(v for _, v in volume_data),
        'trend_percentage': round(trend_pct, 1),
        'trend_direction': trend_direction,
        'city': city or 'All Cities',
    })


@app.route('/api/analytics/trades')
def api_analytics_trades():
    """
    GET /api/analytics/trades
    Query params: city
    Returns trade breakdown for the selected city.
    V12.51: Uses SQLite for efficient aggregation.
    """
    city = request.args.get('city', '')
    conn = permitdb.get_connection()

    if city:
        cursor = conn.execute("""
            SELECT COALESCE(trade_category, 'Other') as trade, COUNT(*) as cnt
            FROM permits
            WHERE city = ?
            GROUP BY trade_category
            ORDER BY cnt DESC
        """, (city,))
        total_row = conn.execute("SELECT COUNT(*) FROM permits WHERE city = ?", (city,)).fetchone()
    else:
        cursor = conn.execute("""
            SELECT COALESCE(trade_category, 'Other') as trade, COUNT(*) as cnt
            FROM permits
            GROUP BY trade_category
            ORDER BY cnt DESC
        """)
        total_row = conn.execute("SELECT COUNT(*) FROM permits").fetchone()

    trades = [{'trade': row['trade'] or 'Other', 'count': row['cnt']} for row in cursor]
    total = total_row[0] if total_row else 0

    return jsonify({
        'trades': trades,
        'total': total,
        'city': city or 'All Cities',
    })


@app.route('/api/analytics/values')
def api_analytics_values():
    """
    GET /api/analytics/values
    Query params: city, weeks (default 12)
    Returns weekly average project values.
    V12.51: Uses SQLite for efficient aggregation.
    """
    city = request.args.get('city', '')
    weeks = int(request.args.get('weeks', 12))

    conn = permitdb.get_connection()
    now = datetime.now()
    cutoff = (now - timedelta(weeks=weeks)).strftime('%Y-%m-%d')

    # Build query with optional city filter
    if city:
        cursor = conn.execute("""
            SELECT filing_date, SUM(estimated_cost) as total_value, COUNT(*) as cnt
            FROM permits
            WHERE city = ? AND filing_date >= ? AND filing_date IS NOT NULL
                  AND estimated_cost > 0
            GROUP BY filing_date
        """, (city, cutoff))
    else:
        cursor = conn.execute("""
            SELECT filing_date, SUM(estimated_cost) as total_value, COUNT(*) as cnt
            FROM permits
            WHERE filing_date >= ? AND filing_date IS NOT NULL
                  AND estimated_cost > 0
            GROUP BY filing_date
        """, (cutoff,))

    # Initialize week buckets
    weekly_values = {}
    weekly_counts = {}
    for i in range(weeks):
        week_start = now - timedelta(weeks=i+1)
        week_key = week_start.strftime('%Y-%m-%d')
        weekly_values[week_key] = 0
        weekly_counts[week_key] = 0

    # Aggregate by week
    for row in cursor:
        filing_date = row['filing_date']
        if not filing_date:
            continue
        try:
            filed = datetime.strptime(filing_date[:10], '%Y-%m-%d')
            weeks_ago = (now - filed).days // 7
            if 0 <= weeks_ago < weeks:
                week_start = now - timedelta(weeks=weeks_ago+1)
                week_key = week_start.strftime('%Y-%m-%d')
                if week_key in weekly_values:
                    weekly_values[week_key] += row['total_value'] or 0
                    weekly_counts[week_key] += row['cnt']
        except (ValueError, TypeError):
            continue

    # Calculate averages
    value_data = []
    for week_key in sorted(weekly_values.keys()):
        count = weekly_counts[week_key]
        avg = weekly_values[week_key] / count if count > 0 else 0
        value_data.append({'week': week_key, 'average_value': round(avg, 2), 'count': count})

    # Calculate trend
    recent_values = [d['average_value'] for d in value_data[-4:] if d['average_value'] > 0]
    older_values = [d['average_value'] for d in value_data[:4] if d['average_value'] > 0]

    if recent_values and older_values:
        recent_avg = sum(recent_values) / len(recent_values)
        older_avg = sum(older_values) / len(older_values)
        if older_avg > 0:
            trend_pct = ((recent_avg - older_avg) / older_avg) * 100
        else:
            trend_pct = 0
        trend_direction = 'up' if trend_pct > 0 else 'down' if trend_pct < 0 else 'flat'
    else:
        trend_pct = 0
        trend_direction = 'flat'

    return jsonify({
        'values': value_data,
        'trend_percentage': round(trend_pct, 1),
        'trend_direction': trend_direction,
        'city': city or 'All Cities',
    })


@app.route('/analytics')
def analytics_page():
    """Render the Analytics page (Pro users only)."""
    user = get_current_user()

    # Check if user has Pro plan using centralized utility
    if not is_pro(user):
        footer_cities = get_cities_with_data()
        return render_template('upgrade_gate.html',
            title="Analytics",
            icon="📊",
            heading="Analytics is a Pro Feature",
            description="Upgrade to Professional to access trend analytics, market insights, and contractor intelligence.",
            footer_cities=footer_cities
        )

    footer_cities = get_cities_with_data()
    return render_template('analytics.html', user=user, footer_cities=footer_cities)


# ===========================
# PRE-CONSTRUCTION SIGNALS API
# ===========================

SIGNAL_TYPES = {
    "zoning_application": {"label": "Zoning Application", "color": "purple"},
    "planning_approval": {"label": "Planning Approval", "color": "blue"},
    "variance_request": {"label": "Variance Request", "color": "orange"},
    "demolition_filing": {"label": "Demolition Filing", "color": "red"},
    "new_building_filing": {"label": "New Building Filing", "color": "green"},
    "land_use_review": {"label": "Land Use Review", "color": "purple"},
}


def calculate_lead_potential(signal):
    """Calculate lead potential for a signal."""
    estimated_value = signal.get('estimated_value') or 0
    signal_type = signal.get('signal_type', '')

    if estimated_value >= 500000 or signal_type == 'new_building_filing':
        return 'high'
    elif signal_type in ('zoning_application', 'planning_approval', 'land_use_review'):
        return 'medium'
    else:
        return 'low'


@app.route('/api/signals')
def api_signals():
    """
    GET /api/signals
    Query params: city, type, status, page, per_page
    Returns pre-construction signals.
    """
    signals = load_signals()

    city = request.args.get('city', '')
    signal_type = request.args.get('type', '')
    status = request.args.get('status', '')

    if city:
        signals = [s for s in signals if s.get('city') == city]
    if signal_type:
        signals = [s for s in signals if s.get('signal_type') == signal_type]
    if status:
        signals = [s for s in signals if s.get('status') == status]

    # Add lead potential
    for s in signals:
        s['lead_potential'] = calculate_lead_potential(s)
        s['has_permit'] = len(s.get('linked_permits', [])) > 0

    # Sort by date_filed desc
    signals.sort(key=lambda x: x.get('date_filed', '') or '', reverse=True)

    # Pagination
    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 50))
    total = len(signals)
    start = (page - 1) * per_page
    page_signals = signals[start:start + per_page]

    # Get available cities and types for filters
    all_signals = load_signals()
    cities = sorted(set(s.get('city', '') for s in all_signals if s.get('city')))
    types = sorted(set(s.get('signal_type', '') for s in all_signals if s.get('signal_type')))

    return jsonify({
        'signals': page_signals,
        'total': total,
        'page': page,
        'per_page': per_page,
        'total_pages': (total + per_page - 1) // per_page,
        'cities': cities,
        'types': types,
    })


@app.route('/api/signals/<signal_id>')
def api_signal_detail(signal_id):
    """
    GET /api/signals/<signal_id>
    Returns a single signal with linked permits.
    V12.51: Uses SQLite for permit lookups.
    """
    signals = load_signals()
    signal = next((s for s in signals if s.get('signal_id') == signal_id), None)

    if not signal:
        return jsonify({'error': 'Signal not found'}), 404

    # Add lead potential
    signal['lead_potential'] = calculate_lead_potential(signal)

    # Load linked permits from SQLite
    linked_permits = []
    if signal.get('linked_permits'):
        conn = permitdb.get_connection()
        permit_numbers = signal['linked_permits']
        placeholders = ','.join('?' * len(permit_numbers))
        cursor = conn.execute(
            f"SELECT * FROM permits WHERE permit_number IN ({placeholders})",
            permit_numbers
        )
        linked_permits = [dict(row) for row in cursor]
        linked_permits = add_lead_scores(linked_permits)

    return jsonify({
        'signal': signal,
        'linked_permits': linked_permits,
    })


@app.route('/api/signals/stats')
def api_signal_stats():
    """
    GET /api/signals/stats
    Query params: city
    Returns signal counts by type and status.
    """
    signals = load_signals()

    city = request.args.get('city', '')
    if city:
        signals = [s for s in signals if s.get('city') == city]

    type_counts = {}
    status_counts = {'pending': 0, 'approved': 0, 'denied': 0, 'withdrawn': 0}
    lead_potential_counts = {'high': 0, 'medium': 0, 'low': 0}
    linked_count = 0

    for s in signals:
        signal_type = s.get('signal_type', 'unknown')
        type_counts[signal_type] = type_counts.get(signal_type, 0) + 1

        status = s.get('status', 'pending')
        if status in status_counts:
            status_counts[status] += 1

        potential = calculate_lead_potential(s)
        lead_potential_counts[potential] += 1

        if s.get('linked_permits'):
            linked_count += 1

    return jsonify({
        'total': len(signals),
        'type_breakdown': type_counts,
        'status_breakdown': status_counts,
        'lead_potential_breakdown': lead_potential_counts,
        'linked_to_permits': linked_count,
        'unlinked': len(signals) - linked_count,
        'city': city or 'All Cities',
    })


@app.route('/api/address-intel/<path:address>')
def api_address_intel(address):
    """
    GET /api/address-intel/<address>
    Returns ALL intelligence for an address: permits, signals, violations, history.
    V12.51: Uses SQLite for permits and history lookups.
    """
    normalized = normalize_address_for_lookup(address)

    if not normalized:
        return jsonify({'error': 'Address required'}), 400

    conn = permitdb.get_connection()

    # Find matching permits from SQLite (LIKE search on address)
    cursor = conn.execute(
        "SELECT * FROM permits WHERE LOWER(address) LIKE ?",
        (f"%{normalized}%",)
    )
    matching_permits = [dict(row) for row in cursor]
    matching_permits = add_lead_scores(matching_permits)

    # Signals and violations still use JSON (not in SQLite)
    signals = load_signals()
    violations = load_violations()

    # Find matching signals
    matching_signals = []
    for s in signals:
        s_addr = s.get('address_normalized', '')
        if normalized in s_addr or s_addr in normalized:
            s['lead_potential'] = calculate_lead_potential(s)
            matching_signals.append(s)

    # Find matching violations
    matching_violations = []
    for v in violations:
        v_addr = normalize_address_for_lookup(v.get('address', ''))
        if normalized in v_addr or v_addr in normalized:
            matching_violations.append(v)

    # Find permit history from SQLite
    history_permits = permitdb.get_address_history(normalized)
    history_entry = {}
    if history_permits:
        history_entry = {
            'address': history_permits[0].get('address'),
            'city': history_permits[0].get('city'),
            'state': history_permits[0].get('state'),
            'permits': history_permits,
            'permit_count': len(history_permits),
        }

    return jsonify({
        'address': address,
        'address_normalized': normalized,
        'permits': matching_permits,
        'permit_count': len(matching_permits),
        'signals': matching_signals,
        'signal_count': len(matching_signals),
        'violations': matching_violations,
        'violation_count': len(matching_violations),
        'has_active_violations': any(v.get('status', '').lower() in ('open', 'active', 'pending') for v in matching_violations),
        'history': history_entry,
        'historical_permit_count': history_entry.get('permit_count', 0),
        'is_repeat_renovator': history_entry.get('permit_count', 0) >= 3,
    })


@app.route('/early-intel')
def early_intel_page():
    """Render the Early Intel page (Pro users only)."""
    user = get_current_user()

    # Check if user has Pro plan using centralized utility
    if not is_pro(user):
        footer_cities = get_cities_with_data()
        return render_template('upgrade_gate.html',
            title="Early Intel",
            icon="🔮",
            heading="Early Intel is a Pro Feature",
            description="Upgrade to Professional to access pre-construction signals, zoning applications, and early-stage filings before permits are issued.",
            footer_cities=footer_cities
        )

    footer_cities = get_cities_with_data()
    return render_template('early_intel.html', user=user, footer_cities=footer_cities)


# ===========================
# STRIPE PAYMENT ENDPOINTS
# ===========================

# Stripe configuration from environment variables
STRIPE_SECRET_KEY = os.environ.get('STRIPE_SECRET_KEY', '')
STRIPE_PRICE_ID = os.environ.get('STRIPE_PRICE_ID', '')  # Monthly price ID ($149/mo)
# REPLACE WITH ANNUAL STRIPE PRICE ID - Create a new price in Stripe for $1,548/year ($129/mo)
STRIPE_ANNUAL_PRICE_ID = os.environ.get('STRIPE_ANNUAL_PRICE_ID', '')
STRIPE_WEBHOOK_SECRET = os.environ.get('STRIPE_WEBHOOK_SECRET', '')
SITE_URL = os.environ.get('SITE_URL', 'http://localhost:5000')

@app.route('/api/create-checkout-session', methods=['POST'])
def create_checkout_session():
    """Create a Stripe Checkout Session for Professional plan (monthly or annual)."""
    if not STRIPE_SECRET_KEY or not STRIPE_PRICE_ID:
        return jsonify({'error': 'Stripe not configured'}), 500

    stripe.api_key = STRIPE_SECRET_KEY

    data = request.get_json() or {}
    customer_email = data.get('email')
    billing_period = data.get('billing_period', 'monthly')

    # Choose the correct price based on billing period
    if billing_period == 'annual' and STRIPE_ANNUAL_PRICE_ID:
        price_id = STRIPE_ANNUAL_PRICE_ID
        plan_name = 'professional_annual'
    else:
        price_id = STRIPE_PRICE_ID
        plan_name = 'professional_monthly'

    # Track checkout started event
    analytics.track_event('checkout_started', event_data={
        'plan': plan_name,
        'billing': billing_period
    })

    try:
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price': price_id,
                'quantity': 1,
            }],
            mode='subscription',
            success_url=f'{SITE_URL}/?payment=success',
            cancel_url=f'{SITE_URL}/?payment=cancelled',
            customer_email=customer_email,
            metadata={
                'plan': plan_name,
                'billing_period': billing_period,
            },
        )
        return jsonify({'url': checkout_session.url})
    except stripe.error.StripeError as e:
        return jsonify({'error': str(e)}), 400


@app.route('/api/stripe-webhook', methods=['POST'])
def stripe_webhook():
    """Handle Stripe webhook events."""
    payload = request.get_data(as_text=True)
    sig_header = request.headers.get('Stripe-Signature')

    if not STRIPE_SECRET_KEY:
        return jsonify({'error': 'Stripe not configured'}), 500

    stripe.api_key = STRIPE_SECRET_KEY

    try:
        if STRIPE_WEBHOOK_SECRET:
            event = stripe.Webhook.construct_event(
                payload, sig_header, STRIPE_WEBHOOK_SECRET
            )
        else:
            # For testing without webhook signature verification
            event = json.loads(payload)
    except ValueError:
        return jsonify({'error': 'Invalid payload'}), 400
    except stripe.error.SignatureVerificationError:
        return jsonify({'error': 'Invalid signature'}), 400

    event_type = event['type']
    print(f"[Stripe] Received event: {event_type}")

    # V12.53: Handle all subscription lifecycle events
    if event_type == 'checkout.session.completed':
        # New subscription or upgrade
        session_obj = event['data']['object']
        customer_email = session_obj.get('customer_email') or session_obj.get('customer_details', {}).get('email')
        plan = session_obj.get('metadata', {}).get('plan', 'professional')

        if customer_email:
            user = find_user_by_email(customer_email)
            if user:
                user.plan = 'pro'
                user.stripe_customer_id = session_obj.get('customer')
                user.subscription_id = session_obj.get('subscription')
                # Clear trial fields since they're now a paying customer
                user.trial_end_date = None
                user.trial_started_at = None
                db.session.commit()
                print(f"[Stripe] User {customer_email} upgraded to {plan}")

                # V12.53: Send payment success email
                try:
                    from email_alerts import send_payment_success
                    send_payment_success(user, plan)
                except Exception as e:
                    print(f"[Stripe] Payment success email failed: {e}")

            # Track payment success event
            analytics.track_event('payment_success', event_data={
                'plan': plan,
                'stripe_customer_id': session_obj.get('customer')
            }, user_id_override=customer_email)

    elif event_type == 'invoice.payment_failed':
        # Payment failed
        invoice = event['data']['object']
        customer_email = invoice.get('customer_email')

        if customer_email:
            user = find_user_by_email(customer_email)
            if user:
                print(f"[Stripe] Payment failed for {customer_email}")
                try:
                    from email_alerts import send_payment_failed
                    send_payment_failed(user)
                except Exception as e:
                    print(f"[Stripe] Payment failed email failed: {e}")

    elif event_type == 'invoice.payment_succeeded':
        # Renewal payment succeeded
        invoice = event['data']['object']
        customer_email = invoice.get('customer_email')
        # Only send renewal email if this is not the first payment
        billing_reason = invoice.get('billing_reason')

        if customer_email and billing_reason == 'subscription_cycle':
            user = find_user_by_email(customer_email)
            if user:
                print(f"[Stripe] Subscription renewed for {customer_email}")
                try:
                    from email_alerts import send_subscription_renewed
                    send_subscription_renewed(user)
                except Exception as e:
                    print(f"[Stripe] Renewal email failed: {e}")

    elif event_type == 'customer.subscription.deleted':
        # Subscription cancelled
        subscription = event['data']['object']
        customer_id = subscription.get('customer')

        # Find user by stripe_customer_id
        user = User.query.filter_by(stripe_customer_id=customer_id).first()
        if user:
            user.plan = 'free'
            db.session.commit()
            print(f"[Stripe] Subscription cancelled for {user.email}")
            try:
                from email_alerts import send_subscription_cancelled
                send_subscription_cancelled(user)
            except Exception as e:
                print(f"[Stripe] Cancellation email failed: {e}")

    return jsonify({'status': 'success'})


@app.route('/api/webhooks/sendgrid', methods=['POST'])
def sendgrid_webhook():
    """
    Handle SendGrid Event Webhooks for email engagement tracking.
    NOTE: Configure this URL in SendGrid dashboard > Settings > Mail Settings > Event Webhook
    URL: https://permitgrab.com/api/webhooks/sendgrid
    Enable events: Delivered, Opened, Clicked, Bounced, Unsubscribed, Spam Report
    """
    try:
        events = request.get_json()
        if not events or not isinstance(events, list):
            return '', 200

        for event in events:
            sg_type = event.get('event')  # 'delivered', 'open', 'click', 'bounce', etc.
            email = event.get('email', '')

            if not sg_type:
                continue

            # Find user by email (if exists) for user_id
            user_id = None
            if email:
                users = load_users()
                user = next((u for u in users if u.get('email', '').lower() == email.lower()), None)
                if user:
                    user_id = user.get('email')

            # Track the email event
            analytics.track_event(
                event_type=f'email_{sg_type}',  # email_delivered, email_open, email_click, etc.
                event_data={
                    'email': email,
                    'subject': event.get('subject', ''),
                    'url': event.get('url', ''),  # For click events
                    'sg_event_id': event.get('sg_event_id', ''),
                    'sg_message_id': event.get('sg_message_id', ''),
                    'category': event.get('category', []),
                    'reason': event.get('reason', ''),  # For bounce/drop events
                },
                user_id_override=user_id
            )

    except Exception as e:
        print(f"[SendGrid Webhook] Error processing events: {e}")

    return '', 200


# ===========================
# USER AUTHENTICATION
# ===========================

@app.route('/api/register', methods=['POST'])
@limiter.limit("10 per hour")
def api_register():
    """POST /api/register - Register a new user.

    V7: Uses PostgreSQL database with UNIQUE constraint on email.
    Database constraint prevents duplicates even under race conditions.
    """
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Invalid request'}), 400

    email = data.get('email', '').strip().lower()
    password = data.get('password', '')
    name = data.get('name', '').strip()

    if not email or not password:
        return jsonify({'error': 'Email and password required'}), 400

    if len(password) < 8:
        return jsonify({'error': 'Password must be at least 8 characters'}), 400

    # Validate email format (basic check)
    if '@' not in email or '.' not in email.split('@')[-1]:
        return jsonify({'error': 'Please enter a valid email address'}), 400

    # Check for existing account BEFORE creating user
    existing = find_user_by_email(email)
    if existing:
        print(f"[Register] DUPLICATE BLOCKED: {email}")
        return jsonify({'error': 'An account with this email already exists. Please log in instead.'}), 409

    # Create new user in database
    try:
        import secrets
        plan = data.get('plan', 'free')
        is_trial = plan == 'pro_trial'

        new_user = User(
            email=email,
            name=name,
            password_hash=generate_password_hash(password),
            plan='pro_trial' if is_trial else 'free',
            # V12.53: Email system fields
            unsubscribe_token=secrets.token_urlsafe(32),
            digest_active=True,
            trial_started_at=datetime.utcnow() if is_trial else None,
            trial_end_date=(datetime.utcnow() + timedelta(days=14)) if is_trial else None,
        )
        db.session.add(new_user)
        db.session.commit()
        print(f"[Register] User created in database: {email} (plan: {new_user.plan})")
    except IntegrityError:
        # Database UNIQUE constraint caught a race condition
        db.session.rollback()
        print(f"[Register] DUPLICATE BLOCKED (IntegrityError): {email}")
        return jsonify({'error': 'An account with this email already exists. Please log in instead.'}), 409

    # Log in the user
    session['user_email'] = email

    # Track signup event
    analytics.track_event('signup', event_data={'method': 'email', 'plan': new_user.plan})

    # V12.53: Send welcome email (async to not block registration)
    try:
        from email_alerts import send_welcome_free, send_welcome_pro_trial
        if new_user.plan == 'pro_trial':
            send_welcome_pro_trial(new_user)
            new_user.welcome_email_sent = True
            db.session.commit()
            print(f"[Register] Welcome Pro Trial email sent to {email}")
        else:
            send_welcome_free(new_user)
            new_user.welcome_email_sent = True
            db.session.commit()
            print(f"[Register] Welcome Free email sent to {email}")
    except Exception as e:
        print(f"[Register] Welcome email failed for {email}: {e}")

    # Return user without password hash
    return jsonify({
        'message': 'Registration successful',
        'user': {
            'email': new_user.email,
            'name': new_user.name,
            'plan': new_user.plan,
        }
    }), 201


@app.route('/api/login', methods=['POST'])
@limiter.limit("20 per minute")
def api_login():
    """POST /api/login - Log in a user (V7: uses PostgreSQL)."""
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Invalid request'}), 400

    email = data.get('email', '').strip().lower()
    password = data.get('password', '')

    if not email or not password:
        return jsonify({'error': 'Email and password required'}), 400

    # Find user in database
    user = find_user_by_email(email)

    if not user:
        print(f"[Login] No user found for email: {email}")
        return jsonify({'error': 'Invalid email or password'}), 401

    if not check_password_hash(user.password_hash, password):
        print(f"[Login] Invalid password for email: {email}")
        return jsonify({'error': 'Invalid email or password'}), 401

    # Log in the user
    session['user_email'] = email

    # V12.53: Update last_login_at timestamp
    user.last_login_at = datetime.utcnow()
    db.session.commit()

    # Track login event
    analytics.track_event('login')

    return jsonify({
        'message': 'Login successful',
        'user': {
            'email': user.email,
            'name': user.name,
            'plan': user.plan,
        }
    })


@app.route('/api/logout', methods=['POST'])
def api_logout():
    """POST /api/logout - Log out the current user."""
    session.pop('user_email', None)
    return jsonify({'message': 'Logged out'})


@app.route('/api/me')
def api_me():
    """GET /api/me - Get current logged-in user."""
    user = get_current_user()
    if not user:
        return jsonify({'user': None})

    # V9 Fix 8: Include daily_alerts and city for alert widget status
    return jsonify({
        'user': {
            'email': user['email'],
            'name': user['name'],
            'plan': user['plan'],
            'daily_alerts': user.get('daily_alerts', False),
            'city': user.get('city', ''),
            'trade': user.get('trade', ''),
        }
    })


# ===========================
# UNSUBSCRIBE
# ===========================

@app.route('/api/unsubscribe')
def api_unsubscribe():
    """GET /api/unsubscribe?token=xxx - Unsubscribe from email alerts."""
    token = request.args.get('token', '')

    if not token:
        return render_template_string('''
            <!DOCTYPE html>
            <html><head><title>Error</title></head>
            <body style="font-family: sans-serif; padding: 40px; text-align: center;">
                <h1>Invalid Link</h1>
                <p>This unsubscribe link is invalid or has expired.</p>
            </body></html>
        '''), 400

    # V12.53: Use User model instead of subscribers.json
    user = User.query.filter_by(unsubscribe_token=token).first()

    if not user:
        return render_template_string('''
            <!DOCTYPE html>
            <html><head><title>Error</title></head>
            <body style="font-family: sans-serif; padding: 40px; text-align: center;">
                <h1>Invalid Link</h1>
                <p>This unsubscribe link is invalid or has already been used.</p>
            </body></html>
        '''), 404

    # Mark digest as inactive
    user.digest_active = False
    db.session.commit()

    return render_template_string('''
        <!DOCTYPE html>
        <html><head><title>Unsubscribed</title></head>
        <body style="font-family: sans-serif; padding: 40px; text-align: center;">
            <h1>You've been unsubscribed</h1>
            <p>{{ email }} will no longer receive permit alerts.</p>
            <p style="margin-top: 20px; color: #666;">
                Changed your mind? <a href="/">Re-subscribe anytime</a>
            </p>
        </body></html>
    ''', email=user.email)


# ===========================
# ADMIN PAGE
# ===========================

@app.route('/admin')
def admin_page():
    """GET /admin - Admin dashboard (password protected)."""
    # Check for admin password in query param or session
    password = request.args.get('password', '')

    if password and ADMIN_PASSWORD and password == ADMIN_PASSWORD:
        session['admin_authenticated'] = True

    if not session.get('admin_authenticated'):
        if not ADMIN_PASSWORD:
            return render_template_string('''
                <!DOCTYPE html>
                <html><head><title>Admin</title></head>
                <body style="font-family: sans-serif; padding: 40px; text-align: center;">
                    <h1>Admin Not Configured</h1>
                    <p>Set the ADMIN_PASSWORD environment variable to enable admin access.</p>
                </body></html>
            '''), 500

        return render_template_string('''
            <!DOCTYPE html>
            <html><head><title>Admin Login</title></head>
            <body style="font-family: sans-serif; padding: 40px; max-width: 400px; margin: 0 auto;">
                <h1>Admin Login</h1>
                <form method="GET" action="/admin">
                    <input type="password" name="password" placeholder="Admin Password"
                           style="width: 100%; padding: 12px; margin-bottom: 12px; border: 1px solid #ccc; border-radius: 4px;">
                    <button type="submit" style="width: 100%; padding: 12px; background: #2563eb; color: white; border: none; border-radius: 4px; cursor: pointer;">
                        Login
                    </button>
                </form>
            </body></html>
        ''')

    # V12.51: Load data from SQLite for admin dashboard
    permit_stats = permitdb.get_permit_stats()
    # V12.53: Count digest subscribers from User model instead of subscribers.json
    digest_subscribers = User.query.filter(User.digest_active == True).all()
    stats = load_stats()

    # V11 Fix 2.1: Get real user stats from database
    all_users = User.query.all()
    pro_users = User.query.filter(User.plan.in_(['pro', 'professional', 'enterprise'])).all()
    alert_users = User.query.filter_by(daily_alerts=True).all()

    # Stats from SQLite
    city_count = permit_stats['city_count']

    # V12: Load collection diagnostic
    diag_path = os.path.join(DATA_DIR, 'collection_diagnostic.json')
    diagnostic = {}
    if os.path.exists(diag_path):
        try:
            with open(diag_path) as f:
                diagnostic = json.load(f, strict=False)
        except Exception:
            pass

    return render_template_string('''
        <!DOCTYPE html>
        <html>
        <head>
            <title>PermitGrab Admin</title>
            <style>
                * { margin: 0; padding: 0; box-sizing: border-box; }
                body { font-family: -apple-system, BlinkMacSystemFont, sans-serif; background: #f3f4f6; }
                .header { background: #111827; color: white; padding: 20px 32px; }
                .header h1 { font-size: 24px; }
                .container { max-width: 1200px; margin: 0 auto; padding: 24px; }
                .stats-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-bottom: 24px; }
                .stat-card { background: white; padding: 20px; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,.1); }
                .stat-card .value { font-size: 32px; font-weight: 700; color: #111827; }
                .stat-card .label { font-size: 14px; color: #6b7280; margin-top: 4px; }
                .section { background: white; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,.1); margin-bottom: 24px; }
                .section-header { padding: 16px 20px; border-bottom: 1px solid #e5e7eb; font-weight: 600; }
                .section-body { padding: 20px; }
                table { width: 100%; border-collapse: collapse; }
                th, td { padding: 12px; text-align: left; border-bottom: 1px solid #e5e7eb; }
                th { background: #f9fafb; font-weight: 600; font-size: 13px; color: #6b7280; }
                .badge { padding: 2px 8px; border-radius: 12px; font-size: 12px; font-weight: 500; }
                .badge-active { background: #dcfce7; color: #166534; }
                .badge-inactive { background: #fee2e2; color: #991b1b; }
                .badge-pro { background: #dbeafe; color: #1e40af; }
                .logout-link { color: rgba(255,255,255,.7); text-decoration: none; font-size: 14px; }
                .form-row { display: flex; gap: 12px; align-items: flex-end; }
                .form-group { display: flex; flex-direction: column; gap: 4px; }
                .form-group label { font-size: 13px; font-weight: 500; color: #374151; }
                .form-group input, .form-group select { padding: 8px 12px; border: 1px solid #d1d5db; border-radius: 6px; font-size: 14px; }
                .btn-upgrade { background: #2563eb; color: white; border: none; padding: 8px 20px; border-radius: 6px; font-weight: 500; cursor: pointer; }
                .btn-upgrade:hover { background: #1d4ed8; }
                .alert { padding: 12px 16px; border-radius: 6px; margin-bottom: 16px; font-size: 14px; }
                .alert-success { background: #dcfce7; color: #166534; }
                .alert-error { background: #fee2e2; color: #991b1b; }
            </style>
        </head>
        <body>
            <div class="header">
                <div style="display: flex; justify-content: space-between; align-items: center; max-width: 1200px; margin: 0 auto;">
                    <h1>PermitGrab Admin</h1>
                    <a href="/admin?logout=1" class="logout-link">Logout</a>
                </div>
            </div>
            <div class="container">
                <div class="stats-grid">
                    <div class="stat-card">
                        <div class="value">{{ total_permits }}</div>
                        <div class="label">Total Permits</div>
                    </div>
                    <div class="stat-card">
                        <div class="value">{{ city_count }}</div>
                        <div class="label">Active Cities</div>
                    </div>
                    <div class="stat-card">
                        <div class="value">{{ total_users }}</div>
                        <div class="label">Registered Users</div>
                    </div>
                    <div class="stat-card">
                        <div class="value">{{ pro_users }}</div>
                        <div class="label">Pro Users</div>
                    </div>
                </div>
                {% if last_updated %}
                <div style="text-align: center; margin-bottom: 16px; padding: 8px; background: #dbeafe; border-radius: 6px; font-size: 14px; color: #1e40af;">
                    Last data collection: {{ last_updated }}
                </div>
                {% endif %}

                {% if success_msg %}
                <div class="alert alert-success">{{ success_msg }}</div>
                {% endif %}
                {% if error_msg %}
                <div class="alert alert-error">{{ error_msg }}</div>
                {% endif %}

                <div class="section">
                    <div class="section-header">Upgrade User</div>
                    <div class="section-body">
                        <form method="POST" action="/admin/upgrade-user" class="form-row">
                            <div class="form-group">
                                <label for="email">Email</label>
                                <input type="email" id="email" name="email" placeholder="user@example.com" required style="width: 280px;">
                            </div>
                            <div class="form-group">
                                <label for="plan">Plan</label>
                                <select id="plan" name="plan" required>
                                    <option value="free">Free</option>
                                    <option value="pro">Pro</option>
                                    <option value="enterprise">Enterprise</option>
                                </select>
                            </div>
                            <button type="submit" class="btn-upgrade">Upgrade</button>
                        </form>
                    </div>
                </div>

                <div class="section">
                    <div class="section-header">Collection Status</div>
                    <div class="section-body">
                        <p><strong>Last Updated:</strong> {{ last_updated or 'Never' }}</p>
                        <p><strong>Total Users:</strong> {{ total_users }}</p>
                        {% if diagnostic %}
                        <hr style="margin: 16px 0; border: none; border-top: 1px solid #e5e7eb;">
                        <h4 style="margin-bottom: 12px; color: #374151;">Collection Diagnostic</h4>
                        <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin-bottom: 16px;">
                            <div style="background: #dcfce7; padding: 12px; border-radius: 6px; text-align: center;">
                                <div style="font-size: 24px; font-weight: 700; color: #16a34a;">{{ diagnostic.cities_with_permits }}</div>
                                <div style="font-size: 12px; color: #166534;">Cities With Permits</div>
                            </div>
                            <div style="background: #fef9c3; padding: 12px; border-radius: 6px; text-align: center;">
                                <div style="font-size: 24px; font-weight: 700; color: #ca8a04;">{{ diagnostic.cities_zero_permits }}</div>
                                <div style="font-size: 12px; color: #854d0e;">Zero Permits</div>
                            </div>
                            <div style="background: #fee2e2; padding: 12px; border-radius: 6px; text-align: center;">
                                <div style="font-size: 24px; font-weight: 700; color: #dc2626;">{{ diagnostic.cities_with_errors }}</div>
                                <div style="font-size: 12px; color: #991b1b;">Errors</div>
                            </div>
                        </div>
                        <p style="font-size: 13px; color: #6b7280;"><strong>Timeouts:</strong> {{ diagnostic.cities_timeout }} | <strong>Connection Errors:</strong> {{ diagnostic.cities_connection_error }}</p>
                        {% endif %}
                    </div>
                </div>

                <div class="section">
                    <div class="section-header">Subscribers ({{ total_subscribers }})</div>
                    <div class="section-body" style="overflow-x: auto;">
                        <table>
                            <thead>
                                <tr>
                                    <th>Email</th>
                                    <th>Name</th>
                                    <th>City</th>
                                    <th>Trade</th>
                                    <th>Plan</th>
                                    <th>Status</th>
                                    <th>Subscribed</th>
                                </tr>
                            </thead>
                            <tbody>
                                {% for sub in subscribers %}
                                <tr>
                                    <td>{{ sub.email }}</td>
                                    <td>{{ sub.name or '-' }}</td>
                                    <td>{{ sub.city or '-' }}</td>
                                    <td>{{ sub.trade or '-' }}</td>
                                    <td>
                                        {% if sub.plan in ['professional', 'enterprise'] %}
                                        <span class="badge badge-pro">{{ sub.plan }}</span>
                                        {% else %}
                                        {{ sub.plan or 'free' }}
                                        {% endif %}
                                    </td>
                                    <td>
                                        {% if sub.active != false %}
                                        <span class="badge badge-active">Active</span>
                                        {% else %}
                                        <span class="badge badge-inactive">Inactive</span>
                                        {% endif %}
                                    </td>
                                    <td>{{ sub.subscribed_at[:10] if sub.subscribed_at else '-' }}</td>
                                </tr>
                                {% endfor %}
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
        </body>
        </html>
    ''',
        total_permits=permit_stats['total_permits'],
        city_count=city_count,
        total_users=len(all_users),
        pro_users=len(pro_users),
        last_updated=stats.get('collected_at', ''),
        subscribers=subscribers,
        total_subscribers=len(digest_subscribers),
        diagnostic=diagnostic,
        success_msg=request.args.get('success', ''),
        error_msg=request.args.get('error', ''),
    )


# Handle admin logout
@app.before_request
def check_admin_logout():
    if request.path == '/admin' and request.args.get('logout'):
        session.pop('admin_authenticated', None)


@app.route('/api/collection-status')
def api_collection_status():
    """GET /api/collection-status - Check data collection status (admin only).
    V12.51: Uses SQLite for permit stats.
    """
    if not session.get('admin_authenticated'):
        return jsonify({'error': 'Admin authentication required'}), 401

    stats = load_stats()
    permit_stats = permitdb.get_permit_stats()

    # Check data directory (some JSON files still exist for signals/violations)
    data_files = {}
    for filename in ['violations.json', 'signals.json', 'city_health.json']:
        filepath = os.path.join(DATA_DIR, filename)
        if os.path.exists(filepath):
            data_files[filename] = {
                'exists': True,
                'size_kb': round(os.path.getsize(filepath) / 1024, 1),
                'modified': datetime.fromtimestamp(os.path.getmtime(filepath)).isoformat(),
            }
        else:
            data_files[filename] = {'exists': False}

    # Add SQLite database info
    if os.path.exists(permitdb.DB_PATH):
        data_files['permitgrab.db'] = {
            'exists': True,
            'size_kb': round(os.path.getsize(permitdb.DB_PATH) / 1024, 1),
            'modified': datetime.fromtimestamp(os.path.getmtime(permitdb.DB_PATH)).isoformat(),
        }

    return jsonify({
        'data_dir': DATA_DIR,
        'total_permits': permit_stats['total_permits'],
        'unique_cities': permit_stats['city_count'],
        'last_collection': stats.get('collected_at', 'Never'),
        'city_stats': stats.get('city_stats', {}),
        'data_files': data_files,
        'collector_started': _collector_started,
    })


@app.route('/admin/trigger-collection', methods=['POST'])
def admin_trigger_collection():
    """V12.2: Manually trigger data collection (admin only)."""
    if not session.get('admin_authenticated'):
        return jsonify({"error": "Unauthorized"}), 403

    import threading
    from collector import collect_all

    # Run in background thread so it doesn't block
    # V12.38: Expanded from 60 to 180 days
    thread = threading.Thread(target=collect_all, kwargs={"days_back": 180}, daemon=True)
    thread.start()

    return jsonify({
        "status": "Collection triggered",
        "message": "Running in background. Check /api/stats in a few minutes."
    })


@app.route('/admin/collector-health')
def admin_collector_health():
    """V15: Collector health dashboard - shows status of all prod cities."""
    if not session.get('admin_authenticated'):
        return redirect('/admin?error=Please+log+in')

    # Get health data
    try:
        health_data = permitdb.get_city_health_status()
        summary = permitdb.get_daily_collection_summary()
        recent_runs = permitdb.get_recent_scraper_runs(limit=20)
    except Exception as e:
        health_data = []
        summary = None
        recent_runs = []
        print(f"[V15] Error loading collector health: {e}")

    # Count by status
    green_count = sum(1 for c in health_data if c.get('health_color') == 'GREEN')
    yellow_count = sum(1 for c in health_data if c.get('health_color') == 'YELLOW')
    red_count = sum(1 for c in health_data if c.get('health_color') == 'RED')

    html = f'''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Collector Health - PermitGrab Admin</title>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; margin: 20px; background: #1a1a2e; color: #e0e0e0; }}
            h1 {{ color: #00d4ff; }}
            h2 {{ color: #888; margin-top: 30px; }}
            table {{ border-collapse: collapse; width: 100%; margin-bottom: 30px; }}
            th, td {{ border: 1px solid #333; padding: 8px 12px; text-align: left; }}
            th {{ background: #252540; color: #00d4ff; }}
            tr:nth-child(even) {{ background: #1e1e35; }}
            .green {{ color: #00ff88; font-weight: bold; }}
            .yellow {{ color: #ffcc00; font-weight: bold; }}
            .red {{ color: #ff4444; font-weight: bold; }}
            .summary {{ display: flex; gap: 20px; margin-bottom: 30px; }}
            .summary-card {{ background: #252540; padding: 20px; border-radius: 8px; text-align: center; min-width: 120px; }}
            .summary-card .value {{ font-size: 32px; font-weight: bold; }}
            .summary-card .label {{ font-size: 14px; color: #888; }}
            a {{ color: #00d4ff; }}
            .back-link {{ margin-bottom: 20px; display: block; }}
        </style>
    </head>
    <body>
        <a href="/admin" class="back-link">&larr; Back to Admin</a>
        <h1>Collector Health Dashboard</h1>

        <div class="summary">
            <div class="summary-card">
                <div class="value green">{green_count}</div>
                <div class="label">Healthy (0-2 days)</div>
            </div>
            <div class="summary-card">
                <div class="value yellow">{yellow_count}</div>
                <div class="label">Warning (3-7 days)</div>
            </div>
            <div class="summary-card">
                <div class="value red">{red_count}</div>
                <div class="label">Critical (7+ days)</div>
            </div>
            <div class="summary-card">
                <div class="value">{len(health_data)}</div>
                <div class="label">Total Cities</div>
            </div>
        </div>
    '''

    if summary:
        html += f'''
        <h2>Today's Collection Summary</h2>
        <table>
            <tr>
                <th>Total Runs</th>
                <th>Successful</th>
                <th>Errors</th>
                <th>No New Data</th>
                <th>Permits Inserted</th>
                <th>Avg Duration</th>
            </tr>
            <tr>
                <td>{summary.get('total_runs', 0)}</td>
                <td class="green">{summary.get('successful', 0)}</td>
                <td class="red">{summary.get('errors', 0)}</td>
                <td>{summary.get('no_new_data', 0)}</td>
                <td>{summary.get('total_permits_inserted', 0)}</td>
                <td>{int(summary.get('avg_duration_ms', 0) or 0)}ms</td>
            </tr>
        </table>
        '''

    html += '''
        <h2>City Health Status</h2>
        <table>
            <tr>
                <th>City</th>
                <th>State</th>
                <th>Status</th>
                <th>Days Since Data</th>
                <th>Total Permits</th>
                <th>Failures</th>
                <th>Last Error</th>
            </tr>
    '''

    for city in health_data:
        color_class = city.get('health_color', 'RED').lower()
        days = city.get('days_since_data', 'N/A')
        if days is None:
            days = 'Never'
        html += f'''
            <tr>
                <td>{city.get('city', '')}</td>
                <td>{city.get('state', '')}</td>
                <td class="{color_class}">{city.get('status', '').upper()}</td>
                <td class="{color_class}">{days}</td>
                <td>{city.get('total_permits', 0)}</td>
                <td>{city.get('consecutive_failures', 0)}</td>
                <td>{(city.get('last_error') or '')[:50]}</td>
            </tr>
        '''

    html += '''
        </table>

        <h2>Recent Collection Runs</h2>
        <table>
            <tr>
                <th>Time</th>
                <th>City</th>
                <th>Status</th>
                <th>Permits Found</th>
                <th>Inserted</th>
                <th>Duration</th>
                <th>Error</th>
            </tr>
    '''

    for run in recent_runs:
        status_class = 'green' if run.get('status') == 'success' else ('yellow' if run.get('status') == 'no_new' else 'red')
        html += f'''
            <tr>
                <td>{run.get('run_started_at', '')}</td>
                <td>{run.get('city', '')} {run.get('state', '')}</td>
                <td class="{status_class}">{run.get('status', '')}</td>
                <td>{run.get('permits_found', 0)}</td>
                <td>{run.get('permits_inserted', 0)}</td>
                <td>{run.get('duration_ms', '')}ms</td>
                <td>{(run.get('error_message') or '')[:30]}</td>
            </tr>
        '''

    html += '''
        </table>

        <p style="color: #666; margin-top: 40px;">
            V15 Collector Redesign - prod_cities table
        </p>
    </body>
    </html>
    '''

    return html


@app.route('/admin/upgrade-user', methods=['POST'])
def admin_upgrade_user():
    """POST /admin/upgrade-user - Upgrade a user's subscription plan."""
    # Check admin authentication
    if not session.get('admin_authenticated'):
        return jsonify({'error': 'Unauthorized'}), 401

    email = request.form.get('email', '').strip().lower()
    plan = request.form.get('plan', 'free').strip().lower()

    if not email:
        return redirect('/admin?error=Email+is+required')

    if plan not in ('free', 'pro', 'enterprise'):
        return redirect('/admin?error=Invalid+plan')

    # V12.53: Direct User model update (removed subscribers.json dependency)
    user_obj = find_user_by_email(email)
    if user_obj:
        user_obj.plan = plan
        db.session.commit()
        return redirect(f'/admin?success=Upgraded+{email}+to+{plan}')
    else:
        return redirect(f'/admin?error=User+{email}+not+found')


# ===========================
# ADMIN ANALYTICS DASHBOARD
# ===========================

# Admin emails list - add emails that should have admin access
ADMIN_EMAILS = os.environ.get('ADMIN_EMAILS', 'wcrainshaw@gmail.com').lower().split(',')

@app.route('/admin/analytics')
def admin_analytics_page():
    """Admin analytics dashboard."""
    # Check admin authentication
    user = get_current_user()
    if not user or user.get('email', '').lower() not in ADMIN_EMAILS:
        if not session.get('admin_authenticated'):
            return "Unauthorized - Admin access required", 403

    # Gather all analytics data
    data = {
        'visitors_today': analytics.get_visitors_today(),
        'signups_week': analytics.get_signups_this_week(),
        'active_users_7d': analytics.get_active_users_7d(),
        'trial_starts_30d': analytics.get_trial_starts_30d(),
        'daily_traffic': analytics.get_daily_traffic(30),
        'top_pages': analytics.get_top_pages(7, 20),
        'funnel': analytics.get_conversion_funnel(30),
        'event_counts': analytics.get_event_counts(7),
        'city_engagement': analytics.get_city_engagement(30),
        'traffic_sources': analytics.get_traffic_sources(30),
        'health_status': analytics.get_latest_health_status(),
        'health_failures': analytics.get_health_failures_recent(20),
        'city_health': analytics.get_city_health_summary(),
        'route_health': analytics.get_route_health_summary(),
        'service_health': analytics.get_service_health_status(),
        'email_perf_7d': analytics.get_email_performance(7),
        'email_perf_30d': analytics.get_email_performance(30),
    }

    return render_template('admin_analytics.html', data=data)


# ===========================
# MY LEADS CRM PAGE
# ===========================

@app.route('/my-leads')
def my_leads_page():
    """Render the My Leads CRM page."""
    user = get_current_user()
    if not user:
        # Redirect to login with message
        return redirect('/login?redirect=my-leads')

    footer_cities = get_cities_with_data()
    return render_template('my_leads.html', user=user, footer_cities=footer_cities)


# ===========================
# SAVED SEARCHES PAGE
# ===========================

@app.route('/saved-searches')
def saved_searches_page():
    """Render the Saved Searches page."""
    user = get_current_user()
    if not user:
        return redirect('/login?redirect=saved-searches')

    searches = get_user_saved_searches(user['email'])
    footer_cities = get_cities_with_data()
    return render_template('saved_searches.html', user=user, searches=searches, footer_cities=footer_cities)


# ===========================
# SEO CITY LANDING PAGES
# ===========================

# City configurations with SEO content
CITY_SEO_CONFIG = {
    "new-york": {
        "name": "New York City",
        "state": "NY",
        "meta_title": "New York City Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse 2000+ active building permits in New York City. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>New York City's construction market is one of the largest and most dynamic in the world. With constant development across all five boroughs, NYC building permits represent billions of dollars in annual construction activity. From luxury high-rise developments in Manhattan to residential renovations in Brooklyn and Queens, the opportunities for contractors are endless.</p>
            <p>The NYC construction industry spans every trade imaginable—HVAC installations in commercial towers, electrical upgrades in historic brownstones, plumbing renovations in pre-war buildings, and roofing projects across thousands of residential properties. New York City construction permits are filed daily with the Department of Buildings, creating a steady stream of new contractor leads.</p>
            <p>For contractors seeking NYC building permits and construction leads, timing is everything. PermitGrab delivers fresh New York City permit data daily, giving you the edge to connect with property owners before your competition even knows the project exists.</p>
        """
    },
    "los-angeles": {
        "name": "Los Angeles",
        "state": "CA",
        "meta_title": "Los Angeles Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse 2000+ active building permits in Los Angeles. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Los Angeles is experiencing unprecedented construction growth, making it one of the hottest markets for contractor leads in the nation. From Santa Monica to Downtown LA to the Valley, LA building permits cover everything from ADU (Accessory Dwelling Unit) construction to major commercial developments and earthquake retrofit projects.</p>
            <p>The LA construction market is unique in its diversity—solar panel installations are booming, pool construction remains strong year-round, and seismic retrofitting creates steady demand for structural contractors. Los Angeles construction permits also reflect the city's focus on sustainability, with green building projects and EV charger installations on the rise.</p>
            <p>Contractors looking for Los Angeles building permits need fast access to new filings. PermitGrab pulls LA permit data directly from official city sources, delivering actionable contractor leads for every trade from roofing to HVAC to general construction.</p>
        """
    },
    "chicago": {
        "name": "Chicago",
        "state": "IL",
        "meta_title": "Chicago Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse 2000+ active building permits in Chicago. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Chicago's construction industry is thriving, with billions invested in residential, commercial, and infrastructure projects each year. Chicago building permits cover everything from downtown high-rise construction to single-family renovations in neighborhoods across the city. The Windy City's harsh winters create strong seasonal demand for HVAC, roofing, and weatherization projects.</p>
            <p>The Chicago contractor market benefits from the city's aging housing stock—thousands of greystone and brick buildings require ongoing maintenance, window replacements, tuckpointing, and interior renovations. Chicago construction permits also reflect the city's industrial heritage, with many warehouse-to-residential conversions creating opportunities for general contractors and specialty trades alike.</p>
            <p>For contractors seeking Chicago building permits and construction leads, staying ahead of the competition means accessing permit data as soon as it's filed. PermitGrab delivers fresh Chicago permit leads daily, helping you find and win jobs across Cook County.</p>
        """
    },
    "san-francisco": {
        "name": "San Francisco",
        "state": "CA",
        "meta_title": "San Francisco Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse 2000+ active building permits in San Francisco. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>San Francisco's construction market commands some of the highest project values in the nation. SF building permits range from tech company office buildouts to Victorian home renovations in Pacific Heights to seismic retrofitting in older buildings. The city's strict building codes and permitting requirements mean property owners actively seek qualified, reliable contractors.</p>
            <p>The San Francisco construction industry reflects the city's unique character—historic preservation projects, ADU construction under California's housing laws, and high-end residential renovations drive steady permit activity. San Francisco construction permits also include significant solar and green building projects as the city pushes toward sustainability goals.</p>
            <p>Contractors targeting San Francisco building permits face stiff competition in this premium market. PermitGrab gives you the advantage of seeing new SF permit filings first, so you can reach property owners while they're still evaluating contractors.</p>
        """
    },
    "austin": {
        "name": "Austin",
        "state": "TX",
        "meta_title": "Austin Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse 2000+ active building permits in Austin. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Austin is one of America's fastest-growing cities, and the construction boom shows no signs of slowing. Austin building permits reflect the city's explosive growth—new residential developments, commercial construction, and infrastructure projects create constant demand for contractors of every trade. From Round Rock to South Austin, the permit pipeline is full.</p>
            <p>The Austin construction market offers unique opportunities including new home construction in master-planned communities, office buildouts for tech companies relocating to Texas, and renovation projects in established neighborhoods like Hyde Park and Travis Heights. Austin construction permits span HVAC installations critical for Texas summers, pool construction, and outdoor living projects.</p>
            <p>For contractors seeking Austin building permits, speed matters in this competitive market. PermitGrab delivers fresh Austin permit data daily, connecting you with property owners and builders who need quality contractors now.</p>
        """
    },
    "seattle": {
        "name": "Seattle",
        "state": "WA",
        "meta_title": "Seattle Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse 900+ active building permits in Seattle. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Seattle's construction industry continues to boom, driven by tech industry growth and population influx. Seattle building permits cover high-rise development downtown, residential construction in neighborhoods like Capitol Hill and Ballard, and renovation projects across King County. The Pacific Northwest climate creates strong demand for roofing, weatherization, and moisture-control projects.</p>
            <p>The Seattle construction market includes significant green building activity—the city leads in LEED-certified construction, solar installations, and energy-efficient upgrades. Seattle construction permits also reflect the region's seismic concerns, with retrofit and structural reinforcement projects common in older buildings.</p>
            <p>Contractors pursuing Seattle building permits benefit from accessing new filings before they become public knowledge. PermitGrab pulls permit data from official Seattle sources daily, delivering contractor leads for every specialty from plumbing to electrical to general construction.</p>
        """
    },
    "new-orleans": {
        "name": "New Orleans",
        "state": "LA",
        "meta_title": "New Orleans Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse 2000+ active building permits in New Orleans. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>New Orleans has a vibrant construction market shaped by the city's unique architecture, climate, and ongoing revitalization efforts. New Orleans building permits cover historic preservation in the French Quarter, residential renovations in the Garden District, and new construction in rapidly developing neighborhoods like the Bywater and Mid-City.</p>
            <p>The New Orleans construction industry requires specialized knowledge—hurricane-resistant construction, moisture control, foundation work in challenging soil conditions, and historic preservation standards create demand for skilled contractors. NOLA construction permits reflect seasonal patterns, with roofing and exterior work concentrated outside hurricane season.</p>
            <p>For contractors seeking New Orleans building permits and construction leads, local market knowledge combined with fast permit access creates winning opportunities. PermitGrab delivers fresh NOLA permit data to help you find and win jobs throughout the Crescent City.</p>
        """
    },
    "baton-rouge": {
        "name": "Baton Rouge",
        "state": "LA",
        "meta_title": "Baton Rouge Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse 1200+ active building permits in Baton Rouge. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Baton Rouge's construction market benefits from Louisiana's capital city status and growing economy. Baton Rouge building permits cover residential construction in areas like Prairieville and Denham Springs, commercial development along I-10 and I-12 corridors, and renovation projects throughout East Baton Rouge Parish.</p>
            <p>The Baton Rouge construction industry reflects regional priorities—flood mitigation, hurricane-resistant construction, and energy-efficient HVAC systems are common project types. BR construction permits also include significant industrial and petrochemical-related construction given the area's economic base.</p>
            <p>Contractors pursuing Baton Rouge building permits find steady work in this growing market. PermitGrab delivers fresh EBR permit data daily, connecting contractors with property owners who need quality work done right.</p>
        """
    },
    "nashville": {
        "name": "Nashville",
        "state": "TN",
        "meta_title": "Nashville Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse 900+ active building permits in Nashville. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Nashville is one of America's hottest construction markets, with unprecedented growth driving demand for every type of contractor. Nashville building permits reflect the city's transformation—luxury condo towers downtown, new residential developments in surrounding counties, and renovations in trendy neighborhoods like East Nashville and The Nations.</p>
            <p>The Nashville construction industry benefits from the city's booming entertainment, healthcare, and corporate relocation activity. Music City construction permits include high-end residential work, commercial tenant improvements, and hospitality projects serving the tourism industry. HVAC installation is critical given Tennessee's hot summers.</p>
            <p>For contractors seeking Nashville building permits, getting to leads first is essential in this competitive market. PermitGrab delivers fresh Nashville permit data daily, giving you the inside track on new construction projects throughout Davidson County.</p>
        """
    },
    "atlanta": {
        "name": "Atlanta",
        "state": "GA",
        "meta_title": "Atlanta Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse 500+ active building permits in Atlanta. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Atlanta's construction market is booming, fueled by corporate relocations, population growth, and major infrastructure investments. Atlanta building permits span high-rise development in Midtown and Buckhead, residential construction in metro Atlanta suburbs, and renovation projects in historic neighborhoods like Virginia-Highland and Inman Park.</p>
            <p>The Atlanta construction industry reflects the region's diversity—from luxury home construction in North Fulton to commercial buildouts in the Perimeter area to adaptive reuse projects in emerging neighborhoods. ATL construction permits include significant HVAC and electrical work given the hot Georgia summers and aging housing stock.</p>
            <p>Contractors pursuing Atlanta building permits compete in a fast-moving market where early access to permits means more wins. PermitGrab delivers fresh Atlanta permit data daily, connecting you with property owners and developers who need quality contractors now.</p>
        """
    },
    "cincinnati": {
        "name": "Cincinnati",
        "state": "OH",
        "meta_title": "Cincinnati Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse 300+ active building permits in Cincinnati. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Cincinnati's construction market is experiencing a renaissance, with major investments in downtown development and neighborhood revitalization. Cincinnati building permits cover riverfront development projects, residential renovations in historic neighborhoods like Over-the-Rhine and Mount Adams, and commercial construction throughout Hamilton County.</p>
            <p>The Cincinnati construction industry benefits from the city's aging housing stock—Victorian-era homes require ongoing maintenance, window replacements, roofing projects, and interior renovations. Cincy construction permits also reflect the region's industrial legacy with many warehouse-to-residential conversions and adaptive reuse projects.</p>
            <p>For contractors seeking Cincinnati building permits, accessing new filings quickly means beating the competition to quality leads. PermitGrab delivers fresh Cincinnati permit data daily, helping you find and win jobs throughout the Queen City.</p>
        """
    },
    "cambridge": {
        "name": "Cambridge",
        "state": "MA",
        "meta_title": "Cambridge Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Cambridge, MA. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Cambridge, home to Harvard and MIT, has a thriving construction market driven by academic institutions, biotech companies, and residential demand. Cambridge building permits span laboratory construction, commercial office space, and renovations to the city's historic housing stock.</p>
            <p>The Cambridge construction industry benefits from the city's density and ongoing development around Kendall Square and Central Square. Cambridge construction permits reflect strong demand for HVAC, electrical, and plumbing work in both commercial and residential sectors.</p>
            <p>For contractors seeking Cambridge building permits, timing is key in this competitive market. PermitGrab delivers fresh Cambridge permit data daily, helping you connect with project owners across Middlesex County.</p>
        """
    },
    "washington-dc": {
        "name": "Washington DC",
        "state": "DC",
        "meta_title": "Washington DC Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Washington DC. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Washington DC's construction market is driven by government buildings, commercial development, and a dense residential market. DC building permits cover everything from federal facility renovations to row house restorations in Capitol Hill, Georgetown, and Adams Morgan.</p>
            <p>The DC construction industry benefits from constant government investment and the city's historic preservation requirements. Washington DC construction permits reflect strong demand for structural work, window replacements, and interior renovations in the city's iconic architecture.</p>
            <p>For contractors seeking DC building permits, quick access to new filings means getting ahead of the competition. PermitGrab delivers fresh Washington DC permit data daily, helping you win contracts across the District.</p>
        """
    },
    "san-antonio": {
        "name": "San Antonio",
        "state": "TX",
        "meta_title": "San Antonio Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in San Antonio. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>San Antonio is one of the fastest-growing cities in Texas, with a booming construction market across residential and commercial sectors. San Antonio building permits cover new home construction, commercial development along the I-35 corridor, and renovations throughout Bexar County.</p>
            <p>The San Antonio construction industry benefits from the city's affordable land and strong population growth. San Antonio construction permits reflect high demand for HVAC in the Texas heat, roofing projects, and general construction work.</p>
            <p>For contractors seeking San Antonio building permits, early access to new filings is essential. PermitGrab delivers fresh San Antonio permit data daily, helping you connect with property owners across the Alamo City.</p>
        """
    },
    "kansas-city": {
        "name": "Kansas City",
        "state": "MO",
        "meta_title": "Kansas City Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Kansas City. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Kansas City's construction market spans both Missouri and Kansas, with major development in downtown KC, the Plaza, and surrounding suburbs. Kansas City building permits cover commercial construction, residential development, and renovations across the metro area.</p>
            <p>The KC construction industry benefits from the region's central location and ongoing revitalization efforts. Kansas City construction permits reflect demand across all trades, from HVAC and electrical to general construction and roofing.</p>
            <p>For contractors seeking Kansas City building permits, quick access to permit data helps you beat the competition. PermitGrab delivers fresh KC permit data daily, helping you find quality leads across the metro.</p>
        """
    },
    "detroit": {
        "name": "Detroit",
        "state": "MI",
        "meta_title": "Detroit Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Detroit. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Detroit's construction market is experiencing a renaissance, with major investments in downtown development and neighborhood revitalization. Detroit building permits cover commercial construction in the central business district, residential renovations across the city's historic neighborhoods, and industrial development.</p>
            <p>The Detroit construction industry benefits from the city's comeback story—historic buildings being restored, new developments rising, and a growing population demanding quality contractors. Detroit construction permits reflect strong demand for renovation work, electrical upgrades, and HVAC installations.</p>
            <p>For contractors seeking Detroit building permits, early access to new filings is crucial. PermitGrab delivers fresh Detroit permit data daily, helping you win jobs across the Motor City.</p>
        """
    },
    "pittsburgh": {
        "name": "Pittsburgh",
        "state": "PA",
        "meta_title": "Pittsburgh Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Pittsburgh. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Pittsburgh's construction market is thriving, driven by tech industry growth, healthcare development, and residential demand. Pittsburgh building permits cover commercial construction downtown, university expansions, and renovations in neighborhoods like Shadyside, Lawrenceville, and the South Side.</p>
            <p>The Pittsburgh construction industry benefits from the city's transformation from industrial powerhouse to tech hub. Pittsburgh construction permits reflect strong demand for HVAC, electrical, and renovation work in both commercial and residential sectors.</p>
            <p>For contractors seeking Pittsburgh building permits, quick access to new filings helps you connect with project owners first. PermitGrab delivers fresh Pittsburgh permit data daily across Allegheny County.</p>
        """
    },
    "denver": {
        "name": "Denver",
        "state": "CO",
        "meta_title": "Denver Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Denver. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Denver's construction market is one of the hottest in the nation, with explosive growth in both residential and commercial development. Denver building permits cover high-rise construction downtown, residential development across the metro, and renovations throughout the Front Range.</p>
            <p>The Denver construction industry benefits from the city's population boom and strong economy. Denver construction permits reflect high demand for all trades—HVAC, electrical, plumbing, roofing, and general construction work are all in constant demand.</p>
            <p>For contractors seeking Denver building permits, timing is everything in this competitive market. PermitGrab delivers fresh Denver permit data daily, helping you win contracts across the Mile High City.</p>
        """
    },
    "portland": {
        "name": "Portland",
        "state": "OR",
        "meta_title": "Portland Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Portland, OR. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Portland's construction market reflects the city's commitment to sustainability and urban density. Portland building permits cover green building projects, residential development, ADU construction, and commercial renovations throughout Multnomah County.</p>
            <p>The Portland construction industry benefits from the city's unique building codes and environmental focus. Portland construction permits reflect strong demand for energy-efficient upgrades, solar installations, and sustainable building practices.</p>
            <p>For contractors seeking Portland building permits, early access to new filings helps you connect with eco-conscious project owners. PermitGrab delivers fresh Portland permit data daily, helping you win jobs across the Rose City.</p>
        """
    },
    "miami": {
        "name": "Miami-Dade County",
        "state": "FL",
        "meta_title": "Miami Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Miami-Dade County. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Miami's construction market is among the most active in the nation, with constant development across residential, commercial, and hospitality sectors. Miami building permits cover high-rise condo construction, luxury home development, and renovations throughout Miami-Dade County.</p>
            <p>The Miami construction industry benefits from the region's year-round building season and strong demand from domestic and international buyers. Miami construction permits reflect high demand for hurricane-resistant construction, HVAC work in the tropical climate, and pool construction.</p>
            <p>For contractors seeking Miami building permits, quick access to new filings is essential in this competitive market. PermitGrab delivers fresh Miami permit data daily, helping you win contracts across South Florida.</p>
        """
    },
    "raleigh": {
        "name": "Raleigh",
        "state": "NC",
        "meta_title": "Raleigh Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Raleigh, NC. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Raleigh's construction market is booming as part of the Research Triangle's explosive growth. Raleigh building permits cover residential development, commercial construction, and tech campus expansions throughout Wake County.</p>
            <p>The Raleigh construction industry benefits from the region's strong job growth and influx of new residents. Raleigh construction permits reflect high demand for new home construction, HVAC installations, and commercial build-outs.</p>
            <p>For contractors seeking Raleigh building permits, early access to permit data helps you stay ahead of the competition. PermitGrab delivers fresh Raleigh permit data daily, helping you win jobs across the Triangle.</p>
        """
    },
    "phoenix": {
        "name": "Phoenix",
        "state": "AZ",
        "meta_title": "Phoenix Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Phoenix. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Phoenix's construction market is one of the largest in the Southwest, with constant development across the Valley of the Sun. Phoenix building permits cover new home construction, commercial development, and renovations throughout Maricopa County.</p>
            <p>The Phoenix construction industry benefits from year-round building weather and strong population growth. Phoenix construction permits reflect high demand for HVAC in the desert heat, pool construction, and solar installations.</p>
            <p>For contractors seeking Phoenix building permits, quick access to new filings is crucial. PermitGrab delivers fresh Phoenix permit data daily, helping you win contracts across the Valley.</p>
        """
    },
    "san-jose": {
        "name": "San Jose",
        "state": "CA",
        "meta_title": "San Jose Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in San Jose. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>San Jose's construction market is driven by Silicon Valley's tech industry and strong housing demand. San Jose building permits cover tech campus construction, residential development, and ADU projects throughout Santa Clara County.</p>
            <p>The San Jose construction industry benefits from the region's high property values and constant development pressure. San Jose construction permits reflect strong demand for electrical work, seismic retrofitting, and energy-efficient upgrades.</p>
            <p>For contractors seeking San Jose building permits, timing is key in this premium market. PermitGrab delivers fresh San Jose permit data daily, helping you connect with project owners across the South Bay.</p>
        """
    },
    "san-diego": {
        "name": "San Diego",
        "state": "CA",
        "meta_title": "San Diego Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in San Diego. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>San Diego's construction market benefits from year-round building weather and strong residential demand. San Diego building permits cover new home construction, ADU development, and commercial projects throughout San Diego County.</p>
            <p>The San Diego construction industry reflects the region's military presence, biotech sector, and tourism industry. San Diego construction permits show strong demand for HVAC, solar installations, and pool construction.</p>
            <p>For contractors seeking San Diego building permits, early access to permit data helps you win more jobs. PermitGrab delivers fresh San Diego permit data daily, helping you grow your business across America's Finest City.</p>
        """
    },
    "sacramento": {
        "name": "Sacramento",
        "state": "CA",
        "meta_title": "Sacramento Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Sacramento. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Sacramento's construction market is thriving as California's capital attracts new residents and businesses. Sacramento building permits cover new home construction, commercial development, and renovations throughout the Sacramento Valley.</p>
            <p>The Sacramento construction industry benefits from the region's more affordable land compared to the Bay Area. Sacramento construction permits reflect strong demand for HVAC in the hot summers, roofing, and residential construction.</p>
            <p>For contractors seeking Sacramento building permits, quick access to new filings helps you compete effectively. PermitGrab delivers fresh Sacramento permit data daily, helping you win contracts across the region.</p>
        """
    },
    "boston": {
        "name": "Boston",
        "state": "MA",
        "meta_title": "Boston Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Boston. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Boston's construction market is driven by world-class universities, healthcare institutions, and a dense residential market. Boston building permits cover commercial development in the Seaport, residential renovations in historic neighborhoods, and institutional construction throughout Greater Boston.</p>
            <p>The Boston construction industry benefits from the region's strong economy and aging housing stock requiring constant maintenance. Boston construction permits reflect high demand for HVAC, electrical upgrades, and renovation work in the city's historic buildings.</p>
            <p>For contractors seeking Boston building permits, early access to permit data is essential. PermitGrab delivers fresh Boston permit data daily, helping you win jobs across the Greater Boston area.</p>
        """
    },
    "philadelphia": {
        "name": "Philadelphia",
        "state": "PA",
        "meta_title": "Philadelphia Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Philadelphia. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Philadelphia's construction market is experiencing strong growth, with major development downtown and in surrounding neighborhoods. Philadelphia building permits cover commercial construction, residential renovations in historic rowhomes, and new development across the city.</p>
            <p>The Philadelphia construction industry benefits from the city's affordability relative to NYC and DC. Philly construction permits reflect strong demand for HVAC, electrical work, and renovations in the city's historic housing stock.</p>
            <p>For contractors seeking Philadelphia building permits, quick access to new filings helps you stay competitive. PermitGrab delivers fresh Philly permit data daily, helping you win contracts across the City of Brotherly Love.</p>
        """
    },
    "baltimore": {
        "name": "Baltimore",
        "state": "MD",
        "meta_title": "Baltimore Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Baltimore. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Baltimore's construction market is driven by waterfront development, historic preservation, and neighborhood revitalization. Baltimore building permits cover commercial projects in the Inner Harbor, residential renovations in rowhouse neighborhoods, and institutional construction.</p>
            <p>The Baltimore construction industry benefits from major redevelopment initiatives and proximity to Washington DC. Baltimore construction permits reflect strong demand for renovation work, HVAC upgrades, and historic preservation projects.</p>
            <p>For contractors seeking Baltimore building permits, quick access to new filings is essential. PermitGrab delivers fresh Baltimore permit data daily, helping you win contracts across Charm City.</p>
        """
    },
    "charlotte": {
        "name": "Charlotte",
        "state": "NC",
        "meta_title": "Charlotte Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Charlotte. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Charlotte's construction market is booming as one of the fastest-growing cities in the Southeast. Charlotte building permits cover high-rise construction uptown, residential development across Mecklenburg County, and commercial projects throughout the metro.</p>
            <p>The Charlotte construction industry benefits from strong population growth and corporate relocations. Charlotte construction permits reflect high demand for new home construction, commercial build-outs, and HVAC installations.</p>
            <p>For contractors seeking Charlotte building permits, early access to permit data helps you stay competitive. PermitGrab delivers fresh Charlotte permit data daily, helping you win jobs across the Queen City.</p>
        """
    },
    "columbus": {
        "name": "Columbus",
        "state": "OH",
        "meta_title": "Columbus Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Columbus, OH. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Columbus is Ohio's fastest-growing city with a thriving construction market. Columbus building permits cover commercial development downtown, residential growth in suburbs like Dublin and Westerville, and university-related construction around Ohio State.</p>
            <p>The Columbus construction industry benefits from a diverse economy and strong housing demand. Columbus construction permits reflect steady demand for new construction, renovations, and commercial projects.</p>
            <p>For contractors seeking Columbus building permits, quick access to new filings helps you win more jobs. PermitGrab delivers fresh Columbus permit data daily across Franklin County.</p>
        """
    },
    "fort-worth": {
        "name": "Fort Worth",
        "state": "TX",
        "meta_title": "Fort Worth Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Fort Worth. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Fort Worth's construction market is thriving as part of the Dallas-Fort Worth metroplex's explosive growth. Fort Worth building permits cover residential development, commercial construction, and industrial projects across Tarrant County.</p>
            <p>The Fort Worth construction industry benefits from Texas's business-friendly environment and strong population growth. Fort Worth construction permits reflect high demand for HVAC, roofing, and general construction work.</p>
            <p>For contractors seeking Fort Worth building permits, early access to new filings is key. PermitGrab delivers fresh Fort Worth permit data daily, helping you compete across the DFW metroplex.</p>
        """
    },
    "honolulu": {
        "name": "Honolulu",
        "state": "HI",
        "meta_title": "Honolulu Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Honolulu. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Honolulu's construction market serves Hawaii's most populous island with residential, commercial, and hospitality projects. Honolulu building permits cover high-rise condos, resort renovations, and residential construction across Oahu.</p>
            <p>The Honolulu construction industry is unique with island-specific challenges and opportunities. Honolulu construction permits reflect steady demand for renovation work, HVAC installations, and new development.</p>
            <p>For contractors seeking Honolulu building permits, quick access to new filings is valuable. PermitGrab delivers fresh Honolulu permit data daily, helping you win contracts across the island.</p>
        """
    },
    "indianapolis": {
        "name": "Indianapolis",
        "state": "IN",
        "meta_title": "Indianapolis Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Indianapolis. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Indianapolis has a strong construction market driven by downtown development and suburban growth. Indianapolis building permits cover commercial construction, residential development across Marion County, and industrial projects.</p>
            <p>The Indianapolis construction industry benefits from the city's central location and affordable market. Indy construction permits reflect steady demand for all trades from HVAC to general construction.</p>
            <p>For contractors seeking Indianapolis building permits, early access to permit data is essential. PermitGrab delivers fresh Indy permit data daily, helping you grow your business across the Circle City.</p>
        """
    },
    "louisville": {
        "name": "Louisville",
        "state": "KY",
        "meta_title": "Louisville Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Louisville. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Louisville's construction market spans residential, commercial, and industrial development across Jefferson County. Louisville building permits cover downtown revitalization, residential growth in the Highlands and East End, and commercial construction.</p>
            <p>The Louisville construction industry benefits from the city's strategic location and growing economy. Louisville construction permits reflect demand for renovation work, new construction, and commercial build-outs.</p>
            <p>For contractors seeking Louisville building permits, quick access to new filings helps you compete. PermitGrab delivers fresh Louisville permit data daily across Kentucky's largest city.</p>
        """
    },
    "memphis": {
        "name": "Memphis",
        "state": "TN",
        "meta_title": "Memphis Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Memphis. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Memphis has a diverse construction market with residential, commercial, and industrial projects. Memphis building permits cover downtown development, residential renovations, and logistics/warehouse construction due to FedEx's headquarters.</p>
            <p>The Memphis construction industry benefits from the city's role as a logistics hub and growing economy. Memphis construction permits reflect steady demand for all construction trades.</p>
            <p>For contractors seeking Memphis building permits, early access to permit data is valuable. PermitGrab delivers fresh Memphis permit data daily, helping you win contracts across the Bluff City.</p>
        """
    },
    "mesa": {
        "name": "Mesa",
        "state": "AZ",
        "meta_title": "Mesa Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Mesa, AZ. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Mesa is Arizona's third-largest city with a thriving construction market. Mesa building permits cover residential development, commercial construction, and renovation projects across the East Valley.</p>
            <p>The Mesa construction industry benefits from Phoenix metro growth and year-round building weather. Mesa construction permits reflect high demand for HVAC, pool construction, and residential work.</p>
            <p>For contractors seeking Mesa building permits, quick access to new filings is essential. PermitGrab delivers fresh Mesa permit data daily, helping you compete across the Valley.</p>
        """
    },
    "milwaukee": {
        "name": "Milwaukee",
        "state": "WI",
        "meta_title": "Milwaukee Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Milwaukee. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Milwaukee's construction market includes residential, commercial, and industrial projects across the metro. Milwaukee building permits cover downtown development, residential renovations in historic neighborhoods, and commercial construction.</p>
            <p>The Milwaukee construction industry benefits from the city's manufacturing heritage and revitalization efforts. Milwaukee construction permits reflect strong demand for renovation work, HVAC, and general construction.</p>
            <p>For contractors seeking Milwaukee building permits, early access to permit data helps you compete. PermitGrab delivers fresh Milwaukee permit data daily across Wisconsin's largest city.</p>
        """
    },
    "oakland": {
        "name": "Oakland",
        "state": "CA",
        "meta_title": "Oakland Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Oakland. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Oakland's construction market is thriving with development across the East Bay. Oakland building permits cover high-density residential projects, commercial development, and renovations in neighborhoods from Rockridge to Jack London Square.</p>
            <p>The Oakland construction industry benefits from San Francisco Bay Area growth and relative affordability. Oakland construction permits reflect strong demand for residential construction and seismic retrofitting.</p>
            <p>For contractors seeking Oakland building permits, quick access to new filings is valuable. PermitGrab delivers fresh Oakland permit data daily across Alameda County.</p>
        """
    },
    "oklahoma-city": {
        "name": "Oklahoma City",
        "state": "OK",
        "meta_title": "Oklahoma City Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Oklahoma City. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Oklahoma City has a strong construction market driven by energy industry investment and population growth. OKC building permits cover commercial development downtown, residential growth in suburbs like Edmond, and industrial construction.</p>
            <p>The Oklahoma City construction industry benefits from affordable land and business-friendly policies. OKC construction permits reflect steady demand for all construction trades.</p>
            <p>For contractors seeking Oklahoma City building permits, early access to permit data is key. PermitGrab delivers fresh OKC permit data daily across the metro area.</p>
        """
    },
    "omaha": {
        "name": "Omaha",
        "state": "NE",
        "meta_title": "Omaha Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Omaha. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Omaha's construction market serves Nebraska's largest city with residential, commercial, and industrial projects. Omaha building permits cover downtown development, suburban residential growth, and commercial construction throughout Douglas County.</p>
            <p>The Omaha construction industry benefits from a stable economy and steady population growth. Omaha construction permits reflect consistent demand for new construction and renovation work.</p>
            <p>For contractors seeking Omaha building permits, quick access to new filings helps you compete. PermitGrab delivers fresh Omaha permit data daily across the metro.</p>
        """
    },
    "st-louis": {
        "name": "St. Louis",
        "state": "MO",
        "meta_title": "St. Louis Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in St. Louis. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>St. Louis has a diverse construction market with residential, commercial, and institutional projects. St. Louis building permits cover downtown revitalization, residential renovations in historic neighborhoods, and commercial development.</p>
            <p>The St. Louis construction industry benefits from the city's affordability and architectural heritage. STL construction permits reflect strong demand for renovation work, HVAC, and general construction.</p>
            <p>For contractors seeking St. Louis building permits, early access to permit data is valuable. PermitGrab delivers fresh St. Louis permit data daily across the Gateway City.</p>
        """
    },
    "tucson": {
        "name": "Tucson",
        "state": "AZ",
        "meta_title": "Tucson Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Tucson. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Tucson's construction market serves Arizona's second-largest city with residential, commercial, and university-related projects. Tucson building permits cover new home construction, commercial development, and renovation work across Pima County.</p>
            <p>The Tucson construction industry benefits from year-round building weather and steady growth. Tucson construction permits reflect high demand for HVAC, pool construction, and solar installations.</p>
            <p>For contractors seeking Tucson building permits, quick access to new filings is essential. PermitGrab delivers fresh Tucson permit data daily across Southern Arizona.</p>
        """
    },
    "long-beach": {
        "name": "Long Beach",
        "state": "CA",
        "meta_title": "Long Beach Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Long Beach. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Long Beach has a thriving construction market as LA County's second-largest city. Long Beach building permits cover waterfront development, residential renovations, and commercial construction throughout the port city.</p>
            <p>The Long Beach construction industry benefits from port-related development and residential demand. Long Beach construction permits reflect steady work for all trades.</p>
            <p>For contractors seeking Long Beach building permits, early access to permit data helps you compete. PermitGrab delivers fresh Long Beach permit data daily.</p>
        """
    },
    "fresno": {
        "name": "Fresno",
        "state": "CA",
        "meta_title": "Fresno Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Fresno. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Fresno's construction market serves California's Central Valley with residential, commercial, and agricultural projects. Fresno building permits cover new home construction, commercial development, and renovation work throughout Fresno County.</p>
            <p>The Fresno construction industry benefits from affordable land and California's housing demand. Fresno construction permits reflect steady work across all construction trades.</p>
            <p>For contractors seeking Fresno building permits, quick access to new filings is valuable. PermitGrab delivers fresh Fresno permit data daily across the Valley.</p>
        """
    },
    "las-vegas": {
        "name": "Las Vegas",
        "state": "NV",
        "meta_title": "Las Vegas Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Las Vegas. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Las Vegas has one of the most dynamic construction markets in the country. Las Vegas building permits cover casino and resort development, residential construction in rapidly growing suburbs, and commercial projects across Clark County.</p>
            <p>The Las Vegas construction industry benefits from constant tourism investment and population growth. Vegas construction permits reflect high demand for HVAC, pool construction, and commercial build-outs.</p>
            <p>For contractors seeking Las Vegas building permits, early access to permit data is essential. PermitGrab delivers fresh Las Vegas permit data daily, helping you win contracts across the Valley.</p>
        """
    },
    "orlando": {
        "name": "Orlando",
        "state": "FL",
        "meta_title": "Orlando Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Orlando. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Orlando's construction market is driven by tourism, population growth, and theme park expansion. Orlando building permits cover hospitality projects, residential development across Orange County, and commercial construction throughout Central Florida.</p>
            <p>The Orlando construction industry benefits from year-round building weather and strong economic growth. Orlando construction permits reflect high demand for all construction trades.</p>
            <p>For contractors seeking Orlando building permits, quick access to new filings helps you compete. PermitGrab delivers fresh Orlando permit data daily across the I-4 corridor.</p>
        """
    },
    "tampa": {
        "name": "Tampa",
        "state": "FL",
        "meta_title": "Tampa Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Tampa. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Tampa's construction market is thriving with waterfront development, residential growth, and commercial projects. Tampa building permits cover downtown high-rises, residential construction across Hillsborough County, and commercial development.</p>
            <p>The Tampa construction industry benefits from Florida's growth and year-round building weather. Tampa construction permits reflect high demand for HVAC, hurricane-resistant construction, and pool work.</p>
            <p>For contractors seeking Tampa building permits, early access to permit data is key. PermitGrab delivers fresh Tampa permit data daily across the Tampa Bay area.</p>
        """
    },
    "jacksonville": {
        "name": "Jacksonville",
        "state": "FL",
        "meta_title": "Jacksonville Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Jacksonville. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Jacksonville is Florida's largest city by area with a diverse construction market. Jacksonville building permits cover residential development, commercial construction, and port-related projects across Duval County.</p>
            <p>The Jacksonville construction industry benefits from affordable land and Florida's population growth. Jax construction permits reflect steady demand for all construction trades.</p>
            <p>For contractors seeking Jacksonville building permits, quick access to new filings is valuable. PermitGrab delivers fresh Jacksonville permit data daily across Northeast Florida.</p>
        """
    },
    "virginia-beach": {
        "name": "Virginia Beach",
        "state": "VA",
        "meta_title": "Virginia Beach Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Virginia Beach. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Virginia Beach has a strong construction market serving the Hampton Roads region. Virginia Beach building permits cover residential development, commercial construction, and military-related projects.</p>
            <p>The Virginia Beach construction industry benefits from military investment and tourism. VB construction permits reflect steady demand for renovation work and new construction.</p>
            <p>For contractors seeking Virginia Beach building permits, early access to permit data helps you compete. PermitGrab delivers fresh Virginia Beach permit data daily.</p>
        """
    },
    "albuquerque": {
        "name": "Albuquerque",
        "state": "NM",
        "meta_title": "Albuquerque Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Albuquerque. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Albuquerque's construction market serves New Mexico's largest city with residential, commercial, and institutional projects. Albuquerque building permits cover new home construction, commercial development, and renovation work across Bernalillo County.</p>
            <p>The Albuquerque construction industry benefits from film industry growth and steady population. ABQ construction permits reflect demand for HVAC, solar, and general construction work.</p>
            <p>For contractors seeking Albuquerque building permits, quick access to new filings is valuable. PermitGrab delivers fresh Albuquerque permit data daily.</p>
        """
    },
    "cleveland": {
        "name": "Cleveland",
        "state": "OH",
        "meta_title": "Cleveland Building Permits & Contractor Leads | PermitGrab",
        "meta_description": "Browse active building permits in Cleveland. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
        "seo_content": """
            <p>Cleveland's construction market includes downtown revitalization, residential renovations, and healthcare/university projects. Cleveland building permits cover commercial construction, residential work in neighborhoods like Ohio City and Tremont, and institutional development.</p>
            <p>The Cleveland construction industry benefits from major hospital systems and ongoing revitalization. Cleveland construction permits reflect strong demand for renovation work and HVAC.</p>
            <p>For contractors seeking Cleveland building permits, early access to permit data is essential. PermitGrab delivers fresh Cleveland permit data daily across Cuyahoga County.</p>
        """
    },
}

# Dynamic city list from city_configs.py
def get_all_cities_list():
    """Get all active cities for navigation."""
    return [{"slug": c["slug"], "name": c["name"]} for c in get_all_cities_info()]

ALL_CITIES = get_all_cities_list()

# V12.23 SEO: State hub pages
# V12.28 MEGA EXPANSION: Added 28 new states (38 total with 3+ cities)
STATE_CONFIG = {
    'texas': {'name': 'Texas', 'abbrev': 'TX'},
    'california': {'name': 'California', 'abbrev': 'CA'},
    'maryland': {'name': 'Maryland', 'abbrev': 'MD'},
    'colorado': {'name': 'Colorado', 'abbrev': 'CO'},
    'florida': {'name': 'Florida', 'abbrev': 'FL'},
    'louisiana': {'name': 'Louisiana', 'abbrev': 'LA'},
    'new-york-state': {'name': 'New York', 'abbrev': 'NY'},
    'illinois': {'name': 'Illinois', 'abbrev': 'IL'},
    'ohio': {'name': 'Ohio', 'abbrev': 'OH'},
    'washington': {'name': 'Washington', 'abbrev': 'WA'},
    # V12.28 New state hubs
    'arizona': {'name': 'Arizona', 'abbrev': 'AZ'},
    'massachusetts': {'name': 'Massachusetts', 'abbrev': 'MA'},
    'new-jersey': {'name': 'New Jersey', 'abbrev': 'NJ'},
    'north-carolina': {'name': 'North Carolina', 'abbrev': 'NC'},
    'virginia': {'name': 'Virginia', 'abbrev': 'VA'},
    'connecticut': {'name': 'Connecticut', 'abbrev': 'CT'},
    'utah': {'name': 'Utah', 'abbrev': 'UT'},
    'wisconsin': {'name': 'Wisconsin', 'abbrev': 'WI'},
    'nevada': {'name': 'Nevada', 'abbrev': 'NV'},
    'iowa': {'name': 'Iowa', 'abbrev': 'IA'},
    'missouri': {'name': 'Missouri', 'abbrev': 'MO'},
    'pennsylvania': {'name': 'Pennsylvania', 'abbrev': 'PA'},
    'georgia': {'name': 'Georgia', 'abbrev': 'GA'},
    'indiana': {'name': 'Indiana', 'abbrev': 'IN'},
    'south-carolina': {'name': 'South Carolina', 'abbrev': 'SC'},
    'idaho': {'name': 'Idaho', 'abbrev': 'ID'},
    'michigan': {'name': 'Michigan', 'abbrev': 'MI'},
    'tennessee': {'name': 'Tennessee', 'abbrev': 'TN'},
    'nebraska': {'name': 'Nebraska', 'abbrev': 'NE'},
    'new-mexico': {'name': 'New Mexico', 'abbrev': 'NM'},
    'alabama': {'name': 'Alabama', 'abbrev': 'AL'},
    'kansas': {'name': 'Kansas', 'abbrev': 'KS'},
    'rhode-island': {'name': 'Rhode Island', 'abbrev': 'RI'},
    'minnesota': {'name': 'Minnesota', 'abbrev': 'MN'},
    'oregon': {'name': 'Oregon', 'abbrev': 'OR'},
    'kentucky': {'name': 'Kentucky', 'abbrev': 'KY'},
    'oklahoma': {'name': 'Oklahoma', 'abbrev': 'OK'},
    'mississippi': {'name': 'Mississippi', 'abbrev': 'MS'},
}


def get_state_data(state_slug):
    """Get aggregated data for a state hub page.
    V12.36: Fixed to use normalized city names for accurate counting.
    V12.51: Uses SQLite aggregation for efficiency.
    """
    if state_slug not in STATE_CONFIG:
        return None

    state_info = STATE_CONFIG[state_slug]
    state_abbrev = state_info['abbrev']

    # V12.32: Get all cities in this state, including auto-discovered bulk source cities
    state_cities = get_cities_by_state_auto(state_abbrev)

    # V12.51: Use SQL aggregation for city stats
    # V13.7: Include cities that were misassigned to other states using same heuristics
    conn = permitdb.get_connection()

    # Known cities for state correction heuristics (same as get_cities_with_data)
    KNOWN_OK_CITIES = {
        'oklahoma city', 'tulsa', 'norman', 'broken arrow', 'edmond',
        'lawton', 'moore', 'midwest city', 'enid', 'stillwater',
        'muskogee', 'bartlesville', 'owasso', 'shawnee', 'ponca city'
    }
    KNOWN_NV_CITIES = {
        'las vegas', 'henderson', 'reno', 'north las vegas', 'sparks',
        'carson city', 'elko', 'mesquite', 'boulder city', 'fernley'
    }

    if state_abbrev == 'TX':
        # TX gets its own permits PLUS misassigned OK/NV permits
        cursor = conn.execute("""
            SELECT city, state, COUNT(*) as permit_count, COALESCE(SUM(estimated_cost), 0) as total_value
            FROM permits
            WHERE state IN ('TX', 'OK', 'NV')
            GROUP BY city, state
        """)
    else:
        cursor = conn.execute("""
            SELECT city, state, COUNT(*) as permit_count, COALESCE(SUM(estimated_cost), 0) as total_value
            FROM permits
            WHERE state = ?
            GROUP BY city, state
        """, (state_abbrev,))

    # Build city stats from SQL
    city_permit_counts = {}
    city_values = {}
    city_display_names = {}

    for row in cursor:
        city_name = row['city'] or ''
        row_state = row['state'] if 'state' in row.keys() else state_abbrev
        if not city_name:
            continue

        norm_name = ' '.join(city_name.lower().split())

        # V13.7: Apply state correction heuristics for TX state hub
        if state_abbrev == 'TX':
            # Include TX cities directly
            if row_state == 'TX':
                pass  # Include
            # Include OK cities that are NOT in KNOWN_OK_CITIES (they're actually TX)
            elif row_state == 'OK' and norm_name not in KNOWN_OK_CITIES:
                pass  # Include
            # Include NV cities that are NOT in KNOWN_NV_CITIES (they're actually TX)
            elif row_state == 'NV' and norm_name not in KNOWN_NV_CITIES:
                pass  # Include
            else:
                continue  # Skip actual OK/NV cities
        else:
            # For other states, only include cities with matching state
            if row_state != state_abbrev:
                continue

        # Aggregate permit counts (in case same city appears with different states)
        if norm_name in city_permit_counts:
            city_permit_counts[norm_name] += row['permit_count']
            city_values[norm_name] += row['total_value']
        else:
            city_permit_counts[norm_name] = row['permit_count']
            city_values[norm_name] = row['total_value']

        # Keep track of display name (prefer title case)
        if norm_name not in city_display_names or city_name.istitle():
            city_display_names[norm_name] = city_name

    # Add counts to city info, matching by normalized name
    cities_with_data = []
    seen_norm_names = set()  # Prevent duplicates in output

    for c in state_cities:
        norm_name = ' '.join(c['name'].lower().split())
        if norm_name in seen_norm_names:
            continue  # Skip duplicate

        city_data = c.copy()
        city_data['permit_count'] = city_permit_counts.get(norm_name, 0)
        city_data['total_value'] = city_values.get(norm_name, 0)

        # Use best display name if available
        if norm_name in city_display_names:
            display_name = city_display_names[norm_name]
            # Prefer title case version
            if display_name.istitle():
                city_data['name'] = display_name

        if city_data['permit_count'] > 0:
            cities_with_data.append(city_data)
            seen_norm_names.add(norm_name)

    # V13.6: Add cities from DB that have permits but aren't in registry (e.g., Houston)
    # V13.7: Minimum threshold to avoid showing tiny cities on state hub
    MIN_STATE_HUB_PERMITS = 50
    for norm_name, permit_count in city_permit_counts.items():
        if norm_name not in seen_norm_names and permit_count >= MIN_STATE_HUB_PERMITS:
            display_name = city_display_names.get(norm_name, norm_name.title())
            slug = display_name.lower().replace(' ', '-').replace(',', '').replace('.', '')
            cities_with_data.append({
                'name': display_name,
                'state': state_abbrev,
                'slug': slug,
                'permit_count': permit_count,
                'total_value': city_values.get(norm_name, 0),
                'active': True,
            })
            seen_norm_names.add(norm_name)

    # Sort by permit count
    cities_with_data.sort(key=lambda x: x['permit_count'], reverse=True)

    # Calculate totals
    total_permits = sum(city_permit_counts.values())
    total_value = sum(city_values.values())

    return {
        'state_name': state_info['name'],
        'state_slug': state_slug,
        'cities': cities_with_data,
        'total_permits': total_permits,
        'total_value': total_value,
    }


@app.route('/permits/<state_slug>')
def state_or_city_landing(state_slug):
    """Route that handles both state hub pages and city landing pages."""
    # Check if it's a state slug first
    if state_slug in STATE_CONFIG:
        state_data = get_state_data(state_slug)
        if state_data and state_data['cities']:
            footer_cities = get_cities_with_data()

            # V14.0: Find blog posts for cities in this state
            state_blog_posts = []
            state_abbrev = STATE_CONFIG[state_slug]['abbrev'].lower()
            blog_dir = os.path.join(os.path.dirname(__file__), 'blog')
            for city in state_data['cities'][:20]:  # Check top 20 cities
                city_slug = city.get('slug', city['name'].lower().replace(' ', '-').replace(',', ''))
                blog_slug = f"building-permits-{city_slug}-{state_abbrev}-contractor-guide"
                blog_path = os.path.join(blog_dir, f"{blog_slug}.md")
                if os.path.exists(blog_path):
                    state_blog_posts.append({
                        'name': city['name'],
                        'url': f"/blog/{blog_slug}"
                    })

            return render_template('state_landing.html',
                                   footer_cities=footer_cities,
                                   state_blog_posts=state_blog_posts,
                                   **state_data)

    # Otherwise, fall through to city landing page logic
    return city_landing_inner(state_slug)


def city_landing_inner(city_slug):
    """Render SEO-optimized city landing page."""
    # V15: Check prod_cities status for this city
    is_prod_city = False
    prod_city_status = None
    city_freshness = 'fresh'
    newest_permit_date = None

    try:
        if permitdb.prod_cities_table_exists():
            is_prod_city, prod_city_status = permitdb.is_prod_city(city_slug)

            # V18: Check if city is paused due to stale data
            if prod_city_status == 'paused':
                conn = permitdb.get_connection()
                row = conn.execute("""
                    SELECT pause_reason, newest_permit_date, city, state
                    FROM prod_cities WHERE city_slug = ?
                """, (city_slug,)).fetchone()
                if row and row['pause_reason'] == 'stale_data':
                    # Show a "data updating" page instead of normal city page
                    return render_template('city_paused.html',
                        city_name=row['city'],
                        state=row['state'],
                        last_updated=row['newest_permit_date'],
                        canonical_url=f"{SITE_URL}/permits/{city_slug}",
                        robots="noindex, follow"
                    )

            # V18: Get freshness info for stale indicator
            if is_prod_city:
                conn = permitdb.get_connection()
                row = conn.execute("""
                    SELECT data_freshness, newest_permit_date
                    FROM prod_cities WHERE city_slug = ?
                """, (city_slug,)).fetchone()
                if row:
                    city_freshness = row['data_freshness'] or 'fresh'
                    newest_permit_date = row['newest_permit_date']
    except Exception as e:
        print(f"[V15] Error checking prod_cities: {e}")

    # Check for SEO config, or create fallback from city_configs
    if city_slug in CITY_SEO_CONFIG:
        config = CITY_SEO_CONFIG[city_slug]
    else:
        # V12.32: Try both explicit configs AND auto-discovered bulk source cities
        city_key, city_config = get_city_by_slug_auto(city_slug)
        if not city_config:
            # V18: Slug fallback - handle city-state format (e.g., san-antonio-tx -> san-antonio)
            # Check if slug ends with a state abbreviation suffix
            import re
            state_suffix_match = re.match(r'^(.+)-([a-z]{2})$', city_slug)
            if state_suffix_match:
                bare_slug = state_suffix_match.group(1)
                state_suffix = state_suffix_match.group(2).upper()
                # Verify it's a valid US state abbreviation
                VALID_STATE_ABBREVS = {
                    'AL', 'AK', 'AZ', 'AR', 'CA', 'CO', 'CT', 'DE', 'DC', 'FL', 'GA',
                    'HI', 'ID', 'IL', 'IN', 'IA', 'KS', 'KY', 'LA', 'ME', 'MD', 'MA',
                    'MI', 'MN', 'MS', 'MO', 'MT', 'NE', 'NV', 'NH', 'NJ', 'NM', 'NY',
                    'NC', 'ND', 'OH', 'OK', 'OR', 'PA', 'RI', 'SC', 'SD', 'TN', 'TX',
                    'UT', 'VT', 'VA', 'WA', 'WV', 'WI', 'WY'
                }
                if state_suffix in VALID_STATE_ABBREVS:
                    # Try the bare slug
                    bare_key, bare_config = get_city_by_slug_auto(bare_slug)
                    if bare_config:
                        # Redirect to canonical bare slug URL
                        return redirect(f'/permits/{bare_slug}', code=301)
            return render_city_not_found(city_slug)
        # Generate basic SEO config with properly formatted city name
        display_name = format_city_name(city_config["name"])
        config = {
            "name": display_name,
            "raw_name": city_config["name"],  # For filtering permits
            "state": city_config["state"],
            "meta_title": f"{display_name}, {city_config['state']} Building Permits & Contractor Leads | PermitGrab",
            "meta_description": f"Browse active building permits in {display_name}, {city_config['state']}. Get real-time contractor leads with contact info, project values, and trade details. Start free.",
            "seo_content": f"""
                <p>Track new building permits in {display_name}, {city_config['state']}. PermitGrab delivers fresh permit data daily, helping contractors find quality leads across the region.</p>
                <p>Access permit data including project values, contact information, and trade categories. Start browsing {display_name} construction permits today.</p>
            """
        }

    # V12.51: Use SQLite for city permits
    filter_name = config.get('raw_name', config['name'])
    filter_state = config.get('state', '')
    conn = permitdb.get_connection()

    # V32: Add state filter to prevent cross-state data pollution
    # (e.g., Newark NJ showing Oklahoma permits)
    if filter_state:
        state_clause = " AND state = ?"
        state_params = (filter_name, filter_state)
    else:
        state_clause = ""
        state_params = (filter_name,)

    # Get stats via SQL aggregation
    stats_row = conn.execute(f"""
        SELECT COUNT(*) as permit_count,
               COALESCE(SUM(estimated_cost), 0) as total_value,
               COUNT(CASE WHEN estimated_cost >= 100000 THEN 1 END) as high_value_count
        FROM permits WHERE city = ?{state_clause}
    """, state_params).fetchone()

    permit_count = stats_row['permit_count']
    total_value = stats_row['total_value']
    # V12.18: High-value = $100K+ projects (more meaningful to contractors)
    high_value_count = stats_row['high_value_count']

    # Get permits for display (limited set, sorted by value)
    cursor = conn.execute(f"""
        SELECT * FROM permits WHERE city = ?{state_clause} ORDER BY estimated_cost DESC LIMIT 100
    """, state_params)
    city_permits = [dict(row) for row in cursor]

    # V15: noindex for non-prod cities or empty cities
    # If prod_cities table is active and this city isn't in it, treat as coming soon
    if permitdb.prod_cities_table_exists() and not is_prod_city:
        robots_directive = "noindex, follow"
        is_coming_soon = True
    else:
        # V12.5: noindex for empty city pages to avoid thin content in Google
        robots_directive = "noindex, follow" if permit_count == 0 else "index, follow"
        # V12.11: Coming Soon flag for empty cities
        is_coming_soon = permit_count == 0

    # V12.51: Trade breakdown via SQL for full accuracy
    trade_cursor = conn.execute("""
        SELECT COALESCE(trade_category, 'Other') as trade, COUNT(*) as cnt
        FROM permits WHERE city = ? GROUP BY trade_category
    """, (filter_name,))
    trade_breakdown = {row['trade'] or 'Other': row['cnt'] for row in trade_cursor}

    # Permits already sorted by value from SQL
    sorted_permits = city_permits

    # V12.17: Add "is_new" flag for permits filed in last 7 days
    seven_days_ago = datetime.now() - timedelta(days=7)
    for p in sorted_permits:
        filing_date_str = p.get('filing_date', '')
        if filing_date_str:
            try:
                filing_date = datetime.strptime(filing_date_str, '%Y-%m-%d')
                p['is_new'] = filing_date >= seven_days_ago
            except (ValueError, TypeError):
                p['is_new'] = False
        else:
            p['is_new'] = False

    # V12.9: Calculate alternative stats for cities without value data
    # V12.51: Use SQL for accurate counts
    new_this_month = permit_count  # Fallback to total count
    unique_contractors = 0
    if total_value == 0:
        thirty_days_ago = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
        recent_row = conn.execute("""
            SELECT COUNT(*) as cnt FROM permits
            WHERE city = ? AND filing_date >= ?
        """, (filter_name, thirty_days_ago)).fetchone()
        new_this_month = recent_row['cnt'] if recent_row else permit_count

        contractor_row = conn.execute("""
            SELECT COUNT(DISTINCT LOWER(COALESCE(contractor_name, ''))) as cnt
            FROM permits WHERE city = ? AND contractor_name IS NOT NULL AND contractor_name != ''
        """, (filter_name,)).fetchone()
        unique_contractors = contractor_row['cnt'] if contractor_row and contractor_row['cnt'] > 0 else '50+'

    # V17c: Freshness badge — compute human-readable "last updated" time
    last_collected_row = conn.execute("""
        SELECT MAX(collected_at) as latest FROM permits WHERE city = ?
    """, (filter_name,)).fetchone()
    last_collected = None
    if last_collected_row and last_collected_row['latest']:
        try:
            latest_dt = datetime.strptime(last_collected_row['latest'], '%Y-%m-%d %H:%M:%S')
            delta = datetime.now() - latest_dt
            if delta.total_seconds() < 3600:
                last_collected = f"{int(delta.total_seconds() / 60)} minutes ago"
            elif delta.total_seconds() < 86400:
                last_collected = f"{int(delta.total_seconds() / 3600)} hours ago"
            elif delta.days == 1:
                last_collected = "yesterday"
            elif delta.days < 7:
                last_collected = f"{delta.days} days ago"
            else:
                last_collected = latest_dt.strftime('%b %d, %Y')
        except (ValueError, TypeError):
            last_collected = None

    # V12.60: Moved current_state assignment before its first use (was at line 5626)
    current_state = config.get('state', '')

    # V17d: Related blog articles for cross-linking SEO
    # V13.7: Fixed H6 - only show city-specific articles, not state-level
    related_articles = []
    try:
        all_posts = get_all_blog_posts()
        city_lower = config['name'].lower()
        # Only match posts that specifically mention this city (not just the state)
        for post in all_posts:
            title_lower = post.get('title', '').lower()
            keywords_lower = post.get('keywords', '').lower()
            slug_lower = post.get('slug', '').lower()
            # Match city name in title, keywords, or slug
            if city_lower in title_lower or city_lower in keywords_lower or city_lower in slug_lower:
                related_articles.append(post)
        # If no city-specific matches, add general articles (not state-level)
        if not related_articles:
            general_slugs = ['what-is-a-building-permit', 'how-to-find-construction-leads', 'construction-leads-from-building-permits']
            for post in all_posts:
                if post.get('slug') in general_slugs:
                    related_articles.append(post)
        related_articles = related_articles[:3]  # Max 3 articles
    except Exception:
        pass

    # V12.17: Other cities for footer links - sorted by permit volume
    cities_by_volume = get_cities_with_data()  # Pre-sorted by permit count descending
    other_cities = [c for c in cities_by_volume if c['slug'] != city_slug]

    # V12.17: Nearby cities sorted by permit volume (not alphabetical)
    nearby_cities = [c for c in cities_by_volume if c.get('state') == current_state and c['slug'] != city_slug]
    # If fewer than 6 same-state cities, add top cities from other states
    if len(nearby_cities) < 6:
        other_state_cities = [c for c in cities_by_volume if c.get('state') != current_state][:6 - len(nearby_cities)]
        nearby_cities = nearby_cities + other_state_cities

    # V14.0: Top neighborhoods by zip code for city enrichment
    top_neighborhoods = []
    try:
        zip_cursor = conn.execute("""
            SELECT zip_code, COUNT(*) as permit_count
            FROM permits
            WHERE city = ? AND zip_code IS NOT NULL AND zip_code != ''
            GROUP BY zip_code
            ORDER BY permit_count DESC
            LIMIT 5
        """, (filter_name,))
        for row in zip_cursor:
            top_neighborhoods.append({
                'zip_code': row['zip_code'],
                'permit_count': row['permit_count']
            })
    except Exception:
        pass

    # V14.0: State info for internal linking
    state_slug = None
    state_name = current_state
    for slug, info in STATE_CONFIG.items():
        if info['abbrev'] == current_state:
            state_slug = slug
            state_name = info['name']
            break

    # V14.0: City blog URL if exists
    city_blog_url = None
    if current_state:
        blog_slug = f"building-permits-{city_slug}-{current_state.lower()}-contractor-guide"
        blog_path = os.path.join(os.path.dirname(__file__), 'blog', f"{blog_slug}.md")
        if os.path.exists(blog_path):
            city_blog_url = f"/blog/{blog_slug}"

    # V14.0: Top trades for Related Content links
    top_trades = [
        {'name': 'Plumbing', 'slug': 'plumbing'},
        {'name': 'Electrical', 'slug': 'electrical'},
        {'name': 'HVAC', 'slug': 'hvac'},
        {'name': 'Roofing', 'slug': 'roofing'},
        {'name': 'General Construction', 'slug': 'general-construction'},
    ]

    return render_template(
        'city_landing.html',
        city_name=config['name'],
        city_slug=city_slug,
        city_state=current_state,
        meta_title=config['meta_title'],
        meta_description=config['meta_description'],
        seo_content=config['seo_content'],
        canonical_url=f"{SITE_URL}/permits/{city_slug}",
        robots_directive=robots_directive,  # V12.5: noindex empty pages
        permit_count=permit_count,
        total_value=total_value,
        high_value_count=high_value_count,
        new_this_month=new_this_month,
        unique_contractors=unique_contractors,
        trade_breakdown=trade_breakdown,
        permits=sorted_permits,
        other_cities=other_cities,
        nearby_cities=nearby_cities,  # V12.11: Same-state cities for internal linking
        current_year=datetime.now().year,
        current_date=datetime.now().strftime('%Y-%m-%d'),
        last_collected=last_collected,  # V17c: Freshness badge
        related_articles=related_articles,  # V17d: Cross-linked blog articles
        is_coming_soon=is_coming_soon,  # V12.11: Coming Soon badge
        top_neighborhoods=top_neighborhoods,  # V14.0: Top zip codes
        state_slug=state_slug,  # V14.0: For state hub link
        state_name=state_name,  # V14.0: For display
        city_blog_url=city_blog_url,  # V14.0: City guide link
        top_trades=top_trades,  # V14.0: Trade page links
        data_freshness=city_freshness,  # V18: stale indicator
        newest_permit_date=newest_permit_date,  # V18: for "last updated" display
    )


@app.route('/permits/<state_slug>/<city_slug>')
def state_city_landing(state_slug, city_slug):
    """V77: Render SEO-optimized city landing page with state/city URL format.

    URL format: /permits/{state}/{city} e.g., /permits/texas/fort-worth

    This is the primary city page route for SEO. Each city page targets
    "[city] building permits" keywords for contractors.
    """
    # Check if state_slug is a valid state
    if state_slug not in STATE_CONFIG:
        # Not a valid state — fall through to city/trade route
        # by calling city_trade_landing directly
        return city_trade_landing(state_slug, city_slug)

    state_abbrev = STATE_CONFIG[state_slug]['abbrev']
    state_name = STATE_CONFIG[state_slug]['name']

    # Look up city in prod_cities first (authoritative source)
    conn = permitdb.get_connection()
    city_row = conn.execute("""
        SELECT city, state, city_slug, source_id, source_type, total_permits,
               newest_permit_date, last_collection, data_freshness, status
        FROM prod_cities
        WHERE city_slug = ? AND state = ?
    """, (city_slug, state_abbrev)).fetchone()

    if not city_row:
        # Try without state filter (some cities might not have state stored correctly)
        city_row = conn.execute("""
            SELECT city, state, city_slug, source_id, source_type, total_permits,
                   newest_permit_date, last_collection, data_freshness, status
            FROM prod_cities WHERE city_slug = ?
        """, (city_slug,)).fetchone()

    if not city_row:
        # Fall back to CITY_REGISTRY lookup
        city_key, city_config = get_city_by_slug_auto(city_slug)
        if not city_config:
            return render_city_not_found(city_slug)
        city_name = city_config['name']
        city_state = city_config.get('state', state_abbrev)
        total_permits = 0
        newest_permit_date = None
        last_collection = None
        data_freshness = 'no_data'
        is_active = city_config.get('active', False)
    else:
        city_name = city_row['city']
        city_state = city_row['state']
        total_permits = city_row['total_permits'] or 0
        newest_permit_date = city_row['newest_permit_date']
        last_collection = city_row['last_collection']
        data_freshness = city_row['data_freshness'] or 'no_data'
        is_active = city_row['status'] == 'active'

    # Get recent permits from permits table
    filter_name = city_name
    permits_cursor = conn.execute("""
        SELECT * FROM permits
        WHERE city = ? AND state = ?
        ORDER BY filing_date DESC, estimated_cost DESC
        LIMIT 50
    """, (filter_name, city_state))
    recent_permits = [dict(row) for row in permits_cursor]

    # If no permits with state filter, try without
    if not recent_permits:
        permits_cursor = conn.execute("""
            SELECT * FROM permits
            WHERE city = ?
            ORDER BY filing_date DESC, estimated_cost DESC
            LIMIT 50
        """, (filter_name,))
        recent_permits = [dict(row) for row in permits_cursor]

    # Get permit stats
    stats_row = conn.execute("""
        SELECT COUNT(*) as permit_count,
               MIN(filing_date) as earliest_date,
               MAX(filing_date) as latest_date
        FROM permits WHERE city = ?
    """, (filter_name,)).fetchone()

    permit_count = stats_row['permit_count'] if stats_row else 0
    earliest_date = stats_row['earliest_date'] if stats_row else None
    latest_date = stats_row['latest_date'] if stats_row else None

    # Get permit types breakdown
    types_cursor = conn.execute("""
        SELECT COALESCE(permit_type, 'Other') as ptype, COUNT(*) as cnt
        FROM permits WHERE city = ?
        GROUP BY permit_type
        ORDER BY cnt DESC
        LIMIT 10
    """, (filter_name,))
    permit_types = {row['ptype']: row['cnt'] for row in types_cursor}

    # Get nearby cities in same state for internal linking
    nearby_cities = conn.execute("""
        SELECT city_slug, city, total_permits
        FROM prod_cities
        WHERE state = ? AND city_slug != ? AND status = 'active' AND total_permits > 0
        ORDER BY total_permits DESC
        LIMIT 10
    """, (city_state, city_slug)).fetchall()

    # Format display name
    display_name = format_city_name(city_name)

    # SEO meta
    meta_title = f"{display_name}, {state_name} Building Permits | PermitGrab"
    meta_description = f"Browse recent building permits in {display_name}, {state_name}. Track new construction, renovations, and remodeling permits updated daily. Built for contractors and builders."

    # Robots directive
    robots_directive = "index, follow" if permit_count > 0 and is_active else "noindex, follow"

    # Canonical URL
    canonical_url = f"{SITE_URL}/permits/{state_slug}/{city_slug}"

    footer_cities = get_cities_with_data()

    # V79: Get relevant blog posts for this city
    city_link = f"/permits/{state_slug}/{city_slug}"
    city_blog_posts = get_blog_posts_for_city(city_link)

    return render_template('city_landing_v77.html',
        city_name=display_name,
        city_slug=city_slug,
        state_abbrev=city_state,
        state_name=state_name,
        state_slug=state_slug,
        total_permits=total_permits,
        permit_count=permit_count,
        earliest_date=earliest_date,
        latest_date=latest_date,
        newest_permit_date=newest_permit_date,
        last_collection=last_collection,
        data_freshness=data_freshness,
        is_active=is_active,
        recent_permits=recent_permits,
        permit_types=permit_types,
        nearby_cities=nearby_cities,
        meta_title=meta_title,
        meta_description=meta_description,
        robots_directive=robots_directive,
        canonical_url=canonical_url,
        footer_cities=footer_cities,
        blog_posts=city_blog_posts,
    )


@app.route('/permits/<city_slug>/<trade_slug>')
def city_trade_landing(city_slug, trade_slug):
    """Render SEO-optimized city × trade landing page."""
    # V12.60: Use get_city_by_slug_auto() to match sitemap-generated URLs
    # (previously used get_city_by_slug which missed auto-discovered cities)
    city_key, city_config = get_city_by_slug_auto(city_slug)
    if not city_config:
        return render_city_not_found(city_slug)

    # Get trade from config
    trade = get_trade(trade_slug)
    if not trade:
        return render_city_not_found(trade_slug)

    # V12.51: Use SQLite for city permits
    conn = permitdb.get_connection()

    # V12.9: Format city name for display
    display_name = format_city_name(city_config['name'])

    # V14.1: Filter permits using SQL LIKE patterns for both city AND trade
    # This is more efficient than loading all city permits then filtering in Python
    city_name = city_config['name']
    matching_permits = []

    # V14.1: Get LIKE patterns for this trade from TRADE_MAPPING
    trade_patterns = TRADE_MAPPING.get(trade_slug, [f'%{trade_slug}%'])

    # V14.1: Build SQL query with trade patterns
    # Check description, permit_type, work_type, trade_category for trade keywords
    def query_trade_permits(city_filter, city_param):
        """Helper to query permits matching city filter AND trade patterns."""
        # Build OR conditions for trade patterns across multiple fields
        trade_conditions = []
        trade_params = []
        for pattern in trade_patterns:
            trade_conditions.append("""
                (LOWER(COALESCE(description, '')) LIKE ?
                 OR LOWER(COALESCE(permit_type, '')) LIKE ?
                 OR LOWER(COALESCE(work_type, '')) LIKE ?
                 OR LOWER(COALESCE(trade_category, '')) LIKE ?)
            """)
            trade_params.extend([pattern.lower(), pattern.lower(), pattern.lower(), pattern.lower()])

        trade_clause = " OR ".join(trade_conditions)
        sql = f"""
            SELECT * FROM permits
            WHERE {city_filter}
              AND ({trade_clause})
            ORDER BY filing_date DESC
            LIMIT 500
        """
        params = [city_param] + trade_params
        cursor = conn.execute(sql, params)
        return [dict(row) for row in cursor]

    # Strategy 1: Exact city match
    matching_permits = query_trade_permits("city = ?", city_name)

    # Strategy 2: Case-insensitive city match if no results
    if not matching_permits:
        matching_permits = query_trade_permits("LOWER(city) = LOWER(?)", city_name)

    # Strategy 3: Partial city match (without state suffix) if still no results
    if not matching_permits and ',' in city_name:
        base_name = city_name.split(',')[0].strip()
        matching_permits = query_trade_permits("LOWER(city) LIKE ?", f"%{base_name.lower()}%")

    # V14.1: Fallback to Python-side matching if SQL patterns didn't match
    # This handles edge cases where data format differs from expected patterns
    if not matching_permits:
        # Load city permits and filter with trade_configs keywords
        city_permits = []
        cursor = conn.execute(
            "SELECT * FROM permits WHERE LOWER(city) LIKE ? ORDER BY filing_date DESC LIMIT 2000",
            (f"%{city_name.split(',')[0].strip().lower()}%",)
        )
        city_permits = [dict(row) for row in cursor]

        trade_keywords = [kw.lower() for kw in trade['keywords']]
        for p in city_permits:
            text = ""
            for field in ['description', 'permit_type', 'work_type', 'trade_category']:
                if p.get(field):
                    text += p[field].lower() + " "
            if any(kw in text for kw in trade_keywords):
                matching_permits.append(p)
                if len(matching_permits) >= 500:
                    break

    # Results are already sorted by date from SQL

    # V30: Fallback — show recent city permits if no trade-specific matches found
    # This prevents empty trade pages which kill conversion and risk thin content penalties
    trade_fallback = False
    if not matching_permits:
        cursor = conn.execute(
            "SELECT * FROM permits WHERE LOWER(city) LIKE ? ORDER BY filing_date DESC LIMIT 20",
            (f"%{city_name.split(',')[0].strip().lower()}%",)
        )
        matching_permits = [dict(row) for row in cursor]
        trade_fallback = True

    # Calculate stats
    # V12.23: Use module-level datetime/timedelta imports
    now = datetime.now()
    month_ago = (now - timedelta(days=30)).isoformat()
    week_ago = (now - timedelta(days=7)).isoformat()

    monthly_count = len([p for p in matching_permits if (p.get('filing_date') or '') >= month_ago])
    weekly_count = len([p for p in matching_permits if (p.get('filing_date') or '') >= week_ago])

    values = [p.get('estimated_cost', 0) for p in matching_permits if p.get('estimated_cost')]
    avg_value = int(sum(values) / len(values)) if values else 0

    # Build city dict for template with formatted name
    city_dict = {
        "name": display_name,
        "state": city_config['state'],
        "slug": city_slug,
    }

    stats = {
        "monthly_count": monthly_count or len(matching_permits),
        "weekly_count": weekly_count,
        "avg_value": f"{avg_value:,}" if avg_value else "N/A",
    }

    # Other trades for cross-linking (exclude current)
    other_trades = [t for t in get_all_trades() if t['slug'] != trade_slug]

    # Other cities for cross-linking (exclude current)
    other_cities = [{"name": c['name'], "slug": c['slug']} for c in ALL_CITIES if c['slug'] != city_slug]

    # V30: noindex for thin trade pages (fewer than 5 trade-specific permits, or fallback mode)
    robots_directive = "noindex, follow" if (trade_fallback or len(matching_permits) < 5) else "index, follow"

    # V14.0: Get state info for internal linking
    state_abbrev = city_config.get('state', '')
    state_slug = None
    state_name = state_abbrev  # Fallback to abbrev
    for slug, info in STATE_CONFIG.items():
        if info['abbrev'] == state_abbrev:
            state_slug = slug
            state_name = info['name']
            break

    # V14.0: Check if city blog post exists
    city_blog_url = None
    if state_abbrev:
        blog_slug = f"building-permits-{city_slug}-{state_abbrev.lower()}-contractor-guide"
        blog_path = os.path.join(os.path.dirname(__file__), 'blog', f"{blog_slug}.md")
        if os.path.exists(blog_path):
            city_blog_url = f"/blog/{blog_slug}"

    return render_template(
        'city_trade_landing.html',
        city=city_dict,
        trade=trade,
        permits=matching_permits[:10],
        stats=stats,
        other_trades=other_trades,
        other_cities=other_cities,
        robots_directive=robots_directive,
        state_slug=state_slug,
        state_name=state_name,
        city_blog_url=city_blog_url,
        trade_fallback=trade_fallback,
    )


# ===========================
# V28: SEARCH PAGE — Required for SearchAction schema (sitelinks searchbox)
# ===========================

@app.route('/search')
def search_page():
    """V28: Search page for SearchAction schema.
    Redirects to cities browse filtered by query, or shows permits filtered by query.
    """
    query = request.args.get('q', '').strip()
    if not query:
        return redirect('/cities')

    # Try to find a matching city
    all_cities = get_cities_with_data()
    query_lower = query.lower()

    # Direct city match (exact query == city name)
    for city in all_cities:
        city_name = city.get('name', '') or city.get('city', '')
        if city_name.lower() == query_lower:
            slug = city.get('slug', city_name.lower().replace(' ', '-'))
            return redirect(f'/permits/{slug}')

    # City name appears in query (e.g. "denver roofing" contains "denver")
    best_match = None
    best_len = 0
    for city in all_cities:
        city_name = (city.get('name', '') or city.get('city', '')).lower()
        if city_name and city_name in query_lower and len(city_name) > best_len:
            best_match = city
            best_len = len(city_name)
    if best_match:
        slug = best_match.get('slug', (best_match.get('name', '') or best_match.get('city', '')).lower().replace(' ', '-'))
        # Check if query has a trade keyword after the city name
        remainder = query_lower.replace((best_match.get('name', '') or best_match.get('city', '')).lower(), '').strip()
        if remainder:
            # Try to match a trade — redirect to city+trade page
            return redirect(f'/permits/{slug}/{remainder.replace(" ", "-")}')
        return redirect(f'/permits/{slug}')

    # Query appears in a city name (e.g. "den" matches "denver")
    matching_cities = [c for c in all_cities if query_lower in (c.get('name', '') or c.get('city', '')).lower()]
    if matching_cities:
        city = matching_cities[0]
        slug = city.get('slug', (city.get('name', '') or city.get('city', '')).lower().replace(' ', '-'))
        return redirect(f'/permits/{slug}')

    # No match - redirect to cities browse
    return redirect('/cities')


# ===========================
# V17e: CITIES BROWSE PAGE — Hub for all city landing pages
# ===========================

@app.route('/cities')
def cities_browse():
    """V17e: Dedicated browse page for all cities, organized by state.
    Reduces homepage link dilution by moving 300+ city links here.
    Acts as an SEO hub that distributes PageRank to all city pages.
    """
    all_cities = get_cities_with_data()
    footer_cities = all_cities

    # Group cities by state
    states = {}
    no_state = []
    for city in all_cities:
        state = city.get('state', '').strip()
        if state:
            if state not in states:
                states[state] = []
            states[state].append(city)
        else:
            no_state.append(city)

    # Sort states alphabetically, cities within each state by permit count
    sorted_states = sorted(states.items(), key=lambda x: x[0])
    for state_name, cities in sorted_states:
        cities.sort(key=lambda c: c.get('permit_count', 0), reverse=True)

    # Top cities across all states (for hero section)
    # V13.2: Increased from 12 to 20 for better coverage
    top_cities = all_cities[:20]

    total_cities = len(all_cities)
    total_states = len(states)

    return render_template('cities_browse.html',
        footer_cities=footer_cities,
        sorted_states=sorted_states,
        no_state_cities=no_state,
        top_cities=top_cities,
        total_cities=total_cities,
        total_states=total_states,
        canonical_url=f"{SITE_URL}/cities",
    )


# ===========================
# SITEMAP & ROBOTS.TXT
# ===========================

def _get_city_lastmod_map():
    """V28: Get lastmod timestamps per city from permit data."""
    city_lastmod = {}
    try:
        conn = permitdb.get_connection()
        rows = conn.execute("SELECT city, MAX(collected_at) as latest FROM permits GROUP BY city").fetchall()
        for row in rows:
            if row['city'] and row['latest']:
                city_slug_key = row['city'].lower().replace(' ', '-').replace(',', '').replace('.', '')
                try:
                    city_lastmod[city_slug_key] = row['latest'][:10]  # YYYY-MM-DD
                except (TypeError, IndexError):
                    pass
    except Exception:
        pass
    return city_lastmod


def _generate_sitemap_xml(urls):
    """V28: Generate XML sitemap from list of URL dicts."""
    xml_parts = ['<?xml version="1.0" encoding="UTF-8"?>\n',
                 '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n']
    for url in urls:
        xml_parts.append('  <url>\n')
        xml_parts.append(f"    <loc>{url['loc']}</loc>\n")
        xml_parts.append(f"    <lastmod>{url['lastmod']}</lastmod>\n")
        xml_parts.append(f"    <changefreq>{url['changefreq']}</changefreq>\n")
        xml_parts.append(f"    <priority>{url['priority']}</priority>\n")
        xml_parts.append('  </url>\n')
    xml_parts.append('</urlset>')
    return ''.join(xml_parts)


@app.route('/sitemap.xml')
def sitemap_index():
    """V28: Sitemap index pointing to child sitemaps."""
    today = datetime.now().strftime('%Y-%m-%d')

    xml_parts = ['<?xml version="1.0" encoding="UTF-8"?>\n',
                 '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n']

    child_sitemaps = [
        ('sitemap-pages.xml', today),
        ('sitemap-cities.xml', today),
        ('sitemap-trades.xml', today),
        ('sitemap-blog.xml', today),
    ]

    for sitemap_name, lastmod in child_sitemaps:
        xml_parts.append('  <sitemap>\n')
        xml_parts.append(f"    <loc>{SITE_URL}/{sitemap_name}</loc>\n")
        xml_parts.append(f"    <lastmod>{lastmod}</lastmod>\n")
        xml_parts.append('  </sitemap>\n')

    xml_parts.append('</sitemapindex>')
    return Response(''.join(xml_parts), mimetype='application/xml')


@app.route('/sitemap-pages.xml')
def sitemap_pages():
    """V28: Sitemap for static pages."""
    today = datetime.now().strftime('%Y-%m-%d')
    urls = [
        {'loc': SITE_URL, 'changefreq': 'daily', 'priority': '1.0', 'lastmod': today},
        {'loc': f"{SITE_URL}/pricing", 'changefreq': 'weekly', 'priority': '0.9', 'lastmod': today},
        {'loc': f"{SITE_URL}/contractors", 'changefreq': 'daily', 'priority': '0.8', 'lastmod': today},
        {'loc': f"{SITE_URL}/map", 'changefreq': 'daily', 'priority': '0.8', 'lastmod': today},
        {'loc': f"{SITE_URL}/get-alerts", 'changefreq': 'weekly', 'priority': '0.7', 'lastmod': today},
        {'loc': f"{SITE_URL}/blog", 'changefreq': 'weekly', 'priority': '0.7', 'lastmod': today},
        {'loc': f"{SITE_URL}/cities", 'changefreq': 'daily', 'priority': '0.9', 'lastmod': today},
        {'loc': f"{SITE_URL}/stats", 'changefreq': 'daily', 'priority': '0.7', 'lastmod': today},
        {'loc': f"{SITE_URL}/about", 'changefreq': 'monthly', 'priority': '0.6', 'lastmod': today},
        {'loc': f"{SITE_URL}/contact", 'changefreq': 'monthly', 'priority': '0.5', 'lastmod': today},
        {'loc': f"{SITE_URL}/privacy", 'changefreq': 'monthly', 'priority': '0.3', 'lastmod': today},
        {'loc': f"{SITE_URL}/terms", 'changefreq': 'monthly', 'priority': '0.3', 'lastmod': today},
    ]
    return Response(_generate_sitemap_xml(urls), mimetype='application/xml')


@app.route('/sitemap-cities.xml')
def sitemap_cities():
    """V28: Sitemap for city pages and state hub pages.
    V77: Added city URLs in /permits/{state}/{city} format for SEO.
    """
    today = datetime.now().strftime('%Y-%m-%d')
    url_map = {}

    # V77: Create reverse mapping from state abbrev to state slug
    abbrev_to_state_slug = {v['abbrev']: k for k, v in STATE_CONFIG.items()}

    # State hub pages
    for state_slug in STATE_CONFIG.keys():
        url_map[f"{SITE_URL}/permits/{state_slug}"] = {
            'loc': f"{SITE_URL}/permits/{state_slug}",
            'changefreq': 'daily',
            'priority': '0.85',
            'lastmod': today
        }

    # Get cities with permits
    cities_with_data = get_cities_with_data()
    cities_with_permits = {c['name'] for c in cities_with_data}
    city_lastmod = _get_city_lastmod_map()
    state_slugs = set(STATE_CONFIG.keys())

    # City pages from discovered cities (old format: /permits/{city})
    all_discovered_cities = discover_cities_from_permits()
    for slug, city_info in all_discovered_cities.items():
        if slug in state_slugs:
            continue
        if city_info['name'] not in cities_with_permits:
            continue

        lastmod = city_lastmod.get(slug, today)
        loc = f"{SITE_URL}/permits/{slug}"
        if loc not in url_map:
            url_map[loc] = {
                'loc': loc,
                'changefreq': 'daily',
                'priority': '0.8',
                'lastmod': lastmod
            }

    # V58: Also include ALL active CITY_REGISTRY cities for SEO (even without data yet)
    for key, config in CITY_REGISTRY.items():
        if not config.get('active'):
            continue
        slug = config.get('slug', key.replace('_', '-'))
        loc = f"{SITE_URL}/permits/{slug}"
        if loc not in url_map and slug not in state_slugs:
            url_map[loc] = {
                'loc': loc,
                'changefreq': 'weekly',
                'priority': '0.6',
                'lastmod': today
            }

    # V77: Add city URLs in new /permits/{state}/{city} format
    # These are the SEO-optimized URLs targeting "[city] building permits" keywords
    try:
        conn = permitdb.get_connection()
        active_cities = conn.execute("""
            SELECT city_slug, state, last_collection, data_freshness, total_permits
            FROM prod_cities
            WHERE status = 'active'
              AND data_freshness != 'no_data'
              AND total_permits > 0
        """).fetchall()

        for city_row in active_cities:
            city_slug = city_row['city_slug']
            state_abbrev = city_row['state']
            last_collection = city_row['last_collection']

            # Get state slug from abbreviation
            state_slug = abbrev_to_state_slug.get(state_abbrev)
            if not state_slug:
                continue  # Skip if state not in our config

            # Format lastmod
            lastmod = last_collection[:10] if last_collection else today

            # New format: /permits/{state}/{city}
            loc = f"{SITE_URL}/permits/{state_slug}/{city_slug}"
            if loc not in url_map:
                url_map[loc] = {
                    'loc': loc,
                    'changefreq': 'daily',
                    'priority': '0.7',
                    'lastmod': lastmod
                }
    except Exception as e:
        print(f"[sitemap_cities] V77 city URLs error: {e}")

    return Response(_generate_sitemap_xml(url_map.values()), mimetype='application/xml')


@app.route('/sitemap-trades.xml')
def sitemap_trades():
    """V28: Sitemap for city × trade pages."""
    today = datetime.now().strftime('%Y-%m-%d')
    url_map = {}

    cities_with_data = get_cities_with_data()
    cities_with_permits = {c['name'] for c in cities_with_data}
    city_lastmod = _get_city_lastmod_map()
    state_slugs = set(STATE_CONFIG.keys())
    trade_slugs = [t for t in get_trade_slugs() if t != 'all-trades']

    all_discovered_cities = discover_cities_from_permits()
    for slug, city_info in all_discovered_cities.items():
        if slug in state_slugs:
            continue
        if city_info['name'] not in cities_with_permits:
            continue

        lastmod = city_lastmod.get(slug, today)
        for trade_slug in trade_slugs:
            loc = f"{SITE_URL}/permits/{slug}/{trade_slug}"
            if loc not in url_map:
                url_map[loc] = {
                    'loc': loc,
                    'changefreq': 'daily',
                    'priority': '0.7',
                    'lastmod': lastmod
                }

    return Response(_generate_sitemap_xml(url_map.values()), mimetype='application/xml')


@app.route('/sitemap-blog.xml')
def sitemap_blog():
    """V79: Sitemap for blog posts — uses BLOG_POSTS data structure."""
    urls = []

    # Add blog index page
    urls.append({
        'loc': f"{SITE_URL}/blog",
        'changefreq': 'weekly',
        'priority': '0.7',
        'lastmod': '2026-04-06'
    })

    # Add all blog posts
    for post in BLOG_POSTS:
        urls.append({
            'loc': f"{SITE_URL}/blog/{post['slug']}",
            'changefreq': 'monthly',
            'priority': '0.6',
            'lastmod': post['date']
        })

    return Response(_generate_sitemap_xml(urls), mimetype='application/xml')


@app.route('/logout')
def logout_page():
    """Log out and redirect to homepage."""
    session.clear()
    return redirect('/')


# ===========================
# ACCOUNT SETTINGS (V8)
# ===========================
@app.route('/account')
def account_page():
    """Account settings page."""
    if 'user_email' not in session:
        return redirect('/login')
    user = find_user_by_email(session['user_email'])
    if not user:
        session.clear()
        return redirect('/login')
    footer_cities = get_cities_with_data()
    is_pro = user.plan in ('pro', 'professional', 'enterprise')
    return render_template('account.html', user=user, is_pro=is_pro, footer_cities=footer_cities)


@app.route('/api/account', methods=['PUT'])
def api_update_account():
    """Update account settings."""
    if 'user_email' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    user = find_user_by_email(session['user_email'])
    if not user:
        return jsonify({'error': 'User not found'}), 404

    data = request.get_json()
    if not data:
        return jsonify({'error': 'Invalid request'}), 400

    if 'name' in data:
        user.name = data['name'].strip()
    if 'city' in data:
        user.city = data['city']
    if 'trade' in data:
        user.trade = data['trade']
    if 'daily_alerts' in data:
        user.daily_alerts = bool(data['daily_alerts'])
    # V12.26: Competitor Watch and Digest Cities
    if 'watched_competitors' in data:
        competitors = data['watched_competitors']
        if isinstance(competitors, list):
            # Limit to 5 competitors, Pro only
            if user.plan == 'pro':
                user.watched_competitors = json.dumps(competitors[:5])
    if 'digest_cities' in data:
        cities = data['digest_cities']
        if isinstance(cities, list):
            user.digest_cities = json.dumps(cities)

    db.session.commit()
    return jsonify({'success': True, 'user': user.to_dict()})


# V12.26: Competitor Watch API
@app.route('/api/competitors/watch', methods=['GET', 'POST', 'DELETE'])
def api_competitor_watch():
    """Manage watched competitors for Competitor Watch alerts."""
    if 'user_email' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    user = find_user_by_email(session['user_email'])
    if not user:
        return jsonify({'error': 'User not found'}), 404
    if user.plan != 'pro':
        return jsonify({'error': 'Competitor Watch is a Pro feature', 'upgrade_url': '/pricing'}), 403

    competitors = json.loads(user.watched_competitors or '[]')

    if request.method == 'GET':
        return jsonify({'competitors': competitors})

    elif request.method == 'POST':
        data = request.get_json()
        name = (data.get('name', '') if data else '').strip()
        if not name:
            return jsonify({'error': 'Competitor name required'}), 400
        if len(competitors) >= 5:
            return jsonify({'error': 'Maximum 5 competitors allowed'}), 400
        if name.lower() not in [c.lower() for c in competitors]:
            competitors.append(name)
            user.watched_competitors = json.dumps(competitors)
            db.session.commit()
        return jsonify({'competitors': competitors})

    elif request.method == 'DELETE':
        data = request.get_json()
        name = (data.get('name', '') if data else '').strip()
        competitors = [c for c in competitors if c.lower() != name.lower()]
        user.watched_competitors = json.dumps(competitors)
        db.session.commit()
        return jsonify({'competitors': competitors})


# V12.26: Check for competitor matches in recent permits
@app.route('/api/competitors/matches')
def api_competitor_matches():
    """Get permits matching watched competitors."""
    if 'user_email' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    user = find_user_by_email(session['user_email'])
    if not user:
        return jsonify({'error': 'User not found'}), 404
    if user.plan != 'pro':
        return jsonify({'error': 'Competitor Watch is a Pro feature'}), 403

    competitors = json.loads(user.watched_competitors or '[]')
    if not competitors:
        return jsonify({'matches': [], 'message': 'No competitors being watched'})

    # V12.51: Use SQLite with LIKE queries for competitor matching
    conn = permitdb.get_connection()
    matches = []

    for comp in competitors:
        cursor = conn.execute("""
            SELECT * FROM permits
            WHERE LOWER(contact_name) LIKE ?
            ORDER BY filing_date DESC
            LIMIT 50
        """, (f"%{comp.lower()}%",))

        for row in cursor:
            matches.append({
                'permit': dict(row),
                'matched_competitor': comp
            })

    # Sort by filing date, most recent first
    matches.sort(key=lambda x: x['permit'].get('filing_date', ''), reverse=True)

    return jsonify({'matches': matches[:50]})  # Limit to 50 most recent


@app.route('/api/change-password', methods=['POST'])
def api_change_password():
    """Change user password."""
    if 'user_email' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    user = find_user_by_email(session['user_email'])
    if not user:
        return jsonify({'error': 'User not found'}), 404

    data = request.get_json()
    if not data:
        return jsonify({'error': 'Invalid request'}), 400

    current_password = data.get('current_password', '')
    new_password = data.get('new_password', '')

    if not check_password_hash(user.password_hash, current_password):
        return jsonify({'error': 'Current password is incorrect'}), 400

    if len(new_password) < 8:
        return jsonify({'error': 'New password must be at least 8 characters'}), 400

    user.password_hash = generate_password_hash(new_password)
    db.session.commit()
    return jsonify({'success': True, 'message': 'Password changed successfully'})


@app.route('/robots.txt')
def robots():
    """V12.11: Serve robots.txt for search engines."""
    content = f"""User-agent: *
Allow: /
Disallow: /admin/
Disallow: /api/
Disallow: /dashboard/
Disallow: /my-leads
Disallow: /early-intel
Disallow: /analytics
Disallow: /account
Disallow: /saved-leads
Disallow: /saved-searches
Disallow: /billing
Disallow: /onboarding
Disallow: /logout
Disallow: /reset-password
Disallow: /login
Disallow: /signup

# Crawl-delay for polite crawling
Crawl-delay: 1

Sitemap: {SITE_URL}/sitemap.xml
"""
    return Response(content, mimetype='text/plain')


# ===========================
# BLOG SYSTEM
# ===========================
import markdown
import re

BLOG_DIR = os.path.join(os.path.dirname(__file__), 'blog')


def parse_blog_post(filename):
    """Parse a markdown blog post with frontmatter."""
    filepath = os.path.join(BLOG_DIR, filename)
    if not os.path.exists(filepath):
        return None

    with open(filepath, 'r') as f:
        content = f.read()

    # Parse frontmatter (YAML between --- markers)
    meta = {}
    body = content
    if content.startswith('---'):
        parts = content.split('---', 2)
        if len(parts) >= 3:
            for line in parts[1].strip().split('\n'):
                if ':' in line:
                    key, val = line.split(':', 1)
                    meta[key.strip()] = val.strip().strip('"').strip("'")
            body = parts[2]

    # Convert markdown to HTML
    html = markdown.markdown(body, extensions=['fenced_code', 'tables'])

    # Extract excerpt (first 160 chars of text)
    text_only = re.sub(r'<[^>]+>', '', html)
    excerpt = text_only[:160].strip() + '...' if len(text_only) > 160 else text_only

    # V12.26: Parse FAQs if present in frontmatter (JSON array format)
    faqs = []
    if 'faqs' in meta:
        try:
            faqs = json.loads(meta['faqs'])
        except (json.JSONDecodeError, TypeError):
            pass

    return {
        'slug': filename.replace('.md', ''),
        'title': meta.get('title', 'Untitled'),
        'date': meta.get('date', ''),
        'author': meta.get('author', 'PermitGrab Team'),
        'excerpt': excerpt,
        'content': html,
        'keywords': meta.get('keywords', ''),
        'faqs': faqs,
    }


def get_all_blog_posts():
    """Get all blog posts sorted by date."""
    if not os.path.exists(BLOG_DIR):
        return []

    posts = []
    for filename in os.listdir(BLOG_DIR):
        if filename.endswith('.md'):
            post = parse_blog_post(filename)
            if post:
                posts.append(post)

    # Sort by date descending
    posts.sort(key=lambda x: x.get('date', ''), reverse=True)
    return posts


# V79: Old blog routes removed — now using BLOG_POSTS data structure (see line ~6178)

# ===========================
# SCHEDULED DATA COLLECTION
# ===========================
def scheduled_collection():
    """V12.50: Run delta collection every 6 hours. V13.2: Added auto-discovery."""
    # Wait for initial data to be ready (reduced from 30 — SQLite startup is instant)
    print(f"[{datetime.now()}] V12.50: Scheduled collector waiting 5 minutes for startup...")
    time.sleep(300)  # 5 minutes

    # Track when we last ran permit history (run weekly)
    last_history_run = None
    # V13.2: Track when we last ran auto-discovery (run daily)
    last_discovery_run = None

    while True:
        print(f"[{datetime.now()}] V12.50: Starting scheduled collection cycle...")

        # V13.3: Each task has its own try/except so one failure doesn't block others
        # Permit collection - V72.1: Disabled include_scrapers to prevent memory crash
        try:
            from collector import collect_refresh, collect_permit_history
            collect_refresh(days_back=7)
            print(f"[{datetime.now()}] Refresh collection complete.")
        except Exception as e:
            print(f"[{datetime.now()}] Permit collection error: {e}")
            import traceback
            traceback.print_exc()

        # V12.50: Prune old permits (keep last 90 days)
        try:
            deleted = permitdb.delete_old_permits(days=90)
            if deleted > 0:
                print(f"[{datetime.now()}] Pruned {deleted} old permits.")
        except Exception as e:
            print(f"[{datetime.now()}] Prune error: {e}")

        # Violation collection (daily)
        try:
            from violation_collector import collect_all_violations
            collect_all_violations(days_back=90)
            print(f"[{datetime.now()}] Violation collection complete.")
        except Exception as e:
            print(f"[{datetime.now()}] Violation collection error: {e}")

        # Signal collection (daily)
        try:
            from signal_collector import collect_all_signals
            collect_all_signals(days_back=90)
            print(f"[{datetime.now()}] Signal collection complete.")
        except Exception as e:
            print(f"[{datetime.now()}] Signal collection error: {e}")

        # Permit history collection (weekly or first run)
        now = datetime.now()
        if last_history_run is None or (now - last_history_run).days >= 7:
            try:
                collect_permit_history(years_back=1)
                last_history_run = now
                print(f"[{datetime.now()}] Permit history collection complete.")
            except Exception as e:
                print(f"[{datetime.now()}] Permit history collection error: {e}")

        # City health check (daily)
        try:
            from city_health import check_all_cities
            check_all_cities()
            print(f"[{datetime.now()}] City health check complete.")
        except Exception as e:
            print(f"[{datetime.now()}] City health check error: {e}")

        # V17: Auto-discovery pipeline (daily)
        # 1. Discover new sources (Socrata + ArcGIS)
        # 2. Auto-test and activate pending cities
        # 3. Send SEO notification email for new activations
        if permitdb.should_run_daily('discovery'):
            print(f"[{datetime.now()}] V17b: Starting accelerated discovery pipeline...")

            total_new_sources = 0

            # V17b: Use accelerated parallel discovery (Socrata + ArcGIS combined)
            try:
                from auto_discover import run_accelerated_discovery
                total_new_sources = run_accelerated_discovery(max_results=300, max_workers=5)
                print(f"[{datetime.now()}] V17b accelerated discovery: {total_new_sources} new sources")
            except ImportError:
                # Fallback to sequential if accelerated not available
                print(f"[{datetime.now()}] V17b not available, using sequential discovery...")
                try:
                    from auto_discover import run_full_discovery, run_arcgis_bulk_discovery
                    total_new_sources += run_full_discovery(max_results=100)
                    total_new_sources += run_arcgis_bulk_discovery(max_results=100)
                except Exception as e:
                    print(f"[{datetime.now()}] Sequential discovery error: {e}")
            except Exception as e:
                print(f"[{datetime.now()}] V17b discovery error: {e}")

            # Auto-test pending cities and activate the good ones
            try:
                from collector import activate_pending_cities
                activated, failed = activate_pending_cities()
                print(f"[{datetime.now()}] Pending cities: {len(activated)} activated, {len(failed)} failed")

                # Send SEO notification if new cities activated
                if activated:
                    try:
                        from email_alerts import send_new_cities_alert
                        send_new_cities_alert(activated)
                        print(f"[{datetime.now()}] SEO notification sent for {len(activated)} cities")
                    except Exception as e:
                        print(f"[{datetime.now()}] SEO notification error: {e}")
            except Exception as e:
                print(f"[{datetime.now()}] Pending activation error: {e}")

            permitdb.mark_daily_complete('discovery')
            print(f"[{datetime.now()}] V17 discovery pipeline complete: {total_new_sources} new sources")

        print(f"[{datetime.now()}] All collection tasks complete.")

        # V18: Run staleness check after collection
        try:
            from collector import staleness_check
            print(f"[{datetime.now()}] V18: Running staleness check...")
            staleness_stats = staleness_check()
            print(f"[{datetime.now()}] V18: Staleness check done - {staleness_stats.get('paused', 0)} paused, {staleness_stats.get('stale', 0)} stale")
        except Exception as e:
            print(f"[{datetime.now()}] V18: Staleness check error: {e}")

        # V64: Run freshness classification
        print(f"[{datetime.now()}] V64: Running freshness classification...")
        try:
            from collector import classify_city_freshness
            freshness = classify_city_freshness()
            summary = freshness.get('summary', {})
            attention_count = freshness.get('total_needing_attention', 0)
            print(f"[{datetime.now()}] V64: Freshness: {summary}")
            if attention_count > 0:
                print(f"[{datetime.now()}] V64: WARNING: {attention_count} cities need attention")
        except Exception as e:
            print(f"[{datetime.now()}] V64: Freshness classification failed: {e}")

        # V16: Track last successful collection run
        global _last_collection_run
        _last_collection_run = datetime.now()

        # V12.50: Sleep 6 hours (reduced from 24 — deltas are lightweight)
        time.sleep(21600)


# ===========================
# V12.53: EMAIL SCHEDULER
# ===========================

def schedule_email_tasks():
    """V12.53: Schedule all email tasks to run at specific times daily.

    - Daily digest: 7 AM ET (12:00 UTC)
    - Trial lifecycle check: 8 AM ET (13:00 UTC)
    - Onboarding nudges: 9 AM ET (14:00 UTC)

    V64: Added robust error logging, heartbeat, and crash recovery.
    V78: Added DIGEST_STATUS tracking and fixed timing (5-min checks during 7 AM window).
    """
    global DIGEST_STATUS

    # V78: Mark thread as started
    DIGEST_STATUS['thread_started'] = datetime.now().isoformat()
    DIGEST_STATUS['thread_alive'] = True

    # V64: Wrap imports in try/except to catch missing dependencies
    try:
        import pytz
        from email_alerts import send_daily_digest, check_trial_lifecycle, check_onboarding_nudges
    except ImportError as e:
        print(f"[{datetime.now()}] [CRITICAL] Email scheduler failed to import: {e}")
        import traceback
        traceback.print_exc()
        DIGEST_STATUS['thread_alive'] = False
        DIGEST_STATUS['last_digest_result'] = f'import_error: {e}'
        return  # Don't silently die — exit with error logged

    # V78: Auto-create subscribers.json if it doesn't exist
    try:
        from pathlib import Path
        subscribers_path = Path("/var/data/subscribers.json")
        if not subscribers_path.exists():
            # Check if /var/data exists (Render persistent disk)
            var_data = Path("/var/data")
            if var_data.exists():
                default_subscribers = [
                    {
                        "email": "wcrainshaw@gmail.com",
                        "active": True,
                        "digest_cities": ["atlanta"],
                        "created_at": datetime.now().strftime("%Y-%m-%d")
                    }
                ]
                subscribers_path.write_text(json.dumps(default_subscribers, indent=2))
                print(f"[{datetime.now()}] V78: Created subscribers.json with default subscriber")
            else:
                print(f"[{datetime.now()}] V78: /var/data not found - running locally, skipping subscribers.json creation")
        else:
            print(f"[{datetime.now()}] V78: subscribers.json already exists")
    except Exception as e:
        print(f"[{datetime.now()}] V78: Could not create subscribers.json: {e}")

    # V68: Wait 3 minutes for initial startup (increased from 2)
    print(f"[{datetime.now()}] V68: Email scheduler waiting 3 minutes for startup...")
    time.sleep(180)

    et = pytz.timezone('America/New_York')

    # V78: Track if we've already run digest today to prevent duplicates
    last_digest_date = None

    while True:
        try:
            now_utc = datetime.utcnow()
            now_et = datetime.now(et)
            today_date = now_et.date()

            # V78: Update heartbeat timestamp
            DIGEST_STATUS['last_heartbeat'] = datetime.now().isoformat()

            # V64: Heartbeat every cycle so we can verify thread is alive in Render logs
            print(f"[{datetime.now()}] V78: Email scheduler heartbeat: {now_et.strftime('%I:%M %p ET')} (thread_alive=True)")

            # Check if it's time for daily tasks (7-9 AM ET window)
            if 7 <= now_et.hour <= 9:
                # Daily digest at 7 AM ET (run once per day)
                if now_et.hour == 7 and last_digest_date != today_date:
                    print(f"[{datetime.now()}] V78: Running daily digest...")
                    DIGEST_STATUS['last_digest_attempt'] = datetime.now().isoformat()
                    try:
                        sent, failed = send_daily_digest()
                        print(f"[{datetime.now()}] V78: Daily digest complete - {sent} sent, {failed} failed")
                        DIGEST_STATUS['last_digest_result'] = 'success'
                        DIGEST_STATUS['last_digest_sent'] = sent
                        DIGEST_STATUS['last_digest_failed'] = failed
                        last_digest_date = today_date  # Mark as done for today
                    except Exception as e:
                        print(f"[{datetime.now()}] [ERROR] Daily digest failed: {e}")
                        import traceback
                        traceback.print_exc()
                        DIGEST_STATUS['last_digest_result'] = f'error: {e}'

                # Trial lifecycle at 8 AM ET
                if now_et.hour == 8 and now_et.minute < 30:
                    print(f"[{datetime.now()}] V64: Checking trial lifecycle...")
                    try:
                        results = check_trial_lifecycle()
                        print(f"[{datetime.now()}] V64: Trial lifecycle complete - {results}")
                    except Exception as e:
                        print(f"[{datetime.now()}] [ERROR] Trial lifecycle failed: {e}")
                        import traceback
                        traceback.print_exc()

                # Onboarding nudges at 9 AM ET
                if now_et.hour == 9 and now_et.minute < 30:
                    print(f"[{datetime.now()}] V64: Checking onboarding nudges...")
                    try:
                        sent = check_onboarding_nudges()
                        print(f"[{datetime.now()}] V64: Onboarding nudges complete - {sent} sent")
                    except Exception as e:
                        print(f"[{datetime.now()}] [ERROR] Onboarding nudges failed: {e}")
                        import traceback
                        traceback.print_exc()

        except Exception as e:
            print(f"[{datetime.now()}] [ERROR] Email scheduler error: {e}")
            import traceback
            traceback.print_exc()
            DIGEST_STATUS['last_digest_result'] = f'loop_error: {e}'
            # V64: Wait 5 min on error before retrying, don't die
            time.sleep(300)
            continue

        # V78: Check every 5 minutes during 7-9 AM ET window to not miss digest
        # Check every 30 minutes outside that window to save resources
        if 6 <= now_et.hour <= 9:
            time.sleep(300)  # 5 minutes during morning window
        else:
            time.sleep(1800)  # 30 minutes otherwise


def run_initial_collection():
    """V12.57: Clear stale lock, then run a quick REFRESH (not full 365-day rebuild).
    The SQLite DB on the persistent disk already has all historical data.
    Full collections blocked every REFRESH cycle by holding the lock for hours.
    V68: Added 120s initial delay to prevent pool exhaustion."""
    # V68: Wait 120s before starting to prevent pool exhaustion at startup
    print(f"[{datetime.now()}] V68: initial_collection waiting 120s before starting...")
    time.sleep(120)

    try:
        # V12.57: Clear orphaned lock files from killed instances
        lock_file = os.path.join(DATA_DIR, ".collection_lock")
        if os.path.exists(lock_file):
            try:
                os.remove(lock_file)
                print(f"[{datetime.now()}] V12.57: Cleared stale collection lock file.")
            except Exception as e:
                print(f"[{datetime.now()}] V12.57: Could not clear lock: {e}")

        # V100: 180-day lookback for initial collection to ensure 6-month backfill
        # V72.1: Disabled include_scrapers to prevent memory crash on Render
        print(f"[{datetime.now()}] V100: Running initial collection (180 days for backfill)...")
        from collector import collect_refresh
        collect_refresh(days_back=180)

        # Violation collection
        try:
            from violation_collector import collect_all_violations
            collect_all_violations(days_back=90)
        except Exception as e:
            print(f"[{datetime.now()}] Initial violation collection error: {e}")

        # Signal collection
        try:
            from signal_collector import collect_all_signals
            collect_all_signals(days_back=90)
        except Exception as e:
            print(f"[{datetime.now()}] Initial signal collection error: {e}")

        print(f"[{datetime.now()}] V12.57: Initial collection complete.")
    except Exception as e:
        print(f"[{datetime.now()}] Initial collection error: {e}")


# ===========================
# ERROR HANDLERS
# ===========================
@app.errorhandler(404)
def page_not_found(e):
    """Handle 404 errors with a styled page."""
    footer_cities = get_cities_with_data()
    return render_template('404.html', footer_cities=footer_cities), 404


# ===========================
# STARTUP: DATA COLLECTION
# ===========================
# This runs when gunicorn imports the module (or when running directly).
# Use a flag to prevent multiple workers from each starting collectors.

_collector_started = False


def _test_outbound_connectivity():
    """V12.2: Quick test of outbound API access on startup."""
    import requests
    test_session = requests.Session()
    test_session.headers.update({
        "User-Agent": "PermitGrab/1.0 (permit lead aggregator; contact@permitgrab.com)",
        "Accept": "application/json",
    })
    test_urls = [
        "https://data.cityofnewyork.us/resource/rbx6-tga4.json?$limit=1",
        "https://data.cityofchicago.org/resource/ydr8-5enu.json?$limit=1",
        "https://data.lacity.org/resource/yv23-pmwf.json?$limit=1",
    ]
    print(f"[{datetime.now()}] Testing outbound API connectivity...")
    for url in test_urls:
        try:
            resp = test_session.get(url, timeout=15)
            print(f"  [NET TEST] {url[:50]}... → {resp.status_code} ({len(resp.content)} bytes)")
        except Exception as e:
            print(f"  [NET TEST] {url[:50]}... → FAILED: {type(e).__name__}: {str(e)[:80]}")


def start_collectors():
    """Start background data collection threads. Safe to call multiple times.

    V66: Stagger thread starts to prevent connection pool stampede.
    """
    global _collector_started
    if _collector_started:
        return
    _collector_started = True

    os.makedirs(DATA_DIR, exist_ok=True)

    # V12.2: Test network connectivity before starting threads
    _test_outbound_connectivity()

    # V75: Apply known fixes to broken city_sources configs
    print(f"[{datetime.now()}] V75: Applying known config fixes...")
    try:
        fixes = fix_known_broken_configs()
        print(f"[{datetime.now()}] V75: Applied {len(fixes)} config fixes")
    except Exception as e:
        print(f"[{datetime.now()}] V75: Config fix error (non-fatal): {e}")

    # V77: Sync CITY_REGISTRY to prod_cities — activates paused cities
    print(f"[{datetime.now()}] V77: Syncing CITY_REGISTRY to prod_cities...")
    try:
        sync_city_registry_to_prod_cities()
        print(f"[{datetime.now()}] V77: Registry sync complete")
    except Exception as e:
        print(f"[{datetime.now()}] V77: Registry sync error (non-fatal): {e}")

    print(f"[{datetime.now()}] V67: Starting background collectors with staggered init...")

    # V67: Stagger each thread start by 30 seconds to avoid pool exhaustion (was 10s in V66)
    # Initial collection thread
    initial_thread = threading.Thread(target=run_initial_collection, name='initial_collection', daemon=True)
    initial_thread.start()
    print(f"[{datetime.now()}] V67: Initial collection thread started, waiting 30s...")
    time.sleep(30)

    # Scheduled daily collection thread
    collector_thread = threading.Thread(target=scheduled_collection, name='scheduled_collection', daemon=True)
    collector_thread.start()
    print(f"[{datetime.now()}] V67: Scheduled collection thread started, waiting 30s...")
    time.sleep(30)

    # V12.53: Email scheduler thread
    # V73: Added try/except to log startup failures
    try:
        email_thread = threading.Thread(target=schedule_email_tasks, name='email_scheduler', daemon=True)
        email_thread.start()
        print(f"[{datetime.now()}] V73: Email scheduler thread started, waiting 30s...")
        time.sleep(30)
    except Exception as e:
        print(f"[{datetime.now()}] [CRITICAL] Email scheduler thread failed to start: {e}")
        import traceback
        traceback.print_exc()

    # V12.55c: One-time fix for Socrata location JSON in address fields
    try:
        fix_thread = threading.Thread(target=_fix_socrata_addresses, name='socrata_fix', daemon=True)
        fix_thread.start()
        print(f"[{datetime.now()}] V67: Address cleanup thread started, waiting 30s...")
        time.sleep(30)
    except Exception as e:
        print(f"[{datetime.now()}] V12.55c: Address cleanup error: {e}")

    # V12.54: Autonomous city discovery engine
    try:
        from autonomy_engine import run_autonomy_engine
        autonomy_thread = threading.Thread(target=run_autonomy_engine, name='autonomy_engine', daemon=True)
        autonomy_thread.start()
        print(f"[{datetime.now()}] V67: Autonomy engine thread started.")
    except ImportError:
        print(f"[{datetime.now()}] V12.54: autonomy_engine.py not found, skipping.")

    print(f"[{datetime.now()}] V67: All collector threads started (staggered over ~2 minutes).")

# V12.12: Preload existing data from disk BEFORE starting collectors
# This ensures stale data is served immediately rather than showing 0 permits
preload_data_from_disk()

# V66: Removed module-level DB init — now deferred to first request via _deferred_startup()
# This prevents connection pool exhaustion during gunicorn startup.
# The sync_city_registry_to_prod(), sync_city_registry_to_prod_cities(), and
# start_collectors() are all called from _deferred_startup() on first HTTP request.
print(f"[{datetime.now()}] V70: Module loaded — Postgres pool DISABLED, SQLite only mode")


# ===========================
# MAIN
# ===========================
if __name__ == '__main__':
    # Local development only (gunicorn handles production)
    print("=" * 50)
    print("PermitGrab Server Starting")
    print(f"Dashboard: http://localhost:5000")
    print(f"API: http://localhost:5000/api/permits")
    print("=" * 50)

    app.run(host='0.0.0.0', port=5000, debug=True)
