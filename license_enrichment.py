"""V237-V238: State contractor-license imports.

DDG-based enrichment yields 1-3% phones for company names and 0% for
numeric IDs. State construction licensing boards publish the actual
phone number for every licensed contractor, so downloading their open-
data CSVs and matching against contractor_profiles is an order of
magnitude higher-leverage than one more scrape tweak.

V237 shipped Oregon CCB via a direct license_number lookup (a single
Socrata dataset).

V238 adds Florida DBPR, which is very different in shape:
- Three separate CSV downloads (applicants with phones, certified
  licensees with addresses, registered licensees with addresses),
  none of which carry headers.
- Must cross-reference applicant → licensee by name to attach the
  phone onto a full contractor record.
- Profile names come in three formats (business, "FIRST LAST",
  "LAST, FIRST *"), so matching needs multi-strategy normalization.

Adding more states (OH OCILB, WA L&I) means adding an entry to
STATE_CONFIGS. The dispatcher in `import_state` picks the right fetch
+ enrich path based on `config['format']`.
"""
from __future__ import annotations

import csv
import io
import re
import time
from datetime import datetime

import requests

import db as permitdb


SOCRATA_PAGE_LIMIT = 50000  # hard cap per fetch page; Socrata allows up to 50K
SOCRATA_TIMEOUT = 60
CSV_DOWNLOAD_TIMEOUT = 180   # FL DBPR CSVs can be 50MB+; give them room

# Business-word tokens stripped during normalization. Used for both
# `_norm` and the FL fuzzy-match token comparison.
BUSINESS_WORDS = {
    'LLC', 'INC', 'LTD', 'CORP', 'CO', 'COMPANY', 'CORPORATION',
    'LP', 'LLP', 'PC', 'PLLC', 'SERVICES', 'SERVICE',
    'CONSTRUCTION', 'CONTRACTING', 'CONTRACTORS', 'CONTRACTOR',
    'ENTERPRISES', 'GROUP', 'THE', 'OF', 'DBA', 'AND',
}


