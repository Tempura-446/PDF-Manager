// 0.30.0 in a browser: a right-click action started from Explorer. The page
// opens with #action=<batch>, follows the batch's job on a card at the foot
// of the window, and shows the result as a new, unsaved tab whose note says
// what it was made from and offers Save….
// Run by test_rightclick.py:
//   node actions30.js <page URL with #token=...&action=...>
// Prints one JSON object with what it found.
const { chromium } = require("playwright");

(async () => {
  const [url] = process.argv.slice(2);
  const out = { errors: [], native: [] };
  const browser = await chromium.launch();
  try {
    const page = await (await browser.newContext({ viewport: { width: 1280, height: 800 } })).newPage();
    page.on("pageerror", (e) => out.errors.push(String(e.message || e)));
    page.on("console", (m) => { if (m.type() === "error" && !/Failed to load resource/.test(m.text())) out.errors.push(m.text()); });
    page.on("dialog", (d) => { out.native.push(d.type() + ": " + d.message()); d.dismiss().catch(() => {}); });
    await page.goto(url);
    await page.waitForSelector(".action-card", { timeout: 30000 });
    out.cardTitle = await page.textContent(".action-card .ac-title");
    await page.waitForFunction(() => typeof reader !== "undefined" && reader && reader.tabs.length === 1 && reader.doc && document.querySelector(".page canvas"), null, { timeout: 60000 });
    await page.waitForFunction(() => /Opened/.test(document.querySelector(".action-card .ac-msg")?.textContent || ""), null, { timeout: 30000 });
    out.cardMsg = await page.textContent(".action-card .ac-msg");
    out.tab = await page.evaluate(() => {
      const t = reader.activeTab;
      return { name: t.name, unsaved: !!t.unsaved, pages: reader.doc.numPages, from: t.madeBy?.from?.name, fromId: t.madeBy?.from?.fileId || null, op: t.madeBy?.op };
    });
    out.note = await page.evaluate(() => document.querySelector(".fp-done")?.textContent || "");
    out.saveOffered = await page.evaluate(() => !!document.querySelector(".fp-saveas"));
    // Close on the card takes it away.
    await page.click(".action-card button");
    await page.waitForFunction(() => !document.querySelector(".action-card"), null, { timeout: 5000 }).catch(() => {});
    out.cardGone = await page.evaluate(() => !document.querySelector(".action-card"));
  } catch (e) {
    out.errors.push("script: " + (e.stack || e));
  } finally {
    await browser.close();
  }
  process.stdout.write(JSON.stringify(out));
})();
