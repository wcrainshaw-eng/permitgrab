# AD_COPY_REFRESH — 7 Zero-Impression Ad Groups (2026-05-03)

**Context:** Buffalo + Cleveland already saved (Pending review). Remaining 7
ad groups still sitting at 0 impressions because (a) generic headlines that
don't match keyword intent, and (b) in some cases, wrong destination URL.
This file is the exact paste-in spec for each remaining ad group.

**Why this file exists:** I was applying these via the Google Ads UI but
hit the "Confirm it's you" 2FA wall after the Cleveland save. Henderson's
URL fix + 5 headline replacements were entered into the editor but never
persisted because Save kept triggering the 2FA challenge that requires
your phone. Apply this spec via either:
1. **Google Ads Editor desktop app** (downloads cached state, applies
   changes offline, uploads in one batch) — recommended.
2. **One fresh-auth pass through the web UI** — sign out, sign back in
   with your phone nearby, then save each ad in sequence within the same
   session before the 2FA cookie expires.

**Headline rules (Google enforces):**
- 30 character maximum each.
- Don't replace headlines pinned to a slot (visible pin icon).
- Required slot 1 (PermitGrab) and slot 3 (Contact Information) should
  stay as-is on every ad — those are pinned.
- Replace 5 unpinned generics: Find New Projects, Stop Chasing Start
  Winning, Fuel Your Business Growth, Need More Construction Leads,
  We're Here to Help (or whichever 5 are unpinned + generic in that ad).

---

## #1 Henderson NV  *(URL BUG — fix first)*

**Final URL bug:** currently `https://permitgrab.com/permits/miami-dade-county`
→ change to `https://permitgrab.com/permits/henderson-nv`

**Display path 2:** currently `miami-dade` → change to `henderson-nv`

**Real data:** 120,925 Clark County (Henderson) property owners,
364 phones, 2,572 contractor profiles. Henderson uses an
inline-phone permit feed (BUSINESSPHONE column) so phones come with the
permit, no enrichment lag.

**5 headlines (replace generics):**
1. Henderson Building Permits  *(26)*
2. Henderson Contractor Leads  *(26)*
3. 120K Henderson Property Owners  *(28)*
4. Clark County Permit Data  *(24)*
5. Henderson NV Roofing Leads  *(26)*

---

## #2 San Jose CA

**Final URL:** verify `https://permitgrab.com/permits/san-jose` is correct.
Display path 2 should be `san-jose`.

**Real data:** 1,684 San Jose property owners, 112 phones, 1,310 profiles.
CA CSLB enrichment delivers phones for licensed contractors. Silicon
Valley = high-value commercial + multifamily permits.

**5 headlines:**
1. San Jose Building Permits  *(26)*
2. San Jose Contractor Leads  *(26)*
3. 1,300+ San Jose Contractors  *(27)*
4. Silicon Valley Permit Data  *(26)*
5. CSLB-Verified San Jose Leads  *(28)*

---

## #3 Los Angeles CA

**Final URL:** verify `https://permitgrab.com/permits/los-angeles` is correct.
Display path 2 should be `los-angeles`.

**Real data:** 600 LA phones, 788 profiles. LA city portal stopped
publishing permits ~May 2023 (V258 dead-end), but CSLB phone enrichment
keeps the contractor side fresh. Lean into "verified phones" not
"daily permits" since permit feed is structurally stale.

**5 headlines:**
1. LA Contractor Phones Verified  *(28)*
2. Los Angeles Contractor Leads  *(28)*
3. 600+ LA Phones Updated Weekly  *(28)*
4. CSLB-Verified LA Contractors  *(29)*
5. Greater LA Permit Data  *(22)*

---

## #4 Nashville TN

**Final URL:** verify `https://permitgrab.com/permits/nashville` is correct.
Display path 2 should be `nashville`.

**Real data:** 71,263 Davidson County (Nashville) property owners, 77
phones, 714 profiles. Nashville Metro permit feed live + Davidson assessor.
TN has no bulk state license DB so phone enrichment is DDG-only — lean
into owner data + permit volume.

