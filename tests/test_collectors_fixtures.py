"""V527b: per-platform parse() integration tests against recorded fixtures.

Each fixture is hand-crafted to mirror the actual schema/shape of a
real upstream API response (Socrata/ArcGIS/CKAN/Accela). The fixtures
are the contract pin: if a future refactor changes how parse() handles
ArcGIS attribute unwrapping, CKAN's nested result.records, or Accela's
flat dict shape, these tests fire.

Two cities per platform per the V527 directive. csv_state has no
parse() (state license imports write directly to applicant_phones,
not to the permits pipeline) so no fixture there.
"""
from __future__ import annotations

import json
import os

import pytest

_FIXDIR = os.path.join(os.path.dirname(__file__), 'fixtures', 'collectors')


def _load(name):
    with open(os.path.join(_FIXDIR, name)) as f:
        return json.load(f)


# ---------------------------------------------------------------------
# Socrata — flat dict records
# ---------------------------------------------------------------------

def test_parse_socrata_chicago_il():
    """Chicago Building Permits dataset shape: flat dict with
    permit_, permit_type, contact_1_name, etc."""
    from collectors import socrata
    raw = _load('socrata_chicago_il.json')
    field_map = {
        'permit_number': 'permit_',
        'permit_type': 'permit_type',
        'description': 'work_description',
        'date': 'issue_date',
        'filing_date': 'application_start_date',
        'contractor_name': 'contact_1_name',
        'estimated_cost': 'reported_cost',
    }
    out = socrata.parse(raw, field_map)
    assert len(out) == 3, f'Expected 3 chicago permits, got {len(out)}'
    pn = {p['permit_number'] for p in out}
    assert pn == {'100923857', '100923922', '100924001'}
    assert all(p.get('contractor_name') for p in out)
    # Specific value pinned so a regression on field-map application is loud
    chi = next(p for p in out if p['permit_number'] == '100923857')
    assert chi['contractor_name'] == 'ACME CONSTRUCTION CORP'
    assert chi['estimated_cost'] == '85000'


def test_parse_socrata_nyc():
    """NYC DOB NOW shape: job__, permittee_s_business_name, etc."""
    from collectors import socrata
    raw = _load('socrata_nyc.json')
    field_map = {
        'permit_number': 'job__',
        'permit_type': 'permit_type',
        'date': 'issuance_date',
        'filing_date': 'filing_date',
        'contractor_name': 'permittee_s_business_name',
        'owner_name': 'owner_s_business_name',
        'contact_phone': 'permittee_s_phone__',
        'estimated_cost': 'estimated__job_costs',
    }
    out = socrata.parse(raw, field_map)
    assert len(out) == 2
    pn = {p['permit_number'] for p in out}
    assert pn == {'121234567', '121234890'}
    nyc1 = next(p for p in out if p['permit_number'] == '121234567')
    assert nyc1['contractor_name'] == 'BROOKLYN HEIGHTS CONTRACTING LLC'
    assert nyc1['contact_phone'] == '7185551234'


# ---------------------------------------------------------------------
# ArcGIS — {attributes:{}, geometry:{}} wrapping
# ---------------------------------------------------------------------

def test_parse_arcgis_miami_dade_unwraps_attributes():
    """ArcGIS query response: features[].attributes is the actual
    record. parse() must auto-unwrap so the field_map resolves
    against the inner attributes dict."""
    from collectors import arcgis
    raw = _load('arcgis_miami_dade.json')['features']
    field_map = {
        'permit_number': 'PermitNumber',
        'contractor_name': 'ContractorName',
        'owner_name': 'OwnerName',
        'permit_type': 'PermitType',
        'description': 'WorkDescription',
        'address': 'FullAddress',
        'date': 'IssuedDate',
        'estimated_cost': 'EstimatedValue',
    }
    out = arcgis.parse(raw, field_map)
    assert len(out) == 3
    pn = {p['permit_number'] for p in out}
    assert pn == {'MD-2026-04458', 'MD-2026-04501', 'MD-2026-04612'}
    md = next(p for p in out if p['permit_number'] == 'MD-2026-04458')
    assert md['contractor_name'] == 'STRADA SERVICES INC'
    assert md['owner_name'] == 'PALMETTO BAY PROPERTIES LLC'


def test_parse_arcgis_phoenix():
    """Phoenix MapServer shape: same {attributes:{}} wrapping."""
    from collectors import arcgis
    raw = _load('arcgis_phoenix.json')['features']
    field_map = {
        'permit_number': 'PERMITNUMBER',
        'contractor_name': 'CONTRACTOR',
        'permit_type': 'PERMITTYPE',
        'description': 'DESCRIPTION',
        'address': 'ADDRESS',
        'date': 'ISSUEDATE',
        'estimated_cost': 'VALUATION',
    }
    out = arcgis.parse(raw, field_map)
    assert len(out) == 2
    pho = next(p for p in out if p['permit_number'] == 'BLDR-26-0008891')
    assert pho['contractor_name'] == 'DESERT WAVE POOLS LLC'


