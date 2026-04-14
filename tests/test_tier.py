"""Value tier classification tests."""
from value_tier import value_tier

def test_zero(): assert value_tier(0) == 'unknown'
def test_negative(): assert value_tier(-100) == 'unknown'
def test_none(): assert value_tier(None) == 'unknown'
def test_empty(): assert value_tier('') == 'unknown'
def test_bogus(): assert value_tier('bogus') == 'unknown'
def test_small_1(): assert value_tier(1) == 'small'
def test_small_49999(): assert value_tier(49_999) == 'small'
def test_mid_50000(): assert value_tier(50_000) == 'mid'
def test_mid_499999(): assert value_tier(499_999) == 'mid'
def test_large_500000(): assert value_tier(500_000) == 'large'
def test_large_10m(): assert value_tier(9_999_999) == 'large'
def test_string_number(): assert value_tier('75000') == 'mid'
