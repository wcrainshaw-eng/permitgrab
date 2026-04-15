# V171 UAT Findings (2026-04-15)

## UAT Steps Tested

### 1. Homepage
- ✅ /healthz responds instantly via WSGI middleware
- ✅ /api/health returns V171
- ✅ Homepage returns 200
- ⚠️ City count depends on freshness recalc having run — verify after manual recalc

### 2. Sign Up Flow
- ✅ User model has trial_started_at, trial_end_date, stripe fields
- ✅ Welcome email function exists (send_welcome_free, send_welcome_pro_trial)
- ❓ NEEDS MANUAL TEST: actual signup flow in browser

### 3. City Page (/permits/<slug>)
- ✅ City pages return 200 (tested NYC, Austin, Dallas, Houston)
- ✅ Trade categories populated (99.94% coverage)
- ✅ CSV export endpoint exists (/api/permits/<slug>/export.csv)
- ⚠️ Trade filter chips and tier badges need template wiring (modules exist but UI integration needs verification)

### 4. Saved Searches
- ✅ /api/saved-searches returns 401 for unauthenticated users
- ✅ SavedSearch model with all CRUD routes
- ❓ NEEDS MANUAL TEST: UI "Save this search" button on filtered views

### 5. Daily Alert Emails
- ✅ send_daily_alerts() function exists
- ✅ /api/admin/run-daily-alerts trigger route exists (returns 401 without key)
- ✅ /unsubscribe/<id> route exists
- ✅ Email template created (saved_search_digest.html)
- ❓ NEEDS MANUAL TEST: trigger via admin and check inbox

### 6. Stripe/Trial Flow
- ✅ Checkout session creator exists (line ~11200)
- ✅ Webhook handler exists (line ~11247)
- ✅ User.is_pro() checks trial_end_date
- ❓ NEEDS MANUAL TEST: test card 4242 checkout flow

### 7. Violations
- ✅ 67,195 violations in DB (NYC, Chicago, LA, Philadelphia)
- ✅ /api/violations/<slug> returns data
- ✅ Violation section on city pages (template added)

## Items Requiring Manual Browser Testing
1. Full signup flow
2. City page UI (trade chips, tier badges, save search button)
3. Stripe checkout with test card
4. Email delivery for saved search alerts
5. Unsubscribe link in emails

## Known Issues
- Admin API queries time out when collector is running (known limitation, documented in RUNBOOK)
- Freshness recalc too heavy for HTTP — must use Render Shell
- No branch protection on main (needs GitHub settings)
- Render auto-deploy gate status unknown (needs dashboard check)
