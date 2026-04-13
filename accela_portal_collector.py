"""
Accela Citizen Access portal collector — requests + BeautifulSoup.
Replaces the Playwright-based scraper with a lightweight HTTP approach.

Key insight: All ACA portals share the same ASP.NET WebForms structure.
The only variable is the agency code in the URL. CSRF bypass via Referer/Origin headers.

Usage:
    permits = fetch_accela_portal("DALLASTX", days_back=30)
    # Returns list of dicts with: permit_number, address, permit_type, description, status, date
"""

import re
import time
import requests
from datetime import datetime, timedelta
from bs4 import BeautifulSoup


# Standard ACA form field names (identical across all Accela portals)
FIELD_START_DATE = 'ctl00$PlaceHolderMain$generalSearchForm$txtGSStartDate'
FIELD_END_DATE = 'ctl00$PlaceHolderMain$generalSearchForm$txtGSEndDate'
FIELD_START_DATE_EXT = 'ctl00$PlaceHolderMain$generalSearchForm$txtGSStartDate_ext_ClientState'
FIELD_END_DATE_EXT = 'ctl00$PlaceHolderMain$generalSearchForm$txtGSEndDate_ext_ClientState'
BTN_SEARCH = 'ctl00$PlaceHolderMain$btnNewSearch'

# Standard result columns (order varies slightly by agency but names are consistent)
STANDARD_COLUMNS = ['Date', 'Record Number', 'Record Type', 'Address',
                    'Description', 'Project Name', 'Expiration Date', 'Status']

# Rate limit: 1 request per second per agency
_RATE_LIMIT_SECONDS = 1.0


def _build_session():
    """Create a requests session with browser-like headers."""
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
    })
    return session


def _extract_form_fields(soup):
    """Extract all form fields from the page (ViewState, hidden fields, dropdowns)."""
    fields = {}
    for inp in soup.find_all('input'):
        name = inp.get('name')
        if name:
            fields[name] = inp.get('value', '')
    for sel in soup.find_all('select'):
        name = sel.get('name')
        if name:
            opt = sel.find('option', selected=True)
            fields[name] = opt.get('value', '') if opt else ''
    return fields


def _parse_results_table(soup):
    """Parse the ACA results grid table into a list of dicts."""
    grid = soup.find('table', id=re.compile(r'gdvPermitList'))
    if not grid:
        return [], 0

    # Extract column headers
    headers = []
    header_row = grid.find('tr', class_=re.compile(r'Header|header'))
    if header_row:
        for th in header_row.find_all(['th', 'td']):
            headers.append(th.get_text(strip=True))

    # Extract data rows
    data_rows = grid.find_all('tr', class_=re.compile(r'ACA_TabRow'))
    records = []
    for row in data_rows:
        cells = row.find_all('td')
        values = {}
        for i, cell in enumerate(cells):
            col_name = headers[i] if i < len(headers) else f'col_{i}'
            link = cell.find('a')
            text = link.get_text(strip=True) if link else cell.get_text(strip=True)
            if text:
                values[col_name] = text
        # Skip empty/header-only rows — check for any permit number or address
        has_permit = values.get('Record Number') or values.get('Permit Number') or values.get('Record #') or values.get('Case Number')
        has_address = values.get('Address')
        if has_permit or has_address:
            records.append(values)

    # Extract total count from "Showing X-Y of Z" text
    total = 0
    count_text = soup.find(string=re.compile(r'Showing\s+\d'))
    if count_text:
        m = re.search(r'of\s+([\d,]+)', count_text)
        if m:
            total = int(m.group(1).replace(',', ''))

    return records, total


