"""V508: Playwright-based Accela CapDetail scraper for JS-SPA portals.

Some Accela installations (SBCO, likely others) return an EMPTY HTML
shell at /Cap/CapDetail.aspx — the actual permit details (including
the contractor / Licensed Professional block) are loaded via XHR after
page render. The static-HTML scraper at accela_portal_collector.py:
parse_accela_licensed_professional gets nothing for those cities.

This module spawns a headless Chromium per call (cheap on memory if
torn down between calls), navigates to CapDetail, waits for the
contractor block to populate, then extracts what it can.

Usage:
    from accela_playwright_collector import fetch_accela_detail_playwright
    info = fetch_accela_detail_playwright('SBCO', '26GEN-00750')
    # info → {'contractor_name': '...', 'license_number': '...',
    #         'contractor_phone': '...', 'contractor_email': '...'}

Memory budget: Chromium peaks at ~500 MB while running. We launch once
per call and kill it on exit so steady-state memory is unaffected.
For the SBCO backfill we'll batch (open one browser, navigate per
permit, close at end) — see fetch_accela_details_batch().
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple


# SBCO permits look like 26GEN-00750. Split into capID1/2/3 expected
# by the CapDetail.aspx query string.
_PERMIT_SPLIT_RE = re.compile(r"^(\d{2})([A-Z]+)-(\d+)$")


def _split_permit_number(permit_number: str) -> Optional[Tuple[str, str, str]]:
    """Convert '26GEN-00750' → ('26', 'GEN', '00750'). Returns None if
    the format doesn't match."""
    m = _PERMIT_SPLIT_RE.match(permit_number.strip())
    if not m:
        return None
    return m.group(1), m.group(2), m.group(3)


def _build_capdetail_url(agency_code: str, permit_number: str,
                        module: str = "Building") -> Optional[str]:
    parts = _split_permit_number(permit_number)
    if not parts:
        return None
    capID1, capID2, capID3 = parts
    return (
        f"https://aca-prod.accela.com/{agency_code}/Cap/CapDetail.aspx"
        f"?Module={module}&capID1={capID1}&capID2={capID2}&capID3={capID3}"
    )


def _extract_from_rendered_html(html: str) -> Dict[str, str]:
    """Parse contractor info from a fully-rendered CapDetail page.

    Falls back to the static-HTML extractor in accela_portal_collector
    so we share the regex library."""
    try:
        from accela_portal_collector import parse_accela_licensed_professional
    except Exception:
        parse_accela_licensed_professional = None  # type: ignore

    info: Dict[str, str] = {}
    if parse_accela_licensed_professional:
        try:
            base = parse_accela_licensed_professional(html) or {}
            for k, v in base.items():
                if v:
                    info[k] = v
        except Exception:
            pass

    # V508: also pull plain "<label>: <value>" patterns from rendered DOM
    # text. Different ACA themes label the contractor block differently
    # (Tampa = "Licensed Professional", SBCO observed labels include
    # "Contractor", "Applicant Information", "Business Name", etc.).
    label_patterns = [
        ("contractor_name",
         r"(?:Contractor|Licensed Professional|Business Name|Company Name|Applicant)\s*:?\s*(?:</[^>]+>\s*)?([A-Z][A-Z0-9 &.,'\-/]{3,80}(?:INC\.?|LLC\.?|CORP\.?|CO\.?|COMPANY|CONSTRUCTION|SERVICES|BUILDERS|CONTRACTOR|PLUMBING|ELECTRIC(?:AL)?|HVAC|HEATING|COOLING|ROOFING|HOMES))"),
        ("license_number",
         r"License\s*(?:Number|#|No\.?)\s*:?\s*(?:</[^>]+>\s*)?([A-Z]{0,4}\d{5,9})"),
        ("contractor_phone",
         r"(?:Phone|Telephone|Tel)\s*:?\s*(?:</[^>]+>\s*)?\(?(\d{3})\)?[\s.\-]?(\d{3})[\s.\-]?(\d{4})"),
        ("contractor_email",
         r"(?:Email|E-?mail)\s*:?\s*(?:</[^>]+>\s*)?([\w.+\-]+@[\w\-]+\.[\w.\-]+)"),
    ]
    for key, pattern in label_patterns:
        if info.get(key):
            continue
        m = re.search(pattern, html, re.IGNORECASE)
        if m:
            if key == "contractor_phone" and m.lastindex and m.lastindex >= 3:
                info[key] = f"({m.group(1)}) {m.group(2)}-{m.group(3)}"
            else:
                info[key] = m.group(1).strip()
    return info


