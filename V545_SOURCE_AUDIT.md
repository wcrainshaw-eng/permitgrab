## V545 — Source quality audit + digest dry-run + V507/V509 reconciliation

**Computed:** 2026-05-06 ~01:50 UTC (after V544/V544x deploy)
**Total active prod_cities:** 1,761
**City_health rows:** 1,761 (28 Pass / 1,161 Degraded / 572 Fail)
**Subscribers:** 4 (1 paid pro_monthly, 1 free, 1 pro, 1 pro_monthly never-sent)
**Compute confirmed accurate** — V541b/V543 silent AttributeError class is gone; numbers reflect real fleet state.

---

## Headline finding — the rubric labels lie

**The 509 "platform_fail" Fail cities are NOT erroring.** Last-24h Socrata
runs across the fleet: 979 no_new + 514 success + **0 errors**. Yet 447
Socrata cities are tagged `platform_fail` in city_health. Same pattern
on ArcGIS: 35 cities tagged platform_fail; the daemon ran ArcGIS 494
times in 24h, almost zero errors.

Reading collectors/_base.py:health_check, `status='fail'` is set when
`visit_age_hours > recent_hours_threshold` (36h for socrata, 36h for
arcgis, 72h for accela). compute.py then maps that to
`reason_code='platform_fail'`. So platform_fail in city_health really
means **"daemon hasn't reached this city in 36h"** — not "the platform
is erroring." The label is misleading, and it's why this audit was
needed.

The daemon visited only **186 unique cities in 24h, 191 in 7d**, while
**3,043 total runs** fired in 24h. The same ~186 cities are getting hit
~16x each per day, and ~550 platform cities (out of 736 platform-typed
active) are sitting outside the 36h freshness window. **Daemon coverage,
not source death, is the dominant Fail cause in V541's inventory.**

---

## Phase A — Status × reason_code breakdown (deduplicated)

| Status | Reason | Count | What it actually means |
|--------|--------|-------|------------------------|
| **Pass** | all_thresholds_met | **28** | Real Pass — sellable today. |
| Degraded | unknown_platform | 1,019 | Bulk recipient (NULL source_type). Excluded from per-city sort by design — not actionable as Pass candidates. |
| Degraded | low_profiles | 79 | Permits flowing but contractor-extraction is thin. Field_map miscoupling cluster. |
| Degraded | low_permits | 63 | Source live, daemon visiting, just small city. |
| Fail | platform_fail | 509 | **MISLABELED.** No actual platform error in the last 24h. = daemon hasn't visited within 36h freshness threshold. ~447 of these are Socrata. |
| Fail | never_visited | 34 | Active row but no scraper_runs. V496 source_endpoint patch class. |
| Fail | dead_source | 29 | >21d since last successful collection. True source death. |

Fleet-coverage corroboration:

| Metric | Count | Note |
|--------|-------|------|
| Active prod_cities | 1,761 | |
| Active w/ NULL source_endpoint | 1,534 | Mostly bulk recipients; 5 V507/V509 platform cities still NULL after V496 (cedar-park, garland-tx, irving-tx, frisco-tx, round-rock) |
| Platform-typed active cities | 736 | The "needs daemon visit" universe |
| Unique cities visited 24h | 186 | 25% of platform fleet |
| Unique cities visited 7d | 191 | Barely changes — daemon stuck on same set |
| Total scraper_runs 24h | 3,043 | ~16 visits per visited city |

### Per-platform health vs. visit volume (24h)

| source_type | Pass | Degraded | Fail | 24h runs | Note |
|-------------|------|----------|------|----------|------|
| socrata | 7 | 62 | **481** | 1,493 | 447 of the Fail are platform_fail = stale-visit, NOT actual errors |
| accela | 6 | 69 | 28 | 719 | More balanced — Accela's 72h threshold is more forgiving |
| arcgis | 11 | 11 | 53 | 494 | 35 platform_fail = stale-visit |
| ckan | 3 | 0 | 2 | 91 | Small fleet, mostly fine |
| carto | 1 | 0 | 0 | 21 | Philadelphia working |

---

## Phase B — V507/V509 reconciliation

