# PermitGrab — Step-by-Step Launch Instructions

Everything you need to do, in order. Each step tells you whether YOU do it or Claude Code does it.

---

## PHASE 1: GET THE CODE ONLINE (30 minutes)

### Step 1: Open Claude Code
**YOU DO THIS.**
Open Claude Code on your computer (the terminal/CLI version, not Cowork).

### Step 2: Tell Claude Code to read the reference file and set up the repo
**PASTE THIS INTO CLAUDE CODE:**

```
Read the file at ~/Documents/PermitGrab/CLAUDE_CODE_REFERENCE.txt — this is the complete technical reference for the PermitGrab project. Then do the following:

1. Read all the Python files in ~/Documents/PermitGrab/ to understand the codebase.
2. Initialize a git repo in ~/Documents/PermitGrab/
3. Create a .gitignore that excludes: data/*.json, __pycache__/, *.pyc, .env, node_modules/, .DS_Store
4. Make an initial commit with all the code files.
5. Create a public GitHub repo called "permitgrab" and push the code to it using: gh repo create permitgrab --public --source=. --push
6. Give me the GitHub repo URL when done.
```

**WHAT YOU GET BACK:** A GitHub URL like `https://github.com/YourUsername/permitgrab`

### Step 3: Deploy to Render.com
**YOU DO THIS.** (takes ~5 minutes)

1. Go to **render.com** and sign up (use your GitHub account)
2. Click **"New +"** then **"Web Service"**
3. Connect your **permitgrab** GitHub repo
4. Render will auto-detect the settings from render.yaml
5. Click **"Create Web Service"**
6. Wait 2-3 minutes for it to build and deploy
7. You'll get a URL like `https://permitgrab.onrender.com`
8. Visit that URL — you should see the dashboard with sample data!

**IMPORTANT:** The free tier sleeps after 15 minutes of no traffic. First visit after sleep takes ~30 seconds to wake up. This is fine for testing. Upgrade to $7/month Starter plan when you have paying customers.

---

## PHASE 2: GET REAL DATA FLOWING (15 minutes)

### Step 4: Enable the real data collector
**PASTE THIS INTO CLAUDE CODE:**

```
In ~/Documents/PermitGrab/server.py, find the lines around line 237-238 that are commented out:
# collector_thread = threading.Thread(target=scheduled_collection, daemon=True)
# collector_thread.start()

Uncomment those two lines so the data collector runs automatically every 24 hours.

Also, run "python ~/Documents/PermitGrab/collector.py" to test that the collector can reach the city APIs and pull real permits. Show me the results.

Then commit and push the changes to GitHub.
```

**WHAT YOU GET BACK:** A report showing how many real permits were pulled from each city. Some cities might return 0 if their API is temporarily down — that's normal. As long as at least 3-4 cities return data, you're good.

### Step 5: Trigger a redeploy on Render
**YOU DO THIS.**

1. Go to your Render.com dashboard
2. Click on the permitgrab service
3. Click **"Manual Deploy"** → **"Deploy latest commit"**
4. Wait 2-3 minutes
5. Visit your URL — you should now see REAL permit data!

---

## PHASE 3: SET UP EMAIL ALERTS (20 minutes)

### Step 6: Create a Gmail App Password
**YOU DO THIS.**

1. Go to **myaccount.google.com**
2. Click **Security** (left sidebar)
3. Make sure **2-Step Verification** is turned ON
4. Search for **"App Passwords"** or go to Security → 2-Step Verification → App Passwords
5. Create a new app password (name it "PermitGrab")
6. Copy the 16-character password (looks like: `abcd efgh ijkl mnop`)
7. **Save this password somewhere safe — you'll need it in the next step**

### Step 7: Add email environment variables in Render
**YOU DO THIS.**

1. Go to your Render.com dashboard → permitgrab service
2. Click **"Environment"** tab
3. Add these environment variables:
   - `SMTP_HOST` = `smtp.gmail.com`
   - `SMTP_PORT` = `587`
   - `SMTP_USER` = `wcrainshaw@gmail.com`
   - `SMTP_PASS` = (the App Password from Step 6)
   - `FROM_EMAIL` = `wcrainshaw@gmail.com`
   - `SITE_URL` = (your Render URL, like `https://permitgrab.onrender.com`)
4. Click **"Save Changes"** — Render will auto-redeploy

### Step 8: Test email alerts
**PASTE THIS INTO CLAUDE CODE:**

```
In ~/Documents/PermitGrab/, set these environment variables then run the email test:

export SMTP_HOST=smtp.gmail.com
export SMTP_PORT=587
export SMTP_USER=wcrainshaw@gmail.com
export SMTP_PASS="YOUR_APP_PASSWORD_HERE"
export FROM_EMAIL=wcrainshaw@gmail.com
export SITE_URL=https://permitgrab.onrender.com

python email_alerts.py test wcrainshaw@gmail.com
```

**WHAT YOU GET BACK:** You should receive a test email at wcrainshaw@gmail.com showing ~15 NYC permit leads with contact info, trade tags, and project values.

---

## PHASE 4: ADD PAYMENTS (45 minutes)

### Step 9: Create a Stripe account
**YOU DO THIS.**

1. Go to **stripe.com** and sign up
2. Once in the dashboard, go to **Developers** → **API Keys**
3. Copy your **Secret key** (starts with `sk_test_` for testing, `sk_live_` for real)
4. Go to **Products** → **Add Product**
5. Create a product called "PermitGrab Professional"
   - Price: $149.00 / month
   - Recurring
