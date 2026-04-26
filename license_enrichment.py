"""V237-V238: State contractor-license imports.

DDG-based enrichment yields 1-3% phones for company names and 0% for
numeric IDs. State construction licensing boards publish the actual
phone number for every licensed contractor, so downloading their open-
data CSVs and matching against contractor_profiles is an order of
magnitude higher-leverage than one more scrape tweak.

V237 shipped Oregon CCB via a direct license_number lookup (a single
Socrata dataset).

V238 adds Florida DBPR, which is very different in shape:
- Three separate CSV downloads (applicants with phones, certified
  licensees with addresses, registered licensees with addresses),
  none of which carry headers.
- Must cross-reference applicant → licensee by name to attach the
  phone onto a full contractor record.
- Profile names come in three formats (business, "FIRST LAST",
  "LAST, FIRST *"), so matching needs multi-strategy normalization.

Adding more states (OH OCILB, WA L&I) means adding an entry to
STATE_CONFIGS. The dispatcher in `import_state` picks the right fetch
+ enrich path based on `config['format']`.
"""
from __future__ import annotations

import csv
import io
import re
import threading
import time
from datetime import datetime

import requests

import db as permitdb


# V242 P0: module-level Event the collection + enrichment daemons check
# at the top of every cycle. license-import sets it for the duration
# of a download + write pass; daemons see set() and skip their cycle
# so WAL contention doesn't stretch a 12s import into 51 minutes.
IMPORT_IN_PROGRESS = threading.Event()


def is_import_running() -> bool:
    """Thin wrapper daemons can call without a direct Event reference."""
    return IMPORT_IN_PROGRESS.is_set()


SOCRATA_PAGE_LIMIT = 50000  # hard cap per fetch page; Socrata allows up to 50K
SOCRATA_TIMEOUT = 60
CSV_DOWNLOAD_TIMEOUT = 180   # FL DBPR CSVs can be 50MB+; give them room

# Business-word tokens stripped during normalization. Used for both
# `_norm` and the FL fuzzy-match token comparison.
BUSINESS_WORDS = {
    'LLC', 'INC', 'LTD', 'CORP', 'CO', 'COMPANY', 'CORPORATION',
    'LP', 'LLP', 'PC', 'PLLC', 'SERVICES', 'SERVICE',
    'CONSTRUCTION', 'CONTRACTING', 'CONTRACTORS', 'CONTRACTOR',
    'ENTERPRISES', 'GROUP', 'THE', 'OF', 'DBA', 'AND',
}


