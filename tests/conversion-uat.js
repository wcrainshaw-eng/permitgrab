// V251 F7: End-to-end conversion UAT.
// Covers the F1/F2/F3/F4/F5/F6 features shipped this cycle. Anonymous
// visitor browsing only — logged-in / Stripe-redirect flows are gated
// behind real sessions and test in a separate suite.
//
// Run: npm run test:conversion
// Override target: BASE_URL=... npm run test:conversion

const puppeteer = require('puppeteer');

const BASE_URL = process.env.BASE_URL || 'https://permitgrab.com';
const CITIES = ['chicago-il', 'new-york-city', 'phoenix-az', 'miami-dade-county', 'orlando-fl', 'san-jose'];

const results = { pass: 0, fail: 0, errors: [] };
const pass = (name) => { results.pass++; console.log(`  ✓ ${name}`); };
const fail = (name, reason) => {
  results.fail++;
  results.errors.push({ test: name, reason });
  console.log(`  ✗ ${name}: ${reason}`);
};

async function newPage(browser) {
  const page = await browser.newPage();
  const errs = [];
  page.on('console', (msg) => { if (msg.type() === 'error') errs.push(msg.text()); });
  page.on('pageerror', (e) => errs.push(e.message));
  page._consoleErrs = errs;
  return page;
}

async function gotoCity(page, slug, query = '') {
  return page.goto(`${BASE_URL}/permits/${slug}${query}`, { waitUntil: 'networkidle2', timeout: 30000 });
}

// ------ Scenarios ------

async function gatedPreview(browser) {
  // Run against both Chicago AND NYC — the P0 phone-leak bug hit NYC
  // specifically (212 area codes showed unredacted) so single-city
  // sampling would have kept missing it.
  for (const slug of ['chicago-il', 'new-york-city']) {
    const page = await newPage(browser);
    await gotoCity(page, slug);

    const probe = await page.evaluate(() => {
      // Only count tel: links inside the contractors table so we don't
      // double-count unrelated click-to-call links elsewhere on the page.
      const contractorScope = document.querySelector('.contractors-table');
      const telLinks = contractorScope
        ? contractorScope.querySelectorAll('a[href^="tel:"]').length
        : document.querySelectorAll('.contractors-table a[href^="tel:"]').length;
      return {
        reveal: document.querySelectorAll('.gate-reveal-btn').length,
        blurred: document.querySelectorAll('.gate-blur').length,
        cta: document.querySelectorAll('.gate-cta').length,
        contractorRows: document.querySelectorAll('.contractors-table tbody tr').length,
        telLinks,
      };
    });
    // HARD assertion — anon must have ZERO tel: links in the contractors
    // table. If this fails the phone gate is leaking and it's a P0.
    if (probe.telLinks === 0) pass(`F1.0 ${slug} anon sees no tel: phone links`);
    else fail(`F1.0 ${slug} phone leak (P0)`, `${probe.telLinks} tel: links visible to anon`);
    if (probe.reveal >= 1) pass(`F1.1 ${slug} Reveal CTAs on contractor rows`);
    else fail(`F1.1 ${slug} Reveal CTAs`, `0 .gate-reveal-btn found`);
    if (probe.blurred >= 1) pass(`F1.2 ${slug} blurred rows 6+`);
    else fail(`F1.2 ${slug} blurred rows`, `0 .gate-blur`);
    if (probe.cta >= 1) pass(`F1.3 ${slug} below-table CTA banner`);
    else fail(`F1.3 ${slug} CTA banner`, `0 .gate-cta`);
    if (probe.contractorRows >= 5) pass(`F1.4 ${slug} contractor table has ${probe.contractorRows} rows`);
    else fail(`F1.4 ${slug} rows`, `only ${probe.contractorRows}`);
    await page.close();
  }
}

