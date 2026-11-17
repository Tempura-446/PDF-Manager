/* Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE. */
// 0.50.0 in a browser: turning on one of pdf.js's tools (Highlight, Text,
// Draw) on a document that has a text box of its own (a callout) doesn't mark
// the document as changed: pdf.js only takes the box into its storage to be
// able to edit it. A real change afterwards is still noticed.
// Run by test_adopted_ui.py:
//   node adopted.js <page URL with #token=...> <id of an opened PDF with a callout>
// Prints one JSON object with what it found.
const { chromium } = require("playwright");

(async () => {
  const [url, id] = process.argv.slice(2);
  const out = { errors: [] };
  const browser = await chromium.launch();
  try {
    const page = await (await browser.newContext({ viewport: { width: 1400, height: 1000 } })).newPage();
    page.on("pageerror", (e) => out.errors.push(String(e.message || e)));
    await page.goto(url + "&open=" + id);
    await page.waitForSelector(".page canvas", { timeout: 60000 });
    await page.waitForFunction(() => reader.doc && reader.loading.hidden, null, { timeout: 30000 });
    const state = () => page.evaluate(() => ({ version: reader.activeTab.version || 0, taken: reader.doc.annotationStorage.size,
      changed: reader._changed(), needsSaving: !!reader.activeTab.dirty }));
    out.opened = await state();
    await page.evaluate(() => reader.openAnnotate());
    for (const mode of ["HIGHLIGHT", "FREETEXT", "INK"]) {
      await page.evaluate((m) => reader._setEditMode(m), mode);
      await page.waitForTimeout(700);
      out[mode] = await state();
    }
    // A real change: something in pdf.js's storage that would be saved.
    await page.evaluate(() => reader.doc.annotationStorage.setValue("pdfjs_internal_editor_test", { annotationType: 3, value: "x" }));
    await page.waitForTimeout(200);
    out.afterAChange = await state();
  } catch (e) {
    out.errors.push(String(e.stack || e));
  } finally {
    await browser.close();
  }
  console.log(JSON.stringify(out));
})();
