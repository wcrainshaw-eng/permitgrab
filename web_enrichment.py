"""V209: Bulk web enrichment module — daemon-side contractor contact lookup.

Complements contractor_profiles.enrich_city_profiles (V201-2), which runs
tight per-city loops with an $/cycle cap. This module runs a broader
pending-oldest-first sweep across ALL cities per cycle with a separate
rate limit, so cities outside the priority top-10 still make progress.

Mechanism: the working path is Google Places Text Search, which is
already enabled on GCP project 4054277856 (verified V201). We don't
re-implement Custom Search / Bing / BBB scraping — the Places API
gives structured phone/website in one call and is what V201 uses in
prod, just via the other module.

Writes to:
  contractor_contacts  (source='web_enrichment', confidence='medium')
  contractor_profiles  (phone, website, enrichment_status='enriched')
  enrichment_log       (source='web_enrichment', status='enriched'|'not_found')
"""

import os
import re
import time
from datetime import datetime

import requests

import db as permitdb


SESSION = requests.Session()
PLACES_TEXT_SEARCH = "https://maps.googleapis.com/maps/api/place/textsearch/json"
PLACES_DETAILS = "https://maps.googleapis.com/maps/api/place/details/json"

# Rate limits
MIN_DELAY_SEC = 2.0           # between API calls — polite pacing
COST_PER_LOOKUP = 0.034       # TextSearch ($17/1k) + Details ($17/1k) rough


_SUFFIXES = [' llc', ' inc', ' corp', ' co', ' company', ' ltd',
             ' l.l.c.', ' l.l.c', ' incorporated', ' limited',
             ' enterprises', ' services', ' construction', ' contracting']


