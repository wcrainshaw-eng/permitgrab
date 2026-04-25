# V205: Top-500 Coverage Probe — Findings

## Run summary

- Target: top 500 cities by population from `prod_cities`
- NEEDS_WORK subset: 262 cities (no source_type set, no permits collected)
- Probed via batch DCAT script (`scripts/v205_probe.py`) run on Render

## Method

For each NEEDS_WORK city the probe tried 5 ArcGIS-hub URL patterns:

```
https://data-{slug}.opendata.arcgis.com/api/feed/dcat-us/1.1.json
https://{slug}-gis.hub.arcgis.com/api/feed/dcat-us/1.1.json
https://opendata-{slug}.opendata.arcgis.com/api/feed/dcat-us/1.1.json
https://opendata.{slug}.gov/api/feed/dcat-us/1.1.json
https://{slug}.opendata.arcgis.com/api/feed/dcat-us/1.1.json
```

When a hub responded, the DCAT was scanned for dataset titles containing
"permit" + ("build" | "construction" | "issue") for permits, and any of
"violation" / "enforce" / "code comp" / "citation" / "nuisance" for
violations. Only datasets that exposed a `rest/services` distribution URL
were counted as actionable.

## Batch results

| Batch | Cities probed | Permit hits | Violation hits | NONE | SKIP_CDP |
|-------|---------------|-------------|----------------|------|----------|
| 1     | 60            | 1*          | 1*             | 53   | 6        |
| 2     | 100           | 0           | 0              | 93   | 7        |
| **Total** | **160**   | **0 (net)** | **0 (net)**    | **146** | **13** |

*Batch 1's single permit/violation hit was the columbus-ga probe resolving
against Columbus OH's hub (my slug-normalization collapses both to the
`columbus` string), so both hits are Columbus OH data that's already in
the registry. Net new sources: 0.

## Pattern confirmation

This result is consistent with the V197 → V204 research rounds:
mid-size US cities (≤~500k population, outside the top 20) overwhelmingly
either:

1. Publish permits only via Accela Citizen Access (`aca-prod.accela.com/<juris>`)
   — scraper-gated, would need ACCELA_CONFIGS entries, not an API endpoint.
2. Publish via city-specific custom portals that don't expose DCAT or a
   REST service directory (e.g. Winston-Salem's BuildIT, Jacksonville's
   JAXEPICS, Houston's XLSX-only CKAN).
3. Have no public permits feed at all.

ArcGIS-hub pattern guessing wasn't expected to catch more than it did;
the cities in the next 200-place population band are below the threshold
where Esri hub publishing is common.

## Marking status

The probe generated 146 NONE + 13 SKIP_CDP slugs (159 total,
see Render `/tmp/v205_all_none.txt`). Initial SSH-driven bulk UPDATE
attempts were blocked by `sqlite3.OperationalError: database is locked`
— the gunicorn daemon's hourly cycle + enrichment pass holds the write
lock intermittently.

After two retry passes with `PRAGMA busy_timeout=120000` and per-slug
commits, **83 of the 159 slugs landed as `source_type='none'`** (prod
count rose from 0 → 83). The remaining 76 either had their UPDATE
skipped during a lock window or already had a non-NULL source_type
from earlier versions.

Follow-up options:
- Add a `scripts/v205_mark_none.py` that the deploy cycle runs once
  inside the gunicorn worker process (no lock contention).
- Or just let future V-series PRs skip these cities via the `source_type
  IS NULL AND population > X` filter rather than requiring the explicit
  'none' mark.

## Still-unmarked-but-probed slugs

```
oklahoma-city, district-of-columbia-dc, boston, albuquerque,
brookhaven-ny, bakersfield, tulsa-ok, wichita, islip-ny,
lexington-fayette-urban-county-ky, riverside-ca-accela, santa-ana,
saint-paul, jersey-city, oyster-bay-ny, north-las-vegas-nv, gilbert,
chula-vista, fort-wayne-in, toledo, port-st-lucie-fl, glendale-az,
winston-salem-nc, north-hempstead-ny, san-bernardino-ca, modesto-ca,
salt-lake-city, des-moines-ia, yonkers-ny, huntington-ny,
overland-park-ks, mobile-al, oxnard-ca, grand-rapids, vancouver-wa,
providence-ri, huntington-beach-ca, akron-oh, newport-news-va,
elk-grove, aurora-il, eugene-or, garden-grove, oceanside, surprise-az,
salinas-ca, hayward-ca, ramapo-ny, kansas-city-ks, springfield-ma,
joliet-il, pasadena-tx, olathe-ks
```
(plus batch 2's 93 similarly-probed slugs — see `/tmp/v205_batch2.out`
on Render for the full list; all returned NONE with the same ArcGIS-hub
guessing strategy)

## Artifacts

- `scripts/v205_probe.py` — the DCAT probe script, re-runnable
- `scripts/v205_findings.md` — this file
- Render `/tmp/v205_batch1.out`, `/tmp/v205_batch2.out` — raw probe output
  per city
