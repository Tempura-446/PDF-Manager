/* Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE. */
// 0.52.0 in a browser: with Links on, a click on a link opens the box for
// changing it, also on a page with many annotations before the link (pdf.js
// stacks each one higher, which put the link's own <a> above ZPDF's box).
// Run by test_linkbox_ui.py:
//   node linkbox.js <page URL with #token=...> <id of an opened PDF: many markups, then a link>
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
    await page.evaluate(() => { reader.openAnnotate(); reader.pdfViewer.currentScaleValue = "page-fit"; reader._setEditMode("LINKS"); });
    await page.waitForSelector(".rd-objbox.link", { timeout: 15000 });
    const at = await page.evaluate(() => {
      const r = document.querySelector(".rd-objbox.link").getBoundingClientRect();
      return [r.left + r.width / 2, r.top + r.height / 2];
    });
    out.sections = await page.evaluate(() => document.querySelectorAll(".annotationLayer section").length);
    out.under = await page.evaluate(([x, y]) => {
      const el = document.elementFromPoint(x, y);
      return el ? { tag: el.tagName, box: el.classList.contains("rd-objbox") } : null;
    }, at);
    await page.mouse.click(at[0], at[1]);
    await page.waitForSelector("dialog[open]", { timeout: 5000 }).catch(() => {});
    out.box = await page.evaluate(() => document.querySelector("dialog[open] h3")?.textContent || null);
  } catch (e) {
    out.errors.push(String(e.stack || e));
  } finally {
    await browser.close();
  }
  console.log(JSON.stringify(out));
})();
