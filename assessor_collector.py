"""
V279: County assessor property owner collection pipeline.

Task doc reference: CODE_V276_PROPERTY_OWNERS Phase 2.

Populates the property_owners table with owner + mailing address rows
pulled from county assessor open data portals. Runs as a separate
admin-triggered job (NOT part of the 30-minute permit daemon) because
assessor data changes slowly (annual reassessments). One-time
backfill per county, then monthly refresh.

Memory constraint (Render 512MB): paginates in pages of 1000 records
for ArcGIS, inserting+committing each page before fetching the next.
Never loads a full dataset into memory.

Collector pattern per source:
1. Query source API for a page of records (1000 max per call)
2. Normalize field names to property_owners schema
3. INSERT OR IGNORE into property_owners (dedup via unique index
   on address, owner_name, source)
4. Commit batch
5. Advance resultOffset, repeat until fewer records returned than
   page size (= end of dataset reached)

Sources (V279 ships Maricopa; other counties added in V280-V283):
  - maricopa (Phoenix + Mesa + Scottsdale + Chandler + Glendale
    + Tempe metro area — single fetch covers ~1.76M parcels
    across all 6 cities).
"""

import json
import time
import requests

import db as permitdb

SESSION = requests.Session()
SESSION.headers.update({'Accept': 'application/json'})

# Per the task-doc spec, returnGeometry=False is mandatory for
# MapServer endpoints and saves huge bytes on the wire. Page size
# = the server's maxRecordCount (1000 for Maricopa).
DEFAULT_PAGE_SIZE = 1000