**5 headlines:**
1. Nashville Building Permits  *(26)*
2. Nashville Contractor Leads  *(26)*
3. 71K Davidson Property Owners  *(28)*
4. Nashville Permit Data Daily  *(27)*
5. Music City Construction Leads  *(28)*

---

## #5 Phoenix AZ

**Final URL:** verify `https://permitgrab.com/permits/phoenix-az` is correct.
Display path 2 should be `phoenix-az`.

**Real data:** 247,129 Maricopa County property owners (V474 split:
maricopa primary 79K + maricopa_secondary 168K). 1,088 phones, 1,966
profiles. Phoenix has both a live permit feed AND maintenance violations
feed AND assessor data — full stack.

**5 headlines:**
1. Phoenix Building Permits  *(24)*
2. Phoenix Contractor Leads  *(24)*
3. 247K Maricopa Property Owners  *(29)*
4. Phoenix Roofing Leads Daily  *(27)*
5. Maricopa County Permit Data  *(27)*

---

## #6 San Antonio TX

**Final URL:** verify `https://permitgrab.com/permits/san-antonio-tx` is correct.
Display path 2 should be `san-antonio-tx`.

**Real data:** Bexar County (currently 8,648 stored owners — V491 promised
711K from upgraded source but only 3,871 landed; hotfix pending). 3,838
phones, 4,626 profiles — the highest phone count of any city. SA already
has the strongest phone coverage; ad just needs city-keyword headlines
to unlock impressions.

**5 headlines:**
1. San Antonio Building Permits  *(28)*
2. 3,800+ San Antonio Phones  *(24)*
3. San Antonio Contractor Leads  *(28)*
4. Bexar County Permit Data  *(24)*
5. SA Hail-Belt Roofing Leads  *(25)*

---

## #7 Buyer Intent National  *(no city, different angle)*

**Final URL:** keep `https://permitgrab.com/permits/miami-dade` (top-CTR
city — 5.56% conv) OR change to `https://permitgrab.com/pricing` to
pull pricing-curious traffic.

**Real data:** Cross-city national value prop. 22 city pages live, 17
have full data stack. $149/mo flat. Best angle: high-value contractors
in any market.

**5 headlines (no city tokens — universal value props with proof points):**
1. 22 Cities Permit Data Live  *(25)*
2. $149/mo Unlimited Lead Access  *(28)*
3. Daily Permit Updates Verified  *(29)*
4. Real Phones, No Stale Lists  *(26)*
5. Try PermitGrab Free for 7 Days  *(29)*

---

## Apply via Google Ads Editor (recommended path)

1. Open Google Ads Editor desktop app, sign in with wcrainshaw@gmail.com.
2. Click "Get recent changes" — pulls the latest campaign state
   (including Buffalo+Cleveland's saved Pending ads).
3. Filter to campaign `PermitGrab - Contractor Leads Search`.
4. For each ad group above:
   a. Open the responsive search ad.
   b. Edit Final URL + Display path 2 (only Henderson needs URL fix).
   c. In the headlines panel, replace the 5 generics named above with
      the 5 specific headlines listed.
   d. Save.
5. Click "Post" — Editor uploads all changes in one batch. **No 2FA
   challenge** because Editor uses an API token, not a session cookie.

## Apply via web UI (fallback)

1. Sign out of Google Ads, close all tabs.
2. Have your phone unlocked next to you.
3. Sign back in, complete the 2FA challenge once.
4. Open the campaign, edit each ad in turn — within ~30 minutes the
   2FA cookie should let you save all 7 without re-prompting.
5. If the 2FA wall hits again mid-batch, take a 10-minute break and
   try the remaining ones — Google's heuristic seems to fire on
   rapid-fire saves.

## Expected impact

Buffalo's identical refresh moved Ad strength **Poor → Good** (highest
in campaign). Same lift expected on these 7. With ad strength = Good,
Google starts serving them and the impression count moves off zero
within 24-48 hours. CTR target: match Miami-Dade's 2.61% baseline at
minimum, NYC's 9.94% on Phoenix/SA where data depth is highest.