def _get_next_page_target(soup, current_page):
    """Find the __EVENTTARGET for the next page link."""
    # ACA uses numbered page links (2, 3, 4...) and "..." for more pages
    # Links are <a> tags with href="javascript:__doPostBack('ctl00$...','')
    postback_links = soup.find_all('a', href=re.compile(r'__doPostBack'))
    for link in postback_links:
        text = link.get_text(strip=True)
        href = link.get('href', '')
        # Look for next page number or "..." for more pages
        if text == str(current_page + 1) or (text == '...' and current_page >= 10):
            m = re.search(r"__doPostBack\('([^']+)'", href)
            if m:
                return m.group(1)
    return None


def fetch_accela_portal(agency_code, days_back=30, module="Building",
                        tab_name="Building", max_pages=25):
    """
    Fetch permits from an Accela Citizen Access portal.

    Args:
        agency_code: Accela agency code (e.g., "DALLASTX", "ATLANTA")
        days_back: Number of days of history to fetch
        module: Accela module name (usually "Building")
        tab_name: Tab name for URL (usually same as module)
        max_pages: Maximum pages to scrape (10 results per page)

    Returns:
        List of permit dicts with keys: permit_number, address, permit_type,
        description, project_name, status, date, expiration_date
    """
    session = _build_session()
    base_url = f"https://aca-prod.accela.com/{agency_code}/Cap/CapHome.aspx"

    # Step 1: GET search page
    print(f"  [ACA] {agency_code}: Loading search page...")
    try:
        resp = session.get(base_url, params={"module": module, "TabName": tab_name}, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        print(f"  [ACA] {agency_code}: Failed to load search page: {e}")
        return []

    soup = BeautifulSoup(resp.text, 'html.parser')
    form_data = _extract_form_fields(soup)

    if '__VIEWSTATE' not in form_data:
        print(f"  [ACA] {agency_code}: No ViewState found — page structure unexpected")
        return []

    # Step 2: Submit search with date range
    end_date = datetime.now().strftime("%m/%d/%Y")
    start_date = (datetime.now() - timedelta(days=days_back)).strftime("%m/%d/%Y")

    form_data[FIELD_START_DATE] = start_date
    form_data[FIELD_END_DATE] = end_date
    form_data[FIELD_START_DATE_EXT] = ''
    form_data[FIELD_END_DATE_EXT] = ''
    form_data['__EVENTTARGET'] = BTN_SEARCH
    form_data['__EVENTARGUMENT'] = ''

    # CSRF bypass: Referer + Origin headers
    session.headers.update({
        'Referer': resp.url,
        'Origin': 'https://aca-prod.accela.com',
    })

    print(f"  [ACA] {agency_code}: Searching {start_date} to {end_date}...")
    time.sleep(_RATE_LIMIT_SECONDS)

    try:
        resp2 = session.post(resp.url, data=form_data, timeout=60, allow_redirects=True)
    except Exception as e:
        print(f"  [ACA] {agency_code}: Search POST failed: {e}")
        return []

    if 'Error' in resp2.url:
        print(f"  [ACA] {agency_code}: Search returned error page")
        return []

    # Step 3: Parse first page of results
    soup2 = BeautifulSoup(resp2.text, 'html.parser')
    page_records, total = _parse_results_table(soup2)
    all_records = list(page_records)
    print(f"  [ACA] {agency_code}: Page 1 — {len(page_records)} records (total: {total})")

    if not page_records:
        print(f"  [ACA] {agency_code}: No results found")
        return []

    # Step 4: Paginate
    current_page = 1
    current_soup = soup2
    while current_page < max_pages and len(all_records) < total:
        next_target = _get_next_page_target(current_soup, current_page)
        if not next_target:
            break

        # Extract updated form fields from current page
        page_form = _extract_form_fields(current_soup)
        page_form['__EVENTTARGET'] = next_target
        page_form['__EVENTARGUMENT'] = ''

        time.sleep(_RATE_LIMIT_SECONDS)
        try:
            resp_page = session.post(resp2.url, data=page_form, timeout=60, allow_redirects=True)
            if 'Error' in resp_page.url:
                break
            current_soup = BeautifulSoup(resp_page.text, 'html.parser')
            page_records, _ = _parse_results_table(current_soup)
            if not page_records:
                break
            all_records.extend(page_records)
            current_page += 1
            if current_page % 5 == 0:
                print(f"  [ACA] {agency_code}: Page {current_page} — {len(all_records)} records so far")
        except Exception as e:
            print(f"  [ACA] {agency_code}: Pagination error on page {current_page + 1}: {e}")
            break

    print(f"  [ACA] {agency_code}: Done — {len(all_records)} total records from {current_page} pages")

    # Step 5: Normalize to standard permit format
    permits = []
    for rec in all_records:
        # Handle column name variants across agencies
        pn = rec.get('Record Number') or rec.get('Permit Number') or rec.get('Record #') or rec.get('Case Number') or ''
        dt = rec.get('Date') or rec.get('Received Date') or rec.get('Filed Date') or rec.get('Opened Date') or ''
        pt = rec.get('Record Type') or rec.get('Permit Type') or rec.get('Type') or rec.get('Case Type') or ''
        addr = rec.get('Address') or ''
        desc = rec.get('Description') or rec.get('Project Name') or ''
        status = rec.get('Status') or ''

        permit = {
            'permit_number': pn,
            'address': addr,
            'permit_type': pt,
            'description': desc,
            'status': status,
            'filing_date': _parse_aca_date(dt),
            'date': _parse_aca_date(dt),
            'issued_date': _parse_aca_date(dt),
            'expiration_date': _parse_aca_date(rec.get('Expiration Date', '')),
            'project_name': rec.get('Project Name', ''),
        }
        if permit['permit_number']:
            permits.append(permit)

    return permits


def _parse_aca_date(date_str):
    """Parse MM/DD/YYYY date to ISO YYYY-MM-DD."""
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str.strip(), "%m/%d/%Y").strftime("%Y-%m-%d")
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Integration with PermitGrab collector framework
# ---------------------------------------------------------------------------

