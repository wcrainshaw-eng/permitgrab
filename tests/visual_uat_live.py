"""V329 (CODE_V320 Part C): live-prod visual UAT.

The pytest tests in test_visual_uat.py run against an in-process
Flask app with whatever (often empty) SQLite is on disk. This script
hits production over HTTPS and asserts the same visual contracts
against the actual rendered HTML.

Usage:
    python3 tests/visual_uat_live.py
    python3 tests/visual_uat_live.py --base https://staging.permitgrab.com

Exits non-zero if any check fails — wire into post-deploy CI to catch
the kind of UX bugs the in-process tests can't see (CSS class drift,
JS-only fallbacks, dead nav links, slow page loads on real DB).
"""
from __future__ import annotations

import argparse
import re
import sys
import time
import urllib.error
import urllib.request


DEFAULT_BASE = 'https://permitgrab.com'

# Cities to probe — kept short so the run is fast. Rotate as ad-ready set
# evolves.
PROBE_SLUGS = ['chicago-il', 'san-antonio-tx', 'phoenix-az', 'miami-dade-county']


class Result:
    def __init__(self):
        self.failures: list[str] = []
        self.warnings: list[str] = []

    def fail(self, msg: str) -> None:
        self.failures.append(msg)
        print(f'  FAIL: {msg}')

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)
        print(f'  WARN: {msg}')

    def ok(self, msg: str) -> None:
        print(f'  OK:   {msg}')


def fetch(base: str, path: str, timeout: int = 15) -> tuple[int, str, float]:
    """Return (status_code, body, elapsed_seconds). 0 / '' on network error."""
    url = base.rstrip('/') + path
    t0 = time.perf_counter()
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'PermitGrab-UAT/1'})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode('utf-8', errors='replace')
            return r.status, body, time.perf_counter() - t0
    except urllib.error.HTTPError as e:
        return e.code, '', time.perf_counter() - t0
    except Exception:
        return 0, '', time.perf_counter() - t0


def check_city_page(res: Result, base: str, slug: str) -> None:
    print(f'\n[{slug}]')
    status, body, elapsed = fetch(base, f'/permits/{slug}')

    if status != 200:
        res.fail(f'/permits/{slug} → HTTP {status}')
        return

    # Speed budget — real DB on Render
    if elapsed > 3.0:
        res.warn(f'/permits/{slug} took {elapsed:.2f}s (>3s soft budget)')
    else:
        res.ok(f'response time {elapsed:.2f}s')

    # Bug 1: nav must include San Antonio (live or fallback path)
    if '/permits/san-antonio-tx' not in body:
        res.fail('San Antonio missing from nav')
    else:
        res.ok('nav includes San Antonio')

    # Bug 2: dropdown click-toggle wired
    if 'dropdown-open' not in body:
        res.fail('dropdown-open class missing — dropdown will be hover-only')
    elif ':hover .dropdown-menu' in body:
        res.fail('old :hover-only dropdown rule regressed')
    else:
        res.ok('dropdown click-toggle wired')

    # Bug 3: no /?city= "View all" links
    if '/?city=' in body:
        res.fail('"/?city=" link present — drops auth state via homepage')
    else:
        res.ok('no homepage-routing View-all links')

    # Bug 4: no duplicate violations sections
    matches = re.findall(r'Code Violations in', body)
    if len(matches) > 1:
        res.fail(f'duplicate violations sections ({len(matches)} headers)')
    else:
        res.ok(f'violations sections: {len(matches)}')
    if '\U0001F525 Active Code Violations' in body:
        res.fail('V162 fire-emoji violations block came back')

    # Part B: unified table + filter
    if 'id="record-filter"' not in body:
        res.warn('filter dropdown missing — page may be in empty/coming-soon state')
    else:
        res.ok('unified-table filter dropdown present')
        for v in ('value="all"', 'value="permits"', 'value="violations"'):
            if v not in body:
                res.fail(f'filter option {v!r} missing')


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', default=DEFAULT_BASE,
                    help=f'Base URL (default {DEFAULT_BASE})')
    ap.add_argument('--slug', action='append',
                    help='Override probe slugs (repeat to add more)')
    args = ap.parse_args()

    slugs = args.slug or PROBE_SLUGS
    res = Result()

    print(f'Visual UAT against {args.base} ({len(slugs)} cities)')
    for slug in slugs:
        check_city_page(res, args.base, slug)

    print()
    print(f'== summary ==  {len(res.failures)} failures, {len(res.warnings)} warnings')
    if res.failures:
        for f in res.failures:
            print(f'  FAIL: {f}')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