ASSESSOR_SOURCES = {
    'maricopa': {
        # Maricopa County (Phoenix metro). 1.76M parcels. Discovered
        # MAIL_ADDRESS / MAIL_ADDR1-2 / MAIL_CITY / MAIL_STATE /
        # MAIL_ZIP are public on MapServer/0 — task doc claimed these
        # needed an API key; live schema probe says otherwise.
        #
        # V432 (CODE_V428 Phase 1c): tightened where_clause from
        # `OWNER_NAME IS NOT NULL` to also exclude empty PHYSICAL_ADDRESS.
        # Probed 2026-04-27: PHYSICAL_ADDRESS is the literal empty string
        # `""` (NOT NULL) for ~80% of rows in OBJECTID order — utility
        # parcels (EL PASO NATURAL GAS) and city-owned land (AVONDALE
        # CITY OF) where the assessor only has a mailing address.
        # _insert_batch's `if not addr` guard then silently dropped
        # those rows, so a 1000-row fetch yielded ~13 inserts and the
        # admin call appeared "stuck at 13 records." Adding the
        # `PHYSICAL_ADDRESS <> ''` filter pushes that filter into the
        # ArcGIS query so each page yields ~80–95% inserts (proportional
        # to true unique addresses left after dedup).
        'platform': 'arcgis_mapserver',
        'service_description': 'Maricopa County Assessor Parcels',
        'endpoint': 'https://gis.mcassessor.maricopa.gov/arcgis/rest/services/Parcels/MapServer/0',
        'where_clause': "OWNER_NAME IS NOT NULL AND PHYSICAL_ADDRESS IS NOT NULL AND PHYSICAL_ADDRESS <> ''",
        'field_map': {
            'owner_name': 'OWNER_NAME',
            'address': 'PHYSICAL_ADDRESS',
            'city': 'PHYSICAL_CITY',
            'zip': 'PHYSICAL_ZIP',
            'owner_mailing_address': 'MAIL_ADDRESS',
            'parcel_id': 'APN',
        },
        'state': 'AZ',
        'source_tag': 'assessor:maricopa',
    },
    'bexar': {
        # Bexar County (San Antonio). 710K parcels. Schema richer than
        # Maricopa — has assessed value (TotVal), year built, and
        # split mailing (AddrLn1/AddrCity/AddrSt/Zip). Note sample
        # data has literal "NULL" strings in some fields (YrBlt,
        # AddrLn1) which the normalizer treats as null.
        'platform': 'arcgis_mapserver',
        'service_description': 'Bexar County Appraisal District Parcels',
        'endpoint': 'https://maps.bexar.org/arcgis/rest/services/Parcels/MapServer/0',
        'where_clause': 'Owner IS NOT NULL',
        'field_map': {
            'owner_name': 'Owner',
            'address': 'Situs',
            'city': 'AddrCity',
            'zip': 'Zip',
            'parcel_id': 'PropID',
            # Mailing rolled up from several columns; assessor_collector
            # doesn't currently concatenate — store the first line and
            # add a second pass later if needed.
            'owner_mailing_address': 'AddrLn1',
        },
        'state': 'TX',
        'source_tag': 'assessor:bexar',
    },
    'dc_vacant_blighted': {
        # Washington DC — Integrated Tax System Vacant/Blighted Building
        # extract at dcgis.dc.gov layer 80. Not a full parcel file but a
        # filtered subset: 2,332 officially-flagged vacant or blighted
        # properties, every row a concentrated distressed-property
        # lead. Fields include owner name + split mailing address
        # (ADDRESS1 / CITYSTZIP) + property type + neighborhood code.
        # DC DOB open data doesn't publish code enforcement cases
        # (V274 session finding) so this also doubles as DC's only
        # violations-style signal until DOB migrates.
        'platform': 'arcgis_mapserver',
        'service_description': 'DC Vacant/Blighted Buildings (ITSPE)',
        'endpoint': 'https://maps2.dcgis.dc.gov/dcgis/rest/services/DCGIS_DATA/Property_and_Land_WebMercator/MapServer/80',
        'where_clause': 'OWNERNAME IS NOT NULL',
        'field_map': {
            'owner_name': 'OWNERNAME',
            'address': 'PREMISEADD',
            'owner_mailing_address': 'ADDRESS1',
            'parcel_id': 'SSL',
            # DC addresses bake city/state/zip into PREMISEADD
            # ('835 KENNEDY ST NE WASHINGTON DC 20011') so no
            # separate city/zip mapping — downstream address
            # normalization handles it.
        },
        'state': 'DC',
        'source_tag': 'assessor:dc_vacant_blighted',
    },
    'cook_chicago': {
        # V429 (CODE_V428 Phase 1a): Cook County (Chicago + suburbs)
        # Assessor Parcel Addresses on Socrata. Probed 2026-04-27:
        # 3723-97qp returns owner_address_name + mail_address_full +
        # prop_address_full per row. ~1.86M parcels per year × 9 years
        # of history (2017-2025) = ~16.8M total rows in the dataset.
        # Filter to year=2025 (latest fully-published roll) so we pull
        # one snapshot, not history. V432: explicit year filter prevents
        # 9× over-fetching.
        'platform': 'soda',
        'service_description': 'Cook County Assessor — Parcel Addresses',
        'endpoint': 'https://datacatalog.cookcountyil.gov/resource/3723-97qp.json',
        # Some rows have owner_address_name='' (LLC properties only
        # populate the entity name elsewhere). Skip those at insert
        # time via _insert_batch's len(owner) >= 2 guard.
        'where_clause': "year = '2025' AND owner_address_name IS NOT NULL AND prop_address_full IS NOT NULL",
        'field_map': {
            'owner_name': 'owner_address_name',
            'address': 'prop_address_full',
            'city': 'prop_address_city_name',
            'zip': 'prop_address_zipcode_1',
            'owner_mailing_address': 'mail_address_full',
            'parcel_id': 'pin',
        },
        'state': 'IL',
        'source_tag': 'assessor:cook_chicago',
        'default_page_size': 5000,
    },
    'miami_dade': {
        # V430 (CODE_V428 Phase 1b): Miami-Dade Property Appraiser parcels.
        # Found via web research 2026-04-27 — the directive's MapServer
        # URL (gisfs.miamidade.gov MD_PA_PropertySearch) is a stub
        # service. The real PA data lives on Miami-Dade's AGOL org at
        # services.arcgis.com/8Pc9XBTAsYuxx9Ny/PaParcelView_gdb.
        # Schema (41 fields): TRUE_OWNER1/2/3, TRUE_SITE_ADDR/CITY/ZIP,
        # TRUE_MAILING_ADDR1/CITY/STATE/ZIP, FOLIO, BUILDING_HEATED_AREA,
        # YEAR_BUILT, BEDROOM_COUNT. Same AGOL org hosts the violations
        # CCVIOL_gdb (CLAUDE.md). Covers all of Miami-Dade County
        # including Hialeah → wires both cities at once.
        'platform': 'arcgis_mapserver',
        'service_description': 'Miami-Dade County Property Appraiser PaParcelView',
        'endpoint': 'https://services.arcgis.com/8Pc9XBTAsYuxx9Ny/arcgis/rest/services/PaParcelView_gdb/FeatureServer/0',
        'where_clause': 'TRUE_OWNER1 IS NOT NULL',
        'field_map': {
            'owner_name': 'TRUE_OWNER1',
            'address': 'TRUE_SITE_ADDR',
            'city': 'TRUE_SITE_CITY',
            'zip': 'TRUE_SITE_ZIP_CODE',
            'owner_mailing_address': 'TRUE_MAILING_ADDR1',
            'parcel_id': 'FOLIO',
        },
        'state': 'FL',
        'source_tag': 'assessor:miami_dade',
    },
    'davidson_nashville': {
        # V430 (CODE_V428 Phase 1d): Davidson County (Nashville) parcels.
        # Found via web research 2026-04-27. The directive's URL
        # (maps.nashville.gov Cadastral/Parcels_SP) is a State-Plane
        # MapServer with empty fields list — the actual hosted feature
        # service lives on AGOL at services2.arcgis.com/HdTo6HJqh92wn4D8.
        # Schema (43 fields): Owner, OwnAddr1/2/3, OwnCity, OwnState,
        # OwnZip, PropAddr, PropCity, PropZip, ParID, Acres, TotlAppr.
        # Owner is the official Davidson County PA "Real Property"
        # field; matches what padctn.org's search exposes.
        'platform': 'arcgis_mapserver',
        'service_description': 'Davidson County (Nashville) Parcels View',
        'endpoint': 'https://services2.arcgis.com/HdTo6HJqh92wn4D8/arcgis/rest/services/Parcels_view/FeatureServer/0',
        'where_clause': 'Owner IS NOT NULL',
        'field_map': {
            'owner_name': 'Owner',
            'address': 'PropAddr',
            'city': 'PropCity',
            'zip': 'PropZip',
            'owner_mailing_address': 'OwnAddr1',
            'parcel_id': 'ParID',
        },
        'state': 'TN',
        'source_tag': 'assessor:davidson_nashville',
    },
    'onondaga_syracuse': {
        # V433g: Onondaga County NY (Syracuse). Same NYS statewide
        # endpoint as Erie/Buffalo (V433f); only the COUNTY_NAME filter
        # changes. Probed 2026-04-27: 181,884 parcels with PRIMARY_OWNER.
        # Schema identical to erie_buffalo so the field_map mirrors it.
        'platform': 'arcgis_mapserver',
        'service_description': 'NYS Tax Parcels Public — Onondaga County (Syracuse)',
        'endpoint': 'https://gisservices.its.ny.gov/arcgis/rest/services/NYS_Tax_Parcels_Public/MapServer/1',
        'where_clause': "COUNTY_NAME = 'Onondaga' AND PRIMARY_OWNER IS NOT NULL AND LOC_STREET IS NOT NULL",
        'field_map': {
            'owner_name': 'PRIMARY_OWNER',
            'address': ['LOC_ST_NBR', 'LOC_STREET'],
            'city': 'MUNI_NAME',
            'zip': 'LOC_ZIP',
            'owner_mailing_address': 'MAIL_ADDR',
            'parcel_id': 'SWIS_SBL_ID',
        },
        'state': 'NY',
        'source_tag': 'assessor:onondaga_syracuse',
    },
    'erie_buffalo': {
        # V433f: Erie County NY (Buffalo metro). Probed 2026-04-27:
        # NYS Office of Information Technology Services hosts a statewide
        # tax-parcel layer at gisservices.its.ny.gov for the 38 of 62
        # NYS counties that gave permission to share publicly. Erie is
        # included with 370,424 parcels.
        # Schema (75 fields) includes PRIMARY_OWNER, LOC_ST_NBR,
        # LOC_STREET, LOC_ZIP, MUNI_NAME, MAIL_ADDR, MAIL_CITY,
        # MAIL_STATE, MAIL_ZIP, SWIS_SBL_ID, PROP_CLASS.
        # Filter to COUNTY_NAME='Erie' to scope just Buffalo + Erie metro.
        # Same NYS endpoint can later be wired for Rochester (Monroe),
        # Syracuse (Onondaga), Albany — separate source entries per
        # county keeps dedup + tagging clean.
        'platform': 'arcgis_mapserver',
        'service_description': 'NYS Tax Parcels Public — Erie County (Buffalo)',
        'endpoint': 'https://gisservices.its.ny.gov/arcgis/rest/services/NYS_Tax_Parcels_Public/MapServer/1',
        'where_clause': "COUNTY_NAME = 'Erie' AND PRIMARY_OWNER IS NOT NULL AND LOC_STREET IS NOT NULL",
        'field_map': {
            'owner_name': 'PRIMARY_OWNER',
            # Concat LOC_ST_NBR + LOC_STREET ("123 Main St"). LOC_ST_NBR
            # null on vacant rural land — V433b's _resolve filters out
            # the empty string at concat time.
            'address': ['LOC_ST_NBR', 'LOC_STREET'],
            'city': 'MUNI_NAME',
            'zip': 'LOC_ZIP',
            'owner_mailing_address': 'MAIL_ADDR',
            'parcel_id': 'SWIS_SBL_ID',
        },
        'state': 'NY',
        'source_tag': 'assessor:erie_buffalo',
    },
    'travis_austin': {
        # V433e: Travis Central Appraisal District (Austin metro).
        # Probed 2026-04-27: TCAD_Parcels_Dec_2025 on AGOL services1
        # org HGcSYZ5bvjRswoCb. 42 fields including py_owner_name,
        # situs_address (pre-concatenated street+city+zip), situs_num,
        # situs_street, situs_zip, py_address (owner mailing), PROP_ID,
        # market_value, appraised_val, assessed_val. Bonus over the
        # V428 list — Austin has permits + violations already wired so
        # this closes the owner gap and pushes Austin toward 5/5.
        'platform': 'arcgis_mapserver',
        'service_description': 'Travis Central Appraisal District Parcels (Dec 2025)',
        'endpoint': 'https://services1.arcgis.com/HGcSYZ5bvjRswoCb/arcgis/rest/services/TCAD_Parcels_Dec_2025/FeatureServer/0',
        'where_clause': "py_owner_name IS NOT NULL AND situs_address IS NOT NULL AND situs_address <> ''",
        'field_map': {
            'owner_name': 'py_owner_name',
            'address': 'situs_address',
            'zip': 'situs_zip',
            'owner_mailing_address': 'py_address',
            'parcel_id': 'PROP_ID',
        },
        'state': 'TX',
        'source_tag': 'assessor:travis_austin',
    },
    'clark_lasvegas': {
        # V433c (CODE_V428 follow-on): Clark County NV (Las Vegas +
        # Henderson + N Las Vegas + Boulder City + unincorporated CC).
        # Probed 2026-04-27: maps.clarkcountynv.gov/.../Accela/AccelaPoints/
        # MapServer/0 = "Accela_XAPO_Parcel_OwnerEvents". 29-field schema
        # includes ownerFullName, address, city, state, zip, mailAddress1,
        # mailCity, parcelNumber, improvedValue, landValue. The "XAPO"
        # name is Accela's "External API Output" — parcels keyed for
        # permit/violation linkage. Earlier (V313 / today's V433b)
        # probes of the Assessor folder + clarkcountygis Hub returned
        # no owner data; the Accela XAPO layer is where it actually
        # lives. Filter to primaryParcelFlag='Y' to get one row per
        # parcel (the layer holds multiple owner events per parcel).
        'platform': 'arcgis_mapserver',
        'service_description': 'Clark County NV Accela XAPO Parcel Owner Events',
        'endpoint': 'https://maps.clarkcountynv.gov/arcgis/rest/services/Accela/AccelaPoints/MapServer/0',
        'where_clause': "ownerFullName IS NOT NULL AND address IS NOT NULL AND primaryParcelFlag = 'Y'",
        'field_map': {
            'owner_name': 'ownerFullName',
            'address': 'address',
            'city': 'city',
            'zip': 'zip',
            'owner_mailing_address': 'mailAddress1',
            'parcel_id': 'parcelNumber',
        },
        'state': 'NV',
        'source_tag': 'assessor:clark_lasvegas',
    },
    'hennepin_minneapolis': {
        # V433b (CODE_V428 follow-on): Hennepin County (Minneapolis +
        # Bloomington + Edina). Probed 2026-04-27: HennepinData/
        # LAND_PROPERTY/MapServer/1 has 122 fields including OWNER_NM,
        # TAXPAYER_NM, HOUSE_NO, STREET_NM, ZIP_CD, PID_TEXT,
        # MAILING_MUNIC_NM. 443,560 parcels with owner names. Address
        # is split across HOUSE_NO + STREET_NM — leverages V433b's
        # list-concat support in field_map.
        'platform': 'arcgis_mapserver',
        'service_description': 'Hennepin County (Minneapolis) Parcels',
        'endpoint': 'https://gis.hennepin.us/arcgis/rest/services/HennepinData/LAND_PROPERTY/MapServer/1',
        'where_clause': "OWNER_NM IS NOT NULL AND STREET_NM IS NOT NULL",
        'field_map': {
            'owner_name': 'OWNER_NM',
            # Concat HOUSE_NO + STREET_NM into one address string.
            'address': ['HOUSE_NO', 'STREET_NM'],
            'city': 'MUNIC_NM',
            'zip': 'ZIP_CD',
            'owner_mailing_address': 'TAXPAYER_NM',
            'parcel_id': 'PID_TEXT',
        },
        'state': 'MN',
        'source_tag': 'assessor:hennepin_minneapolis',
    },
    'philadelphia_opa': {
        # V433 (CODE_V428 Phase 1g): Philadelphia Office of Property
        # Assessment via Carto SQL. Probed 2026-04-27: 583,562 parcels
        # with owner_1 + location populated. Schema (82 fields) includes
        # owner_1, owner_2, mailing_street, mailing_zip, mailing_city_state,
        # location (site address), parcel_number. Same hosting platform
        # as Philadelphia permits (phl.carto.com), so wires alongside
        # the existing 'philadelphia' permits config in CITY_REGISTRY.
        'platform': 'carto',
        'service_description': 'Philadelphia OPA Properties',
        'endpoint': 'https://phl.carto.com/api/v2/sql',
        'table_name': 'opa_properties_public',
        'where_clause': 'owner_1 IS NOT NULL AND location IS NOT NULL',
        'order_by': 'cartodb_id',
        'field_map': {
            'owner_name': 'owner_1',
            'address': 'location',
            'zip': 'mailing_zip',
            'owner_mailing_address': 'mailing_street',
            'parcel_id': 'parcel_number',
        },
        'state': 'PA',
        'source_tag': 'assessor:philadelphia_opa',
        'default_page_size': 1000,
    },
    'cuyahoga_cleveland': {
        # V429 (CODE_V428 Phase 1e): Cuyahoga County (Cleveland) CAMA
        # parcels. Probed 2026-04-27: layer 3 "AppraisalParcelView"
        # has 35-field schema with parcel_owner / deeded_owner /
        # mail_name / mail_addr_street / par_addr / par_addr_all /
        # par_city / par_zip / parcel_id. CAMA = Computer-Assisted
        # Mass Appraisal — the assessor's working copy with mailing
        # info exposed.
        'platform': 'arcgis_mapserver',
        'service_description': 'Cuyahoga County Appraisal Parcels',
        'endpoint': 'https://gis.cuyahogacounty.us/server/rest/services/CCGIS/Parcels_CAMA_Real_Property/MapServer/3',
        'where_clause': 'parcel_owner IS NOT NULL',
        'field_map': {
            'owner_name': 'parcel_owner',
            'address': 'par_addr_all',
            'city': 'par_city',
            'zip': 'par_zip',
            'owner_mailing_address': 'mail_addr_street',
            'parcel_id': 'parcel_id',
        },
        'state': 'OH',
        'source_tag': 'assessor:cuyahoga_cleveland',
    },
    'nyc_pluto': {
        # NYC Department of Planning PLUTO (Primary Land Use Tax Lot
        # Output). 858,644 lots — every tax lot in the 5 boroughs.
        # Socrata, not ArcGIS, so the collector uses the soda
        # platform handler. Includes ownername, address, zipcode,
        # assesstot, yearbuilt, zonedist1. Updated ~quarterly by
        # NYC DCP (live probe: 2026-02-20).
        'platform': 'soda',
        'service_description': 'NYC PLUTO - Primary Land Use Tax Lot Output',
        'endpoint': 'https://data.cityofnewyork.us/resource/64uk-42ks.json',
        'where_clause': "ownername IS NOT NULL",
        'field_map': {
            'owner_name': 'ownername',
            'address': 'address',
            'zip': 'zipcode',
            'parcel_id': 'bbl',
        },
        'state': 'NY',
        'source_tag': 'assessor:nyc_pluto',
        # PLUTO supports up to 50000 / page on Socrata. Start
        # conservative at 5000 to keep per-page latency low.
        'default_page_size': 5000,
    },
}