def fetch_accela_detail_playwright(
    agency_code: str,
    permit_number: str,
    module: str = "Building",
    timeout_s: int = 25,
) -> Dict[str, str]:
    """Single-permit fetch. Spawns Chromium, gets contractor info, exits.

    Returns {} on any failure (including bad permit format, network
    error, missing Chromium, Playwright not installed, etc.) — call
    sites should treat the empty dict as "no contractor data available".
    """
    url = _build_capdetail_url(agency_code, permit_number, module=module)
    if not url:
        return {}

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"_error": "playwright not installed"}

    info: Dict[str, str] = {}
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                # Render's container often lacks the system bits Chromium
                # wants (sandbox, /dev/shm). The flags below are the
                # standard CI/CD compatibility set.
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--disable-software-rasterizer",
                ],
            )
            try:
                ctx = browser.new_context(user_agent=(
                    "Mozilla/5.0 (compatible; PermitGrab/1.0; "
                    "+https://permitgrab.com)"))
                page = ctx.new_page()
                # Warm session via CapHome (some ACA installs gate
                # CapDetail behind cookies set on CapHome).
                page.goto(
                    f"https://aca-prod.accela.com/{agency_code}/"
                    f"Cap/CapHome.aspx?module={module}",
                    timeout=timeout_s * 1000,
                    wait_until="domcontentloaded",
                )
                page.goto(url, timeout=timeout_s * 1000,
                         wait_until="domcontentloaded")
                # Wait for the contractor / applicant section to populate.
                # Don't fail if the selector never appears — some permits
                # genuinely lack a Licensed Professional (owner-builder).
                try:
                    page.wait_for_selector(
                        "text=/Contractor|Licensed Professional|Applicant|"
                        "Business Name|Company/i",
                        timeout=15000,
                    )
                except Exception:
                    pass
                # Scroll to trigger any lazy-loaded sections
                try:
                    page.evaluate(
                        "window.scrollTo(0, document.body.scrollHeight);")
                    page.wait_for_timeout(1500)
                except Exception:
                    pass
                html = page.content()
                info = _extract_from_rendered_html(html)
                info["_html_size"] = str(len(html))
                info["_url"] = url
            finally:
                browser.close()
    except Exception as e:
        info["_error"] = f"{type(e).__name__}: {str(e)[:120]}"

    return info


def fetch_accela_details_batch(
    agency_code: str,
    permit_numbers: List[str],
    module: str = "Building",
    timeout_s: int = 25,
    max_permits: int = 50,
) -> Dict[str, Dict[str, str]]:
    """Batch version: one browser, many navigations. Use for backfill.

    Returns {permit_number: contractor_info_dict}. Each entry is what
    fetch_accela_detail_playwright would return for that permit. Cap at
    max_permits to keep a single call under Render's gunicorn timeout
    when triggered via admin endpoint."""
    permit_numbers = list(permit_numbers)[:max_permits]
    if not permit_numbers:
        return {}

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"_error": {"_error": "playwright not installed"}}

    out: Dict[str, Dict[str, str]] = {}
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--disable-software-rasterizer",
                ],
            )
            try:
                ctx = browser.new_context(user_agent=(
                    "Mozilla/5.0 (compatible; PermitGrab/1.0; "
                    "+https://permitgrab.com)"))
                page = ctx.new_page()
                page.goto(
                    f"https://aca-prod.accela.com/{agency_code}/"
                    f"Cap/CapHome.aspx?module={module}",
                    timeout=timeout_s * 1000,
                    wait_until="domcontentloaded",
                )
                for pn in permit_numbers:
                    url = _build_capdetail_url(agency_code, pn, module=module)
                    if not url:
                        out[pn] = {"_error": "bad permit format"}
                        continue
                    try:
                        page.goto(url, timeout=timeout_s * 1000,
                                 wait_until="domcontentloaded")
                        try:
                            page.wait_for_selector(
                                "text=/Contractor|Licensed Professional|"
                                "Applicant|Business Name|Company/i",
                                timeout=12000,
                            )
                        except Exception:
                            pass
                        try:
                            page.evaluate(
                                "window.scrollTo(0, document.body.scrollHeight);")
                            page.wait_for_timeout(800)
                        except Exception:
                            pass
                        html = page.content()
                        info = _extract_from_rendered_html(html)
                        info["_html_size"] = str(len(html))
                        out[pn] = info
                    except Exception as e:
                        out[pn] = {"_error": f"{type(e).__name__}: {str(e)[:120]}"}
            finally:
                browser.close()
    except Exception as e:
        out["_browser_error"] = {"_error": f"{type(e).__name__}: {str(e)[:120]}"}

    return out
