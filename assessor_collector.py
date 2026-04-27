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
        # prop_address_full per row. Latest year=2026 records present.
        # Cook County covers ~1.86M parcels — Chicago is the bulk but
        # ~all 5 N IL counties feed in via mail_address_state filter.
        # Filter to year=current to keep page sets sane (~1 year of
        # data is enough; the table is updated annually).
        'platform': 'soda',
        'service_description': 'Cook County Assessor — Parcel Addresses',
        'endpoint': 'https://datacatalog.cookcountyil.gov/resource/3723-97qp.json',
        # Some rows have owner_address_name='' (LLC properties only
        # populate the entity name elsewhere). Skip those at insert
        # time via _insert_batch's len(owner) >= 2 guard.
        'where_clause': "owner_address_name IS NOT NULL AND prop_address_full IS NOT NULL",
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
    if platform not in ('arcgis_mapserver', 'soda'):
        raise NotImplementedError(f"Platform {platform} not wired yet")

    field_map = cfg['field_map']
    # Build outFields param from the source field names so we only
    # pull the columns we actually need over the wire.
    out_fields = ','.join(v for v in field_map.values() if v)
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

        rows = []
        for feat in features:
            attrs = feat.get('attributes', {}) or {}
            # Remap source field names → property_owners column names.
            rows.append({
                'owner_name': attrs.get(field_map.get('owner_name')),
                'address': attrs.get(field_map.get('address')),
                'city': attrs.get(field_map.get('city')),
                'zip': attrs.get(field_map.get('zip')),
                'owner_mailing_address': attrs.get(field_map.get('owner_mailing_address')),
                'parcel_id': attrs.get(field_map.get('parcel_id')),
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