# ---------------------------------------------------------------------
# Accela — flat dicts post-BS4 scrape
# ---------------------------------------------------------------------

def test_parse_accela_chandler():
    """Accela HTML scrape produces flat dicts keyed by header text
    ('Record Number', 'Description', etc.)."""
    from collectors import accela
    raw = _load('accela_chandler.json')
    field_map = {
        'permit_number': 'Record Number',
        'permit_type': 'Record Type',
        'description': 'Description',
        'address': 'Address',
        'date': 'Date',
        'filing_date': 'Filed Date',
        'issued_date': 'Issue Date',
        'contractor_name': 'Contractor',
        'owner_name': 'Owner Name',
        'estimated_cost': 'Estimated Cost',
    }
    out = accela.parse(raw, field_map)
    assert len(out) == 2
    chd = next(p for p in out if p['permit_number'] == 'BLDR2026-00488')
    assert chd['contractor_name'] == 'SOLAR DESIGN ASSOCIATES INC'


def test_parse_accela_bradenton():
    """Same shape, different city — Manatee County tenant."""
    from collectors import accela
    raw = _load('accela_bradenton.json')
    field_map = {
        'permit_number': 'Record Number',
        'permit_type': 'Record Type',
        'description': 'Description',
        'address': 'Address',
        'date': 'Date',
        'contractor_name': 'Contractor',
        'owner_name': 'Owner Name',
        'estimated_cost': 'Estimated Cost',
    }
    out = accela.parse(raw, field_map)
    assert len(out) == 2
    brd = next(p for p in out if p['permit_number'] == 'BP-2026-1421')
    assert brd['contractor_name'] == 'GULF COAST ROOFING SOLUTIONS'


# ---------------------------------------------------------------------
# CKAN — nested result.records
# ---------------------------------------------------------------------

def test_parse_ckan_pittsburgh():
    """CKAN datastore_search returns {success, result: {records: [...]}}.
    The platform module's caller (collector.fetch_ckan) extracts
    result.records before passing to parse, but the records themselves
    are flat dicts."""
    from collectors import ckan
    raw = _load('ckan_pittsburgh.json')['result']['records']
    field_map = {
        'permit_number': 'permit_number',
        'permit_type': 'permit_type',
        'address': 'address',
        'contractor_name': 'contractor_name',
        'owner_name': 'owner_name',
        'description': 'description',
        'date': 'issue_date',
        'estimated_cost': 'estimated_cost',
    }
    out = ckan.parse(raw, field_map)
    assert len(out) == 2
    p = next(x for x in out if x['permit_number'] == 'BLDR-2026-04-1107')
    assert p['contractor_name'] == 'Phillips Heating & Air Conditioning, Inc.'


def test_parse_ckan_boston():
    """Boston CKAN dataset — different schema from WPRDC.
    permitnumber not permit_number, applicant not contractor_name."""
    from collectors import ckan
    raw = _load('ckan_boston.json')['result']['records']
    field_map = {
        'permit_number': 'permitnumber',
        'permit_type': 'worktype',
        'description': 'description',
        'address': 'address',
        'contractor_name': 'applicant',
        'owner_name': 'owner',
        'date': 'issued_date',
        'estimated_cost': 'declared_valuation',
        'status': 'status',
    }
    out = ckan.parse(raw, field_map)
    assert len(out) == 2
    b = next(x for x in out if x['permit_number'] == 'ALT-2026-04-2891')
    assert b['address'] == '55 BAY STATE RD, BOSTON MA 02215'


# ---------------------------------------------------------------------
# Cross-platform contract: empty inputs are safe
# ---------------------------------------------------------------------

@pytest.mark.parametrize('platform_name', ['socrata', 'arcgis', 'accela', 'ckan'])
def test_parse_safe_on_empty_input(platform_name):
    """Empty list, empty dict, None field_map — none should crash."""
    import collectors
    mod = getattr(collectors, platform_name)
    assert mod.parse([], {}) == []
    assert mod.parse(None, {}) == []
    assert mod.parse([{'x': 1}], None) == []
    # Records missing the permit_number key are dropped (not None'd back)
    assert mod.parse([{'x': 1}], {'permit_number': 'permit_number'}) == []


@pytest.mark.parametrize('platform_name', ['socrata', 'arcgis', 'accela', 'ckan'])
def test_parse_drops_records_without_permit_number(platform_name):
    """A record where field_map['permit_number'] resolves to nothing
    is unusable — must be dropped, not returned with None permit_number
    (which would later violate the upsert NOT NULL constraint)."""
    import collectors
    mod = getattr(collectors, platform_name)
    records = [
        {'permit_': 'P1', 'name': 'good'},
        {'name': 'no permit number'},          # dropped
        {'permit_': '', 'name': 'empty'},      # dropped
        {'permit_': 'P2', 'name': 'also good'},
    ]
    field_map = {'permit_number': 'permit_', 'contractor_name': 'name'}
    out = mod.parse(records, field_map)
    assert len(out) == 2
    assert {p['permit_number'] for p in out} == {'P1', 'P2'}