async function filters(browser) {
  const page = await newPage(browser);

  // Unfiltered baseline
  await gotoCity(page, 'chicago-il');
  const baseline = await page.evaluate(() => ({
    selects: {
      trade: !!document.getElementById('f-trade'),
      zip: !!document.getElementById('f-zip'),
      days: !!document.getElementById('f-days'),
    },
    tradeOptions: document.querySelectorAll('#f-trade option').length,
    zipOptions: document.querySelectorAll('#f-zip option').length,
    rows: document.querySelectorAll('#permits-tbody tr').length,
  }));
  if (baseline.selects.trade && baseline.selects.zip && baseline.selects.days) pass('F2.1 filter bar has trade+zip+days selects');
  else fail('F2.1 filter bar', JSON.stringify(baseline.selects));
  if (baseline.tradeOptions > 2) pass(`F2.2 trade dropdown populated (${baseline.tradeOptions} options)`);
  else fail('F2.2 trade dropdown populated', `only ${baseline.tradeOptions} options`);
  // F2.3 zip: Chicago permit feed stores zip on very few records (1-2
  // distinct zips total). "Populated" here is "at least one real zip
  // option beyond 'All Zips'". Other cities with richer data still
  // validate the many-options case via the trade dropdown above.
  if (baseline.zipOptions >= 2) pass(`F2.3 zip dropdown populated (${baseline.zipOptions} options)`);
  else fail('F2.3 zip dropdown populated', `only ${baseline.zipOptions} options`);

  // Apply days=7 filter via URL
  await gotoCity(page, 'chicago-il', '?days=7');
  const filtered = await page.evaluate(() => ({
    selectedDays: (document.getElementById('f-days') || {}).value,
    hasSummary: !!document.querySelector('a[href*="Clear filters"], a[href$="/permits/chicago-il"]'),
  }));
  if (filtered.selectedDays === '7') pass('F2.4 days=7 persists in select');
  else fail('F2.4 days=7 persists', `got ${filtered.selectedDays}`);
  await page.close();
}

async function csvGate(browser) {
  const page = await newPage(browser);
  // Follow redirect → final URL should be /signup
  const resp = await page.goto(`${BASE_URL}/api/permits/chicago-il/export.csv`, { waitUntil: 'networkidle2', timeout: 20000 });
  const finalUrl = resp.url();
  if (finalUrl.includes('/signup')) pass('F3.1 CSV endpoint redirects anon to signup');
  else fail('F3.1 CSV gate', `final url: ${finalUrl}`);
  await page.close();
}

async function emailCapture(browser) {
  const page = await newPage(browser);
  await gotoCity(page, 'chicago-il', '?trade=Electrical&zip=60614');
  const form = await page.evaluate(() => {
    const h3 = document.querySelector('#email-capture h3');
    const emailInput = document.querySelector('#v211-sub-email');
    return {
      heading: h3 ? h3.textContent.trim() : '',
      hasEmailInput: !!emailInput,
    };
  });
  const looksFiltered = /electrical/i.test(form.heading) && /60614/.test(form.heading);
  if (looksFiltered) pass(`F4.1 alert form headline reflects filters: "${form.heading.slice(0, 70)}"`);
  else fail('F4.1 alert form filter headline', `heading: "${form.heading.slice(0, 100)}"`);
  if (form.hasEmailInput) pass('F4.2 email input present');
  else fail('F4.2 email input present', 'no #v211-sub-email');
  await page.close();
}

async function velocityRecency(browser) {
  const page = await newPage(browser);
  await gotoCity(page, 'chicago-il');
  const probe = await page.evaluate(() => ({
    dots: document.querySelectorAll('.recency-dot').length,
    velocityBadges: document.querySelectorAll('.velocity-badge').length,
  }));
  if (probe.dots >= 5) pass(`F5.1 recency dots rendered (${probe.dots})`);
  else fail('F5.1 recency dots', `only ${probe.dots}`);
  if (probe.velocityBadges >= 1) pass(`F5.2 velocity badges rendered (${probe.velocityBadges})`);
  else fail('F5.2 velocity badges', `0 badges`);
  await page.close();
}

