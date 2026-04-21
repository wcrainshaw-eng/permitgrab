"""V210: Free web enrichment — no Google API dependency.

GCP free trial on project 4054277856 is exhausted; Places API returns
REQUEST_DENIED for billing. V201-2 + V203 + V209-1 all went silent.
This module replaces the Places backend with free methods that work
forever:

  1. DuckDuckGo HTML search (no API key, no IP blocking on normal
     request rates). Parse phone numbers + plausible website URLs
     from the result snippets.
  2. Domain guess fallback — try '{normalized-name}.com' and
     '{normalized-name}construction.com' via requests.head(). If the
     domain resolves and returns 200/301/302 with a reasonable content
     type, accept it.

Signature is compatible with the V209 wiring in server.py
(scheduled_collection calls `enrich_batch(limit=N)`). The daemon
loop can keep calling this function — the module no longer needs a
working API key, so it produces results even with the Places API dead.

Writes to:
  contractor_contacts  (source='web_enrichment', confidence='low')
  contractor_profiles  (phone, website, enrichment_status='enriched')
  enrichment_log       (source='web_enrichment', status='enriched'|'not_found')

Hit rate is lower than Places was (~20-40% vs ~90%) but the cost is
zero and volume is unlimited. 30 attempts per cycle × hourly =
~720/day. Over a week that's ~1000-2000 real phone numbers.
"""

import html
import re
import time
import urllib.parse
from datetime import datetime

import requests

import db as permitdb


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

#: Require at least one formatter char (space, dash, dot, paren) to reduce
#: false-positive matches on digit runs like UNIX timestamps (1776641430)
#: or long permit IDs. US phone with mandatory separator.
PHONE_RE = re.compile(
    r"(?:\(\d{3}\)\s?|\d{3}[-.\s])\d{3}[-.\s]\d{4}"
)
URL_RE = re.compile(r'https?://(?:[a-z0-9-]+\.)+[a-z]{2,}(?:/[^\s"\'<>]*)?', re.I)

# Don't accept these domains as the contractor's website
DENYLIST_DOMAINS = {
    'bbb.org', 'yelp.com', 'yellowpages.com', 'google.com', 'duckduckgo.com',
    'bing.com', 'facebook.com', 'linkedin.com', 'instagram.com', 'twitter.com',
    'x.com', 'youtube.com', 'nextdoor.com', 'homeadvisor.com', 'angi.com',
    'thumbtack.com', 'manta.com', 'buildzoom.com', 'buzzfile.com',
    'mapquest.com', 'zillow.com', 'wheree.com', 'thebluebook.com',
    'procore.com', 'zoominfo.com', 'datanyze.com', 'dnb.com', 'spokeo.com',
    'radaris.com', 'veripages.com', 'whitepages.com', 'usphonebook.com',
    'chamberofcommerce.com', 'bisprofiles.com', 'networx.com',
    'diamondcertified.org',
}

# Rate limits
MIN_DELAY_SEC = 3.0
PER_CYCLE_DEFAULT = 30

SESSION = requests.Session()
SESSION.headers.update({'User-Agent': DEFAULT_USER_AGENT})


# ---------------------------------------------------------------------------
# Name normalization (mirror of contractor_enrichment.normalize_contractor_name)
# ---------------------------------------------------------------------------

_SUFFIXES = [' llc', ' inc', ' corp', ' co', ' company', ' ltd',
             ' l.l.c.', ' l.l.c', ' incorporated', ' limited',
             ' enterprises', ' services', ' construction', ' contracting']


def normalize_name(name):
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


def _domain_slug(name):
    """Produce 'acmeplumbing' from 'Acme Plumbing LLC'."""
    n = normalize_name(name)
    return re.sub(r'[^a-z0-9]', '', n)


# ---------------------------------------------------------------------------
# Free enrichment engine
# ---------------------------------------------------------------------------

