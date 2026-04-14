"""Address normalization tests."""
from address_utils import normalize_address

def test_basic():
    assert normalize_address('123 Main St') == '123 main st'

def test_apt_removal():
    assert normalize_address('123 Main Street, Apt 4B') == '123 main street'

def test_hash_removal():
    assert normalize_address('123 MAIN ST. #4') == '123 main st'

def test_collapse_whitespace():
    assert normalize_address('123  Main  St.') == '123 main st'

def test_none():
    assert normalize_address(None) == ''

def test_empty():
    assert normalize_address('') == ''

def test_suite():
    assert normalize_address('456 Oak Ave Suite 200') == '456 oak ave'

def test_unit():
    assert normalize_address('789 Elm Blvd Unit B') == '789 elm blvd'

def test_uppercase():
    assert normalize_address('100 BROADWAY') == '100 broadway'
