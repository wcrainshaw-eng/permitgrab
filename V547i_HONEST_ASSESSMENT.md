# V547i — Honest Assessment

**Computed:** 2026-05-06 ~14:15 UTC
**Trigger:** Wes's V547i directive said "fix the field_map, 10-min config edit." After tracing the code path, this is **not what's actually broken** and the one-line fix doesn't apply.

## Wes's premise (from the V547i directive)

> "SBCO uses the same accela_arcgis_hybrid platform — the field exists, it's just not wired into the field_map."

## Reality (from city_registry_data.py:6435-6463 + collector.py:1907-1916)

```python
"san_bernardino_county_ca": {
    "platform": "accela",          # ← NOT accela_arcgis_hybrid
    "endpoint": "https://aca-prod.accela.com/SBC/Cap/CapHome.aspx?...",
                                    # ← Accela ACA URL, NOT ArcGIS
    "field_map": {
        "permit_number": "Record Number",
        # ... no contractor_name. But ALSO: the source records from
        # plain `accela` don't have a contractor field to map TO.
    },
}
```

`collector.py:1907-1916` dispatches:
- `platform == "accela"` → `fetch_accela()` → `fetch_accela_portal()` (line 119)
- `platform == "accela_arcgis_hybrid"` → `fetch_accela_arcgis_hybrid()` (line 406)

`fetch_accela_portal` scrapes the Accela ACA search grid HTML. The
grid columns are: Record Number, Date, Record Type, Address, Description,
Status, Expiration Date, Project Name. **NO contractor field exists in
the search grid** — that's the V476 P1 root cause that the hybrid
pattern was invented to work around.

So adding `contractor_name` to the SBCO field_map can't help —
there's no source value to map TO.

## Why migrating to `accela_arcgis_hybrid` doesn't work

`fetch_accela_arcgis_hybrid` requires an ArcGIS FeatureServer query
URL that returns a record list with per-permit detail-page URLs in a
`url_field` (default 'URL'). Tampa works because Tampa's
`arcgis.tampagov.net Planning/PermitsAll` feed populates each row with
a CLICKGOVLINK / URL attribute.

SBC does NOT publish such a feed. Probed via SSH from Render:
- `gis.sbcounty.gov` — DNS doesn't resolve
- `maps.sbcounty.gov/arcgis` — exists; folders are
  ROV / Surveyor / Test2 / TPIMS / Utilities — **no Permits/Building folder**
- ArcGIS Hub global search for "san bernardino building permits"
  → 9 hits, all are LAMP (septic), Community Development Projects
  (zoning), or generic OpenDataMapService. **No building permits feed.**

So Option (migrate to hybrid) requires an ArcGIS feed that doesn't
exist. We can't ship a one-line config edit for SBCO.

## What an actual fix looks like (V547i-2, deferred — ~30-60 min)

The Accela ACA search grid result rows DO include a per-permit
detail-page link — clicking a row navigates to `Cap/CapDetail.aspx?
Module=...&capID=...`. The `_parse_results_table` function in
`accela_portal_collector.py` doesn't currently capture this link.

Engineering V547i-2:

1. Modify `_parse_results_table` to also return each row's detail-page
   `<a href>` from the Record Number cell.
2. Modify `fetch_accela_portal` (or wrap it as a new variant
   `fetch_accela_with_capdetail`) to follow each detail link, fetch
   the HTML, and run the existing `parse_accela_licensed_professional`
   parser (already in `accela_portal_collector.py:367`) to extract
   contractor + license + email + phone.
3. Cap detail-page fetches at `max_details_per_run=200` per cycle to
   stay within the worker's runtime budget.
4. Add an `accela_search_capdetail` platform variant for cities like
   SBCO that lack an ArcGIS feed but have detail-page deep-extraction
   available.

That's not a 10-min config edit — it's a real refactor with test
coverage.

## What V547i actually shipped (this commit)

