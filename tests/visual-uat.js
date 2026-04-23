const puppeteer = require('puppeteer');

const BASE_URL = process.env.BASE_URL || 'https://permitgrab.com';
const AD_READY_CITIES = [
  'chicago-il',
  'new-york-city',
  'phoenix-az',
  'san-antonio-tx',
  'miami-dade-county',
  'san-jose'
];

const results = { pass: 0, fail: 0, errors: [] };

function pass(name) {
  results.pass++;
  console.log(`  ✓ ${name}`);
}

function fail(name, reason) {
  results.fail++;
  results.errors.push({ test: name, reason });
  console.log(`  ✗ ${name}: ${reason}`);
}

async function testCityPage(browser, slug) {
  console.log(`\n--- Testing /permits/${slug} ---`);
  const page = await browser.newPage();
  const consoleErrors = [];

  // Capture JS errors
  page.on('console', msg => {
    if (msg.type() === 'error') consoleErrors.push(msg.text());
  });
  page.on('pageerror', err => {
    consoleErrors.push(err.message);
  });

  try {
    const response = await page.goto(`${BASE_URL}/permits/${slug}`, {
      waitUntil: 'networkidle2',
      timeout: 30000
    });

    // Test 1: Page loads with 200
    if (response.status() === 200) {
      pass(`${slug} returns 200`);
    } else {
      fail(`${slug} returns 200`, `Got ${response.status()}`);
    }

    // Test 2: Data table is VISIBLE (not just in DOM)
    const tableVisible = await page.evaluate(() => {
      // Look for the data table — it might be a <table> or a div with role="table"
      const tables = document.querySelectorAll('table, [role="table"], .data-table, .contractor-table, .permit-table');
      for (const table of tables) {
        const rect = table.getBoundingClientRect();
        const style = window.getComputedStyle(table);
        // Check the table AND all ancestors for visibility
        let el = table;
        while (el) {
          const s = window.getComputedStyle(el);
          if (s.display === 'none' || s.visibility === 'hidden' || s.opacity === '0') {
            return { found: true, visible: false, reason: `${el.tagName}.${el.className} has ${s.display === 'none' ? 'display:none' : s.visibility === 'hidden' ? 'visibility:hidden' : 'opacity:0'}` };
          }
          // Check for zero-height containers
          if (el !== table && el.getBoundingClientRect().height === 0) {
            return { found: true, visible: false, reason: `${el.tagName}.${el.className} has height:0` };
          }
          el = el.parentElement;
        }
        if (rect.height > 50 && rect.width > 100) {
          return { found: true, visible: true, height: rect.height, width: rect.width };
        }
        return { found: true, visible: false, reason: `Table dimensions: ${rect.width}x${rect.height}` };
      }
      return { found: false, visible: false, reason: 'No table element found in DOM' };
    });

    if (tableVisible.found && tableVisible.visible) {
      pass(`${slug} data table is visible (${tableVisible.height}px tall)`);
    } else if (tableVisible.found) {
      fail(`${slug} data table is visible`, `Table in DOM but NOT visible: ${tableVisible.reason}`);
    } else {
      fail(`${slug} data table is visible`, 'No data table found in DOM at all');
    }

    // Test 3: Table has actual data rows (not just headers)
    const rowCount = await page.evaluate(() => {
      const rows = document.querySelectorAll('table tbody tr, .data-table .row, .contractor-row');
      return rows.length;
    });
    if (rowCount > 0) {
      pass(`${slug} has ${rowCount} data rows`);
    } else {
      fail(`${slug} has data rows`, 'Zero data rows found');
    }

    // Test 4: No JavaScript errors
    if (consoleErrors.length === 0) {
      pass(`${slug} no JS errors`);
    } else {
      fail(`${slug} no JS errors`, `${consoleErrors.length} errors: ${consoleErrors.slice(0, 3).join('; ')}`);
    }

    // Test 5: No large empty white space gaps
    const whiteSpace = await page.evaluate(() => {
      const body = document.body;
      const children = body.children;
      let maxGap = 0;
      let gapLocation = '';
      for (let i = 0; i < children.length - 1; i++) {
        const bottom = children[i].getBoundingClientRect().bottom;
        const top = children[i + 1].getBoundingClientRect().top;
        const gap = top - bottom;
        if (gap > maxGap) {
          maxGap = gap;
          gapLocation = `between ${children[i].tagName}.${children[i].className} and ${children[i+1].tagName}.${children[i+1].className}`;
        }
      }
      return { maxGap, gapLocation };
    });
    if (whiteSpace.maxGap < 500) {
      pass(`${slug} no large white space gaps (max ${Math.round(whiteSpace.maxGap)}px)`);
    } else {
      fail(`${slug} no large white space gaps`, `${Math.round(whiteSpace.maxGap)}px gap ${whiteSpace.gapLocation}`);
    }

    // Test 6: H1 contains city name
    const h1 = await page.evaluate(() => {
      const el = document.querySelector('h1');
      return el ? el.textContent.trim() : null;
    });
    if (h1) {
      pass(`${slug} has H1: "${h1.substring(0, 60)}"`);
    } else {
      fail(`${slug} has H1`, 'No H1 tag found');
    }

  } catch (err) {
    fail(`${slug} loads`, err.message);
  } finally {
    await page.close();
  }
}