def fetch_accela(config, days_back=30):
    """
    Drop-in replacement for the Playwright-based fetch_accela.
    Called by fetch_permits() in collector.py when platform='accela'.

    Args:
        config: City config dict with 'endpoint' or '_accela_city_key'
        days_back: Days of history to fetch

    Returns:
        List of raw permit dicts (same format as other collectors)
    """
    # Extract agency code from config
    agency_code = None
    module = "Building"
    tab_name = "Building"

    # Check _accela_city_key first (e.g., "dallastx" -> "DALLASTX")
    accela_key = config.get('_accela_city_key', '')
    if accela_key:
        agency_code = accela_key.upper()
        # Look up module from ACCELA_CONFIGS in old scraper
        try:
            from accela_scraper import ACCELA_CONFIGS
            old_config = ACCELA_CONFIGS.get(accela_key) or ACCELA_CONFIGS.get(accela_key.lower())
            if old_config:
                agency_code = old_config.get('agency_code', agency_code)
                module = old_config.get('module', module)
                tab_name = old_config.get('tab_name', module)
        except ImportError:
            pass

    # Fall back to extracting from endpoint URL
    if not agency_code:
        endpoint = config.get('endpoint', '')
        m = re.search(r'accela\.com/([^/]+)/', endpoint)
        if m:
            agency_code = m.group(1)
        if 'module=' in endpoint:
            m2 = re.search(r'module=(\w+)', endpoint)
            if m2:
                module = m2.group(1)
                tab_name = module

    if not agency_code:
        print(f"  [ACA] No agency code found in config")
        return []

    return fetch_accela_portal(agency_code, days_back=days_back,
                               module=module, tab_name=tab_name)


if __name__ == "__main__":
    # Test with Dallas
    permits = fetch_accela_portal("DALLASTX", days_back=14)
    print(f"\nTotal permits: {len(permits)}")
    if permits:
        p = permits[0]
        print(f"Sample: {p['permit_number']} | {p['address'][:40]} | {p['permit_type']} | {p['status']}")
