/* Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE. */
// The owner's Windows check of 0.24.1, in a browser: a signature put on its
// line stays drawn there while another is selected and after a zoom or a
// turn; a signature and a drawing not saved yet can still be picked, moved
// and deleted after switching tabs and back, and Save writes each once;
// typing straight after the Sign dialog opens keeps every letter.
// Run by test_fixes242.py:
//   node fix242.js <page URL with #token=...> <JSON with ids and paths>
// Prints one JSON object with what it found.
const { chromium } = require("playwright");

(async () => {
  const [url, extraJson] = process.argv.slice(2);
  const cfg = JSON.parse(extraJson);
  const out = { errors: [], native: [] };
  const browser = await chromium.launch();
  const watch = (page) => {
    page.on("pageerror", (e) => out.errors.push(String(e.message || e)));
    page.on("console", (m) => { if (m.type() === "error" && !/Failed to load resource/.test(m.text())) out.errors.push(m.text()); });
    page.on("dialog", (d) => { out.native.push(d.type() + ": " + d.message()); d.dismiss().catch(() => {}); });
  };
  const shots = cfg.shots || null;
  const shot = async (page, name) => { if (shots) { await page.waitForTimeout(400); await page.screenshot({ path: `${shots}/${name}.png` }); } };
  const opened = (page) => page.waitForSelector(".page canvas", { timeout: 60000 });
  const typeSignature = async (page, name) => {
    await page.waitForSelector("dialog.rd-dialog[open] .rd-sigtext", { timeout: 10000 });
    await page.fill("dialog.rd-dialog[open] .rd-sigtext", name);
    await page.click("dialog.rd-dialog[open] button:has-text('Place signature')");
  };
  // Each editor on page 1: its rectangle in PDF points, and how far the middle of
  // its box is drawn from the middle of that rectangle on the screen (through the
  // page's viewport, so at any zoom and turn), in CSS pixels. (Its edges are a
  // border's width out: pdf.js draws the box around the picture.)
  const editors = (page) => page.evaluate(() => [...reader.uiManager.getEditors(0)].map((e) => {
    const view = reader.pdfViewer.getPageView(0), p = view.div.getBoundingClientRect();
    const drawn = e.div.getBoundingClientRect();
    const r = e.getRect(0, 0);
    const [ax, ay] = view.viewport.convertToViewportPoint(r[0], r[1]), [bx, by] = view.viewport.convertToViewportPoint(r[2], r[3]);
    const x0 = p.left + view.div.clientLeft, y0 = p.top + view.div.clientTop;
    const off = Math.hypot((drawn.left + drawn.right) / 2 - (x0 + (ax + bx) / 2), (drawn.top + drawn.bottom) / 2 - (y0 + (ay + by) / 2));
    return { type: e.editorType, rect: r.map((v) => Math.round(v * 10) / 10), off: Math.round(off * 10) / 10, selected: e.isSelected,
      id: e.annotationElementId, hidden: !!e.div.hidden };
  }));
  const saveState = (page) => page.evaluate(() => ({ dirty: !!reader.activeTab.dirty, dot: !reader.titleDot.hidden,
    tabDot: !!document.querySelector(".rd-doctab.active .rd-dot:not([hidden])") }));
  const showTab = async (page, n) => {
    await page.click(`.rd-doctab:nth-child(${n}) .rd-doctab-name`);
    await page.waitForFunction((i) => reader.doc && reader.activeTab === reader.tabs[i] && reader.loading.hidden && document.querySelector(".page canvas"), n - 1, { timeout: 30000 });
    await page.waitForTimeout(1200);
  };
  const middle = (page, type) => page.evaluate((type) => {
    const e = [...reader.uiManager.getEditors(0)].find((x) => x.editorType === type);
    const r = e.div.getBoundingClientRect();
    return [r.left + r.width / 2, r.top + r.height / 2];
  }, type);

  try {
    // ---- A. Sign it on the line, then Sign again: the first stays drawn on its line ------------
    for (const [w, h, dpr] of [[1280, 800, 1], [1280, 720, 1.5]]) {
      const ctx = await browser.newContext({ viewport: { width: w, height: h }, deviceScaleFactor: dpr });
      const page = await ctx.newPage();
      watch(page);
      await page.goto(url + "&open=" + cfg.invoiceIds[dpr === 1 ? 0 : 1]);
      await opened(page);
      await page.waitForSelector(".rd-side .fp-facts:not([aria-busy])", { state: "attached", timeout: 30000 });
      await page.waitForSelector('.rd-side .fp-card-head[data-op="sign"]', { timeout: 30000 });
      const a = (out[`lines-${dpr}`] = {});
      a.line = await page.evaluate(() => fp.facts.suggestions.find((x) => x.op === "sign").line);
      await page.click('.rd-side .fp-card-head[data-op="sign"]');
      await typeSignature(page, "Jane Doe");
      await page.waitForFunction(() => [...reader.uiManager.getEditors(0)].some((e) => e.width > 0 && e.div && !e.div.hidden), null, { timeout: 30000 })
        .catch(async (e) => { out.diag = await page.evaluate(() => ({ toast: document.querySelector(".rd-toast")?.textContent, mode: reader.uiManager?.getMode(),
          viewer: reader.pdfViewer.annotationEditorMode, eds: [...reader.uiManager.getEditors(0)].map((x) => [x.width, !!x.div, x.div?.hidden]) })); throw e; });
      await page.waitForTimeout(600);
      a.first = await editors(page);
      // Sign again (the annotate bar's Sign): the new one selected, the first not.
      await page.click(".rd-annotbar button:has-text('Sign')");
      await typeSignature(page, "Second Signer");
      await page.waitForFunction(() => [...reader.uiManager.getEditors(0)].filter((e) => e.width > 0 && !e.div.hidden).length === 2, null, { timeout: 30000 });
      await page.waitForTimeout(800);
      a.second = await editors(page);
      // The selected signature's handles: its four corners only (sides would squash it).
      a.handles = await page.evaluate(() => [...document.querySelectorAll(".annotationEditorLayer .selectedEditor .resizer")]
        .filter((h) => h.getBoundingClientRect().width > 0).map((h) => h.className.replace("resizer", "").trim()).sort());
      if (dpr === 1) await shot(page, "A-two-signatures-second-selected");
      // Zoom in and out, and a turn of the view: pdf.js lays the editors out again each time.
      for (const [name, act] of [["zoom150", () => { reader.pdfViewer.currentScaleValue = 1.5; }],
        ["zoom75", () => { reader.pdfViewer.currentScaleValue = 0.75; }], ["turned", () => reader.rotate(90)]]) {
        await page.evaluate(act);
        await page.waitForTimeout(900);
        a[name] = await editors(page);
      }
      if (dpr === 1) await shot(page, "A-turned");
      await ctx.close();
    }

    // ---- B. Not saved yet, then another tab and back: still editors --------------------------
    {
      const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
      const page = await ctx.newPage();
      watch(page);
      await page.goto(url + "&open=" + cfg.tabA);
      await opened(page);
      await page.evaluate((id) => reader.openById(id), cfg.tabB);
      await page.waitForFunction(() => reader.tabs.length === 2 && reader.activeTab === reader.tabs[1] && document.querySelector(".page canvas"), null, { timeout: 30000 });
      await showTab(page, 1);
      const b = (out.tabs = {});
      // A signature (in the middle of the view), and a stroke drawn with Draw.
      await page.evaluate(() => { reader.signBtn.click(); });
      await typeSignature(page, "Jane Doe");
      await page.waitForFunction(() => [...reader.uiManager.getEditors(0)].some((e) => e.width > 0 && !e.div.hidden), null, { timeout: 30000 });
      await page.waitForTimeout(500);
      await page.evaluate(() => reader.useTool("INK"));
      await page.waitForTimeout(400);
      const pg = await page.evaluate(() => { const r = reader.pdfViewer.getPageView(0).div.getBoundingClientRect(); return [r.left, r.top]; });
      await page.mouse.move(pg[0] + 120, pg[1] + 120);
      await page.mouse.down();
      await page.mouse.move(pg[0] + 260, pg[1] + 190, { steps: 10 });
      await page.mouse.up();
      await page.waitForTimeout(500);
      // (Another tool and back to Draw: the stroke is an editor of its own; Draw stays on.)
      await page.evaluate(() => reader.useTool("FREETEXT"));
      await page.waitForTimeout(300);
      await page.evaluate(() => reader.useTool("INK"));
      await page.waitForTimeout(500);
      b.before = { editors: await editors(page), state: await saveState(page), mode: await page.evaluate(() => reader.editMode) };
      await showTab(page, 2);
      await showTab(page, 1);
      // (The tool comes back once the page is drawn for editing: pdf.js then makes the editors.)
      await page.waitForFunction(() => [...(reader.uiManager?.getEditors(0) || [])].filter((e) => e.div && !e.div.hidden).length >= 2, null, { timeout: 20000 }).catch(() => {});
      await page.waitForTimeout(300);
      b.back = { editors: await editors(page), state: await saveState(page), mode: await page.evaluate(() => reader.editMode),
        annot: await page.evaluate(() => !reader.annotbar.hidden),
        modes: await page.evaluate(() => [reader.uiManager?.getMode(), reader.pdfViewer.annotationEditorMode]) };
      // Picked with a click, dragged, then the drawing picked and deleted.
      const at = await middle(page, "stamp");
      await page.mouse.click(...at);
      await page.waitForTimeout(400);
      b.picked = (await editors(page)).find((e) => e.type === "stamp");
      await page.mouse.move(...at);
      await page.mouse.down();
      await page.mouse.move(at[0] + 60, at[1] + 40, { steps: 8 });
      await page.mouse.up();
      await page.waitForTimeout(500);
      b.moved = (await editors(page)).find((e) => e.type === "stamp");
      const ink = await middle(page, "ink");
      await page.mouse.click(...ink);
      await page.waitForTimeout(400);
      b.inkPicked = (await editors(page)).find((e) => e.type === "ink")?.selected;
      await page.keyboard.press("Delete");
      await page.waitForTimeout(500);
      b.afterDelete = { editors: await editors(page), state: await saveState(page) };
      await shot(page, "B-after-tab-switch-moved");
      // Select (no tool): a double-click on the signature picks it too (pdf.js's way).
      await page.evaluate(() => document.querySelector('.rd-annotbar .rd-tool[data-mode="NONE"]').click());
      await page.waitForTimeout(500);
      const at2 = await middle(page, "stamp");
      await page.mouse.dblclick(...at2);
      await page.waitForTimeout(800);
      b.selectTool = (await editors(page)).find((e) => e.type === "stamp")?.selected;
      await page.keyboard.press("Control+s");
      await page.waitForFunction(() => !reader.activeTab.dirty, null, { timeout: 20000 });
      await page.waitForTimeout(500);
      b.saved = await saveState(page);
      await ctx.close();
    }

    // ---- C. Typing straight after the Sign dialog opens -----------------------------------------
    {
      const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
      const page = await ctx.newPage();
      watch(page);
      await page.goto(url + "&open=" + cfg.typeId);
      await opened(page);
      await page.waitForSelector('.rd-side .fp-card-head[data-op="sign"]', { timeout: 30000 });
      const c = (out.typing = []);
      const ways = [
        ["sign it", () => page.click('.rd-side .fp-card-head[data-op="sign"]')],
        ["sign button", () => page.click(".rd-annotbar button:has-text('Sign')")],
        ["S key", async () => { await page.evaluate(() => reader.container.focus()); await page.keyboard.press("s"); }],
      ];
      for (const [how, open] of ways) {
        await open();
        await page.keyboard.type("Test Signer");  // no waiting for the dialog
        await page.waitForSelector("dialog.rd-dialog[open] .rd-sigtext", { timeout: 10000 });
        c.push(await page.evaluate((how) => { const box = document.querySelector("dialog.rd-dialog[open] .rd-sigtext");
          return { how, value: box.value, focused: document.activeElement === box, autocomplete: box.getAttribute("autocomplete") }; }, how));
        await page.click("dialog.rd-dialog[open] button:has-text('Place signature')");
        await page.waitForTimeout(1200);
      }
      await ctx.close();
    }
  } catch (e) {
    out.errors.push("script: " + (e.stack || e));
  } finally {
    await browser.close();
  }
  console.log(JSON.stringify(out));
})();
