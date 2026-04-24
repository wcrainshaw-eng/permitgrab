"""
V162: Code Violation Collector — Socrata SODA API
Fetches building/housing code violations from public open data portals.
Stores in the violations table with prod_city_id FK for fast lookups.
"""

import json
import time
import requests
from datetime import datetime, timedelta

import db as permitdb

SESSION = requests.Session()
SESSION.headers.update({'Accept': 'application/json'})

# ---------------------------------------------------------------------------
# Violation source configs — hardcoded per instructions
# ---------------------------------------------------------------------------

VIOLATION_SOURCES = {
    'new-york-city': {
        # V229 addendum I3: was hardcoded prod_city_id=1. Removed so the
        # runtime lookup in collect_for_city() resolves it dynamically —
        # otherwise a prod_cities rebuild or ID shift would silently
        # write violations against the wrong FK.
        'prod_city_id': None,
        'city': 'New York City',
        'state': 'NY',
        'endpoints': [
            {
                'name': 'HPD Violations',
                'domain': 'data.cityofnewyork.us',
                'resource_id': 'wvxf-dwi5',
                'date_field': 'inspectiondate',
                'id_field': 'violationid',
                'description_field': 'novdescription',
                'status_field': 'currentstatus',
                'type_field': 'class',
                'address_fields': {'number': 'housenumber', 'street': 'streetname'},
                'zip_field': 'zip',
                'lat_field': 'latitude',
                'lng_field': 'longitude',
            },
            {
                'name': 'DOB Violations',
                'domain': 'data.cityofnewyork.us',
                'resource_id': '3h2n-5cm9',
                'date_field': 'issue_date',
                'id_field': 'isn_dob_bis_viol',
                'description_field': 'violation_type_code',
                'status_field': 'violation_category',
                'type_field': 'violation_type',
                'address_fields': {'number': 'house_number', 'street': 'street'},
                'zip_field': None,
                'lat_field': None,
                'lng_field': None,
            },
        ],
    },
    'los-angeles': {
        # V229 addendum I3: was hardcoded prod_city_id=3; now dynamic.
        'prod_city_id': None,
        'city': 'Los Angeles',
        'state': 'CA',
        'endpoints': [
            {
                'name': 'Open Cases',
                'domain': 'data.lacity.org',
                'resource_id': 'u82d-eh7z',
                'date_field': 'adddttm',
                'id_field': 'apno',
                'description_field': 'aptype',
                'status_field': 'stat',
                'type_field': 'aptype',
                'address_fields': {'number': 'stno', 'street': 'stname', 'prefix': 'predir', 'suffix': 'suffix'},
                'zip_field': 'zip',
                'lat_field': None,
                'lng_field': None,
            },
            {
                'name': 'Closed Cases',
                'domain': 'data.lacity.org',
                'resource_id': 'rken-a55j',
                'date_field': 'adddttm',
                'id_field': 'apno',
                'description_field': 'aptype',
                'status_field': 'stat',
                'type_field': 'aptype',
                'address_fields': {'number': 'stno', 'street': 'stname', 'prefix': 'predir', 'suffix': 'suffix'},
                'zip_field': 'zip',
                'lat_field': None,
                'lng_field': None,
            },
        ],
    },
    'chicago-il': {
        # V229 addendum I3: was hardcoded prod_city_id=2; now dynamic.
        'prod_city_id': None,
        'city': 'Chicago',
        'state': 'IL',
        'endpoints': [
            {
                'name': 'Building Violations',
                'domain': 'data.cityofchicago.org',
                'resource_id': '22u3-xenr',
                'date_field': 'violation_date',
                'id_field': 'id',
                'description_field': 'violation_description',
                'status_field': 'violation_status',
                'type_field': 'violation_code',
                'address_fields': {'full': 'address'},
                'zip_field': None,
                'lat_field': 'latitude',
                'lng_field': 'longitude',
            },
        ],
    },
    'austin-tx': {
        'prod_city_id': None,  # Looked up dynamically by (city, state)
        'city': 'Austin',
        'state': 'TX',
        'endpoints': [
            {
                'name': 'Code Enforcement Cases',
                'domain': 'data.austintexas.gov',
                'resource_id': '6wtj-zbtb',
                'date_field': 'opened_date',
                'id_field': 'case_id',
                'description_field': 'description',
                'status_field': 'status',
                'type_field': 'case_type',
                'address_fields': {'full': 'address'},
                'zip_field': 'zip_code',
                'lat_field': 'latitude',
                'lng_field': 'longitude',
            },
        ],
    },
    # V258: New Orleans code enforcement — freshly-updated Socrata dataset
    # 3ehi-je3s, last update 2026-04-23. Pairs with PR #150 permit fix.
    'new-orleans-la': {
        'prod_city_id': None,
        'city': 'New Orleans',
        'state': 'LA',
        'endpoints': [
            {
                'name': 'Code Enforcement All Violations',
                'domain': 'data.nola.gov',
                'resource_id': '3ehi-je3s',
                'date_field': 'violationdate',
                'id_field': 'violationid',
                'description_field': 'description',
                'status_field': None,
                'type_field': 'violation',
                'address_fields': {'full': 'location'},
                'zip_field': None,
                'lat_field': None,
                'lng_field': None,
            },
        ],
    },
    # V184: 5 new Socrata SODA violation sources
    'seattle-wa': {
        'prod_city_id': None,
        'city': 'Seattle',
        'state': 'WA',
        'endpoints': [
            {
                'name': 'Code Complaints and Violations (SDCI)',
                'domain': 'data.seattle.gov',
                'resource_id': 'ez4a-iug7',
                'date_field': 'opendate',
                'id_field': 'recordnum',
                'description_field': 'description',
                'status_field': 'statuscurrent',
                'type_field': 'recordtypedesc',
                'address_fields': {'full': 'originaladdress1'},
                'zip_field': None,
                'lat_field': None,
                'lng_field': None,
            },
        ],
    },
    'san-francisco': {
        'prod_city_id': None,
        'city': 'San Francisco',
        'state': 'CA',
        'endpoints': [
            {
                'name': 'Notices of Violation (DBI)',
                'domain': 'data.sfgov.org',
                'resource_id': 'nbtm-fbw5',
                'date_field': 'date_filed',
                'id_field': 'complaint_number',
                'description_field': 'code_violation_desc',
                'status_field': 'status',
                'type_field': 'code_violation_desc',
                'address_fields': {'number': 'street_number', 'street': 'street_name', 'suffix': 'street_suffix'},
                'zip_field': None,
                'lat_field': None,
                'lng_field': None,
            },
        ],
    },
    'new-orleans-la': {
        'prod_city_id': None,
        'city': 'New Orleans',
        'state': 'LA',
        'endpoints': [
            {
                'name': 'Code Enforcement All Violations',
                'domain': 'data.nola.gov',
                'resource_id': '3ehi-je3s',
                'date_field': 'violationdate',
                'id_field': 'violationid',
                'description_field': 'description',
                'status_field': None,
                'type_field': 'codesection',
                'address_fields': {'full': 'location'},
                'zip_field': None,
                'lat_field': None,
                'lng_field': None,
            },
        ],
    },
    'cincinnati-oh': {
        'prod_city_id': None,
        'city': 'Cincinnati',
        'state': 'OH',
        'endpoints': [
            {
                'name': 'Code Enforcement',
                'domain': 'data.cincinnati-oh.gov',
                'resource_id': 'cncm-znd6',
                'date_field': 'entered_date',
                'id_field': 'number_key',
                'description_field': 'comp_type_desc',
                'status_field': 'data_status_display',
                'type_field': 'sub_type_desc',
                'address_fields': {'full': 'full_address'},
                'zip_field': None,
                'lat_field': None,
                'lng_field': None,
            },
        ],
    },
    'buffalo-ny': {
        'prod_city_id': None,
        'city': 'Buffalo',
        'state': 'NY',
        'endpoints': [
            {
                'name': 'Code Violations (DPIS)',
                'domain': 'data.buffalony.gov',
                'resource_id': 'ivrf-k9vm',
                'date_field': 'date',
                'id_field': 'case_number',
                'description_field': 'description',
                'status_field': 'status',
                'type_field': 'code_section',
                'address_fields': {'full': 'address'},
                'zip_field': None,
                'lat_field': None,
                'lng_field': None,
            },
        ],
    },
    # V182 PR2 + V184: Skipped endpoints after endpoint discovery:
    #   - Fort Worth: data.fortworthtexas.gov redirects to ArcGIS (needs new collector)
    #   - Mesa: data.mesaaz.gov nnr9-eg5e returns empty, amsn-zipb 404
    #   - Memphis: data.memphistn.gov 2a5n-q5ky and h4nu-tbge return empty
    #   - Little Rock: data.littlerock.gov f28w-j2qp returns empty records
    #   - Houston: no Socrata violations dataset on data.houstontx.gov
    #   - Boston: CKAN platform (data.boston.gov), 17K records but different API pattern
    #   - Greensboro: no datasets found on data.greensboro-nc.gov
    #   - Phila (hq7x): aggregated monthly counts, not individual violations
    #   - Phila (jr6a): crime incidents, not building violations
    #   - data.mesaaz.gov resources nnr9-eg5e returns empty, amsn-zipb 404s
    #
    # V216 audit — top-10 cities still without violations (Dallas/Phoenix/SA/SD/Houston):
    #   - Dallas: www.dallasopendata.com has food inspections + parking only, no code
    #     enforcement. DallasGIS ArcGIS has CRM_30Days but rows are 311 service events
    #     (ANTI_KEYWORDS: event requests), not building code violations.
    #   - Phoenix: maps.phoenix.gov NSD_Property_Maintenance has 25,820 real code-
    #     enforcement cases (FY2024-FY2026, still updating). BLOCKED: schema has no
    #     date field — only CSM_CASENO, CSM_ADDRESS, CSM_STATUS, NOTES, TOTAL_INSP.
    #     Needs a no-date-field mode (OID pagination + no WHERE filter) in this
    #     collector to ingest. Candidate for follow-up PR.
    #   - San Antonio: 311_Cases_WFL1 'Property Maintenance' category has 12,310
    #     code-enforcement rows but CREATE_DATE is a STRING field (not esri date)
    #     AND the dataset is frozen at 2020-07-30 (stopped updating). Not usable.
    #   - San Diego: data.sandiego.gov is S3-hosted static files with no live search
    #     API; ArcGIS Hub returns no code-enforcement dataset. No viable source.
    #   - Houston: data.houstontx.gov has "Building Code Enforcement Violations (DON)"
    #     but it's distributed as XLSX files only — no API. Would need an xlsx-fetch
    #     path (openpyxl dep + periodic re-download). Candidate for follow-up PR.
    # Document here to prevent re-investigation next iteration.
    'philadelphia': {
        'prod_city_id': None,  # Looked up dynamically
        'city': 'Philadelphia',
        'state': 'PA',
        'platform': 'carto',
        'endpoints': [
            {
                'name': 'L&I Violations',
                'carto_base': 'https://phl.carto.com/api/v2/sql',
                'carto_table': 'violations',
                'date_field': 'casecreateddate',
                'id_field': 'violationnumber',
                'description_field': 'violationcodetitle',
                'status_field': 'violationstatus',
                'type_field': 'casetype',
                'address_fields': {'full': 'address'},
                'zip_field': 'zip',
                'lat_field': None,
                'lng_field': None,
            },
        ],
    },
    # V197: Expansion — 1 new Socrata + 5 new ArcGIS sources
    'kansas-city-mo': {
        'prod_city_id': None,
        'city': 'Kansas City',
        'state': 'MO',
        'endpoints': [
            {
                'name': 'Property Violations (NPD)',
                'domain': 'data.kcmo.org',
                'resource_id': 'vq3e-m9ge',
                'date_field': 'date_found',
                'id_field': 'violationid',
                'description_field': 'ord_text',
                'status_field': 'vio_status',
                'type_field': 'description',
                'address_fields': {'full': 'full_address'},
                'zip_field': 'postalcode',
                'lat_field': None,
                'lng_field': None,
            },
        ],
    },
    'indianapolis-in': {
        'prod_city_id': None,
        'city': 'Indianapolis',
        'state': 'IN',
        # V200-T2: endpoint stopped updating — max OPEN_DATE on server is 2024-02-27
        # (910K historical rows, no new rows since). Retained for future backfill.
        'endpoints': [
            {
                'name': 'Code Enforcement Violations',
                'platform': 'arcgis',
                'resource_id': 'indy-code-enforcement',
                'arcgis_url': 'https://gis.indy.gov/server/rest/services/OpenData/OpenData_NonSpatial/MapServer/1',
                'date_field': 'OPEN_DATE',
                'id_field': 'CASE_NUMBER',
                'description_field': 'CASE_TYPE',
                'status_field': 'CASE_STATUS',
                'type_field': 'CASE_TYPE',
                'address_fields': {'full': 'STREET_ADDRESS'},
                'zip_field': 'ZIP',
                'lat_field': None,
                'lng_field': None,
            },
        ],
    },
    'columbus-oh': {
        'prod_city_id': None,
        'city': 'Columbus',
        'state': 'OH',
        'endpoints': [
            {
                'name': 'Code Enforcement Cases',
                'platform': 'arcgis',
                'resource_id': 'columbus-code-enforcement',
                'arcgis_url': 'https://maps2.columbus.gov/arcgis/rest/services/Schemas/BuildingZoning/MapServer/23',
                'date_field': 'B1_FILE_DD',
                'id_field': 'B1_ALT_ID',
                'description_field': 'B1_PER_CATEGORY',
                'status_field': 'B1_APPL_STATUS',
                'type_field': 'B1_PER_SUB_TYPE',
                'address_fields': {'full': 'SITE_ADDRESS'},
                'zip_field': None,
                'lat_field': None,
                'lng_field': None,
            },
        ],
    },
    'greensboro-nc': {
        'prod_city_id': None,
        'city': 'Greensboro',
        'state': 'NC',
        # V200-T2: endpoint stale — max IssuedDate on server is 2024-06-18
        # (96K historical rows). Retained for future backfill.
        'endpoints': [
            {
                'name': 'Code Compliance All Violations',
                'platform': 'arcgis',
                'resource_id': 'greensboro-cc-violations',
                'arcgis_url': 'https://gis.greensboro-nc.gov/arcgis/rest/services/OpenGateCity/OpenData_CC_DS/MapServer/3',
                'date_field': 'IssuedDate',
                'id_field': 'ViolationID',
                'description_field': 'ViolationDescription',
                'status_field': 'CaseStatus',
                'type_field': 'CaseType',
                'address_fields': {'full': 'FullAddress'},
                'zip_field': None,
                'lat_field': 'Latitude',
                'lng_field': 'Longitude',
            },
        ],
    },
    'nashville-tn': {
        'prod_city_id': None,
        'city': 'Nashville',
        'state': 'TN',
        'endpoints': [
            {
                'name': 'Property Standards Violations',
                'platform': 'arcgis',
                'resource_id': 'nashville-property-standards',
                'arcgis_url': 'https://services2.arcgis.com/HdTo6HJqh92wn4D8/arcgis/rest/services/Property_Standards_Violations_2/FeatureServer/0',
                'date_field': 'Date_Received',
                'id_field': 'Request_Nbr',
                'description_field': 'Reported_Problem',
                'status_field': 'Status',
                'type_field': 'Subtype_Description',
                'address_fields': {'full': 'Property_Address'},
                'zip_field': 'ZIP',
                'lat_field': 'Lat',
                'lng_field': 'Lon',
            },
        ],
    },
    'fort-worth-tx': {
        'prod_city_id': None,
        'city': 'Fort Worth',
        'state': 'TX',
        'endpoints': [
            {
                'name': 'Code Violations',
                'platform': 'arcgis',
                'resource_id': 'fort-worth-code-violations',
                'arcgis_url': 'https://services5.arcgis.com/3ddLCBXe1bRt7mzj/arcgis/rest/services/CFW_Open_Data_Code_Violations_Table_view/FeatureServer/0',
                'date_field': 'Case_Created_Date',
                'id_field': 'Violation_ID',
                'description_field': 'Complaint_Type_Description',
                'status_field': 'Case_Current_Status',
                'type_field': 'Complaint_Type_Description',
                'address_fields': {'full': 'Violation_Address'},
                'zip_field': None,
                'lat_field': 'Latitude',
                'lng_field': 'Longitude',
            },
        ],
    },
    # V200: San Jose CA — ArcGIS Code Complaints (Open), fresh
    'san-jose-ca': {
        'prod_city_id': None,
        'city': 'San Jose',
        'state': 'CA',
        'endpoints': [
            {
                'name': 'Code Complaints (Open)',
                'platform': 'arcgis',
                'resource_id': 'san-jose-code-complaints',
                'arcgis_url': 'https://geo.sanjoseca.gov/server/rest/services/PLN/PLN_PermitsAndComplaints/MapServer/1',
                'date_field': 'OPENDATE',
                'id_field': 'CASENUMBER',
                'description_field': 'DESCRIPTION',
                'status_field': 'CASESTATUS',
                'type_field': 'PROGRAM',
                'address_fields': {'full': 'LOCATION'},
                'zip_field': None,
                'lat_field': None,
                'lng_field': None,
            },
        ],
    },
    # V200: Miami-Dade FL — ArcGIS Code Compliance Violations, fresh
    'miami-dade-fl': {
        'prod_city_id': None,
        'city': 'Miami',
        'state': 'FL',
        'endpoints': [
            {
                'name': 'Code Compliance Violations (MDC)',
                'platform': 'arcgis',
                'resource_id': 'miami-dade-code-compliance',
                'arcgis_url': 'https://services.arcgis.com/8Pc9XBTAsYuxx9Ny/arcgis/rest/services/CCVIOL_gdb/FeatureServer/0',
                'date_field': 'CASE_DATE',
                'id_field': 'CASE_NUM',
                'description_field': 'PROBLEM_DESC',
                'status_field': 'STAT_DESC',
                'type_field': 'PROBLEM',
                'address_fields': {'full': 'ADDRESS'},
                'zip_field': None,
                'lat_field': None,
                'lng_field': None,
            },
        ],
    },
    # V244b: Cape Coral FL — ArcGIS MapServer TABLE (non-spatial). 635K
    # code enforcement cases, updated actively. Same return-geometry
    # requirement as Phoenix since it's a Table type. Paired with the
    # 1,751 Cape Coral profiles + FL DBPR phones (pending V244 import
    # completion) to complete the three-dimension ad-ready set.
    'cape-coral': {
        'prod_city_id': None,
        'city': 'Cape Coral',
        'state': 'FL',
        'endpoints': [
            {
                'name': 'Code Enforcement Cases',
                'platform': 'arcgis',
                'arcgis_url': 'https://capeims.capecoral.gov/arcgis/rest/services/OpenData/OpenData/MapServer/5',
                'mapserver_table': True,
                'date_field': 'opened',
                'id_field': 'CaseNumber',
                'description_field': 'case_description',
                'status_field': 'Status',
                'type_field': 'CaseType',
                'address_fields': {'full': 'Main_Site_addr'},
                'zip_field': 'Main_Site_Zip',
                'lat_field': None,
                'lng_field': None,
                'resource_id': 'cape-coral-code-enforcement',
            },
        ],
    },
    # V244b: Cleveland OH — FeatureServer, 30K Building Complaint
    # Violation Notices (Accela-sourced, exported to ArcGIS hub).
    # Fresh date range. Pairs with Cleveland's 1,068 profiles —
    # phone enrichment remains the gap (OH OCILB has no public phone
    # feed) but at least the violations leg lights up.
    'cleveland-oh': {
        'prod_city_id': None,
        'city': 'Cleveland',
        'state': 'OH',
        'endpoints': [
            {
                'name': 'Building Complaint Violation Notices',
                'platform': 'arcgis',
                'arcgis_url': 'https://services3.arcgis.com/dty2kHktVXHrqO8i/arcgis/rest/services/Complaint_Violation_Notices/FeatureServer/0',
                'date_field': 'FILE_DATE',
                'id_field': 'VIOLATION_NUMBER',
                'description_field': 'VIOLATION_APP_STATUS',
                'status_field': 'VIOLATION_APP_STATUS',
                'type_field': 'SOURCE',
                'address_fields': {'full': 'PRIMARY_ADDRESS'},
                'zip_field': None,
                'lat_field': None,
                'lng_field': None,
                'resource_id': 'cleveland-complaint-violation-notices',
            },
        ],
    },
    # V244b: Fort Lauderdale FL — CodeCaseTracker MapServer layer. 66K
    # code cases, spatial Feature Layer with geometry (so no
    # mapserver_table flag needed). Pairs with FL DBPR phones (V244
    # streaming import) to put Fort Lauderdale into ad-ready territory.
    'fort-lauderdale': {
        'prod_city_id': None,
        'city': 'Fort Lauderdale',
        'state': 'FL',
        'endpoints': [
            {
                'name': 'Code Cases',
                'platform': 'arcgis',
                'arcgis_url': 'https://gis.fortlauderdale.gov/arcgis/rest/services/CodeCaseTracker/CodeCase/MapServer/0',
                'date_field': 'INITDATE',
                'id_field': 'CASENUM',
                'description_field': 'CASETYPE',
                'status_field': 'CASESTATUS',
                'type_field': 'CASETYPE',
                'address_fields': {'full': 'SITEADDRESS'},
                'zip_field': 'PARCELZIP',
                'lat_field': None,
                'lng_field': None,
                'resource_id': 'fort-lauderdale-code-cases',
            },
        ],
    },
    # V243: Phoenix AZ — ArcGIS MapServer TABLE (non-spatial). 25K+
    # code enforcement cases, 4K+ from 2026, actively maintained. Data
    # covers wider Phoenix metro (Scottsdale, Glendale, Mesa appear in
    # the same table) so address_city_filter drops non-Phoenix rows
    # before insert. The table has no date column — year comes from
    # the CSM_CASENO prefix (PEF2026-xxxxx) and incremental_where pins
    # a single year per fetch so we don't haul the whole 25K backlog
    # on every cycle.
    'phoenix-az': {
        'prod_city_id': None,
        'city': 'Phoenix',
        'state': 'AZ',
        'endpoints': [
            {
                'name': 'NSD Property Maintenance',
                'platform': 'arcgis',
                'arcgis_url': 'https://maps.phoenix.gov/pub/rest/services/Public/NSD_Property_Maintenance/MapServer/0',
                'mapserver_table': True,
                'date_field': None,
                'date_from_id_pattern': r'PEF(\d{4})-\d+',
                'incremental_where': "CSM_CASENO LIKE 'PEF{YEAR}%'",
                'orderby': 'ESRI_OID ASC',
                'id_field': 'CSM_CASENO',
                'description_field': 'NOTES',
                'status_field': 'CSM_STATUS',
                'type_field': None,
                'fixed_violation_type': 'Property Maintenance',
                'address_fields': {'full': 'CSM_ADDRESS'},
                'address_parse': 'phoenix_full',
                'address_city_filter': 'PHOENIX',
                'resource_id': 'phoenix-nsd-violations',
                'zip_field': None,
                'lat_field': None,
                'lng_field': None,
            },
        ],
    },
    # V242 P2: Miami-Dade County FL — ArcGIS FeatureServer. 183K records,
    # refreshed daily; covers unincorporated Miami-Dade. Pairs with the
    # ~4K Miami-Dade contractor profiles (phone-enriched via FL DBPR
    # import) to make Miami-Dade ad-ready.
    'miami-dade-county': {
        'prod_city_id': None,
        'city': 'Miami-Dade County',
        'state': 'FL',
        'endpoints': [
            {
                'name': 'Code Enforcement Violations',
                'platform': 'arcgis',
                'resource_id': 'miami-dade-ccviol',
                'arcgis_url': 'https://services.arcgis.com/8Pc9XBTAsYuxx9Ny/arcgis/rest/services/CCVIOL_gdb/FeatureServer/0',
                'date_field': 'CASE_DATE',
                'id_field': 'CASE_NUM',
                'description_field': 'PROBLEM_DESC',
                'status_field': 'STAT_DESC',
                'type_field': 'PROBLEM_DESC',
                'address_fields': {'full': 'ADDRESS'},
                'zip_field': None,
                'lat_field': None,
                'lng_field': None,
            },
        ],
    },
    # V239b: Orlando FL — Socrata, 249K code enforcement cases, fresh through
    # 2026-04-21. Orlando has 1,681 contractor profiles already; pairing
    # violations with profiles lifts the page to "has all three data
    # dimensions" once FL DBPR fills in phones.
    'orlando': {
        'prod_city_id': None,
        'city': 'Orlando',
        'state': 'FL',
        'endpoints': [
            {
                'name': 'Code Enforcement Cases',
                'domain': 'data.cityoforlando.net',
                'resource_id': 'k6e8-nw6w',
                'date_field': 'casedt',
                'id_field': 'apno',
                'description_field': 'case_comments',
                'status_field': 'caseinfostatus',
                'type_field': 'case_type',
                # location_notes is the clean street address variant —
                # derived_address bakes " ORLANDO FL" into the field and
                # sorts badly downstream. casename is a description.
                'address_fields': {'full': 'location_notes'},
                'zip_field': None,
                'lat_field': None,
                'lng_field': None,
            },
        ],
    },
    # V239b: Mesa AZ — Socrata, 78K code enforcement cases, fresh through
    # 2026-04-20. (V197 Phase 2 previously noted "0 violation hits" under
    # Mesa, but that search used the old Socrata catalog domain — the live
    # dataset is at data.mesaaz.gov/resource/hgf6-yenu.) Mesa has ~8.4K
    # contractor profiles, so violations here unlock a lot of page value.
    'mesa-az-accela': {
        'prod_city_id': None,
        'city': 'Mesa',
        'state': 'AZ',
        'endpoints': [
            {
                'name': 'Code Enforcement Cases',
                'domain': 'data.mesaaz.gov',
                'resource_id': 'hgf6-yenu',
                'date_field': 'opened_date',
                'id_field': 'record_id',
                'description_field': 'description',
                'status_field': 'status',
                'type_field': 'permit_type',
                'address_fields': {'full': 'case_address'},
                'zip_field': 'case_zip',
                'lat_field': 'latitude',
                'lng_field': 'longitude',
            },
        ],
    },
    # V198: Charlotte NC — ArcGIS, fresh (sampled 2026-04-18)
    'charlotte-nc': {
        'prod_city_id': None,
        'city': 'Charlotte',
        'state': 'NC',
        'endpoints': [
            {
                'name': 'Code Enforcement Cases All',
                'platform': 'arcgis',
                'resource_id': 'charlotte-code-enforcement',
                'arcgis_url': 'https://gis.charlottenc.gov/arcgis/rest/services/HNS/CodeEnforcementCasesAll/MapServer/0',
                'date_field': 'DateCreated',
                'id_field': 'CaseNumber',
                'description_field': 'DetailedDescription',
                'status_field': 'CaseStatus',
                'type_field': 'CaseType',
                'address_fields': {'full': 'FullAddress'},
                'zip_field': None,
                'lat_field': None,
                'lng_field': None,
            },
        ],
    },
    # V259: Denver CO — ArcGIS FeatureServer TABLE under ODC 311. Agency
    # filter isolates the Community Planning & Development subset
    # (~19.7K rolling-12-month records; ~10K match building-relevant
    # Case_Summary values). V198 dead-end note flagged the geospatial
    # hub as having no "violations" dataset — true, but the 311 table
    # exposes CPD code-enforcement service requests with address +
    # case date, which is what we need. The base_where pre-filters to
    # building-permit / structural / construction-violation Case_Summary
    # values so nuisance 311 (graffiti, parking, tree) stays excluded.
    'denver-co': {
        'prod_city_id': None,
        'city': 'Denver',
        'state': 'CO',
        'endpoints': [
            {
                'name': 'CPD Code Enforcement 311',
                'platform': 'arcgis',
                'resource_id': 'denver-cpd-311',
                'arcgis_url': 'https://services1.arcgis.com/zdB7qR0BtYrg0Xpl/arcgis/rest/services/ODC_service_requests_311/FeatureServer/66',
                'mapserver_table': True,
                'base_where': (
                    "Agency='Community Planning & Development' AND ("
                    "Case_Summary LIKE '%Construction%' OR "
                    "Case_Summary LIKE '%Permit%' OR "
                    "Case_Summary LIKE '%Violation%' OR "
                    "Case_Summary LIKE '%Illegal Occupancy%' OR "
                    "Case_Summary LIKE '%Illegal Home Business%' OR "
                    "Case_Summary LIKE '%Dilapidated%' OR "
                    "Case_Summary LIKE '%Vacant%' OR "
                    "Case_Summary LIKE '%Inspection%'"
                    ")"
                ),
                'date_field': 'Case_Created_Date',
                'id_field': 'OBJECTID',
                'description_field': 'Case_Summary',
                'status_field': 'Case_Status',
                'type_field': 'Case_Summary',
                'address_fields': {'full': 'Incident_Address_1'},
                'zip_field': 'Incident_Zip_Code',
                'lat_field': 'Latitude',
                'lng_field': 'Longitude',
            },
        ],
    },
    # V259: San Antonio TX — ArcGIS FeatureServer. 311 All Service Calls
    # includes Development Services / Property Maintenance cases which
    # are the city's code enforcement workflow (no separate violations
    # dataset exists — V198 DCAT audit confirmed). base_where filters to
    # building-relevant TypeName values (dangerous premise, building
    # without permit, structure concerns, CoO investigation, emergency
    # demolition); drops pure nuisance (graffiti, overgrown yard, junk
    # vehicle, illegal parking). ~13K matching records; max opened
    # 2025-12-07 (feed refresh lag is ~4 months but historical coverage
    # is dense).
    'san-antonio-tx': {
        'prod_city_id': None,
        'city': 'San Antonio',
        'state': 'TX',
        'endpoints': [
            {
                'name': '311 Property Maintenance',
                'platform': 'arcgis',
                'resource_id': 'san-antonio-311-property-maintenance',
                'arcgis_url': 'https://services.arcgis.com/g1fRTDLeMgspWrYp/arcgis/rest/services/311_All_Service_Calls/FeatureServer/0',
                'base_where': (
                    "ReasonName='Code Enforcement' AND ("
                    "TypeName LIKE '%Dangerous Premise%' OR "
                    "TypeName LIKE '%Building Without%' OR "
                    "TypeName LIKE '%Structure Concerns%' OR "
                    "TypeName LIKE '%Structure Maintenance%' OR "
                    "TypeName LIKE '%Certificate of Occupancy%' OR "
                    "TypeName LIKE '%Emergency Demolition%' OR "
                    "TypeName LIKE '%DP Warrant%' OR "
                    "TypeName LIKE '%Vacant/Overgrown%' OR "
                    "TypeName LIKE '%Property Maintenance%' OR "
                    "TypeName LIKE '%Improper Sewer%' OR "
                    "TypeName LIKE '%Broken Sewer%' OR "
                    "TypeName LIKE '%Absentee Property%'"
                    ")"
                ),
                'date_field': 'OpenedDateTime',
                'id_field': 'CaseID',
                'description_field': 'ObjectDescription',
                'status_field': 'CaseStatus',
                'type_field': 'TypeName',
                'address_fields': {'full': 'ObjectDescription'},
                'zip_field': None,
                'lat_field': None,
                'lng_field': None,
            },
        ],
    },
    # V260: Scottsdale AZ — ArcGIS MapServer TABLE (non-spatial). 17.8K
    # code violation records, rolling year (max DateComplaintReceived
    # 2026-04-20 at check time). Pairs with 762 existing contractor
    # profiles → moves Scottsdale toward ad-ready. ViolationCategory
    # includes vacation-rental (STVR) cases plus substantive land-use
    # items. Street components are split across number/direction/name/
    # type fields; _build_address assembles via the parts-style
    # address_fields config.
    'scottsdale-az': {
        'prod_city_id': None,
        'city': 'Scottsdale',
        'state': 'AZ',
        'endpoints': [
            {
                'name': 'Planning and Development Code Violations',
                'platform': 'arcgis',
                'resource_id': 'scottsdale-code-violations',
                'arcgis_url': 'https://maps.scottsdaleaz.gov/arcgis/rest/services/OpenData_Tabular/MapServer/10',
                'mapserver_table': True,
                'date_field': 'DateComplaintReceived',
                'id_field': 'ViolationID',
                'description_field': 'ViolationCode',
                'status_field': 'ComplaintStatus',
                'type_field': 'ViolationCategory',
                'address_fields': {
                    'number': 'StreetNumber',
                    'prefix': 'StreetDirection',
                    'street': 'StreetName',
                    'suffix': 'StreetType',
                },
                'zip_field': 'ZipCode',
                'lat_field': 'Latitude',
                'lng_field': 'Longitude',
            },
        ],
    },
    # V261: Baton Rouge LA — ArcGIS FeatureServer. EBRGIS 311 Citizen
    # Request feed shared across 4 departments; base_where isolates
    # Division='BLIGHT ENFORCEMENT' under DEVELOPMENT → 2,917 records,
    # fresh through 2026-04-21. Blight typenames are substantive
    # property-maintenance work (condemned/torn-down, missing windows,
    # debris removal) mixed with nuisance (junk vehicles, tall grass).
    # Pairs with 1,004 existing baton-rouge-la profiles.
    'baton-rouge-la': {
        'prod_city_id': None,
        'city': 'Baton Rouge',
        'state': 'LA',
        'endpoints': [
            {
                'name': 'Blight Enforcement (EBRGIS 311)',
                'platform': 'arcgis',
                'resource_id': 'baton-rouge-blight-enforcement',
                'arcgis_url': 'https://services.arcgis.com/KYvXadMcgf0K1EzK/arcgis/rest/services/311_Citizen_Request_for_Service___All_Requests/FeatureServer/0',
                'base_where': "Division='BLIGHT ENFORCEMENT'",
                'date_field': 'createdate',
                'id_field': 'id',
                'description_field': 'comments',
                'status_field': 'StatusDesc',
                'type_field': 'typename',
                'address_fields': {'full': 'StreetAddress'},
                'zip_field': None,
                'lat_field': None,
                'lng_field': None,
            },
        ],
    },
    # V261: Raleigh NC — ArcGIS FeatureServer. "Ask Raleigh Requests"
    # is the city's 311 system; base_where isolates CATEGORY='Housing
    # & Neighborhoods' which covers Public Nuisance + Unsafe Housing
    # Conditions (~1.2K records, max applied 2026-01-21 at check time —
    # ~3mo refresh lag, comparable to San Antonio's 311 lag pattern).
    # Pairs with 895 existing raleigh contractor profiles.
    'raleigh-nc': {
        'prod_city_id': None,
        'city': 'Raleigh',
        'state': 'NC',
        'endpoints': [
            {
                'name': 'Housing & Neighborhoods (Ask Raleigh)',
                'platform': 'arcgis',
                'resource_id': 'raleigh-housing-neighborhoods',
                'arcgis_url': 'https://services.arcgis.com/v400IkDOw1ad7Yad/arcgis/rest/services/Ask_Raleigh_Requests/FeatureServer/0',
                'base_where': "CATEGORY='Housing & Neighborhoods'",
                'date_field': 'APPLIED_DATE',
                'id_field': 'NUMBER',
                'description_field': 'REQUEST_TYPE',
                'status_field': 'STATUS',
                'type_field': 'SERVICE',
                'address_fields': {'full': 'ADDRESS'},
                'zip_field': 'ZIP_CODE',
                'lat_field': None,
                'lng_field': None,
            },
        ],
    },
    # V262: Little Rock AR — Socrata. "Little Rock 311 Service Calls"
    # 2x6n-j9fb at data.littlerock.gov. Updated daily (last refresh
    # 2026-04-23). 265K records of issue_type='Code Violations' — but
    # the bulk is High Grass/Trash/Parking nuisance, so $where filters
    # to the building-relevant housing + rental code subset (~30K of
    # 265K). Socrata collector supports $where clauses via the existing
    # `extra_where` hook? No — need to leverage a per-source filter.
    # Using the date_field gating alone would pull nuisance noise, so
    # bake the subcategory whitelist into a combined where clause
    # (Socrata SODA supports this natively in $where). Pairs with 638
    # existing little-rock-ar profiles.
    'little-rock-ar': {
        'prod_city_id': None,
        'city': 'Little Rock',
        'state': 'AR',
        'endpoints': [
            {
                'name': '311 Housing Code Violations',
                'domain': 'data.littlerock.gov',
                'resource_id': '2x6n-j9fb',
                'date_field': 'ticket_created_date_time',
                'id_field': 'ticket_id',
                'description_field': 'issue_sub_category',
                'status_field': 'ticket_status',
                'type_field': 'issue_sub_category',
                'address_fields': {'full': 'street_address'},
                'zip_field': 'zip',
                'lat_field': 'latitude',
                'lng_field': 'longitude',
                # Socrata extra filter bolted onto the $where via the
                # existing Socrata branch's $where builder — we need
                # the ReasonName-style subset so nuisance-only tickets
                # stay out of the digest. (High Grass + Trash + Parking
                # in Yard + Graffiti + Abandoned Vehicle are the
                # nuisance we're excluding; what's left is Housing /
                # Rental Code / Rental Inspections / Illegal Dumping /
                # Mobile-home variants.)
                'socrata_extra_where': (
                    "issue_type = 'Code Violations' AND "
                    "(issue_sub_category LIKE 'Housing%' OR "
                    "issue_sub_category LIKE '%Rental%' OR "
                    "issue_sub_category LIKE '%Mobile Home%' OR "
                    "issue_sub_category = 'Illegal Dumping')"
                ),
            },
        ],
    },
    # V263: Boulder County CO — ArcGIS FeatureServer. Accela-sourced
    # feed at maps.bouldercounty.org exposes 272K planning records
    # across 8 modules (Building/Code/Planning/Licensing/AirQuality/
    # PublicHealth/PublicWorks/SpecialEvents); base_where isolates
    # Module='Code' (10,159 records) then whitelists the building-
    # relevant DocketTypes so broad weed/sign/rubbish/liquor-review
    # noise stays out. Pairs with boulder-county prod_city_id=949
    # which already collects permits via Accela. Fresh through
    # 2026-04-21.
    'boulder-county-co': {
        'prod_city_id': None,
        'city': 'Boulder County',
        'state': 'CO',
        'endpoints': [
            {
                'name': 'Boulder County Code Enforcement (Accela-sourced)',
                'platform': 'arcgis',
                'resource_id': 'boulder-county-code-enforcement',
                'arcgis_url': 'https://maps.bouldercounty.org/arcgis/rest/services/PLANNING/OP_Accela_Point/MapServer/0',
                'mapserver_table': False,
                'base_where': (
                    "Module='Code' AND DocketType IN ("
                    "'Zoning Enforcement',"
                    "'Building Code Violation',"
                    "'Grading Enforcement',"
                    "'Illegal Dwelling Enforcement',"
                    "'Multiple Rubbish Weeds and Unsafe Structure Enforcement',"
                    "'Outdoor Storage',"
                    "'Rental Licensing Enforcement',"
                    "'Unsafe Structure Enforcement'"
                    ")"
                ),
                'date_field': 'ApplicationDate',
                'id_field': 'CAPID',
                'description_field': 'Description',
                'status_field': 'ApplicationStatus',
                'type_field': 'DocketType',
                'address_fields': {'full': 'Address'},
                'zip_field': None,
                'lat_field': None,
                'lng_field': None,
            },
        ],
    },
    # V264: Aurora CO — ArcGIS MapServer Feature Layer. City publishes
    # Code Enforcement Violations as rolling-window datasets
    # (1-month/6-months/1-year) under ags.auroragov.org/aurora/rest/
    # services/OpenData/MapServer. The 1-year layer gives the most
    # backfill headroom (12,227 records, fresh through 2026-04-21).
    # Pairs with Aurora prod_city_id that already collects permits
    # via accela (Aurora CO had 968 recent permits in last audit).
    'aurora-co': {
        'prod_city_id': None,
        'city': 'Aurora',
        'state': 'CO',
        'endpoints': [
            {
                'name': 'Code Enforcement Violations (1 Year)',
                'platform': 'arcgis',
                'resource_id': 'aurora-co-code-violations-1y',
                'arcgis_url': 'https://ags.auroragov.org/aurora/rest/services/OpenData/MapServer/206',
                'date_field': 'violation_date',
                'id_field': 'OBJECTID',
                'description_field': 'violation_comments',
                'status_field': 'folderstatus',
                'type_field': 'Violation',
                'address_fields': {'full': 'ADDRESS'},
                'zip_field': None,
                'lat_field': 'LATITUDE',
                'lng_field': 'LONGITUDE',
            },
        ],
    },
    # V265: Louisville Metro KY — ArcGIS FeatureServer. Metro 311
    # service requests, per-year layers. The current-year (2026) layer
    # has 59.7K rows; base_where isolates Exterior (2.9K — construction
    # debris, trash piles) + Interior (839) + Graffiti (216) —
    # the property-maintenance subset of 311. Skips Streets/Sidewalks/
    # Trees/Animals/Signals nuisance. Pairs with existing louisville
    # Metro permits (776 recent at last audit). Per-year endpoints
    # means next year we'll need to rotate to metro_311_2027 — flag
    # for V285 calendar maintenance.
    'louisville-metro-ky': {
        'prod_city_id': None,
        'city': 'Louisville',
        'state': 'KY',
        'endpoints': [
            {
                'name': 'Metro 311 Code Enforcement (2026)',
                'platform': 'arcgis',
                'resource_id': 'louisville-metro-311-2026',
                'arcgis_url': 'https://services1.arcgis.com/79kfd2K6fskCAkyg/arcgis/rest/services/metro_311_2026/FeatureServer/0',
                'base_where': (
                    "service_name IN ('Exterior','Interior','Graffiti')"
                ),
                'date_field': 'requested_datetime',
                'id_field': 'service_request_id',
                'description_field': 'description',
                'status_field': 'status_description',
                'type_field': 'service_name',
                'address_fields': {'full': 'address'},
                'zip_field': 'zip_code',
                'lat_field': 'latitude',
                'lng_field': 'longitude',
            },
        ],
    },
    # V266: Glendale AZ — ArcGIS FeatureServer. GlendaleOne Code
    # Compliance Cases has 50,491 records, fresh through 2026-04-03.
    # base_where keeps building-relevant RequestTypeName values
    # (Property Maintenance, Building Without A Permit, Code Compliance
    # Referral, Code General Requests, Graffiti) — drops pure nuisance
    # (Animal, Noise, Yard Sale, Home-Based Business, Pool, Vehicle).
    # Street components split across StreetNum/StreetName; existing
    # parts-style address_fields assembler covers it. Pairs with
    # existing glendale-az prod_city_id=21770 (currently paused —
    # collector will resolve the FK via city+state lookup regardless).
    'glendale-az': {
        'prod_city_id': None,
        'city': 'Glendale',
        'state': 'AZ',
        'endpoints': [
            {
                'name': 'GlendaleOne Code Compliance',
                'platform': 'arcgis',
                'resource_id': 'glendale-az-code-compliance',
                'arcgis_url': 'https://services1.arcgis.com/9fVTQQSiODPjLUTa/arcgis/rest/services/GlendaleOne_Code_Compliance_Cases/FeatureServer/0',
                'base_where': (
                    "RequestTypeName IN ("
                    "'Property Maintenance - Private Property',"
                    "'Building Without A Permit',"
                    "'Code Compliance - Referral',"
                    "'Code General Requests',"
                    "'Graffiti - Refer to Code Compliance'"
                    ")"
                ),
                'date_field': 'RequestDate',
                'id_field': 'CodeCaseNumber',
                'description_field': 'Violation1',
                'status_field': 'RequestStatus',
                'type_field': 'RequestTypeName',
                'address_fields': {'number': 'StreetNum', 'street': 'StreetName'},
                'zip_field': None,
                'lat_field': 'Latitude',
                'lng_field': 'Longitude',
            },
        ],
    },
    # V267: Columbia SC — ArcGIS FeatureServer. ColaCityGIS publishes
    # "Code Violation Case Status" (service name CodeRental) with 7,852
    # records, max OpenedDate 2026-01-15 (~3mo lag). Fields include
    # CaseNum, Problem, CaseStatus, ADDRESS, Neighborhood — the
    # Problem/CaseStatus/ADDRESS trio is clean lead-gen material (no
    # need for base_where filtering). Pairs with existing columbia
    # prod_city_id=647.
    'columbia-sc': {
        'prod_city_id': None,
        'city': 'Columbia',
        'state': 'SC',
        'endpoints': [
            {
                'name': 'Code Violation Case Status',
                'platform': 'arcgis',
                'resource_id': 'columbia-sc-code-violation-status',
                'arcgis_url': 'https://services1.arcgis.com/Mnt8FoJcogKtoVBs/arcgis/rest/services/CodeRental/FeatureServer/0',
                'date_field': 'OpenedDate',
                'id_field': 'CaseNum',
                'description_field': 'Problem',
                'status_field': 'CaseStatus',
                'type_field': 'Problem',
                'address_fields': {'full': 'ADDRESS'},
                'zip_field': None,
                'lat_field': None,
                'lng_field': None,
            },
        ],
    },
    # V267: Wheaton IL — ArcGIS FeatureServer. Small dataset (363
    # records) but fresh (max Created_Date 2026-04-21) and pre-
    # formatted Location field already includes city/state/zip.
    # Pairs with existing wheaton-il prod_city_id=22351.
    'wheaton-il': {
        'prod_city_id': None,
        'city': 'Wheaton',
        'state': 'IL',
        'endpoints': [
            {
                'name': 'Code Violations',
                'platform': 'arcgis',
                'resource_id': 'wheaton-il-code-violations',
                'arcgis_url': 'https://services2.arcgis.com/YyIQHvpylgCY7DEY/arcgis/rest/services/Code_Violations/FeatureServer/0',
                'date_field': 'Created_Date',
                'id_field': 'Reference_Number',
                'description_field': 'Primary_Violation',
                'status_field': 'Status',
                'type_field': 'Primary_Violation',
                'address_fields': {'full': 'Location'},
                'zip_field': None,
                'lat_field': None,
                'lng_field': None,
            },
        ],
    },
    # V268: Pasadena CA — ArcGIS FeatureServer. CodeComplianceCases
    # has 2,057 rows with CTP{YEAR}-XXXXX case numbers but no explicit
    # date field. Reuses Phoenix's date_from_id_pattern + incremental_
    # where approach to filter by year prefix. Schema unusual: carries
    # Assigned_Officer PHONE/EMAIL as case-level metadata (officer
    # contact, not violator). Pairs with pasadena prod_city_id=653.
    'pasadena-ca': {
        'prod_city_id': None,
        'city': 'Pasadena',
        'state': 'CA',
        'endpoints': [
            {
                'name': 'Pasadena Code Compliance Cases',
                'platform': 'arcgis',
                'resource_id': 'pasadena-ca-code-compliance',
                'arcgis_url': 'https://services2.arcgis.com/zNjnZafDYCAJAbN0/arcgis/rest/services/CodeComplianceCases/FeatureServer/0',
                'date_field': None,
                'date_from_id_pattern': r'CTPB?(\d{4})-\d+',
                'incremental_where': "CASENUMBER LIKE 'CTP{YEAR}%' OR CASENUMBER LIKE 'CTPB{YEAR}%'",
                'orderby': 'ObjectID ASC',
                'id_field': 'CASENUMBER',
                'description_field': 'DESCRIPTION',
                'status_field': 'Case_Status',
                'type_field': 'CodeType',
                'address_fields': {'full': 'Case_Address'},
                'zip_field': None,
                'lat_field': None,
                'lng_field': None,
            },
        ],
    },
    # V269: Syracuse NY — ArcGIS FeatureServer. Code_Violations_V2 at
    # services6.arcgis.com/bdPqSfflsdgFRVVM. 144,680 records, fresh
    # through 2026-04-23. Rich schema — complaint_address, complaint
    # _type_name (e.g. Vacant House), violation text, Neighborhood,
    # Vacant flag, lat/lon. No filter needed since the feed is
    # already scoped to code enforcement. Pairs with syracuse-ny
    # prod_city_id=21827.
    'syracuse-ny': {
        'prod_city_id': None,
        'city': 'Syracuse',
        'state': 'NY',
        'endpoints': [
            {
                'name': 'Code Violations',
                'platform': 'arcgis',
                'resource_id': 'syracuse-ny-code-violations',
                'arcgis_url': 'https://services6.arcgis.com/bdPqSfflsdgFRVVM/arcgis/rest/services/Code_Violations_V2/FeatureServer/0',
                'date_field': 'violation_date',
                'id_field': 'violation_number',
                'description_field': 'violation',
                'status_field': 'status_type_name',
                'type_field': 'complaint_type_name',
                'address_fields': {'full': 'complaint_address'},
                'zip_field': 'complaint_zip',
                'lat_field': 'Latitude',
                'lng_field': 'Longitude',
            },
        ],
    },
    # V198 PHASE 2 SKIPS (probed via DCAT/SSH, documented):
    #   - Houston TX: only publishes XLSX via CKAN (no JSON/CSV endpoint)
    #   - San Diego CA: seshat.datasd.org CSV returns 403, data.sandiego.gov not CKAN
    #   - Baltimore MD: egisdata Housing FS has no single "all violations" layer
    #       (only Vacant Building Notices and filtered subsets in dmxPermitsCodeEnforcement)
    #   - San Antonio TX: opendata-cosagis DCAT has 0 violation/enforcement datasets
    #   - Atlanta GA: dpcd-coaplangis DCAT has 0 violation/enforcement datasets
    # V202 TOP-10 ROUND (probed for market-readiness, none usable):
    #   - Houston TX: data.houstontx.gov CKAN dataset
    #       'city-of-houston-building-code-enforcement-violations-don' has only
    #       XLSX resources (5 files), last modified 2023-06-09 — stale + format
    #       violation_collector does not support. (re-confirmed from V198 skip.)
    #   - Phoenix AZ: phoenix-az.maps.arcgis.com org search returns no code
    #       enforcement FeatureServers under city owner; phoenixopendata.com CKAN
    #       has no code/violation/enforcement packages. Phoenix NSD publishes
    #       only dashboards, no underlying REST layer.
    #   - Dallas TX: dallasgis.maps.arcgis.com has only 2020 monthly snapshots
    #       ('Code Enforcement Cases <Month> 2020'), last modified 2021-04.
    #       No current/rolling dataset.
    # V206 S3 SKIPS (web-searched + SSH-probed, documented):
    #   - Las Vegas NV: Code_Enforcement_Open_Data FeatureServer EXISTS at
    #     services1.arcgis.com/F1v0ufATbBQScMtY/...FeatureServer/0 but
    #     max Event_Date is 2024-02-27 (stale, same pattern as Indianapolis
    #     + Greensboro). Deferred — needs either a current-year feed or
    #     an initial-window override to pick up the 2024-and-earlier backlog.
    #   - Detroit MI: data.detroitmi.gov portal responds but no code
    #     enforcement dataset published via DCAT; D3 portal is a different
    #     platform layer that would need its own adapter.
    #   - Sacramento CA: SACOG exposes Code Enforcement Violations only
    #     for West Sacramento, not the main City of Sacramento.
    # V200 PHASE 3 SKIPS (probed via DCAT/SSH, documented):
    #   - Pittsburgh PA: WPRDC CKAN has fresh daily-updated violations (CSV/GeoJSON,
    #       resource 70c06278-...), but violation_collector.py has no CKAN backend —
    #       deferred until CKAN support is added
    #   - Jacksonville FL: no public endpoint found
    #   - Tampa FL: city-tampa.opendata.arcgis.com DCAT: 0 violation datasets
    #   - Minneapolis MN: opendata.minneapolismn.gov DCAT: 0 violation datasets
    #       (violations shown only in Tableau dashboards, no REST export)
    #   - Oklahoma City OK: data.okc.gov DCAT returned empty JSON
    #   - Memphis TN, Louisville KY, Dallas TX (already V197-skipped: stale/empty)
    #   - Denver CO, Portland OR (V197 SSH DNS failures + 0 DCAT hits)
    # V197 PHASE 1 SKIPS (tested via SSH, documented to prevent re-investigation):
    #   - Nashville data.nashville.gov/479w-kw2x — 302 to hub.arcgis.com (migrated, new source added above)
    #   - Baltimore data.baltimorecity.gov/pugq-wdem — 302 to hub.arcgis.com; egisdata housing FS
    #       only has Vacant Building Notices (2010 data, not violations)
    #   - Fort Worth data.fortworthtexas.gov/spnu-bq4u — 302 to hub.arcgis.com (migrated, new source added)
    #   - Dallas www.dallasopendata.com/x9pz-kdq9 — STALE, last update 2018-07 / rowsUpdatedAt=2019
    #   - SF data.sfgov.org/nyek-jaw8 — 311 service requests, stale (2021), not building violations
    #   - LA data.lacity.org/2uz8-3tj3 — HTTP 404 dataset.missing
    #
    # V197 PHASE 2 SOCRATA MISSES (on-domain catalog search returned 0 violation hits):
    #   Denver, Portland, Tucson, Mesa, Baton Rouge, Honolulu, Louisville, Raleigh, Virginia Beach.
    # Kansas City had two hits: nhtf-e75a (Historical, 2009-2011, skipped) and vq3e-m9ge (kept).
}


