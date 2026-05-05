"""V530 regression tests for the enrichment/ scheduler.

Pins the V475-class silent-thread-drop bug for enrichment_daemon the
same way V524 pinned it for email_scheduler. The thread name
'enrichment_daemon' is the contract that routes/admin.py +
/api/admin/debug/threads + the V493 IRONCLAD watchdog all rely on.
"""
from __future__ import annotations

import os
import time as _time
from unittest.mock import patch


def test_enrichment_daemon_thread_spawned_with_correct_name():
    """V475 / V530 regression: thread name must be 'enrichment_daemon'.

    Mock the loop body so the test doesn't actually run the
    600s-delay enrichment_daemon — we only care that the spawn
    happens, the thread is daemonic, and the name is exact.
    """
    import enrichment.scheduler as scheduler_mod
    scheduler_mod._thread = None

    with patch.object(scheduler_mod, 'enrichment_daemon', lambda: None):
        t = scheduler_mod.start_thread()
        assert t is not None
        assert t.name == 'enrichment_daemon', (
            f"V530 regression: thread name is {t.name!r}, must be "
            "'enrichment_daemon' for routes/admin.py's alive check."
        )
        assert t.daemon is True

        # Idempotent — second call either returns same live thread
        # or spawns a new one (no-op body finishes fast).
        for _ in range(20):
            if not t.is_alive():
                break
            _time.sleep(0.01)
        t2 = scheduler_mod.start_thread()
        assert t2 is not None
        assert t2.name == 'enrichment_daemon'

    scheduler_mod._thread = None


def test_enrichment_package_exports():
    """V530 contract: the package re-exports the public API."""
    import enrichment
    assert callable(getattr(enrichment, 'start_thread', None))
    assert callable(getattr(enrichment, 'enrichment_daemon', None))


def test_enrichment_module_does_not_eagerly_import_license_enrichment():
    """The license_enrichment.py module is large (69k bytes) and
    pulls in several heavy deps. The V530 scheduler loops imports
    of license_enrichment and web_enrichment INSIDE the daemon
    loop, so importing `enrichment` itself stays lightweight —
    callers that just want start_thread() (e.g. server.py boot)
    don't pay the license_enrichment.py module-load cost."""
    import sys
    if 'license_enrichment' in sys.modules:
        import pytest
        pytest.skip('license_enrichment already imported by another test')
    if 'enrichment' in sys.modules:
        del sys.modules['enrichment']
        for k in list(sys.modules.keys()):
            if k.startswith('enrichment.'):
                del sys.modules[k]
    import enrichment  # noqa: F401
    assert 'license_enrichment' not in sys.modules, (
        "V530: importing enrichment must not transitively load "
        "license_enrichment.py — keep imports lazy in the loop body."
    )
