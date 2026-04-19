#!/usr/bin/env python3
"""V205: Batch DCAT probe for NEEDS_WORK cities. Outputs findings for permits + violations."""
import sys, json, time, re
import requests

# Read worklist from stdin, format: slug|city|state|pop|permits|newest|source_type|status
def try_get(url, params=None, timeout=15):
    try:
        r = requests.get(url, params=params, timeout=timeout)
        return r
    except Exception as e:
        return None

def probe_dcat(portal_url):
    """Return list of dataset titles that mention permit/violation/enforcement."""
    r = try_get(portal_url, timeout=30)
    if not r or r.status_code != 200:
        return None, None
    try:
        j = r.json()
    except Exception:
        return None, None
    datasets = j.get("dataset", [])
    permit_hits = []
    viol_hits = []
    for ds in datasets:
        t = (ds.get("title") or "")
        tl = t.lower()
        dists = ds.get("distribution", [])
        rest_url = None
        for d in dists:
            u = d.get("accessURL") or ""
            if "rest/services" in u:
                rest_url = u
                break
        if not rest_url:
            continue
        if "permit" in tl and ("build" in tl or "issue" in tl or "construction" in tl):
            if "last 30" in tl or "current" in tl or "2026" in tl or "2025" in tl or "all" in tl or ("build" in tl and "permit" in tl):
                permit_hits.append((t, rest_url))
        if any(k in tl for k in ["violation","enforce","code comp","citation","nuisance"]) \
           and not any(x in tl for x in ["zone","district","area","boundary","service"]):
            viol_hits.append((t, rest_url))
    return permit_hits, viol_hits

def guess_hub_urls(city_slug, city_name):
    """Generate candidate hub URLs to try."""
    name_norm = city_name.lower().replace('.','').replace(',','').strip()
    slug = re.sub(r'[^a-z0-9-]', '', name_norm.replace(' ', '-'))
    candidates = [
        f"https://data-{slug}.opendata.arcgis.com/api/feed/dcat-us/1.1.json",
        f"https://{slug}-gis.hub.arcgis.com/api/feed/dcat-us/1.1.json",
        f"https://opendata-{slug}.opendata.arcgis.com/api/feed/dcat-us/1.1.json",
        f"https://opendata.{slug}.gov/api/feed/dcat-us/1.1.json",
        f"https://{slug}.opendata.arcgis.com/api/feed/dcat-us/1.1.json",
    ]
    return candidates

def main():
    worklist = []
    for line in sys.stdin:
        parts = line.strip().split('|')
        if len(parts) < 9:
            continue
        # format: idx|slug|city|state|pop|permits|newest|source_type|status
        worklist.append({
            'idx': parts[0],
            'slug': parts[1],
            'city': parts[2],
            'state': parts[3],
            'pop': int(parts[4] or 0),
            'status': parts[8],
        })
    for item in worklist:
        if 'NEEDS_WORK' not in item['status']:
            continue
        slug = item['slug']
        city = item['city']
        state = item['state']
        # Skip balance-of-* (CDP/township portions)
        if slug.startswith('balance-of-'):
            print(f"RESULT|{slug}|SKIP_CDP|{city}|{state}|")
            continue
        found_permit = None
        found_viol = None
        for url in guess_hub_urls(slug, city):
            permit_hits, viol_hits = probe_dcat(url)
            if permit_hits or viol_hits:
                if permit_hits and not found_permit:
                    found_permit = (url, permit_hits[0])
                if viol_hits and not found_viol:
                    found_viol = (url, viol_hits[0])
                break  # stop at first working hub
        result = {
            'slug': slug,
            'city': city,
            'state': state,
            'permit': found_permit,
            'viol': found_viol,
        }
        if found_permit:
            print(f"PERMIT|{slug}|{city}|{state}|{found_permit[1][0]}|{found_permit[1][1]}")
        if found_viol:
            print(f"VIOLATION|{slug}|{city}|{state}|{found_viol[1][0]}|{found_viol[1][1]}")
        if not found_permit and not found_viol:
            print(f"NONE|{slug}|{city}|{state}")
        sys.stdout.flush()
        time.sleep(0.3)

if __name__ == '__main__':
    main()