STATE_CONFIGS = {
    'OR': {
        'name': 'Oregon CCB Active Licenses',
        # Confirmed 2026-04-22: data.oregon.gov Socrata resource. ~45K rows.
        'format': 'socrata',
        'socrata_url': 'https://data.oregon.gov/resource/g77e-6bhs.json',
        # Portland contractor_profiles.contractor_name_raw holds the CCB
        # license number (numeric), so we match on license_number directly.
        # Other OR cities have real business names; fallback to name match.
        'match_strategy': 'license_number',
        'field_map': {
            'license_number': 'license_number',
            'business_name': 'full_name',
            'phone': 'phone_number',
            'address': 'address',
            'city': 'city',
            'state': 'state',
            'zip': 'zip_code',
            'license_type': 'license_type',
            'license_exp': 'lic_exp_date',
        },
        'city_slugs': ['portland', 'portland-or', 'hillsboro', 'beaverton',
                       'eugene', 'salem-or', 'gresham', 'bend-or',
                       'corvallis', 'medford'],
        'socrata_state_filter': "state='OR'",
    },
    'NY': {
        # V240: New York DOL Registered Public Work Contractors.
        # Socrata, ~13K active records, verified phone field. Covers
        # Buffalo (151 phones in the source), NYC boroughs, and the
        # rest of NY state. Same match_strategy as WA — business name.
        'name': 'NY DOL Public Work Contractors',
        'format': 'socrata',
        'socrata_url': 'https://data.ny.gov/resource/i4jv-zkey.json',
        'match_strategy': 'name',
        'field_map': {
            'license_number': 'certificate_number',
            'business_name': 'business_name',
            'phone': 'phone',
            'address': 'address',
            'city': 'city',
            'state': 'state',
            'zip': 'zip_code',
            'license_type': 'business_type',
            'license_exp': 'expiration_date',
        },
        'socrata_state_filter': "status='Active'",
        'city_slugs': ['new-york-city', 'buffalo-ny', 'rochester-ny',
                       'yonkers', 'syracuse', 'albany'],
    },
    'MN': {
        # V240: Minnesota DLI Contractor Registration. Direct CSV
        # download — not Socrata. 20K total, 19K carry phones, 1,061
        # Minneapolis rows available. Compare with Minneapolis's 6
        # existing phones: this is one import away from 500+.
        'name': 'Minnesota DLI Contractor Registration',
        'format': 'csv_dict',
        'source_url': 'https://secure.doli.state.mn.us/ccld/data/MNDLILicRegCertExport_Contractor_Registrations.csv',
        'match_strategy': 'name',
        'field_map': {
            'license_number': 'Lic_Number',
            'business_name': 'Name',
            'phone': 'Phone_No',
            'address': 'Addr1',
            'city': 'City',
            'state': 'St',
            'zip': 'Zip',
            'license_type': 'License_Subtype',
            'license_exp': 'Exp_Date',
        },
        # Only registrations in the 'Issued' status count. The file
        # also contains 'Expired', 'Revoked', 'Cancelled' rows that
        # would pollute the match index.
        'status_filter': {'field': 'Status', 'value': 'Issued'},
        'city_slugs': ['minneapolis', 'saint-paul', 'duluth',
                       'rochester-mn', 'bloomington-mn'],
    },
    'WA': {
        # V239: Washington L&I Contractor License Data — General. Socrata
        # SODA endpoint. 74K active licensees statewide, 3,368 in Seattle.
        # Seattle's contractor_profiles are sparse (~30) because the
        # upstream Seattle permits feed publishes contractor names on
        # only ~108 of ~190K records — so this import is mostly about
        # populating phones on the 30 profiles we do have. Running the
        # import statewide (not just Seattle) also primes a cache for
        # any WA city we add later.
        'name': 'Washington L&I Contractor License',
        'format': 'socrata',
        'socrata_url': 'https://data.wa.gov/resource/m8qx-ubtq.json',
        'match_strategy': 'name',
        'field_map': {
            # NOTE: Cowork's V239 doc referenced businesscity/businessstate
            # but the live dataset uses 'city'/'state'/'zip'. Verified
            # via the Socrata column inspect 2026-04-22.
            'license_number': 'contractorlicensenumber',
            'business_name': 'businessname',
            'phone': 'phonenumber',
            'address': 'address1',
            'city': 'city',
            'state': 'state',
            'zip': 'zip',
            'license_type': 'contractorlicensetypecodedesc',
            'license_exp': 'licenseexpirationdate',
        },
        # Pre-filter to active licenses only — expired/suspended numbers
        # aren't useful contractor leads. No city filter at the Socrata
        # layer because `_enrich_by_name` is already city-scoped against
        # contractor_profiles.source_city_key.
        'socrata_state_filter': "contractorlicensestatus='ACTIVE'",
        'city_slugs': ['seattle', 'seattle-wa'],
    },
    'FL': {
        # V238: Florida DBPR publishes three separate headerless CSVs.
        # The applicant file carries phones; the licensee files carry
        # license metadata + DBA + address. We download all three,
        # cross-reference them, then fuzzy-match against existing
        # contractor_profiles for FL cities.
        'name': 'Florida DBPR Construction',
        'format': 'fl_dbpr',
        'match_strategy': 'name_fuzzy',
        'source_urls': {
            'applicants': 'https://www2.myfloridalicense.com/sto/file_download/extracts/constr_app.csv',
            'certified': 'https://www2.myfloridalicense.com/sto/file_download/extracts/cilb_certified.csv',
            'registered': 'https://www2.myfloridalicense.com/sto/file_download/extracts/cilb_registered.csv',
        },
        # FL CSVs have no header row — positional columns only.
        'applicant_columns': [
            'occupation_number', 'occupation_description', 'first_name',
            'second_name', 'last_name', 'suffix', 'address_1', 'address_2',
            'city', 'state', 'zip', 'phone', 'phone_ext',
        ],
        'licensee_columns': [
            'board_number', 'occupation_code', 'licensee_name', 'dba_name',
            'class_code', 'address_1', 'address_2', 'address_3',
            'city', 'state', 'zip', 'county_code', 'license_number',
            'primary_status', 'secondary_status', 'original_date',
            'effective_date', 'expiration_date', 'blank', 'renewal_period',
            'alternate_lic',
        ],
        # prod_cities FL slugs we care about. Match is city-scoped to
        # avoid pulling in a Miami contractor for a St Petersburg profile.
        'city_slugs': ['miami-dade-county', 'saint-petersburg', 'cape-coral',
                       'orlando-fl', 'fort-lauderdale', 'tallahassee',
                       'miami', 'inverness', 'tampa', 'jacksonville',
                       'hialeah', 'hollywood-fl', 'pembroke-pines',
                       'coral-springs', 'gainesville', 'pompano-beach',
                       'west-palm-beach', 'clearwater', 'lakeland',
                       'palm-bay', 'brandon-fl'],
    },
}


