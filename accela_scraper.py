"""
Accela Citizen Access scraper for PermitGrab.
Uses Playwright (headless Chromium) to scrape permit data from Accela portals.

V24: Initial implementation supporting 8 cities.
"""

import asyncio
import csv
import io
import os
import re
import time
import tempfile
from datetime import datetime, timedelta

# Playwright is imported lazily to avoid breaking servers that don't need it
_playwright = None
_browser = None

# ============================================================================
# ACCELA CITY CONFIGURATIONS
# ============================================================================
# Each city's Accela portal has slightly different module names and form layouts.
# The scraper uses these configs to navigate to the right search page.

ACCELA_CONFIGS = {
    "dallas": {
        "agency_code": "DALLASTX",
        "module": "Building",
        "tab_name": "Building",
        "search_url_path": "Cap/CapHome.aspx?module=Building&TabName=Building",
    },
    "detroit": {
        "agency_code": "DETROIT",
        "module": "Permits",
        "tab_name": "Permits",
        "search_url_path": "Cap/CapHome.aspx?module=Permits&TabName=Permits",
    },
    "charlotte": {
        "agency_code": "CHARLOTTE",
        "module": "Building",
        "tab_name": "Building",
        # Charlotte has a custom home; try direct URL first, fall back to clicking
        "search_url_path": "Cap/CapHome.aspx?module=Building&TabName=Building",
        "custom_home": True,
    },
    "reno": {
        "agency_code": "RENO",
        "module": "Building",
        "tab_name": "Building",
        "search_url_path": "Cap/CapHome.aspx?module=Building&TabName=Building",
    },
    "indianapolis": {
        "agency_code": "INDY",
        "module": "Permits",
        "tab_name": "Permits",
        # Indianapolis uses "Permits and Contractors" tab on home, but module=Permits in URL
        "search_url_path": "Cap/CapHome.aspx?module=Permits&TabName=Permits",
    },
    "memphis": {
        "agency_code": "SHELBYCO",
        "module": "Building",
        "tab_name": "Building",
        "search_url_path": "Cap/CapHome.aspx?module=Building&TabName=Building",
    },
    "omaha": {
        "agency_code": "OMAHA",
        "module": "Permits",
        "tab_name": "Permits",
        "search_url_path": "Cap/CapHome.aspx?module=Permits&TabName=Permits",
    },
    "oklahoma_city": {
        "agency_code": "OKIE",
        "module": "Building",
        "tab_name": "Building",
        # OKC module might be different — Code must verify by loading the page
        # and checking which tabs exist. Try Building first, then Permits.
        "search_url_path": "Cap/CapHome.aspx?module=Building&TabName=Building",
        "needs_module_discovery": True,
    },
    "oakland": {
        "agency_code": "OAKLAND",
        "module": "Building",
        "tab_name": "Building",
        "search_url_path": "Cap/CapHome.aspx?module=Building&TabName=Building",
    },
    "lancaster_ca": {
        "agency_code": "LANCASTER",
        "module": "Permits",
        "tab_name": "Permits",
        "search_url_path": "Cap/CapHome.aspx?module=Permits&TabName=Permits",
    },
    "palmdale": {
        "agency_code": "PALMDALE",
        "module": "Building",
        "tab_name": "Building",
        "search_url_path": "Cap/CapHome.aspx?module=Building&TabName=Building",
    },
    "stockton": {
        "agency_code": "STOCKTON",
        "module": "Building",
        "tab_name": "Building",
        "search_url_path": "Cap/CapHome.aspx?module=Building&TabName=Building",
    },
    "santa_clarita": {
        "agency_code": "SANTACLARITA",
        "module": "Building",
        "tab_name": "Building",
        "search_url_path": "Cap/CapHome.aspx?module=Building&TabName=Building",
    },
    "fresno": {
        "agency_code": "FRESNO",
        "module": "Building",
        "tab_name": "Building",
        "search_url_path": "Cap/CapHome.aspx?module=Building&TabName=Building",
    },
}

BASE_URL = "https://aca-prod.accela.com"


# ============================================================================
# BROWSER LIFECYCLE
# ============================================================================

async def _get_browser():
    """Get or create a shared Playwright browser instance."""
    global _playwright, _browser
    if _browser is None or not _browser.is_connected():
        from playwright.async_api import async_playwright
        _playwright = await async_playwright().start()
        _browser = await _playwright.chromium.launch(
            headless=True,
            args=[
                '--no-sandbox',
                '--disable-dev-shm-usage',  # Important for Docker/Render
                '--disable-gpu',
            ]
        )
    return _browser


