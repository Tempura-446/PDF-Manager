/* Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE. */
// Preview & Check form in Prepare form mode: fill with scripts running,
// discard on exit, Keep as default values, and Check form list.
const { chromium } = require("playwright");

(async () => {
  const [url, fileId] = process.argv.slice(2);
  const out = { errors: [] };
  const browser = await chromium.launch();
  try {
    const context = await browser.newContext({ viewport: { width: 1360, height: 900 } });
    const page = await context.newPage();
    page.on("pageerror", (e) => out.errors.push(String(e.message || e)));
    page.on("console", (m) => { if (m.type() === "error" && !/Failed to load resource/.test(m.text())) out.errors.push(m.text()); });
    page.on("response", async (r) => { if (r.url().includes("/api/form/") && !r.ok()) out.errors.push(`${r.status()} ${r.url()}: ${await r.text().catch(() => "")}`); });

    await page.goto(url + "&open=" + fileId);
    await page.waitForSelector('.page[data-page-number="1"] canvas', { timeout: 60000 });
    await page.waitForFunction(() => reader.doc && reader.loading.hidden, null, { timeout: 30000 });

    // Enter Prepare form mode
    await page.keyboard.press("Control+k");
    await page.waitForSelector("#palette-input");
    await page.fill("#palette-input", "Prepare form");
    await page.keyboard.press("Enter");
    await page.waitForSelector(".rd-formmodebar:not([hidden])");
    await page.waitForSelector('.rd-formfield[data-name="Qty"]');

    // Enter Preview mode
    await page.click('.rd-formmodebar button:has-text("Preview")');
    await page.waitForSelector('.rd-formmode.rd-form-preview');
    await page.waitForSelector('.rd-formmodebar button:has-text("Keep as default values"):not([hidden])');

    // Fill Qty and Price and check live calculation of Total
    await page.waitForSelector('.annotationLayer input[name="Qty"]');
    await page.fill('.annotationLayer input[name="Qty"]', "3");
    await page.locator('.annotationLayer input[name="Qty"]').press("Tab");
    await page.fill('.annotationLayer input[name="Price"]', "12");
    await page.locator('.annotationLayer input[name="Price"]').press("Tab");
    await page.waitForFunction(() => document.querySelector('.annotationLayer input[name="Total"]')?.value === "36");
    out.previewTotal = await page.inputValue('.annotationLayer input[name="Total"]');

    // Exit Preview -> typed values are discarded
    await page.click('.rd-formmodebar button:has-text("Preview")');
    await page.waitForFunction(() => !reader.formPreview);
    await page.waitForSelector('.rd-formfield[data-name="Qty"]');
    out.afterExitQty = await page.inputValue('.annotationLayer input[name="Qty"]');

    // Re-enter Preview, type new values, and click Keep as default values
    await page.click('.rd-formmodebar button:has-text("Preview")');
    await page.waitForSelector('.rd-formmode.rd-form-preview');
    await page.fill('.annotationLayer input[name="Qty"]', "4");
    await page.locator('.annotationLayer input[name="Qty"]').press("Tab");
    await page.fill('.annotationLayer input[name="Price"]', "5");
    await page.locator('.annotationLayer input[name="Price"]').press("Tab");
    await page.waitForFunction(() => document.querySelector('.annotationLayer input[name="Total"]')?.value === "20");

    await page.click('.rd-formmodebar button:has-text("Keep as default values")');
    await page.waitForFunction(() => !reader.formPreview && document.querySelector('.rd-formfield[data-name="Qty"]'));
    await page.click('.rd-formmodebar button:has-text("Save")');
    await page.waitForFunction(() => !reader.activeTab?.dirty, null, { timeout: 15000 });

    // Click Check form -> verify issues list and click an issue to select the field
    await page.click('.rd-formmodebar button:has-text("Check form")');
    await page.waitForSelector('.fp-formcheck:not([hidden]) .fp-checkitem');
    out.checkKinds = await page.$$eval('.fp-formcheck .fp-checkitem', (els) => els.map((e) => e.dataset.kind));
    await page.click('.fp-formcheck .fp-checkitem[data-kind="missing_tooltip"]');
    await page.waitForSelector('.rd-formfield.selected[data-name="Bad"]');
    out.selectedName = await page.$eval('.rd-formfield.selected', (e) => e.dataset.name);

    console.log(JSON.stringify(out));
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