def _norm(text: str) -> str:
    """Normalize a business name for fuzzy equality.

    Matches the logic in contractor_profiles.py's `normalize_name`:
    uppercase, drop punctuation, collapse whitespace, strip common
    business suffixes so `"MD DOORS LLC"` and `"MD Doors, LLC."` hash
    the same.
    """
    if not text:
        return ''
    t = re.sub(r'[^A-Z0-9 ]', ' ', text.upper())
    # Strip the business-word tokens (LLC, INC, CORP, CONSTRUCTION, etc).
    tokens = [tok for tok in t.split() if tok not in BUSINESS_WORDS]
    return ' '.join(tokens).strip()


def _name_tokens(text: str) -> set[str]:
    """Token set for fuzzy overlap matching — same normalization as
    `_norm` but returns a set instead of a string. Single-char tokens
    are dropped (middle initials, trailing "H" in "JAMES H")."""
    if not text:
        return set()
    t = re.sub(r'[^A-Z0-9 ]', ' ', text.upper())
    return {tok for tok in t.split()
            if len(tok) > 1 and tok not in BUSINESS_WORDS}


def _looks_like_business(name: str) -> bool:
    """True if this name contains any business-entity marker (LLC/INC/
    CORP/CO/COMPANY/SERVICES/...). Used to decide whether to run the
    person-name flip logic."""
    if not name:
        return False
    words = set(re.sub(r'[^A-Z ]', ' ', name.upper()).split())
    return bool(words & BUSINESS_WORDS)


def _fl_name_variants(raw: str) -> list[str]:
    """Return the normalized name forms to try when matching an FL
    contractor_profile. Handles all three observed formats:

    - FORMAT 1 "FIRST LAST"        → also try "LAST FIRST"
    - FORMAT 2 "LAST, FIRST MIDDLE *" (St Pete style) → strip `*`,
      also try reordered "FIRST LAST"
    - FORMAT 3 business name        → just the normalized name
    """
    if not raw:
        return []
    # Strip the "* " marker St Petersburg appends to some permit names.
    cleaned = raw.rstrip().rstrip('*').strip().rstrip(',').strip()
    variants = [cleaned]
    if _looks_like_business(cleaned):
        return [_norm(v) for v in variants if _norm(v)]

    # LAST, FIRST [M] → FIRST LAST
    if ',' in cleaned:
        parts = [p.strip() for p in cleaned.split(',', 1)]
        if len(parts) == 2 and parts[0] and parts[1]:
            first_parts = parts[1].split()
            first = first_parts[0] if first_parts else ''
            last = parts[0]
            if first and last:
                variants.append(f'{first} {last}')
    else:
        # FIRST LAST → LAST, FIRST  and  LAST FIRST
        tokens = cleaned.split()
        if len(tokens) == 2:
            variants.append(f'{tokens[1]}, {tokens[0]}')
            variants.append(f'{tokens[1]} {tokens[0]}')
        elif len(tokens) == 3:
            variants.append(f'{tokens[2]}, {tokens[0]}')
            variants.append(f'{tokens[2]} {tokens[0]}')

    # Dedupe while preserving order.
    seen = set()
    out = []
    for v in variants:
        n = _norm(v)
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