async def close_browser():
    """Close the shared browser instance. Call during graceful shutdown."""
    global _playwright, _browser
    if _browser:
        await _browser.close()
        _browser = None
    if _playwright:
        await _playwright.stop()
        _playwright = None


# ============================================================================
# CORE SCRAPER
# ============================================================================

async def scrape_accela_permits(city_key, days_back=1):
    """
    Scrape permits from an Accela Citizen Access portal.

    Args:
        city_key: Key into ACCELA_CONFIGS (e.g., "dallas", "detroit")
        days_back: Number of days back to search (default: 1 for 12h cycles)

    Returns:
        List of dicts, each representing a raw permit record with keys that
        match PermitGrab's field_map expectations. The dicts should have:
        - "Record Number" -> permit_number
        - "Record Type" -> permit_type
        - "Address" -> address
        - "Description" -> description
        - "Date" -> issued_date
        - "Status" -> status
        - "Project Name" -> (extra info, can go in description)
        - "Expiration Date" -> (extra info)

    Raises:
        Exception on unrecoverable errors (page not found, portal down, etc.)
    """
    if city_key not in ACCELA_CONFIGS:
        raise ValueError(f"Unknown Accela city: {city_key}. Valid: {list(ACCELA_CONFIGS.keys())}")

    config = ACCELA_CONFIGS[city_key]
    agency = config["agency_code"]
    search_url = f"{BASE_URL}/{agency}/{config['search_url_path']}"

    browser = await _get_browser()
    context = await browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        viewport={"width": 1280, "height": 800},
    )
    page = await context.new_page()

    try:
        print(f"    [Accela] Navigating to {agency} search page...")
        await page.goto(search_url, wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(2000)  # Let ASP.NET finish rendering

        # ---- STEP 1: Set date range ----
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days_back)
        start_str = start_date.strftime("%m/%d/%Y")
        end_str = end_date.strftime("%m/%d/%Y")

        print(f"    [Accela] Setting date range: {start_str} to {end_str}")

        # Find and fill the Start Date field
        # Accela date fields have IDs containing "txtGSStartDate" or similar
        # Try multiple selectors since each city may differ slightly
        start_date_selectors = [
            'input[id*="txtGSStartDate"]',
            'input[id*="StartDate"]',
            'input[id*="startDate"]',
        ]
        for sel in start_date_selectors:
            start_input = await page.query_selector(sel)
            if start_input:
                await start_input.click(click_count=3)  # Select all
                await start_input.fill(start_str)
                break

        # Find and fill the End Date field
        end_date_selectors = [
            'input[id*="txtGSEndDate"]',
            'input[id*="EndDate"]',
            'input[id*="endDate"]',
        ]
        for sel in end_date_selectors:
            end_input = await page.query_selector(sel)
            if end_input:
                await end_input.click(click_count=3)
                await end_input.fill(end_str)
                break

        # ---- STEP 2: Handle any Terms/Disclaimer popups ----
        # Some Accela portals show a terms acceptance dialog first
        accept_btn = await page.query_selector('a:has-text("I Agree"), a:has-text("Accept"), button:has-text("Accept"), a:has-text("Continue")')
        if accept_btn:
            print(f"    [Accela] Accepting terms/disclaimer...")
            await accept_btn.click()
            await page.wait_for_timeout(2000)

        # ---- STEP 3: Click Search ----
        print(f"    [Accela] Submitting search...")

        # Try multiple selectors for the Search button
        search_selectors = [
            'a[id*="btnNewSearch"]',
            'input[id*="btnNewSearch"]',
            '#ctl00_PlaceHolderMain_btnNewSearch',
            'a.ACA_LgButton:has-text("Search")',
            'a.ACA_SmButton:has-text("Search")',
            'a[title="Search"]',
            'input[value="Search"]',
            'button:has-text("Search")',
            # Generic fallback - any link with Search text in the main content area
            '#ctl00_PlaceHolderMain a:has-text("Search")',
        ]

        search_btn = None
        for sel in search_selectors:
            search_btn = await page.query_selector(sel)
            if search_btn:
                # Scroll into view and wait for visibility
                await search_btn.scroll_into_view_if_needed()
                await page.wait_for_timeout(500)
                try:
                    await search_btn.click(timeout=10000)
                    break
                except Exception as click_err:
                    # Try JavaScript click as fallback
                    try:
                        await page.evaluate('(el) => el.click()', search_btn)
                        break
                    except:
                        search_btn = None
                        continue

        if not search_btn:
            # Save screenshot for debugging
            await page.screenshot(path=f"/tmp/accela_debug_{agency}.png")
            raise Exception(f"Could not find/click Search button on {agency} portal. Debug screenshot saved.")

        # Wait for results to load (ASP.NET postback)
        await page.wait_for_load_state("networkidle", timeout=30000)
        await page.wait_for_timeout(3000)

        # ---- STEP 3: Check for results ----
        page_text = await page.inner_text("body")
        if "No matching records" in page_text or "0 results" in page_text.lower():
            print(f"    [Accela] No results for {agency} in date range")
            return []

        # ---- STEP 4: Try CSV Export first (preferred — gets all results) ----
        permits = await _try_csv_export(page, agency)
        if permits:
            print(f"    [Accela] Got {len(permits)} permits via CSV export from {agency}")
            return permits

        # ---- STEP 5: Fall back to HTML table parsing with pagination ----
        print(f"    [Accela] CSV export unavailable, parsing HTML table...")
        permits = await _parse_results_table(page, agency)
        print(f"    [Accela] Got {len(permits)} permits via HTML parsing from {agency}")
        return permits

    except Exception as e:
        print(f"    [Accela] ERROR scraping {agency}: {str(e)[:200]}")
        raise
    finally:
        await context.close()


