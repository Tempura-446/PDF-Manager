/* Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE. */
// The app page in a browser: the rail, the flyout, the palette, the start
// screen and the layout at two widths. Run by test_startscreen.py:
//   node startscreen.js <page URL with #token=...>
// Prints one JSON object with what it found.
const { chromium } = require("playwright");

(async () => {
  const [url] = process.argv.slice(2);
  const out = { requests: [], errors: [] };
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
    page.on("request", (r) => out.requests.push(r.url()));
    page.on("pageerror", (e) => out.errors.push(String(e.message || e)));
    await page.goto(url);
    await page.waitForSelector(".recent-row", { timeout: 30000 });
    await page.evaluate(() => document.fonts.ready);

    // The start screen.
    out.startLine = await page.$eval("#home h1, .start h1, h1.sr-only", (h) => ({ text: h.textContent, shown: h.offsetWidth > 1 })).catch(() => null);
    out.recent = await page.$$eval(".recent-row", (rows) => rows.map((r) => ({
      name: r.querySelector(".recent-name")?.textContent, facts: r.querySelector(".recent-facts")?.textContent })));
    out.multi = await page.$$eval(".multi button", (bs) => bs.map((b) => b.dataset.tool || b.textContent.trim()));
    out.findLabel = await page.textContent("#find-label");
    out.fonts = await page.evaluate(() => ["Instrument Sans", "Source Serif 4", "JetBrains Mono"].map((f) => document.fonts.check(`16px "${f}"`)));
    out.scrollWidth1280 = await page.evaluate(() => [document.documentElement.scrollWidth, innerWidth]);

    // The rail: five groups, and every tool as a real button[data-op].
    out.groups = await page.$$eval(".rail-btn[data-group]", (bs) => bs.map((b) => b.textContent.trim()));
    out.toolButtons = await page.$$eval(".tools button[data-op]", (bs) => bs.map((b) => b.dataset.op));
    out.flyout = {};
    for (const group of ["pages", "improve", "convert", "protect", "all"]) {
      await page.click(`.rail-btn[data-group="${group}"]`);
      await page.waitForSelector("#flyout:not([hidden])");
      out.flyout[group] = await page.$$eval("#flyout .fly-tool", (bs) => bs.map((b) => b.dataset.op));
      out.flyout[group + "Focus"] = await page.evaluate(() => document.activeElement?.classList.contains("fly-tool"));
      await page.keyboard.press("Escape");
      out.flyout[group + "Closed"] = await page.isHidden("#flyout");
    }
    // A click outside closes it; choosing a tool opens the tool.
    await page.click('.rail-btn[data-group="pages"]');
    await page.mouse.click(900, 600);
    out.closedByOutsideClick = await page.isHidden("#flyout");
    await page.click('.rail-btn[data-group="pages"]');
    await page.click('#flyout .fly-tool[data-op="split"]');
    out.fromFlyout = [await page.textContent("#title"), await page.isHidden("#flyout")];

    // The end-to-end test's way in: a tool from .tools without opening anything.
    await page.dispatchEvent('.tools button[data-op="rotate"]', 'click');
    out.rotateTitle = await page.textContent("#title");
    out.rotateCurrent = await page.getAttribute('.rail-btn[data-group="pages"]', "aria-current");

    // "/" and Ctrl+K open the palette.
    await page.locator("body").click({ position: { x: 900, y: 790 } }).catch(() => {});
    await page.evaluate(() => document.activeElement && document.activeElement.blur());
    await page.keyboard.press("/");
    out.slashOpens = await page.evaluate(() => document.getElementById("palette").open);
    await page.keyboard.press("Escape");
    out.escapeCloses = await page.evaluate(() => !document.getElementById("palette").open);
    await page.keyboard.press("Control+k");
    out.ctrlKOpens = await page.evaluate(() => document.getElementById("palette").open);
    out.paletteBlur = await page.evaluate(() => getComputedStyle(document.getElementById("palette")).backdropFilter);
    await page.keyboard.press("Escape");
    await page.click("#find");
    out.findOpens = await page.evaluate(() => document.getElementById("palette").open);
    await page.keyboard.press("Escape");

    // The reader: the rail stays.
    await page.dispatchEvent('.tools button[data-op="reader"]', 'click');
    await page.waitForSelector("#reader-host .rd");
    out.railInReader = await page.isVisible(".rail");

    // A narrow window: nothing scrolls sideways.
    await page.setViewportSize({ width: 420, height: 860 });
    await page.click("#brand");
    await page.waitForSelector(".recent-row");
    out.scrollWidth420 = await page.evaluate(() => [document.documentElement.scrollWidth, innerWidth]);
    await page.dispatchEvent('.tools button[data-op="compress"]', 'click');
    out.scrollWidth420Tool = await page.evaluate(() => [document.documentElement.scrollWidth, innerWidth]);
  } finally {
    await browser.close();
  }
  console.log(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(1); });