# ---------------------------------------------------------------------------
# Collection logic
# ---------------------------------------------------------------------------

def _ensure_table():
    """Create violations table if it doesn't exist (V162 schema)."""
    conn = permitdb.get_connection()
    try:
        # Check if table has the right schema (prod_city_id column)
        test = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='violations'").fetchone()
        if test:
            schema = test[0] if isinstance(test, tuple) else test['sql']
            if 'prod_city_id' not in schema:
                print("[V162] Dropping old violations table (missing prod_city_id)")
                conn.execute("DROP TABLE IF EXISTS violations")
                conn.commit()

        conn.executescript("""
            CREATE TABLE IF NOT EXISTS violations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                prod_city_id INTEGER NOT NULL,
                city TEXT NOT NULL,
                state TEXT NOT NULL,
                source_violation_id TEXT UNIQUE,
                violation_date TEXT,
                violation_type TEXT,
                violation_description TEXT,
                status TEXT,
                address TEXT,
                zip TEXT,
                latitude REAL,
                longitude REAL,
                raw_data TEXT,
                collected_at TEXT DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_violations_prod_city_id ON violations(prod_city_id);
            CREATE INDEX IF NOT EXISTS idx_violations_date ON violations(violation_date);
            CREATE INDEX IF NOT EXISTS idx_violations_city_state ON violations(city, state);
        """)
        conn.commit()
    except Exception as e:
        print(f"[V162] Table creation note: {e}")


