// 0.29.0, from the Windows check, in a browser. One scenario per run:
//   note    a note added, Ctrl+Z (gone, no dot), Ctrl+Y (back, the dot, Redo
//           off again), Ctrl+S saves it.
//   select  with Select on (Esc after Draw): Ctrl+Z / Ctrl+Y undo and redo a
//           drawing, and a note (the reader's own page changes), as the
//           buttons do; not while typing in a box.
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
        editors: [...(reader.uiManager?.getEditors(0) || [])].filter((e) => !e.isEmpty() && !e.div?.hidden).length,
        pdfjsMode: reader.pdfViewer.annotationEditorMode, depth: [tab.undoDepth, tab.redoDepth],
        focus: document.activeElement?.className || document.activeElement?.tagName || "",
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

    if (cfg.scenario === "select") {
      const key = async (k) => { await page.keyboard.press(k); await page.waitForTimeout(600); await settled(); await page.waitForTimeout(300); };
      await open(cfg.file);
      await page.evaluate(() => reader.openAnnotate());
      await page.waitForTimeout(400);
      await page.evaluate(() => reader.useTool("INK"));
      await page.waitForTimeout(400);
      const [x, y] = await pageBox();
      await page.mouse.move(x + 150, y + 150);
      await page.mouse.down();
      await page.mouse.move(x + 300, y + 220, { steps: 8 });
      await page.mouse.up();
      await page.waitForTimeout(500);
      await page.keyboard.press("Escape");  // (Select)
      await page.waitForFunction(() => reader.editMode === "NONE" && reader.pdfViewer.annotationEditorMode === 0, null, { timeout: 5000 });
      await page.waitForTimeout(500);
      out.drawn = await state();
      await key("Control+z");
      out.inkUndone = await state();
      await key("Control+y");
      out.inkRedone = await state();
      // Typing in a box: its own Ctrl+Z, not the document's.
      await page.evaluate(() => reader.openFind());
      await page.waitForTimeout(300);
      await page.keyboard.type("report");
      await page.keyboard.press("Control+z");
      await page.waitForTimeout(500);
      out.typing = await state();
      await page.keyboard.press("Escape");
      await page.waitForTimeout(300);
      // A note (a page change), then Select: Ctrl+Z takes the note back, Ctrl+Y puts it back.
      await page.evaluate(() => reader.useTool("NOTE"));
      await page.waitForTimeout(300);
      await page.mouse.click(x + 300, y + 400);
      await page.waitForFunction(() => document.activeElement && document.activeElement.closest(".rd-notebox"), null, { timeout: 10000 });
      await page.keyboard.type("Look");
      await page.click(".rd-notebox button:has-text('Add note')");
      await page.waitForFunction(() => reader.activeTab.undoDepth === 1, null, { timeout: 30000 });
      await settled();
      await page.waitForTimeout(500);
      await page.keyboard.press("Escape");
      await page.waitForFunction(() => reader.editMode === "NONE", null, { timeout: 5000 });
      await page.waitForTimeout(300);
      out.noted = await state();
      await page.keyboard.press("Control+z");
      await page.waitForFunction(() => reader.activeTab.redoDepth === 1, null, { timeout: 30000 }).catch(() => {});
      await settled();
      await page.waitForTimeout(500);
      out.noteUndone = await state();
      await page.keyboard.press("Control+y");
      await page.waitForFunction(() => reader.activeTab.undoDepth === 1, null, { timeout: 30000 }).catch(() => {});
      await settled();
      await page.waitForTimeout(500);
      out.noteRedone = await state();
    }
    await ctx.close();
  } catch (e) {
    out.errors.push("script: " + (e.stack || e));
  } finally {
    await browser.close();
  }
  console.log(JSON.stringify(out));
})();
