"""V170 B2: Value tier classification for permit values."""


def value_tier(permit_value):
    """Classify permit estimated cost into a tier.

    Returns: 'small' (<$50K), 'mid' ($50K-$500K), 'large' (>$500K), 'unknown'
    """
    try:
        v = float(permit_value or 0)
    except (ValueError, TypeError):
        return 'unknown'
    if v <= 0:
        return 'unknown'
    if v < 50_000:
        return 'small'
    if v < 500_000:
        return 'mid'
    return 'large'