def _build_address(record, addr_config):
    """Build address string from configured fields."""
    if 'full' in addr_config:
        return str(record.get(addr_config['full'], '')).strip()
    parts = []
    for key in ('number', 'prefix', 'street', 'suffix'):
        if key in addr_config:
            val = record.get(addr_config[key])
            if val and str(val).strip():
                parts.append(str(val).strip())
    return ' '.join(parts)


def _parse_date(date_str):
    """Parse SODA / Carto / ArcGIS date formats to ISO YYYY-MM-DD."""
    if date_str is None:
        return None
    # V197: ArcGIS returns esriFieldTypeDate as epoch milliseconds (int/float).
    if isinstance(date_str, (int, float)):
        try:
            dt = datetime.fromtimestamp(date_str / 1000.0)
            if dt.year < 2000 or dt > datetime.now() + timedelta(days=7):
                return None
            return dt.strftime('%Y-%m-%d')
        except (ValueError, OSError, OverflowError):
            return None
    if not isinstance(date_str, str):
        return None
    s = date_str.strip()
    # Skip obviously bad dates
    if s.startswith('Y') or len(s) < 8:
        return None
    # V213: strip trailing 'Z' (UTC marker) that Carto / Socrata sometimes
    # emit. Python's strptime with %S can't match 'Z' directly — without
    # this step Philly's L&I Carto values ('2026-04-18T18:59:18Z') were
    # failing to parse and dropping every row during insert.
    if s.endswith('Z'):
        s = s[:-1]
    for fmt in ('%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d'):
        try:
            dt = datetime.strptime(s[:26], fmt)
            if dt.year < 2020 or dt > datetime.now() + timedelta(days=7):
                return None
            return dt.strftime('%Y-%m-%d')
        except ValueError:
            continue
    return None


