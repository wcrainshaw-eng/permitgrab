"""V547b regression tests: throughput crank for collector cycles.

Pin three things that V547b's directive promised, so a future
refactor can't silently regress them:

1. MAX_CITIES_PER_CYCLE = 200 (was 75)
2. Per-platform semaphores configured for the documented limits
3. ThreadPoolExecutor referenced in collector._collect_all_inner
4. worker.py cycle cadence is 15-min (900s), not 30-min (1800s)
"""
import pathlib


_REPO = pathlib.Path(__file__).parent.parent


def test_v547b_max_cities_per_cycle_is_200():
    """V547b: cycle cap raised 75 → 200."""
    text = (_REPO / 'collector.py').read_text()
    assert 'MAX_CITIES_PER_CYCLE = 200' in text, (
        'V547b regression: MAX_CITIES_PER_CYCLE no longer 200'
    )
    # And the old value should be gone (otherwise both exist and
    # the wrong one might be used).
    # Allow the literal '75' in surrounding comments — only fail
    # if `MAX_CITIES_PER_CYCLE = 75` is still an active assignment.
    assert 'MAX_CITIES_PER_CYCLE = 75' not in text, (
        'V547b regression: legacy MAX_CITIES_PER_CYCLE = 75 still '
        'present in collector.py — the V166 line was supposed to '
        'be replaced, not duplicated'
    )


def test_v547b_threadpool_executor_in_collector():
    """V547b: per-cycle pre-fetch must use ThreadPoolExecutor with
    max_workers=20 + per-platform semaphores. Without this, the
    cycle reverts to serial I/O at the old throughput."""
    text = (_REPO / 'collector.py').read_text()
    assert 'ThreadPoolExecutor' in text, (
        'V547b regression: collector.py no longer references '
        'ThreadPoolExecutor — the parallel pre-fetch was removed.'
    )
    assert 'max_workers=20' in text, (
        'V547b regression: max_workers=20 dropped — the cycle '
        "won't use the budgeted concurrency."
    )
    # Per-platform semaphore values (from the V547b directive).
    for line in (
        "'socrata': _v547b_sem(10)",
        "'arcgis': _v547b_sem(8)",
        "'accela': _v547b_sem(4)",
        "'ckan': _v547b_sem(10)",
    ):
        assert line in text, (
            f'V547b regression: per-platform semaphore line {line!r} '
            f'missing from collector.py'
        )


def test_v547b_worker_cycle_cadence_15min():
    """V547b: worker.py scheduled_collection sleep target is 900s
    (15-min outer cadence). Pre-V547b was 1800s (30-min)."""
    text = (_REPO / 'worker.py').read_text()
    assert 'sleep_time = max(60, 900' in text, (
        'V547b regression: worker.py cycle cadence reverted to '
        '30-min — sleep_time = max(60, 900 - elapsed_total) line '
        'is missing'
    )
    assert 'sleep_time = max(300, 1800' not in text, (
        'V547b regression: legacy 30-min cadence line is back in '
        'worker.py — should be 900s not 1800s'
    )


def test_v547b_pre_fetch_cache_lookup_in_per_city_loop():
    """V547b: the per-city loop must read raw responses from the
    `_v547b_raw_cache` dict warmed by the parallel pre-fetch. If
    the lookup is removed, the loop reverts to inline sync fetches
    and the parallel pre-fetch becomes dead work."""
    text = (_REPO / 'collector.py').read_text()
    assert '_v547b_raw_cache.get(source_id)' in text, (
        'V547b regression: per-city loop no longer reads from '
        '_v547b_raw_cache. The parallel pre-fetch warmed it for '
        'nothing — sync fetches will run again in the loop.'
    )