def normalize_name(name):
    """Mirror of contractor_enrichment.normalize_contractor_name so the
    contractor_contacts cache is keyed consistently across modules."""
    if not name:
        return ''
    s = name.lower()
    s = re.sub(r'[.,\s]+$', '', s)
    changed = True
    while changed:
        changed = False
        for suf in _SUFFIXES:
            if s.endswith(suf):
                s = s[:-len(suf)].rstrip(' .,')
                changed = True
    s = re.sub(r'[^a-z0-9 &]', '', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def _lookup_via_places(name, city, state):
    """Hit Places Text Search + Details; return dict with phone/website or None."""
    api_key = os.environ.get('GOOGLE_PLACES_API_KEY')
    if not api_key:
        return None, 'no_api_key'

    query = f"{name} {city} {state} contractor"
    try:
        r = SESSION.get(PLACES_TEXT_SEARCH,
                        params={'query': query, 'key': api_key},
                        timeout=10)
        payload = r.json()
    except Exception as e:
        return None, f'text_search_error: {e}'

    status = payload.get('status')
    if status == 'ZERO_RESULTS':
        return None, 'no_results'
    if status != 'OK':
        return None, f'text_search_{status}'

    results = payload.get('results') or []
    if not results:
        return None, 'no_results'
    place = results[0]
    place_id = place.get('place_id')
    if not place_id:
        return None, 'no_place_id'

    try:
        d = SESSION.get(PLACES_DETAILS,
                        params={
                            'place_id': place_id,
                            'fields': 'name,formatted_phone_number,website,formatted_address',
                            'key': api_key,
                        },
                        timeout=10).json()
    except Exception as e:
        return None, f'details_error: {e}'

    details = d.get('result') or {}
    return {
        'display_name': details.get('name') or name,
        'phone': details.get('formatted_phone_number'),
        'website': details.get('website'),
        'address': details.get('formatted_address'),
        'place_id': place_id,
    }, 'ok'


def _select_pending(conn, limit):
    """Pick contractors that have no contact cache row yet (or a stale
    cache miss), ordered by permit_count DESC so the most valuable ones
    get enriched first. Oldest profile first as a tie-breaker."""
    rows = conn.execute("""
        SELECT cp.id, cp.contractor_name_raw, cp.contractor_name_normalized,
               cp.city, cp.state, cp.source_city_key
        FROM contractor_profiles cp
        LEFT JOIN contractor_contacts cc
               ON cc.contractor_name_normalized = cp.contractor_name_normalized
        WHERE cp.is_active = 1
          AND (cp.enrichment_status IS NULL OR cp.enrichment_status = 'pending')
          AND cp.contractor_name_raw IS NOT NULL AND cp.contractor_name_raw != ''
          AND LENGTH(cp.contractor_name_raw) > 3
          AND (cc.id IS NULL
               OR (cc.phone IS NULL AND cc.website IS NULL
                   AND (cc.last_error IS NULL OR cc.last_error != 'no results')))
        ORDER BY cp.total_permits DESC, cp.id ASC
        LIMIT ?
    """, (limit,)).fetchall()
    return rows


def enrich_batch(limit=50, min_delay=MIN_DELAY_SEC):
    """Daemon entry point — enrich up to `limit` pending contractors.

    Safe under the daemon's SQLite write lock: single connection,
    commits per-row, uses the `busy_timeout` from db.get_connection().
    Logs one line per enrichment attempt.
    """
    api_key = os.environ.get('GOOGLE_PLACES_API_KEY')
    if not api_key:
        print(f"[{datetime.now()}] [V209] web_enrichment: no API key, skipping")
        return {'enriched': 0, 'not_found': 0, 'errors': 0, 'cost': 0.0}

    conn = permitdb.get_connection()
    rows = _select_pending(conn, limit)
    if not rows:
        print(f"[{datetime.now()}] [V209] web_enrichment: no pending profiles")
        return {'enriched': 0, 'not_found': 0, 'errors': 0, 'cost': 0.0}

    now = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    enriched = 0
    not_found = 0
    errors = 0
    cost = 0.0

    for row in rows:
        pid = row['id'] if isinstance(row, dict) else row[0]
        raw = row['contractor_name_raw'] if isinstance(row, dict) else row[1]
        norm = row['contractor_name_normalized'] if isinstance(row, dict) else row[2]
        city = row['city'] if isinstance(row, dict) else row[3]
        state = row['state'] if isinstance(row, dict) else row[4]

        if not norm:
            norm = normalize_name(raw)

        record, status = _lookup_via_places(raw, city or '', state or '')
        cost += COST_PER_LOOKUP

        if record and (record.get('phone') or record.get('website')):
            enriched += 1
            try:
                conn.execute("""
                    INSERT OR REPLACE INTO contractor_contacts
                      (contractor_name_normalized, display_name, phone, website,
                       address, source, confidence, looked_up_at)
                    VALUES (?, ?, ?, ?, ?, 'web_enrichment', 'medium', ?)
                """, (norm, record.get('display_name') or raw,
                      record.get('phone'), record.get('website'),
                      record.get('address'), now))
                conn.execute("""
                    UPDATE contractor_profiles
                    SET phone = COALESCE(phone, ?),
                        website = COALESCE(website, ?),
                        google_place_id = COALESCE(google_place_id, ?),
                        enrichment_status = 'enriched',
                        enriched_at = ?,
                        updated_at = ?
                    WHERE id = ?
                """, (record.get('phone'), record.get('website'),
                      record.get('place_id'), now, now, pid))
                conn.execute("""
                    INSERT INTO enrichment_log
                      (contractor_profile_id, source, status, cost, created_at)
                    VALUES (?, 'web_enrichment', 'enriched', ?, ?)
                """, (pid, COST_PER_LOOKUP, now))
                conn.commit()
                print(f"[{datetime.now()}] [V209] web_enrichment: {city}, {state} - "
                      f"{raw} - phone={record.get('phone')!r}, "
                      f"website={record.get('website')!r}")
            except Exception as e:
                errors += 1
                print(f"[{datetime.now()}] [V209] web_enrichment write error "
                      f"for {raw}: {e}")
        elif status in ('no_results', 'text_search_ZERO_RESULTS'):
            not_found += 1
            try:
                conn.execute("""
                    INSERT OR REPLACE INTO contractor_contacts
                      (contractor_name_normalized, source, confidence,
                       looked_up_at, last_error)
                    VALUES (?, 'web_enrichment', 'none', ?, 'no results')
                """, (norm, now))
                conn.execute("""
                    UPDATE contractor_profiles
                    SET enrichment_status = 'not_found',
                        enriched_at = ?, updated_at = ?
                    WHERE id = ? AND enrichment_status != 'enriched'
                """, (now, now, pid))
                conn.execute("""
                    INSERT INTO enrichment_log
                      (contractor_profile_id, source, status, cost, created_at)
                    VALUES (?, 'web_enrichment', 'not_found', ?, ?)
                """, (pid, COST_PER_LOOKUP, now))
                conn.commit()
            except Exception:
                pass
        else:
            errors += 1
            print(f"[{datetime.now()}] [V209] web_enrichment API error "
                  f"for {raw}: {status}")

        time.sleep(min_delay)

    summary = {'enriched': enriched, 'not_found': not_found,
               'errors': errors, 'cost': round(cost, 4),
               'seen': len(rows)}
    print(f"[{datetime.now()}] [V209] web_enrichment batch: {summary}")
    return summary


if __name__ == '__main__':
    import sys
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    print(enrich_batch(limit=limit))