def _format_phone(raw: str) -> str | None:
    """Normalize a phone string to `(503) 555-0100` format. Drops anything
    that doesn't look like a 10-digit US number."""
    if not raw:
        return None
    digits = re.sub(r'\D', '', raw)
    if len(digits) == 11 and digits.startswith('1'):
        digits = digits[1:]
    if len(digits) != 10:
        return None
    return f'({digits[:3]}) {digits[3:6]}-{digits[6:]}'


def _is_expired(lic_exp_date: str | None) -> bool:
    """True if an expiration date string is in the past. Unknown = not expired."""
    if not lic_exp_date:
        return False
    for fmt in ('%m/%d/%Y', '%Y-%m-%d', '%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S'):
        try:
            return datetime.strptime(lic_exp_date[:19], fmt).date() < datetime.utcnow().date()
        except ValueError:
            continue
    return False


def _fetch_csv_with_header(url: str) -> list[dict]:
    """Download a CSV that already has a header row. Returns list of
    dicts keyed by the column names the file declares.

    V240: MN DLI CSV pattern — unlike FL DBPR (headerless positional
    columns), state portals that ship Content-Disposition CSV downloads
    usually include a standard header. csv.DictReader handles the rest.

    Decoding: tries UTF-8 first but falls back to latin-1 for state
    portals that export from Windows systems (MN DLI was hitting a
    0xa0 non-breaking-space byte in UTF-8 mode).
    """
    r = requests.get(url, timeout=CSV_DOWNLOAD_TIMEOUT)
    r.raise_for_status()
    body = r.content
    try:
        text = body.decode('utf-8')
    except UnicodeDecodeError:
        text = body.decode('latin-1')
    reader = csv.DictReader(io.StringIO(text))
    rows = []
    for row in reader:
        if not row:
            continue
        rows.append({k: (v or '').strip() for k, v in row.items() if k})
    return rows


def _fetch_csv_no_header(url: str, columns: list[str]) -> list[dict]:
    """Download a headerless CSV and return rows as dicts keyed by the
    positional column names. Trims whitespace on every value.

    Streams the response so a 50MB download doesn't hold the whole
    response body in memory before parsing — iter_lines + csv.reader.
    """
    r = requests.get(url, timeout=CSV_DOWNLOAD_TIMEOUT, stream=True)
    r.raise_for_status()
    r.encoding = r.encoding or 'latin-1'
    rows = []
    # csv.reader expects an iterable of strings. iter_lines yields bytes
    # unless decode_unicode=True. We do that + forward to csv.reader.
    reader = csv.reader(
        r.iter_lines(decode_unicode=True),
        skipinitialspace=True,
    )
    n_cols = len(columns)
    for raw_row in reader:
        if not raw_row:
            continue
        # Pad or trim to the expected column count.
        row_vals = (raw_row + [''] * n_cols)[:n_cols]
        rows.append({columns[i]: (row_vals[i] or '').strip()
                     for i in range(n_cols)})
    return rows


def _build_fl_applicant_phone_index(applicants: list[dict]) -> dict:
    """Map normalized "FIRST LAST" → best phone number from the
    applicants file. If multiple applicants share a name we keep the
    first phone we see (DBPR doesn't publish a dedup key)."""
    idx = {}
    for r in applicants:
        first = r.get('first_name', '').strip()
        last = r.get('last_name', '').strip()
        phone = _format_phone(r.get('phone', ''))
        if not first or not last or not phone:
            continue
        key = _norm(f'{first} {last}')
        if key and key not in idx:
            idx[key] = phone
    return idx


def _normalize_city(raw: str) -> str:
    """Collapse city strings to a canonical form for filter comparison.
    'SAINT PETERSBURG' / 'St. Petersburg' / 'ST PETERSBURG' all need to
    match the prod_cities slug `saint-petersburg`."""
    if not raw:
        return ''
    t = raw.upper().replace('.', '').replace(',', '').strip()
    t = re.sub(r'\s+', ' ', t)
    t = t.replace(' ', '-')
    # Handle common St/Saint prefix variants.
    if t.startswith('ST-'):
        t = 'SAINT-' + t[3:]
    return t.lower()


