"""V547i regression tests for the plain-Accela CapDetail deep-fetch.

The deep-fetch path follows each Accela ACA search-grid row's detail
link and extracts contractor info from the CapDetail.aspx HTML — the
fix for SBCO and 42 other plain-`accela` cities whose search grid has
no contractor column.
"""
from unittest.mock import patch, MagicMock

import accela_portal_collector as apc


# A minimal CapDetail-like HTML fixture. The parser regex looks for
# 'Licensed Professional:' then captures business name + license +
# email + phone within the next ~1500 chars.
_FIXTURE_DETAIL_HTML = """
<html><body>
<div>
  Licensed Professional:
  ACME CONSTRUCTION INC
  100 Main St, Anytown CA
  License Type: General Building Contractor
  License Number: B12345678
  Email: contact@acme-construction.example
  Phone: (555) 123-4567
</div>
<div>Project Description: Single family residence</div>
</body></html>
"""


def test_v547i_parse_accela_licensed_professional_extracts_contractor():
    """V547i pre-req: the V476 parser must extract a contractor name
    from a CapDetail-shaped HTML page. If this regresses, deep_fetch
    yields nothing for SBCO."""
    parsed = apc.parse_accela_licensed_professional(_FIXTURE_DETAIL_HTML)
    assert parsed.get('contractor_name'), (
        f'V547i regression: parser failed to extract contractor_name '
        f'from CapDetail fixture. Output: {parsed}'
    )
    assert 'ACME CONSTRUCTION' in parsed['contractor_name'], (
        f'V547i regression: extracted unexpected contractor name '
        f'{parsed["contractor_name"]!r}'
    )


def test_v547i_sbco_config_has_deep_fetch_true():
    """V547i: the SBCO config must opt into deep_fetch — this is the
    actual behavior that lifts san-bernardino-county profiles>0.
    Removing the flag re-creates the V547h gabriel-class bug."""
    from city_registry_data import CITY_REGISTRY
    sbco = CITY_REGISTRY.get('san_bernardino_county_ca')
    assert sbco is not None, 'V547i: SBCO config missing entirely'
    assert sbco.get('platform') == 'accela', (
        'V547i: SBCO platform changed; deep_fetch only applies to '
        '`accela` platform. Update test if migrating to a different '
        'fetcher.'
    )
    assert sbco.get('deep_fetch') is True, (
        'V547i regression: SBCO no longer opts into deep_fetch. '
        '0 contractor profiles will silently re-emerge for gabriel.'
    )


def test_v547i_fetch_accela_dispatcher_threads_deep_fetch_flag():
    """V547i: fetch_accela() must pass deep_fetch + max_details_per_run
    from config through to fetch_accela_portal. Without that, the
    SBCO config's flag is silently dropped."""
    captured = {}

    def fake_fetch(agency_code, days_back=30, module="Building",
                   tab_name="Building", max_pages=25, portal_base_url=None,
                   deep_fetch=False, max_details_per_run=200):
        captured['agency_code'] = agency_code
        captured['deep_fetch'] = deep_fetch
        captured['max_details_per_run'] = max_details_per_run
        return []

    with patch.object(apc, 'fetch_accela_portal', side_effect=fake_fetch):
        apc.fetch_accela({
            '_accela_city_key': 'san_bernardino_county_ca',
            'deep_fetch': True,
            'max_details_per_run': 200,
        }, days_back=7)

    assert captured.get('deep_fetch') is True, (
        f'V547i regression: dispatcher dropped deep_fetch flag. '
        f'Captured: {captured}'
    )
    assert captured.get('max_details_per_run') == 200, (
        f'V547i regression: dispatcher dropped max_details_per_run. '
        f'Captured: {captured}'
    )


def test_v547i_parse_results_table_captures_detail_url():
    """V547i: _parse_results_table must capture the first <a href> in
    each row as `_detail_url` so the deep-fetch can follow it. Without
    this capture, the deep_fetch loop has nothing to fetch."""
    from bs4 import BeautifulSoup
    grid_html = """
    <table id="ctl00_PlaceHolderMain_dgvPermitList_gdvPermitList">
      <tr class="HeaderRow"><th>Date</th><th>Record Number</th><th>Address</th></tr>
      <tr class="ACA_TabRow_Odd">
        <td>05/01/2026</td>
        <td><a href="CapDetail.aspx?Module=Building&capID1=ABC123">26TMP-019854</a></td>
        <td>9853 ALABAMA ST</td>
      </tr>
    </table>
    """
    soup = BeautifulSoup(grid_html, 'html.parser')
    records, _ = apc._parse_results_table(soup)
    assert records, 'V547i regression: _parse_results_table returned no rows'
    assert records[0].get('_detail_url'), (
        'V547i regression: _parse_results_table did not capture the '
        'detail-page href. Deep-fetch will have nothing to fetch.'
    )
    assert 'CapDetail' in records[0]['_detail_url']