def normalize_violation(record, city_config, endpoint):
    """Map source fields to normalized schema."""
    # V243: address_parse='phoenix_full' signals that the address field
    # is "STREET CITY ZIP" all in one string — parse it out and apply
    # the city_filter so non-Phoenix records (Scottsdale / Glendale /
    # Mesa) get dropped before insert.
    address = _build_address(record, endpoint['address_fields'])
    parsed_zip = ''
    parse_mode = endpoint.get('address_parse')
    if parse_mode == 'phoenix_full' and address:
        import re as _re
        # Greedy street + single-word city + trailing ZIP. Phoenix's
        # table packs everything into one field as
        # "STREET STATE_CITY ZIP" — the city is always one token so
        # `[A-Z]+` (no whitespace) is a tighter match than a lazy run.
        m = _re.search(
            r'^(.+?)\s+([A-Z][A-Z]+)\s+(\d{5}(?:-\d{4})?)$',
            address.strip(),
        )
        # Try greedy-street first (single-word city) so "41ST AVE"
        # stays with the street instead of getting eaten as the city.
        m2 = _re.search(
            r'^(.+)\s+([A-Z][A-Z]+)\s+(\d{5}(?:-\d{4})?)$',
            address.strip(),
        )
        m = m2 or m
        if m:
            street, city_token, zip_code = m.group(1), m.group(2).strip(), m.group(3)
            parsed_zip = zip_code
            required_city = (endpoint.get('address_city_filter') or '').upper()
            if required_city and city_token.upper() != required_city:
                # Signal the caller to drop this record.
                return {
                    'prod_city_id': city_config['prod_city_id'],
                    'city': city_config['city'],
                    'state': city_config['state'],
                    'source_violation_id': None,
                    'violation_date': None,
                    'violation_type': '', 'violation_description': '',
                    'status': '', 'address': '', 'zip': '',
                    'latitude': None, 'longitude': None, 'raw_data': '',
                }
            address = street
    vid = record.get(endpoint['id_field'], '')
    # V213: Carto endpoints don't set 'resource_id' (they use 'carto_table'
    # + 'carto_base'). Fall back through a sane prefix ladder so the
    # source_violation_id stays unique across sources and Philly's L&I
    # Carto feed stops raising KeyError here and getting silently dropped.
    id_prefix = (
        endpoint.get('resource_id')
        or endpoint.get('carto_table')
        or endpoint.get('arcgis_url', '').rsplit('/', 2)[-2]
        or 'violations'
    )
    source_id = f"{id_prefix}_{vid}" if vid else None

    # V243: Phoenix NSD doesn't expose a date column. Extract the year
    # from the case-number prefix (PEF2026-13612) and anchor to Jan 1
    # of that year so the row isn't dropped as dateless.
    date_val = None
    date_pattern = endpoint.get('date_from_id_pattern')
    if date_pattern:
        import re as _re
        if vid:
            m = _re.match(date_pattern, str(vid))
            if m:
                try:
                    year = int(m.group(1))
                    date_val = f"{year:04d}-01-01"
                except (ValueError, IndexError):
                    pass
    elif endpoint.get('date_field'):
        date_val = _parse_date(record.get(endpoint['date_field']))

    vtype = endpoint.get('fixed_violation_type') or str(
        record.get(endpoint.get('type_field', ''), '') or '')[:200]
    zip_val = ''
    if endpoint.get('zip_field'):
        zip_val = str(record.get(endpoint['zip_field'], '') or '')
    elif parsed_zip:
        zip_val = parsed_zip

    return {
        'prod_city_id': city_config['prod_city_id'],
        'city': city_config['city'],
        'state': city_config['state'],
        'source_violation_id': source_id,
        'violation_date': date_val,
        'violation_type': vtype,
        'violation_description': str(record.get(endpoint.get('description_field', ''), '') or '')[:500],
        'status': str(record.get(endpoint.get('status_field', ''), '') or '')[:100],
        'address': address,
        'zip': zip_val,
        'latitude': record.get(endpoint['lat_field']) if endpoint.get('lat_field') else None,
        'longitude': record.get(endpoint['lng_field']) if endpoint.get('lng_field') else None,
        'raw_data': json.dumps(record, default=str),
    }