# ============================================================================
# CSV EXPORT (preferred path — gets all results in one shot)
# ============================================================================

async def _try_csv_export(page, agency_code):
    """
    Try to click the "Download results" CSV export button.
    Returns list of permit dicts if successful, empty list if export unavailable.
    """
    try:
        export_btn = await page.query_selector('a[id*="btnExport"]')
        if not export_btn:
            return []

        # Set up download listener BEFORE clicking
        async with page.expect_download(timeout=30000) as download_info:
            await export_btn.click()

        download = await download_info.value
        # Save to temp file and parse
        tmp_path = os.path.join(tempfile.gettempdir(), f"accela_{agency_code}.csv")
        await download.save_as(tmp_path)

        permits = []
        with open(tmp_path, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                permit = _csv_row_to_permit(row)
                if permit:
                    permits.append(permit)

        # Clean up
        os.remove(tmp_path)
        return permits

    except Exception as e:
        print(f"    [Accela] CSV export failed for {agency_code}: {str(e)[:100]}")
        return []


def _csv_row_to_permit(row):
    """Convert a CSV export row to our standard permit dict format."""
    # CSV column names vary by city but typically include:
    # "Date", "Record Number", "Record Type", "Address", "Description", etc.
    # Handle common variations:
    permit = {}

    # Record Number (permit_number)
    permit["Record Number"] = (
        row.get("Record Number", "") or
        row.get("Record #", "") or
        row.get("Permit Number", "") or
        row.get("Case Number", "")
    ).strip()

    # Record Type (permit_type)
    permit["Record Type"] = (
        row.get("Record Type", "") or
        row.get("Type", "") or
        row.get("Permit Type", "")
    ).strip()

    # Address
    permit["Address"] = (
        row.get("Address", "") or
        row.get("Site Address", "") or
        row.get("Location", "") or
        row.get("Work Location", "")
    ).strip()

    # Date (issued_date)
    permit["Date"] = (
        row.get("Date", "") or
        row.get("Filed Date", "") or
        row.get("Open Date", "") or
        row.get("Submitted Date", "")
    ).strip()

    # Description
    permit["Description"] = (
        row.get("Description", "") or
        row.get("Project Description", "") or
        row.get("Work Description", "")
    ).strip()

    # Status
    permit["Status"] = (
        row.get("Status", "") or
        row.get("Record Status", "")
    ).strip()

    # Project Name (bonus)
    permit["Project Name"] = (
        row.get("Project Name", "") or
        row.get("Name", "")
    ).strip()

    # Expiration Date (bonus)
    permit["Expiration Date"] = (
        row.get("Expiration Date", "") or
        row.get("Expiration", "")
    ).strip()

    # Skip rows with no permit number and no address
    if not permit["Record Number"] and not permit["Address"]:
        return None

    return permit


# ============================================================================
# HTML TABLE PARSER (fallback — paginates through results)
# ============================================================================

async def _parse_results_table(page, agency_code):
    """
    Parse the HTML results table and paginate through all pages.
    Returns list of permit dicts.
    """
    all_permits = []
    max_pages = 50  # Safety limit

    for page_num in range(1, max_pages + 1):
        # Parse current page
        rows = await _parse_current_page(page)
        if not rows:
            break

        all_permits.extend(rows)
        print(f"      Page {page_num}: {len(rows)} records (total: {len(all_permits)})")

        # Check for "Next >" link
        next_btn = await page.query_selector('a:has-text("Next")')
        if not next_btn:
            break  # No more pages

        # Click next page
        await next_btn.click()
        await page.wait_for_load_state("networkidle", timeout=30000)
        await page.wait_for_timeout(2000)

    return all_permits


async def _parse_current_page(page):
    """Parse the results table on the current page. Returns list of permit dicts."""
    permits = []

    # The results table has a specific structure in Accela ACA
    # Headers are in <th> elements, data in <tr>/<td> elements
    # Table ID typically contains "gdvPermitList"

    # Get column headers
    headers = await page.eval_on_selector_all(
        'th',
        """elements => elements.map(el => el.innerText.trim()).filter(t => t.length > 0 && t.length < 50)"""
    )

    if not headers:
        return []

    # Get all data rows
    rows_data = await page.evaluate("""() => {
        const table = document.querySelector('table[id*="gdvPermitList"], table[id*="GridView"]');
        if (!table) return [];

        const rows = table.querySelectorAll('tr');
        const result = [];

        for (let i = 0; i < rows.length; i++) {
            const cells = rows[i].querySelectorAll('td');
            if (cells.length < 3) continue;  // Skip header/empty rows

            const rowData = [];
            cells.forEach(td => {
                // Get text, strip whitespace
                let text = td.innerText.trim();
                // Cap cell text to avoid huge descriptions
                if (text.length > 500) text = text.substring(0, 500);
                rowData.push(text);
            });
            result.push(rowData);
        }
        return result;
    }""")

    if not rows_data:
        return []

    # Map columns to permit fields
    # Standard Accela columns: (checkbox), Date, Record Number, Record Type,
    #   Address, Description, Project Name, Expiration Date, Status, Action, Short Notes
    # The first column is often a checkbox (empty text)

    for row_cells in rows_data:
        if len(row_cells) < 4:
            continue

        permit = {}

        # Try to map by matching headers to cells
        # Skip first cell if it's empty (checkbox column)
        offset = 1 if (row_cells[0] == "" or row_cells[0] == " ") else 0

        for i, header in enumerate(headers):
            cell_idx = i + offset
            if cell_idx >= len(row_cells):
                break
            header_lower = header.lower().strip()

            if "date" == header_lower or header_lower == "filed date":
                permit["Date"] = row_cells[cell_idx]
            elif "record number" in header_lower or "permit number" in header_lower:
                permit["Record Number"] = row_cells[cell_idx]
            elif "record type" in header_lower or "permit type" in header_lower:
                permit["Record Type"] = row_cells[cell_idx]
            elif header_lower == "address" or "location" in header_lower:
                permit["Address"] = row_cells[cell_idx]
            elif header_lower == "description" or "work" in header_lower:
                permit["Description"] = row_cells[cell_idx]
            elif "project" in header_lower:
                permit["Project Name"] = row_cells[cell_idx]
            elif "expir" in header_lower:
                permit["Expiration Date"] = row_cells[cell_idx]
            elif header_lower == "status":
                permit["Status"] = row_cells[cell_idx]

        # Only keep if we got at least a permit number or address
        if permit.get("Record Number") or permit.get("Address"):
            permits.append(permit)

    return permits


# ============================================================================
# SYNCHRONOUS WRAPPER (for collector.py integration)
# ============================================================================

def fetch_accela(config, days_back):
    """
    Synchronous wrapper for the async Accela scraper.
    Called by fetch_permits() in collector.py.

    Args:
        config: City config dict (must have "agency_code" key)
        days_back: Number of days back to scrape

    Returns:
        List of raw permit dicts (same format as other fetch_* functions)
    """
    city_key = config.get("_accela_city_key", "")

    # Also accept agency_code and reverse-lookup city_key
    if not city_key:
        agency = config.get("agency_code", "")
        for key, acfg in ACCELA_CONFIGS.items():
            if acfg["agency_code"] == agency:
                city_key = key
                break

    if not city_key:
        print(f"    [Accela] Cannot determine city_key from config")
        return []

    try:
        # Run the async scraper in an event loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            permits = loop.run_until_complete(scrape_accela_permits(city_key, days_back))
        finally:
            loop.close()
        return permits
    except Exception as e:
        print(f"    [Accela] Scraper failed for {city_key}: {str(e)[:200]}")
        return []