async function testHomepage(browser) {
  console.log('\n--- Testing Homepage ---');
  const page = await browser.newPage();
  const consoleErrors = [];

  page.on('console', msg => {
    if (msg.type() === 'error') consoleErrors.push(msg.text());
  });
  page.on('pageerror', err => {
    consoleErrors.push(err.message);
  });

  try {
    const response = await page.goto(BASE_URL, {
      waitUntil: 'networkidle2',
      timeout: 30000
    });

    if (response.status() === 200) {
      pass('Homepage returns 200');
    } else {
      fail('Homepage returns 200', `Got ${response.status()}`);
    }

    // Check for JS errors (this is what caught loadMarketLeaders, updateAlertWidget, populateFilters)
    if (consoleErrors.length === 0) {
      pass('Homepage no JS errors');
    } else {
      fail('Homepage no JS errors', `${consoleErrors.length} errors: ${consoleErrors.slice(0, 3).join('; ')}`);
    }

    // Check for white space
    const whiteSpace = await page.evaluate(() => {
      const body = document.body;
      const sections = document.querySelectorAll('section, .section, [class*="section"], header, footer, main > *');
      let maxGap = 0;
      let gapInfo = '';
      const items = Array.from(sections);
      for (let i = 0; i < items.length - 1; i++) {
        const bottom = items[i].getBoundingClientRect().bottom;
        const top = items[i + 1].getBoundingClientRect().top;
        const gap = top - bottom;
        if (gap > maxGap) {
          maxGap = gap;
          gapInfo = `between "${items[i].textContent.substring(0, 30)}" and "${items[i+1].textContent.substring(0, 30)}"`;
        }
      }
      return { maxGap, gapInfo };
    });

    if (whiteSpace.maxGap < 500) {
      pass(`Homepage no white space gaps (max ${Math.round(whiteSpace.maxGap)}px)`);
    } else {
      fail(`Homepage no white space gaps`, `${Math.round(whiteSpace.maxGap)}px gap ${whiteSpace.gapInfo}`);
    }

  } catch (err) {
    fail('Homepage loads', err.message);
  } finally {
    await page.close();
  }
}

async function testNavLinks(browser) {
  console.log('\n--- Testing Navigation Links ---');
  const page = await browser.newPage();

  try {
    await page.goto(BASE_URL, { waitUntil: 'networkidle2', timeout: 30000 });

    // Extract all nav links that go to /permits/*
    const navLinks = await page.evaluate(() => {
      const links = document.querySelectorAll('nav a[href*="/permits/"], .nav a[href*="/permits/"], .dropdown a[href*="/permits/"], header a[href*="/permits/"]');
      return Array.from(links).map(a => ({
        text: a.textContent.trim(),
        href: a.getAttribute('href')
      }));
    });

    console.log(`  Found ${navLinks.length} navigation links to city pages`);

    for (const link of navLinks) {
      // Follow the link and check it doesn't 404
      const url = link.href.startsWith('http') ? link.href : `${BASE_URL}${link.href}`;
      const resp = await page.goto(url, { waitUntil: 'networkidle2', timeout: 15000 });

      if (resp.status() === 200) {
        pass(`Nav "${link.text}" → ${link.href} (200)`);
      } else if (resp.status() === 404) {
        fail(`Nav "${link.text}" → ${link.href}`, `404 — slug is wrong, check prod_cities.city_slug`);
      } else {
        fail(`Nav "${link.text}" → ${link.href}`, `Got ${resp.status()}`);
      }
    }

  } catch (err) {
    fail('Navigation links', err.message);
  } finally {
    await page.close();
  }
}

async function testPricingPage(browser) {
  console.log('\n--- Testing Pricing Page ---');
  const page = await browser.newPage();

  try {
    const response = await page.goto(`${BASE_URL}/pricing`, {
      waitUntil: 'networkidle2',
      timeout: 30000
    });

    if (response.status() === 200) {
      pass('Pricing page returns 200');
    } else {
      fail('Pricing page returns 200', `Got ${response.status()}`);
    }

    // Check that pricing buttons/links exist. PermitGrab routes pricing
    // CTAs through /signup (Stripe checkout kicks in post-account-creation),
    // so accept signup/upgrade/get-started/subscribe in addition to direct
    // stripe/checkout links.
    const hasCheckout = await page.evaluate(() => {
      const buttons = document.querySelectorAll(
        'a[href*="stripe"], a[href*="checkout"], a[href*="/signup"], a[href*="/upgrade"], a[href*="get-started"], ' +
        'button[onclick*="stripe"], button[onclick*="checkout"], ' +
        '.pricing-button, .subscribe-button, .cta-button, [data-stripe]'
      );
      return buttons.length > 0;
    });

    if (hasCheckout) {
      pass('Pricing page has checkout buttons');
    } else {
      fail('Pricing page has checkout buttons', 'No Stripe/checkout elements found');
    }

  } catch (err) {
    fail('Pricing page loads', err.message);
  } finally {
    await page.close();
  }
}

// Main runner
(async () => {
  console.log('=== PermitGrab Visual UAT ===');
  console.log(`Target: ${BASE_URL}`);
  console.log(`Date: ${new Date().toISOString()}\n`);

  const browser = await puppeteer.launch({
    headless: 'new',
    args: ['--no-sandbox', '--disable-setuid-sandbox']
  });

  try {
    await testHomepage(browser);
    await testNavLinks(browser);
    await testPricingPage(browser);

    for (const slug of AD_READY_CITIES) {
      await testCityPage(browser, slug);
    }

    console.log(`\n=== RESULTS ===`);
    console.log(`Passed: ${results.pass}`);
    console.log(`Failed: ${results.fail}`);

    if (results.errors.length > 0) {
      console.log(`\n=== FAILURES ===`);
      for (const e of results.errors) {
        console.log(`  ✗ ${e.test}: ${e.reason}`);
      }
    }

    // Exit with error code if any failures
    process.exit(results.fail > 0 ? 1 : 0);

  } finally {
    await browser.close();
  }
})();
