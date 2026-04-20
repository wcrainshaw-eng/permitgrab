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
        'prod_city_id': 1,
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
        'prod_city_id': 3,
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
        'prod_city_id': 2,
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
    address = _build_address(record, endpoint['address_fields'])
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

    return {
        'prod_city_id': city_config['prod_city_id'],
        'city': city_config['city'],
        'state': city_config['state'],
        'source_violation_id': source_id,
        'violation_date': _parse_date(record.get(endpoint['date_field'])),
        'violation_type': str(record.get(endpoint.get('type_field', ''), '') or '')[:200],
        'violation_description': str(record.get(endpoint.get('description_field', ''), '') or '')[:500],
        'status': str(record.get(endpoint.get('status_field', ''), '') or '')[:100],
        'address': address,
        'zip': str(record.get(endpoint['zip_field'], '') or '') if endpoint.get('zip_field') else '',
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
    date_field = endpoint['date_field']
    prod_city_id = city_config['prod_city_id']

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
    max_records = 5000  # V166: Reduced from 50K to 5K per run to limit memory

    # V197/V198: ArcGIS needs `timestamp 'YYYY-MM-DD HH:MM:SS'` format for
    # esriFieldTypeDate where clauses. Raw epoch ms is rejected by hosted
    # FeatureServer/MapServer with "Invalid query parameters".
    arcgis_ts_literal = None
    if is_arcgis:
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
            params = {
                'where': f"{date_field} >= timestamp '{arcgis_ts_literal}'",
                'outFields': '*',
                'orderByFields': f"{date_field} DESC",
                'resultOffset': offset,
                'resultRecordCount': batch_size,
                'f': 'json',
            }
        else:
            params = {
                '$limit': batch_size,
                '$offset': offset,
                '$order': f"{date_field} DESC",
                '$where': f"{date_field} > '{last_date}'",
            }

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
            inserted = _insert_batch(batch)
            total_inserted += inserted
        del batch, records  # V166: Free memory immediately

        if total_inserted >= max_records:
            break

        offset += batch_size
        time.sleep(1)

    print(f"  [V162] {city_config['city']} / {endpoint['name']}: {total_inserted} violations inserted")
    return total_inserted


def _insert_batch(violations):
    """Insert a batch of violations, skip duplicates."""
    conn = permitdb.get_connection()
    inserted = 0
    for v in violations:
        try:
            conn.execute("""
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
            inserted += 1
        except Exception:
            pass
    conn.commit()
    return inserted


def collect_violations():
    """Collect violations for all configured cities."""
    _ensure_table()

    print(f"\n{'='*60}")
    print(f"V162: Violation Collection — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*60}")

    results = {}
    for slug, config in VIOLATION_SOURCES.items():
        city_total = 0
        for endpoint in config['endpoints']:
            try:
                count = collect_violations_from_endpoint(config, endpoint)
                city_total += count
            except Exception as e:
                print(f"  [V162] Error on {config['city']}/{endpoint['name']}: {e}")
        results[slug] = city_total

    print(f"\n{'='*60}")
    print(f"VIOLATION COLLECTION COMPLETE")
    for slug, count in results.items():
        print(f"  {VIOLATION_SOURCES[slug]['city']}: {count:,} new violations")
    print(f"  Total: {sum(results.values()):,}")
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
