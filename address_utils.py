"""V170 B5: Address normalization for permit-violation matching."""

import re


def normalize_address(s):
    """Normalize an address for matching across permits and violations.

    Lowercases, strips punctuation, removes apt/suite/unit, collapses whitespace.

    >>> normalize_address('123 Main St, Apt 4B')
    '123 main st'
    >>> normalize_address('123 MAIN ST. #4')
    '123 main st'
    """
    if not s:
        return ''
    s = s.lower().strip()
    # Remove suite/apt/unit
    s = re.sub(r'\s+(apt|apartment|suite|ste|unit|#)\s*[\w-]*', '', s)
    # Strip punctuation
    s = re.sub(r'[^\w\s]', '', s)
    # Collapse whitespace
    s = re.sub(r'\s+', ' ', s)
    return s.strip()
