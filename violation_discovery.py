"""V209: Violation endpoint auto-discovery.

Probes Socrata catalog API and ArcGIS Hub search for datasets that
look like code enforcement / housing violations feeds. Outputs a
REPORT (not a DB mutation). Humans read the report and manually wire
the good endpoints into violation_collector.VIOLATION_SOURCES, which
avoids breaking the collector on a bad hit.

Usage:
    from violation_discovery import discover_all
    for hit in discover_all():
        print(hit)

    # Or standalone:
    python3 violation_discovery.py

Returns/prints one line per candidate:
    FOUND: {city}, {state} | {dataset_name} | {domain}/resource/{id}
           | newest={iso_date} | rows_approx={n}
    SKIP:  {city}, {state} | {reason}
"""

import json
import re
import time
from datetime import datetime

import requests

SESSION = requests.Session()
SESSION.headers.update({'Accept': 'application/json'})

# Cities we already have configured (skip these to focus probe on gaps).
# Keep this in sync with violation_collector.VIOLATION_SOURCES keys.
ALREADY_CONFIGURED = {
    ('new-york-city', 'NY'), ('los-angeles', 'CA'), ('chicago-il', 'IL'),
    ('austin-tx', 'TX'), ('seattle-wa', 'WA'), ('san-francisco', 'CA'),
    ('new-orleans-la', 'LA'), ('cincinnati-oh', 'OH'), ('buffalo-ny', 'NY'),
    ('philadelphia', 'PA'), ('kansas-city-mo', 'MO'),
    ('indianapolis-in', 'IN'), ('columbus-oh', 'OH'),
    ('greensboro-nc', 'NC'), ('nashville-tn', 'TN'),
    ('fort-worth-tx', 'TX'), ('charlotte-nc', 'NC'),
    ('san-jose-ca', 'CA'), ('miami-dade-fl', 'FL'),
}

# Socrata instances — (domain, default_city_name, state).
# None = skip (federal/state).
KNOWN_SOCRATA = [
    ('data.sfgov.org', 'San Francisco', 'CA'),
    ('data.seattle.gov', 'Seattle', 'WA'),
    ('data.cityofchicago.org', 'Chicago', 'IL'),
    ('data.boston.gov', 'Boston', 'MA'),
    ('data.cityofnewyork.us', 'New York', 'NY'),
    ('data.lacity.org', 'Los Angeles', 'CA'),
    ('data.austintexas.gov', 'Austin', 'TX'),
    ('data.nashville.gov', 'Nashville', 'TN'),
    ('data.denvergov.org', 'Denver', 'CO'),
    ('data.detroitmi.gov', 'Detroit', 'MI'),
    ('data.cityofgainesville.org', 'Gainesville', 'FL'),
    ('data.jacksonms.gov', 'Jackson', 'MS'),
    ('data.kcmo.org', 'Kansas City', 'MO'),
    ('data.nola.gov', 'New Orleans', 'LA'),
    ('data.milwaukee.gov', 'Milwaukee', 'WI'),
    ('data.memphistn.gov', 'Memphis', 'TN'),
    ('data.raleighnc.gov', 'Raleigh', 'NC'),
    ('data.charlottenc.gov', 'Charlotte', 'NC'),
    ('data.columbus.gov', 'Columbus', 'OH'),
    ('data.stlouis-mo.gov', 'St. Louis', 'MO'),
    ('data.somervillema.gov', 'Somerville', 'MA'),
    ('data.wprdc.org', 'Pittsburgh', 'PA'),
    ('data.baltimorecity.gov', 'Baltimore', 'MD'),
    ('data.oaklandca.gov', 'Oakland', 'CA'),
    ('data.phila.gov', 'Philadelphia', 'PA'),
    ('data.cincinnati-oh.gov', 'Cincinnati', 'OH'),
    ('data.buffalony.gov', 'Buffalo', 'NY'),
]

# Keyword heuristic for dataset titles.
VIOLATION_KEYWORDS = [
    'code enforcement', 'code violation', 'housing violation',
    'property maintenance', 'building complaint', 'housing inspection',
    'nuisance', 'blight', 'citation'
]


def _title_looks_like_violations(title):
    if not title:
        return False
    t = title.lower()
    return any(k in t for k in VIOLATION_KEYWORDS)


def _probe_socrata_domain(domain, city, state, max_results=10):
    """Query the Socrata catalog for violation-style datasets on one domain."""
    url = 'https://api.us.socrata.com/api/catalog/v1'
    candidates = []
    for term in ('code enforcement', 'violations', 'complaints'):
        try:
            r = SESSION.get(url, params={
                'domains': domain,
                'search_context': domain,
                'q': term,
                'only': 'dataset',
                'limit': max_results,
            }, timeout=20)
            if r.status_code != 200:
                continue
            payload = r.json()
        except Exception:
            continue
        for hit in payload.get('results', []):
            res = hit.get('resource') or {}
            name = res.get('name')
            if not _title_looks_like_violations(name):
                continue
            rid = res.get('id')
            if not rid:
                continue
            meta = hit.get('metadata') or {}
            updated = res.get('updatedAt') or meta.get('updatedAt')
            candidates.append({
                'domain': domain,
                'resource_id': rid,
                'name': name,
                'updated_at': updated,
                'city': city,
                'state': state,
            })
    # dedupe by resource_id
    seen = set()
    unique = []
    for c in candidates:
        if c['resource_id'] in seen:
            continue
        seen.add(c['resource_id'])
        unique.append(c)
    return unique


