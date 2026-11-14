// 0.29.0 in a browser: what the reader shows about saving follows the tab's
// state (static/tabstate.js) through open → annotate → undo → redo → save →
// page edit → undo → switch tab and back → save as → redact and save a copy
// → close: the dots on the tabs and by the title, the Save buttons, the
// window's title, the close prompt and what Ctrl+S does.
// Run by test_tabstate.py:
//   node tabflow.js <page URL with #token=...> <JSON with ids>
// Prints one JSON object with what it found.
const { chromium } = require("playwright");

(async () => {
  const [url, extraJson] = process.argv.slice(2);
  const cfg = JSON.parse(extraJson);
  const out = { errors: [], native: [], steps: {} };
  const browser = await chromium.launch();
  try {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
    // The desktop window's Save dialog, answered at once.
    await ctx.addInitScript(() => {
      window.__saved = [];
      window.pywebview = { api: { save_result: async (id, name) => { window.__saved.push(name); return "/tmp/" + name; } } };
    });
    const page = await ctx.newPage();
    page.on("pageerror", (e) => out.errors.push(String(e.message || e)));
    page.on("console", (m) => { if (m.type() === "error" && !/Failed to load resource/.test(m.text())) out.errors.push(m.text()); });
    page.on("dialog", (d) => { out.native.push(d.type() + ": " + d.message()); d.dismiss().catch(() => {}); });

    const settled = () => page.waitForFunction(() => reader.doc && reader.loading.hidden && document.querySelector(".page canvas"), null, { timeout: 30000 });
    // What the screen says now (read from the page itself), and what the derived state says Ctrl+S and closing do.
    const snap = async (name, extra = {}) => {
      out.steps[name] = { ...await page.evaluate(() => {
        const tab = reader.activeTab;
        const save = document.querySelector(".rd-annotsave");
        return {
          dot: !reader.tabEls.get(tab.id).dot.hidden,
          dots: reader.tabs.map((t) => !reader.tabEls.get(t.id).dot.hidden),
          titleDot: !document.querySelector(".rd-title .rd-dot").hidden,
          saveMain: save.classList.contains("seal") && save.classList.contains("primary"),
          saveTitle: save.title,
          saveNow: !document.querySelector(".rd-savenow").hidden,
          undoNow: !reader.undoNowBtn.hidden,
          title: document.title,
          close: reader.ui.close ? reader.ui.close.text : null,
          ctrlS: reader.ui.ctrlS,
          dirty: tab.dirty, version: tab.version,
        };
      }), ...extra };
    };
    const toast = () => page.evaluate(() => document.querySelector(".rd-toast span")?.textContent || "");
    const ctrl = async (key) => { await page.focus(".rd-container"); await page.keyboard.press("Control+" + key); };
    const draw = async (dy = 0) => {
      await page.evaluate(() => { reader.openAnnotate(); reader.useTool("INK"); });
      await page.waitForTimeout(400);
      const [x, y] = await page.evaluate(() => { const r = reader.pdfViewer.getPageView(0).div.getBoundingClientRect(); return [r.left, r.top]; });
      await page.mouse.move(x + 120, y + 200 + dy);
      await page.mouse.down();
      await page.mouse.move(x + 260, y + 260 + dy, { steps: 8 });
      await page.mouse.up();
      await page.waitForTimeout(300);
      await page.evaluate(() => reader.useTool("NONE"));  // (the stroke is finished)
      await page.evaluate(() => reader.openAnnotate());
      await page.waitForFunction(() => reader.activeTab.dirty, null, { timeout: 10000 });
    };
    const showTab = async (n) => {
      await page.click(`.rd-doctab:nth-child(${n}) .rd-doctab-name`);
      await page.waitForFunction((i) => reader.activeTab === reader.tabs[i], n - 1, { timeout: 30000 });
      await settled();
      await page.waitForTimeout(800);
    };

    await page.goto(url + "&open=" + cfg.a);
    await page.waitForSelector(".page canvas", { timeout: 60000 });
    await page.evaluate((id) => reader.openById(id), cfg.b);
    await page.waitForFunction(() => reader.tabs.length === 2 && reader.activeTab === reader.tabs[1], null, { timeout: 30000 });
    await settled();
    await showTab(1);
    await snap("opened");

    // Annotate: a stroke.
    await draw();
    await snap("annotated");
    // Undo back to how it was opened, then Redo.
    await page.evaluate(() => reader.undoBtn.click());
    await page.waitForFunction(() => !reader.activeTab.dirty, null, { timeout: 10000 });
    await snap("undone");
    await page.evaluate(() => reader.redoBtn.click());
    await page.waitForFunction(() => reader.activeTab.dirty, null, { timeout: 10000 });
    await snap("redone");

    // Save over the file (Ctrl+S), then Ctrl+S again with nothing changed.
    await ctrl("s");
    await page.waitForFunction(() => !reader.activeTab.dirty && /Saved over/.test(document.querySelector(".rd-toast span")?.textContent || ""), null, { timeout: 20000 });
    await snap("saved", { toast: await toast() });
    await ctrl("s");
    await page.waitForFunction(() => /No changes|Saved over/.test(document.querySelector(".rd-toast span")?.textContent || "")
      && !/Saved over/.test(document.querySelector(".rd-toast span")?.textContent || ""), null, { timeout: 5000 }).catch(() => {});
    out.steps.savedAgain = { toast: await toast() };

    // A page edit (turn page 1), with the annotate bar closed, then Undo (Ctrl+Z).
    await page.evaluate(() => reader.closeAnnotate());
    await page.evaluate(() => reader.rotatePages([1], 90));
    await page.waitForFunction(() => reader.activeTab.dirty && reader.activeTab.undoDepth === 1, null, { timeout: 20000 });
    await settled();
    await page.waitForTimeout(500);
    await snap("pageEdited");
    await ctrl("z");
    await page.waitForFunction(() => reader.activeTab.undoDepth === 0, null, { timeout: 20000 });
    await settled();
    await page.waitForTimeout(500);
    await snap("pageUndone");

    // Another stroke, then to the other tab and back.
    await draw(150);
    await snap("annotatedAgain");
    await showTab(2);
    await snap("switched");
    await showTab(1);
    await snap("back");

    // Save as (the desktop window's dialog).
    await page.evaluate(() => reader.saveCopy());
    await page.waitForFunction(() => window.__saved.length === 1 && !reader.activeTab.dirty, null, { timeout: 20000 });
    await snap("savedAs");

    // Redact: a box, Black out; Ctrl+S makes a redacted copy, once; Undo takes the black-out back.
    await page.evaluate(() => { reader.openAnnotate(); reader.useTool("REDACT"); });
    await page.waitForTimeout(300);
    const p = await page.evaluate(() => { const r = reader.pdfViewer.getPageView(0).div.getBoundingClientRect(); return { x: r.left + 60, y: r.top + 60 }; });
    await page.mouse.move(p.x, p.y); await page.mouse.down(); await page.mouse.move(p.x + 200, p.y + 40, { steps: 5 }); await page.mouse.up();
    await page.waitForTimeout(300);
    await page.click(".rd-redact-apply");
    await page.waitForSelector("dialog.rd-dialog[open]");
    await page.click("dialog[open] button.primary");
    await page.waitForFunction(() => reader.activeTab.redacted && /redacted copy/.test(document.querySelector(".rd-toast span")?.textContent || ""), null, { timeout: 30000 });
    await settled();
    await page.waitForTimeout(500);
    await snap("redacted");
    await ctrl("s");
    await page.waitForFunction(() => window.__saved.length === 2 && !reader.activeTab.dirty, null, { timeout: 20000 });
    await snap("redactedSaved", { name: await page.evaluate(() => window.__saved[1]) });
    await page.waitForTimeout(300);
    await ctrl("s");
    await page.waitForFunction(() => /Already saved/.test(document.querySelector(".rd-toast span")?.textContent || ""), null, { timeout: 10000 }).catch(() => {});
    out.steps.redactedAgain = { toast: await toast(), saved: await page.evaluate(() => window.__saved.length) };
    await ctrl("z");
    await page.waitForFunction(() => !reader.activeTab.redacted, null, { timeout: 20000 });
    await settled();
    await page.waitForTimeout(500);
    await snap("redactUndone");

    // Close: it asks (changes not saved); Cancel keeps it, Close without saving closes it.
    await page.evaluate(() => { reader.closeTab(reader.tabs[0]); });
    await page.waitForSelector("dialog.rd-dialog[open]");
    out.steps.closeAsked = await page.evaluate(() => { const d = document.querySelector("dialog[open]");
      return { title: d.querySelector("h3").textContent, text: d.querySelector("p").textContent }; });
    await page.click("dialog[open] .actions button:nth-child(2)");  // Close without saving
    await page.waitForFunction(() => reader.tabs.length === 1, null, { timeout: 20000 });
    await settled();
    out.steps.closed = await page.evaluate(() => ({ tabs: reader.tabs.length, title: document.title, unsaved: reader.hasUnsavedChanges() }));

    // The tab's fields are its own.
    out.steps.assign = await page.evaluate(() => {
      "use strict";  // (as in the reader's own modules; elsewhere the assignment is ignored, which the dirty check shows)
      let threw = false;
      try { reader.activeTab.dirty = true; } catch (e) { threw = true; }
      return { threw, dirty: reader.activeTab.dirty };
    });
  } catch (e) {
    out.errors.push("script: " + (e.stack || e));
  } finally {
    await browser.close();
  }
  console.log(JSON.stringify(out));
})();
