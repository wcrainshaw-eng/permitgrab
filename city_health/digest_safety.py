"""V540 PR4: defense-in-depth digest safety net. Per Wes's V540
reframe — pre-curation (PR3) means subscribers can only ever pick
Pass cities. PR4's job is the rare case where a city was Pass at
subscribe-time and went Fail after.

Behavior:
- Filter the subscriber's city list to only Pass cities.
- For each dropped city: log to digest_log with status='safety_net_skip'
  so the admin dashboard sees an alert; the alert IS the notification
  (Wes monitors digest_log).
- Don't email the subscriber 'sorry'. The expectation we set in PR3
  is that the system works; if it doesn't this once, we eat it
  silently and notify ourselves.
- City flips back to Pass next day → digests resume automatically
  (next cycle picks up the Pass status from the daily-cron-refreshed
  city_health table).
"""
from __future__ import annotations

import db as permitdb

from .curation import get_sellable_cities, has_city_health_data


def filter_subscriber_cities_for_digest(user_email, slugs):
    """Drop slugs that aren't currently sellable (Pass).

    Returns the filtered list. If the original list is non-empty but
    the filtered result is empty, caller should skip the digest send
    entirely (per V540 PR4: don't email a subscriber whose entire
    chosen city set has gone Fail).

    Cold-start fail-open: if city_health hasn't been populated yet,
    return the input list unchanged (matches the PR3 fail-open
    contract).

    Each dropped slug → one digest_log row at status='safety_net_skip'
    with the slug in error_message. The admin dashboard / nightly
    triage script picks these up so Wes investigates ahead of any
    user-visible bug report.
    """
    if not slugs:
        return []
    if not has_city_health_data():
        return list(slugs)

    sellable = get_sellable_cities()
    kept = [s for s in slugs if s in sellable]
    skipped = [s for s in slugs if s not in sellable]

    if skipped:
        try:
            conn = permitdb.get_connection()
            for slug in skipped:
                conn.execute(
                    "INSERT INTO digest_log "
                    "(recipient_email, permits_count, status, error_message) "
                    "VALUES (?, 0, 'safety_net_skip', ?)",
                    (
                        user_email or 'unknown',
                        f'V540 PR4: city {slug!r} flipped to non-Pass since subscribe-time; '
                        f'skipped for this digest. Investigate via /api/admin/city-health?slug={slug}',
                    ),
                )
            conn.commit()
        except Exception as e:
            print(
                f'[city_health.digest_safety] log skip failed for {user_email}: {e}',
                flush=True,
            )

    return kept
