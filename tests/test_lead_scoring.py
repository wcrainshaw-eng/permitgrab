"""V536 regression tests for lead_scoring/.

Pin the V13.1 absolute scoring rubric. Wrong values here = wrong
ranking on city pages = downstream UX impact (hot leads buried,
cold leads promoted).

The test rubric covers each of the 5 score buckets (A-E) plus the
hot/warm/standard tier assignment in add_lead_scores.
"""
from __future__ import annotations

from datetime import datetime, timedelta


# Helper: build a permit dict with overrides
def _permit(**kwargs):
    base = {
        'estimated_cost': 0,
        'filing_date': None,
        'address': '',
        'contact_phone': '',
        'contact_email': '',
        'contractor_name': '',
        'owner_name': '',
        'status': '',
    }
    base.update(kwargs)
    return base


# ---------------------------------------------------------------------
# Bucket A: Project value (0-35 pts)
# ---------------------------------------------------------------------

def test_value_bucket_zero_for_missing():
    from lead_scoring import calculate_lead_score
    p = _permit()
    # No other fields; only A bucket scored. Value missing → 0.
    assert calculate_lead_score(p) == 0


def test_value_bucket_500k_caps_at_35():
    from lead_scoring import calculate_lead_score
    # Pure A-bucket: $500K+ should add 35
    p = _permit(estimated_cost=500000)
    assert calculate_lead_score(p) == 35
    p2 = _permit(estimated_cost=1500000)
    assert calculate_lead_score(p2) == 35


def test_value_bucket_brackets():
    """Each bracket adds the documented points."""
    from lead_scoring import calculate_lead_score
    cases = [
        (5000, 5),       # < 10K
        (40000, 10),     # < 50K
        (90000, 16),     # < 100K
        (150000, 22),    # < 200K
        (300000, 28),    # < 500K
    ]
    for value, expected in cases:
        p = _permit(estimated_cost=value)
        assert calculate_lead_score(p) == expected, f'value={value}, got {calculate_lead_score(p)}'


def test_value_field_aliases():
    """V536 contract: project_value and value act as fallbacks ONLY
    when estimated_cost is missing entirely. If estimated_cost is set
    (even to 0), it wins — that's the production behavior. Pin it
    here so a future refactor doesn't accidentally change priority."""
    from lead_scoring import calculate_lead_score
    # Build a permit WITHOUT estimated_cost so project_value gets a chance.
    p_proj = {'project_value': 600000}
    assert calculate_lead_score(p_proj) == 35
    p_val = {'value': 600000}
    assert calculate_lead_score(p_val) == 35
    # estimated_cost=0 short-circuits even when project_value is large
    p_short = {'estimated_cost': 0, 'project_value': 600000}
    assert calculate_lead_score(p_short) == 0, (
        "V536 contract: estimated_cost=0 wins over project_value (the "
        "loop's `if v is not None` check passes for 0)."
    )


def test_value_dollar_string_parses():
    """Real production data has '$1,234,567' formatted strings."""
    from lead_scoring import calculate_lead_score
    p = _permit(estimated_cost='$750,000')
    assert calculate_lead_score(p) == 35


# ---------------------------------------------------------------------
# Bucket B: Recency (0-30 pts)
# ---------------------------------------------------------------------

def test_recency_zero_for_missing_or_invalid():
    from lead_scoring import calculate_lead_score
    assert calculate_lead_score(_permit(filing_date='WROCCO')) == 0
    assert calculate_lead_score(_permit(filing_date='not-a-date')) == 0
    # Missing entirely
    assert calculate_lead_score(_permit()) == 0


def test_recency_brackets():
    from lead_scoring import calculate_lead_score
    today = datetime.now().date()

    def fmt(days_ago):
        return (datetime.now() - timedelta(days=days_ago)).strftime('%Y-%m-%d')

    cases = [
        (3, 30),        # ≤ 7 days
        (15, 24),       # ≤ 30 days
        (60, 18),       # ≤ 90 days
        (120, 12),      # ≤ 180 days
        (300, 6),       # ≤ 365 days
        (400, 0),       # > 365 days
    ]
    for days_ago, expected in cases:
        p = _permit(filing_date=fmt(days_ago))
        assert calculate_lead_score(p) == expected, f'days={days_ago}'


def test_recency_future_date_rejects():
    """Bad data: dates in the future score 0 in the recency bucket."""
    from lead_scoring import calculate_lead_score
    future = (datetime.now() + timedelta(days=10)).strftime('%Y-%m-%d')
    p = _permit(filing_date=future)
    assert calculate_lead_score(p) == 0


# ---------------------------------------------------------------------
# Bucket C: Address quality (0-15 pts)
# ---------------------------------------------------------------------

def test_address_with_street_number_scores_15():
    from lead_scoring import calculate_lead_score
    p = _permit(address='100 Main St, Chicago')
    assert calculate_lead_score(p) == 15


def test_address_placeholder_scores_zero():
    """V19 contract: placeholder strings score 0 to stay out of Best Leads."""
    from lead_scoring import calculate_lead_score
    placeholders = [
        '', 'address not provided', 'NOT PROVIDED', 'N/A', 'na',
        'NONE', 'unknown', 'TBD', '-',
        'Address Not Available',  # starts with 'address not'
    ]
    for ph in placeholders:
        p = _permit(address=ph)
        assert calculate_lead_score(p) == 0, f"placeholder {ph!r} should score 0, got {calculate_lead_score(p)}"


def test_address_name_only_scores_7():
    """Has name but no street number: 7 pts."""
    from lead_scoring import calculate_lead_score
    p = _permit(address='Main Street, Chicago')
    assert calculate_lead_score(p) == 7