STATE_CONFIGS = {
    'OR': {
        'name': 'Oregon CCB Active Licenses',
        # Confirmed 2026-04-22: data.oregon.gov Socrata resource. ~45K rows.
        'format': 'socrata',
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
    'CA': {
        # V240b: California Contractors State License Board — License
        # Master. Free public download, but delivered via an ASP.NET
        # WebForms page that requires a multi-step postback (ViewState +
        # __EVENTTARGET). 200K+ active/renewable licenses statewide,
        # phone numbers included. Unlocks San Jose (1 phone → 500+?),
        # LA, SF, San Diego, Sacramento, all other CA cities.
        #
        # Field_map column names are educated guesses based on CSLB's
        # documentation. The _fetch_aspnet_csv helper prints the real
        # header row on every run so the map can be corrected if the
        # first run reports mismatches.
        'name': 'California CSLB License Master',
        'format': 'aspnet_csv',
        # V241 P2: CSLB path is case-sensitive. Lowercase
        # /onlineservices/dataportal/ 302s to /OnlineServices/DataPortal/
        # Page_Not_Found.aspx, which ASP.NET serves as 500. Capitals.
        'download_url': 'https://www.cslb.ca.gov/OnlineServices/DataPortal/ContractorList',
        'aspnet_steps': [
            # V241 P3: dropdown submits the VALUE attribute, not the
            # display text. Values are "" / "M" / "W" / "P"; "M" =
            # License Master. Submitting "License Master" previously
            # produced an empty form state and the final postback
            # returned the "pick an option" HTML page instead of a CSV.
            {
                'eventtarget': 'ctl00$MainContent$ddlStatus',
                'fields': {
                    'ctl00$MainContent$ddlStatus': 'M',
                },
            },
            {
                'eventtarget': 'ctl00$MainContent$lbMasterCSV',
                'fields': {
                    'ctl00$MainContent$ddlStatus': 'M',
                },
            },
        ],
        'match_strategy': 'name',
        'field_map': {
            'license_number': 'LicenseNo',
            'business_name': 'BusinessName',
            'phone': 'BusinessPhone',
            'address': 'MailingAddress',
            'city': 'City',
            'state': 'State',
            'zip': 'ZIPCode',
            'license_type': 'Classifications',
            'license_exp': 'ExpirationDate',
        },
        'city_slugs': ['san-jose', 'los-angeles', 'san-francisco',
                       'san-diego', 'sacramento', 'oakland', 'fresno',
                       'long-beach', 'anaheim', 'santa-ana',
                       'bakersfield', 'riverside-ca', 'stockton'],
    },
    'NY': {
        # V240: New York DOL Registered Public Work Contractors.
        # Socrata, ~13K active records, verified phone field. Covers
        # Buffalo (151 phones in the source), NYC boroughs, and the
        # rest of NY state. Same match_strategy as WA — business name.
        'name': 'NY DOL Public Work Contractors',
        'format': 'socrata',
        'socrata_url': 'https://data.ny.gov/resource/i4jv-zkey.json',
        'match_strategy': 'name',
        'field_map': {
            'license_number': 'certificate_number',
            'business_name': 'business_name',
            'phone': 'phone',
            'address': 'address',
            'city': 'city',
            'state': 'state',
            'zip': 'zip_code',
            'license_type': 'business_type',
            'license_exp': 'expiration_date',
        },
        'socrata_state_filter': "status='Active'",
        'city_slugs': ['new-york-city', 'buffalo-ny', 'rochester-ny',
                       'yonkers', 'syracuse', 'albany'],
    },
    'MN': {
        # V240: Minnesota DLI Contractor Registration. Direct CSV
        # download — not Socrata. 20K total, 19K carry phones, 1,061
        # Minneapolis rows available. Compare with Minneapolis's 6
        # existing phones: this is one import away from 500+.
        'name': 'Minnesota DLI Contractor Registration',
        'format': 'csv_dict',
        'source_url': 'https://secure.doli.state.mn.us/ccld/data/MNDLILicRegCertExport_Contractor_Registrations.csv',
        'match_strategy': 'name',
        'field_map': {
            'license_number': 'Lic_Number',
            'business_name': 'Name',
            'phone': 'Phone_No',
            'address': 'Addr1',
            'city': 'City',
            'state': 'St',
            'zip': 'Zip',
            'license_type': 'License_Subtype',
            'license_exp': 'Exp_Date',
        },
        # Only registrations in the 'Issued' status count. The file
        # also contains 'Expired', 'Revoked', 'Cancelled' rows that
        # would pollute the match index.
        'status_filter': {'field': 'Status', 'value': 'Issued'},
        'city_slugs': ['minneapolis', 'saint-paul', 'duluth',
                       'rochester-mn', 'bloomington-mn'],
    },
    'WA': {
        # V239: Washington L&I Contractor License Data — General. Socrata
        # SODA endpoint. 74K active licensees statewide, 3,368 in Seattle.
        # Seattle's contractor_profiles are sparse (~30) because the
        # upstream Seattle permits feed publishes contractor names on
        # only ~108 of ~190K records — so this import is mostly about
        # populating phones on the 30 profiles we do have. Running the
        # import statewide (not just Seattle) also primes a cache for
        # any WA city we add later.
        'name': 'Washington L&I Contractor License',
        'format': 'socrata',
        'socrata_url': 'https://data.wa.gov/resource/m8qx-ubtq.json',
        'match_strategy': 'name',
        'field_map': {
            # NOTE: Cowork's V239 doc referenced businesscity/businessstate
            # but the live dataset uses 'city'/'state'/'zip'. Verified
            # via the Socrata column inspect 2026-04-22.
            'license_number': 'contractorlicensenumber',
            'business_name': 'businessname',
            'phone': 'phonenumber',
            'address': 'address1',
            'city': 'city',
            'state': 'state',
            'zip': 'zip',
            'license_type': 'contractorlicensetypecodedesc',
            'license_exp': 'licenseexpirationdate',
        },
        # Pre-filter to active licenses only — expired/suspended numbers
        # aren't useful contractor leads. No city filter at the Socrata
        # layer because `_enrich_by_name` is already city-scoped against
        # contractor_profiles.source_city_key.
        'socrata_state_filter': "contractorlicensestatus='ACTIVE'",
        'city_slugs': ['seattle', 'seattle-wa'],
    },
    'NY_BUFFALO_ELEC': {
        # V302: Buffalo Master Electrician registry — sibling dataset to V301's
        # General/Home/Handyman. Same schema (businessname + dayphn + status).
        # V301 alone lifted Buffalo 15 → 45 phones; 5 short of ad-ready. This
        # layer adds ~7.2K Master Electrician rows targeting Buffalo electrical
        # contractors that the General registry misses.
        'name': 'Buffalo NY Licensed Contractors (Master Electrician)',
        'format': 'socrata',
        'socrata_url': 'https://data.buffalony.gov/resource/h6v3-63kd.json',
        'socrata_state_filter': "status='ACTIVE'",
        'match_strategy': 'name',
        'field_map': {
            'business_name': 'businessname',
            'phone': 'dayphn',
            'license_type': 'description',
            'license_exp': 'expdate',
        },
        'city_slugs': ['buffalo-ny', 'buffalo'],
    },
    'NY_BUFFALO_PLUMB': {
        # V302: Buffalo Plumber registry (Journeyman + Master) — completes
        # the trio. ~6.4K rows. Same schema.
        'name': 'Buffalo NY Licensed Contractors (Plumbers)',
        'format': 'socrata',
        'socrata_url': 'https://data.buffalony.gov/resource/avrc-zchj.json',
        'socrata_state_filter': "status='ACTIVE'",
        'match_strategy': 'name',
        'field_map': {
            'business_name': 'businessname',
            'phone': 'dayphn',
            'license_type': 'description',
            'license_exp': 'expdate',
        },
        'city_slugs': ['buffalo-ny', 'buffalo'],
    },
    'NY_BUFFALO': {
        # V301: Buffalo NY city-level contractor registry on data.buffalony.gov.
        # Three companion Socrata datasets cover the main trades:
        #   xu7s-vsr5 — General/Home/Handyman (~8K rows)
        #   h6v3-63kd — Master Electricians (~7.2K)
        #   avrc-zchj — Plumbers (~6.4K)
        # This config wires the General/Home/Handyman one first; the others
        # can be added as additional state_codes later. Buffalo currently has
        # 1,106 profiles / 15 phones; adding this registry should push it
        # past 50 → ad-ready. Violations already wired.
        # Sample row: BUSINESS NAME='KT CONSTRUCTION SERVICES', dayphn='(716)525-1097'.
        'name': 'Buffalo NY Licensed Contractors (General/Home/Handyman)',
        'format': 'socrata',
        'socrata_url': 'https://data.buffalony.gov/resource/xu7s-vsr5.json',
        'socrata_state_filter': "status='ACTIVE'",
        'match_strategy': 'name',
        'field_map': {
            'business_name': 'businessname',
            'phone': 'dayphn',
            'license_type': 'description',
            'license_exp': 'expdate',
        },
        'city_slugs': ['buffalo-ny', 'buffalo'],
    },
    'NV_LASVEGAS': {
        # V299: Las Vegas Business Licenses on the same Opendata_lasvegas
        # org (F1v0ufATbBQScMtY) that publishes LV permits. 206,928 total
        # licenses but filtered to active trades here. Probed 2026-04-24:
        # "VEGAS MASONRY AND CONCRETE LLC" 702-803-0874, "VEGAS GRANITE
        # AND MARBLE LLC" 702-868-3300 — real businesses with phones.
        # LV permits source (V292/V295) just delivered 4,418 contractor
        # names with no phones; this cross-reference pushes LV to 10+.
        'name': 'Las Vegas Business Licenses',
        'format': 'arcgis_fs',
        'arcgis_url': 'https://services1.arcgis.com/F1v0ufATbBQScMtY/arcgis/rest/services/Business_Licenses_OpenData/FeatureServer/0',
        # Trim to active + construction-adjacent trades so we aren't
        # fuzzy-matching restaurant owners into our contractor table.
        'arcgis_where': "Status = 'Active' AND (Type_of_Business LIKE '%Contractor%' OR Type_of_Business LIKE '%Building%' OR Type_of_Business LIKE '%Plumb%' OR Type_of_Business LIKE '%Electric%' OR Type_of_Business LIKE '%HVAC%' OR Type_of_Business LIKE '%Roof%' OR Type_of_Business LIKE '%Construction%')",
        'match_strategy': 'name',
        'field_map': {
            'business_name': 'Business_Name',
            'phone': 'Phone',
            'address': 'Address',
            'zip': 'Zip_Code',
            'license_type': 'Type_of_Business',
            'license_number': 'License__',
        },
        'city_slugs': ['las-vegas', 'las-vegas-nv', 'north-las-vegas',
                       'henderson'],  # Henderson benefits too (same metro)
    },
    'OH_CLEVELAND': {
        # V298: Cleveland Active Contractor Registrations — same opendataCLE
        # org that serves Cleveland permits (dty2kHktVXHrqO8i). 2,548 active
        # contractors with BUSINESS_NAME + B1_PHONE1 populated at 100%
        # (probed 2026-04-24: "HJ WOODWORTH CONSTRUCTION, LLC" 216-502-5787,
        # "HD BATH PRO, LLC" 216 377-2480, etc.). Cleveland currently has
        # 2,244 profiles with 2 phones; violations already wired. Expected
        # to push past 50-phone ad-ready threshold easily.
        'name': 'Cleveland Active Contractor Registrations',
        'format': 'arcgis_fs',
        'arcgis_url': 'https://services3.arcgis.com/dty2kHktVXHrqO8i/arcgis/rest/services/Active_Contractor_Registrations/FeatureServer/0',
        'arcgis_where': "STATUS IN ('Issued','Approved','Active')",
        'match_strategy': 'name',
        'field_map': {
            'business_name': 'BUSINESS_NAME',
            'phone': 'B1_PHONE1',
            'address': 'B1_ADDRESS1',
            'city': 'B1_CITY',
            'state': 'B1_STATE',
            'zip': 'B1_ZIP',
            'license_type': 'CONTRACTOR_LICENSE_TYPE',
            'license_number': 'B1_ALT_ID',
        },
        'city_slugs': ['cleveland', 'cleveland-oh'],
    },
    'TN_NASHVILLE': {
        # V297: Nashville Registered Professional Contractors — an ArcGIS
        # FeatureServer layer on the same NashvilleOpenData org as the
        # Building_Permits_Issued_2 source we already collect for Nashville
        # permits. 7,574 rows, each with Company_Name + Phone + E_Mail.
        # Per-city config (not state): Tennessee has no state bulk source,
        # and this registry only covers Nashville-area trades.
        'name': 'Nashville Registered Professional Contractors',
        'format': 'arcgis_fs',
        'arcgis_url': 'https://services2.arcgis.com/HdTo6HJqh92wn4D8/arcgis/rest/services/Registered_Professional_Contractors_view_2/FeatureServer/0',
        'match_strategy': 'name',
        'field_map': {
            'business_name': 'Company_Name',
            'phone': 'Phone',
            'address': 'Address',
            'city': 'City',
            'state': 'ST',
            'zip': 'ZIP',
            'license_type': 'Prof__Type',
        },
        'city_slugs': ['nashville', 'nashville-tn'],
    },
    'FL': {
        # V238: Florida DBPR publishes three separate headerless CSVs.
        # The applicant file carries phones; the licensee files carry
        # license metadata + DBA + address. We download all three,
        # cross-reference them, then fuzzy-match against existing
        # contractor_profiles for FL cities.
        'name': 'Florida DBPR Construction',
        'format': 'fl_dbpr',
        'match_strategy': 'name_fuzzy',
        'source_urls': {
            'applicants': 'https://www2.myfloridalicense.com/sto/file_download/extracts/constr_app.csv',
            # V244d: the "cilb_certified" and "cilb_registered" URLs
            # the V238 task doc pointed us at turned out to be
            # continuing-education records, not licensee rosters
            # (one row per licensee × course, 3.8M rows total, zero
            # DBA / business-name fields). The real FL DBPR licensee
            # file is CONSTRUCTIONLICENSE_1.csv — 47MB, 22 columns,
            # one row per licensee with DBA populated ("GREG MINOR
            # CONSTRUCTION", "COX CORPORATION", etc.).
            'licensees': 'https://www2.myfloridalicense.com/sto/file_download/extracts/CONSTRUCTIONLICENSE_1.csv',
        },
        # V244d: applicant CSV actually has 15 columns (not 13). A
        # sample row showed "...SARASOTA","FL","34240","68",
        # "941.330.5058",""  — address_3 and country_code sit between
        # what we thought was zip/phone. The pre-V244d config put
        # 'phone' at position 11, which was actually the zip code, so
        # _format_phone rejected nearly every entry. Fixed layout
        # verified against the live CSV 2026-04-22.
        'applicant_columns': [
            'occupation_number',       # 0
            'occupation_description',  # 1
            'first_name',              # 2
            'middle_name',             # 3  (was 'second_name')
            'last_name',               # 4
            'suffix',                  # 5
            'address_1',               # 6
            'address_2',               # 7
            'address_3',               # 8  (new)
            'city',                    # 9
            'state',                   # 10
            'zip',                     # 11
            'country_code',            # 12 (new)
            'phone',                   # 13 (was at index 11)
            'phone_ext',               # 14
        ],
        # CONSTRUCTIONLICENSE_1.csv has 22 columns; this 21-entry list
        # covers the fields the matcher needs (the last column is an
        # unused trailer).
        'licensee_columns': [
            'board_number', 'occupation_code', 'licensee_name', 'dba_name',
            'class_code', 'address_1', 'address_2', 'address_3',
            'city', 'state', 'zip', 'county_code', 'license_number',
            'primary_status', 'secondary_status', 'original_date',
            'effective_date', 'expiration_date', 'blank', 'renewal_period',
            'alternate_lic',
        ],
        # prod_cities FL slugs we care about. Match is city-scoped to
        # avoid pulling in a Miami contractor for a St Petersburg profile.
        # V410 (loop): dropped "inverness" from this list — the only
        # Inverness in CITY_REGISTRY is a CA filter on Marin County
        # (Inverness, CA in West Marin), not Inverness FL. FL DBPR
        # phones could never match CA contractor profiles, so the slug
        # was just wasting cycles and adding a confusing entry.
        'city_slugs': ['miami-dade-county', 'saint-petersburg', 'cape-coral',
                       'orlando-fl', 'fort-lauderdale', 'tallahassee',
                       'miami', 'tampa', 'jacksonville',
                       'hialeah', 'hollywood-fl', 'pembroke-pines',
                       'coral-springs', 'gainesville', 'pompano-beach',
                       'west-palm-beach', 'clearwater', 'lakeland',
                       'palm-bay', 'brandon-fl'],
    },
}


def _norm(text: str) -> str:
    """Normalize a business name for fuzzy equality.

    V241 P0: delegates to `contractor_enrichment.normalize_contractor_name`
    — the exact same function that populates
    contractor_profiles.contractor_name_normalized. Earlier versions
    used a separate uppercase + token-strip variant, so license-data
    keys never matched profile keys and every license import returned
    `matched: 0` despite finding thousands of candidates.
    """
    # Import inside the function to avoid a circular at module-load
    # time (contractor_enrichment imports db which imports us in
    # some code paths).
    from contractor_enrichment import normalize_contractor_name
    return normalize_contractor_name(text or '')


def _name_tokens(text: str) -> set[str]:
    """Token set for fuzzy overlap matching — same normalization as
    `_norm` but returns a set instead of a string. Single-char tokens
    are dropped (middle initials, trailing "H" in "JAMES H")."""
    if not text:
        return set()
    t = re.sub(r'[^A-Z0-9 ]', ' ', text.upper())
    return {tok for tok in t.split()
            if len(tok) > 1 and tok not in BUSINESS_WORDS}


def _looks_like_business(name: str) -> bool:
    """True if this name contains any business-entity marker (LLC/INC/
    CORP/CO/COMPANY/SERVICES/...). Used to decide whether to run the
    person-name flip logic."""
    if not name:
        return False
    words = set(re.sub(r'[^A-Z ]', ' ', name.upper()).split())
    return bool(words & BUSINESS_WORDS)


def _fl_name_variants(raw: str) -> list[str]:
    """Return the normalized name forms to try when matching an FL
    contractor_profile. Handles all three observed formats:

    - FORMAT 1 "FIRST LAST"        → also try "LAST FIRST"
    - FORMAT 2 "LAST, FIRST MIDDLE *" (St Pete style) → strip `*`,
      also try reordered "FIRST LAST"
    - FORMAT 3 business name        → just the normalized name
    """
    if not raw:
        return []
    # Strip the "* " marker St Petersburg appends to some permit names.
    cleaned = raw.rstrip().rstrip('*').strip().rstrip(',').strip()
    variants = [cleaned]
    if _looks_like_business(cleaned):
        return [_norm(v) for v in variants if _norm(v)]

    # LAST, FIRST [M] → FIRST LAST
    if ',' in cleaned:
        parts = [p.strip() for p in cleaned.split(',', 1)]
        if len(parts) == 2 and parts[0] and parts[1]:
            first_parts = parts[1].split()
            first = first_parts[0] if first_parts else ''
            last = parts[0]
            if first and last:
                variants.append(f'{first} {last}')
    else:
        # FIRST LAST → LAST, FIRST  and  LAST FIRST
        tokens = cleaned.split()
        if len(tokens) == 2:
            variants.append(f'{tokens[1]}, {tokens[0]}')
            variants.append(f'{tokens[1]} {tokens[0]}')
        elif len(tokens) == 3:
            variants.append(f'{tokens[2]}, {tokens[0]}')
            variants.append(f'{tokens[2]} {tokens[0]}')

    # Dedupe while preserving order.
    seen = set()
    out = []
    for v in variants:
        n = _norm(v)
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


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


def _fetch_aspnet_csv(config: dict) -> list[dict]:
    """V240b: download a CSV from an ASP.NET WebForms page that requires
    a multi-step postback sequence (ViewState + __EVENTTARGET).

    Used for CA CSLB's dataportal/ContractorList — the actual download
    link is a LinkButton that posts back to the same URL, so we have to
    round-trip ViewState through each step, then capture the CSV body
    from the final response.

    `config` must supply:
      - 'download_url': the ASP.NET page URL
      - 'aspnet_steps': list of form postbacks to execute in order.
        Each step is a dict with either:
          * 'fields': {name: value, ...}        — extra form fields merged in
          * 'eventtarget': '<asp-control-id>'   — __EVENTTARGET value
    """
    from bs4 import BeautifulSoup

    url = config['download_url']
    steps = config.get('aspnet_steps', [])

    session = requests.Session()
    session.headers.update({
        'User-Agent': ('Mozilla/5.0 (X11; Linux x86_64) '
                       'AppleWebKit/537.36 (KHTML, like Gecko) '
                       'Chrome/120 Safari/537.36'),
    })

    def _viewstate_payload(html: str) -> dict:
        """Extract the three ViewState fields that every ASP.NET
        postback needs. Returns empty dict if the page doesn't have
        them (unusual — would indicate a redirect or error page)."""
        soup = BeautifulSoup(html, 'html.parser')
        payload = {}
        for name in ('__VIEWSTATE', '__VIEWSTATEGENERATOR',
                     '__EVENTVALIDATION', '__VIEWSTATEENCRYPTED'):
            tag = soup.find('input', {'name': name})
            if tag and tag.get('value') is not None:
                payload[name] = tag['value']
        return payload

    # Step 0: GET the page to seed cookies + ViewState.
    resp = session.get(url, timeout=CSV_DOWNLOAD_TIMEOUT)
    resp.raise_for_status()
    state = _viewstate_payload(resp.text)

    last_resp = resp
    for i, step in enumerate(steps):
        form = dict(state)
        form.setdefault('__EVENTTARGET', step.get('eventtarget', ''))
        form.setdefault('__EVENTARGUMENT', '')
        for k, v in (step.get('fields') or {}).items():
            form[k] = v
        if 'eventtarget' in step:
            form['__EVENTTARGET'] = step['eventtarget']
        print(f"[V240b] aspnet step {i+1}/{len(steps)}: "
              f"EVENTTARGET={form.get('__EVENTTARGET','')!r}", flush=True)
        resp = session.post(url, data=form,
                            timeout=CSV_DOWNLOAD_TIMEOUT,
                            allow_redirects=True)
        resp.raise_for_status()
        last_resp = resp
        # If the server handed back a CSV (by Content-Type or -Disposition)
        # we're done — the next step is irrelevant.
        ct = resp.headers.get('Content-Type', '').lower()
        cd = resp.headers.get('Content-Disposition', '').lower()
        if 'csv' in ct or 'attachment' in cd or '.csv' in cd:
            print(f"[V240b] received CSV after step {i+1} "
                  f"({len(resp.content):,} bytes)", flush=True)
            break
        # Otherwise parse the new ViewState for the next postback.
        state = _viewstate_payload(resp.text)

    body = last_resp.content
    try:
        text = body.decode('utf-8')
    except UnicodeDecodeError:
        text = body.decode('latin-1')

    # Diagnostic: print the header line so Cowork can cross-check
    # field_map against the actual CSV columns without waiting for a
    # match-rate analysis.
    first_line = text.split('\n', 1)[0] if text else ''
    print(f"[V240b] CSV header: {first_line[:400]}", flush=True)

    reader = csv.DictReader(io.StringIO(text))
    rows = []
    for row in reader:
        if not row:
            continue
        rows.append({k: (v or '').strip() for k, v in row.items() if k})
    return rows


def _fetch_csv_with_header(url: str) -> list[dict]:
    """Download a CSV that already has a header row. Returns list of
    dicts keyed by the column names the file declares.

    V240: MN DLI CSV pattern — unlike FL DBPR (headerless positional
    columns), state portals that ship Content-Disposition CSV downloads
    usually include a standard header. csv.DictReader handles the rest.

    Decoding: tries UTF-8 first but falls back to latin-1 for state
    portals that export from Windows systems (MN DLI was hitting a
    0xa0 non-breaking-space byte in UTF-8 mode).
    """
    r = requests.get(url, timeout=CSV_DOWNLOAD_TIMEOUT)
    r.raise_for_status()
    body = r.content
    try:
        text = body.decode('utf-8')
    except UnicodeDecodeError:
        text = body.decode('latin-1')
    reader = csv.DictReader(io.StringIO(text))
    rows = []
    for row in reader:
        if not row:
            continue
        rows.append({k: (v or '').strip() for k, v in row.items() if k})
    return rows


def _fetch_csv_no_header(url: str, columns: list[str]) -> list[dict]:
    """Download a headerless CSV and return rows as dicts keyed by the
    positional column names. Trims whitespace on every value.

    V242 P1: was `stream=True` + `iter_lines`, which hung indefinitely
    mid-body on FL DBPR's certified licensees CSV. requests' `timeout`
    only covers the initial connect under stream mode, so a server
    that stalls mid-body never triggers a timeout. Switched to a
    non-streaming download with a (connect=30s, read=300s) tuple that
    bounds the whole response body, plus a 3-attempt retry with
    exponential backoff. Per-attempt byte count is logged so a future
    stall is diagnosable from Render output alone.
    """
    last_err = None
    body = None
    for attempt in range(1, 4):
        t0 = time.time()
        try:
            r = requests.get(url, timeout=(30, 300))
            r.raise_for_status()
            body = r.content
            print(f"[V242] CSV attempt {attempt} OK: {len(body):,} bytes "
                  f"in {time.time()-t0:.1f}s ({url})", flush=True)
            break
        except Exception as e:
            last_err = e
            wait = 30 * attempt  # 30s, 60s, 90s
            print(f"[V242] CSV attempt {attempt} FAILED after "
                  f"{time.time()-t0:.1f}s: {e}; "
                  f"{'retrying in ' + str(wait) + 's' if attempt < 3 else 'giving up'} "
                  f"({url})", flush=True)
            if attempt < 3:
                time.sleep(wait)
    if body is None:
        raise last_err if last_err else RuntimeError("CSV download failed")

    try:
        text = body.decode('utf-8')
    except UnicodeDecodeError:
        text = body.decode('latin-1')
    reader = csv.reader(io.StringIO(text), skipinitialspace=True)
    n_cols = len(columns)
    rows = []
    for raw_row in reader:
        if not raw_row:
            continue
        row_vals = (raw_row + [''] * n_cols)[:n_cols]
        rows.append({columns[i]: (row_vals[i] or '').strip()
                     for i in range(n_cols)})
    return rows


def _download_csv_to_tmp(url: str) -> str:
    """V244c: download a CSV to a /tmp file so the bytes never live
    permanently in the Python heap. V244's in-memory streaming
    approach still OOM'd Render's 512MB worker because
    `requests.get().content` + `body.decode()` + `io.StringIO(text)`
    held ~200MB per CSV in flight. Writing to disk drops the peak to
    the size of `response.content` (which we delete immediately after
    writing) plus any Python allocator residual.

    Returns the tempfile path. Caller is responsible for unlinking
    the file when done.
    """
    import os as _os
    import tempfile as _tempfile
    r = requests.get(url, timeout=(30, 300))
    r.raise_for_status()
    body = r.content
    fd, path = _tempfile.mkstemp(prefix='fl_dbpr_', suffix='.csv')
    try:
        with _os.fdopen(fd, 'wb') as fp:
            fp.write(body)
    except Exception:
        try:
            _os.unlink(path)
        except Exception:
            pass
        raise
    size = len(body)
    del body
    import gc as _gc
    _gc.collect()
    print(f"[V244c] wrote {size:,} bytes from {url} to {path}", flush=True)
    return path


def _iter_csv_no_header_from_file(path: str, columns: list[str]):
    """V244c: stream-parse a CSV file from disk with the same
    positional-column schema the DBPR files use. Never materializes
    the full response body — reads the file line-by-line."""
    n_cols = len(columns)
    row_count = 0
    # latin-1 never errors (it's a single-byte encoding), so we skip
    # the utf-8-first-then-fallback dance here. Every FL DBPR row the
    # collector has seen decodes cleanly via latin-1.
    with open(path, encoding='latin-1', newline='') as fp:
        for raw_row in csv.reader(fp, skipinitialspace=True):
            if not raw_row:
                continue
            row_vals = (raw_row + [''] * n_cols)[:n_cols]
            yield {columns[i]: (row_vals[i] or '').strip()
                   for i in range(n_cols)}
            row_count += 1
    print(f"[V244c] iterated {row_count:,} rows from {path}", flush=True)


def _iter_csv_no_header(url: str, columns: list[str]):
    """V244 compatibility shim. Downloads to /tmp and streams from
    disk — the in-memory variant from V244 still OOM'd on FL DBPR
    even after the dict-list-removal refactor."""
    import os as _os
    path = _download_csv_to_tmp(url)
    try:
        yield from _iter_csv_no_header_from_file(path, columns)
    finally:
        try:
            _os.unlink(path)
        except Exception:
            pass


def _build_fl_applicant_phone_sqlite(applicants_csv_path: str,
                                     applicant_columns: list[str]) -> str:
    """V244c: write applicant phones to a /tmp SQLite DB instead of an
    in-memory dict. Prior versions kept ~100K name→phone entries in a
    Python dict — with per-entry overhead that was pushing 30-50MB on
    its own and contributing to the 512MB OOM.

    SQLite on disk has near-zero resident memory (OS page cache aside)
    and a single indexed lookup per licensee row is fast enough to not
    dominate runtime.

    Returns the SQLite DB path. Caller owns cleanup.
    """
    import os as _os
    import sqlite3 as _sq
    import tempfile as _tempfile

    fd, db_path = _tempfile.mkstemp(prefix='fl_dbpr_phone_', suffix='.db')
    _os.close(fd)
    conn = _sq.connect(db_path)
    conn.execute('PRAGMA journal_mode = OFF')
    conn.execute('PRAGMA synchronous = OFF')
    conn.execute('''
        CREATE TABLE applicant_phones (
            name_norm TEXT PRIMARY KEY,
            phone TEXT NOT NULL
        )
    ''')
    # V303: add a second index keyed on occupation_number (DBPR license
    # number — same ID appears in both applicant + licensee CSVs). The
    # original name_norm key only matches when the licensee field is an
    # individual's name; 99% of real licensees in CONSTRUCTIONLICENSE_1
    # are businesses ("ABC CONSTRUCTION INC") so name_norm never hits.
    # License-number join is the natural key, boosts match rate from
    # ~2 phones/100K rows to the full set.
    conn.execute('''
        CREATE TABLE applicant_phones_by_license (
            license_number TEXT PRIMARY KEY,
            phone TEXT NOT NULL
        )
    ''')

    inserted = 0
    skipped = 0
    batch: list[tuple] = []
    batch_lic: list[tuple] = []
    BATCH = 2000
    for row in _iter_csv_no_header_from_file(applicants_csv_path, applicant_columns):
        first = row.get('first_name', '').strip()
        last = row.get('last_name', '').strip()
        lic_no = row.get('occupation_number', '').strip()
        phone = _format_phone(row.get('phone', ''))
        if not phone:
            skipped += 1
            continue
        # Index by license_number always (V303) — works for business licensees.
        if lic_no:
            batch_lic.append((lic_no, phone))
        # Also keep the name index for the minority of individual-name
        # licensee rows (sole proprietors, handyman licenses, etc.).
        if first and last:
            key = _norm(f'{first} {last}')
            if key:
                batch.append((key, phone))
        if len(batch) >= BATCH:
            conn.executemany(
                'INSERT OR IGNORE INTO applicant_phones(name_norm, phone) VALUES (?, ?)',
                batch,
            )
            inserted += conn.total_changes
            batch.clear()
        if len(batch_lic) >= BATCH:
            conn.executemany(
                'INSERT OR IGNORE INTO applicant_phones_by_license(license_number, phone) VALUES (?, ?)',
                batch_lic,
            )
            batch_lic.clear()
    if batch:
        conn.executemany(
            'INSERT OR IGNORE INTO applicant_phones(name_norm, phone) VALUES (?, ?)',
            batch,
        )
    if batch_lic:
        conn.executemany(
            'INSERT OR IGNORE INTO applicant_phones_by_license(license_number, phone) VALUES (?, ?)',
            batch_lic,
        )
    conn.commit()
    row = conn.execute('SELECT COUNT(*) FROM applicant_phones').fetchone()
    total = row[0] if row else 0
    row2 = conn.execute('SELECT COUNT(*) FROM applicant_phones_by_license').fetchone()
    total_lic = row2[0] if row2 else 0
    print(f"[V303] applicant phone index: {total:,} name_norm / {total_lic:,} license_number", flush=True)
    conn.close()
    print(f"[V244c] applicant phone SQLite: {total:,} rows at {db_path} "
          f"(skipped {skipped:,})", flush=True)
    return db_path


def _normalize_city(raw: str) -> str:
    """Collapse city strings to a canonical form for filter comparison.
    'SAINT PETERSBURG' / 'St. Petersburg' / 'ST PETERSBURG' all need to
    match the prod_cities slug `saint-petersburg`."""
    if not raw:
        return ''
    t = raw.upper().replace('.', '').replace(',', '').strip()
    t = re.sub(r'\s+', ' ', t)
    t = t.replace(' ', '-')
    # Handle common St/Saint prefix variants.
    if t.startswith('ST-'):
        t = 'SAINT-' + t[3:]
    return t.lower()


def _build_fl_profile_index(city_slugs: list[str]) -> dict:
    """V244: Load the FL contractor_profiles that still lack a phone
    and index them by every normalized name variant `_fl_name_variants`
    generates. Small (<20K profiles, each with 1-3 variants) so it fits
    comfortably in memory while the 100K+ DBPR CSVs stream past.

    Returns: {normalized_variant: {'id':..., 'city_slug':...}, ...}
    plus a set of variant keys for O(1) lookup.
    """
    placeholders = ','.join('?' * len(city_slugs))
    conn = permitdb.get_connection()
    rows = conn.execute(f"""
        SELECT id, contractor_name_raw, source_city_key, phone
        FROM contractor_profiles
        WHERE source_city_key IN ({placeholders})
          AND (phone IS NULL OR phone = '')
    """, city_slugs).fetchall()

    idx: dict[str, dict] = {}
    for row in rows:
        pid = row['id'] if isinstance(row, dict) else row[0]
        raw = row['contractor_name_raw'] if isinstance(row, dict) else row[1]
        slug = row['source_city_key'] if isinstance(row, dict) else row[2]
        for variant in _fl_name_variants(raw):
            if variant and variant not in idx:
                idx[variant] = {'id': pid, 'city_slug': slug}
    print(f"[V244] FL profile index: {len(rows):,} profiles → "
          f"{len(idx):,} variants", flush=True)
    return idx


def _commit_fl_batch(batch: list[tuple], state_code: str) -> int:
    """V244: flush a batch of (profile_id, phone, license_number,
    license_status) tuples in a single transaction. Keeps commit
    overhead off the per-row hot path during streaming."""
    if not batch:
        return 0
    conn = permitdb.get_connection()
    now = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    n = 0
    for pid, phone, lic_num, lic_status in batch:
        try:
            conn.execute("""
                UPDATE contractor_profiles
                SET phone = COALESCE(?, phone),
                    license_number = COALESCE(NULLIF(?, ''), license_number),
                    license_status = ?,
                    enrichment_status = 'enriched',
                    enriched_at = ?, updated_at = ?
                WHERE id = ?
            """, (phone, lic_num, lic_status, now, now, pid))
            conn.execute("""
                INSERT INTO enrichment_log
                    (contractor_profile_id, source, status, cost, created_at)
                VALUES (?, ?, 'enriched', 0.0, ?)
            """, (pid, f'license:{state_code}', now))
            n += 1
        except Exception as e:
            print(f"[V244] {state_code} write error for profile {pid}: {e}",
                  flush=True)
    conn.commit()
    return n


def _import_fl_streaming(state_code: str, config: dict) -> dict:
    """V244c: disk-backed FL DBPR import.

    V244's in-memory streaming STILL OOM'd Render's 512MB worker.
    Peak was ~200MB per CSV because `requests.get().content` +
    `.decode()` + `io.StringIO(text)` kept multiple copies of the
    body in Python heap. Plus a ~100K-entry applicant phone dict
    with Python's per-entry overhead was another 50MB. Combined with
    daemon/Flask/Python runtime (~120MB baseline), peak exceeded 512MB.

    New pipeline (V244c):
      1. Load profile_idx from DB (small — 10-20K string→int dict).
      2. Download each CSV to a /tmp file via non-streaming `.content`,
         then immediately `del body` and `gc.collect()` so the bytes
         don't linger in the Python heap.
      3. Build the applicant phone index as a /tmp SQLite DB rather
         than a Python dict — near-zero resident memory, indexed
         lookups are fast enough.
      4. Stream each licensee CSV from disk, match against
         profile_idx (in-memory), resolve phone via SQLite lookup,
         stage updates in 500-row batches, commit per batch.
      5. Clean up all /tmp files in `finally`.

    Peak memory: profile_idx (~5MB) + one CSV's `body` in flight
    during download (~50MB, released right after write) + SQLite
    DB handle + Python runtime overhead ≈ 60-80MB. Well under 512MB
    even with Flask + daemon co-resident.
    """
    import os as _os
    import sqlite3 as _sq
    import gc as _gc

    urls = config['source_urls']
    slugs = config['city_slugs']
    profile_idx = _build_fl_profile_index(slugs)
    if not profile_idx:
        print(f"[V244c] FL: no FL profiles pending — nothing to match",
              flush=True)
        return {
            'candidates': 0, 'matched': 0,
            'licensees': 0, 'applicants_phone_indexed': 0,
        }

    tmp_paths: list[str] = []
    phone_db_path: str | None = None
    phone_conn = None
    try:
        # Phase 2a: download applicants + build phone SQLite.
        print(f"[V244c] FL: downloading applicants CSV…", flush=True)
        applicants_path = _download_csv_to_tmp(urls['applicants'])
        tmp_paths.append(applicants_path)
        phone_db_path = _build_fl_applicant_phone_sqlite(
            applicants_path, config['applicant_columns']
        )
        # Applicants CSV no longer needed — its data is now in SQLite.
        try:
            _os.unlink(applicants_path)
            tmp_paths.remove(applicants_path)
        except Exception:
            pass
        _gc.collect()

        phone_conn = _sq.connect(phone_db_path)
        phone_conn.row_factory = None  # tuple rows, one column

        def _lookup_phone(name_norm: str) -> str | None:
            if not name_norm:
                return None
            row = phone_conn.execute(
                'SELECT phone FROM applicant_phones WHERE name_norm = ?',
                (name_norm,),
            ).fetchone()
            return row[0] if row else None

        # V303: license-number lookup for business licensees (most of them).
        def _lookup_phone_by_license(license_number: str) -> str | None:
            if not license_number:
                return None
            row = phone_conn.execute(
                'SELECT phone FROM applicant_phones_by_license WHERE license_number = ?',
                (license_number,),
            ).fetchone()
            return row[0] if row else None

        # Phase 2b+2c: stream each licensee CSV, match + commit.
        matched_total = 0
        licensee_count = 0
        batch: list[tuple] = []
        BATCH_SIZE = 500
        updated_ids: set = set()

        # V244d: one licensee file (CONSTRUCTIONLICENSE_1.csv), not two.
        for csv_key in ('licensees',):
            print(f"[V244c] FL: downloading {csv_key} licensees…", flush=True)
            csv_path = _download_csv_to_tmp(urls[csv_key])
            tmp_paths.append(csv_path)
            try:
                for lic in _iter_csv_no_header_from_file(
                        csv_path, config['licensee_columns']):
                    licensee_count += 1
                    ln_norm = _norm(lic.get('licensee_name', ''))
                    dba_norm = _norm(lic.get('dba_name', ''))

                    hit = profile_idx.get(ln_norm) or profile_idx.get(dba_norm)
                    if not hit:
                        continue
                    if hit['id'] in updated_ids:
                        continue

                    lic_city_slug = _normalize_city(lic.get('city', ''))
                    if lic_city_slug and hit['city_slug'] and lic_city_slug != hit['city_slug']:
                        # Allow substring-contains either direction
                        # (e.g. "miami-dade-county" vs DBPR city="MIAMI").
                        if lic_city_slug not in hit['city_slug'] and hit['city_slug'] not in lic_city_slug:
                            continue

                    # V303: license_number (board_number) is the natural
                    # join key between applicant + licensee files. Try it
                    # first, then fall back to the name-based lookups.
                    board_number = lic.get('board_number') or ''
                    phone = _lookup_phone_by_license(board_number)
                    if not phone:
                        phone = _lookup_phone(ln_norm)
                    if not phone and ',' in lic.get('licensee_name', ''):
                        parts = [p.strip() for p in lic['licensee_name'].split(',', 1)]
                        if len(parts) == 2 and parts[0] and parts[1]:
                            first = parts[1].split()[0]
                            phone = _lookup_phone(_norm(f'{first} {parts[0]}'))

                    license_number = lic.get('license_number') or ''
                    primary_status = lic.get('primary_status') or ''
                    secondary_status = lic.get('secondary_status') or ''
                    expiration = lic.get('expiration_date') or ''
                    status_parts = [s for s in (primary_status, secondary_status) if s]
                    if _is_expired(expiration):
                        status_parts.append('expired')
                    license_status = '|'.join(status_parts) if status_parts else 'unknown'

                    batch.append((hit['id'], phone, license_number, license_status))
                    updated_ids.add(hit['id'])

                    if len(batch) >= BATCH_SIZE:
                        matched_total += _commit_fl_batch(batch, state_code)
                        print(f"[V244c] FL: committed batch, "
                              f"{matched_total:,} matched / "
                              f"{licensee_count:,} licensees scanned",
                              flush=True)
                        batch = []
            finally:
                try:
                    _os.unlink(csv_path)
                    tmp_paths.remove(csv_path)
                except Exception:
                    pass
                _gc.collect()

        if batch:
            matched_total += _commit_fl_batch(batch, state_code)

        row = phone_conn.execute(
            'SELECT COUNT(*) FROM applicant_phones'
        ).fetchone()
        phone_indexed = row[0] if row else 0

        return {
            'candidates': len(profile_idx),
            'matched': matched_total,
            'licensees': licensee_count,
            'applicants_phone_indexed': phone_indexed,
        }
    finally:
        if phone_conn is not None:
            try:
                phone_conn.close()
            except Exception:
                pass
        for p in (tmp_paths + ([phone_db_path] if phone_db_path else [])):
            try:
                _os.unlink(p)
            except Exception:
                pass


def _fetch_arcgis_fs(url: str, where: str = '1=1') -> list[dict]:
    """V297: Page through an ArcGIS FeatureServer layer and return one
    flat list of attribute dicts. Used for Nashville's
    Registered_Professional_Contractors_view_2 (7,574 rows with Phone +
    E_Mail inline). Same output shape as _fetch_socrata so the downstream
    _enrich_by_name path is unchanged.
    """
    results = []
    offset = 0
    batch = 2000
    base = url.rstrip('/').rstrip('/query')
    while True:
        params = {
            'where': where,
            'outFields': '*',
            'returnGeometry': 'false',
            'resultOffset': offset,
            'resultRecordCount': batch,
            'f': 'json',
        }
        r = requests.get(base + '/query', params=params, timeout=SOCRATA_TIMEOUT)
        r.raise_for_status()
        body = r.json()
        if 'error' in body:
            raise RuntimeError(f"ArcGIS error: {body['error']}")
        feats = body.get('features', [])
        if not feats:
            break
        for f in feats:
            attrs = f.get('attributes', {})
            if attrs:
                results.append(attrs)
        if len(feats) < batch and not body.get('exceededTransferLimit'):
            break
        offset += batch
    return results


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
          AND (enrichment_status IS NULL
               OR enrichment_status NOT IN ('no_source', 'enriched'))
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
        # V300: registry datasets (e.g. Nashville
        # Registered_Professional_Contractors_view_2, Cleveland
        # Active_Contractor_Registrations, LV Business_Licenses_OpenData)
        # don't all carry a license_number or license_exp column. Treat
        # those field_map keys as optional — missing → '' on write.
        phone = _format_phone(hit.get(fm['phone']))
        lic = hit.get(fm.get('license_number', ''), '') or ''
        lic_type = hit.get(fm.get('license_type', ''), '') or ''
        lic_exp = hit.get(fm.get('license_exp', ''), '') or ''
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

    Dispatches on `config['format']`:
      - 'socrata'  → single JSON endpoint (Oregon CCB).
      - 'fl_dbpr'  → three headerless CSVs cross-referenced (FL DBPR).
      - 'csv_dict' → CSV with header (MN DLI).
      - 'aspnet_csv' → ASP.NET postback CSV (CA CSLB).

    Returns a dict with per-strategy counters. Raises ValueError on
    unknown state_code. Caller is responsible for spawning this in a
    background thread if the run might exceed Render's 30s HTTP timeout.

    V242 P0: sets the module-level IMPORT_IN_PROGRESS Event for the
    whole run so the collection + enrichment daemons skip their cycles
    and SQLite WAL contention doesn't stretch a 12s import into 51
    minutes (observed on NY, V241).
    """
    IMPORT_IN_PROGRESS.set()
    try:
        return _import_state_inner(state_code)
    finally:
        IMPORT_IN_PROGRESS.clear()


def _import_state_inner(state_code: str) -> dict:
    state_code = state_code.upper()
    if state_code not in STATE_CONFIGS:
        raise ValueError(f'Unknown state {state_code!r} — '
                         f'available: {sorted(STATE_CONFIGS)}')

    config = STATE_CONFIGS[state_code]
    fmt = config.get('format', 'socrata')
    t0 = time.time()

    if fmt == 'fl_dbpr':
        # V244 P0: swapped from the load-all-then-process _fetch_fl_dbpr
        # path (which held three ~50MB CSVs + two in-memory indexes
        # simultaneously and OOM'd Render twice) to a streaming
        # pipeline that never materializes a licensee CSV as a list.
        print(f"[V244] {state_code}: streaming {config['name']}", flush=True)
        summary = {
            'state': state_code,
            'strategy': config.get('match_strategy', 'name_fuzzy'),
        }
        summary['by_name'] = _import_fl_streaming(state_code, config)
        summary['elapsed_seconds'] = round(time.time() - t0, 2)
        print(f"[V244] {state_code}: import complete — {summary}", flush=True)
        return summary

    if fmt == 'aspnet_csv':
        # V240b: CA CSLB pattern — the "download" is actually an ASP.NET
        # postback sequence that eventually returns a CSV body. After
        # the fetch, reuse the same status-filter + name-match path the
        # csv_dict format uses.
        print(f"[V240b] {state_code}: starting ASP.NET download for "
              f"{config['name']}", flush=True)
        raw = _fetch_aspnet_csv(config)
        status_filter = config.get('status_filter')
        if status_filter:
            fld = status_filter['field']
            target = status_filter['value'].upper()
            raw = [r for r in raw if r.get(fld, '').upper() == target]
        print(f"[V240b] {state_code}: {len(raw):,} rows post-filter "
              f"in {time.time()-t0:.1f}s", flush=True)
        summary = {
            'state': state_code,
            'source_rows': len(raw),
            'strategy': config.get('match_strategy', 'name'),
        }
        summary['by_name'] = _enrich_by_name(state_code, config, {}, raw)
        summary['elapsed_seconds'] = round(time.time() - t0, 2)
        print(f"[V240b] {state_code}: import complete — {summary}", flush=True)
        return summary

    if fmt == 'arcgis_fs':
        # V297: ArcGIS FeatureServer registry (e.g. Nashville
        # Registered_Professional_Contractors_view_2). Same fetch →
        # name-match pipeline as Socrata/csv_dict; just a different
        # pager on the fetch side.
        print(f"[V297] {state_code}: fetching {config['name']}", flush=True)
        raw = _fetch_arcgis_fs(
            config['arcgis_url'],
            where=config.get('arcgis_where', '1=1'),
        )
        print(f"[V297] {state_code}: fetched {len(raw)} rows in "
              f"{time.time()-t0:.1f}s", flush=True)
        summary = {
            'state': state_code,
            'source_rows': len(raw),
            'strategy': config.get('match_strategy', 'name'),
        }
        summary['by_name'] = _enrich_by_name(state_code, config, {}, raw)
        summary['elapsed_seconds'] = round(time.time() - t0, 2)
        print(f"[V297] {state_code}: import complete — {summary}", flush=True)
        return summary

    if fmt == 'csv_dict':
        # V240: simple CSV-with-header download (e.g. MN DLI). Stream,
        # parse via DictReader, optionally post-filter by a status
        # column, then reuse the name-match path.
        print(f"[V240] {state_code}: fetching {config['name']}", flush=True)
        raw = _fetch_csv_with_header(config['source_url'])
        status_filter = config.get('status_filter')
        if status_filter:
            fld = status_filter['field']
            target = status_filter['value'].upper()
            raw = [r for r in raw if r.get(fld, '').upper() == target]
        print(f"[V240] {state_code}: {len(raw):,} rows post-filter "
              f"in {time.time()-t0:.1f}s", flush=True)
        # Empty license_idx — the name-match path doesn't need it, but
        # _enrich_by_name still expects the arg to be a dict.
        summary = {
            'state': state_code,
            'source_rows': len(raw),
            'strategy': config.get('match_strategy', 'name'),
        }
        summary['by_name'] = _enrich_by_name(state_code, config, {}, raw)
        summary['elapsed_seconds'] = round(time.time() - t0, 2)
        print(f"[V240] {state_code}: import complete — {summary}", flush=True)
        return summary

    # Default: Socrata (Oregon CCB and future JSON-endpoint states).
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
