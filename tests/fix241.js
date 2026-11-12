// The owner's Windows check of 0.24.0, in a browser: Sign it puts the
// signature on the printed line (and Sign without one, a moderate size in
// view); Undo back to the saved state clears the unsaved dot; the annotate
// bar comes back when the find bar opened over it closes; the shortcuts
// sheet's descriptions line up; turning a page just after switching tabs.
// Run by test_fixes241.py:
//   node fix241.js <page URL with #token=...> <JSON with ids and paths>
// Prints one JSON object with what it found.
const { chromium } = require("playwright");

(async () => {
  const [url, extraJson] = process.argv.slice(2);
  const cfg = JSON.parse(extraJson);
  const out = { errors: [], native: [] };
  const browser = await chromium.launch();
  const watch = (page, into = out.errors) => {
    page.on("pageerror", (e) => into.push(String(e.message || e)));
    page.on("console", (m) => { if (m.type() === "error" && !/Failed to load resource/.test(m.text())) into.push(m.text()); });
    page.on("dialog", (d) => { out.native.push(d.type() + ": " + d.message()); d.dismiss().catch(() => {}); });
  };
  const shots = cfg.shots || null;
  const shot = async (page, name) => { if (shots) { await page.waitForTimeout(500); await page.screenshot({ path: `${shots}/${name}.png` }); } };
  const opened = (page) => page.waitForSelector(".page canvas", { timeout: 60000 });
  const facts = (page) => page.waitForSelector(".rd-side .fp-facts:not([aria-busy])", { state: "attached", timeout: 30000 });
  // The Sign dialog: a name typed, then Place signature.
  const typeSignature = async (page, name = "Jane Doe") => {
    await page.waitForSelector("dialog.rd-dialog[open] .rd-sigtext", { timeout: 10000 });
    await page.fill("dialog.rd-dialog[open] .rd-sigtext", name);
    await page.click("dialog.rd-dialog[open] button:has-text('Place signature')");
  };
  // The signature just placed: its rectangle in PDF points (the editor's), and whether it's in sight.
  const placed = async (page) => {
    await page.waitForFunction(() => reader.uiManager && [...reader.uiManager.getEditors(reader.uiManager.currentLayer?.pageIndex ?? 0)].some((e) => e.width > 0 && e.div && !e.div.hidden),
      null, { timeout: 15000 });
    await page.waitForTimeout(400);  // (placed, then scrolled into view)
    return page.evaluate(() => {
      const ed = [...reader.uiManager.getEditors(reader.uiManager.currentLayer.pageIndex)].find((e) => e.width > 0);
      const box = ed.div.getBoundingClientRect(), c = document.querySelector(".rd-container").getBoundingClientRect();
      return { rect: ed.getRect(0, 0), page: ed.pageIndex + 1, resizable: ed.isResizable,
        inView: box.top >= c.top - 1 && box.bottom <= c.bottom + 1 && box.left >= c.left - 1 && box.right <= c.right + 1 };
    });
  };
  const saveState = (page) => page.evaluate(() => ({ dirty: !!reader.activeTab.dirty,
    dot: !reader.titleDot.hidden,
    seal: reader.annotSaveBtn.classList.contains("seal") }));

  try {
    // ---- 1. Sign it: on the invoice's signature line; 2. Undo back to how it was opened -----
    for (const theme of ["light", "dark"]) {
      const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 }, colorScheme: theme });
      const page = await ctx.newPage();
      watch(page);
      await page.goto(url + "&open=" + cfg.invoiceIds[theme]);  // (a copy each: the light one is saved)
      await opened(page);
      await facts(page);
      await page.waitForSelector('.rd-side .fp-card-head[data-op="sign"]', { timeout: 30000 });
      const s = (out["sign-" + theme] = {});
      s.line = await page.evaluate(() => fp.facts.suggestions.find((x) => x.op === "sign").line);
      await page.click('.rd-side .fp-card-head[data-op="sign"]');
      await typeSignature(page);
      Object.assign(s, await placed(page));
      s.after = await saveState(page);
      await shot(page, `${theme}-1280-signed-on-the-line`);
      if (theme === "light") {
        // Undo the only change: not changed any more. Redo: changed again.
        await page.keyboard.press("Control+z");
        await page.waitForTimeout(500);
        s.undone = await saveState(page);
        s.undoneEditors = await page.evaluate(() => [...reader.uiManager.getEditors(0)].filter((e) => !e.isEmpty()).length);
        await page.keyboard.press("Control+y");
        await page.waitForTimeout(500);
        s.redone = await saveState(page);
        // Saved, then a second signature undone: back to the saved state, quiet again.
        await page.keyboard.press("Control+s");
        await page.waitForFunction(() => !reader.activeTab.dirty, null, { timeout: 20000 });
        s.saved = await saveState(page);
        await page.evaluate(() => { reader.sign(); });
        await typeSignature(page, "J. D.");
        await page.waitForTimeout(800);
        s.second = await saveState(page);
        await page.keyboard.press("Control+z");
        await page.waitForTimeout(500);
        s.secondUndone = await saveState(page);
      }
      await ctx.close();
    }

    // ---- 1b. Sign without a known line: about 2 inches wide, in sight ------------------------
    {
      const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
      const page = await ctx.newPage();
      watch(page);
      await page.goto(url + "&open=" + cfg.plainId);
      await opened(page);
      await page.evaluate(() => reader.signBtn.click());
      await typeSignature(page);
      out.signPlain = await placed(page);
      await ctx.close();
    }

    // ---- 3. E, then Ctrl+F, then Escape: the annotate bar comes back --------------------------
    {
      const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
      const page = await ctx.newPage();
      watch(page);
      await page.goto(url + "&open=" + cfg.plainId);
      await opened(page);
      await facts(page);
      const state = () => page.evaluate(() => ({ side: !document.querySelector(".rd-side").hidden,
        find: !document.querySelector(".rd-findbar:not(.rd-annotbar):not(.rd-sigbar):not(.rd-formbar)").hidden,
        annot: !document.querySelector(".rd-annotbar").hidden, mode: reader.editMode || "NONE" }));
      const b = (out.bars = { start: await state() });
      await page.evaluate(() => document.querySelector(".rd-container").focus());
      await page.keyboard.press("e");
      await page.waitForTimeout(200);
      b.annotating = await state();
      await page.keyboard.press("Control+f");
      await page.waitForTimeout(200);
      b.finding = await state();
      await page.keyboard.press("Escape");
      await page.waitForTimeout(300);
      b.back = await state();
      await page.keyboard.press("Escape");  // the tool off
      await page.keyboard.press("Escape");  // the bar closed: the panel comes back
      await page.waitForTimeout(300);
      b.closed = await state();
      // The other way round: annotate opened over the find bar, then closed.
      await page.keyboard.press("Control+f");
      await page.waitForTimeout(200);
      await page.evaluate(() => reader.openAnnotate());
      await page.waitForTimeout(200);
      b.annotOverFind = await state();
      await page.evaluate(() => document.querySelector(".rd-container").focus());
      await page.keyboard.press("Escape");
      await page.waitForTimeout(300);
      b.findBack = await state();
      await ctx.close();
    }

    // ---- 4. The shortcuts sheet: one edge for the descriptions --------------------------------
    for (const theme of ["light", "dark"]) {
      const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 }, colorScheme: theme });
      const page = await ctx.newPage();
      watch(page);
      await page.goto(url);
      await page.waitForSelector("#home-host, .rail-btn", { timeout: 30000 });
      await page.waitForTimeout(300);
      await page.locator("body").press("?");
      await page.waitForSelector("#keys-dialog[open]");
      await page.waitForTimeout(400);
      out["keys-" + theme] = await page.evaluate(() => [...document.querySelectorAll("#keys-body section")].map((sec) => {
        const s = sec.getBoundingClientRect();
        return { head: sec.querySelector("h3").textContent, left: s.left,
          dd: [...sec.querySelectorAll("dd")].map((d) => d.getBoundingClientRect().left),
          dtOver: [...sec.querySelectorAll("dt")].some((d) => d.scrollWidth > d.clientWidth + 1) };
      }));
      await shot(page, `${theme}-1280-shortcuts-sheet`);
      await ctx.close();
    }

    // ---- 5. Turning a page just after switching tabs ------------------------------------
    {
      const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
      const page = await ctx.newPage();
      const errors = [];
      watch(page, errors);
      await page.goto(url + "&open=" + cfg.turnA);
      await opened(page);
      await page.evaluate((id) => reader.openById(id), cfg.turnB);
      await page.waitForFunction(() => reader.tabs.length === 2 && reader.activeTab === reader.tabs[1] && document.querySelector(".page canvas"), null, { timeout: 30000 });
      await page.waitForTimeout(800);
      const r = (out.turn = []);
      // While the switch is still under way (-1), just as it's done (0), and a moment later.
      for (const delay of [-1, 0, 15, 40, 80, 150]) {
        const before = await page.evaluate(async () => ({ a: reader.tabs[0].rotated || 0 }));
        await page.click(".rd-doctab:nth-child(1) .rd-doctab-name");
        if (delay >= 0) await page.waitForFunction(() => reader.doc && reader.activeTab === reader.tabs[0], null, { polling: "raf" });
        if (delay > 0) await page.waitForTimeout(delay);
        await page.evaluate(() => reader.rotatePages([1], 90));
        await page.waitForFunction(() => reader.activeTab === reader.tabs[0] && reader.doc && reader.loading.hidden && reader.activeTab.dirty, null, { timeout: 20000 });
        await page.waitForTimeout(1200);
        const got = await page.evaluate(async () => ({ active: reader.tabs.indexOf(reader.activeTab),
          rotateA: (await reader.doc.getPage(1)).rotate, pagesA: reader.doc.numPages }));
        await page.click(".rd-doctab:nth-child(2) .rd-doctab-name");
        await page.waitForFunction(() => reader.doc && reader.activeTab === reader.tabs[1], null, { timeout: 20000 });
        await page.waitForTimeout(600);
        got.rotateB = await page.evaluate(async () => (await reader.doc.getPage(1)).rotate);
        got.pagesB = await page.evaluate(() => reader.doc.numPages);
        r.push({ delay, ...got, errors: errors.splice(0) });
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
