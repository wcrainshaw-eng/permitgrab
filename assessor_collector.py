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
        'platform': 'arcgis_mapserver',
        'service_description': 'Maricopa County Assessor Parcels',
        'endpoint': 'https://gis.mcassessor.maricopa.gov/arcgis/rest/services/Parcels/MapServer/0',
        'where_clause': 'OWNER_NAME IS NOT NULL',
        'field_map': {
            'owner_name': 'OWNER_NAME',
            'address': 'PHYSICAL_ADDRESS',
            'city': 'PHYSICAL_CITY',
            'zip': 'PHYSICAL_ZIP',
            'owner_mailing_address': 'MAIL_ADDRESS',
            'parcel_id': 'APN',
            'lat': 'LATITUDE',
            'lng': 'LONGITUDE',
        },
        'state': 'AZ',
        'source_tag': 'assessor:maricopa',
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


def _insert_batch(rows, source_tag, state):
    """INSERT OR IGNORE a batch into property_owners.

    Relies on V278's unique index (address, owner_name, source) for
    dedup so re-runs don't double-write.
    """
    if not rows:
        return 0
    conn = permitdb.get_connection()
    inserted_before = conn.execute(
        "SELECT COUNT(*) FROM property_owners WHERE source = ?",
        (source_tag,)
    ).fetchone()
    before_n = inserted_before[0] if not isinstance(inserted_before, dict) else inserted_before['COUNT(*)']

    # Skip rows that would violate NOT NULL on address.
    payload = []
    for r in rows:
        addr = (r.get('address') or '').strip()
        owner = (r.get('owner_name') or '').strip()
        if not addr or not owner or len(addr) < 3 or len(owner) < 2:
            continue
        payload.append((
            addr,
            (r.get('city') or '').strip() or None,
            state,
            (r.get('zip') or '').strip() or None,
            owner,
            (r.get('owner_mailing_address') or '').strip() or None,
            (r.get('parcel_id') or '').strip() or None,
            source_tag,
        ))
    if not payload:
        return 0

    conn.executemany(
        """
        INSERT OR IGNORE INTO property_owners
            (address, city, state, zip, owner_name,
             owner_mailing_address, parcel_id, source, last_updated)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """,
        payload
    )
    conn.commit()

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
    if cfg['platform'] != 'arcgis_mapserver':
        raise NotImplementedError(f"Platform {cfg['platform']} not wired yet")

    field_map = cfg['field_map']
    # Build outFields param from the source field names so we only
    # pull the columns we actually need over the wire.
    out_fields = ','.join(v for v in field_map.values() if v)
    page_size = page_size or DEFAULT_PAGE_SIZE
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
            features = _fetch_arcgis_page(
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
