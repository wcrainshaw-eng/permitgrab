"""V537 regression tests for permit_processing/.

Pin the 4 permit-data transforms. Bug class: silent display
corruption — wrong category bucket, wrong "Best Leads" sort, wrong
email digest content.
"""
from __future__ import annotations

from datetime import datetime, timedelta


# ---------------------------------------------------------------------
# format_permit_address
# ---------------------------------------------------------------------

def test_format_address_with_street_number():
    from permit_processing import format_permit_address
    p = {'address': '100 Main St'}
    format_permit_address(p)
    assert p['display_address'] == '100 Main St'
    assert p['address_type'] == 'street'


def test_format_address_empty_or_blank():
    from permit_processing import format_permit_address
    for addr in ('', '   '):
        p = {'address': addr}
        format_permit_address(p)
        assert p['display_address'] == 'Address not provided'
        assert p['address_type'] == 'none'


def test_format_address_location_only_uppercase():
    """All-caps short string with no street number → 'Area:' prefix."""
    from permit_processing import format_permit_address
    p = {'address': 'BETHESDA'}
    format_permit_address(p)
    assert p['display_address'] == 'Area: Bethesda'
    assert p['address_type'] == 'location'


def test_format_address_location_short_words():
    """Short address (≤3 words, no number) NOT in the uppercase
    location list → 'Location:' prefix. The test uses an arbitrary
    string ('Maple Glen') that's NOT in the hardcoded county
    uppercase list ('Rock Spring' is in that list and would match
    the Area: branch instead).
    """
    from permit_processing import format_permit_address
    p = {'address': 'Maple Glen'}
    format_permit_address(p)
    assert p['display_address'] == 'Location: Maple Glen'
    assert p['address_type'] == 'location'


def test_format_address_listed_uppercase_county_areas():
    """The hardcoded county list (BETHESDA, ROCKVILLE, ROCK SPRING,
    etc.) gets an 'Area:' prefix — pinning the production behavior."""
    from permit_processing import format_permit_address
    for area in ('BETHESDA', 'Rock Spring', 'Wheaton'):
        p = {'address': area}
        format_permit_address(p)
        assert p['address_type'] == 'location', f'{area!r}'
        assert p['display_address'].startswith('Area: '), (
            f'{area!r}: got {p["display_address"]!r}'
        )


def test_format_address_long_address_without_number():
    """Long address without number → kept as-is, marked street."""
    from permit_processing import format_permit_address
    p = {'address': 'Five Hundred and Twenty Two North Magnolia Drive'}
    format_permit_address(p)
    assert p['display_address'] == 'Five Hundred and Twenty Two North Magnolia Drive'
    assert p['address_type'] == 'street'


# ---------------------------------------------------------------------
# validate_permit_dates
# ---------------------------------------------------------------------

def test_validate_dates_skips_when_no_filing_date():
    from permit_processing import validate_permit_dates
    p = {}
    validate_permit_dates(p)
    assert 'date_label' not in p  # untouched


def test_validate_dates_recent_date_labeled_filed():
    from permit_processing import validate_permit_dates
    today = datetime.now().strftime('%Y-%m-%d')
    p = {'filing_date': today}
    validate_permit_dates(p)
    assert p['date_label'] == 'Filed'


def test_validate_dates_far_future_relabeled_expires():
    """Date >30 days in future → expires (but with no fallback,
    the date_label stays 'Expires'). Pin this branch."""
    from permit_processing import validate_permit_dates
    far = (datetime.now() + timedelta(days=120)).strftime('%Y-%m-%d')
    p = {'filing_date': far}
    validate_permit_dates(p)
    assert p['expiration_date'] == far
    assert p['date_label'] == 'Expires'


def test_validate_dates_far_future_with_fallback_filing_date():
    """Date >30 days future BUT issued_date is recent → relabel both."""
    from permit_processing import validate_permit_dates
    far = (datetime.now() + timedelta(days=120)).strftime('%Y-%m-%d')
    recent = (datetime.now() - timedelta(days=5)).strftime('%Y-%m-%d')
    p = {'filing_date': far, 'issued_date': recent}
    validate_permit_dates(p)
    assert p['expiration_date'] == far
    assert p['filing_date'] == recent
    assert p['date_label'] == 'Filed'


def test_validate_dates_garbage_filing_date_default_filed():
    """Per V537 / production behavior: invalid filing_date → date_label='Filed' default."""
    from permit_processing import validate_permit_dates
    p = {'filing_date': 'WROCCO'}
    validate_permit_dates(p)
    assert p['date_label'] == 'Filed'