def _fetch_fl_dbpr(config: dict) -> dict:
    """Download + parse all three FL DBPR CSVs, build a merged index
    of licensee records keyed by the normalized name variants we'll
    try against contractor_profiles."""
    urls = config['source_urls']
    print(f"[V238] FL: downloading applicants CSV…", flush=True)
    applicants = _fetch_csv_no_header(
        urls['applicants'], config['applicant_columns'])
    print(f"[V238] FL: {len(applicants):,} applicants", flush=True)

    print(f"[V238] FL: downloading certified licensees CSV…", flush=True)
    certified = _fetch_csv_no_header(
        urls['certified'], config['licensee_columns'])
    print(f"[V238] FL: downloading registered licensees CSV…", flush=True)
    registered = _fetch_csv_no_header(
        urls['registered'], config['licensee_columns'])
    licensees = certified + registered
    print(f"[V238] FL: {len(licensees):,} licensees "
          f"(cert={len(certified):,}, reg={len(registered):,})", flush=True)

    phone_idx = _build_fl_applicant_phone_index(applicants)

    # Build three lookup indexes so a single contractor_profiles row
    # can be matched on licensee_name OR dba OR applicant name.
    by_name: dict[str, list[dict]] = {}
    by_dba: dict[str, list[dict]] = {}
    for lic in licensees:
        # Attach applicant phone up front so each hit already has it.
        ln_norm = _norm(lic.get('licensee_name', ''))
        # For person-name licensees ("SMITH, JOHN"), also build the
        # "FIRST LAST" variant and look up phone by that key.
        lic['_city_slug'] = _normalize_city(lic.get('city', ''))
        phone = None
        if ln_norm:
            phone = phone_idx.get(ln_norm)
        if not phone and ',' in lic.get('licensee_name', ''):
            parts = [p.strip() for p in lic['licensee_name'].split(',', 1)]
            if len(parts) == 2 and parts[0] and parts[1]:
                first = parts[1].split()[0]
                phone = phone_idx.get(_norm(f'{first} {parts[0]}'))
        lic['_phone'] = phone

        if ln_norm:
            by_name.setdefault(ln_norm, []).append(lic)
        dba_norm = _norm(lic.get('dba_name', ''))
        if dba_norm and dba_norm != ln_norm:
            by_dba.setdefault(dba_norm, []).append(lic)

    return {
        'by_name': by_name,
        'by_dba': by_dba,
        'licensee_count': len(licensees),
        'applicant_count': len(applicants),
        'phone_index_size': len(phone_idx),
    }


