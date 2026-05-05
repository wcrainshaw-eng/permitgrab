"""V531: unsubscribe-token helpers. Extracted from server.py:4766.

Pure crypto — uses the stdlib `secrets` module. Tests pin token
length + URL-safety so a future refactor can't silently downgrade
to a weaker token (which would let an attacker enumerate unsubscribe
URLs and unsubscribe other users from digests).
"""
from __future__ import annotations

import secrets


def generate_unsubscribe_token():
    """Generate a unique unsubscribe token.

    V531: 32 bytes = 256 bits, encoded URL-safe base64 → 43 chars.
    secrets.token_urlsafe is cryptographically secure and uses
    os.urandom under the hood.
    """
    return secrets.token_urlsafe(32)
