// 0.29.0 in a browser: the parts of the reader that now live in modules of
// their own still work as before: measuring (measuring.js: the scale bar, a
// length, an area, the list, Undo), find (find.js), the text editor box
// (textedit-ui.js: click a line with Edit text on, change it, Undo).
// Run by test_tabstate.py:
//   node readerparts.js <page URL with #token=...> <JSON with ids>
// Prints one JSON object with what it found.
const { chromium } = require("playwright");

(async () => {
  const [url, extraJson] = process.argv.slice(2);
  const cfg = JSON.parse(extraJson);
  const out = { errors: [], native: [] };
  const browser = await chromium.launch();
  try {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
    const page = await ctx.newPage();
    page.on("pageerror", (e) => out.errors.push(String(e.message || e)));
    page.on("console", (m) => { if (m.type() === "error" && !/Failed to load resource/.test(m.text())) out.errors.push(m.text()); });
    page.on("dialog", (d) => { out.native.push(d.type() + ": " + d.message()); d.dismiss().catch(() => {}); });
    const settled = () => page.waitForFunction(() => reader.doc && reader.loading.hidden && document.querySelector(".page canvas"), null, { timeout: 30000 });
    const corner = () => page.evaluate(() => { const r = reader.pdfViewer.getPageView(0).div.getBoundingClientRect(); return [r.left, r.top]; });

    await page.goto(url + "&open=" + cfg.a);
    await page.waitForSelector(".page canvas", { timeout: 60000 });
    await settled();
    await page.waitForTimeout(800);

    // ---- measuring: a length (two clicks), an area (three corners and Enter), the list, Undo.
    await page.evaluate(() => reader.measureTool("distance"));
    await page.waitForTimeout(500);
    let [x, y] = await corner();
    await page.mouse.click(x + 100, y + 400);
    await page.waitForTimeout(150);
    await page.mouse.click(x + 400, y + 400);
    await page.waitForTimeout(400);
    await page.evaluate(() => reader.measureTool("area"));
    await page.waitForTimeout(300);
    for (const [dx, dy] of [[100, 450], [300, 450], [300, 600]]) { await page.mouse.click(x + dx, y + dy); await page.waitForTimeout(150); }
    await page.keyboard.press("Enter");
    await page.waitForTimeout(400);
    out.measured = await page.evaluate(() => ({
      bar: !reader.scalebar.hidden, mode: reader.measureMode,
      kinds: (reader.activeTab.measures || []).map((m) => m.kind),
      drawn: document.querySelectorAll(".page[data-page-number='1'] .rd-measure-layer *, .page[data-page-number='1'] svg.rd-measures *").length > 0,
    }));
    await page.evaluate(() => reader.measureTool(null));
    await page.waitForTimeout(200);
    await page.focus(".rd-container");
    await page.keyboard.press("Control+z");
    await page.waitForTimeout(300);
    out.measureUndone = await page.evaluate(() => (reader.activeTab.measures || []).map((m) => m.kind));
    await page.keyboard.press("Control+y");
    await page.waitForTimeout(300);
    out.measureRedone = await page.evaluate(() => (reader.activeTab.measures || []).map((m) => m.kind));
    await page.evaluate(() => reader.toggleScaleBar(false));
    await page.waitForTimeout(200);

    // ---- find: Ctrl+F, words on every page, the count; Escape closes it.
    await page.focus(".rd-container");
    await page.keyboard.press("Control+f");
    await page.waitForTimeout(200);
    await page.keyboard.type("Quarterly");
    await page.waitForFunction(() => /3/.test(document.querySelector(".rd-findcount, .rd-findbar [aria-live]")?.textContent || ""), null, { timeout: 10000 }).catch(() => {});
    out.find = await page.evaluate(() => ({ open: !document.querySelector(".rd-findbar:not(.rd-annotbar):not(.rd-sigbar):not(.rd-formbar)").hidden,
      count: (document.querySelector(".rd-findcount, .rd-findbar [aria-live]")?.textContent || "").trim(),
      marked: document.querySelectorAll(".textLayer .highlight").length > 0 }));
    await page.keyboard.press("Escape");
    await page.waitForTimeout(200);
    out.findClosed = await page.evaluate(() => document.querySelector(".rd-findbar:not(.rd-annotbar):not(.rd-sigbar):not(.rd-formbar)").hidden);

    // ---- the text editor box: Edit text, click the first line, change a word, Enter; Undo.
    await page.evaluate(() => reader.useTool("EDITTEXT"));
    await page.waitForTimeout(400);
    const span = await page.evaluate(() => {
      const s = [...document.querySelectorAll(".page[data-page-number='1'] .textLayer span")].find((x) => /Quarterly/.test(x.textContent));
      s.scrollIntoView({ block: "center" });
      const r = s.getBoundingClientRect();
      return [r.left + 10, r.top + r.height / 2];
    });
    await page.waitForTimeout(300);
    const again = await page.evaluate(() => {
      const r = [...document.querySelectorAll(".page[data-page-number='1'] .textLayer span")].find((x) => /Quarterly/.test(x.textContent)).getBoundingClientRect();
      return [r.left + 10, r.top + r.height / 2];
    });
    await page.mouse.click(again[0], again[1]);
    await page.waitForSelector(".rd-textedit-box", { timeout: 10000 });
    out.editBox = await page.evaluate(() => { const f = document.querySelector(".rd-textedit-box input, .rd-textedit-box textarea, .rd-textedit-box [contenteditable]");
      return { value: f ? (f.value ?? f.textContent) : null }; });
    await page.evaluate(() => {
      const f = document.querySelector(".rd-textedit-box input, .rd-textedit-box textarea");
      f.focus(); f.select();
    });
    await page.keyboard.type("Annual report, page 1");
    await page.keyboard.press("Enter");
    await page.waitForFunction(() => reader.activeTab.dirty && reader.loading.hidden && reader.doc, null, { timeout: 30000 });
    await settled();
    await page.waitForTimeout(800);
    const pageText = () => page.evaluate(async () => (await (await reader.doc.getPage(1)).getTextContent()).items.map((i) => i.str).join(" "));
    out.edited = { dirty: await page.evaluate(() => reader.activeTab.dirty), text: await pageText(), box: await page.evaluate(() => !!document.querySelector(".rd-textedit-box")) };
    await page.focus(".rd-container");
    await page.keyboard.press("Control+z");
    await page.waitForFunction(() => !reader.activeTab.dirty && reader.loading.hidden && reader.doc, null, { timeout: 30000 }).catch(() => {});
    await settled();
    await page.waitForTimeout(500);
    out.editUndone = { dirty: await page.evaluate(() => reader.activeTab.dirty), text: await pageText() };
  } catch (e) {
    out.errors.push("script: " + (e.stack || e));
  } finally {
    await browser.close();
  }
  console.log(JSON.stringify(out));
})();
