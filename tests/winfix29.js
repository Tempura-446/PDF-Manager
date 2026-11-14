// 0.29.0, from the Windows check, in a browser. One scenario per run:
//   note    a note added, Ctrl+Z (gone, no dot), Ctrl+Y (back, the dot, Redo
//           off again), Ctrl+S saves it.
// Run by test_winfix29.py:
//   node winfix29.js <page URL with #token=...> <JSON: { scenario, ids... }>
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
    const settled = () => page.waitForFunction(() => reader.doc && reader.loading.hidden
      && document.querySelector(".page canvas"), null, { timeout: 30000 });
    // What the screen says about the shown document.
    const state = () => page.evaluate(async () => {
      const annots = await (await reader.doc.getPage(1)).getAnnotations({ intent: "display" });
      const tab = reader.activeTab;
      const dotEl = reader.tabEls?.get(tab.id)?.dot;
      return {
        notes: annots.filter((a) => a.subtype === "Text").length,
        inks: annots.filter((a) => a.subtype === "Ink").length,
        dirty: !!tab.dirty, titleDot: !reader.titleDot.hidden, tabDot: dotEl ? !dotEl.hidden : null,
        undo: !reader.undoBtn.disabled, redo: !reader.redoBtn.disabled, mode: reader.editMode,
        annotSaveMain: reader.annotSaveBtn.classList.contains("primary"),
        stored: reader.doc.annotationStorage.size,
      };
    });
    const open = async (id) => {
      await page.goto(url + "&open=" + id);
      await page.waitForSelector(".page canvas", { timeout: 60000 });
      await settled();
      await page.waitForTimeout(500);
    };
    const pageBox = () => page.evaluate(() => { const r = reader.pdfViewer.getPageView(0).div.getBoundingClientRect(); return [r.left, r.top]; });

    if (cfg.scenario === "note") {
      await open(cfg.file);
      await page.evaluate(() => reader.openAnnotate());
      await page.waitForTimeout(400);
      await page.evaluate(() => reader.useTool("NOTE"));
      await page.waitForTimeout(300);
      const [x, y] = await pageBox();
      await page.mouse.click(x + 300, y + 300);
      await page.waitForFunction(() => document.activeElement && document.activeElement.closest(".rd-notebox"), null, { timeout: 10000 });
      await page.keyboard.type("Check this");
      await page.click(".rd-notebox button:has-text('Add note')");
      await page.waitForFunction(() => reader.activeTab.dirty && reader.activeTab.undoDepth === 1, null, { timeout: 30000 });
      await settled();
      await page.waitForTimeout(500);
      out.added = await state();
      await page.keyboard.press("Control+z");
      await page.waitForFunction(() => !reader.activeTab.dirty && reader.activeTab.redoDepth === 1, null, { timeout: 30000 });
      await settled();
      await page.waitForTimeout(500);
      out.undone = await state();
      await page.keyboard.press("Control+y");
      await page.waitForFunction(() => reader.activeTab.dirty && reader.activeTab.redoDepth === 0, null, { timeout: 30000 });
      await settled();
      await page.waitForTimeout(500);
      out.redone = await state();
      await page.keyboard.press("Control+s");
      await page.waitForFunction(() => !reader.activeTab.dirty, null, { timeout: 30000 });
      await page.waitForTimeout(500);
      out.saved = await state();
    }
    await ctx.close();
  } catch (e) {
    out.errors.push("script: " + (e.stack || e));
  } finally {
    await browser.close();
  }
  console.log(JSON.stringify(out));
})();
