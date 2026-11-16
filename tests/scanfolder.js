/* Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE. */
// 0.45.0 in a browser: "My scanner isn't listed" on the Scan page. A folder
// the scanner saves to is watched from here; a missing folder is refused in
// plain words; the watched folder is listed with what it has done.
// Run by test_scan_folder_ui.py:
//   node scanfolder.js <page URL with #token=...> <the folder> <a file to wait for> [shots folder]
// Prints one JSON object with what it found.
const { chromium } = require("playwright");
const fs = require("fs");

(async () => {
  const [url, folder, result, shots] = process.argv.slice(2);
  const out = { errors: [] };
  const browser = await chromium.launch();
  try {
    const page = await (await browser.newContext({ viewport: { width: 1280, height: 1000 } })).newPage();
    page.on("pageerror", (e) => out.errors.push(String(e.message || e)));
    await page.goto(url);
    await page.waitForSelector("#home-host .start-drop", { timeout: 30000 });
    await page.click("#home-host button[data-tool='scan']");
    await page.waitForSelector("#scan-host .scan-setup", { timeout: 10000 });
    await page.waitForFunction(() => scanState.status, null, { timeout: 20000 });
    await page.check("#scan-source-scanner");
    out.before = await page.evaluate(() => ({ guideHidden: document.getElementById("scan-folder-guide").hidden,
      expanded: document.getElementById("scan-not-listed").getAttribute("aria-expanded") }));
    await page.click("#scan-not-listed");
    await page.waitForSelector("#scan-folder-guide:not([hidden])");
    out.open = await page.evaluate(() => ({ expanded: document.getElementById("scan-not-listed").getAttribute("aria-expanded"),
      choices: [...document.getElementById("scan-folder-do").options].map((o) => o.textContent),
      batch: document.getElementById("scan-folder-batch").checked, focused: document.activeElement.id }));
    const said = () => page.textContent("#scan-folder-said");

    await page.click("#scan-folder-add");                 // nothing typed
    out.empty = await said();
    await page.fill("#scan-folder", folder + "-missing");
    await page.click("#scan-folder-add");
    await page.waitForFunction(() => /no folder/.test(document.getElementById("scan-folder-said").textContent), null, { timeout: 10000 });
    out.missing = await said();

    await page.fill("#scan-folder", folder);
    await page.click("#scan-folder-add");
    await page.waitForFunction(() => /now watches/.test(document.getElementById("scan-folder-said").textContent), null, { timeout: 10000 });
    out.added = { said: await said(), listed: await page.$$eval("#scan-guide-list li", (l) => l.map((li) => li.textContent)) };
    if (shots) await page.screenshot({ path: `${shots}/scan-folder-guide.png` });

    // The scan the test put in the folder becomes a PDF in its Done folder.
    for (let tries = 0; !fs.existsSync(result); tries++) {
      if (tries > 150) throw new Error("the scan was never done");
      await page.waitForTimeout(200);
    }
    out.done = true;
    await page.click("#scan-not-listed");                 // close, open again: the folder shows what it did
    await page.click("#scan-not-listed");
    await page.waitForFunction(() => /1 done/.test(document.getElementById("scan-guide-list").textContent), null, { timeout: 15000 });
    out.after = await page.$$eval("#scan-guide-list li", (l) => l.map((li) => li.textContent));
  } catch (e) {
    out.errors.push(String(e.stack || e));
  } finally {
    await browser.close();
  }
  console.log(JSON.stringify(out));
})();
