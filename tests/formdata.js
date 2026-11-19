/* Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE. */
// Data menu in Prepare form mode: Import data, Export data, and Clear form.
const { chromium } = require("playwright");

(async () => {
  const [url, fileId, jsonPath] = process.argv.slice(2);
  const out = { errors: [] };
  const browser = await chromium.launch();
  try {
    const context = await browser.newContext({ viewport: { width: 1360, height: 900 }, acceptDownloads: true });
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

    // Import JSON via the Data menu's file input
    await page.click('.rd-formmodebar button:has-text("Data")');
    await page.waitForSelector('.rd-formdatamenu:not([hidden])');
    const [chooser] = await Promise.all([
      page.waitForEvent("filechooser"),
      page.click('.rd-formdatamenu button:has-text("Import data")'),
    ]);
    await chooser.setFiles(jsonPath);
    await page.waitForFunction(() => document.querySelector('.annotationLayer input[name="FullName"]')?.value === "Ada Lovelace");
    out.afterImportName = await page.inputValue('.annotationLayer input[name="FullName"]');

    // Export XFDF via Data menu
    await page.click('.rd-formmodebar button:has-text("Data")');
    await page.waitForSelector('.rd-formdatamenu:not([hidden])');
    const [download] = await Promise.all([
      page.waitForEvent("download"),
      page.click('.rd-formdatamenu button:has-text("XFDF")'),
    ]);
    out.exportedFilename = download.suggestedFilename();

    // Clear form via Data menu
    await page.click('.rd-formmodebar button:has-text("Data")');
    await page.waitForSelector('.rd-formdatamenu:not([hidden])');
    await page.click('.rd-formdatamenu button:has-text("Clear form")');
    await page.waitForFunction(() => document.querySelector('.annotationLayer input[name="FullName"]')?.value === "");
    out.afterClearName = await page.inputValue('.annotationLayer input[name="FullName"]');

    console.log(JSON.stringify(out));
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
