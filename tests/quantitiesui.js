/* Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE. */
// 0.43.0 in a browser: the Measurements panel saves a quantity schedule.
// The workbook holds what's measured in the PDF and the measurement still
// only in the reader's list.
// Run by test_quantities_ui.py:
//   node quantitiesui.js <page URL with #token=...> <id of an opened drawing> <folder for the files> [shots folder]
// Prints one JSON object with what it found.
const { chromium } = require("playwright");

(async () => {
  const [url, id, folder, shots] = process.argv.slice(2);
  const out = { errors: [] };
  const browser = await chromium.launch();
  try {
    const page = await (await browser.newContext({ viewport: { width: 1400, height: 1000 }, acceptDownloads: true })).newPage();
    page.on("pageerror", (e) => out.errors.push(String(e.message || e)));
    await page.goto(url + "&open=" + id);
    await page.waitForSelector(".page canvas", { timeout: 60000 });
    await page.waitForFunction(() => reader.doc && reader.loading.hidden, null, { timeout: 30000 });
    await page.waitForTimeout(800);

    // A measurement drawn in the reader and not yet in the PDF: 216 pt at 1/4" = 1'-0" is 12 ft.
    await page.evaluate(() => {
      reader.toggleScaleBar(true);
      const tab = reader.activeTab;
      tab.scales = { ...(tab.scales || {}), 1: { ratio: 48, units: "ft", label: "1/4\" = 1'-0\"" } };
      tab.measures = [...(tab.measures || []), { id: "t1", kind: "distance", page: 1, pts: [[100, 500], [316, 500]], label: "South wall", group: "Walls" }];
      reader._drawAllMeasures();
      reader._toggleMeasPanel(true);
    });
    await page.waitForSelector(".rd-mfoot button", { timeout: 10000 });
    out.buttons = await page.$$eval(".rd-mfoot button", (l) => l.map((b) => b.textContent.trim()));
    if (shots) await page.screenshot({ path: `${shots}/quantities-panel.png` });
    // (What would be saved is caught here: the browser's save dialog can't be driven.)
    await page.evaluate(() => {
      window.kept = [];
      reader._saveBytes = async (bytes, name) => {
        let text = "";
        for (const b of bytes) text += String.fromCharCode(b);
        window.kept.push({ name, data: btoa(text) });
      };
    });
    const fs = require("fs");
    for (const [label, name] of [["Excel", "book.xlsx"], ["CSV", "list.csv"]]) {
      const before = await page.evaluate(() => window.kept.length);
      await page.click(`.rd-mfoot button:has-text("${label}")`);
      await page.waitForFunction((n) => window.kept.length > n, before, { timeout: 30000 });
      const got = await page.evaluate(() => window.kept[window.kept.length - 1]);
      out[label] = got.name;
      fs.writeFileSync(`${folder}/${name}`, Buffer.from(got.data, "base64"));
    }
  } catch (e) {
    out.errors.push(String(e.stack || e));
  } finally {
    await browser.close();
  }
  console.log(JSON.stringify(out));
})();
