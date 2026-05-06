# V547g — Digest Audit (Pillar 5)

**Computed:** 2026-05-06 ~13:25 UTC
**Source:** digest_log table query, last 7 days

## Today's 7 AM ET digest fire (2026-05-06)

| sent_at | recipient_email | status | error_message |
|---------|----------------|--------|--------------|
| 11:01:09 | scheduled | sent | (none — batch row) |
| 08:01:12 | health_scheduler | sent | pass=38 degraded=1210 fail=513 errored=0 |

**Per-subscriber outcomes:** UNKNOWN. The current digest pipeline only
writes a single batch-level row per fire (`recipient_email='scheduled'`),
not per-subscriber rows. So we cannot definitively say from digest_log
alone:

- Did gabriel@smartbuildpros.com get an email?
- Did V540 PR4 fire `v540_safety_net_all_fail` for any subscriber?
- Did all 4 subscribers get processed?

**Inference (best available):**
The 11:01 row's `error_message=null` and `status=sent` suggest the batch
completed without an unhandled error. V540 PR4 safety_net_skip rows would
write to digest_log with `status` containing "safety_net" — none found
for today. But this only proves "the safety net WROTE no rows," not
"no subscriber was affected" — the writes themselves could've thrown
(broad except handler in digest_safety.py).

## V515 dup-fire verification

Last 7 days: only **one** day had a dup-fire — 2026-05-02 had
`all_subscribers` fire 2x. That predates V515 (shipped 2026-05-05).

**V515 fix held cleanly on 2026-05-05 and 2026-05-06.** No
duplicates since the fix.

## Gabriel SBCO outcome

**INDETERMINATE.** Without per-subscriber digest_log rows, I cannot
prove or disprove that gabriel@smartbuildpros.com got an email today.

Pre-V547e remediation path:
1. Manual check: SSH into worker, grep gunicorn/worker logs for
   "gabriel" or "san-bernardino-county" around 11:01 UTC.
2. Check Sendgrid (or whichever SMTP provider) for delivery logs to
   gabriel@smartbuildpros.com today.
3. Worst case: assume he got nothing and email him a "data
   interruption" note.

**Recommendation:** Treat as still-unknown. Wes should email gabriel
proactively today if not already done. Once V547e ships,
this audit is trivial going forward.

## V547e — Per-subscriber digest_log enhancement

**Scope (deferred to its own commit, not in this audit doc):**
- Each digest fire writes one row per subscriber attempt
- Schema additions (suggested):
  - `subscriber_email` (vs the current batch `recipient_email`)
  - `cycle_id` (correlation key per fire)
  - `cities_attempted` JSON (which slugs the subscriber subscribed to)
  - `cities_filtered` JSON (which slugs V540 PR4 dropped)
  - `cities_delivered` JSON (which slugs survived to the final email)
  - `permits_count` int
  - `status` enum (sent / suppressed_all_fail / suppressed_unsubscribed
    / error / skipped_no_new)
  - `error_message` (free text)

**Implementation sketch:**
- Wrap `send_daily_digest_to_user` to write a row at function start
  (cycle_id + subscriber_email + status='attempting'), then UPDATE
  the row at function exit with the final status + cities.
- Add a unique index on (cycle_id, subscriber_email) so dups are
  caught at DB level.
- Regression test: feed 3 mock subscribers, assert 3 rows written
  with correct statuses.

**Why deferred:** Requires schema migration + ~30 lines of code in
email_alerts.py + 2-3 regression tests. Not blocked on anything —
should ship in next 4-6h once observation of V547a + Pillar 2
is complete and we know the daemon is stable.

## Action items for Wes

| Item | Priority | Owner | Notes |
|------|----------|-------|-------|
| Email gabriel today (precautionary) | P0 | Wes | While we don't know if his digest fired correctly |
| Ship V547e per-subscriber logging | P1 | Code | Makes future audits trivial |
| Verify Sendgrid logs for 2026-05-06 11:01 fire | P1 | Wes | Cross-check delivery against subscriber list |
| (Long-term) GSC URL Inspection on 35 Pass cities post-V546c | P2 | Wes via Chrome MCP | After Google's next sitemap fetch |

— Computed 2026-05-06 ~13:25 UTC