def collect_violations_from_endpoint(city_config, endpoint):
    """Fetch violations from a SODA, Carto, or ArcGIS endpoint."""
    is_carto = 'carto_base' in endpoint
    is_arcgis = endpoint.get('platform') == 'arcgis'
    if is_carto:
        base_url = endpoint['carto_base']
    elif is_arcgis:
        base_url = endpoint['arcgis_url'].rstrip('/') + '/query'
    else:
        base_url = f"https://{endpoint['domain']}/resource/{endpoint['resource_id']}.json"
    date_field = endpoint.get('date_field')
    prod_city_id = city_config['prod_city_id']
    # V243: ArcGIS MapServer TABLE endpoints (non-spatial, no geometry)
    # require returnGeometry=false on every query or the server responds
    # 400 "Failed to execute query.". Phoenix NSD Property Maintenance
    # is the first such source we support.
    is_mapserver_table = bool(endpoint.get('mapserver_table'))
    # V243: some sources have no date column at all (Phoenix's case
    # number embeds the year). In that case we use a hardcoded
    # incremental_where template instead of the date filter logic, and
    # sort on a stable non-date column.
    incremental_where = endpoint.get('incremental_where')

    # V170: Dynamic prod_city_id lookup for cities with None
    if prod_city_id is None:
        try:
            conn_tmp = permitdb.get_connection()
            row = conn_tmp.execute(
                "SELECT id FROM prod_cities WHERE city = ? AND state = ?",
                (city_config['city'], city_config['state'])
            ).fetchone()
            if row:
                prod_city_id = row['id'] if isinstance(row, dict) else row[0]
                city_config['prod_city_id'] = prod_city_id
        except Exception:
            pass
    if not prod_city_id:
        print(f"  [V170] {city_config['city']}: No prod_city_id found, skipping")
        return 0

    # Get last collected date for incremental collection
    conn = permitdb.get_connection()
    try:
        row = conn.execute(
            "SELECT MAX(violation_date) as last_date FROM violations WHERE prod_city_id = ?",
            (prod_city_id,)
        ).fetchone()
        last_date = (row['last_date'] if isinstance(row, dict) else row[0]) if row else None
    except Exception:
        last_date = None

    if not last_date:
        # V198: Widen first-time window to 365 days so newly-added cities
        # with slower-updating feeds (e.g. KC vq3e-m9ge, 2025-07 max) still
        # backfill something meaningful on the first collection pass.
        last_date = (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%dT00:00:00')

    print(f"  [V162] {city_config['city']} / {endpoint['name']}: fetching since {last_date[:10]}")

    offset = 0
    batch_size = 1000
    total_inserted = 0
    total_api_rows = 0  # V215: rows returned by upstream API (any page)
    total_dupes = 0     # V215: rows that hit INSERT OR IGNORE and no-op'd
    first_page_params = None  # V215: captured for diagnostic log
    max_records = 5000  # V166: Reduced from 50K to 5K per run to limit memory

    # V197/V198: ArcGIS needs `timestamp 'YYYY-MM-DD HH:MM:SS'` format for
    # esriFieldTypeDate where clauses. Raw epoch ms is rejected by hosted
    # FeatureServer/MapServer with "Invalid query parameters".
    arcgis_ts_literal = None
    if is_arcgis and date_field:
        try:
            last_dt = datetime.strptime(last_date[:10], '%Y-%m-%d')
        except ValueError:
            last_dt = datetime.now() - timedelta(days=180)
        arcgis_ts_literal = last_dt.strftime('%Y-%m-%d %H:%M:%S')

    while total_inserted < max_records:
        # V170: Build request based on platform (SODA vs Carto vs ArcGIS)
        if is_carto:
            table = endpoint['carto_table']
            sql = (f"SELECT * FROM {table} WHERE {date_field} > '{last_date}' "
                   f"ORDER BY {date_field} DESC LIMIT {batch_size} OFFSET {offset}")
            params = {'q': sql, 'format': 'json'}
        elif is_arcgis:
            # V243: dateless MapServer tables use an incremental_where
            # template (Phoenix filters by the year prefix in the case
            # number) and sort on a stable column the config declares.
            if incremental_where:
                year = datetime.now().year
                where_clause = incremental_where.replace('{YEAR}', str(year))
                order_by = endpoint.get('orderby', 'ESRI_OID ASC')
            else:
                where_clause = f"{date_field} >= timestamp '{arcgis_ts_literal}'"
                order_by = f"{date_field} DESC"
            # V259: base_where pre-filters shared 311/all-cases feeds
            # (Denver ODC 311 Agency=CPD, SA 311 Category=Property
            # Maintenance) down to the code-enforcement subset before
            # the date/incremental filter is AND-ed on top.
            base_where = endpoint.get('base_where')
            if base_where:
                where_clause = f"({base_where}) AND ({where_clause})"
            params = {
                'where': where_clause,
                'outFields': '*',
                'orderByFields': order_by,
                'resultOffset': offset,
                'resultRecordCount': batch_size,
                'f': 'json',
            }
            if is_mapserver_table:
                # Non-spatial tables return 400 unless geometry is
                # explicitly suppressed.
                params['returnGeometry'] = 'false'
        else:
            # V262: socrata_extra_where pre-filters shared 311/all-cases
            # feeds (Little Rock issue_type/sub_category) down to the
            # code-enforcement subset. AND-combined with the incremental
            # date clause so bulk nuisance tickets stay out of the
            # digest.
            where_clause = f"{date_field} > '{last_date}'"
            extra = endpoint.get('socrata_extra_where')
            if extra:
                where_clause = f"({extra}) AND ({where_clause})"
            params = {
                '$limit': batch_size,
                '$offset': offset,
                '$order': f"{date_field} DESC",
                '$where': where_clause,
            }

        if first_page_params is None:
            # V215: capture the first page's params — that's what a human
            # running the query themselves would need to reproduce it.
            try:
                first_page_params = json.dumps(params, default=str)[:1000]
            except Exception:
                first_page_params = None

        try:
            resp = SESSION.get(base_url, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            # V170: Carto wraps in 'rows', SODA returns array, ArcGIS wraps in 'features'.
            if is_arcgis:
                if isinstance(data, dict) and 'error' in data:
                    err = data['error']
                    print(f"  [V197] ArcGIS error {err.get('code')}: {err.get('message')}")
                    resp.close()
                    break
                feats = data.get('features', []) if isinstance(data, dict) else []
                records = [f.get('attributes', {}) for f in feats]
            elif is_carto:
                records = data.get('rows', []) if isinstance(data, dict) else []
            else:
                records = data
            resp.close()
        except Exception as e:
            print(f"  [V170] Error fetching page at offset {offset}: {e}")
            break

        if not records or not isinstance(records, list):
            break
        total_api_rows += len(records)

        # V166: Stream-process — normalize and insert immediately per page, don't accumulate
        batch = []
        for record in records:
            try:
                norm = normalize_violation(record, city_config, endpoint)
                if norm['source_violation_id'] and norm['violation_date']:
                    batch.append(norm)
            except Exception:
                continue

        if batch:
            inserted, dupes = _insert_batch(batch)
            total_inserted += inserted
            total_dupes += dupes
        del batch, records  # V166: Free memory immediately

        if total_inserted >= max_records:
            break

        offset += batch_size
        time.sleep(1)

    print(f"  [V162] {city_config['city']} / {endpoint['name']}: "
          f"{total_inserted} inserted, {total_dupes} dupes skipped, "
          f"{total_api_rows} api rows")
    return {
        'inserted': total_inserted,
        'api_rows_returned': total_api_rows,
        'duplicate_rows_skipped': total_dupes,
        'api_url': base_url,
        'query_params': first_page_params,
    }


def _insert_batch(violations):
    """Insert a batch of violations, skip duplicates.

    V215: returns (inserted, duplicates_skipped). Previously returned just
    a count that double-counted dupes because INSERT OR IGNORE does not
    raise on conflict — rowcount is the source of truth.
    """
    conn = permitdb.get_connection()
    inserted = 0
    dupes = 0
    for v in violations:
        try:
            cur = conn.execute("""
                INSERT OR IGNORE INTO violations
                (prod_city_id, city, state, source_violation_id, violation_date,
                 violation_type, violation_description, status, address, zip,
                 latitude, longitude, raw_data)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                v['prod_city_id'], v['city'], v['state'], v['source_violation_id'],
                v['violation_date'], v['violation_type'], v['violation_description'],
                v['status'], v['address'], v['zip'],
                v['latitude'], v['longitude'], v['raw_data'],
            ))
            if cur.rowcount and cur.rowcount > 0:
                inserted += 1
            else:
                dupes += 1
        except Exception:
            pass
    conn.commit()
    return inserted, dupes


def collect_violations():
    """Collect violations for all configured cities."""
    _ensure_table()

    print(f"\n{'='*60}")
    print(f"V162: Violation Collection — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*60}")

    # V215: results[slug] is a dict with per-city aggregated diagnostics
    # (inserted, api_rows_returned, duplicate_rows_skipped, api_url,
    # query_params) so the daemon can decide between success /
    # caught_up / no_api_data and write them to collection_log.
    # V220 T2: skip sources known to be permanently stale. These endpoints
    # respond (not dead) but have stopped updating upstream — every poll
    # cycle wastes an HTTP round trip and fills collection_log with
    # no_api_data noise for nothing. Keep the entries in VIOLATION_SOURCES
    # so historical data stays queryable; just skip polling.
    PAUSED_VIOLATION_SOURCES = {
        'greensboro-nc',     # max IssuedDate 2024-06-18
        'indianapolis-in',   # max OPEN_DATE 2024-02-27
        'kansas-city-mo',    # max date_found 2025-07-04
    }

    results = {}
    for slug, config in VIOLATION_SOURCES.items():
        if slug in PAUSED_VIOLATION_SOURCES:
            print(f"  [V220] skipping {slug} — permanently stale upstream")
            continue
        agg = {
            'inserted': 0,
            'api_rows_returned': 0,
            'duplicate_rows_skipped': 0,
            'api_url': None,
            'query_params': None,
            'error': None,
        }
        for endpoint in config['endpoints']:
            try:
                out = collect_violations_from_endpoint(config, endpoint)
                # Back-compat: older callers may still return an int
                if isinstance(out, dict):
                    agg['inserted'] += out.get('inserted', 0) or 0
                    agg['api_rows_returned'] += out.get('api_rows_returned', 0) or 0
                    agg['duplicate_rows_skipped'] += out.get('duplicate_rows_skipped', 0) or 0
                    if not agg['api_url']:
                        agg['api_url'] = out.get('api_url')
                    if not agg['query_params']:
                        agg['query_params'] = out.get('query_params')
                else:
                    agg['inserted'] += int(out or 0)
            except Exception as e:
                print(f"  [V162] Error on {config['city']}/{endpoint['name']}: {e}")
                agg['error'] = str(e)[:500]
        results[slug] = agg

    print(f"\n{'='*60}")
    print(f"VIOLATION COLLECTION COMPLETE")
    for slug, agg in results.items():
        print(f"  {VIOLATION_SOURCES[slug]['city']}: "
              f"{agg['inserted']:,} new violations "
              f"(api_rows={agg['api_rows_returned']}, "
              f"dupes={agg['duplicate_rows_skipped']})")
    print(f"  Total: {sum(a['inserted'] for a in results.values()):,}")
    print(f"{'='*60}\n")

    # V182 PR2: refresh emblem flags so cities that just gained violations
    # get their has_violations flag updated before the UI queries them.
    try:
        from contractor_profiles import update_city_emblems
        stats = update_city_emblems()
        print(f"[V182] Emblem refresh after violations: "
              f"{stats['cities_with_violations']} cities with violations, "
              f"{stats['cities_with_enrichment']} with enrichment")
    except Exception as e:
        print(f"[V182] Emblem refresh failed (non-fatal): {e}")

    return results