# ---------------------------------------------------------------------
# generate_permit_description
# ---------------------------------------------------------------------

def test_generate_description_uses_permit_type():
    from permit_processing import generate_permit_description
    p = {'permit_type': 'Residential Renovation'}
    desc = generate_permit_description(p)
    assert 'Residential Renovation' in desc


def test_generate_description_includes_address_and_value():
    from permit_processing import generate_permit_description
    p = {
        'permit_type': 'Building',
        'address': '100 Main St',
        'estimated_cost': 150000,
    }
    desc = generate_permit_description(p)
    assert '100 Main St' in desc
    assert '$150K project' in desc or '$150,000' in desc


def test_generate_description_skips_max_cost_cap():
    """Production data sometimes has 50_000_000 as a placeholder cap.
    Skip it to avoid lying about project value."""
    from permit_processing import generate_permit_description
    p = {'permit_type': 'Building', 'estimated_cost': 50_000_000}
    desc = generate_permit_description(p)
    assert '50.0M' not in desc and '$50,000,000' not in desc


def test_generate_description_keeps_real_existing_when_unique():
    """Substantial existing description with permit number → keep it."""
    from permit_processing import generate_permit_description
    p = {
        'description': 'Custom interior renovation per architect plans, BLDG-2026-1234, third floor',
        'permit_number': 'BLDG-2026-1234',
        'permit_type': 'Building',
    }
    desc = generate_permit_description(p)
    assert desc == p['description']


# ---------------------------------------------------------------------
# reclassify_permit (smoke test — needs collector.classify_trade)
# ---------------------------------------------------------------------

def test_reclassify_permit_sets_trade_category():
    """Smoke test: reclassify_permit calls collector.classify_trade
    and writes the result to permit['trade_category']. The actual
    classification rules live in collector.py and are tested there;
    V537 only pins that the module wiring works."""
    from permit_processing import reclassify_permit
    p = {'description': 'Replace HVAC', 'work_type': '', 'permit_type': 'Mechanical'}
    out = reclassify_permit(p)
    assert out is p  # mutates in place AND returns the same dict
    assert 'trade_category' in p
    assert isinstance(p['trade_category'], str)


# ---------------------------------------------------------------------
# Re-export contract
# ---------------------------------------------------------------------

def test_permit_processing_re_exported_from_server():
    """server.py keeps re-exports so existing callsites resolve."""
    import server
    for name in ('reclassify_permit', 'generate_permit_description',
                 'format_permit_address', 'validate_permit_dates',
                 'normalize_address_for_lookup'):  # V539 added
        assert hasattr(server, name), (
            f'V537/V539 regression: server.py no longer re-exports {name!r}; '
            f'existing callsites will NameError.'
        )


# ---------------------------------------------------------------------
# V539: normalize_address_for_lookup
# ---------------------------------------------------------------------

def test_normalize_address_lowercases_and_collapses_whitespace():
    from permit_processing import normalize_address_for_lookup
    assert normalize_address_for_lookup('  100  Main Street  ') == '100 main st'


def test_normalize_address_expands_common_abbreviations():
    """Long forms collapse to canonical short forms."""
    from permit_processing import normalize_address_for_lookup
    cases = [
        ('100 Main Street', '100 main st'),
        ('5 Oak Avenue', '5 oak ave'),
        ('200 Washington Boulevard', '200 washington blvd'),
        ('15 Hidden Drive', '15 hidden dr'),
        ('22 Country Road', '22 country rd'),
        ('8 Maple Lane', '8 maple ln'),
        ('99 Court Place', '99 ct pl'),
        ('45 Apartment 3', '45 apt 3'),
        ('North 4th Street', 'n 4th st'),
        ('South West Avenue', 's w ave'),
    ]
    for src, expected in cases:
        got = normalize_address_for_lookup(src)
        assert got == expected, f'{src!r} → {got!r}, expected {expected!r}'


def test_normalize_address_handles_empty():
    from permit_processing import normalize_address_for_lookup
    assert normalize_address_for_lookup('') == ''
    assert normalize_address_for_lookup(None) == ''


def test_normalize_address_strips_punctuation_keeps_apt_marker():
    """`#` and `-` survive; `,` `.` `;` get stripped."""
    from permit_processing import normalize_address_for_lookup
    assert normalize_address_for_lookup('100 Main St., Apt #3-A') == '100 main st apt #3-a'