def _enrich_fl_profiles(state_code: str, config: dict, index: dict) -> dict:
    """Iterate FL contractor_profiles that still lack a phone and match
    them against the DBPR index. Uses `_fl_name_variants` to try three
    name formats before giving up.
    """
    slugs = config['city_slugs']
    placeholders = ','.join('?' * len(slugs))
    conn = permitdb.get_connection()
    rows = conn.execute(f"""
        SELECT id, contractor_name_raw, contractor_name_normalized,
               source_city_key, city
        FROM contractor_profiles
        WHERE source_city_key IN ({placeholders})
          AND (phone IS NULL OR phone = '')
    """, slugs).fetchall()

    by_name = index['by_name']
    by_dba = index['by_dba']
    matched = 0
    attempted = 0
    now = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    for row in rows:
        attempted += 1
        pid = row['id'] if isinstance(row, dict) else row[0]
        raw = row['contractor_name_raw'] if isinstance(row, dict) else row[1]
        profile_city_slug = row['source_city_key'] if isinstance(row, dict) else row[3]
        variants = _fl_name_variants(raw)

        hit = None
        for key in variants:
            candidates = by_name.get(key) or by_dba.get(key)
            if not candidates:
                continue
            # Prefer a candidate whose city matches the profile's slug
            # so "JOHN SMITH" in Miami doesn't get paired with a tampa
            # licensee with the same name.
            for cand in candidates:
                if cand['_city_slug'] == profile_city_slug:
                    hit = cand
                    break
            if hit is None:
                # Fall back to the first candidate only if all we had
                # was a single match city-agnostic — avoids cross-city
                # false positives when multiple candidates exist.
                if len(candidates) == 1:
                    hit = candidates[0]
            if hit:
                break

        if not hit:
            continue

        phone = hit.get('_phone')
        license_number = hit.get('license_number') or ''
        primary_status = hit.get('primary_status') or ''
        secondary_status = hit.get('secondary_status') or ''
        expiration = hit.get('expiration_date') or ''
        status_parts = [s for s in (primary_status, secondary_status) if s]
        if _is_expired(expiration):
            status_parts.append('expired')
        license_status = '|'.join(status_parts) if status_parts else 'unknown'

        try:
            conn.execute("""
                UPDATE contractor_profiles
                SET phone = COALESCE(?, phone),
                    license_number = COALESCE(NULLIF(?, ''), license_number),
                    license_status = ?,
                    enrichment_status = 'enriched',
                    enriched_at = ?, updated_at = ?
                WHERE id = ?
            """, (phone, license_number, license_status, now, now, pid))
            conn.execute("""
                INSERT INTO enrichment_log
                    (contractor_profile_id, source, status, cost, created_at)
                VALUES (?, ?, 'enriched', 0.0, ?)
            """, (pid, f'license:{state_code}', now))
            conn.commit()
            matched += 1
        except Exception as e:
            print(f"[V238] {state_code} write error for profile {pid}: {e}",
                  flush=True)

    return {
        'candidates': attempted,
        'matched': matched,
        'licensees': index['licensee_count'],
        'applicants': index['applicant_count'],
        'phone_index_size': index['phone_index_size'],
    }


def _fetch_socrata(url: str, where: str | None = None) -> list[dict]:
    """Page through a Socrata .json endpoint. Returns every matching row
    in one list. Stops early if a page comes back shorter than the limit
    (the dataset is exhausted)."""
    results = []
    offset = 0
    while True:
        params = {'$limit': SOCRATA_PAGE_LIMIT, '$offset': offset}
        if where:
            params['$where'] = where
        r = requests.get(url, params=params, timeout=SOCRATA_TIMEOUT)
        r.raise_for_status()
        page = r.json()
        if not isinstance(page, list):
            break
        results.extend(page)
        if len(page) < SOCRATA_PAGE_LIMIT:
            break
        offset += SOCRATA_PAGE_LIMIT
    return results


def _load_license_index(raw_rows: list[dict], field_map: dict) -> dict:
    """Build a dict keyed by license_number for O(1) lookup."""
    idx = {}
    lf = field_map.get('license_number', 'license_number')
    for row in raw_rows:
        lic = row.get(lf)
        if not lic:
            continue
        # Strip leading zeros so "001234" and "1234" both match.
        lic_key = str(lic).lstrip('0') or '0'
        idx[lic_key] = row
    return idx


