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
            # V474c: switch from PHYSICAL_CITY → JURISDICTION. Probed
            # 2026-04-29: PHYSICAL_CITY is reliably populated for the
            # ~5K residential parcels at offset 50K (99 PHOENIX +
            # 1 SURPRISE in a 100-record sample), but the first 78K
            # imported rows all landed as 'Phoenix' regardless of true
            # situs because earlier code used a Phoenix default. Going
            # forward, JURISDICTION carries the same values as
            # PHYSICAL_CITY (PHOENIX/MESA/SCOTTSDALE/TEMPE/CHANDLER/
            # GLENDALE/SURPRISE/AVONDALE/PEORIA) and is non-null on
            # every utility/government parcel where PHYSICAL_CITY is
            # blank — so JURISDICTION gives strictly better coverage.
            'city': 'JURISDICTION',
            'zip': 'PHYSICAL_ZIP',
            'owner_mailing_address': 'MAIL_ADDRESS',
            'parcel_id': 'APN',
        },
        'state': 'AZ',
        'source_tag': 'assessor:maricopa',
    },
    'miami_dade_hialeah': {
        # V476: Miami-Dade County's PaParcelView_gdb has 37,373 parcels with
        # TRUE_SITE_CITY='HIALEAH' but the existing miami_dade source's
        # 81,126 stored rows are all tagged city='Miami' (relic of an
        # earlier default_city='Miami' import). Splitting Hialeah into its
        # own filtered source lets us land Hialeah-tagged rows so the
        # /cities scorecard credits Hialeah for owners (currently 29).
        # NOTE: TRUE_SITE_ZIP is omitted from the outFields — combining it
        # with TRUE_SITE_ADDR + TRUE_SITE_CITY + extra fields trips a
        # 400 "Invalid query parameters" on the FeatureServer (probed
        # 2026-04-30 via a cumulative add test). All other fields work.
        'platform': 'arcgis_mapserver',
        'service_description': 'Miami-Dade Property Appraiser — Hialeah only',
        'endpoint': 'https://services.arcgis.com/8Pc9XBTAsYuxx9Ny/arcgis/rest/services/PaParcelView_gdb/FeatureServer/0',
        'where_clause': "TRUE_OWNER1 IS NOT NULL AND TRUE_SITE_CITY = 'HIALEAH'",
        'field_map': {
            'owner_name': 'TRUE_OWNER1',
            'address': 'TRUE_SITE_ADDR',
            'city': 'TRUE_SITE_CITY',
            'owner_mailing_address': 'TRUE_MAILING_ADDR1',
            'parcel_id': 'FOLIO',
        },
        'state': 'FL',
        'source_tag': 'assessor:miami_dade_hialeah',
    },
    'santa_clara_sj': {
        # V474g: Santa Clara County CA (San Jose). Probed 2026-04-30 —
        # services3.arcgis.com/JAU7IM34hqT9y9ew is Santa Clara's full
        # parcel feed with ASSESSEE (owner) + SiteAddressFull (situs)
        # + SITUS_CITY_NAME + Jurisdiction. Same structure as
        # standard ArcGIS county parcels. Covers San Jose + Santa
        # Clara + Sunnyvale + Mountain View + Campbell + Cupertino.
        'platform': 'arcgis_mapserver',
        'service_description': 'Santa Clara County (San Jose) Parcels',
        'endpoint': 'https://services3.arcgis.com/JAU7IM34hqT9y9ew/arcgis/rest/services/Parcels/FeatureServer/0',
        'where_clause': "ASSESSEE IS NOT NULL AND SiteAddressFull IS NOT NULL",
        'field_map': {
            'owner_name': 'ASSESSEE',
            'address': 'SiteAddressFull',
            'city': 'City',
            'zip': 'SITUSZIP',
            'owner_mailing_address': 'MAILING_ADDRESS',
            'parcel_id': 'APN',
        },
        'state': 'CA',
        'source_tag': 'assessor:santa_clara_sj',
    },
    'clark_henderson': {
        # V474f: Clark County NV — Henderson-specific filter. Probed
        # 2026-04-29: Clark XAPO has 132,595 Henderson parcels but the
        # 'clark_lasvegas' source's OBJECTID-ordered scan stays in N
        # Las Vegas / Mesquite / LV proper for the first 73K records,
        # never reaching Henderson. Splitting Henderson into its own
        # source lets us target 132K rows directly.
        'platform': 'arcgis_mapserver',
        'service_description': 'Clark County NV Accela XAPO — Henderson only',
        'endpoint': 'https://maps.clarkcountynv.gov/arcgis/rest/services/Accela/AccelaPoints/MapServer/0',
        'where_clause': "ownerFullName IS NOT NULL AND address IS NOT NULL AND primaryParcelFlag = 'Y' AND city = 'HENDERSON'",
        'field_map': {
            'owner_name': 'ownerFullName',
            'address': 'address',
            'city': 'city',
            'zip': 'zip',
            'owner_mailing_address': 'mailAddress1',
            'parcel_id': 'parcelNumber',
        },
        'state': 'NV',
        'source_tag': 'assessor:clark_henderson',
    },
    'collin_plano': {
        # V474e: Collin County (Plano + Frisco + McKinney + Allen).
        # Probed 2026-04-29 — services2.arcgis.com/5aVZxf6eblRfH5Yb is
        # the NCTCOG-hosted Collin CAD parcel feed. 33,432 total
        # records with OwnerName; 468 explicitly tagged situs_city=PLANO
        # (the dataset appears to be a non-residential / commercial
        # subset, not full Collin coverage). situs_display gives the
        # full assembled situs ("2300 W PLANO PKWY \r\nPLANO, TX
        # 75075") which we map to address. addr_line1 is the OWNER
        # mailing address (often "C/O" entries), kept as
        # owner_mailing_address.
        'platform': 'arcgis_mapserver',
        'service_description': 'Collin CAD Parcels (NCTCOG-hosted)',
        'endpoint': 'https://services2.arcgis.com/5aVZxf6eblRfH5Yb/arcgis/rest/services/Parcel/FeatureServer/0',
        'where_clause': "OwnerName IS NOT NULL AND situs_display IS NOT NULL",
        'field_map': {
            'owner_name': 'OwnerName',
            'address': 'situs_display',
            'city': 'situs_city',
            'zip': 'situs_zip',
            'owner_mailing_address': 'addr_line1',
            'parcel_id': 'GEO_ID',
        },
        'state': 'TX',
        'source_tag': 'assessor:collin_plano',
    },
    'maricopa_secondary': {
        # V474d: Maricopa County's non-Phoenix municipalities. Probed
        # 2026-04-29: Mesa alone has 173,092 owner-bearing parcels.
        # Combined Mesa/Scottsdale/Tempe/Chandler/Glendale/Peoria/
        # Gilbert/Surprise/Avondale/Goodyear ≈ 700K+ parcels not
        # covered by the existing 'maricopa' source's first 78K
        # imports (which OBJECTID-ordered into Phoenix territory).
        # JURISDICTION provides the city tag, so each parcel lands
        # under its actual situs city — unblocks Mesa + Scottsdale
        # for the /cities scorecard's COMPLETE bucket.
        'platform': 'arcgis_mapserver',
        'service_description': 'Maricopa County Assessor — non-Phoenix municipalities',
        'endpoint': 'https://gis.mcassessor.maricopa.gov/arcgis/rest/services/Parcels/MapServer/0',
        'where_clause': "OWNER_NAME IS NOT NULL AND PHYSICAL_ADDRESS IS NOT NULL AND PHYSICAL_ADDRESS <> '' AND JURISDICTION IN ('MESA','SCOTTSDALE','TEMPE','CHANDLER','GLENDALE','PEORIA','GILBERT','SURPRISE','AVONDALE','GOODYEAR','BUCKEYE','TOLLESON','EL MIRAGE','LITCHFIELD PARK','FOUNTAIN HILLS','PARADISE VALLEY')",
        'field_map': {
            'owner_name': 'OWNER_NAME',
            'address': 'PHYSICAL_ADDRESS',
            'city': 'JURISDICTION',
            'zip': 'PHYSICAL_ZIP',
            'owner_mailing_address': 'MAIL_ADDRESS',
            'parcel_id': 'APN',
        },
        'state': 'AZ',
        'source_tag': 'assessor:maricopa_secondary',
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
    'franklin_columbus': {
        # V468 (CODE_V468 Phase 4B): Franklin County, OH (Columbus area).
        # Tax Parcel layer at gis.franklincountyohio.gov. Probed 2026-04-29
        # via SSH: 117-field schema with OWNERNME1 (primary owner),
        # MAILNME1/PSTLADDRES (mailing), SITEADDRESS + ZIPCD (situs),
        # TOTVALUEBASE (assessed value), PARCELID. Reachable from Render
        # SSH (DNS resolves, returns valid f=json metadata).
        # Columbus has 2,115 contractor profiles + 6,373 violations but
        # zero owner records pre-V468.
        'platform': 'arcgis_mapserver',
        'service_description': 'Franklin County Tax Parcels',
        'endpoint': 'https://gis.franklincountyohio.gov/hosting/rest/services/ParcelFeatures/Parcel_Features/MapServer/0',
        'where_clause': "OWNERNME1 IS NOT NULL AND OWNERNME1 <> '' AND SITEADDRESS IS NOT NULL AND SITEADDRESS <> ''",
        'field_map': {
            'owner_name': 'OWNERNME1',
            'address': 'SITEADDRESS',
            'zip': 'ZIPCD',
            'parcel_id': 'PARCELID',
            'owner_mailing_address': 'PSTLADDRES',
        },
        'state': 'OH',
        'source_tag': 'assessor:franklin_columbus',
        # V473b: tag every parcel with Columbus — Franklin's situs city
        # column is omitted from field_map, so without this default the
        # /cities (city,state) match credits Columbus with 0 owners.
        'default_city': 'Columbus',
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
    'hillsborough_tampa': {
        # V433n: Hillsborough County FL (Tampa). Probed 2026-04-27:
        # arcgis.tampagov.net/.../Parcels/TaxParcel/FeatureServer/0.
        # Tampa hosts the Hillsborough County PA data on their own
        # ArcGIS server. 46 fields with OWNER, ADDR_1+CITY+STATE+ZIP
        # (mailing), SITE_ADDR+SITE_CITY+SITE_ZIP (site), FOLIO+PIN+
        # STRAP (parcel IDs), JUST/LAND/BLDG/ASD_VAL/TAX_VAL.
        # Tampa permits are dead per CLAUDE.md (Accela no contractor
        # column) — wiring Tampa owners now positions the city to
        # activate the moment the Accela parser fix from CLAUDE.md
        # P1 ships.
        'platform': 'arcgis_mapserver',
        'service_description': 'Hillsborough County FL (Tampa) Tax Parcels',
        'endpoint': 'https://arcgis.tampagov.net/arcgis/rest/services/Parcels/TaxParcel/FeatureServer/0',
        'where_clause': "OWNER IS NOT NULL AND SITE_ADDR IS NOT NULL AND SITE_ADDR <> ''",
        'field_map': {
            'owner_name': 'OWNER',
            'address': 'SITE_ADDR',
            'city': 'SITE_CITY',
            'zip': 'SITE_ZIP',
            'owner_mailing_address': 'ADDR_1',
            'parcel_id': 'FOLIO',
        },
        'state': 'FL',
        'source_tag': 'assessor:hillsborough_tampa',
    },
    'multnomah_portland': {
        # V433m: Portland OR (Multnomah County / BDS Property database).
        # Probed 2026-04-27: portlandmaps.com/arcgis/rest/services/Public/
        # BDS_Property/FeatureServer/0. 48 fields with OWNER_NAME,
        # OWNER_MAILING_ADDRESS, ADDRESS_SITUS (pre-concatenated),
        # PROPERTY_ID, PROPERTY_ID_MULTNOMAH_COUNTY, BDS_PROPERTY_STATUS.
        # Portland permits already wired but the CUSTOMER field on
        # BDS_Permit/22 is numeric license IDs, not contractor names —
        # owner data activates Portland as a no-contractor city per
        # the V362 Part A template pattern.
        'platform': 'arcgis_mapserver',
        'service_description': 'Portland OR BDS Property',
        'endpoint': 'https://www.portlandmaps.com/arcgis/rest/services/Public/BDS_Property/FeatureServer/0',
        'where_clause': "OWNER_NAME IS NOT NULL AND ADDRESS_SITUS IS NOT NULL AND ADDRESS_SITUS <> ''",
        'field_map': {
            'owner_name': 'OWNER_NAME',
            'address': 'ADDRESS_SITUS',
            'owner_mailing_address': 'OWNER_MAILING_ADDRESS',
            'parcel_id': 'PROPERTY_ID_MULTNOMAH_COUNTY',
        },
        'state': 'OR',
        'source_tag': 'assessor:multnomah_portland',
    },
    'lee_capecoral': {
        # V433l: Lee County FL Property Appraiser. Probed 2026-04-27:
        # services2.arcgis.com/LvWGAAhHwbCJ2GMP/.../Lee_County_Parcels/
        # FeatureServer/0. 125 fields. Owner fields prefixed O_:
        # O_NAME (owner), O_OTHERS (joint owner), O_CAREOF (c/o),
        # O_ADDR1, O_CITY, O_STATE, O_ZIP (mailing). Site address
        # pre-concatenated as SITEADDR; STRAP is parcel ID.
        # Cape Coral permits already wired (CLAUDE.md V395 etc).
        'platform': 'arcgis_mapserver',
        'service_description': 'Lee County FL Property Appraiser Parcels',
        'endpoint': 'https://services2.arcgis.com/LvWGAAhHwbCJ2GMP/arcgis/rest/services/Lee_County_Parcels/FeatureServer/0',
        'where_clause': "O_NAME IS NOT NULL AND SITEADDR IS NOT NULL AND SITEADDR <> ''",
        'field_map': {
            'owner_name': 'O_NAME',
            'address': 'SITEADDR',
            'city': 'SITECITY',
            'zip': 'SITEZIP',
            'owner_mailing_address': 'O_ADDR1',
            'parcel_id': 'STRAP',
        },
        'state': 'FL',
        'source_tag': 'assessor:lee_capecoral',
    },
    'broward_ftlauderdale': {
        # V433k: Broward County FL Property Appraiser. Probed 2026-04-27:
        # services.arcgis.com/JMAJrTsHNLrSsWf5/.../PARCEL_POLY_BCPA_TAXROLL/
        # FeatureServer/0. 227 fields including NAME_LINE_1 (owner),
        # NAME_LINE_2 (joint owner), ADDRESS_LINE_1+CITY+STATE+ZIP (owner
        # mailing), SITUS_STREET_NUMBER+DIRECTION+NAME+TYPE+UNIT+SITUS_CITY+
        # SITUS_ZIP_CODE (site address), FOLIO (parcel ID), JUST_LAND_VALUE,
        # JUST_BUILDING_VALUE.
        # Fort Lauderdale permits already wired (CLAUDE.md V326 noted).
        'platform': 'arcgis_mapserver',
        'service_description': 'Broward County FL Property Appraiser Tax Roll',
        'endpoint': 'https://services.arcgis.com/JMAJrTsHNLrSsWf5/arcgis/rest/services/PARCEL_POLY_BCPA_TAXROLL/FeatureServer/0',
        'where_clause': "NAME_LINE_1 IS NOT NULL AND SITUS_STREET_NAME IS NOT NULL",
        'field_map': {
            'owner_name': 'NAME_LINE_1',
            'address': ['SITUS_STREET_NUMBER', 'SITUS_STREET_DIRECTION', 'SITUS_STREET_NAME', 'SITUS_STREET_TYPE'],
            'city': 'SITUS_CITY',
            'zip': 'SITUS_ZIP_CODE',
            'owner_mailing_address': 'ADDRESS_LINE_1',
            'parcel_id': 'FOLIO',
        },
        'state': 'FL',
        'source_tag': 'assessor:broward_ftlauderdale',
    },
    'wake_raleigh': {
        # V433j: Wake County NC (Raleigh + Cary metro). Probed 2026-04-27:
        # maps.wakegov.com/.../Property/Parcels/FeatureServer/0. 59 fields
        # with OWNER, SITE_ADDRESS (pre-concatenated), STNUM, STNAME,
        # STYPE, CITY, ADDR1/2/3 (owner mailing), PIN_NUM, REID,
        # BLDG_VAL, LAND_VAL, TOTAL_VALUE_ASSD. Source CITY field
        # populated per-row so retag rule preserves Raleigh / Cary /
        # Apex / Wake Forest distinction.
        'platform': 'arcgis_mapserver',
        'service_description': 'Wake County (Raleigh) Parcels',
        'endpoint': 'https://maps.wakegov.com/arcgis/rest/services/Property/Parcels/FeatureServer/0',
        # V435b: dropped the SITE_ADDRESS <> '' filter — Wake's empty
        # values are NULL not empty string, so the extra filter excluded
        # ALL rows. Full-table count is ~435K with just IS NOT NULL.
        'where_clause': "OWNER IS NOT NULL AND SITE_ADDRESS IS NOT NULL",
        'field_map': {
            'owner_name': 'OWNER',
            'address': 'SITE_ADDRESS',
            'city': 'CITY',
            'owner_mailing_address': 'ADDR1',
            'parcel_id': 'PIN_NUM',
        },
        'state': 'NC',
        'source_tag': 'assessor:wake_raleigh',
    },
    'hamilton_cincinnati': {
        # V433i: Hamilton County OH (Cincinnati). Probed 2026-04-27:
        # cagisonline.hamilton-co.org/arcgis/rest/services/Hamilton/
        # HCE_Parcels_With_Auditor_Data/MapServer/0. 67 fields joining
        # the County Engineer's parcel fabric with the Auditor's
        # AUDREAL view. Field names are dotted (CAGIS.AUDREAL_VW.OWNNM1)
        # — ArcGIS handles those in outFields= as long as the source
        # publishes the join.
        # Cincinnati permits already wired (V417) with NO contractor
        # field. Adding owner data activates Cincinnati as a no-
        # contractor city per the V362 Part A template pattern.
        'platform': 'arcgis_mapserver',
        'service_description': 'Hamilton County (Cincinnati) Parcels with Auditor Data',
        'endpoint': 'https://cagisonline.hamilton-co.org/arcgis/rest/services/Hamilton/HCE_Parcels_With_Auditor_Data/MapServer/0',
        'where_clause': "CAGIS.AUDREAL_VW.OWNER48 IS NOT NULL AND CAGIS.AUDREAL_VW.ADDRST IS NOT NULL",
        'field_map': {
            'owner_name': 'CAGIS.AUDREAL_VW.OWNER48',
            # Concat house number + street + suffix (ADDRST + ADDRSF
            # = "MAIN" + "ST"). _resolve filters empties.
            'address': ['CAGIS.AUDREAL_VW.ADDRNO', 'CAGIS.AUDREAL_VW.ADDRST', 'CAGIS.AUDREAL_VW.ADDRSF'],
            'city': 'CAGIS.AUDREAL_VW.MLTOWN',
            'zip': 'CAGIS.AUDREAL_VW.OWNADZIP',
            'owner_mailing_address': 'CAGIS.AUDREAL_VW.OWNAD1',
            'parcel_id': 'CAGIS.AUDREAL_VW.PARCEL',
        },
        'state': 'OH',
        'source_tag': 'assessor:hamilton_cincinnati',
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
    'marion_indianapolis': {
        # V473b Section B #1: Marion County, IN (Indianapolis).
        # Probed 2026-04-30 — Accela_HHC_Parcels MapServer/0 has the full
        # owner schema (OWNER_NAME, MAIL_ADDRESS1/2, MAIL_CITY/STATE/ZIP,
        # ADDRESS1, plus fragmented STREET_NAME/STREET_SUFFIX). The
        # adjacent HHC_ParcelOwner service only has SUM_ACRES — wrong
        # service, the working one is Accela_HHC_Parcels.
        # Sample: "GETTELFINGER, ERIC M & JENNIFER L REBER" /
        # "3240 E FALL CREEK PW N DR" / mailing "430 N PARK AVE APT 103
        # INDIANAPOLIS IN 46202-3677".
        'platform': 'arcgis_mapserver',
        'service_description': 'Marion County (Indianapolis) Parcels',
        'endpoint': 'https://gis.indy.gov/server/rest/services/Accela/Accela_HHC_Parcels/MapServer/0',
        'where_clause': "OWNER_NAME IS NOT NULL AND OWNER_NAME <> '' AND ADDRESS1 IS NOT NULL AND ADDRESS1 <> ''",
        'field_map': {
            'owner_name': 'OWNER_NAME',
            'address': 'ADDRESS1',
            'owner_mailing_address': 'MAIL_ADDRESS1',
        },
        'state': 'IN',
        'source_tag': 'assessor:marion_indianapolis',
        # V473b: county source has no SITUS_CITY column. Tag every row
        # with 'Indianapolis' so the cities-page (city,state) match
        # credits the principal city (Marion is ~95% Indianapolis).
        'default_city': 'Indianapolis',
    },
    'tarrant_fortworth': {
        # V473b Section B #4: Tarrant County, TX (Fort Worth + Arlington).
        # Probed 2026-04-30 — Tarrant Appraisal District's Parcels_Enriched
        # FeatureServer/0 carries OWNER_NAME, MAILING_ADDRESS_LINE_1/2/3 +
        # MAILING_CITY_NAME/STATE/ZIP_CODE, LOCATION_ADDRESS (situs),
        # STREET_NAME, plus TOTAL_ASSESSED_VALUE. Sample row was an
        # institutional owner ("PINERY MEADOWS METRO DISTRICT 2") but
        # field structure is correct.
        'platform': 'arcgis_mapserver',  # FeatureServer uses identical query API
        'service_description': 'Tarrant Appraisal District Parcels Enriched',
        'endpoint': 'https://services.arcgis.com/seTexOicoRXDvRsJ/arcgis/rest/services/Parcels_Enriched/FeatureServer/0',
        'where_clause': "OWNER_NAME IS NOT NULL AND OWNER_NAME <> '' AND LOCATION_ADDRESS IS NOT NULL AND LOCATION_ADDRESS <> ''",
        'field_map': {
            'owner_name': 'OWNER_NAME',
            'address': 'LOCATION_ADDRESS',
            'owner_mailing_address': 'MAILING_ADDRESS_LINE_1',
        },
        'state': 'TX',
        'source_tag': 'assessor:tarrant_fortworth',
        # V473b: tag every parcel with Fort Worth — Tarrant covers FW,
        # Arlington, Grand Prairie, Mansfield etc. but the most common
        # destination city in the top-100 list is Fort Worth.
        'default_city': 'Fort Worth',
    },
    'hamilton_cincinnati': {
        # V473b Section B #5: Hamilton County, OH (Cincinnati).
        # Probed 2026-04-30 — CAGIS Cadastral MapServer/0 has the full
        # owner schema: OWNNM1 (primary owner), OWNNM2 (secondary),
        # OWNAD1/OWNAD2 (mailing), MLTOWN, plus situs split across
        # ADDRNO + ADDRST + ADDRSF (number + street + suffix).
        # Concat'd via _resolve()'s list-handling path so the assembled
        # situs ("3920 RIVER RD") matches what permits.address looks like.
        # 419,561 parcels with owner; 353,902 of those have full situs.
        # default_page_size=200: server advertises maxRecordCount=1000
        # but empirically returns HTTP 400 "Unable to complete operation"
        # when the where_clause + orderByFields + resultRecordCount=1000
        # combination is sent. 200 is well under the threshold.
        'platform': 'arcgis_mapserver',
        'service_description': 'Hamilton County (Cincinnati) Parcels',
        'endpoint': 'https://cagisonline.hamilton-co.org/arcgis/rest/services/HCE/Cadastral/MapServer/0',
        # NOTE: NO `<> ''` empty-string filter — Hamilton's MapServer
        # silently returns 0 features when that clause is present
        # (the underlying SQL provider treats varchar NULL/'' inconsistently
        # with the engine, and the empty-string compare collapses the result
        # set to nothing). Empty-string owners get filtered downstream by
        # _insert_batch's `if not owner` guard.
        'where_clause': "OWNNM1 IS NOT NULL AND ADDRST IS NOT NULL",
        'field_map': {
            'owner_name': 'OWNNM1',
            'address': ['ADDRNO', 'ADDRST', 'ADDRSF'],
            'owner_mailing_address': 'OWNAD1',
        },
        'state': 'OH',
        'source_tag': 'assessor:hamilton_cincinnati',
        'default_page_size': 200,
        # V473b: county source's situs is split (ADDRNO/ADDRST/ADDRSF)
        # with no city column — tag every parcel with Cincinnati.
        'default_city': 'Cincinnati',
    },
    'fl_statewide': {
        # V474: Florida statewide cadastral (DOR-published). One source
        # covers Orlando, Jacksonville, St. Petersburg, Hialeah, Cape
        # Coral, Fort Lauderdale + dozens more FL cities in a single
        # feed. OWN_NAME is the primary owner; OWN_CITY tags each parcel
        # with its situs city so no default_city needed. PHY_ADDR1 is
        # the physical (situs) address. Probed 2026-04-29 — 2000-record
        # maxRecordCount, fields confirmed.
        'platform': 'arcgis_mapserver',
        'service_description': 'Florida Statewide Cadastral (DOR)',
        'endpoint': 'https://services9.arcgis.com/Gh9awoU677aKree0/arcgis/rest/services/Florida_Statewide_Cadastral/FeatureServer/0',
        'where_clause': "OWN_NAME IS NOT NULL AND PHY_ADDR1 IS NOT NULL",
        'field_map': {
            'owner_name': 'OWN_NAME',
            'address': 'PHY_ADDR1',
            'owner_mailing_address': 'OWN_ADDR1',
            'city': 'OWN_CITY',
        },
        'state': 'FL',
        'source_tag': 'assessor:fl_statewide',
    },
    'washoe_reno': {
        # V474: Washoe County NV (Reno + Sparks). LASTNAME holds the
        # primary owner entity (LLC, trust, family); FIRSTNAME holds
        # individual first names which can be empty. _resolve() concats
        # list fields with empty-filtering so missing FIRSTNAME drops
        # cleanly. FullAddress is the situs. No city column exposed —
        # tag everything Reno (Washoe is ~85% Reno population).
        'platform': 'arcgis_mapserver',
        'service_description': 'Washoe County (Reno) Open Data',
        'endpoint': 'https://wcgisweb.washoecounty.us/arcgis/rest/services/OpenData/OpenData/FeatureServer/0',
        'where_clause': "LASTNAME IS NOT NULL AND FullAddress IS NOT NULL",
        'field_map': {
            'owner_name': ['FIRSTNAME', 'LASTNAME'],
            'address': 'FullAddress',
        },
        'state': 'NV',
        'source_tag': 'assessor:washoe_reno',
        'default_city': 'Reno',
    },
    'dane_madison': {
        # V474: Dane County WI (Madison + Sun Prairie + Fitchburg + Stoughton).
        # Probed 2026-04-29 — TaxParcels/MapServer/0 has Owner (primary)
        # + CoOwner + PropertyAddress (situs). Municipality field is
        # values like "City of Madison" / "City of Stoughton" — too
        # noisy to map cleanly to (city,state) tuple matching, so
        # default_city Madison covers the principal city. Sub-
        # municipalities won't credit (~30% of parcels) but Madison
        # gets full count. Note arcgissrv (not arcgis) in path.
        'platform': 'arcgis_mapserver',
        'service_description': 'Dane County (Madison) Tax Parcels',
        'endpoint': 'https://dcimapapps.danecounty.gov/arcgissrv/rest/services/TaxParcels/MapServer/0',
        'where_clause': "Owner IS NOT NULL AND PropertyAddress IS NOT NULL",
        'field_map': {
            'owner_name': 'Owner',
            'address': 'PropertyAddress',
            'owner_mailing_address': 'BillingStreetAddress',
        },
        'state': 'WI',
        'source_tag': 'assessor:dane_madison',
        'default_city': 'Madison',
    },
    'pima_tucson': {
        # V474: Pima County AZ (Tucson). NOTE: the `own` field is just
        # an ownership category ("Private", "Public") — NOT the owner
        # name. Real owner is in ADDRESSEE (mailing-address recipient).
        # SITE_ADDRESS is the situs. ADDRESS is the mailing address
        # (may differ for absentee owners — useful as
        # owner_mailing_address). No city column — tag Tucson.
        'platform': 'arcgis_mapserver',
        'service_description': 'Pima County (Tucson) Property Housing',
        'endpoint': 'https://mapdata.tucsonaz.gov/public/rest/services/PublicMaps/PropertyHousing/MapServer/40',
        'where_clause': "ADDRESSEE IS NOT NULL AND SITE_ADDRESS IS NOT NULL",
        'field_map': {
            'owner_name': 'ADDRESSEE',
            'address': 'SITE_ADDRESS',
            'owner_mailing_address': 'ADDRESS',
        },
        'state': 'AZ',
        'source_tag': 'assessor:pima_tucson',
        'default_city': 'Tucson',
    },
    'duval_jacksonville': {
        # V484 B3: Duval County (Jacksonville FL) Property Appraiser
        # Parcels. Probed 2026-05-01: 405,716 parcels city-wide (covers
        # Jacksonville + Beach cities), updated DAILY per Duval PA docs.
        # The owner name is in a single field (LNAMEOWNER), but the
        # property address is split across 4 columns — leverage the
        # V433b list-form `address` field_map (same pattern as
        # hennepin_minneapolis HOUSE_NO + STREET_NM) so the collector
        # concats them client-side. Mailing-address fields (MAILADDR1,
        # MAILCITY, MAILSTATE, MAILZIP) are separately exposed; we only
        # store the line-1 form to match the schema (one column for
        # owner_mailing_address). Site city comes through ADDRCITY so
        # the slug derivation (LOWER+REPLACE) keys most rows under
        # 'jacksonville' (the principal city).
        'platform': 'arcgis_mapserver',
        'service_description': 'Duval County Property Appraiser Parcels',
        'endpoint': 'https://maps.coj.net/coj/rest/services/CityBiz/Parcels/MapServer/0',
        'where_clause': "LNAMEOWNER IS NOT NULL AND LNAMEOWNER <> ''",
        'field_map': {
            'owner_name': 'LNAMEOWNER',
            'address': ['STREET_NO', 'ST_DIR', 'ST_NAME', 'ST_TYPE'],
            'owner_mailing_address': 'MAILADDR1',
            'parcel_id': 'RE',
            'city': 'ADDRCITY',
        },
        'state': 'FL',
        'source_tag': 'assessor:duval_jacksonville',
        'pagination_strategy': 'objectid',
    },
    'spokane_spokane': {
        # V484 B4: Spokane County WA Parcels (SCOUT PropertyLookup).
        # Probed 2026-05-01: 138,046 parcels, nightly refresh. Field
        # names are lowercase (different from most ArcGIS feeds).
        # No mailing-address column is exposed in this layer — leave
        # owner_mailing_address out of the field_map. The
        # `NOT LIKE '%Pending%'` filter excludes the few hundred
        # "Current Information Pending" rows for active segregations.
        'platform': 'arcgis_mapserver',
        'service_description': 'Spokane County Parcels (SCOUT)',
        'endpoint': 'https://gismo.spokanecounty.org/arcgis/rest/services/SCOUT/PropertyLookup/MapServer/0',
        'where_clause': "owner_name IS NOT NULL AND owner_name NOT LIKE '%Pending%'",
        'field_map': {
            'owner_name': 'owner_name',
            'address': 'site_address',
            'parcel_id': 'PID_NUM',
            'city': 'site_city',
        },
        'state': 'WA',
        'source_tag': 'assessor:spokane_spokane',
        'pagination_strategy': 'objectid',
    },
    'wayne_detroit': {
        # V486 A1: City of Detroit (Wayne County) parcels. Probed
        # 2026-05-02: 378K parcels, daily refresh. Sample row:
        # parcel_id=02000184., taxpayer_1='DETROIT CLUB HOLDINGS, LLC',
        # taxpayer_address='712 CASS AVE', taxpayer_city='DETROIT'.
        # Detroit permits are dead (re-confirmed V486) — onboards as an
        # owners-only metro per the Birmingham pattern. 378K is enough
        # to monetize the absentee-investor / motivated-seller / direct-
        # mail playbook even without permits.
        'platform': 'arcgis_mapserver',
        'service_description': 'City of Detroit parcels (current year)',
        'endpoint': 'https://services2.arcgis.com/qvkbeam7Wirps6zC/arcgis/rest/services/parcel_file_current/FeatureServer/0',
        'where_clause': "taxpayer_1 IS NOT NULL AND taxpayer_1 <> ''",
        'field_map': {
            'parcel_id': 'parcel_id',
            'owner_name': 'taxpayer_1',
            'address': 'address',
            'zip': 'zip_code',
            'owner_mailing_address': 'taxpayer_address',
        },
        'state': 'MI',
        'source_tag': 'assessor:wayne_detroit',
        'pagination_strategy': 'objectid',
        'default_city': 'Detroit',
    },
    'lucas_toledo': {
        # V486 A2: Lucas County (Toledo OH) parcels. Probed 2026-05-02:
        # 204K parcels filtered to Toledo, weekly fresh. Toledo permits
        # are also dead — same owners-only metro pattern as Detroit.
        # property_address is single-string ("123 MAIN ST TOLEDO OH 43604")
        # so we filter by LIKE '%TOLEDO%' to scope.
        'platform': 'arcgis_mapserver',
        'service_description': 'Lucas County (Toledo) parcels',
        'endpoint': 'https://services3.arcgis.com/T8dczfwPixv79EgZ/arcgis/rest/services/Parcels_General_Land_Use_Classification_view/FeatureServer/0',
        'where_clause': "owner IS NOT NULL AND owner <> '' AND property_address LIKE '%TOLEDO%'",
        'field_map': {
            'parcel_id': 'parid',
            'owner_name': 'owner',
            'address': 'property_address',
            'owner_mailing_address': 'mailing_address',
        },
        'state': 'OH',
        'source_tag': 'assessor:lucas_toledo',
        'pagination_strategy': 'objectid',
        'default_city': 'Toledo',
    },
    'fulton_atlanta': {
        # V486 A3: Fulton County (Atlanta GA + suburbs). Probed 2026-05-02:
        # 372K parcels, TaxYear=2026 filter narrows to current roll.
        # Sample owner='CHASTAIN KRISTINA I', mailing='140 SHAMROCK IND
        # BLVD, TYRONE GA 30290' (absentee). TaxDist=25 = City of
        # Atlanta proper; rest is Sandy Springs / Roswell / College Park
        # / East Point. Pair with dekalb_atlanta for full metro.
        'platform': 'arcgis_mapserver',
        'service_description': 'Fulton County tax parcels (Atlanta core + suburbs)',
        'endpoint': 'https://services1.arcgis.com/AQDHTHDrZzfsFsB5/arcgis/rest/services/Tax_Parcels/FeatureServer/0',
        'where_clause': "Owner IS NOT NULL AND Owner <> '' AND TaxYear='2026'",
        'field_map': {
            'parcel_id': 'ParcelID',
            'owner_name': 'Owner',
            'address': 'Address',
            'owner_mailing_address': 'OwnerAddr1',
        },
        'state': 'GA',
        'source_tag': 'assessor:fulton_atlanta',
        'pagination_strategy': 'objectid',
        'default_city': 'Atlanta',
    },
    'dekalb_atlanta': {
        # V486 A4: DeKalb County GA (Atlanta-east suburbs). Probed
        # 2026-05-02: 245K parcels, LASTUPDATE max 2026-04-30.
        # Decatur / Brookhaven / Doraville / Tucker / Stone Mtn /
        # Lithonia. CITY field is populated per row, so the slug
        # derivation in stats_cache (LOWER + REPLACE) keys each row
        # into the right sub-city. Pair with fulton_atlanta for full
        # Atlanta-metro coverage.
        'platform': 'arcgis_mapserver',
        'service_description': 'DeKalb County GA parcels (Atlanta-east suburbs)',
        'endpoint': 'https://dcgis.dekalbcountyga.gov/hosted/rest/services/Parcels/MapServer/0',
        'where_clause': "OWNERNME1 IS NOT NULL AND OWNERNME1 <> '' AND SITEADDRESS IS NOT NULL",
        'field_map': {
            'parcel_id': 'PARCELID',
            'owner_name': 'OWNERNME1',
            'address': 'SITEADDRESS',
            'owner_mailing_address': 'PSTLADDRESS',
            'zip': 'ZIP',
            'city': 'CITY',
        },
        'state': 'GA',
        'source_tag': 'assessor:dekalb_atlanta',
        'pagination_strategy': 'objectid',
        'default_city': 'Decatur',
    },
    'stlouis_county_mo': {
        # V486 A5: St. Louis County MO (suburbs only). Probed 2026-05-02:
        # 401K parcels, TAXYR=2026 filter. NEW METRO — Clayton, U City,
        # Florissant, Chesterfield, Kirkwood, etc. MUNICIPALITY field is
        # populated ('UNINCORPORATED' or actual city); slug-derivation
        # picks the right sub-city. NOTE: independent City of St. Louis
        # (city='St. Louis', slug=st-louis-city) is a separate
        # jurisdiction NOT in this feed — they have their own city portal
        # which has no public REST per V258.
        'platform': 'arcgis_mapserver',
        'service_description': 'St. Louis County MO parcels (suburbs only)',
        'endpoint': 'https://maps.stlouisco.com/hosting/rest/services/Maps/AGS_Parcels/MapServer/0',
        'where_clause': "OWNER_NAME IS NOT NULL AND OWNER_NAME <> '' AND TAXYR='2026'",
        'field_map': {
            'parcel_id': 'LOCATOR',
            'owner_name': 'OWNER_NAME',
            'address': 'PROP_ADD',
            'zip': 'PROP_ZIP',
            'owner_mailing_address': 'OWN_ADD',
            'city': 'MUNICIPALITY',
        },
        'state': 'MO',
        'source_tag': 'assessor:stlouis_county_mo',
        'pagination_strategy': 'objectid',
        'default_city': 'St. Louis County',
    },
    'mecklenburg_charlotte': {
        # V485 (CODE_V485 A4): Mecklenburg County (Charlotte NC) Tax Parcel
        # Owners. Probed 2026-05-01: 426,294 county-wide parcels, ~250K when
        # filtered to municipality_desc='CHARLOTTE'. Daily updates. Full
        # mailing-address segmentation (txt_mailaddr1 / txt_city / txt_state
        # / txt_zipcode) — better than V474's other NC sources for
        # absentee-owner detection. Charlotte was permits-dead but had
        # 8K live violations + 0 owners; this jump promotes it Tier 2-3 →
        # Tier 4 (own + violations is the home-services / motivated-seller
        # combo). Sample row owner=BINNER ROBERT B JR, situs=2228 N BREVARD
        # ST, mailing=2228 N BREVARD ST CHARLOTTE NC 28206-3454.
        'platform': 'arcgis_mapserver',
        'service_description': 'Mecklenburg County (Charlotte) Tax Parcels',
        'endpoint': 'https://meckgis.mecklenburgcountync.gov/server/rest/services/TaxParcel_Camaownershipvalues/FeatureServer/0',
        'where_clause': "municipality_desc='CHARLOTTE' AND full_owner_name IS NOT NULL AND full_owner_name <> ''",
        'field_map': {
            'parcel_id': 'pid',
            'owner_name': 'full_owner_name',
            'address': 'situsaddress1',
            'owner_mailing_address': 'txt_mailaddr1',
            'city': 'municipality_desc',
        },
        'state': 'NC',
        'source_tag': 'assessor:mecklenburg_charlotte',
        'pagination_strategy': 'objectid',
        'default_city': 'Charlotte',
    },
    'hcad_houston': {
        # V485 (CODE_V485 A5): HCAD via City of Houston ArcGIS. Probed
        # 2026-05-01: 1.73M Harris County parcels; the
        # `Appraised_value_COH IS NOT NULL` filter narrows to ~500K
        # Houston-city-taxed parcels (the rest are unincorporated
        # Harris). MapServer (NOT FeatureServer) — the existing
        # _fetch_arcgis_page() handler already sends returnGeometry=false
        # which MapServer requires. Sample row OWNER='LIVING WATER
        # PLUMBING SERVICE CORP', ADDRESS='123 MAIN ST', mailing fields
        # split across Mail_Addr_2/Mail_City/Mail_State/Mail_Zip. Houston
        # was 0 owners + 83K violations + 0 contractors per CLAUDE.md;
        # 500K owners makes this the largest single-city assessor source
        # we have, second only to FL statewide.
        'platform': 'arcgis_mapserver',
        'service_description': 'HCAD via City of Houston (Houston-city parcels)',
        'endpoint': 'https://mycity2.houstontx.gov/pubgis02/rest/services/HoustonMap/Cadastral/MapServer/0',
        'where_clause': "Appraised_value_COH IS NOT NULL AND Appraised_value_COH <> '' AND OWNER IS NOT NULL AND OWNER <> ''",
        'field_map': {
            'parcel_id': 'TAX_ID',
            'owner_name': 'OWNER',
            'address': 'ADDRESS',
            'owner_mailing_address': 'OWNER_ADDRESS',
        },
        'state': 'TX',
        'source_tag': 'assessor:hcad_houston',
        'pagination_strategy': 'objectid',
        'default_city': 'Houston',
    },
    'orleans_new_orleans': {
        # V483b: Orleans Parish Assessor (New Orleans). Probed 2026-05-01:
        # apps/property3/MapServer/15 ("Property Information [Parcels]")
        # has 22 fields including OWNERNME1/OWNERNME2 (primary +
        # secondary owner), SITEADDRESS (format: "624 S ALEXANDER ST,
        # LA, 70119" — situs with state+ZIP appended inline), PSTLADDRESS
        # / PSTLCITY / PSTLSTATE / PSTLZIP5 (mailing — differs from
        # situs = absentee owner signal), PARCELID (GeoPIN), TAXBILLID,
        # USECD. maxRecordCount=1000.
        # The V483b spec quirk where=1=1 returns embedded error.code=400
        # doesn't bite us — the standard field-existence WHERE pattern
        # ("OWNERNME1 IS NOT NULL AND ..." with `<> ''`) is itself a
        # valid filter, so ArcGIS resultOffset pagination works without
        # needing an OBJECTID-based fallback. Promotes New Orleans Tier 4
        # → Tier 5 (already has permits + profiles + phones + violations;
        # owners was the missing leg).
        # verify_ssl=False because gis.nola.gov's certificate chain
        # doesn't validate against the Render container's CA bundle
        # (verified live during V483b deploy: SSLCertVerificationError).
        # Public parcel data, no creds in flight, JSON parsed verbatim —
        # acceptable trade-off for unblocking the import.
        'platform': 'arcgis_mapserver',
        'service_description': 'Orleans Parish Assessor Parcels',
        'endpoint': 'https://gis.nola.gov/arcgis/rest/services/apps/property3/MapServer/15',
        'where_clause': "OWNERNME1 IS NOT NULL AND OWNERNME1 <> '' AND SITEADDRESS IS NOT NULL AND SITEADDRESS <> ''",
        'verify_ssl': False,
        # V483b: this MapServer rejects any query containing resultOffset
        # with a generic 'code: 400 Failed to execute query'. The fix is
        # OBJECTID-based pagination — collect() appends `AND OBJECTID > N`
        # to the where clause and increments N to the max OBJECTID seen
        # in the previous page.
        'pagination_strategy': 'objectid',
        'field_map': {
            'owner_name': 'OWNERNME1',
            'address': 'SITEADDRESS',
            'owner_mailing_address': 'PSTLADDRESS',
            'parcel_id': 'PARCELID',
        },
        'state': 'LA',
        'source_tag': 'assessor:orleans_new_orleans',
        # SITEADDRESS has no situs city column — this is Orleans Parish
        # which is essentially coterminous with the city of New Orleans.
        # default_city ensures rows tag as 'New Orleans' so /permits/
        # new-orleans (slug=new-orleans) credits the owners after the
        # standard slug-derivation in stats_cache (REPLACE city ' '/'.'
        # → 'new-orleans').
        'default_city': 'New Orleans',
    },

    # V487 PR1 A1: City of St. Louis MO — 129K parcels, INDEPENDENT of
    # St. Louis County (V486 stlouis_county_mo covers only the suburbs).
    # Together they cover the full St. Louis metro. Probed live 2026-05-02.
    'saint_louis_city': {
        'platform': 'arcgis_mapserver',
        'service_description': 'City of St. Louis MO Parcels (independent jurisdiction)',
        'endpoint': 'https://stlgis.stlouis-mo.gov/arcgis/rest/services/public/STL_PUBLICMAP/MapServer/1',
        'where_clause': "OWNERNAME IS NOT NULL AND SITEADDR IS NOT NULL AND SITEADDR <> ''",
        'field_map': {
            'parcel_id': 'HANDLE',
            'owner_name': 'OWNERNAME',
            'owner_secondary': 'OWNERNAME2',
            'address': 'SITEADDR',
            'site_address_num': 'ADDRNUM',
            'site_zip': 'ZIP',
            'owner_mailing_address': 'OWNERADDR',
            'owner_mailing_city': 'OWNERCITY',
            'owner_mailing_state': 'OWNERSTATE',
            'owner_mailing_zip': 'OWNERZIP',
            'neighborhood_code': 'NBRHD',
            'num_buildings': 'NUMBLDGS',
            'building_year': 'BDG1YEAR',
            'assessed_value': 'ASMTTOTAL',
        },
        'state': 'MO',
        'source_tag': 'assessor:saint_louis_city',
        'pagination_strategy': 'objectid',
        'return_geometry': False,
        'default_city': 'St. Louis',
    },

    # V487 PR1 A2: Hamilton County TN / Chattanooga — 166K parcels.
    # Annual refresh (currently 2025-05 data). NEW METRO for owners.
    # Strip trailing whitespace from OWNERNAME1 (length=150 padded).
    # Skip first ~5K rows where OWNERNAME1='Update in Progress' (active
    # segregations) — handled by where_clause.
    'hamilton_chattanooga': {
        'platform': 'arcgis_mapserver',  # FeatureServer uses identical query API; the dispatcher only knows mapserver/carto/soda
        'service_description': 'Hamilton County TN (Chattanooga) Parcels',
        'endpoint': 'https://services5.arcgis.com/74bZbbuf05Ctvbzv/arcgis/rest/services/Chattanooga_Parcels/FeatureServer/0',
        'where_clause': "OWNERNAME1 IS NOT NULL AND OWNERNAME1 <> 'Update in Progress' AND ADDRESS IS NOT NULL AND ADDRESS <> ''",
        'field_map': {
            'parcel_id': 'PARCEL',
            'owner_name': 'OWNERNAME1',
            'owner_secondary': 'OWNERNAME2',
            'address': 'ADDRESS',
            'mailing_address_num': 'MASTNUM',
            'mailing_address_street': 'MASTNAME',
            'owner_mailing_city': 'MACITY',
            'owner_mailing_state': 'MASTATE',
            'owner_mailing_zip': 'MAZIP',
            'tax_map_no': 'TAX_MAP_NO',
            'neighborhood_code': 'NEIGHCODE',
            'land_value': 'LANDVALUE',
            'building_value': 'BUILDVALUE',
            'appraised_value': 'APPVALUE',
            'last_sale_date': 'SALE1DATE',
        },
        'state': 'TN',
        'source_tag': 'assessor:hamilton_chattanooga',
        'pagination_strategy': 'objectid',
        'return_geometry': False,
        'default_city': 'Chattanooga',
    },

    # V487 PR1 A3: Anchorage AK — 99K parcels, daily refresh, NEW METRO.
    # Anchorage permits are dead (V317 confirmed); pure owners-only metro
    # similar to Detroit/Atlanta/Toledo. Skip first ~5K rows with
    # 'UNITED AIRLINES INC' airport-parcel placeholders — handled by the
    # default address-non-empty filter.
    'anchorage_moa': {
        'platform': 'arcgis_mapserver',  # FeatureServer uses identical query API; the dispatcher only knows mapserver/carto/soda
        'service_description': 'Municipality of Anchorage Property Information',
        'endpoint': 'https://services2.arcgis.com/Ce3DhLRthdwbHlfF/arcgis/rest/services/PropertyInformation_Hosted/FeatureServer/0',
        'where_clause': "Owner_Name IS NOT NULL AND Parcel_Address IS NOT NULL AND Parcel_Address <> '' AND Parcel_Address <> '3 UNKNOWN ST'",
        'field_map': {
            'parcel_id': 'Parcel_ID',
            'owner_name': 'Owner_Name',
            'address': 'Parcel_Address',
            'city': 'GIS_Site_City',
            'site_zip': 'GIS_Site_Zipcode',
            'owner_mailing_address': 'Owner_Address',
            'owner_mailing_city': 'Owner_City',
            'owner_mailing_state': 'Owner_State',
            'owner_mailing_zip': 'Owner_Zip',
            'property_type': 'Property_Type',
            'property_class': 'Class',
            'land_use': 'Land_Use',
            'land_value': 'Appraised_Land_Value',
            'total_value': 'Appraised_Total_Value',
            'year_built': 'YearBuilt',
            'lot_size': 'Lot_Size',
            'zoning': 'Zoning_District',
            'deed_date': 'Deed_Date',
        },
        'state': 'AK',
        'source_tag': 'assessor:anchorage_moa',
        'pagination_strategy': 'objectid',
        'return_geometry': False,
        'default_city': 'Anchorage',
    },

    # V489 PART A1: Cobb County GA — 278K parcels, daily refresh.
    # Atlanta metro round 2. Pairs with V486 fulton_atlanta + dekalb_atlanta
    # to cover the full Atlanta metro for owners-only product.
    'cobb_atlanta': {
        'platform': 'arcgis_mapserver',
        'service_description': 'Cobb County GA Tax Assessors Daily Parcels',
        'endpoint': 'https://gis.cobbcounty.gov/gisserver/rest/services/tax/taxassessorsdaily/MapServer/0',
        'where_clause': "OWNER_NAM1 IS NOT NULL AND OWNER_NAM1 <> ''",
        'field_map': {
            'parcel_id': 'PIN',
            'owner_name': 'OWNER_NAM1',
            'owner_secondary': 'OWNER_NAM2',
            'address': 'SITUS_ADDR',
            'owner_mailing_address': 'OWNER_ADDR',
            'owner_mailing_city': 'OWNER_CITY',
            'owner_mailing_state': 'OWNER_STAT',
            'owner_mailing_zip': 'OWNER_ZIP',
            'fmv_total': 'FMV_TOTAL',
            'property_class': 'CLASS',
        },
        'state': 'GA',
        'source_tag': 'assessor:cobb_atlanta',
        'pagination_strategy': 'objectid',
        'return_geometry': False,
        'default_city': 'Marietta',
    },

    # V489 PART B1: Ramsey County MN — 164K parcels (Saint Paul + east TC).
    # Richest schema in the V489 batch (134 fields). MetroGIS Regional Parcel.
    'ramsey_saint_paul': {
        'platform': 'arcgis_mapserver',
        'service_description': 'Ramsey County MN MetroGIS Regional Parcels',
        'endpoint': 'https://maps.co.ramsey.mn.us/arcgis/rest/services/ParcelData/AttributedData/MapServer/3',
        'where_clause': "OwnerName IS NOT NULL AND OwnerName <> '' AND SiteAddress IS NOT NULL",
        'field_map': {
            'parcel_id': 'ParcelID',
            'owner_name': 'OwnerName',
            'owner_secondary': 'OwnerName1',
            'owner_tertiary': 'OwnerName2',
            'address': 'SiteAddress',
            'owner_mailing_address': 'OwnerAddress1',
            'owner_mailing_csz': 'OwnerCityStateZIP',
            'city': 'SiteCityName',
            'site_zip': 'SiteZIP5',
            'year_built': 'YearBuilt',
            'last_sale_date': 'LastSaleDate',
            'last_sale_price': 'SalePrice',
            'inspection_status': 'InspectionStatus',
        },
        'state': 'MN',
        'source_tag': 'assessor:ramsey_saint_paul',
        'pagination_strategy': 'objectid',
        'return_geometry': False,
        'default_city': 'Saint Paul',
    },

    # V489 PART B2: Anoka County MN — 140K parcels (north TC suburbs).
    # FeatureServer hit via the mapserver dispatcher (FeatureServer query
    # API is identical). Covers Coon Rapids, Blaine, Andover, Anoka,
    # Fridley, Ham Lake, etc.
    'anoka_mn': {
        'platform': 'arcgis_mapserver',
        'service_description': 'Anoka County MN Parcels',
        'endpoint': 'https://gisservices.co.anoka.mn.us/anoka_gis/rest/services/Parcels/FeatureServer/0',
        'where_clause': "OWNER IS NOT NULL AND OWNER <> '' AND LOC_ADDR IS NOT NULL AND LOC_ADDR <> ''",
        'field_map': {
            'parcel_id': 'PIN',
            'owner_name': 'OWNER',
            'owner_mailing_address': 'OWNERADDY',
            'owner_mailing_city': 'OWNERCITY',
            'owner_mailing_state': 'OWNERSTATE',
            'owner_mailing_zip': 'OWNERZIP',
            'taxpayer': 'TAXPAYER',
            'address': 'LOC_ADDR',
            'city': 'L_CITY',
            'market_value': 'MKT_VALUE',
            'last_sale_date': 'SALE_DATE',
            'year_built': 'YEAR_BUILT',
            'use_desc': 'USE_DESC',
        },
        'state': 'MN',
        'source_tag': 'assessor:anoka_mn',
        'pagination_strategy': 'objectid',
        'return_geometry': False,
        'default_city': 'Coon Rapids',
    },

    # V489 PART B3: Dakota County MN — 167K parcels (south TC suburbs).
    # MUNICIPALITY field IS populated — use it for city tagging directly.
    'dakota_mn': {
        'platform': 'arcgis_mapserver',
        'service_description': 'Dakota County MN Tax Parcels',
        'endpoint': 'https://gis2.co.dakota.mn.us/arcgis/rest/services/DCGIS_OL_PropertyInformation/MapServer/71',
        'where_clause': "FULLNAME IS NOT NULL AND FULLNAME <> ''",
        'field_map': {
            'parcel_id': 'TAXPIN',
            'owner_name': 'FULLNAME',
            'owner_secondary': 'JOINT_OWNER',
            'address': 'SITEADDRESS',
            'owner_mailing_address': 'OWN_ADD_L1',
            'owner_mailing_address_2': 'OWN_ADD_L2',
            'owner_mailing_csz': 'P_CITY_ST_ZIP',
            'city': 'MUNICIPALITY',
            'total_value': 'TOTALVAL',
            'year_built': 'YEAR_BUILT',
            'last_sale_date': 'SALE_DATE',
            'use1_desc': 'USE1_DESC',
            'update_date': 'Update_Date',
        },
        'state': 'MN',
        'source_tag': 'assessor:dakota_mn',
        'pagination_strategy': 'objectid',
        'return_geometry': False,
        'default_city': 'Apple Valley',
    },

    # V489 PART C1: Oklahoma County OK — 337K parcels (NEW STATE/METRO).
    # OKC + Edmond + Midwest City + Del City + Bethany + others.
    # Note: owner_mailing_city maps to source field 'city' (sic) — that's
    # how the source labels the mailing city; situs city is 'locationcity'.
    'oklahoma_county_okc': {
        'platform': 'arcgis_mapserver',
        'service_description': 'Oklahoma County OK Tax Parcels (Public View)',
        'endpoint': 'https://services8.arcgis.com/euhkr1dAJeQBIjV0/arcgis/rest/services/TaxParcelsPublics_view/FeatureServer/0',
        'where_clause': "name1 IS NOT NULL AND name1 <> '' AND location IS NOT NULL AND location <> ''",
        'field_map': {
            'parcel_id': 'accountno',
            'parcel_pin': 'pin',
            'owner_name': 'name1',
            'owner_secondary': 'name2',
            'owner_tertiary': 'name3',
            'address': 'location',
            'city': 'locationcity',
            'owner_mailing_address': 'mailingaddress1',
            'owner_mailing_city': 'city',
            'owner_mailing_state': 'state',
            'owner_mailing_zip': 'zipcode',
            'market_value': 'currentmarket',
            'last_sale_date': 'saledate',
            'last_sale_price': 'SalePrice',
            'neighborhood': 'nbhd',
            'subdivision': 'subname',
        },
        'state': 'OK',
        'source_tag': 'assessor:oklahoma_county_okc',
        'pagination_strategy': 'objectid',
        'return_geometry': False,
        'default_city': 'Oklahoma City',
    },

    # V489 PART C2: Tulsa County OK — 284K parcels.
    # LoadDate 2025-03-26 — STATIC reference (~1yr stale at V489 ship). Owner
    # names change much slower than permit data, so usable as a lookup pair
    # but flagged static so the city page UX shows "data as of Mar 2025".
    # Hosted by INCOG (Indian Nations Council of Governments), not the
    # county directly. BusinessName field separate from Owner — useful for
    # filtering commercial parcels.
    'tulsa_county_ok': {
        'platform': 'arcgis_mapserver',
        'service_description': 'Tulsa County OK Parcels (via INCOG, ~1yr stale)',
        'endpoint': 'https://map11.incog.org/arcgis11wa/rest/services/Parcels_TulsaCo/FeatureServer/0',
        'where_clause': "Owner IS NOT NULL AND Owner <> '' AND PropertyAddress IS NOT NULL AND PropertyAddress <> ''",
        'field_map': {
            'parcel_id': 'ACCT_NUM',
            'owner_name': 'Owner',
            'owner_secondary': 'Name1',
            'owner_tertiary': 'Name2',
            'address': 'PropertyAddress',
            'city': 'PropertyCity',
            'site_zip': 'PropertyZIP',
            'owner_mailing_address': 'Address1',
            'owner_mailing_address_2': 'Address2',
            'owner_mailing_city': 'City',
            'owner_mailing_state': 'State',
            'owner_mailing_zip': 'ZIPCode',
            'business_name': 'BusinessName',
            'use_code': 'UseCode',
            'year_built': 'YearBuilt',
        },
        'state': 'OK',
        'source_tag': 'assessor:tulsa_county_ok',
        'pagination_strategy': 'objectid',
        'return_geometry': False,
        'default_city': 'Tulsa',
        'freshness': 'static',  # ~1yr stale; UX should label accordingly
    },

    # V490 PART A1: Denton County TX — 305K parcels (DFW north).
    # NEW DFW-NORTH coverage: Denton, Lewisville, Flower Mound, The Colony,
    # Krugerville. CITY field IS populated for direct tagging via
    # fix-property-owner-cities.
    'denton_dfw': {
        'platform': 'arcgis_mapserver',
        'service_description': 'Denton County TX Parcels',
        'endpoint': 'https://gis.dentoncounty.gov/arcgis/rest/services/Parcels/MapServer/0',
        'where_clause': "OWNER_NAME IS NOT NULL AND OWNER_NAME <> ''",
        'field_map': {
            'parcel_id': 'prop_id',
            'owner_name': 'OWNER_NAME',
            'address': 'SITUS',
            'address_line_1': 'ADDR_LINE1',
            'city': 'CITY',
            'site_zip': 'ZIP',
            'living_area': 'LIVINGAREA',
            'year_built': 'YR_BLT',
            'land_sqft': 'LAND_SQFT',
        },
        'state': 'TX',
        'source_tag': 'assessor:denton_dfw',
        'pagination_strategy': 'objectid',
        'return_geometry': False,
        'default_city': 'Denton',
    },

    # V490 PART A2: Tarrant County TX full county-wide view — REPLACES
    # the city-only tarrant_fortworth source. 744K total (715K Tarrant
    # + 29K ETJ slice from Denton/Johnson/Parker/Wise that the where_clause
    # filters out). After import, run fix-property-owner-cities to retag
    # from default 'Fort Worth' to actual CITYNAME (Arlington, Grand
    # Prairie, Mansfield, Bedford, Hurst, Euless, Haltom City, North
    # Richland Hills, etc). Post-deploy admin step: deactivate
    # tarrant_fortworth in prod_cities to prevent duplicate writes.
    'tarrant_county_full': {
        'platform': 'arcgis_mapserver',
        'service_description': 'Tarrant County TX Parcels (full county incl ETJ filter)',
        'endpoint': 'https://services5.arcgis.com/3ddLCBXe1bRt7mzj/arcgis/rest/services/Parcels_Public_Vview/FeatureServer/0',
        'where_clause': "COUNTYNAME='Tarrant' AND OWNER_NAME IS NOT NULL AND OWNER_NAME <> ''",
        'field_map': {
            'parcel_id': 'TAXPIN',
            'account': 'ACCOUNT',
            'owner_name': 'OWNER_NAME',
            'owner_mailing_address': 'OWNER_ADDRESS',
            'owner_mailing_csz': 'OWNER_CITY_ST',
            'owner_mailing_zip': 'OWNER_ZIP_CODE',
            'address': 'SITUS_ADDR',
            'city': 'CITYNAME',
            'county': 'COUNTYNAME',
            'year_built': 'YR_BUILT',
            'land_acres': 'LAND_ACRE',
            'appraised_value': 'APPRAISED_VALUE',
            'market_value': 'MARKET_VALUE',
            'last_sale_date': 'DEED_DATE',
            'property_class': 'PROPERTY_CLASS_CODE',
        },
        'state': 'TX',
        'source_tag': 'assessor:tarrant_county_full',
        'pagination_strategy': 'objectid',
        'return_geometry': False,
        'default_city': 'Fort Worth',
    },

    # V490 PART B1: Hamilton County IN — 153K parcels (Indianapolis north).
    # NEW Indy-north suburb coverage: Carmel, Fishers, Noblesville,
    # Westfield, Zionsville, Cicero, Sheridan. AVTAXYR=2026, EXPORTDATE
    # 2026-04. Pairs with V486 marion_indianapolis for the full Indy metro.
    'hamilton_indianapolis': {
        'platform': 'arcgis_mapserver',
        'service_description': 'Hamilton County IN Parcels (Indianapolis north suburbs)',
        'endpoint': 'https://gis1.hamiltoncounty.in.gov/arcgis/rest/services/HamCoParcelsPublic/FeatureServer/0',
        'where_clause': "OWNNAME IS NOT NULL AND OWNNAME <> '' AND LOCADDRESS IS NOT NULL AND LOCADDRESS <> ''",
        'field_map': {
            'parcel_id': 'STPRCLNO',
            'owner_name': 'OWNNAME',
            'owner_secondary': 'DEEDEDOWNR',
            'owner_mailing_address': 'OWNADDRESS',
            'owner_mailing_city': 'OWNCITY',
            'owner_mailing_state': 'OWNSTATE',
            'owner_mailing_zip': 'OWNZIP',
            'address': 'LOCADDRESS',
            'city': 'LOCCITY',
            'site_zip': 'LOCZIP',
            'total_value': 'AVTOTGROSS',
            'tax_year': 'AVTAXYR',
            'year_built': 'year_built',
            'sqft_residential': 'sq_ft_res',
            'sqft_commercial': 'sq_ft_comm',
            'property_class': 'PROPCLASS',
            'property_use': 'PROPUSE',
            'last_transfer_date': 'LSTXFRDATE',
        },
        'state': 'IN',
        'source_tag': 'assessor:hamilton_indianapolis',
        'pagination_strategy': 'objectid',
        'return_geometry': False,
        'default_city': 'Carmel',
    },

    # V490 PART C1: Lake County OH — 115K parcels (Cleveland east).
    # NEW Cleveland-east coverage: Mentor, Willoughby, Painesville,
    # Eastlake, Wickliffe, Kirtland, Madison, Concord, Perry, Leroy.
    # G_FULLCITY format is "WILLOUGHBY, OH 44094" — needs a comma split
    # at retag time to extract clean city name.
    'lake_cleveland_east': {
        'platform': 'arcgis_mapserver',
        'service_description': 'Lake County OH Auditor Parcels (Cleveland east)',
        'endpoint': 'https://gis.lakecountyohio.gov/arcgis/rest/services/Auditor/Parcels_AppraisedValues_Publish/FeatureServer/0',
        'where_clause': "A_OWNER_NAME IS NOT NULL AND A_OWNER_NAME <> '' AND G_FULLADDRESS IS NOT NULL",
        'field_map': {
            'parcel_id': 'PIN',
            'owner_name': 'A_OWNER_NAME',
            'taxpayer_name': 'A_TAXP_NAME',
            'address': 'G_FULLADDRESS',
            'city': 'G_FULLCITY',
            'owner_mailing_house_no': 'A_O_HOUSENO',
            'owner_mailing_street': 'A_O_ST_NAME',
            'owner_mailing_city': 'A_O_CITY',
            'owner_mailing_state': 'A_O_STATE',
            'owner_mailing_zip': 'A_O_ZIPCODE',
            'total_value': 'A_VAL_TOTAL',
            'land_value': 'A_VAL_LAND',
            'building_value': 'A_VAL_BLDG',
            'year_built': 'A_YEAR_BUILT',
            'last_sale_date': 'A_SALE_DATE',
            'last_sale_amount': 'A_SALE_AMOUNT',
            'legal_description': 'A_LEGAL_DESC',
        },
        'state': 'OH',
        'source_tag': 'assessor:lake_cleveland_east',
        'pagination_strategy': 'objectid',
        'return_geometry': False,
        'default_city': 'Mentor',
    },

    # V490 PART C2: Lorain County OH — 172K parcels (Cleveland west).
    # NEW Cleveland-west coverage: Lorain, Elyria, Avon, North Ridgeville,
    # Sheffield, Huntington Twp.
    # CAVEAT: layer has no situs street address (PPAddress empty length=1).
    # Address-matching to permits requires PIN-based join only.
    'lorain_cleveland_west': {
        'platform': 'arcgis_mapserver',
        'service_description': 'Lorain County OH Parcels (Cleveland west)',
        'endpoint': 'https://services1.arcgis.com/vGBb7WYV10mOJRNM/arcgis/rest/services/parcel_joined/FeatureServer/0',
        'where_clause': "PPOwner IS NOT NULL AND PPOwner <> ''",
        'field_map': {
            'parcel_id': 'Parcel',
            'owner_name': 'PPOwner',
            'city': 'PPComm',
            'taxpayer_name': 'TaxPayerName',
            'owner_mailing_address': 'TaxPayerStreet',
            'owner_mailing_city': 'TaxPayerCity',
            'owner_mailing_state': 'TaxPayerState',
            'owner_mailing_zip': 'TaxPayerZip',
            'year_built': 'PPYearBuilt',
            'living_area': 'PPLivingArea',
            'bedrooms': 'PPBedrooms',
            'fullbaths': 'PPFullbaths',
            'land_value': 'PPLandValue',
            'improvement_value': 'PPImprValue',
            'total_value': 'PPTotalValue',
            'last_sale_date': 'PPSaleDate',
            'last_sale_amount': 'PPAmount',
            'school': 'PPSchool',
            'class_code': 'PPClassCode',
        },
        'state': 'OH',
        'source_tag': 'assessor:lorain_cleveland_west',
        'pagination_strategy': 'objectid',
        'return_geometry': False,
        'default_city': 'Lorain',
    },

    # V491 PART A1: Jackson County MO — 73K parcels (KC east metro).
    # NEW STATE for the owner pipeline. Hosted by City of Independence as
    # a republished county-wide view. tax_year is "2025" string. ~73K is
    # partial Jackson coverage (full county pop ~717K) — most likely east-
    # Jackson + Independence focus. Don't assume full KCMO coverage.
    'jackson_county_mo': {
        'platform': 'arcgis_mapserver',
        'service_description': 'Jackson County MO Parcels (KC east metro)',
        'endpoint': 'https://services.arcgis.com/sbDzK061dd6DNPHv/arcgis/rest/services/COI_Parcels_2_view/FeatureServer/0',
        'where_clause': "owner IS NOT NULL AND owner <> '' AND SitusAddress IS NOT NULL AND SitusAddress <> ''",
        'field_map': {
            'owner_name': 'owner',
            'owner_mailing_address': 'owneraddress',
            'owner_mailing_city': 'ownercity',
            'owner_mailing_state': 'ownerstate',
            'owner_mailing_zip': 'ownerzipcode',
            'address': 'SitusAddress',
            'city': 'SitusCity',
            'site_state': 'SitusState',
            'site_zip': 'SitusZipCode',
            'year_built': 'year_built',
            'tax_year': 'tax_year',
            'assessed_value': 'AssessedValue',
            'market_value': 'Market_Value_Total',
        },
        'state': 'MO',
        'source_tag': 'assessor:jackson_county_mo',
        'pagination_strategy': 'objectid',
        'return_geometry': False,
        'default_city': 'Independence',
    },

    # V491 PART B1: Geauga County OH — 102K parcels (Cleveland far east).
    # Covers Chardon, Bainbridge, Burton, Chester Township, Auburn Township.
    # CAVEAT: Sale_Date is string "mm-dd-yyyy" (not epoch ms) — adapter
    # parsing required if Sale_Date is consumed downstream.
    'geauga_cleveland_far_east': {
        'platform': 'arcgis_mapserver',
        'service_description': 'Geauga County OH Parcels (Cleveland far east)',
        'endpoint': 'https://services3.arcgis.com/otmFGc3Z1CITN3V3/arcgis/rest/services/Parcel_Layer/FeatureServer/0',
        'where_clause': "Oname1 IS NOT NULL AND Oname1 <> '' AND LOCATION_A IS NOT NULL",
        'field_map': {
            'parcel_id': 'PARCEL_ID',
            'owner_name': 'Oname1',
            'owner_secondary': 'Oname2',
            'address': 'LOCATION_A',
            'city': 'LOCATION_C',
            'site_state': 'LOCATION_S',
            'site_zip': 'LOCATION_Z',
            'last_sale_date_str': 'Sale_Date',
            'last_sale_year': 'Sale_Year',
            'last_sale_amount': 'Sale_Amt',
            'acres': 'Acres_Num',
            'deed_number': 'Deed_Num',
        },
        'state': 'OH',
        'source_tag': 'assessor:geauga_cleveland_far_east',
        'pagination_strategy': 'objectid',
        'return_geometry': False,
        'default_city': 'Chardon',
    },

    # V491 PART B2: Medina County OH — 84K parcels (Cleveland south).
    # USE THE v2 LAYER. The non-v2 Medina_Parcel_Layer is frozen 2024-04.
    # Daily refresh confirmed (lastEditDate updated 2026-05-03).
    'medina_cleveland_south': {
        'platform': 'arcgis_mapserver',
        'service_description': 'Medina County OH Parcels (Cleveland south, daily)',
        'endpoint': 'https://services5.arcgis.com/m37BbrYBtVXq1nb8/arcgis/rest/services/Medina_Parcel_Layer_version_2/FeatureServer/0',
        'where_clause': "Owner IS NOT NULL AND Owner <> '' AND Address IS NOT NULL AND Address <> ''",
        'field_map': {
            'parcel_id': 'ParcelPIN',
            'identification_number': 'IdentificationNumber',
            'owner_name': 'Owner',
            'address': 'Address',
            'city': 'City',
            'site_zip': 'ZIP',
            'class_code': 'ClassCode',
            'classification': 'Classification',
            'acres': 'Acres',
            'total_value': 'Total_Market_Value',
            'last_sale_date': 'SaleDate',
            'last_sale_amount': 'SaleAmount',
            'current_owed_taxes': 'CurrentOwedTaxes',
            'longitude': 'Longitude',
            'latitude': 'Latitude',
        },
        'state': 'OH',
        'source_tag': 'assessor:medina_cleveland_south',
        'pagination_strategy': 'objectid',
        'return_geometry': False,
        'default_city': 'Medina',
    },

    # V491 PART C1: Pinellas County FL — 438K parcels (St Pete + Clearwater).
    # SUPERSEDES the V476/V487 saint-petersburg dead-end (2,537 mostly
    # homeowner profiles via the dead city-level Click2Gov path).
    # ADDRESS_ZIP_CITY format: "550 ALT 19 PALM HARBOR, FL 34683" — needs
    # parsing if you want to split situs city/zip explicitly.
    'pinellas_county_fl': {
        'platform': 'arcgis_mapserver',
        'service_description': 'Pinellas County FL Property Appraiser Parcels',
        'endpoint': 'https://egis.pinellas.gov/pcpagis/rest/services/Pcpao_gov/PropertySearch_A/MapServer/0',
        'where_clause': "OWNER1 IS NOT NULL AND OWNER1 <> ''",
        'field_map': {
            'parcel_id': 'PCPA_UID',
            'internal_strap': 'INTERNAL_STRAP',
            'display_strap': 'DISPLAY_STRAP',
            'owner_name': 'OWNER1',
            'owner_secondary': 'OWNER2',
            'address': 'SITE_ADDRESS',
            'site_address_zip_city': 'ADDRESS_ZIP_CITY',
            'subdivision': 'SUBDIVISION',
            'longitude': 'LONGITUDE',
            'latitude': 'LATITUDE',
        },
        'state': 'FL',
        'source_tag': 'assessor:pinellas_county_fl',
        # NOTE: 'objectid' pagination fails — Pinellas's OBJECTID column
        # rejects `OBJECTID > N` with HTTP 400. PCPA_UID does work but
        # the dispatcher hardcodes 'OBJECTID' as the column name. Falling
        # back to resultOffset (default) which Pinellas accepts.
        'return_geometry': False,
        'default_city': 'St. Petersburg',
    },

    # V491 PART C2: Pasco County FL — 322K parcels (Tampa north metro).
    # MUST USE LAYER 3 ("Parcels Clickable Info"). Layer 0 = parcel boundary
    # only, no owner. NAD_NAME_1 sometimes contains gov entities like
    # "PASCO COUNTY" — consider downstream filtering. PHYS_* is situs;
    # NAD_* is taxpayer mailing.
    'pasco_county_fl': {
        'platform': 'arcgis_mapserver',
        'service_description': 'Pasco County FL Property Appraiser Parcels',
        'endpoint': 'https://maps.pascopa.com/arcgis/rest/services/Parcels/MapServer/3',
        'where_clause': "NAD_NAME_1 IS NOT NULL AND NAD_NAME_1 <> '' AND PHYS_STREET IS NOT NULL",
        'field_map': {
            'parcel_id': 'ParcelID',
            'owner_name': 'NAD_NAME_1',
            'owner_secondary': 'NAD_NAME_2',
            'owner_mailing_address': 'NAD_ADD_1',
            'owner_mailing_address_2': 'NAD_ADD_2',
            'owner_mailing_city': 'NAD_CITY',
            'owner_mailing_state': 'NAD_STATE',
            'owner_mailing_zip': 'NAD_ZIP',
            'address': 'PHYS_STREET',
            'city': 'PHYS_CITY',
            'site_state': 'PHYS_STATE',
            'site_zip': 'PHYS_ZIP',
            'appraised_value': 'VAL_APPR',
            'last_sale_year': 'SALE_YEAR',
            'last_sale_amount': 'SALE_AMT',
            'detail_url': 'URL',
        },
        'state': 'FL',
        'source_tag': 'assessor:pasco_county_fl',
        'pagination_strategy': 'objectid',
        'return_geometry': False,
        'default_city': 'New Port Richey',
    },

    # V491 PART D1: Bexar County TX — 711K parcels (full county replaces
    # 5K city-only bexar source — 142x lift). After deploy + verification,
    # deactivate the old bexar source via prod_cities UPDATE.
    # CRITICAL: Owner and AddrLn1 contain literal 'NULL' string (not real
    # null) for many rows — where_clause filters explicitly.
    'bexar_county_full': {
        'platform': 'arcgis_mapserver',
        'service_description': 'Bexar County TX Parcels (full county incl all SA suburbs)',
        'endpoint': 'https://maps.bexar.org/arcgis/rest/services/Parcels/MapServer/0',
        'where_clause': "Owner IS NOT NULL AND Owner <> '' AND Owner <> 'NULL' AND AddrLn1 <> 'NULL'",
        'field_map': {
            'parcel_id': 'PropID',
            'account_number': 'AcctNumb',
            'owner_name': 'Owner',
            'dba': 'DBA',
            'address': 'Situs',
            'owner_mailing_address': 'AddrLn1',
            'owner_mailing_address_2': 'AddrLn2',
            'owner_mailing_address_3': 'AddrLn3',
            'owner_mailing_city': 'AddrCity',
            'owner_mailing_state': 'AddrSt',
            'owner_mailing_zip': 'Zip',
            'land_value': 'LandVal',
            'improvement_value': 'ImprVal',
            'total_value': 'TotVal',
            'year_built': 'YrBlt',
            'acres': 'Acres',
            'roll': 'Roll',
        },
        'state': 'TX',
        'source_tag': 'assessor:bexar_county_full',
        'pagination_strategy': 'objectid',
        'return_geometry': False,
        'default_city': 'San Antonio',
    },

    # V491 PART D2: Travis County TX — 343K parcels (full Austin metro,
    # replaces 55K travis_austin source — 6x lift). After deploy +
    # verification, deactivate the old travis_austin source.
    # NOTE: source field is py_owner_name (NOT owner_name) — common trap.
    # py_address often has full city/state ("11502 TANGLEBRIAR TRL AUSTIN
    # TX 78750") — split downstream as needed.
    'travis_county_full': {
        'platform': 'arcgis_mapserver',
        'service_description': 'Travis County TX TCAD Parcels (full Austin metro)',
        'endpoint': 'https://services1.arcgis.com/HGcSYZ5bvjRswoCb/arcgis/rest/services/TCAD/FeatureServer/0',
        'where_clause': "py_owner_name IS NOT NULL AND py_owner_name <> '' AND situs_address IS NOT NULL",
        'field_map': {
            'parcel_id': 'prop_id',
            'pid_10': 'PID_10',
            'geo_id': 'geo_id',
            'owner_name': 'py_owner_name',
            'owner_id': 'py_owner_id',
            'address': 'situs_address',
            'owner_mailing_address': 'py_address',
            'taxing_entities': 'entities',
            'market_value': 'market_value',
            'appraised_value': 'appraised_val',
            'last_sale_date': 'deed_date',
            'deed_number': 'deed_num',
            'gis_acres': 'GIS_acres',
        },
        'state': 'TX',
        'source_tag': 'assessor:travis_county_full',
        # NOTE: 'objectid' pagination fails — TCAD has no queryable
        # OBJECTID column ("'Invalid field: OBJECTID' parameter is invalid").
        # Falling back to resultOffset (default) which the FeatureServer
        # supports. The unique field is prop_id but the dispatcher hardcodes
        # 'OBJECTID' for objectid pagination.
        'return_geometry': False,
        'default_city': 'Austin',
    },

    # V489 PART D: Cuyahoga County OH (full county-wide) — replaces the
    # city-only cuyahoga_cleveland source. 484K total, ~340K populated
    # after the where_clause excludes blank-geometry placeholders.
    # Includes foreclosure_flag for motivated-seller filtering.
    # parcel_city populated with CLEVELAND, LAKEWOOD, PARMA, BEACHWOOD,
    # SHAKER HEIGHTS, WESTLAKE, NORTH OLMSTED, CLEVELAND HEIGHTS,
    # STRONGSVILLE, etc. After import, deactivate cuyahoga_cleveland to
    # avoid duplicates (manual prod_cities update OR drop from registry).
    'cuyahoga_county_full': {
        'platform': 'arcgis_mapserver',
        'service_description': 'Cuyahoga County OH EPV Parcels (Cleveland + 58 suburbs)',
        'endpoint': 'https://gis.cuyahogacounty.us/server/rest/services/CCFO/EPV_Prod/FeatureServer/2',
        'where_clause': "parcel_owner IS NOT NULL AND parcel_owner <> '' AND par_addr_all IS NOT NULL",
        'field_map': {
            'parcel_id': 'parcel_id',
            'owner_name': 'parcel_owner',
            'owner_secondary': 'second_owner',
            'address': 'par_addr_all',
            'street': 'parcel_street',
            'city': 'parcel_city',
            'site_zip': 'parcel_zip',
            'owner_mailing_name': 'mail_name',
            'owner_mailing_address': 'mail_addr_street',
            'owner_mailing_city': 'mail_city',
            'owner_mailing_state': 'mail_state',
            'owner_mailing_zip': 'mail_zip',
            'last_transfer_date': 'last_transfer_date',
            'last_sale_amount': 'last_sales_amount',
            'market_total': 'tax_market_total',
            'prop_class': 'prop_class_desc',
            'school_descr': 'school_descr',
            'zoning_code': 'zoning_code',
            'condo_complex_id': 'condo_complex_id',
            'lender': 'lender',
            'tax_district_desc': 'tax_dist_desc',
            'foreclosure_flag': 'foreclosure_flag',
            'update_date': 'update_date',
        },
        'state': 'OH',
        'source_tag': 'assessor:cuyahoga_county_full',
        'pagination_strategy': 'objectid',
        'return_geometry': False,
        'default_city': 'Cleveland',
    },

    # ============================================================
    # V509 BEAST — 11 new assessor sources
    # ============================================================
    'nys_tax_parcel_centroid': {
        # V509 #6: HEADLINE WIN — NYS statewide centroid feed.
        # 5,505,719 records covering every NY county with
        # PRIMARY_OWNER + COUNTY_NAME + MUNI_NAME + PARCEL_ADDR +
        # MAIL_ADDR. Single source unlocks Yonkers/Rochester/Albany/
        # Schenectady/White Plains/New Rochelle/Mt Vernon plus all
        # 5 NYC boroughs. Filter by COUNTY_NAME or MUNI_NAME at
        # insert time per maricopa_secondary V474 pattern.
        'platform': 'arcgis_featureserver',
        'service_description': 'NYS ITS Tax Parcel Centroid Points (statewide)',
        'endpoint': 'https://gisservices.its.ny.gov/arcgis/rest/services/NYS_Tax_Parcel_Centroid_Points/FeatureServer/0',
        'where_clause': "PRIMARY_OWNER IS NOT NULL AND PARCEL_ADDR IS NOT NULL AND PARCEL_ADDR <> ''",
        'field_map': {
            'owner_name': 'PRIMARY_OWNER',
            'address': 'PARCEL_ADDR',
            'city': 'MUNI_NAME',
            'county': 'COUNTY_NAME',
            'owner_mailing_address': 'MAIL_ADDR',
            'owner_mailing_city': 'MAIL_CITY',
            'parcel_id': 'SWIS',
        },
        'state': 'NY',
        'source_tag': 'assessor:nys_tax_parcel_centroid',
        'pagination_strategy': 'objectid',
        'return_geometry': False,
        # 5.5M too big for one batch; chunk by county at insert time
        'chunk_by': 'COUNTY_NAME',
    },
    'ct_statewide_cama': {
        # V509 #7: Connecticut statewide CAMA + Parcel Layer.
        # 1,282,833 records covering Hartford/Bridgeport/New Haven/
        # Stamford/Waterbury/Norwalk/Danbury + 13+ smaller CT cities.
        # CT has no bulk state license DB so phone enrichment is
        # DDG-only, but owners alone enable SEO content.
        'platform': 'arcgis_featureserver',
        'service_description': 'CT Statewide CAMA + Parcel Layer 2025',
        'endpoint': 'https://services3.arcgis.com/3FL1kr7L4LvwA2Kb/arcgis/rest/services/Connecticut_CAMA_and_Parcel_Layer/FeatureServer/0',
        'where_clause': "Owner IS NOT NULL AND Owner <> '' AND Full_Address IS NOT NULL",
        'field_map': {
            'owner_name': 'Owner',
            'address': 'Full_Address',
            'city': 'Town_Name',
            'owner_mailing_address': 'Mailing_Address',
            'owner_mailing_city': 'Mailing_City',
            'owner_mailing_state': 'Mailing_State',
            'owner_mailing_zip': 'Mailing_Zip',
        },
        'state': 'CT',
        'source_tag': 'assessor:ct_statewide_cama',
        'pagination_strategy': 'objectid',
        'return_geometry': False,
        'chunk_by': 'Town_Name',
    },
    'fl_statewide_parcels': {
        # V509 #8: Florida statewide parcel feed (DOR aggregated).
        # 10,834,415 records covering EVERY FL county. Pairs with
        # V507's FL_Statewide and unlocks 30+ FL secondary cities
        # (Pensacola/Tallahassee/Sarasota/Naples/Daytona/Lakeland/
        # Brevard/Pinellas/Pasco/Volusia/etc.) at once. MUST chunk
        # by CountyName per FIX #23 — one chunked import per county
        # with resultRecordCount<=2000 — to avoid OOM.
        'platform': 'arcgis_featureserver',
        'service_description': 'FL Statewide Parcels (DOR)',
        'endpoint': 'https://services5.arcgis.com/GcvM6vDlR2gM4x31/arcgis/rest/services/FL_Parcels/FeatureServer/0',
        'where_clause': "OWN_NAME IS NOT NULL AND OWN_NAME <> ''",
        'field_map': {
            'owner_name': 'OWN_NAME',
            'owner_mailing_address': 'OWN_ADDR1',
            'owner_mailing_city': 'OWN_CITY',
            'owner_mailing_state': 'OWN_STATE',
            'owner_mailing_zip': 'OWN_ZIPCD',
            'county': 'CountyName',
            'parcel_id': 'PARCEL_ID',
        },
        'state': 'FL',
        'source_tag': 'assessor:fl_statewide_parcels',
        'pagination_strategy': 'objectid',
        'return_geometry': False,
        'chunk_by': 'CountyName',
    },
    'wcad_williamson_tx': {
        # V509 #9: Williamson County TX (WCAD) — Austin metro NE
        # suburbs: Round Rock, Cedar Park, Leander, Hutto, Georgetown,
        # Liberty Hill, Taylor.
        'platform': 'arcgis_featureserver',
        'service_description': 'Williamson County (WCAD) Tax Parcels',
        'endpoint': 'https://services1.arcgis.com/Xff0bbfp6vwIWmlU/arcgis/rest/services/WCAD_Tax_Parcels/FeatureServer/0',
        'where_clause': "OWNERNME1 IS NOT NULL AND OWNERNME1 <> ''",
        'field_map': {
            'owner_name': 'OWNERNME1',
            'address': 'SITEADDRESS',
            'owner_mailing_address': 'PSTLADDRESS',
            'owner_mailing_city': 'PSTLCITY',
            'owner_mailing_state': 'PSTLSTATE',
            'owner_mailing_zip': 'PSTLZIP5',
        },
        'state': 'TX',
        'source_tag': 'assessor:wcad_williamson_tx',
        'pagination_strategy': 'objectid',
        'return_geometry': False,
    },
    'hayscad_hays_tx': {
        # V509 #10: Hays County TX — San Marcos, Wimberley, Buda,
        # Kyle (Austin metro SW suburbs).
        'platform': 'arcgis_featureserver',
        'service_description': 'Hays County (HaysCAD) Parcels',
        'endpoint': 'https://services6.arcgis.com/j94FvPaik4etwHFk/arcgis/rest/services/HaysCADWebService1/FeatureServer/0',
        'where_clause': "file_as_name IS NOT NULL AND file_as_name <> ''",
        'field_map': {
            'owner_name': 'file_as_name',
            'address': 'situs_street',
            'city': 'situs_city',
            'owner_mailing_address': 'addr_line1',
            'owner_mailing_city': 'addr_city',
            'owner_mailing_state': 'addr_state',
        },
        'state': 'TX',
        'source_tag': 'assessor:hayscad_hays_tx',
        'pagination_strategy': 'objectid',
        'return_geometry': False,
    },
    'fbcad_fort_bend_tx': {
        # V509 #11: Fort Bend County TX — Sugar Land, Missouri City,
        # Richmond, Rosenberg (Houston metro SW suburbs).
        'platform': 'arcgis_featureserver',
        'service_description': 'Fort Bend County (FBCAD) Parcels',
        'endpoint': 'https://services2.arcgis.com/D4saGHECICkCeoJm/arcgis/rest/services/FBCAD_Public_Data/FeatureServer/0',
        'where_clause': "OWNERNAME IS NOT NULL AND OWNERNAME <> ''",
        'field_map': {
            'owner_name': 'OWNERNAME',
            'address': 'SITUS',
            'owner_mailing_address': 'OADDR1',
            'owner_mailing_city': 'OWNERCITY',
            'owner_mailing_state': 'OWNERSTATE',
            'owner_mailing_zip': 'OWNERZIP',
        },
        'state': 'TX',
        'source_tag': 'assessor:fbcad_fort_bend_tx',
        'pagination_strategy': 'objectid',
        'return_geometry': False,
    },
    'denton_cad_tx': {
        # V509 #12: Denton County TX — Denton, Lewisville, Flower
        # Mound, Frisco-north, Krugerville. NCTCOG neighbor of
        # Collin (V474 wired).
        'platform': 'arcgis_mapserver',
        'service_description': 'Denton County (Denton CAD) Parcels',
        'endpoint': 'https://gis.dentoncounty.gov/arcgis/rest/services/Parcels_FC/MapServer/0',
        'where_clause': "name IS NOT NULL AND name <> ''",
        'field_map': {
            'owner_name': 'name',
            'address': 'situsStreetName',
            'city': 'situsCity',
        },
        'state': 'TX',
        'source_tag': 'assessor:denton_cad_tx',
        'pagination_strategy': 'objectid',
        'return_geometry': False,
    },
    'tcad_travis_tx_v509': {
        # V509 #13: Travis County TX REPLACEMENT — TCAD's Dec 2025
        # parcel feed at the new HGcSYZ5bvjRswoCb services1 host.
        # 382K records vs whatever existing 'travis_county' wired —
        # this is fresher (Dec 2025) and has py_owner_name fully
        # populated.
        'platform': 'arcgis_featureserver',
        'service_description': 'Travis County (TCAD) Parcels Dec 2025',
        'endpoint': 'https://services1.arcgis.com/HGcSYZ5bvjRswoCb/arcgis/rest/services/TCAD_Parcels_Dec_2025/FeatureServer/0',
        'where_clause': "py_owner_name IS NOT NULL AND py_owner_name <> ''",
        'field_map': {
            'owner_name': 'py_owner_name',
            'address': 'situs_address',
            'city': 'situs_city',
            'zip': 'situs_zip',
            'owner_mailing_address': 'py_address',
        },
        'state': 'TX',
        'source_tag': 'assessor:tcad_travis_tx_v509',
        'pagination_strategy': 'objectid',
        'return_geometry': False,
    },
    'lucas_county_oh_toledo': {
        # V509 #14: Lucas County OH (Toledo). 408K records with
        # owner field populated. CLAUDE.md V258 marked Toledo
        # permits dead, but owners ARE live.
        'platform': 'arcgis_featureserver',
        'service_description': 'Lucas County OH (Toledo) Parcels',
        'endpoint': 'https://services3.arcgis.com/T8dczfwPixv79EgZ/arcgis/rest/services/Parcels_General_Land_Use_Classification_view/FeatureServer/0',
        'where_clause': "owner IS NOT NULL AND owner <> ''",
        'field_map': {
            'owner_name': 'owner',
            'address': 'property_address',
            'owner_mailing_address': 'mailing_address',
        },
        'state': 'OH',
        'source_tag': 'assessor:lucas_county_oh_toledo',
        'pagination_strategy': 'objectid',
        'return_geometry': False,
        'default_city': 'Toledo',
    },
    'jefferson_county_co_lakewood': {
        # V509 #15: Jefferson County CO (Denver west suburbs).
        # 248K records — Lakewood, Golden, Wheat Ridge, Arvada.
        'platform': 'arcgis_featureserver',
        'service_description': 'Jefferson County CO (Lakewood) Parcels',
        'endpoint': 'https://services.arcgis.com/PFikmPaTMlt2KX1O/arcgis/rest/services/Jeffco_Parcels/FeatureServer/0',
        'where_clause': "OWNNAM IS NOT NULL AND OWNNAM <> ''",
        'field_map': {
            'owner_name': 'OWNNAM',
        },
        'state': 'CO',
        'source_tag': 'assessor:jefferson_county_co_lakewood',
        'pagination_strategy': 'objectid',
        'return_geometry': False,
        'default_city': 'Lakewood',
    },
    'bexar_county_tx_v509': {
        # V509 #16: Bexar County TX REPLACEMENT — V507 wired SA via
        # services.arcgis.com/g1fRTDLeMgspWrYp's BCAD_Parcels (720K
        # records but Owner_Name field blank in samples). Switch to
        # maps.bexar.org's Parcels MapServer (710K records with Owner
        # populated). Don't double-write — flag the older bexar entry
        # paused if it exists.
        'platform': 'arcgis_mapserver',
        'service_description': 'Bexar County (San Antonio) Parcels — V509 replacement',
        'endpoint': 'https://maps.bexar.org/arcgis/rest/services/Parcels/MapServer/0',
        'where_clause': "Owner IS NOT NULL AND Owner <> ''",
        'field_map': {
            'owner_name': 'Owner',
            'address': 'Situs',
            'owner_mailing_address': 'AddrLn1',
            'owner_mailing_city': 'AddrCity',
            'owner_mailing_state': 'AddrSt',
        },
        'state': 'TX',
        'source_tag': 'assessor:bexar_county_tx_v509',
        'pagination_strategy': 'objectid',
        'return_geometry': False,
        'default_city': 'San Antonio',
    },
}


def _fetch_arcgis_page(endpoint, where, offset, page_size, out_fields, verify_ssl=True):
    """Fetch one page from ArcGIS MapServer, non-spatial.

    V483b: verify_ssl can be False for endpoints whose cert chain doesn't
    validate against the Render container's CA bundle (e.g. gis.nola.gov).
    The data is public-facing parcel data, so unverified TLS is an
    acceptable trade-off — we're not sending creds and the response is
    JSON we parse and persist verbatim.
    """
    params = {
        'where': where,
        'outFields': out_fields,
        'resultOffset': offset,
        'resultRecordCount': page_size,
        'returnGeometry': 'false',
        'orderByFields': 'OBJECTID ASC',
        'f': 'json',
    }
    resp = SESSION.get(endpoint + '/query', params=params, timeout=60, verify=verify_ssl)
    resp.raise_for_status()
    data = resp.json()
    if data.get('error'):
        raise RuntimeError(f"ArcGIS error: {data['error']}")
    return data.get('features', [])


def _fetch_arcgis_objectid_page(endpoint, base_where, last_objectid, page_size, out_fields, verify_ssl=True):
    """V483b: ArcGIS pagination via OBJECTID > N for endpoints that don't
    support resultOffset (e.g. gis.nola.gov returns code:400 for any query
    that includes resultOffset, regardless of the where clause).

    The caller passes the *base* where clause; this helper appends
    `AND OBJECTID > {last_objectid}` and drops resultOffset entirely.
    OBJECTID is implicitly added to out_fields so the caller can advance
    last_objectid from the response.
    """
    where = (
        f"({base_where}) AND OBJECTID > {last_objectid}"
        if base_where else f"OBJECTID > {last_objectid}"
    )
    fields = out_fields
    if 'OBJECTID' not in fields.split(','):
        fields = 'OBJECTID,' + fields
    params = {
        'where': where,
        'outFields': fields,
        'resultRecordCount': page_size,
        'returnGeometry': 'false',
        'orderByFields': 'OBJECTID ASC',
        'f': 'json',
    }
    resp = SESSION.get(endpoint + '/query', params=params, timeout=60, verify=verify_ssl)
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
    # V483b: OBJECTID-based pagination state. last_objectid initializes
    # from start_offset so a resumed call carries the high-water mark
    # forward. Used only when pagination_strategy=='objectid'.
    pagination_strategy = cfg.get('pagination_strategy', 'offset')
    last_objectid = start_offset
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
                if pagination_strategy == 'objectid':
                    features = _fetch_arcgis_objectid_page(
                        cfg['endpoint'], cfg['where_clause'],
                        last_objectid, this_page, out_fields,
                        verify_ssl=cfg.get('verify_ssl', True),
                    )
                else:
                    features = _fetch_arcgis_page(
                        cfg['endpoint'], cfg['where_clause'],
                        offset, this_page, out_fields,
                        verify_ssl=cfg.get('verify_ssl', True),
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

        # V473b: default_city falls back when the source has no city
        # field exposed (Marion / Tarrant / Hamilton parcels carry only
        # ADDRESS lines, not a SITUS_CITY column). Without a city tag,
        # the /cities page's (city, state) tuple match shows 0 owners
        # even when 100K+ rows landed. The default labels every row
        # with the county's principal city so the most-relevant city
        # gets credit. Sub-municipalities (e.g. Arlington TX inside
        # Tarrant) won't match — that's an acceptable trade-off vs.
        # tagging zero.
        default_city = cfg.get('default_city')
        rows = []
        for feat in features:
            attrs = feat.get('attributes', {}) or {}
            # Remap source field names → property_owners column names.
            rows.append({
                'owner_name': _resolve(attrs, field_map.get('owner_name')),
                'address': _resolve(attrs, field_map.get('address')),
                'city': _resolve(attrs, field_map.get('city')) or default_city,
                'zip': _resolve(attrs, field_map.get('zip')),
                'owner_mailing_address': _resolve(attrs, field_map.get('owner_mailing_address')),
                'parcel_id': _resolve(attrs, field_map.get('parcel_id')),
            })

        inserted = _insert_batch(rows, cfg['source_tag'], cfg['state'])
        total_fetched += len(features)
        total_inserted += inserted
        pages += 1

        # V483b: advance the OBJECTID high-water mark for OBJECTID
        # pagination — the next page asks for OBJECTID > last_objectid.
        # Falls back gracefully if a row's attributes dict somehow lacks
        # OBJECTID (shouldn't happen since _fetch_arcgis_objectid_page
        # injects it into outFields).
        if pagination_strategy == 'objectid':
            ids = [
                f.get('attributes', {}).get('OBJECTID')
                for f in features
            ]
            ids = [i for i in ids if isinstance(i, int)]
            if ids:
                last_objectid = max(ids)

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
        'last_offset': last_objectid if pagination_strategy == 'objectid' else offset,
        'elapsed_sec': round(time.time() - started, 1),
    }