async function contractorDetail(browser) {
  const page = await newPage(browser);
  // Find a real contractor link from Chicago and follow it
  await gotoCity(page, 'chicago-il');
  const href = await page.evaluate(() => {
    const a = document.querySelector('.contractors-table a[href^="/contractor/"]');
    return a ? a.getAttribute('href') : null;
  });
  if (!href) {
    fail('F6.1 contractor link on city page', 'no /contractor/* link found');
    await page.close();
    return;
  }
  pass(`F6.1 contractor link found (${href})`);

  const resp = await page.goto(`${BASE_URL}${href}`, { waitUntil: 'networkidle2', timeout: 20000 });
  if (resp.status() === 200) pass(`F6.2 detail page 200 (${href})`);
  else fail('F6.2 detail page 200', `${resp.status()}`);

  const detail = await page.evaluate(() => ({
    h1: !!document.querySelector('h1'),
    summaryCards: document.querySelectorAll('.summary-card').length,
    permitRows: document.querySelectorAll('.permit-table tbody tr').length,
    revealGate: !!document.querySelector('.gate-reveal-big, .gate-cta'),
  }));
  if (detail.h1) pass('F6.3 detail page has H1');
  else fail('F6.3 detail H1', 'no h1');
  if (detail.summaryCards >= 3) pass(`F6.4 summary tiles rendered (${detail.summaryCards})`);
  else fail('F6.4 summary tiles', `only ${detail.summaryCards}`);
  if (detail.permitRows >= 1) pass(`F6.5 permit history table has ${detail.permitRows} rows`);
  else fail('F6.5 permit history rows', 'empty');
  if (detail.revealGate) pass('F6.6 gated reveal CTA for anon');
  else fail('F6.6 gated reveal CTA', 'no gate found');

  if (page._consoleErrs.length === 0) pass('F6.7 no JS errors on detail page');
  else fail('F6.7 no JS errors on detail', `${page._consoleErrs.length}: ${page._consoleErrs.slice(0, 2).join('; ')}`);
  await page.close();
}

async function v252Surface(browser) {
  // V252 F6 leaderboard
  const lb = await newPage(browser);
  let resp = await lb.goto(`${BASE_URL}/leaderboard/chicago-il`, { waitUntil: 'networkidle2', timeout: 20000 });
  if (resp.status() === 200) pass('V252.F6 /leaderboard/chicago-il returns 200');
  else fail('V252.F6 leaderboard 200', `${resp.status()}`);
  const lbInfo = await lb.evaluate(() => ({
    rows: document.querySelectorAll('.lb-table tbody tr').length,
    h1: !!document.querySelector('h1'),
    // Anon must see zero tel: links on leaderboard too
    tel: document.querySelectorAll('.lb-table a[href^="tel:"]').length,
  }));
  if (lbInfo.rows > 10) pass(`V252.F6 leaderboard renders ${lbInfo.rows} rows`);
  else fail('V252.F6 leaderboard rows', `only ${lbInfo.rows}`);
  if (lbInfo.tel === 0) pass('V252.F6 leaderboard anon sees no tel: links');
  else fail('V252.F6 leaderboard phone leak', `${lbInfo.tel} tel: links to anon`);
  await lb.close();

  // V251 F22 shareable report
  const rep = await newPage(browser);
  resp = await rep.goto(`${BASE_URL}/report/chicago-il`, { waitUntil: 'networkidle2', timeout: 20000 });
  if (resp.status() === 200) pass('V251.F22 /report/chicago-il returns 200');
  else fail('V251.F22 report 200', `${resp.status()}`);
  const repInfo = await rep.evaluate(() => ({
    kpis: document.querySelectorAll('.kpi').length,
    cta: !!document.querySelector('.cta-footer a.btn'),
  }));
  if (repInfo.kpis >= 4) pass(`V251.F22 report shows ${repInfo.kpis} KPI tiles`);
  else fail('V251.F22 report KPIs', `${repInfo.kpis}`);
  if (repInfo.cta) pass('V251.F22 report has bottom CTA');
  else fail('V251.F22 report CTA', 'no cta-footer');
  await rep.close();

  // V252 F7 trade-first URL redirects
  const tr = await newPage(browser);
  resp = await tr.goto(`${BASE_URL}/solar/chicago-il`, { waitUntil: 'networkidle2', timeout: 20000 });
  const finalUrl = resp.url();
  if (resp.status() === 200 && finalUrl.includes('/permits/chicago-il/')) {
    pass(`V252.F7 /solar/chicago-il → ${finalUrl.split(BASE_URL).pop()}`);
  } else {
    fail('V252.F7 trade URL redirect', `ended at ${finalUrl} status ${resp.status()}`);
  }
  await tr.close();
}