def _enrich_by_license_number(state_code: str, config: dict,
                              license_idx: dict) -> dict:
    """Portland strategy: contractor_name_raw IS the CCB license number.
    Look each one up in the state index; on hit, rewrite the profile
    (and its permits) with the real business name + phone + license meta.
    """
    fm = config['field_map']
    slugs = config['city_slugs']
    placeholders = ','.join('?' * len(slugs))
    conn = permitdb.get_connection()
    rows = conn.execute(f"""
        SELECT id, contractor_name_raw, contractor_name_normalized,
               source_city_key
        FROM contractor_profiles
        WHERE source_city_key IN ({placeholders})
          AND contractor_name_raw GLOB '[0-9]*'
          AND (enrichment_status IS NULL
               OR enrichment_status NOT IN ('no_source', 'enriched'))
    """, slugs).fetchall()

    matched = 0
    not_found = 0
    now = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    for row in rows:
        pid = row['id'] if isinstance(row, dict) else row[0]
        raw = row['contractor_name_raw'] if isinstance(row, dict) else row[1]
        src_key = row['source_city_key'] if isinstance(row, dict) else row[3]
        lic_key = str(raw).lstrip('0') or '0'
        hit = license_idx.get(lic_key)
        if not hit:
            conn.execute("""
                UPDATE contractor_profiles
                SET enrichment_status = 'not_found',
                    enriched_at = ?, updated_at = ?
                WHERE id = ?
            """, (now, now, pid))
            not_found += 1
            continue

        business_name = (hit.get(fm['business_name']) or '').strip()
        phone = _format_phone(hit.get(fm['phone']))
        license_type = hit.get(fm['license_type']) or ''
        lic_exp = hit.get(fm['license_exp']) or ''
        status = 'expired' if _is_expired(lic_exp) else 'active'

        if not business_name:
            business_name = raw  # fall back to the license number itself

        norm = _norm(business_name)
        try:
            conn.execute("""
                UPDATE contractor_profiles
                SET contractor_name_raw = ?,
                    contractor_name_normalized = ?,
                    phone = COALESCE(?, phone),
                    license_number = ?,
                    license_status = ?,
                    enrichment_status = 'enriched',
                    enriched_at = ?, updated_at = ?
                WHERE id = ?
            """, (business_name, norm, phone, raw,
                  f"{license_type}:{status}", now, now, pid))
            # Also backfill the permits table so the UI stops showing the
            # naked license number.
            conn.execute("""
                UPDATE permits
                SET contractor_name = ?
                WHERE contractor_name = ?
                  AND source_city_key = ?
            """, (business_name, raw, src_key))
            conn.execute("""
                INSERT INTO enrichment_log
                    (contractor_profile_id, source, status, cost, created_at)
                VALUES (?, ?, 'enriched', 0.0, ?)
            """, (pid, f'license:{state_code}', now))
            conn.commit()
            matched += 1
        except Exception as e:
            print(f"[V237] {state_code} license match write error "
                  f"for profile {pid} (lic {raw}): {e}", flush=True)

    return {'candidates': len(rows), 'matched': matched, 'not_found': not_found}


def _enrich_by_name(state_code: str, config: dict,
                    license_idx: dict, raw_rows: list[dict]) -> dict:
    """Generic path for states whose profiles carry real business names.

    Builds a second index keyed on normalized business_name, then walks
    contractor_profiles for every city_slug in the config and looks each
    profile up.

    V237 ships with OR only using the license_number path; this function
    is the scaffold for FL / OH / WA imports where contractor_name_raw is
    the business name and we match on that instead.
    """
    fm = config['field_map']
    name_idx = {}
    for row in raw_rows:
        bn = _norm(row.get(fm['business_name'], ''))
        if bn and bn not in name_idx:
            name_idx[bn] = row

    slugs = config['city_slugs']
    placeholders = ','.join('?' * len(slugs))
    conn = permitdb.get_connection()
    rows = conn.execute(f"""
        SELECT id, contractor_name_raw, contractor_name_normalized, source_city_key
        FROM contractor_profiles
        WHERE source_city_key IN ({placeholders})
          AND contractor_name_raw NOT GLOB '[0-9]*'
          AND (enrichment_status IS NULL
               OR enrichment_status IN ('pending', 'not_found'))
    """, slugs).fetchall()

    matched = 0
    now = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    for row in rows:
        pid = row['id'] if isinstance(row, dict) else row[0]
        norm = row['contractor_name_normalized'] if isinstance(row, dict) else row[2]
        hit = name_idx.get(norm) if norm else None
        if not hit:
            continue
        phone = _format_phone(hit.get(fm['phone']))
        lic = hit.get(fm['license_number']) or ''
        lic_type = hit.get(fm['license_type']) or ''
        lic_exp = hit.get(fm['license_exp']) or ''
        status = 'expired' if _is_expired(lic_exp) else 'active'
        try:
            conn.execute("""
                UPDATE contractor_profiles
                SET phone = COALESCE(?, phone),
                    license_number = ?,
                    license_status = ?,
                    enrichment_status = 'enriched',
                    enriched_at = ?, updated_at = ?
                WHERE id = ?
            """, (phone, lic, f"{lic_type}:{status}", now, now, pid))
            conn.execute("""
                INSERT INTO enrichment_log
                    (contractor_profile_id, source, status, cost, created_at)
                VALUES (?, ?, 'enriched', 0.0, ?)
            """, (pid, f'license:{state_code}', now))
            conn.commit()
            matched += 1
        except Exception as e:
            print(f"[V237] {state_code} name match write error "
                  f"for profile {pid}: {e}", flush=True)
    return {'candidates': len(rows), 'matched': matched,
            'index_size': len(name_idx)}