6. Copy the **Price ID** (starts with `price_`)
7. Save both the Secret Key and Price ID

### Step 10: Tell Claude Code to add Stripe
**PASTE THIS INTO CLAUDE CODE:**

```
Read ~/Documents/PermitGrab/CLAUDE_CODE_REFERENCE.txt for full context.

Now add Stripe Checkout payment integration to the PermitGrab app:

1. Add "stripe" to requirements.txt
2. In server.py, add a new endpoint POST /api/create-checkout-session that:
   - Reads STRIPE_SECRET_KEY and STRIPE_PRICE_ID from environment variables
   - Creates a Stripe Checkout Session for the Professional plan ($149/mo)
   - Returns the checkout URL as JSON
   - On success, redirects to /?payment=success
   - On cancel, redirects to /?payment=cancelled
3. In dashboard_production.html, update the Professional plan pricing button to call this endpoint and redirect to Stripe Checkout
4. Add a webhook endpoint POST /api/stripe-webhook that handles checkout.session.completed events and creates/updates the subscriber record with plan="professional"
5. Commit and push to GitHub.
```

### Step 11: Add Stripe keys to Render
**YOU DO THIS.**

1. Render.com dashboard → permitgrab → Environment
2. Add:
   - `STRIPE_SECRET_KEY` = (your Stripe secret key from Step 9)
   - `STRIPE_PRICE_ID` = (your Stripe price ID from Step 9)
3. Save Changes (auto-redeploys)

---

## PHASE 5: BUY A DOMAIN (10 minutes)

### Step 12: Buy a domain name
**YOU DO THIS.**

1. Go to **namecheap.com** or **cloudflare.com/products/registrar/**
2. Search for a domain (ideas: permitgrab.com, getpermitgrab.com, permitgrableads.com)
3. Buy it (~$10-15/year)

### Step 13: Connect domain to Render
**YOU DO THIS.**

1. Render.com dashboard → permitgrab → Settings → Custom Domains
2. Click **"Add Custom Domain"**
3. Enter your domain name
4. Render gives you a CNAME record to add
5. Go to your domain registrar's DNS settings
6. Add the CNAME record Render provided
7. Wait 5-30 minutes for DNS to propagate
8. Render provides free SSL (HTTPS) automatically

---

## PHASE 6: FINAL POLISH (Claude Code does this)

### Step 14: Add authentication, rate limiting, and unsubscribe
**PASTE THIS INTO CLAUDE CODE:**

```
Read ~/Documents/PermitGrab/CLAUDE_CODE_REFERENCE.txt for full context.

Add these features to the PermitGrab app:

1. AUTHENTICATION: Add Flask-Login with email/password. Create /api/register and /api/login endpoints. Store users in data/users.json. Free users see contact info on first 5 permits only. Logged-in paid users (plan="professional" or "enterprise") see all contact info. The dashboard should show login/signup buttons in the nav bar.

2. RATE LIMITING: Install flask-limiter. Limit /api/permits to 60 requests/minute. Limit /api/subscribe to 5 requests/minute. Limit /api/register to 10 requests/hour.

3. UNSUBSCRIBE: Add /api/unsubscribe?token=xxx endpoint. Generate a unique token for each subscriber at signup. Include the token in the unsubscribe link in email footers. The endpoint sets active=false.

4. ADMIN PAGE: Create a /admin route (protected, only accessible with an admin password from the ADMIN_PASSWORD environment variable). Show: total subscribers, total permits, last collection time, subscriber list.

5. Commit and push to GitHub.
```

---

## YOU'RE LIVE!

After all these steps you have:
- A live website at your custom domain
- Real permit data updating daily from 8 cities
- Email alerts going to subscribers
- Stripe payments accepting $149/month subscriptions
- User authentication separating free vs paid features

**Monthly cost: $7-8/month** (Render Starter + domain)
**Break-even: 1 paying customer**

---

## WHAT TO COME BACK TO ME (COWORK) FOR:

- Designing and writing SEO blog posts for contractor keywords
- Creating per-city landing pages (e.g., /permits/new-york)
- Marketing strategy and copy
- Tweaking the dashboard design or pricing
- Adding new cities to the data collector
- Building sales outreach templates for contractors
- Anything business/strategy related

## WHAT TO USE CLAUDE CODE FOR:

- All coding tasks (features, bug fixes, deployment)
- Git operations (commits, pushes)
- Testing APIs and endpoints
- Adding new packages or integrations
- Database migrations
- Anything technical

---

## FILES IN YOUR DOCUMENTS/PERMITGRAB/ FOLDER:

config.py                    - City API configs and trade keywords
collector.py                 - Fetches real permits from city APIs
server.py                    - Flask web server + REST API
email_alerts.py              - Daily digest email sender
generate_sample_data.py      - Creates demo data for testing
dashboard.html               - Standalone demo (open in browser to preview)
dashboard_production.html    - Dashboard served by Flask
landing.html                 - SEO marketing landing page
email_preview.html           - Sample email preview
requirements.txt             - Python dependencies
Dockerfile                   - Docker deployment config
render.yaml                  - Render.com auto-deploy config
CLAUDE_CODE_REFERENCE.txt    - DETAILED reference file for Claude Code
STEPS_FOR_WES.md             - THIS FILE (your step-by-step guide)
PermitGrab_Technical_Spec.docx - Full technical spec document
data/permits.json            - Permit data (sample or real)
data/collection_stats.json   - Collection statistics
