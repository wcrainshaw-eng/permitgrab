"""V536: lead scoring rubric. Extracted from server.py:3461-3598
per the V524 module-extraction template.

Pure functions — no Flask, no DB, no I/O. Bug class: wrong score
ranks permits incorrectly on city pages → hot leads buried, cold
leads promoted. Tests pin every bracket of the V13.1 absolute
rubric.

Score breakdown (max 100):
  A: Project value     0-35 pts (absolute brackets)
  B: Recency          0-30 pts (days since filed)
  C: Address quality  0-15 pts
  D: Contact info     0-15 pts
  E: Status           0-5 pts
"""
from __future__ import annotations

from datetime import datetime


def calculate_lead_score(permit):
    """V13.1: ABSOLUTE lead scoring with WIDER SPREAD for better
    differentiation. Returns integer 0-100. No normalization across
    dataset.

    V536: lifted from server.py:3461 unchanged. Behavior preserved 1-to-1.
    """
    score = 0.0

    # A: Project value (0-35 pts) — ABSOLUTE brackets with wider spread
    value = 0.0
    for key in ['estimated_cost', 'project_value', 'value']:
        v = permit.get(key)
        if v is not None:
            try:
                value = float(str(v).replace('$', '').replace(',', ''))
                break
            except (ValueError, TypeError):
                pass

    if value <= 0:
        score += 0    # V13.1: Missing = 0 (creates bigger gap)
    elif value < 10000:
        score += 5
    elif value < 50000:
        score += 10
    elif value < 100000:
        score += 16
    elif value < 200000:
        score += 22
    elif value < 500000:
        score += 28
    else:
        score += 35   # $500K+ = max

    # B: Recency (0-30 pts) — V13.1: Invalid dates = 0, not default
    for key in ['filing_date', 'issued_date', 'date']:
        d = permit.get(key)
        if d:
            try:
                if isinstance(d, str):
                    # V13.1: Must start with digit to be a valid date
                    if not d[0].isdigit():
                        continue  # Skip non-date strings like "WROCCO"
                    d = datetime.strptime(d[:10], '%Y-%m-%d')
                days_old = (datetime.now() - d).days
                if days_old < 0:
                    score += 0    # Future date = bad data
                elif days_old <= 7:
                    score += 30
                elif days_old <= 30:
                    score += 24
                elif days_old <= 90:
                    score += 18
                elif days_old <= 180:
                    score += 12
                elif days_old <= 365:
                    score += 6
                else:
                    score += 0    # V13.1: Older than 1 year = 0
                break
            except (ValueError, TypeError):
                pass

    # C: Address quality (0-15 pts) — V13.1: Increased weight
    # V19: Explicitly exclude placeholder addresses from scoring
    address = str(permit.get('address', '')).strip()
    address_lower = address.lower()
    is_placeholder = (
        not address
        or address_lower in ('address not provided', 'not provided', 'n/a', 'na', 'none', 'unknown', 'tbd', '-')
        or address_lower.startswith('address not')
    )
    if is_placeholder:
        score += 0    # V19: No address = 0 points (keeps out of Best Leads)
    elif any(c.isdigit() for c in address):
        score += 15   # Has street number = full points
    elif len(address) > 5:
        score += 7    # Has name but no number
    # else 0

    # D: Contact info (0-15 pts) — V13.1: More granular
    has_phone = bool(permit.get('contact_phone'))
    has_email = bool(permit.get('contact_email'))
    has_contractor = bool(permit.get('contractor_name'))
    has_owner = bool(permit.get('owner_name'))

    if has_phone and has_email:
        score += 15
    elif has_phone or has_email:
        score += 12
    elif has_contractor:
        score += 8
    elif has_owner:
        score += 5
    # else 0

    # E: Status (0-5 pts) — V13.1: Reduced weight, least important
    status = str(permit.get('status', '')).lower().strip()
    if status in ('issued', 'approved', 'active', 'permitted', 'finaled'):
        score += 5
    elif status in ('pending', 'in review', 'under review', 'plan review', 'filed', 'submitted'):
        score += 3
    # else 0

    return max(0, min(100, round(score)))


def add_lead_scores(permits):
    """V13.1: Apply absolute lead scoring with wider spread. Also
    assigns lead_quality tier based on score:
        score >= 60 → 'hot'
        score >= 40 → 'warm'
        else        → 'standard'

    V536: lifted from server.py:3578 unchanged.
    """
    if not permits:
        return permits

    for p in permits:
        score = calculate_lead_score(p)
        p['lead_score'] = score

        # V13.1: Adjusted thresholds for wider score distribution
        if score >= 60:
            p['lead_quality'] = 'hot'
        elif score >= 40:
            p['lead_quality'] = 'warm'
        else:
            p['lead_quality'] = 'standard'

    return permits
