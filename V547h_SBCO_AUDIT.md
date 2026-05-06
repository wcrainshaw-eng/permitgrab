# V547h — SBCO Profile Extraction Audit

**Computed:** 2026-05-06 ~13:55 UTC
**Trigger:** Wes flagged SBCO as P0 — 2,055 permits but 0 contractor profiles.
Gabriel SBC subscribed; his digest yesterday was substantively empty.

## Findings

### Smoking gun #1 — SBCO field_map has NO `contractor_name` mapping

`city_registry_data.py:6447-6455` for `san_bernardino_county_ca`:

```python
"field_map": {
    "permit_number": "Record Number",
    "permit_type": "Record Type",
    "address": "Address",
    "description": "Description",
    "issued_date": "Date",
    "date": "Date",
    "status": "Status",
},
```

No `contractor_name` key. The Accela CapDetail "Licensed Professional"
extraction never wires into the field_map.

### Smoking gun #2 — DB confirms the bug

```sql
SELECT COUNT(*), COUNT(contractor_name), COUNT(NULLIF(contractor_name, ''))
FROM permits WHERE source_city_key='san-bernardino-county';
```

Result: **total=2055, with_name=491, with_nonempty_name=0**

The 491 are empty strings — contractor_name field exists on the row but
holds `''`. All 5 raw permit samples confirm: `contractor_name=''`.

### Smoking gun #3 — Plain `accela` platform doesn't fetch CapDetail

The Accela Licensed-Professional extraction lives in
`accela_portal_collector.py:367 parse_accela_licensed_professional()`,
called only by `fetch_accela_arcgis_hybrid()` (line 406).

SBCO is configured as `platform: 'accela'` (NOT
`accela_arcgis_hybrid`). So even after fixing the field_map, the plain
`accela` fetcher doesn't visit CapDetail per permit — it scrapes the
Accela ACA search grid directly, which doesn't expose contractors.

V476 fixed Tampa via `accela_arcgis_hybrid` (ArcGIS feed → per-permit
CapDetail HTML scrape). SBCO would need either:
- An ArcGIS feed for SBC County permits (requires research; may not exist)
- A new platform variant `accela_capdetail_only` that uses Accela's
  permit list as the index and per-permit CapDetail as the deep-fetch
- Or: accept that SBCO's source doesn't expose contractor data

## Fleet impact — same pattern across 105 cities

The /api/admin/v547h/broken-extraction endpoint surfaces every
city with permits>=500 + profiles<50:

| Platform | Cities affected |
|----------|----------------|
| accela | 43 |
| socrata | 41 |
| arcgis | 19 |
| ckan | 1 |
| accela_arcgis_hybrid | 1 |

Examples (from raw query):

**field_map_miss (with_contractor=0):** SBCO, atlanta, hagerstown-md,
virginia-beach, la-county, san-francisco, baltimore, norfolk-va,
plainfield, lindley-ny, ...

**profile_build_fail (with_contractor>>0 but profiles=0):** fenner-ny
(38,137 contractor names → 0 profiles), portland-wi (10,189 → 0),
white-castle-la (3,404 → 6), portland (11,143 → 0), ...

The profile_build_fail bucket is a SEPARATE bug class —
`refresh_contractor_profiles()` isn't running for these slugs, even
though contractor data is available.

## Two distinct bug classes

### Class A: field_map_miss (SBCO et al.)

The collector parses but doesn't extract contractor. Source-side issue.
**Fix path:** per-city, requires source schema research +
field_map update or platform migration.

### Class B: profile_build_fail (fenner-ny et al.)

Contractor names are extracted into permits.contractor_name correctly
but `contractor_profiles` rows aren't created. Profile-build pipeline
issue.
**Fix path:** investigate `contractor_profiles.refresh_contractor_profiles()`
for affected slugs. May be one bug fix that unblocks the entire
profile_build_fail bucket.

## Subscriber impact (Gabriel P0)

All 4 active subscribers have `last_digest_sent_at = 2026-05-06 11:13`,
including gabriel@smartbuildpros.com (digest_cities=`["san-bernardino-county"]`).

**Pre-V546c (yesterday):** V540 PR4 filter was silently fail-opening
because `has_city_health_data()` row.values() bug returned False. So
gabriel may have received an "empty" digest for SBC (permits but no
contractor data, or fallback to most-recent permits).

**Post-V546c (deployed today ~13:00 UTC):** has_city_health_data()
correctly returns True. V540 PR4 filter will correctly identify SBCO
as Degraded(low_profiles) and suppress gabriel's entire digest at
tomorrow's 7 AM ET fire with status='v540_safety_net_all_fail'.

**Action: Wes emails gabriel TODAY** before tomorrow's silent
suppression. Until V547h's underlying contractor-extraction fix
lands, gabriel's only-city is structurally non-deliverable.

## V547h shipped

- New admin endpoint `/api/admin/v547h/broken-extraction` surfaces
  the field_map_miss vs profile_build_fail buckets. Cron a daily
  probe and alert if any Pass city appears.
- Regression test pins endpoint shape so the daily check doesn't
  break silently.
- This audit doc.

## V547h NOT YET DONE — deferred

- Actual SBCO contractor extraction fix. Requires research into
  whether SBC publishes an ArcGIS feed for permits, OR engineering
  a CapDetail-only fetcher for plain Accela. ~2-4h work.
- profile_build_fail root cause investigation. Could be one fix that
  unblocks dozens of cities.
- Fleet-wide regression test "every Pass city has profiles>0" —
  recommended pattern but needs the actual check to pass first
  (currently SBCO would fail it).

## Recommendations for Wes

1. **TODAY:** Email gabriel proactively. Subject suggestion:
   "PermitGrab data update — your San Bernardino County subscription"
   Explain: source published incomplete contractor data; we're
   investigating; offer either (a) add a working metro alongside
   (LA, Riverside, Phoenix), (b) refund the May charge, (c) wait
   1-2 weeks for the fix.

2. **THIS WEEK:** Investigate SBC ArcGIS feed OR ship plain-Accela
   CapDetail fetcher. SBC alone is 43 cities' worth of broken
   extraction (Accela cluster).

3. **NEXT:** Investigate profile_build_fail bucket — fenner-ny et al.
   have 38K+ contractor names available, just not building profiles.
   Could be one fix that pops 50+ cities into Pass.

— Computed 2026-05-06 ~13:55 UTC
