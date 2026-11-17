/* Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE. */
// 0.48.0 in a browser: stamps of your own. "Your stamps…" in the Stamp list
// opens the box; a green CHECKED without name and date is added and saved;
// it is listed under Yours and chosen; a click on the page stamps it in its
// colour; after the page is loaded again it is still offered; removed in the
// box, it is gone.
// Run by test_own_stamps.py:
//   node ownstamps.js <page URL with #token=...> <id of an opened PDF> [shots folder]
// Prints one JSON object with what it found.
const { chromium } = require("playwright");

(async () => {
  const [url, id, shots] = process.argv.slice(2);
  const out = { errors: [] };
  const browser = await chromium.launch();
  try {
    const page = await (await browser.newContext({ viewport: { width: 1400, height: 1000 } })).newPage();
    page.on("pageerror", (e) => out.errors.push(String(e.message || e)));
    page.on("console", (m) => { if (m.type() === "error" && !/Failed to load resource/.test(m.text())) out.errors.push(m.text()); });
    const open = async () => {
      await page.goto(url + "&open=" + id);
      await page.waitForSelector(".page canvas", { timeout: 60000 });
      await page.waitForFunction(() => reader.doc && reader.loading.hidden && reader.ownStamps, null, { timeout: 30000 });
      await page.evaluate(() => { reader.openAnnotate(); reader.pdfViewer.currentScaleValue = "page-fit"; reader._setEditMode("STAMPS"); });
      await page.waitForTimeout(500);
    };
    const yours = () => page.evaluate(() => [...reader.stampSelect.querySelectorAll('optgroup[label="Yours"] option')].map((o) => o.textContent));
    const manage = async () => {
      await page.selectOption('select[aria-label="Stamp"]', "manage");
      await page.waitForSelector("dialog[open] .rd-ownstamps", { timeout: 5000 });
    };

    await open();
    out.atFirst = { yours: await yours(), manage: await page.evaluate(() => !!reader.stampSelect.querySelector('option[value="manage"]')) };

    // The box: a row is there to fill in. Green, CHECKED, without name and date.
    await manage();
    await page.fill("dialog[open] .rd-ownstamps li input[type=text]", "checked");
    await page.$eval("dialog[open] .rd-ownstamps li input[type=color]", (el) => { el.value = "#1f8a3b"; el.dispatchEvent(new Event("input", { bubbles: true })); });
    await page.uncheck("dialog[open] .rd-ownstamps li input[type=checkbox]");
    if (shots) await page.screenshot({ path: `${shots}/own-stamps-box.png` });
    await page.click("dialog[open] button:has-text('Save')");
    await page.waitForFunction(() => reader.ownStamps.length === 1 && !document.querySelector("dialog[open]"), null, { timeout: 10000 });
    out.saved = await yours();
    out.chosen = await page.evaluate(() => reader.stampSelect.value);
    out.datedBox = await page.evaluate(() => reader.stampDated.checked);

    // A click on the page: stamped, in its colour.
    const version = await page.evaluate(() => reader.activeTab.version || 0);
    const box = await page.evaluate(() => { const r = reader.pdfViewer.getPageView(0).div.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; });
    await page.mouse.click(box[0], box[1]);
    await page.waitForFunction((v) => (reader.activeTab.version || 0) > v && reader.loading.hidden, version, { timeout: 30000 });
    await page.waitForSelector(".page canvas");
    out.placed = await page.evaluate(async () => {
      const a = (await (await reader.doc.getPage(1)).getAnnotations()).find((x) => x.subtype === "Stamp");
      return a ? { subtype: a.subtype, color: a.color ? Array.from(a.color) : null } : null;
    });
    if (shots) await page.screenshot({ path: `${shots}/own-stamp-placed.png` });

    // Loaded again (as when ZPDF starts again): still offered, and the one chosen.
    await open();
    out.afterReload = { yours: await yours(), chosen: await page.evaluate(() => reader.stampSelect.value) };

    // "Your own words…" chosen, the box opened and cancelled: still "Your own words…".
    await page.selectOption('select[aria-label="Stamp"]', "");
    await manage();
    await page.click("dialog[open] button:has-text('Cancel')");
    await page.waitForFunction(() => !document.querySelector("dialog[open]"));
    out.ownWordsKept = await page.evaluate(() => reader.stampSelect.value === "");
    await page.selectOption('select[aria-label="Stamp"]', "own:CHECKED");

    // Removed in the box: gone from the list. The focus goes on to what's next
    // (here Add a stamp), and Name and date is back at its own choice.
    await manage();
    await page.click('dialog[open] .rd-ownstamps li button[aria-label="Remove this stamp"]');
    out.focusAfterBin = await page.evaluate(() => document.activeElement?.classList.contains("rd-ownstamp-add"));
    await page.click("dialog[open] button:has-text('Save')");
    await page.waitForFunction(() => reader.ownStamps.length === 0 && !document.querySelector("dialog[open]"), null, { timeout: 10000 });
    out.afterRemove = await yours();
    out.afterRemoveChoice = await page.evaluate(() => [reader.stampSelect.value, reader.stampDated.checked]);
  } catch (e) {
    out.errors.push(String(e.stack || e));
  } finally {
    await browser.close();
  }
  console.log(JSON.stringify(out));
})();