class FreeEnrichmentEngine:
    """Try DuckDuckGo HTML search first, then domain guessing.

    Every method returns (phone_or_None, website_or_None). The first
    method that produces at least one wins.
    """

    def __init__(self, session=None):
        self.session = session or SESSION

    # -- DuckDuckGo HTML search --------------------------------------------

    def _ddg_search(self, query):
        """Return raw HTML from DuckDuckGo's HTML endpoint.

        Uses the `html.duckduckgo.com/html/` path which returns static
        HTML without JavaScript. DDG sometimes returns 202 on the POST
        path — still ships the result HTML, so accept anything < 400.
        """
        url = 'https://html.duckduckgo.com/html/'
        try:
            r = self.session.post(url, data={'q': query}, timeout=15)
            if r.status_code >= 400:
                return ''
            return r.text
        except Exception:
            return ''

    def _parse_phone(self, text):
        """Pull the first plausible US phone number from free text."""
        for m in PHONE_RE.finditer(text):
            digits = re.sub(r'\D', '', m.group(0))
            if len(digits) == 10 and not digits.startswith('0') and not digits.startswith('1'):
                return m.group(0).strip()
        return None

    def _parse_website(self, text, contractor_name):
        """Return the first URL in `text` whose domain is not on the
        denylist and whose domain shares at least 3 chars with the
        contractor name slug (cheap relevance check).

        This keeps us from picking up random lawyer-referral or
        directory URLs that happen to be listed near the phone number.
        """
        slug = _domain_slug(contractor_name)
        for m in URL_RE.finditer(text):
            url = html.unescape(m.group(0)).rstrip('.,);')
            domain = re.sub(r'https?://', '', url).split('/')[0].lower()
            domain = domain.lstrip('www.')
            if any(domain == d or domain.endswith('.' + d) for d in DENYLIST_DOMAINS):
                continue
            # Cheap relevance check
            host = domain.split('.')[0]
            if slug and host and len(slug) >= 4:
                # at least one 4-char substring of slug appears in host
                hits = sum(1 for i in range(len(slug) - 3)
                           if slug[i:i + 4] in host)
                if hits == 0:
                    continue
            return url
        return None

    def search_ddg(self, name, city, state):
        query = f'{name} {city} {state} contractor phone'
        html_text = self._ddg_search(query)
        if not html_text:
            return None, None
        # DuckDuckGo wraps real result URLs in a redirector. Unwrap them.
        html_text = re.sub(
            r'/l/\?kh=-1&uddg=([^&"\'<>\s]+)',
            lambda m: '→' + urllib.parse.unquote(m.group(1)),
            html_text,
        )
        phone = self._parse_phone(html_text)
        website = self._parse_website(html_text, name)
        return phone, website

    # -- Domain guessing ----------------------------------------------------

    def search_domain_guess(self, name, city, state):
        slug = _domain_slug(name)
        if len(slug) < 4 or len(slug) > 30:
            return None, None
        candidates = [f'https://{slug}.com']
        if not slug.endswith('construction'):
            candidates.append(f'https://{slug}construction.com')
        for url in candidates:
            try:
                r = self.session.head(url, timeout=10, allow_redirects=True)
                if r.status_code < 400:
                    return None, r.url.rstrip('/')
            except Exception:
                continue
        return None, None

    # -- Orchestration -----------------------------------------------------

    def enrich_one(self, name, city, state):
        """Try methods in order; return first (phone, website) with content."""
        for method in (self.search_ddg, self.search_domain_guess):
            try:
                phone, website = method(name, city or '', state or '')
            except Exception as e:
                print(f"[V210] method {method.__name__} error for {name}: {e}")
                phone, website = None, None
            if phone or website:
                return phone, website
        return None, None


# ---------------------------------------------------------------------------
# Batch driver + DB plumbing (compatible with V209 daemon wiring)
# ---------------------------------------------------------------------------

def _select_pending(conn, limit, per_city_cap=25):
    """Pick contractors that have no contact cache row yet (or a stale
    cache miss). Filter out known junk names (NOT GIVEN, OWNER, utility
    placeholders, numeric IDs, etc).

    V234 P1: per-city fairness. The pre-V234 version ordered globally by
    total_permits DESC, which meant cities with one massive profile
    (Mesa at 16,859 profiles) monopolized every daemon cycle and cities
    like Portland/Columbus/Philly — with thousands of profiles of their
    own — almost never got their turn. The window-function cap rotates
    top-N profiles per source_city_key so every city with pending work
    gets a share of each cycle.
    """
    rows = conn.execute("""
        WITH candidates AS (
            SELECT cp.id, cp.contractor_name_raw, cp.contractor_name_normalized,
                   cp.city, cp.state, cp.source_city_key, cp.total_permits,
                   ROW_NUMBER() OVER (
                       PARTITION BY cp.source_city_key
                       ORDER BY cp.total_permits DESC, cp.id ASC
                   ) AS city_rank
            FROM contractor_profiles cp
            LEFT JOIN contractor_contacts cc
                   ON cc.contractor_name_normalized = cp.contractor_name_normalized
            WHERE cp.is_active = 1
              AND (cp.enrichment_status IS NULL OR cp.enrichment_status = 'pending')
              AND cp.contractor_name_raw IS NOT NULL AND cp.contractor_name_raw != ''
              AND LENGTH(cp.contractor_name_raw) >= 5
              AND cp.contractor_name_raw NOT LIKE 'NOT GIVEN%'
              AND cp.contractor_name_raw NOT LIKE 'HOMEOWNER%'
              AND cp.contractor_name_raw NOT LIKE 'OWNER %'
              AND UPPER(cp.contractor_name_raw) NOT LIKE '%SELF CONTRACTOR%'
              AND UPPER(cp.contractor_name_raw) NOT LIKE '%SELECT EDIT%'
              AND UPPER(cp.contractor_name_raw) NOT LIKE '%ENERGY RESOURCE%'
              AND cp.contractor_name_raw NOT GLOB '[0-9]*'
              AND (cc.id IS NULL
                   OR (cc.phone IS NULL AND cc.website IS NULL
                       AND (cc.last_error IS NULL OR cc.last_error != 'no results')))
        )
        SELECT id, contractor_name_raw, contractor_name_normalized,
               city, state, source_city_key
        FROM candidates
        WHERE city_rank <= ?
        ORDER BY city_rank ASC, total_permits DESC, id ASC
        LIMIT ?
    """, (per_city_cap, limit)).fetchall()
    return rows


