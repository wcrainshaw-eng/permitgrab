"""Trade classifier tests."""
from trade_classifier import classify_trade

def test_hvac():
    assert classify_trade('HVAC install', 'MECH') == 'hvac'

def test_hvac_from_type():
    assert classify_trade('', 'Mechanical Permit') == 'hvac'

def test_electrical():
    assert classify_trade('electrical panel upgrade 200 amp', '') == 'electrical'

def test_plumbing():
    assert classify_trade('water heater replacement', '') == 'plumbing'

def test_roofing():
    assert classify_trade('Re-roof 30 sq shingle', 'ROOF') == 'roofing'

def test_solar():
    assert classify_trade('solar panel installation 5kw', '') == 'solar'

def test_new_construction():
    assert classify_trade('new construction single family', '') == 'new_construction'

def test_demolition():
    assert classify_trade('demolition of existing structure', '') == 'demolition'

def test_general_fallback():
    assert classify_trade('misc work', '') == 'general'

def test_none_inputs():
    assert classify_trade(None, None) == 'general'

def test_empty_inputs():
    assert classify_trade('', '') == 'general'
