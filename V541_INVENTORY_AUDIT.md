# V541 Inventory Audit — post-V541b + V543 fixes

**Computed:** 2026-05-06 01:13 UTC (after V543 fix deployed)
**Total cities:** 1,761
**Compute duration:** 2.12s

## Status breakdown

| Status   | Count | % of fleet |
|----------|-------|------------|
| **Pass** | **28** | 1.6% |
| Degraded | 1,161 | 65.9% |
| Fail     | 572   | 32.5% |

## Reason-code drilldown

| Status | Reason code | Count | Action |
|--------|-------------|-------|--------|
| **Pass** | all_thresholds_met | **28** | Sellable. The current ad-ready inventory. |
| Degraded | unknown_platform | 1,019 | Bulk-source recipients (NULL source_type). Correctly excluded from per-city sort. NOT actionable as Pass candidates. |
| Degraded | low_profiles | 79 | Has permits but <100 contractor profiles. Run profile-build / wait for more permits to accumulate. |
| Degraded | low_permits | 63 | Has source but <100 permits. Smaller cities; lower-priority lift unless ad-targeting them. |
| Fail | platform_fail | 509 | V527 collectors.health_check returned 'fail' (consecutive errors / source dead). Investigate per-platform. |
| Fail | never_visited | 34 | Active in prod_cities but no scraper_runs row. V496 source_endpoint patch class — daemon never picks them up. |
| Fail | dead_source | 29 | >21d since last successful collection. Source has gone dark. |

## The 28 Pass cities (current sellable inventory)

Major metros:
- new-york-city, chicago-il, los-angeles, philadelphia, washington-dc
- phoenix-az, las-vegas, denver-co, miami, miami-dade-county
- san-jose, charlotte, columbus, fort-worth, sacramento-ca, sacramento-county

Mid-size + standout:
- cincinnati-oh, cleveland-oh, pittsburgh, raleigh, nashville
- new-orleans, orlando-fl
- bernalillo-county (Albuquerque area)
- everett (WA?), fort-collins-co, sugar-land (TX), cambridge-ma

## Top 10 Almost-Pass (closest to threshold = highest-leverage lift)

Cities 1-2 thresholds short. Sorted by permits-to-100 distance:

| City | permits | profiles | with_phone | Action to upgrade |
|------|---------|----------|------------|-------------------|
| garfield | 95 | ? | ? | 5 more permits → Pass. Wait for next collection. |
| east-orange | 92 | ? | ? | 8 more permits → Pass. NJ has no bulk DBPR; DDG-only. |
| fairfax | 62 | ? | ? | 38 permits short. |
| franklin-tn | 30 | ? | ? | 70 permits short. Active scraper in TN. |
| farmersville-tx | 29 | ? | ? | 71 permits short. |
| bartlett | 25 | ? | ? | TX TDLR import would lift TX cities. |
| cary | 25 | ? | ? | NC; daemon visiting via stale-first sort. |
| bethesda | 23 | ? | ? | MD county area. |
| boulder-county | 17 | ? | ? | CO assessor data only — limited. |
| fairview-tx | 16 | ? | ? | TX TDLR would lift. |

## Subscriber risk

Couldn't run subscribers JOIN due to deferred-tool / DB schema constraints. The
4 known production subscribers (per /api/admin/digest/status) need cross-check
with city_health. Wes can run:
```sql
SELECT u.email, u.digest_cities FROM users u WHERE u.digest_active = TRUE
   AND u.digest_cities IS NOT NULL;
```
Any subscriber whose `digest_cities` slug isn't in the 28 Pass list will be
silently filtered by V540 PR4 at digest send time.

## Ad spend waste

I don't have access to the Google Ads campaign config from this session.
Recommendation: pull the campaign geo-targeting list and intersect against the
Pass list. Pause any campaign targeting a non-Pass city.

## Recommended next chain step

**V544 = unify the 3 indexation gates on city_health.is_sellable_city:**
1. Page-level noindex (server.py:6886-6897, currently `permit_count < 20`)
2. Sitemap inclusion (V540 PR3 already partial — currently fail-open since
   pre-V543 city_health was 0-Pass; now activates)
3. V540 pre-curation picker filter (already aligned)

After V544, the SEO contract is coherent: only the 28 Pass cities get indexed,
and Google sees consistent signals across sitemap + page header + internal
links. This converts the V541 audit's 28-Pass count into actual SEO indexation
impact.

— Computed 2026-05-06 01:13 UTC