def import_state(state_code: str) -> dict:
    """Download a state's license data and enrich contractor_profiles.

    Dispatches on `config['format']`:
      - 'socrata'  → single JSON endpoint (Oregon CCB).
      - 'fl_dbpr'  → three headerless CSVs cross-referenced (FL DBPR).

    Returns a dict with per-strategy counters. Raises ValueError on
    unknown state_code. Caller is responsible for spawning this in a
    background thread if the run might exceed Render's 30s HTTP timeout.
    """
    state_code = state_code.upper()
    if state_code not in STATE_CONFIGS:
        raise ValueError(f'Unknown state {state_code!r} — '
                         f'available: {sorted(STATE_CONFIGS)}')

    config = STATE_CONFIGS[state_code]
    fmt = config.get('format', 'socrata')
    t0 = time.time()

    if fmt == 'fl_dbpr':
        print(f"[V238] {state_code}: fetching {config['name']}", flush=True)
        index = _fetch_fl_dbpr(config)
        summary = {
            'state': state_code,
            'strategy': config.get('match_strategy', 'name_fuzzy'),
        }
        summary['by_name'] = _enrich_fl_profiles(state_code, config, index)
        summary['elapsed_seconds'] = round(time.time() - t0, 2)
        print(f"[V238] {state_code}: import complete — {summary}", flush=True)
        return summary

    if fmt == 'csv_dict':
        # V240: simple CSV-with-header download (e.g. MN DLI). Stream,
        # parse via DictReader, optionally post-filter by a status
        # column, then reuse the name-match path.
        print(f"[V240] {state_code}: fetching {config['name']}", flush=True)
        raw = _fetch_csv_with_header(config['source_url'])
        status_filter = config.get('status_filter')
        if status_filter:
            fld = status_filter['field']
            target = status_filter['value'].upper()
            raw = [r for r in raw if r.get(fld, '').upper() == target]
        print(f"[V240] {state_code}: {len(raw):,} rows post-filter "
              f"in {time.time()-t0:.1f}s", flush=True)
        # Empty license_idx — the name-match path doesn't need it, but
        # _enrich_by_name still expects the arg to be a dict.
        summary = {
            'state': state_code,
            'source_rows': len(raw),
            'strategy': config.get('match_strategy', 'name'),
        }
        summary['by_name'] = _enrich_by_name(state_code, config, {}, raw)
        summary['elapsed_seconds'] = round(time.time() - t0, 2)
        print(f"[V240] {state_code}: import complete — {summary}", flush=True)
        return summary

    # Default: Socrata (Oregon CCB and future JSON-endpoint states).
    print(f"[V237] {state_code}: fetching {config['name']}", flush=True)
    raw = _fetch_socrata(
        config['socrata_url'],
        where=config.get('socrata_state_filter'),
    )
    print(f"[V237] {state_code}: fetched {len(raw)} rows in "
          f"{time.time()-t0:.1f}s", flush=True)

    license_idx = _load_license_index(raw, config['field_map'])

    strategy = config.get('match_strategy', 'name')
    summary = {
        'state': state_code,
        'source_rows': len(raw),
        'index_size': len(license_idx),
        'strategy': strategy,
    }
    if strategy == 'license_number':
        summary['by_license'] = _enrich_by_license_number(
            state_code, config, license_idx)
    summary['by_name'] = _enrich_by_name(state_code, config, license_idx, raw)
    summary['elapsed_seconds'] = round(time.time() - t0, 2)
    print(f"[V237] {state_code}: import complete — {summary}", flush=True)
    return summary
