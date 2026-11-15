/* Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE. */
// 0.42.0 in a browser: Settings → Privacy. Clear history empties the Recent
// list and where you were in each file; with "Remember the files I open"
// off, neither is kept; the occasions ZPDF goes online are listed.
// Run by test_privacy_ui.py:
//   node privacyui.js <page URL with #token=...> <id of an opened file> [shots folder]
// Prints one JSON object with what it found.
const { chromium } = require("playwright");

(async () => {
  const [url, id, shots] = process.argv.slice(2);
  const out = { errors: [] };
  const browser = await chromium.launch();
  try {
    const context = await browser.newContext({ viewport: { width: 1280, height: 1000 } });
    const places = (page) => page.evaluate(() => Object.keys(localStorage).filter((k) => k.startsWith("pdfm-pos:")).length);
    // Read a file to its second page, so there's a place to remember.
    const read = async () => {
      const page = await context.newPage();
      page.on("pageerror", (e) => out.errors.push(String(e.message || e)));
      await page.goto(url + "&open=" + id);
      await page.waitForSelector(".page canvas", { timeout: 60000 });
      await page.waitForFunction(() => reader.doc && reader.loading.hidden, null, { timeout: 30000 });
      await page.evaluate(() => reader.goToPage(2));
      await page.waitForTimeout(1500);
      const n = await places(page);
      await page.close();
      return n;
    };
    out.placesKept = await read();

    const page = await context.newPage();
    page.on("pageerror", (e) => out.errors.push(String(e.message || e)));
    await page.goto(url);
    await page.waitForSelector(".recent-list", { timeout: 30000 });
    await page.waitForFunction(() => !document.querySelector(".recent-list .start-empty")
      || !/Looking/.test(document.querySelector(".recent-list .start-empty").textContent), null, { timeout: 30000 });
    const recent = () => page.evaluate(() => ({ rows: document.querySelectorAll(".recent-list [data-path], .recent-list .recent-row, .recent-list button").length,
      text: document.querySelector(".recent-list").textContent }));
    out.recentBefore = await recent();

    await page.click("#settings-btn");
    await page.waitForSelector("#settings-dialog[open]");
    await page.waitForFunction(() => /Kept now/.test(document.querySelector("#set-history").textContent), null, { timeout: 10000 });
    out.before = await page.evaluate(() => ({ remember: document.querySelector("#set-remember").checked,
      history: document.querySelector("#set-history").textContent,
      online: [...document.querySelectorAll("#set-online li")].map((li) => li.textContent),
      page: document.querySelector("#settings-dialog").textContent.replace(/\s+/g, " ") }));
    await page.click(".dlg-online summary");
    await page.locator("#set-clear-history").scrollIntoViewIfNeeded();
    if (shots) await page.screenshot({ path: `${shots}/privacy.png` });

    await page.click("#set-clear-history");
    await page.waitForFunction(() => /Cleared|nothing to clear/.test(document.querySelector("#set-status").textContent), null, { timeout: 10000 });
    out.cleared = await page.evaluate(() => ({ status: document.querySelector("#set-status").textContent,
      history: document.querySelector("#set-history").textContent }));
    out.placesAfterClear = await places(page);

    await page.click("#set-remember");
    await page.waitForFunction(() => /won't keep/.test(document.querySelector("#set-status").textContent), null, { timeout: 10000 });
    out.off = await page.evaluate(() => ({ history: document.querySelector("#set-history").textContent,
      clearDisabled: document.querySelector("#set-clear-history").disabled }));
    await page.click("#set-cancel");
    await page.waitForFunction(() => !/Looking/.test(document.querySelector(".recent-list")?.textContent || ""), null, { timeout: 10000 });
    await page.waitForTimeout(500);
    out.recentAfter = await recent();
    await page.close();

    out.placesWhenOff = await read();           // read again: no place kept, no Recent row
    const again = await context.newPage();
    await again.goto(url);
    await again.waitForSelector(".recent-list", { timeout: 30000 });
    await again.waitForFunction(() => !/Looking/.test(document.querySelector(".recent-list").textContent), null, { timeout: 30000 });
    out.recentWhenOff = await again.evaluate(() => document.querySelector(".recent-list").textContent);
    await again.close();
  } catch (e) {
    out.errors.push(String(e.stack || e));
  } finally {
    await browser.close();
  }
  console.log(JSON.stringify(out));
})();
