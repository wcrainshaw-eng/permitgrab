"""V539: address normalization helper. Extracted from server.py:4414
into the existing permit_processing/ package (lives alongside the
other permit-data shaping helpers from V537).

Pure function — no Flask, no DB, no I/O. Used by routes/api.py for
property-owner / violation address matching, where lookup keys must
match across slightly-different formatting (e.g. '100 Main Street'
vs '100 main st'). Bug here = address-match misses → empty owner /
violation panels on city pages.
"""
from __future__ import annotations

import re


def normalize_address_for_lookup(address):
    """Normalize an address for lookup (matches collector.py logic).

    Lowercases, collapses whitespace, expands common street-type
    abbreviations to their canonical short form, and strips
    punctuation other than `#` (apt #) and `-` (e.g. lot 123-A).

    V539: lifted from server.py:4414 unchanged.
    """
    if not address:
        return ""
    addr = address.lower().strip()
    addr = re.sub(r'\s+', ' ', addr)
    replacements = [
        (r'\bstreet\b', 'st'),
        (r'\bavenue\b', 'ave'),
        (r'\bboulevard\b', 'blvd'),
        (r'\bdrive\b', 'dr'),
        (r'\broad\b', 'rd'),
        (r'\blane\b', 'ln'),
        (r'\bcourt\b', 'ct'),
        (r'\bplace\b', 'pl'),
        (r'\bapartment\b', 'apt'),
        (r'\bsuite\b', 'ste'),
        (r'\bnorth\b', 'n'),
        (r'\bsouth\b', 's'),
        (r'\beast\b', 'e'),
        (r'\bwest\b', 'w'),
    ]
    for pattern, replacement in replacements:
        addr = re.sub(pattern, replacement, addr)
    addr = re.sub(r'[^\w\s#-]', '', addr)
    return addr
