"""Contractor name normalization tests."""
from contractor_enrichment import normalize_contractor_name

def test_basic():
    assert normalize_contractor_name('Smith Construction') == 'smith'

def test_llc():
    assert normalize_contractor_name('ABC Builders LLC') == 'abc builders'

def test_inc():
    assert normalize_contractor_name('Jones Inc.') == 'jones'

def test_corp():
    assert normalize_contractor_name('Big Corp') == 'big'

def test_none():
    assert normalize_contractor_name(None) == ''

def test_empty():
    assert normalize_contractor_name('') == ''

def test_punctuation():
    assert normalize_contractor_name("Smith & Sons, LLC") == 'smith sons'

def test_multiple_suffixes():
    assert normalize_contractor_name('XYZ Services LLC') == 'xyz'

def test_whitespace():
    assert normalize_contractor_name('  Bob  Builder  Co  ') == 'bob builder'
