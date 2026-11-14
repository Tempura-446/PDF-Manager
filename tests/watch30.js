// 0.30.0 in a browser: Settings > Watch folders. Add a folder (a missing one
// first, which is refused plainly), drop a PDF in it, see the toast and the
// folder's status, pause it and remove it.
// Run by test_watchfolders.py:
//   node watch30.js <page URL with #token=...> <JSON: folder, drop, missing>
// Prints one JSON object with what it found.
const fs = require("fs");
const path = require("path");
const { chromium } = require("playwright");

(async () => {
  const [url, extraJson] = process.argv.slice(2);
  const cfg = JSON.parse(extraJson);
  const out = { errors: [], native: [] };
  const browser = await chromium.launch();
  try {
    const page = await (await browser.newContext({ viewport: { width: 1280, height: 900 } })).newPage();
    page.on("pageerror", (e) => out.errors.push(String(e.message || e)));
    page.on("console", (m) => { if (m.type() === "error" && !/Failed to load resource|status of 400/.test(m.text())) out.errors.push(m.text()); });
    page.on("dialog", (d) => { out.native.push(d.type() + ": " + d.message()); d.dismiss().catch(() => {}); });
    await page.goto(url);
    await page.waitForSelector("#settings-btn", { timeout: 30000 });
    await page.click("#settings-btn");
    await page.waitForSelector("#settings-dialog[open]", { timeout: 10000 });
    await page.waitForFunction(() => document.querySelectorAll("#watch-do option").length > 0, null, { timeout: 10000 });
    out.choices = await page.$$eval("#watch-do option", (os) => os.map((o) => o.value));
    out.shellMenuHidden = await page.evaluate(() => document.getElementById("shell-menu").hidden);

    // A folder that isn't there: said plainly, nothing added.
    await page.fill("#watch-folder", cfg.missing);
    await page.click("#watch-add-btn");
    await page.waitForFunction(() => document.getElementById("watch-status").classList.contains("err"), null, { timeout: 10000 });
    out.badFolder = await page.textContent("#watch-status");

    // The real one, with Make smaller.
    await page.fill("#watch-folder", cfg.folder);
    await page.selectOption("#watch-do", "tool:compress");
    await page.press("#watch-folder", "Enter");  // Enter adds (doesn't save and close Settings)
    await page.waitForSelector(".watch-item", { timeout: 10000 });
    out.added = await page.evaluate(() => ({
      count: document.querySelectorAll(".watch-item").length, open: document.getElementById("settings-dialog").open,
      folder: document.querySelector(".watch-item .wi-folder").textContent,
      what: document.querySelector(".watch-item .wi-what").textContent,
      status: document.querySelector(".watch-item .wi-state").textContent }));

    // A PDF put in the folder: done, with a note.
    fs.copyFileSync(cfg.drop, path.join(cfg.folder, "drop.pdf"));
    await page.waitForFunction(() => [...document.querySelectorAll(".toast, .rd-toast")].some((t) => /drop\.pdf/.test(t.textContent)), null, { timeout: 60000 });
    out.toast = await page.evaluate(() => [...document.querySelectorAll(".toast, .rd-toast")].map((t) => t.textContent).join(" | "));
    await page.waitForFunction(() => /1 done/.test(document.querySelector(".watch-item .wi-state")?.textContent || ""), null, { timeout: 30000 });
    out.statusAfter = await page.textContent(".watch-item .wi-state");

    // Pause, then remove.
    await page.click(".watch-item .wi-buttons button:has-text('Pause')");
    await page.waitForFunction(() => /Resume/.test(document.querySelector(".watch-item .wi-buttons button")?.textContent || ""), null, { timeout: 10000 });
    out.paused = await page.evaluate(() => ({ button: document.querySelector(".watch-item .wi-buttons button").textContent,
      status: document.querySelector(".watch-item .wi-state").textContent.split(" · ")[0] }));
    await page.click(".watch-item .wi-buttons button:has-text('Remove')");
    await page.waitForFunction(() => !document.querySelector(".watch-item"), null, { timeout: 10000 });
    out.removed = await page.evaluate(() => document.querySelectorAll(".watch-item").length);
  } catch (e) {
    out.errors.push("script: " + (e.stack || e));
  } finally {
    await browser.close();
  }
  process.stdout.write(JSON.stringify(out));
})();
