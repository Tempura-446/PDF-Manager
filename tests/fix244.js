// 0.24.4 in a browser: with Select (no tool) on, a single click picks a
// signature or a drawing, both one placed in this session and one already in
// the file, as after switching tabs and back; it can then be dragged and
// deleted, and Save writes each once. Links, form fields and selecting text
// still work with Select on.
// Run by test_fixes244.py:
//   node fix244.js <page URL with #token=...> <JSON with ids>
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
    page.on("popup", (p) => p.close().catch(() => {}));
    const showTab = async (n) => {
      await page.click(`.rd-doctab:nth-child(${n}) .rd-doctab-name`);
      await page.waitForFunction((i) => reader.doc && reader.activeTab === reader.tabs[i] && reader.loading.hidden && document.querySelector(".page canvas"), n - 1, { timeout: 30000 });
      await page.waitForTimeout(1200);
    };
    const selectTool = async () => {
      await page.click('.rd-annotbar .rd-tool[data-mode="NONE"]');
      await page.waitForFunction(() => reader.editMode === "NONE" && reader.pdfViewer.annotationEditorMode === 0, null, { timeout: 5000 })
        .catch(() => {});
      await page.waitForTimeout(500);
    };
    // Each editor on page 1, and the annotations drawn from the file there.
    const state = () => page.evaluate(() => ({
      mode: reader.editMode, annot: !reader.annotbar.hidden,
      editors: [...(reader.uiManager?.getEditors(0) || [])].filter((e) => !e.isEmpty()).map((e) => ({
        type: e.editorType, selected: e.isSelected, hidden: !!e.div?.hidden, id: e.annotationElementId,
        rect: e.getRect(0, 0).map((v) => Math.round(v * 10) / 10) })),
      shown: [...document.querySelectorAll(".page[data-page-number='1'] .annotationLayer section:not([hidden])")]
        .map((s) => s.className.split(" ")[0]).filter((c) => /^(stamp|ink)Annotation$/.test(c)).sort(),
      dirty: !!reader.activeTab.dirty,
    }));
    // The middle of what ``selector`` finds (the first visible one), scrolled into view.
    const middle = async (selector) => {
      await page.evaluate((s) => [...document.querySelectorAll(s)].find((x) => x.getBoundingClientRect().width)
        .scrollIntoView({ block: "center", inline: "center" }), selector);
      await page.waitForTimeout(400);
      return page.evaluate((s) => {
        const r = [...document.querySelectorAll(s)].find((x) => x.getBoundingClientRect().width).getBoundingClientRect();
        return [r.left + r.width / 2, r.top + r.height / 2];
      }, selector);
    };
    const drag = async ([x, y], dx, dy) => {
      await page.mouse.move(x, y);
      await page.mouse.down();
      await page.mouse.move(x + dx, y + dy, { steps: 8 });
      await page.mouse.up();
      await page.waitForTimeout(500);
    };

    await page.goto(url + "&open=" + cfg.tabA);
    await page.waitForSelector(".page canvas", { timeout: 60000 });
    await page.evaluate((id) => reader.openById(id), cfg.tabB);
    await page.waitForFunction(() => reader.tabs.length === 2 && reader.activeTab === reader.tabs[1] && document.querySelector(".page canvas"), null, { timeout: 30000 });
    await showTab(1);

    // A signature and a stroke drawn with Draw, then Select.
    await page.evaluate(() => { reader.signBtn.click(); });
    await page.waitForSelector("dialog.rd-dialog[open] .rd-sigtext", { timeout: 10000 });
    await page.fill("dialog.rd-dialog[open] .rd-sigtext", "Jane Doe");
    await page.click("dialog.rd-dialog[open] button:has-text('Place signature')");
    await page.waitForFunction(() => [...reader.uiManager.getEditors(0)].some((e) => e.width > 0 && !e.div.hidden), null, { timeout: 30000 });
    await page.waitForTimeout(500);
    await page.evaluate(() => reader.useTool("INK"));
    await page.waitForTimeout(400);
    const pg = await page.evaluate(() => { const r = reader.pdfViewer.getPageView(0).div.getBoundingClientRect(); return [r.left, r.top]; });
    await drag([pg[0] + 120, pg[1] + 120], 140, 70);
    await selectTool();
    out.placed = await state();
    // Placed in this session: a single click picks it.
    await page.mouse.click(...await middle(".annotationEditorLayer .stampEditor"));
    await page.waitForTimeout(600);
    out.pickedHere = await state();
    await selectTool();

    // Another tab and back, Select still on: both are annotations in the tab's bytes now.
    await showTab(2);
    await showTab(1);
    out.back = await state();

    // Links, form fields and selecting text work as before with Select on.
    await page.evaluate(() => {
      window.__link = null;
      document.addEventListener("click", (e) => {
        if (!e.target.closest?.(".linkAnnotation a")) return;
        window.__link = !e.defaultPrevented;
        e.preventDefault();  // (not opened: there's nowhere to go)
      });
    });
    await page.mouse.click(...await middle(".page[data-page-number='1'] .linkAnnotation a"));
    await page.waitForTimeout(300);
    out.link = await page.evaluate(() => ({ followed: window.__link, mode: reader.editMode }));
    await page.mouse.click(...await middle(".page[data-page-number='1'] .textWidgetAnnotation input"));
    await page.keyboard.type("Ann");
    await page.waitForTimeout(300);
    out.field = await page.evaluate(() => ({ value: document.querySelector(".page[data-page-number='1'] .textWidgetAnnotation input").value,
      mode: reader.editMode }));
    {
      const inv = ".page[data-page-number='1'] .textLayer span";
      const r = await page.evaluate((sel) => {
        const s = [...document.querySelectorAll(sel)].find((x) => x.textContent.startsWith("Invoice 2026"));
        s.scrollIntoView({ block: "center" });
        const b = s.getBoundingClientRect();
        return [b.left + 2, b.top + b.height / 2, b.width];
      }, inv);
      await page.waitForTimeout(300);
      await drag([r[0], r[1]], r[2] - 6, 0);
      out.text = { selected: await page.evaluate(() => String(window.getSelection())), mode: await page.evaluate(() => reader.editMode) };
      await page.evaluate(() => window.getSelection().removeAllRanges());
    }

    // A single click on the signature picks it; dragged, it moves.
    const at = await middle(".page[data-page-number='1'] .annotationLayer section.stampAnnotation");
    await page.mouse.click(...at);
    await page.waitForTimeout(800);
    out.pickedBack = await state();
    const now = await middle(".annotationEditorLayer .stampEditor.selectedEditor");
    await drag(now, 60, 40);
    out.moved = await state();

    // Select again: a single click on the drawing picks it; Delete deletes it.
    await page.keyboard.press("Escape");  // (Select, from the keyboard)
    await page.waitForTimeout(800);
    out.selectAgain = await state();
    await page.mouse.click(...await middle(".page[data-page-number='1'] .annotationLayer section.inkAnnotation"));
    await page.waitForTimeout(800);
    out.inkPicked = await state();
    await page.keyboard.press("Delete");
    await page.waitForTimeout(500);
    out.afterDelete = await state();

    await page.keyboard.press("Control+s");
    await page.waitForFunction(() => !reader.activeTab.dirty, null, { timeout: 20000 });
    await page.waitForTimeout(500);
    out.saved = await state();
    await ctx.close();
  } catch (e) {
    out.errors.push("script: " + (e.stack || e));
  } finally {
    await browser.close();
  }
  console.log(JSON.stringify(out));
})();