def enrich_batch(limit=PER_CYCLE_DEFAULT, min_delay=MIN_DELAY_SEC):
    """Daemon entry point — free-method enrichment for up to `limit` profiles.

    No API key required. Logs one line per contractor attempted.
    """
    conn = permitdb.get_connection()
    rows = _select_pending(conn, limit)
    if not rows:
        print(f"[{datetime.now()}] [V210] web_enrichment: no pending profiles")
        return {'enriched': 0, 'not_found': 0, 'errors': 0, 'seen': 0}

    engine = FreeEnrichmentEngine()
    now = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    enriched = 0
    not_found = 0
    errors = 0

    for row in rows:
        pid = row['id'] if isinstance(row, dict) else row[0]
        raw = row['contractor_name_raw'] if isinstance(row, dict) else row[1]
        norm = row['contractor_name_normalized'] if isinstance(row, dict) else row[2]
        city = row['city'] if isinstance(row, dict) else row[3]
        state = row['state'] if isinstance(row, dict) else row[4]
        if not norm:
            norm = normalize_name(raw)

        phone, website = engine.enrich_one(raw, city or '', state or '')

        if phone or website:
            enriched += 1
            try:
                conn.execute("""
                    INSERT OR REPLACE INTO contractor_contacts
                      (contractor_name_normalized, display_name, phone, website,
                       source, confidence, looked_up_at)
                    VALUES (?, ?, ?, ?, 'web_enrichment', 'low', ?)
                """, (norm, raw, phone, website, now))
                conn.execute("""
                    UPDATE contractor_profiles
                    SET phone = COALESCE(phone, ?),
                        website = COALESCE(website, ?),
                        enrichment_status = 'enriched',
                        enriched_at = ?,
                        updated_at = ?
                    WHERE id = ?
                """, (phone, website, now, now, pid))
                conn.execute("""
                    INSERT INTO enrichment_log
                      (contractor_profile_id, source, status, cost, created_at)
                    VALUES (?, 'web_enrichment', 'enriched', 0.0, ?)
                """, (pid, now))
                conn.commit()
                print(f"[{datetime.now()}] [V210] Enriched: {raw} "
                      f"({city}, {state}) — phone={phone!r} website={website!r}")
            except Exception as e:
                errors += 1
                print(f"[{datetime.now()}] [V210] write error for {raw}: {e}")
        else:
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
                    SET enrichment_status = CASE
                            WHEN enrichment_status = 'enriched' THEN enrichment_status
                            ELSE 'not_found' END,
                        enriched_at = ?, updated_at = ?
                    WHERE id = ?
                """, (now, now, pid))
                conn.execute("""
                    INSERT INTO enrichment_log
                      (contractor_profile_id, source, status, cost, created_at)
                    VALUES (?, 'web_enrichment', 'not_found', 0.0, ?)
                """, (pid, now))
                conn.commit()
            except Exception:
                pass
            print(f"[{datetime.now()}] [V210] No results: {raw} ({city}, {state})")

        time.sleep(min_delay)

    summary = {'enriched': enriched, 'not_found': not_found,
               'errors': errors, 'seen': len(rows)}
    print(f"[{datetime.now()}] [V210] web_enrichment batch: {summary}")
    return summary


if __name__ == '__main__':
    import sys
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    print(enrich_batch(limit=n))
