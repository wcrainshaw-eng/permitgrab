"""V537: permit-data transforms. Extracted from server.py:3609-3630
per the V524 module-extraction template.

Four pure functions that shape raw permit data into display-ready
form. Each mutates the permit dict in-place (matching the existing
production behavior — callers expect the mutation, not a return).

  reclassify_permit          : Re-derive trade_category from text fields
  generate_permit_description: Build factual description from permit fields
  format_permit_address      : Distinguish street addresses from area names
  validate_permit_dates      : Relabel future-dated filing_date as expiration

Bug class: silent data corruption on display. A wrong format or
classification = wrong category buckets, wrong "Best Leads" sort,
wrong email digest content. Tests pin every transform branch.
"""
from __future__ import annotations

import re
from datetime import datetime


def reclassify_permit(permit):
    """Re-classify a permit's trade category based on its description
    and type fields. V200-1: imports the canonical classify_trade
    from collector.py (was duplicated in server.py + collector.py
    before; the duplicates drifted)."""
    from collector import classify_trade
    text_parts = [
        permit.get('description', ''),
        permit.get('work_type', ''),
        permit.get('permit_type', ''),
    ]
    text = ' '.join(filter(None, text_parts))
    permit['trade_category'] = classify_trade(text)
    return permit


def generate_permit_description(permit):
    """Generate a unique, factual description based on actual permit
    data. Falls back to building a description from permit fields if
    no real description exists, or if the description appears
    templated (same as others).

    V537: lifted from server.py:3488 unchanged.
    """
    existing_desc = permit.get('description', '')

    # Build a factual description from permit data
    parts = []

    # Permit type
    permit_type = permit.get('permit_type', '') or permit.get('work_type', '')
    if permit_type:
        parts.append(permit_type.strip())

    # Trade category
    trade = permit.get('trade_category', '')
    if trade and trade not in ['General Construction', 'Other']:
        if not any(trade.lower() in p.lower() for p in parts):
            parts.append(f"({trade})")

    # Address — V12.57: Clean raw JSON/GeoJSON before displaying
    address = permit.get('address', '')
    if address and ('{' in str(address)):
        # Address contains JSON — try to parse it
        from collector import parse_address_value
        address = parse_address_value(address)
    if address:
        parts.append(f"at {address}")

    # Value - V12.27: Skip if at $50M cap (unreliable data)
    cost = permit.get('estimated_cost', 0) or 0
    MAX_REASONABLE_COST = 50_000_000
    if cost > 0 and cost != MAX_REASONABLE_COST:
        if cost >= 1000000:
            parts.append(f"— ${cost/1000000:.1f}M project")
        elif cost >= 1000:
            parts.append(f"— ${cost/1000:.0f}K project")
        else:
            parts.append(f"— ${cost:,.0f}")

    # Status
    status = permit.get('status', '')
    if status:
        parts.append(f"[{status}]")

    # Permit number for uniqueness
    permit_num = permit.get('permit_number', '')
    if permit_num:
        parts.append(f"(Permit #{permit_num})")

    # Combine parts
    generated_desc = ' '.join(parts)

    # Return existing description if it's substantial and unique-looking
    # (has actual address or permit number in it), otherwise use generated
    if existing_desc and len(existing_desc) > 30:
        # Check if existing description contains unique identifiers
        has_address = address and address[:10] in existing_desc
        has_permit_num = permit_num and permit_num in existing_desc
        if has_address or has_permit_num:
            return existing_desc

    return generated_desc if generated_desc else existing_desc


def format_permit_address(permit):
    """V12.11: Format address field appropriately.

    For county datasets, addresses may be location/area names (no
    street number). Detect these and label them as "Location:"
    instead of pretending they're street addresses.

    V537: lifted from server.py:3558 unchanged.
    """
    address = permit.get('address', '') or ''
    if not address.strip():
        permit['display_address'] = 'Address not provided'
        permit['address_type'] = 'none'
        return

    address_clean = address.strip()

    # Check if it looks like a real street address (has a number at the start)
    has_street_number = bool(re.match(r'^\d+\s', address_clean))

    # Common area/location-only patterns (no street number, short, all caps)
    is_location_only = (
        not has_street_number
        and len(address_clean) < 30
        and (
            address_clean.isupper()
            or address_clean.upper() in [
                'MONTGOMERY', 'ROCK SPRING', 'BETHESDA', 'SILVER SPRING',
                'ROCKVILLE', 'WHEATON', 'GERMANTOWN', 'GAITHERSBURG',
                'POTOMAC', 'CHEVY CHASE', 'TAKOMA PARK', 'KENSINGTON',
            ]
        )
    )

    if is_location_only:
        permit['display_address'] = f"Area: {address_clean.title()}"
        permit['address_type'] = 'location'
    elif not has_street_number and len(address_clean.split()) <= 3:
        # Short address without number - likely a location name
        permit['display_address'] = f"Location: {address_clean.title()}"
        permit['address_type'] = 'location'
    else:
        permit['display_address'] = address_clean
        permit['address_type'] = 'street'


def validate_permit_dates(permit):
    """V12.9: Validate and relabel future-dated permits.

    If filing_date is >30 days in the future, it's likely an
    expiration date, not a filing date. Relabel it appropriately.

    V537: lifted from server.py:3598 unchanged.
    """
    filing_date_str = permit.get('filing_date', '')
    if not filing_date_str:
        return

    try:
        filing_date = datetime.strptime(str(filing_date_str)[:10], '%Y-%m-%d')
        days_from_now = (filing_date - datetime.now()).days

        if days_from_now > 30:
            # This is likely an expiration/completion date, not a filing date
            permit['expiration_date'] = filing_date_str
            permit['date_label'] = 'Expires'
            # Try to find an alternative filing date
            for alt_key in ['issued_date', 'issue_date', 'created_date', 'application_date']:
                alt_date = permit.get(alt_key)
                if alt_date:
                    try:
                        alt_parsed = datetime.strptime(str(alt_date)[:10], '%Y-%m-%d')
                        if (alt_parsed - datetime.now()).days <= 30:
                            permit['filing_date'] = str(alt_date)[:10]
                            permit['date_label'] = 'Filed'
                            return
                    except Exception:
                        pass
            # No alternative found, keep expiration date but mark it
            permit['filing_date'] = filing_date_str
            permit['date_label'] = 'Expires'
        else:
            permit['date_label'] = 'Filed'
    except (ValueError, TypeError):
        permit['date_label'] = 'Filed'  # Default label (matches production)