def _fetch_arcgis_page(endpoint, where, offset, page_size, out_fields):
    """Fetch one page from ArcGIS MapServer, non-spatial."""
    params = {
        'where': where,
        'outFields': out_fields,
        'resultOffset': offset,
        'resultRecordCount': page_size,
        'returnGeometry': 'false',
        'orderByFields': 'OBJECTID ASC',
        'f': 'json',
    }
    resp = SESSION.get(endpoint + '/query', params=params, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    if data.get('error'):
        raise RuntimeError(f"ArcGIS error: {data['error']}")
    return data.get('features', [])


def _fetch_carto_page(endpoint, where, offset, page_size, out_fields, table_name=None, order_by='cartodb_id'):
    """V433 (CODE_V428 Phase 1g): Carto SQL API page fetch.

    Philadelphia OPA + a few other muni assessor portals expose
    parcels via the Carto SQL endpoint (phl.carto.com/api/v2/sql).
    Different shape than SODA: takes a SQL string with explicit
    `LIMIT N OFFSET K`, returns `{"rows": [...]}`. Wraps each row
    under 'attributes' so the downstream `collect()` loop can share
    the row-mapping code with arcgis_mapserver/soda paths.
    """
    if not table_name:
        raise ValueError("carto platform requires table_name")
    sql = (
        f"SELECT {out_fields} FROM {table_name} "
        f"WHERE {where} "
        f"ORDER BY {order_by} ASC "
        f"LIMIT {page_size} OFFSET {offset}"
    )
    params = {'q': sql, 'format': 'json'}
    resp = SESSION.get(endpoint, params=params, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, dict) and data.get('error'):
        raise RuntimeError(f"Carto error: {data['error']}")
    rows = data.get('rows') or []
    return [{'attributes': r} for r in rows]


def _fetch_soda_page(endpoint, where, offset, page_size, out_fields):
    """Fetch one page from a Socrata SODA endpoint. Returns a list
    of dicts matching _fetch_arcgis_page's shape (each dict wrapped
    under 'attributes' so the downstream row loop can share code).
    """
    params = {
        '$limit': page_size,
        '$offset': offset,
        '$select': out_fields,
        '$where': where,
    }
    resp = SESSION.get(endpoint, params=params, timeout=60)
    resp.raise_for_status()
    rows = resp.json() or []
    # Normalize: arcgis returns [{'attributes': {...}}, ...]; soda
    # returns [{...}, ...]. Wrap soda rows so the caller's remap
    # step can be uniform.
    return [{'attributes': r} for r in rows]


def _insert_batch(rows, source_tag, state):
    """INSERT OR IGNORE a batch into property_owners.

    Relies on V278's unique index (address, owner_name, source) for
    dedup so re-runs don't double-write.

    V281: the permit-collection daemon holds long-running write locks
    on the SQLite db, so this function retries on "database is locked"
    with exponential backoff. Without retry the assessor route
    consistently 500s during heavy permit cycles.
    """
    if not rows:
        return 0
    conn = permitdb.get_connection()
    # Wait up to 30s for the daemon to release a write lock before
    # giving up. SQLite's busy_timeout handles this at the driver
    # layer without needing manual sleep loops, but it silently
    # falls back to default on some connections — set it explicitly.
    try:
        conn.execute("PRAGMA busy_timeout = 30000")
    except Exception:
        pass
    inserted_before = conn.execute(
        "SELECT COUNT(*) FROM property_owners WHERE source = ?",
        (source_tag,)
    ).fetchone()
    before_n = inserted_before[0] if not isinstance(inserted_before, dict) else inserted_before['COUNT(*)']

    # Skip rows that would violate NOT NULL on address.
    # V280: Bexar uses the literal string "NULL" in place of real
    # nulls for YrBlt/AddrLn1 etc — treat those as empty.
    def _clean(v):
        if v is None:
            return None
        s = str(v).strip()
        if not s or s.upper() == 'NULL':
            return None
        return s

    payload = []
    for r in rows:
        addr = _clean(r.get('address'))
        owner = _clean(r.get('owner_name'))
        if not addr or not owner or len(addr) < 3 or len(owner) < 2:
            continue
        payload.append((
            addr,
            _clean(r.get('city')),
            state,
            _clean(r.get('zip')),
            owner,
            _clean(r.get('owner_mailing_address')),
            _clean(r.get('parcel_id')),
            source_tag,
        ))
    if not payload:
        return 0

    # V281: retry envelope. busy_timeout above should cover the
    # common case, but if the daemon is holding a long writer-lock
    # the pragma times out — fall back to exponential sleep + retry.
    sql = (
        "INSERT OR IGNORE INTO property_owners "
        "(address, city, state, zip, owner_name, owner_mailing_address, "
        " parcel_id, source, last_updated) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))"
    )
    last_err = None
    for attempt in range(5):
        try:
            conn.executemany(sql, payload)
            conn.commit()
            break
        except Exception as e:
            last_err = e
            msg = str(e).lower()
            if 'locked' in msg or 'busy' in msg:
                time.sleep(1.5 * (2 ** attempt))  # 1.5s, 3s, 6s, 12s, 24s
                continue
            raise
    else:
        raise RuntimeError(f"property_owners insert failed after retries: {last_err}")

    after = conn.execute(
        "SELECT COUNT(*) FROM property_owners WHERE source = ?",
        (source_tag,)
    ).fetchone()
    after_n = after[0] if not isinstance(after, dict) else after['COUNT(*)']
    return after_n - before_n


def collect(source_key, max_records=None, page_size=None, start_offset=0):
    """Run a full paginated fetch against one assessor source.

    Args:
        source_key: key in ASSESSOR_SOURCES (e.g. 'maricopa').
        max_records: cap total fetched (None = entire dataset).
            Useful for smoke tests (e.g. max_records=5000).
        page_size: override default 1000.
        start_offset: resume from this offset (if a prior run
            partially completed).

    Returns dict with summary stats.
    """
    cfg = ASSESSOR_SOURCES.get(source_key)
    if not cfg:
        raise ValueError(f"Unknown assessor source: {source_key}")
    platform = cfg['platform']
    if platform not in ('arcgis_mapserver', 'soda', 'carto'):
        raise NotImplementedError(f"Platform {platform} not wired yet")

    field_map = cfg['field_map']
    # Build outFields param from the source field names so we only
    # pull the columns we actually need over the wire.
    # V433b: field_map values can be either a string (single source field)
    # or a list of strings (concat with spaces — used for assessors that
    # don't expose a pre-concatenated address, e.g. Hennepin's HOUSE_NO +
    # STREET_NM split). Flatten for the outFields query string.
    _flat_fields = []
    for v in field_map.values():
        if not v:
            continue
        if isinstance(v, list):
            _flat_fields.extend(v)
        else:
            _flat_fields.append(v)
    out_fields = ','.join(_flat_fields)
    page_size = page_size or cfg.get('default_page_size') or DEFAULT_PAGE_SIZE
    offset = start_offset
    total_fetched = 0
    total_inserted = 0
    pages = 0
    started = time.time()

    while True:
        if max_records and total_fetched >= max_records:
            break
        remaining = (max_records - total_fetched) if max_records else page_size
        this_page = min(remaining, page_size)
        try:
            if platform == 'arcgis_mapserver':
                features = _fetch_arcgis_page(
                    cfg['endpoint'], cfg['where_clause'],
                    offset, this_page, out_fields
                )
            elif platform == 'carto':
                features = _fetch_carto_page(
                    cfg['endpoint'], cfg['where_clause'],
                    offset, this_page, out_fields,
                    table_name=cfg.get('table_name'),
                    order_by=cfg.get('order_by', 'cartodb_id'),
                )
            else:  # soda
                features = _fetch_soda_page(
                    cfg['endpoint'], cfg['where_clause'],
                    offset, this_page, out_fields
                )
        except Exception as e:
            # Surface the offset that failed so a re-run can resume.
            return {
                'status': 'error',
                'source': source_key,
                'error': str(e)[:500],
                'last_offset': offset,
                'pages': pages,
                'total_fetched': total_fetched,
                'total_inserted': total_inserted,
                'elapsed_sec': round(time.time() - started, 1),
            }
        if not features:
            break

        def _resolve(attrs, src):
            """V433b: resolve a field_map value to a string. If src is a
            list, concat the individual attrs with single spaces (filtering
            None/empty); else attrs.get directly."""
            if src is None:
                return None
            if isinstance(src, list):
                parts = [str(attrs.get(k)).strip() for k in src
                         if attrs.get(k) is not None and str(attrs.get(k)).strip()]
                return ' '.join(parts) if parts else None
            return attrs.get(src)

        rows = []
        for feat in features:
            attrs = feat.get('attributes', {}) or {}
            # Remap source field names → property_owners column names.
            rows.append({
                'owner_name': _resolve(attrs, field_map.get('owner_name')),
                'address': _resolve(attrs, field_map.get('address')),
                'city': _resolve(attrs, field_map.get('city')),
                'zip': _resolve(attrs, field_map.get('zip')),
                'owner_mailing_address': _resolve(attrs, field_map.get('owner_mailing_address')),
                'parcel_id': _resolve(attrs, field_map.get('parcel_id')),
            })

        inserted = _insert_batch(rows, cfg['source_tag'], cfg['state'])
        total_fetched += len(features)
        total_inserted += inserted
        pages += 1

        # If the server returned fewer than requested, we've hit the
        # end of the dataset.
        if len(features) < this_page:
            break
        offset += len(features)

    return {
        'status': 'ok',
        'source': source_key,
        'pages': pages,
        'total_fetched': total_fetched,
        'total_inserted': total_inserted,
        'last_offset': offset,
        'elapsed_sec': round(time.time() - started, 1),
    }
