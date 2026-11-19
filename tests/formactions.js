/* Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE. */
// Button actions (reset, goto, url, email) and checkbox show/hide in Prepare form and reader.
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

    // Trigger show/hide checkbox
    await page.waitForSelector('.annotationLayer input[name="ShowMore"]');
    out.detailsHiddenInitially = await page.$eval('.annotationLayer input[name="Details"]', (el) => !!el.closest("section")?.hidden);
    await page.click('.annotationLayer input[name="ShowMore"]');
    out.detailsHiddenAfterTick = await page.$eval('.annotationLayer input[name="Details"]', (el) => !!el.closest("section")?.hidden);

    // Fill Name, then click Reset button
    await page.fill('.annotationLayer input[name="Name"]', "Ada");
    await page.evaluate(() => {
      const secs = [...document.querySelectorAll(".annotationLayer section")].filter((s) => s.dataset.actionWired === "1");
      secs[0]?.click(); // BtnReset
      secs[2]?.click(); // BtnUrl
      secs[3]?.click(); // BtnEmail
      secs[1]?.click(); // BtnGoto (page 2)
    });

    out.nameAfterReset = await page.inputValue('.annotationLayer input[name="Name"]');
    out.lastUrl = await page.evaluate(() => reader._lastOpenedUrl);
    out.lastMailto = await page.evaluate(() => reader._lastMailto);
    out.pageAfterGoto = await page.evaluate(() => reader.pdfViewer.currentPageNumber);

    console.log(JSON.stringify(out));
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