# ---------------------------------------------------------------------
# Bucket D: Contact info (0-15 pts)
# ---------------------------------------------------------------------

def test_contact_phone_and_email_max_15():
    from lead_scoring import calculate_lead_score
    p = _permit(contact_phone='555-1234', contact_email='a@b.com')
    assert calculate_lead_score(p) == 15


def test_contact_phone_or_email_scores_12():
    from lead_scoring import calculate_lead_score
    assert calculate_lead_score(_permit(contact_phone='555-1234')) == 12
    assert calculate_lead_score(_permit(contact_email='a@b.com')) == 12


def test_contact_contractor_only_scores_8():
    from lead_scoring import calculate_lead_score
    assert calculate_lead_score(_permit(contractor_name='ACME LLC')) == 8


def test_contact_owner_only_scores_5():
    from lead_scoring import calculate_lead_score
    assert calculate_lead_score(_permit(owner_name='Jane Doe')) == 5


# ---------------------------------------------------------------------
# Bucket E: Status (0-5 pts)
# ---------------------------------------------------------------------

def test_status_issued_scores_5():
    from lead_scoring import calculate_lead_score
    for s in ('issued', 'ISSUED', 'approved', 'active', 'permitted', 'finaled'):
        assert calculate_lead_score(_permit(status=s)) == 5, f'status={s!r}'


def test_status_pending_scores_3():
    from lead_scoring import calculate_lead_score
    for s in ('pending', 'in review', 'plan review', 'filed', 'submitted'):
        assert calculate_lead_score(_permit(status=s)) == 3


def test_status_other_scores_0():
    from lead_scoring import calculate_lead_score
    assert calculate_lead_score(_permit(status='cancelled')) == 0


# ---------------------------------------------------------------------
# Combined + capping
# ---------------------------------------------------------------------

def test_combined_max_score_caps_at_100():
    """V536 contract: max score is capped at 100. Combined max would
    be 35+30+15+15+5 = 100 exactly."""
    from lead_scoring import calculate_lead_score
    p = _permit(
        estimated_cost=1000000,
        filing_date=datetime.now().strftime('%Y-%m-%d'),
        address='100 Main St',
        contact_phone='555-1234',
        contact_email='a@b.com',
        status='issued',
    )
    assert calculate_lead_score(p) == 100


def test_score_floor_is_zero():
    """No bucket can produce a negative score — floor enforced."""
    from lead_scoring import calculate_lead_score
    # All-empty permit returns 0
    assert calculate_lead_score(_permit()) == 0


# ---------------------------------------------------------------------
# add_lead_scores tier assignment
# ---------------------------------------------------------------------

def test_add_lead_scores_assigns_hot_tier():
    """score >= 60 → 'hot'."""
    from lead_scoring import add_lead_scores
    permits = [{'estimated_cost': 1000000, 'filing_date': datetime.now().strftime('%Y-%m-%d'), 'address': '100 Main St', 'contact_phone': '555-1234', 'status': 'issued'}]
    out = add_lead_scores(permits)
    assert out[0]['lead_quality'] == 'hot'
    assert out[0]['lead_score'] >= 60


def test_add_lead_scores_assigns_warm_tier():
    """40 <= score < 60 → 'warm'."""
    from lead_scoring import add_lead_scores
    permits = [{'estimated_cost': 50000, 'filing_date': datetime.now().strftime('%Y-%m-%d'), 'address': '100 Main St', 'status': 'issued'}]
    out = add_lead_scores(permits)
    # Score: 10 (value) + 30 (recency) + 15 (address) + 0 (no contact) + 5 (issued) = 60 → hot
    # Not exactly warm; pick a smaller value to land in warm
    permits = [{'estimated_cost': 20000, 'filing_date': datetime.now().strftime('%Y-%m-%d'), 'address': '100 Main St', 'status': 'issued'}]
    out = add_lead_scores(permits)
    # 10 + 30 + 15 + 0 + 5 = 60 → still hot
    # Try fewer-bucket: smaller value AND no number address
    permits = [{'estimated_cost': 20000, 'filing_date': datetime.now().strftime('%Y-%m-%d'), 'address': 'Main Street', 'status': 'issued'}]
    out = add_lead_scores(permits)
    # 10 + 30 + 7 + 0 + 5 = 52 → warm
    assert 40 <= out[0]['lead_score'] < 60
    assert out[0]['lead_quality'] == 'warm'


def test_add_lead_scores_assigns_standard_tier():
    """score < 40 → 'standard'."""
    from lead_scoring import add_lead_scores
    permits = [{'estimated_cost': 1000, 'address': '100 Main St', 'status': 'issued'}]
    # 5 + 0 (no date) + 15 + 0 + 5 = 25 → standard
    out = add_lead_scores(permits)
    assert out[0]['lead_quality'] == 'standard'
    assert out[0]['lead_score'] < 40


def test_add_lead_scores_handles_empty_list():
    from lead_scoring import add_lead_scores
    assert add_lead_scores([]) == []
    assert add_lead_scores(None) is None


def test_lead_scoring_re_exported_from_server():
    """V536 contract: server.py keeps re-exports so existing
    callsites (e.g. routes/api.py, lead-rendering paths) keep
    resolving via the back-compat shim."""
    import server
    assert hasattr(server, 'calculate_lead_score'), (
        'V536 regression: server.py no longer re-exports '
        'calculate_lead_score; existing callsites will NameError.'
    )
    assert hasattr(server, 'add_lead_scores')