async function seoBasics(browser) {
  for (const slug of CITIES) {
    const page = await newPage(browser);
    await gotoCity(page, slug);
    const seo = await page.evaluate(() => ({
      canonical: (document.querySelector('link[rel="canonical"]') || {}).href,
      h1: (document.querySelector('h1') || {}).textContent,
      jsonLd: document.querySelectorAll('script[type="application/ld+json"]').length,
    }));
    const canonicalOk = seo.canonical && seo.canonical.includes(`/permits/${slug}`);
    if (canonicalOk) pass(`SEO canonical matches ${slug}`);
    else fail(`SEO canonical ${slug}`, `got ${seo.canonical}`);
    if (seo.jsonLd >= 1) pass(`SEO JSON-LD present on ${slug} (${seo.jsonLd})`);
    else fail(`SEO JSON-LD ${slug}`, '0');
    await page.close();
  }
}

async function signupReachable(browser) {
  const page = await newPage(browser);
  const resp = await page.goto(`${BASE_URL}/signup`, { waitUntil: 'networkidle2', timeout: 20000 });
  if (resp.status() === 200) pass('/signup returns 200');
  else fail('/signup reachable', `${resp.status()}`);
  const hasEmailInput = await page.evaluate(() => !!document.querySelector('input[type="email"], input[name="email"]'));
  if (hasEmailInput) pass('/signup has email input');
  else fail('/signup email input', 'not found');
  await page.close();
}

async function mobileSignup(browser) {
  const page = await newPage(browser);
  await page.setViewport({ width: 375, height: 667, deviceScaleFactor: 2 });
  const resp = await page.goto(`${BASE_URL}/signup`, { waitUntil: 'networkidle2', timeout: 20000 });
  if (resp.status() !== 200) { fail('mobile /signup', `${resp.status()}`); await page.close(); return; }
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 5);
  if (!overflow) pass('mobile /signup no horizontal overflow at 375px');
  else fail('mobile /signup overflow', 'horizontal scroll detected');
  await page.close();
}

// ------ Runner ------
(async () => {
  console.log('=== V251 Conversion UAT ===');
  console.log(`Target: ${BASE_URL}`);
  console.log(`Date: ${new Date().toISOString()}\n`);

  const browser = await puppeteer.launch({
    headless: 'new',
    args: ['--no-sandbox', '--disable-setuid-sandbox'],
  });

  try {
    console.log('--- F1: Gated Preview ---');
    await gatedPreview(browser);
    console.log('\n--- F2: Filters ---');
    await filters(browser);
    console.log('\n--- F3: CSV Export Gate ---');
    await csvGate(browser);
    console.log('\n--- F4: Email Alert Signup ---');
    await emailCapture(browser);
    console.log('\n--- F5: Velocity + Recency ---');
    await velocityRecency(browser);
    console.log('\n--- F6: Contractor Detail ---');
    await contractorDetail(browser);
    console.log('\n--- V251 F22 / V252 F6 F7 Surface ---');
    await v252Surface(browser);
    console.log('\n--- SEO basics on all ad-ready cities ---');
    await seoBasics(browser);
    console.log('\n--- Signup flow ---');
    await signupReachable(browser);
    await mobileSignup(browser);

    console.log(`\n=== RESULTS ===`);
    console.log(`Passed: ${results.pass}`);
    console.log(`Failed: ${results.fail}`);
    if (results.errors.length) {
      console.log('\n=== FAILURES ===');
      for (const e of results.errors) console.log(`  ✗ ${e.test}: ${e.reason}`);
    }
    process.exit(results.fail > 0 ? 1 : 0);
  } finally {
    await browser.close();
  }
})();
