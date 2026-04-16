"""V170 C1: Contractor contact enrichment.

Normalizes contractor names and looks up contact info via Google Places API.
API lookups only run when GOOGLE_PLACES_API_KEY env var is set.
"""

import os
import re
import json
import time
import requests
from datetime import datetime, timedelta

import db as permitdb


def normalize_contractor_name(name):
    """Lowercase, drop LLC/Inc/Corp/etc., strip punctuation.

    V182 fix: suffix stripping is now end-of-string only. The previous
    behavior did a plain .replace() which matched mid-word — e.g.
    'acme construction llc' had " co" inside "construction" removed,
    producing 'acmenstruction' and breaking dedup. Loop-and-chain-strip
    handles names like "ABC INC. LLC".

    >>> normalize_contractor_name('Smith & Sons LLC')
    'smith sons'
    >>> normalize_contractor_name('ABC Corp.')
    'abc'
    >>> normalize_contractor_name('ACME Construction LLC')
    'acme'
    """
    if not name:
        return ''
    s = name.lower()
    suffixes = [' llc', ' inc', ' corp', ' co', ' company',
                ' ltd', ' l.l.c.', ' l.l.c', ' incorporated',
                ' limited', ' enterprises', ' services',
                ' construction', ' contracting']
    # Strip trailing punctuation/whitespace that would shield a suffix
    # (e.g. "ABC Corp." must have "." stripped before " corp" matches end).
    changed = True
    while changed:
        changed = False
        s = s.rstrip(' .,-')
        for suffix in suffixes:
            if s.endswith(suffix):
                s = s[: -len(suffix)]
                changed = True
    s = re.sub(r'[^\w\s]', '', s)
    return re.sub(r'\s+', ' ', s).strip()


def _ensure_table():
    """Create contractor_contacts table if missing."""
    conn = permitdb.get_connection()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS contractor_contacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                contractor_name_normalized TEXT NOT NULL UNIQUE,
                display_name TEXT,
                phone TEXT,
                email TEXT,
                website TEXT,
                address TEXT,
                source TEXT,
                confidence TEXT,
                looked_up_at TEXT DEFAULT (datetime('now')),
                last_error TEXT
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_contractor_contacts_name
            ON contractor_contacts(contractor_name_normalized)
        """)
        conn.commit()
    except Exception:
        pass


def lookup_contractor(name, city='', state=''):
    """Check cache, then Google Places API if key is set.

    Returns dict with phone/website/address or None.
    """
    norm = normalize_contractor_name(name)
    if not norm or len(norm) < 3:
        return None

    _ensure_table()
    conn = permitdb.get_connection()

    # Cache check (90-day TTL)
    try:
        cached = conn.execute(
            "SELECT * FROM contractor_contacts WHERE contractor_name_normalized = ?",
            (norm,)
        ).fetchone()
        if cached:
            looked_up = cached['looked_up_at'] if isinstance(cached, dict) else cached[9]
            if looked_up:
                try:
                    dt = datetime.fromisoformat(looked_up)
                    if (datetime.now() - dt).days < 90:
                        return dict(cached) if hasattr(cached, 'keys') else None
                except Exception:
                    pass
    except Exception:
        pass

    # Google Places API
    api_key = os.environ.get('GOOGLE_PLACES_API_KEY')
    if not api_key:
        return None

    try:
        query = f"{name} {city} {state} contractor"
        resp = requests.get(
            "https://maps.googleapis.com/maps/api/place/textsearch/json",
            params={'query': query, 'key': api_key},
            timeout=10
        )
        results = resp.json().get('results', [])
        if not results:
            # Cache miss
            conn.execute(
                "INSERT OR REPLACE INTO contractor_contacts "
                "(contractor_name_normalized, source, confidence, last_error) "
                "VALUES (?, 'google_places', 'none', 'no results')",
                (norm,)
            )
            conn.commit()
            return None

        place_id = results[0]['place_id']
        details_resp = requests.get(
            "https://maps.googleapis.com/maps/api/place/details/json",
            params={
                'place_id': place_id,
                'fields': 'name,formatted_phone_number,website,formatted_address',
                'key': api_key,
            },
            timeout=10
        )
        details = details_resp.json().get('result', {})

        record = {
            'contractor_name_normalized': norm,
            'display_name': details.get('name'),
            'phone': details.get('formatted_phone_number'),
            'website': details.get('website'),
            'address': details.get('formatted_address'),
            'source': 'google_places',
            'confidence': 'medium',
        }

        conn.execute(
            "INSERT OR REPLACE INTO contractor_contacts "
            "(contractor_name_normalized, display_name, phone, website, address, source, confidence) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (norm, record['display_name'], record['phone'], record['website'],
             record['address'], record['source'], record['confidence'])
        )
        conn.commit()
        return record

    except Exception as e:
        print(f"[ENRICH] Error looking up {norm}: {e}")
        return None


def enrich_batch(limit=100):
    """Look up contact info for uncached contractor names.

    Called by the scheduled worker job. Skips if API key not set.
    """
    api_key = os.environ.get('GOOGLE_PLACES_API_KEY')
    if not api_key:
        return 0

    _ensure_table()
    conn = permitdb.get_connection()

    # Find contractor names not yet cached
    try:
        rows = conn.execute("""
            SELECT DISTINCT p.contractor_name, p.city, p.state
            FROM permits p
            WHERE p.contractor_name IS NOT NULL
              AND p.contractor_name != ''
              AND NOT EXISTS (
                  SELECT 1 FROM contractor_contacts cc
                  WHERE cc.contractor_name_normalized = LOWER(REPLACE(p.contractor_name, ' LLC', ''))
              )
            LIMIT ?
        """, (limit,)).fetchall()
    except Exception:
        return 0

    enriched = 0
    for r in rows:
        name = r['contractor_name'] if isinstance(r, dict) else r[0]
        city = r['city'] if isinstance(r, dict) else r[1]
        state = r['state'] if isinstance(r, dict) else r[2]
        result = lookup_contractor(name, city, state)
        if result and result.get('phone'):
            enriched += 1
        time.sleep(1)  # Rate limit: 1/sec

    print(f"[ENRICH] Batch complete: {enriched}/{len(rows)} enriched")
    return enriched