def _probe_recent_row(domain, resource_id):
    """Hit the resource endpoint, pick a sort field, return newest row
    date (ISO string) + approximate count."""
    url = f'https://{domain}/resource/{resource_id}.json'
    try:
        r = SESSION.get(url, params={'$limit': 1, '$order': ':updated_at DESC'},
                        timeout=15)
        if r.status_code != 200:
            return None, None
        rows = r.json()
        if not rows:
            return None, 0
        row = rows[0]
        # Pull any date-ish field
        newest = None
        for k, v in row.items():
            if not isinstance(v, str):
                continue
            if re.match(r'\d{4}-\d{2}-\d{2}', v):
                if newest is None or v > newest:
                    newest = v
        # Approximate count
        c = SESSION.get(url, params={'$select': 'count(*) as n'}, timeout=15)
        approx = None
        if c.status_code == 200 and isinstance(c.json(), list) and c.json():
            try:
                approx = int(c.json()[0].get('n') or 0)
            except Exception:
                approx = None
        return newest, approx
    except Exception:
        return None, None


def _probe_arcgis_hub(city, state, max_results=5):
    """ArcGIS Hub cross-portal search for feature services by city name."""
    query = f"code enforcement violations {city}"
    url = 'https://hub.arcgis.com/api/v3/search'
    try:
        r = SESSION.get(url, params={
            'q': query,
            'filter[type]': 'Feature Service',
            'page[size]': max_results,
        }, timeout=20)
        if r.status_code != 200:
            return []
        payload = r.json()
    except Exception:
        return []
    hits = []
    for item in (payload.get('data') or [])[:max_results]:
        attrs = item.get('attributes') or {}
        title = attrs.get('title')
        if not _title_looks_like_violations(title):
            continue
        url2 = attrs.get('url') or attrs.get('item', {}).get('url')
        hits.append({
            'source': 'arcgis_hub',
            'title': title,
            'url': url2,
            'city': city,
            'state': state,
            'updated_at': attrs.get('modified'),
        })
    return hits


def _format_line(kind, hit):
    if kind == 'socrata':
        newest, approx = _probe_recent_row(hit['domain'], hit['resource_id'])
        fresh = (newest or '0000')[:4] >= '2025'
        fresh_tag = 'FRESH' if fresh else 'STALE'
        return (f"{'FOUND' if fresh else 'SKIP'}: "
                f"{hit['city']}, {hit['state']} | Socrata | "
                f"{hit['domain']}/resource/{hit['resource_id']}.json | "
                f"{hit['name']!r} | newest={newest} rows={approx} | {fresh_tag}")
    elif kind == 'arcgis':
        return (f"FOUND: {hit['city']}, {hit['state']} | ArcGIS Hub | "
                f"{hit.get('url')} | {hit.get('title')!r} | modified={hit.get('updated_at')}")
    return ''


def discover_all(include_arcgis=True, rate_delay=0.5):
    """Return (and print) a list of endpoint candidates.

    Skips domains whose (city_slug, state) is already in
    ALREADY_CONFIGURED.
    """
    lines = []
    print(f"[{datetime.now()}] [V209] violation discovery starting "
          f"({len(KNOWN_SOCRATA)} Socrata domains)")
    for domain, city, state in KNOWN_SOCRATA:
        # crude slug for the skip set
        slug_guess = re.sub(r'[^a-z0-9]+', '-', city.lower()).strip('-')
        if (slug_guess, state) in ALREADY_CONFIGURED or (f'{slug_guess}-{state.lower()}', state) in ALREADY_CONFIGURED:
            lines.append(f"SKIP: {city}, {state} | already configured")
            continue
        socrata_hits = _probe_socrata_domain(domain, city, state)
        if not socrata_hits:
            lines.append(f"SKIP: {city}, {state} | no Socrata violation datasets on {domain}")
        else:
            for hit in socrata_hits:
                lines.append(_format_line('socrata', hit))
        time.sleep(rate_delay)
        if include_arcgis:
            for hit in _probe_arcgis_hub(city, state):
                lines.append(_format_line('arcgis', hit))
            time.sleep(rate_delay)

    print(f"[{datetime.now()}] [V209] violation discovery complete: {len(lines)} lines")
    return lines


if __name__ == '__main__':
    for line in discover_all():
        print(line)
