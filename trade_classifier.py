"""V170 B1: Trade classification for building permits.

Classifies permits into trade categories based on description and type text.
Used on ingest and for backfilling existing permits.
"""

TRADE_KEYWORDS = {
    'hvac': ['hvac', 'heating', 'cooling', 'air conditioning', 'a/c ',
             'ac install', 'furnace', 'boiler', 'heat pump', 'ventilation',
             'mechanical', 'duct', 'ductwork'],
    'electrical': ['electrical', 'electric ', 'wiring', 'panel',
                   'service upgrade', 'outlet', 'lighting', 'meter'],
    'plumbing': ['plumbing', 'plumb', 'water heater', 'sewer',
                 'drain', 'pipe', 'fixture', 'toilet', 'sink',
                 'backflow', 'water line', 'gas line'],
    'roofing': ['roof', 'roofing', 're-roof', 'reroofing', 'shingle',
                'torch down', 'flat roof', 'tear off'],
    'solar': ['solar', 'photovoltaic', 'pv system', 'battery storage',
              'solar panel'],
    'demolition': ['demo', 'demolition', 'demolish', 'raze', 'tear down'],
    'pool': ['pool', 'spa'],
    'fence': ['fence', 'fencing'],
    'structural': ['foundation', 'structural', 'framing', 'load bearing'],
    'addition': ['addition', 'add on', 'extension', 'expand'],
    'new_construction': ['new construction', 'new build', 'new sfr',
                         'new residential', 'new commercial'],
}


def classify_trade(description, permit_type=''):
    """Classify a permit into a trade category based on text.

    Args:
        description: Permit description text
        permit_type: Permit type/category text

    Returns:
        Trade tag string (e.g., 'hvac', 'electrical', 'general')
    """
    text = (str(description or '') + ' ' + str(permit_type or '')).lower()
    for trade, keywords in TRADE_KEYWORDS.items():
        for kw in keywords:
            if kw in text:
                return trade
    return 'general'