| City | V507/V509 | city_health | Permits | Profiles | Last run | Verdict |
|------|-----------|-------------|---------|----------|----------|---------|
| **las-vegas** | V507 #1 | **Pass** | 7,509 | 1,358 | 24h | ✅ Working — V507 LV addition delivered |
| **charlotte** | V507 (Mecklenburg) | **Pass** | 844 | 102 | 24h | ✅ Working — borderline but Pass |
| **fort-worth** | V507 (TX TDLR) | **Pass** | 15,485 | 1,913 | 24h | ✅ Working — TX TDLR enrichment took |
| **sugar-land** | V507 (TX TDLR) | **Pass** | 4,011 | 1,406 | 24h | ✅ Working |
| seattle | V509 violations | Degraded(low_profiles) | 1,151 | 30 | 24h | ⚠️ Slow-warming — broken extraction |
| san-bernardino-county | V510 (Accela retag) | Degraded(low_profiles) | 2,056 | 0 | 12h | ⚠️ 0 profiles after 2K permits — extraction broken **(P0 — gabriel's only city)** |
| austin-tx | V507 (TX TDLR) | Degraded(low_profiles) | 23,545 | **1** | 24h | ❌ Extraction broken — 23K permits → 1 profile. Field_map bug. |
| atlanta | (pre-V507 Accela) | Fail(platform_fail) | 5,164 | 0 | 5d ago | ❌ Daemon hasn't visited in 5d + extraction broken |
| tampa-fl | accela_arcgis_hybrid | Fail(platform_fail) | 2,449 | 13 | 10d ago | ❌ Daemon hasn't visited in 10d (ArcGIS hybrid path stalled) |
| frisco-tx | V507 (TX TDLR) | Fail(platform_fail) | 1,044 | 277 | 10d ago | ❌ Daemon stale; profiles existed → was working |
| round-rock | V507 (TX TDLR) | Fail(platform_fail) | 216 | 0 | 10d ago | ❌ Daemon stale + extraction broken |
| **san-antonio-tx** | V507 (TX TDLR) | **Fail(dead_source)** | 26,492 | 4,912 | 24d ago | ❌ Real death — Accela COSA endpoint went dark 2026-04-12 |
| cedar-park | V507 (TX TDLR) | Fail(platform_fail) | 146 | 0 | 10d ago | ❌ Daemon stale + NULL source_endpoint (V496 missed) |
| norfolk-va | V509 violations | Fail(platform_fail) | 10,229 | 0 | 10d ago | ❌ Daemon stale + 10K permits → 0 profiles (extraction broken) |
| garland-tx | V507 (TX TDLR) | Degraded(unknown_platform) | 202 | 0 | **never** | ❌ NULL source_endpoint — never ran since V507 |
| irving-tx | V507 (TX TDLR) | Degraded(unknown_platform) | 245 | 0 | **never** | ❌ NULL source_endpoint — never ran since V507 |

### V507/V509 buckets

- **WORKING (4):** las-vegas, charlotte, fort-worth, sugar-land. Per-city
  unblock estimates from V507 (13-16 ad-ready cities) didn't materialize
  because daemon coverage broke before TX TDLR enrichment could lift the
  TX cluster's profiles → phones path.
- **EXTRACTION BROKEN (4):** austin-tx (1/23545), atlanta (0/5164),
  norfolk-va (0/10229), san-bernardino-county (0/2056). Common pattern
  matches V315 Cambridge / V319 Miami / V320 Arlington TX trap —
  field_map names a field the source no longer populates, OR the source
  schema has the contractor field under a name not yet mapped.
- **STARVED BY DAEMON (5):** atlanta, tampa-fl, frisco-tx, round-rock,
  cedar-park. Last run >5-10 days ago. Same V526 fleet-stale class —
  V526's stale-first sort orders correctly but doesn't ENFORCE coverage,
  so a backed-up high-priority subset starves the rest.
- **NEVER RAN (2):** garland-tx, irving-tx. Active rows, NULL
  source_endpoint, no source_id. V496 patch missed these.
- **TRUE SOURCE DEATH (1):** san-antonio-tx. Accela COSA went dark
  2026-04-12 (24d). 26K permits + 4,912 profiles already in the bag —
  retire the Accela source and migrate to TX TDLR-only enrichment.

---

## Phase C / D — Tomorrow's 7 AM ET digest forecast

V540 PR4 filters digest_cities to **Pass-only**. Anything Fail or
Degraded gets silently dropped. Forecast is deterministic from
city_health × subscribers; no `--dry-run` flag required (deferred to
V546+ if Wes wants automated nightly previews).

| Subscriber | Plan | Cities (raw) | Pass-filter | Outcome |
|------------|------|--------------|-------------|---------|
| main@onpointgb.com | free | miami-dade-county ✓ | 1 city | **HEALTHY** — digest fires |
| ethanmokoena1516@gmail.com | pro_monthly (never-sent) | miami-dade-county ✓, miami ✓, atlanta ✗, austin-tx ✗, tampa-fl ✗ | 2 cities | **MIXED** — digest fires for 2; 3 silently dropped to digest_log safety_net_skip |
| **gabriel@smartbuildpros.com** | **pro_monthly PAID** | san-bernardino-county ✗ (Degraded(low_profiles)) | **0 cities** | 🚨 **ALL-FAIL P0** — V540 PR4 returns v540_safety_net_all_fail; no digest fires. Paid customer gets nothing tomorrow. |
| wcrainshaw@gmail.com | pro | "Atlanta" ✗ (capitalization + Fail status) | 0 cities | ALL-FAIL — digest suppressed (also slug-case bug; raw is "Atlanta" not "atlanta") |

**Tomorrow's 7 AM ET digest forecast:**
- **HEALTHY: 1** (main@onpointgb)
- **MIXED: 1** (ethanmokoena)
- **ALL-FAIL: 2** (gabriel ⚠️ paid, Wes — informational since he's the operator)

---

## P0 — gabriel@smartbuildpros.com

**This is a paying pro_monthly customer.** His only digest_city is
`san-bernardino-county`, which is currently Degraded(low_profiles) — has
2,056 permits but 0 contractor_profiles. V540 PR4 will silently filter
all his cities out tomorrow at 7 AM ET. He gets nothing.

Wes's options before 7 AM ET:

| Option | Cost | Risk |
|--------|------|------|
| (a) Upgrade san-bernardino-county to Pass via V508 Playwright Accela contractor extraction | ~30-60 min engineering, requires running V508 collector cycle | Medium — V508 is the new contractor-from-detail-page scraper, not yet smoke-tested at scale on SBC |
| (b) Email gabriel tonight w/ "temporary data interruption" + offer to add 1-2 Pass cities (LA, Riverside, etc.) | Low — manual email | Customer-facing — he ASKED for SBC, redirecting may feel like a downgrade |
| (c) Override V540 PR4 to allow Degraded cities through for gabriel only (per-subscriber whitelist) | ~20 min, but bypasses the safety net | Low — degrades his digest quality but he gets SOMETHING |
| (d) Default: silent suppression | $0 | **Highest** — paid customer gets nothing tomorrow with no notice |

Default (silent suppression) is the worst outcome — recommend (b) tonight + (a) tomorrow as the fix arc.

---

## Phase D status

A `--dry-run` mode for the digest scheduler is NOT shipped. The forecast
above is computed deterministically from city_health × V540 PR4 logic,
which is what the digest will actually do at 7 AM ET. Building a
dedicated dry-run flag is V546-deferred unless Wes wants automated
nightly previews (cron@06:00 ET that emails Wes the forecast).

---

## V546 recommendation — daemon coverage is the highest-impact lever

| V546 sub-fix | Lines | Cities lifted | Cost |
|--------------|-------|---------------|------|
| **V546a — daemon coverage enforcement** | ~50 | up to 447 Socrata + 35 ArcGIS = ~480 | Modify `pick_next_cities` to guarantee every platform city gets visited within its `_RECENT_VISIT_HOURS` window before re-visiting any city. Currently the stale-first sort lets a high-pri subset starve the rest. |
| V546b — atlanta/austin-tx/norfolk-va/SBC field_map fixes | ~20 | 4 broken-extraction cities | Match V315/V319 pattern — query source schema, find contractor field, update field_map. Each ~30 min. |
| V546c — relabel `platform_fail` → `stale_visit` when caused by visit-age | ~3 | 0 directly, but stops misleading future audits | compute.py:267 should split into `stale_visit` (visit_age) vs `platform_fail` (consec errors). |
| V546d — V496 patch the 5 NULL-endpoint orphan platform cities | ~5 | 5 (cedar-park, garland-tx, irving-tx, frisco-tx, round-rock) | Re-run /api/admin/patch-source-endpoint with explicit slug list. |
| V546e — retire san-antonio-tx Accela source, keep the data | ~5 | 1 (SA stays Pass on existing 4,912 profiles) | Mark SA inactive in prod_cities or migrate to TX TDLR-only path. 24d dead, won't recover. |

**V546a alone could shift the inventory dramatically.** After enforcing
coverage, the rubric will re-classify the 447 Socrata "platform_fail"
cities to their actual state — many will land in Pass (if permits +
profiles + phones thresholds are met but the only thing keeping them
Fail was the visit-age check), the rest will land cleanly in
low_profiles / low_permits / dead_source where they belong.

V546a also unblocks V507's TX TDLR promise — austin-tx/SA/houston/dallas
were supposed to be lifted by phone enrichment, but if the daemon never
reaches them within 36h, the city_health rubric keeps flagging them
Fail regardless of profile count.

---

## Answer to Wes's bigger question

> "Are we shipping bad data or measuring it wrong?"

**Both — but more measuring-it-wrong than shipping-bad-data.**

- The 28 Pass cities are real and sellable.
- The 1,019 unknown_platform Degraded are correctly excluded.
- The 142 low_profiles + low_permits Degraded are real Degraded.
- The 29 dead_source Fail are real source death.
- The 34 never_visited Fail are real config gaps (NULL source_endpoint).
- **The 509 platform_fail Fail are 88% mislabel** — 447+ are stale-visit
  artifacts of daemon coverage starvation, not source failures.

So the rubric is RIGHT in spirit (a stale-visit city IS unfit for sale)
but the LABEL hides the root cause. Once V546a enforces coverage, the
447 Socrata cities will re-classify to Pass / low_profiles / low_permits
in proportion to their real data — most likely lifting the Pass list
from 28 → 50-80, not 28 → 500.

We are NOT in V507 BEAST's "13-16 ad-ready cities post-deploy" land —
that promise was conditional on daemon coverage holding, and it didn't.
But we're also NOT shipping fundamentally bad data. The data we have on
the 28 Pass cities is genuine, V544 will index them coherently, and
V546a is a small fix unlocking a much larger inventory than today's 28.

— Computed 2026-05-06 ~01:50 UTC, post-V544x CI green