1. `test_v547i_accela_hybrid_cities_have_url_field` — defensive
   regression test that fails any time a CITY_REGISTRY entry sets
   `platform="accela_arcgis_hybrid"` with an endpoint that doesn't
   look like an ArcGIS REST URL. Catches the SBCO-class config bug
   before it ships.

2. `/api/admin/v547h/broken-extraction` (shipped V547h) already
   surfaces the field_map_miss vs profile_build_fail buckets fleet-
   wide. Daily cron + alert-on-Pass-cities is the long-term watch.

3. This honest doc.

## Per-subscriber audit (Wes asked, run it now)

Query: every active subscriber × each subscribed city × profile/health
status:

| Subscriber | Plan | Slug | Permits | Profiles | Health |
|---|---|---|---|---|---|
| ethanmokoena1516 | pro_monthly | atlanta | 5,160 | **0** | Fail |
| ethanmokoena1516 | pro_monthly | austin-tx | 23,780 | 135 | Degraded |
| ethanmokoena1516 | pro_monthly | miami | 8,806 | 724 | Pass ✓ |
| ethanmokoena1516 | pro_monthly | miami-dade-county | 7,459 | 4,322 | Pass ✓ |
| ethanmokoena1516 | pro_monthly | tampa-fl | 2,448 | 13 | Fail |
| **gabriel** | **pro_monthly** | **san-bernardino-county** | **2,133** | **0** | **Degraded** |
| main@onpointgb | free | miami-dade-county | 7,459 | 4,322 | Pass ✓ |
| wcrainshaw | pro | "Atlanta" (capitalized) | 0 | 0 | unknown |

**Three paying subscribers have at least one city with the SBCO-class
bug:**

- gabriel — sole city is Degraded(0 profiles) — same bug as SBCO
- ethanmokoena — atlanta has 0 profiles despite 5,160 permits;
  tampa-fl has 13 profiles despite 2,448 permits (broken extraction)
- wcrainshaw — "Atlanta" capitalization causes 0 permits to match;
  the slug-case bug means his digest pulls nothing regardless of
  status

ethanmokoena gets a partially-useful digest from miami + miami-dade-
county. Gabriel gets the full silent-suppression tomorrow. Wes's own
subscription has 0 matches today.

## What Wes asked for vs what's possible

**Wes asked:** ship V547i + V547j (bulk fix for broken field_maps),
verify SBCO Pass-or-Degraded-with-profiles before tomorrow's 7 AM ET.

**Reality:** The fix isn't a field_map edit — it's an Accela
CapDetail deep-fetch refactor (~30-60 min coding + 1-2h observation).
That can't ship + be observed before tomorrow's 7 AM ET digest fire
(~16h from now) with confidence.

**Honest options:**

A. **Email gabriel** anyway today — even with the V547i-2 engineering
   in flight, the 16-hour window doesn't leave room for safe deploy +
   observation + Google reaching SBC contractors. The "positive
   message" Wes wanted requires the fix actually landing first.

B. **Override V540 PR4** for gabriel only (per-subscriber whitelist
   that allows Degraded cities through). One small admin endpoint that
   marks gabriel as "deliver everything regardless of city_health
   status." His digest fires tomorrow with whatever SBCO permit data
   exists (sans contractors). Stopgap until V547i-2 lands.

C. **Ship V547i-2 (CapDetail deep-fetch) NOW** + observe overnight.
   Risk: 16h is tight; bug discovered Saturday morning means weekend
   on-call. Reward: V547i-2 also unblocks the OTHER 42 plain-accela
   cities (atlanta, etc.) — bigger fleet impact.

D. **Skip tomorrow's digest for gabriel** by manually setting his
   `last_digest_sent_at` to tomorrow (so the 7 AM check sees "already
   sent" and skips). Buys 1 day to ship V547i-2.

My recommendation: **B (override) + C (ship V547i-2 in parallel).**
B unblocks gabriel TODAY without engineering risk. C is the real fix
that takes time and observation. Don't conflate them.

— Computed 2026-05-06 ~14:15 UTC
