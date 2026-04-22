"""V237 PR#1: State contractor-license imports.

DDG-based enrichment yields 1-3% phones for company names and 0% for
numeric IDs. State construction licensing boards publish the actual
phone number for every licensed contractor, so downloading their open-
data CSVs and matching against contractor_profiles is an order of
magnitude higher-leverage than one more scrape tweak.

V237 ships Oregon CCB first because it's the extreme case: Portland's
contractor_name_raw is literally the CCB license number (numeric), so
there's no fuzzy matching — just a direct license_number lookup.

Adding more states (FL DBPR, OH OCILB, WA L&I) means adding an entry
to STATE_CONFIGS. The normalize + match + write path is state-agnostic.
"""
from __future__ import annotations

import re
import time
from datetime import datetime

import requests

import db as permitdb


SOCRATA_PAGE_LIMIT = 50000  # hard cap per fetch page; Socrata allows up to 50K
SOCRATA_TIMEOUT = 60


STATE_CONFIGS = {
    'OR': {
        'name': 'Oregon CCB Active Licenses',
        # Confirmed 2026-04-22: data.oregon.gov Socrata resource. ~45K rows.
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
    t = re.sub(r'\b(LLC|INC|LTD|CORP|CO|COMPANY|CORPORATION|LP|LLP|PC|PLLC)\b',
               '', t)
    t = re.sub(r'\s+', ' ', t).strip()
    return t


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

    Returns a dict with per-strategy counters. Raises ValueError on
    unknown state_code. Caller is responsible for spawning this in a
    background thread if the run might exceed Render's 30s HTTP timeout.
    """
    state_code = state_code.upper()
    if state_code not in STATE_CONFIGS:
        raise ValueError(f'Unknown state {state_code!r} — '
                         f'available: {sorted(STATE_CONFIGS)}')

    config = STATE_CONFIGS[state_code]
    t0 = time.time()
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
