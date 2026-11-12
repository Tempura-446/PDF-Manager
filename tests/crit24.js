// Fixes from the second interface critique and the Windows check of 0.23,
// in a browser: Sign is the drawn signature, the certificate tool is Digital
// ID; a damaged file gets a lasting state with Try to repair it; the file
// panel steps aside while annotating or finding; notes stay in sight; the
// unsaved dot by the title; redacted copies; files carried between tools;
// Redact waits for a choice; Search a folder's results and memory; Save in
// the close dialog; flyout descriptions; palette words and the shortcuts sheet.
// Run by test_critique24.py:
//   node crit24.js <page URL with #token=...> <JSON with ids and paths>
// Prints one JSON object with what it found.
const { chromium } = require("playwright");
const fs = require("fs");

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
  const shot = async (page, name) => { if (shots) { await page.waitForTimeout(500); await page.screenshot({ path: `${shots}/${name}.png` }); } };
  // The desktop window: pywebview's Save dialog, stubbed (it "chooses" a path and says so).
  const desktop = async (opts = {}) => {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 }, ...opts });
    await ctx.addInitScript((savePath) => {
      window.__saved = [];
      window.pywebview = { api: { save_result: async (id, name, near) => { window.__saved.push({ id, name, near }); return savePath; } } };
    }, cfg.savePath);
    return ctx;
  };
  const opened = (page) => page.waitForSelector(".page canvas", { timeout: 60000 });
  const facts = (page) => page.waitForSelector(".rd-side .fp-facts:not([aria-busy])", { state: "attached", timeout: 30000 });

  try {
    // ---- 1. Sign is the pen; the certificate tool is Digital ID ---------------------------
    {
      const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
      const page = await ctx.newPage();
      watch(page);
      await page.goto(url + "&open=" + cfg.signId);
      await opened(page);
      await facts(page);
      await page.waitForSelector('.rd-side .fp-card-head[data-op="sign"]', { timeout: 30000 });
      out.signSuggestion = await page.evaluate(() => document.querySelector('.fp-card-head[data-op="sign"]').innerText);
      await page.click('.fp-tool[data-op="sign"]');
      await page.waitForSelector("dialog.rd-dialog[open] .rd-sigpad", { timeout: 10000 });
      out.panelSign = await page.evaluate(() => ({ title: document.querySelector("dialog[open] h3").textContent,
        typed: !!document.querySelector("dialog[open] .rd-sigtext"), pfx: !!document.querySelector('input[accept*=".pfx"]') }));
      await shot(page, "light-1280-panel-sign-dialog");
      await page.keyboard.press("Escape");
      out.digitalId = await page.evaluate(async () => {
        const ops = (await (await fetch("/api/operations", { headers: { "X-Pdfm-Token": new URLSearchParams(location.hash.slice(1)).get("token") } })).json()).operations;
        const op = ops.find((o) => o.id === "sign-cert");
        reader.openAnnotate();
        document.querySelector(".rd-moretools").click();
        const menu = [...document.querySelectorAll(".rd-toolsmenu [role=menuitemradio]")].map((b) => b.textContent.trim());
        reader.closeAnnotate();
        return { label: op.label, action: op.action, help: op.help, menu,
          palette: reader.commands().filter((c) => /digital/i.test(c.label)).map((c) => c.label) };
      });
      await ctx.close();
    }

    // ---- 2. A damaged file: a lasting state, Try to repair it, Recent says so ------------------
    {
      const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
      const page = await ctx.newPage();
      watch(page);
      await page.goto(url + "&open=" + cfg.brokenId);
      await page.waitForSelector(".rd-broken:not([hidden])", { timeout: 60000 });
      await page.waitForTimeout(4500);  // longer than the old toast
      out.broken = await page.evaluate(() => {
        const b = document.querySelector(".rd-broken");
        return { shown: !b.hidden, name: b.querySelector(".rd-broken-name").textContent, nameFont: getComputedStyle(b.querySelector(".rd-broken-name")).fontFamily,
          why: b.querySelector(".rd-broken-why").textContent, buttons: [...b.querySelectorAll("button:not([hidden])")].map((x) => x.textContent.trim()),
          seal: b.querySelector(".rd-broken-repair").classList.contains("seal"), focus: document.activeElement.textContent.trim(), toast: !!document.querySelector(".rd-toast") };
      });
      await shot(page, "light-1280-damaged-file");
      await page.click(".rd-broken-repair");
      await page.waitForFunction(() => reader.tabs.length === 2, null, { timeout: 60000 });
      await page.waitForTimeout(500);
      out.repaired = await page.evaluate(() => ({ tabs: reader.tabs.map((t) => [t.name, !!t.broken, !!t.unsaved]), active: reader.activeTab.name,
        dot: !document.querySelector(".rd-doctab.active .rd-dot").hidden }));
      await shot(page, "light-1280-damaged-repaired");
      // Back to the damaged tab: its state again; Close closes it.
      await page.click(".rd-doctab:first-child .rd-doctab-name");
      await page.waitForSelector(".rd-broken:not([hidden])");
      await page.click(".rd-broken-close");
      await page.waitForFunction(() => reader.tabs.length === 1 && reader.doc && reader.activeTab === reader.tabs[0], null, { timeout: 30000 });
      out.closedBroken = await page.evaluate(() => ({ tabs: reader.tabs.map((t) => t.name), broken: !document.querySelector(".rd-broken").hidden }));
      await ctx.close();
      // Recent: flagged, and not moved to the top.
      for (const theme of ["light", "dark"]) {
        const c2 = await browser.newContext({ viewport: { width: 1280, height: 800 }, colorScheme: theme });
        const p2 = await c2.newPage();
        watch(p2);
        await p2.goto(url);
        await p2.waitForSelector("#home-host .recent-row", { timeout: 30000 });
        await p2.waitForTimeout(300);
        out["recent-" + theme] = await p2.evaluate(() => [...document.querySelectorAll("#home-host .recent-row")].map((r) => {
          const f = r.querySelector(".recent-facts");
          return { name: r.dataset.name, facts: f?.textContent || "", bad: !!f?.classList.contains("bad"), color: f ? getComputedStyle(f).color : "",
            err: getComputedStyle(document.documentElement).getPropertyValue("--err").trim() };
        }));
        await shot(p2, `${theme}-1280-recent-cant-be-opened`);
        await c2.close();
      }
    }

    // ---- 3. Room for the page -----------------------------------------------------------
    for (const [w, h] of [[1280, 800], [1180, 820]]) {
      const ctx = await browser.newContext({ viewport: { width: w, height: h } });
      const page = await ctx.newPage();
      watch(page);
      await page.goto(url + "&open=" + cfg.roomIds[w]);  // (a file each: the note is saved into it)
      await opened(page);
      await facts(page);
      const state = () => page.evaluate(() => ({ side: !document.querySelector(".rd-side").hidden,
        button: document.querySelector(".rd-panel-btn").getAttribute("aria-expanded"),
        find: !document.querySelector(".rd-findbar:not(.rd-annotbar):not(.rd-sigbar):not(.rd-formbar)").hidden,
        annot: !document.querySelector(".rd-annotbar").hidden, column: document.querySelector(".rd-container").clientWidth }));
      const r = (out["room" + w] = { start: await state() });
      await page.evaluate(() => reader.openAnnotate());
      await page.waitForTimeout(600);  // (the pages settle at their new width)
      r.annotating = await state();
      await page.keyboard.press("c");
      const at = await page.evaluate(() => { const c = document.querySelector(".rd-container").getBoundingClientRect(), p = document.querySelector(".page").getBoundingClientRect();
        return { x: Math.min(p.right, c.right) - 24, y: Math.min(p.bottom, c.bottom) - 30 }; });
      await page.mouse.click(at.x, at.y);
      await page.waitForSelector(".rd-notebox");
      r.note = await page.evaluate(() => {
        const c = document.querySelector(".rd-container"), cr = c.getBoundingClientRect();
        const inside = (e) => { const b = e.getBoundingClientRect(); return b.left >= cr.left && b.right <= cr.left + c.clientWidth && b.top >= cr.top && b.bottom <= cr.top + c.clientHeight; };
        const add = [...document.querySelectorAll(".rd-notebox button")].find((b) => b.textContent === "Add note");
        return { box: inside(document.querySelector(".rd-notebox")), add: inside(add) };
      });
      await shot(page, `light-${w}-note-editor`);
      await page.keyboard.type("Check this");
      // A note being typed goes in with Ctrl+S (before the save).
      await page.keyboard.press("Control+s");
      await page.waitForTimeout(1500);
      r.noteSaved = await page.evaluate(() => ({ box: !!document.querySelector(".rd-notebox") }));
      // The find bar: one row at a time, and the panel still aside.
      await page.keyboard.press("Control+f");
      await page.waitForTimeout(200);
      r.finding = await state();
      await page.keyboard.press("Escape");
      await page.waitForTimeout(200);
      r.after = await state();
      // Opened by hand while annotating: it stays.
      await page.evaluate(() => reader.openAnnotate());
      await page.evaluate(() => reader.togglePanel());  // (as the panel button and T do)
      r.byHand = await state();
      await page.evaluate(() => reader.closeAnnotate());
      // The mode's hint goes with the mode.
      await page.evaluate(() => { reader.openAnnotate(); reader.useTool("EDITTEXT"); });
      await page.waitForTimeout(200);
      r.hint = await page.evaluate(() => document.querySelector(".rd-toast span")?.textContent || "");
      await page.keyboard.press("Control+f");
      await page.waitForTimeout(400);
      r.hintAfterFind = await page.evaluate(() => [...document.querySelectorAll(".rd-toast:not(.out) span")].map((s) => s.textContent));
      await ctx.close();
    }

    // ---- 4. Saving: the dot by the title, redacted copies ----------------------------------
    {
      const ctx = await desktop();
      const page = await ctx.newPage();
      watch(page);
      await page.goto(url + "&open=" + cfg.redactId);
      await opened(page);
      out.dotBefore = await page.evaluate(() => !document.querySelector(".rd-title .rd-dot").hidden);
      await page.evaluate(() => reader.rotatePages([1], 90));
      await page.waitForFunction(() => reader.activeTab.dirty, null, { timeout: 20000 });
      out.dot = await page.evaluate(() => ({ tabbar: !document.querySelector(".rd-tabbar").hidden, dot: !document.querySelector(".rd-title .rd-dot").hidden,
        sr: document.querySelector(".rd-title .rd-sr").hidden ? null : document.querySelector(".rd-title .rd-sr").textContent }));
      await shot(page, "light-1280-title-unsaved-dot");
      await page.evaluate(() => reader.useTool("REDACT"));
      await page.waitForTimeout(300);
      const p = await page.evaluate(() => { const r = document.querySelector(".page").getBoundingClientRect(); return { x: r.left + 120, y: r.top + 140 }; });
      out.applyBefore = await page.evaluate(() => { const b = document.querySelector(".rd-redact-apply"); return { text: b.textContent, disabled: b.disabled }; });
      await page.mouse.move(p.x, p.y); await page.mouse.down(); await page.mouse.move(p.x + 180, p.y + 50, { steps: 5 }); await page.mouse.up();
      await page.waitForTimeout(300);
      out.apply = await page.evaluate(() => { const b = document.querySelector(".rd-redact-apply"), s = document.querySelector(".rd-annotsave");
        return { text: b.textContent, seal: b.classList.contains("seal"), disabled: b.disabled, saveSeal: s.classList.contains("seal") }; });
      await page.click(".rd-redact-apply");
      await page.waitForSelector("dialog.rd-dialog[open]");
      await page.click("dialog[open] button.primary");
      await page.waitForFunction(() => /redacted copy/.test(document.querySelector(".rd-toast span")?.textContent || ""), null, { timeout: 30000 });
      out.redactToast = await page.evaluate(() => ({ text: document.querySelector(".rd-toast span").textContent,
        buttons: [...document.querySelectorAll(".rd-toast button")].map((b) => [b.textContent, b.classList.contains("seal")]) }));
      await shot(page, "light-1280-redacted-toast");
      // Replace original asks first; Cancel leaves the file as it was.
      const before = fs.readFileSync(cfg.redactPath);
      await page.click(".rd-toast button:nth-of-type(2)");
      await page.waitForSelector("dialog.rd-dialog[open]");
      out.replaceAsk = await page.evaluate(() => { const d = document.querySelector("dialog[open]");
        return { title: d.querySelector("h3").textContent, text: d.querySelector("p").textContent, buttons: [...d.querySelectorAll(".actions button")].map((b) => b.textContent) }; });
      await shot(page, "light-1280-replace-original");
      await page.click("dialog[open] .actions button:first-child");
      await page.waitForTimeout(300);
      out.replaceCancelled = fs.readFileSync(cfg.redactPath).equals(before);
      // Ctrl+S: the redacted copy, by the original (its folder), never over it.
      await page.focus(".rd-container");
      await page.keyboard.press("Control+s");
      await page.waitForFunction(() => window.__saved.length > 0, null, { timeout: 20000 });
      out.redactSave = await page.evaluate(() => ({ saved: window.__saved, dirty: reader.activeTab.dirty }));
      out.redactUntouched = fs.readFileSync(cfg.redactPath).equals(before);
      await ctx.close();
    }

    // ---- 5. Files carried between tools; Redact waits for a choice; errors by the bar ---------
    for (const theme of ["light", "dark"]) {
      const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 }, colorScheme: theme });
      const page = await ctx.newPage();
      watch(page);
      await page.goto(url);
      await page.waitForSelector("#home-host .recent-row, #home-host .start-empty", { timeout: 30000 });
      await page.evaluate(() => select("compress"));
      await page.setInputFiles("#file-input", [cfg.plainPath, cfg.extraPath]);
      await page.setInputFiles("#file-input", [cfg.plainPath]);  // the same file again
      await page.waitForTimeout(300);
      const t = (out["carry-" + theme] = { afterTwice: await page.evaluate(() => items.map((i) => i.file.name)) });
      await page.evaluate(() => select("redact"));
      await page.waitForTimeout(500);
      t.redact = await page.evaluate(() => ({ line: document.getElementById("carried").hidden ? null : document.getElementById("carried").textContent,
        summary: document.getElementById("summary").textContent, run: document.getElementById("run").disabled, items: items.length }));
      await shot(page, `${theme}-1280-carried-files`);
      await page.check("#f-emails");
      await page.waitForTimeout(200);
      t.chosen = await page.evaluate(() => ({ summary: document.getElementById("summary").textContent, run: document.getElementById("run").disabled }));
      await page.click("#carried .link-btn");
      t.cleared = await page.evaluate(() => ({ items: items.length, line: !document.getElementById("carried").hidden }));
      // Removing a file hides the line too.
      await page.evaluate(() => select("compress"));
      await page.setInputFiles("#file-input", [cfg.plainPath, cfg.extraPath]);
      await page.evaluate(() => select("rotate"));
      t.carriedAgain = await page.evaluate(() => !document.getElementById("carried").hidden);
      await page.click('#file-grid .file-card:first-child .chip-btn[title="Remove"]');
      t.afterRemove = await page.evaluate(() => !document.getElementById("carried").hidden);
      // A server error: a capital, by the action bar too.
      await page.route("**/api/run", (r) => r.fulfill({ status: 400, contentType: "application/json", body: JSON.stringify({ error: "that page doesn't exist" }) }));
      await page.click("#run");
      await page.waitForSelector("#run-err:not([hidden])", { timeout: 10000 });
      t.error = await page.evaluate(() => ({ top: document.querySelector("#error span").firstChild.textContent, bar: document.getElementById("run-err").textContent,
        inBar: !!document.querySelector(".action-bar #run-err") }));
      await shot(page, `${theme}-1280-run-error`);
      await ctx.close();
    }

    // ---- 6. Small things -------------------------------------------------------------------
    {
      // (a) A result of Compress isn't offered Compress: the facts are asked with what made it.
      const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
      const page = await ctx.newPage();
      watch(page);
      let asked = [];
      // (The browser keeps a file part of a request body to itself: what the page put in the form is noted instead.)
      await page.addInitScript(() => { window.__made = []; const add = FormData.prototype.append; FormData.prototype.append = function (k, v, ...rest) { if (k === "made_by") window.__made.push(v); return add.call(this, k, v, ...rest); }; });
      await page.goto(url + "&open=" + cfg.plainId);
      await opened(page);
      await facts(page);
      await page.evaluate(() => fpChoose("compress"));
      await page.click("#fp-opts-compress .fp-run");
      await page.waitForFunction(() => reader.tabs.length === 2, null, { timeout: 60000 });
      await page.waitForFunction(() => document.querySelector(".rd-side .fp-facts:not([aria-busy])"), null, { timeout: 30000 });
      await page.waitForTimeout(1200);
      asked = await page.evaluate(() => window.__made);
      out.compressFacts = asked.map((v) => v === "compress");
      out.compressSuggested = await page.evaluate(() => [...document.querySelectorAll('.rd-side .fp-sec:not([hidden]) .fp-card-head[data-op="compress"]')].length);

      // (f) Closing a tab with changes: Save saves it, then closes it.
      await page.click(".rd-doctab:first-child .rd-doctab-name");
      await page.waitForFunction(() => reader.activeTab === reader.tabs[0]);
      await page.close();
      await ctx.close();
    }
    {
      const ctx = await desktop();
      const page = await ctx.newPage();
      watch(page);
      await page.goto(url + "&open=" + cfg.closeId);
      await opened(page);
      await page.evaluate((id) => reader.openById(id), cfg.extraId);
      await page.waitForFunction(() => reader.tabs.length === 2, null, { timeout: 30000 });
      await page.click(".rd-doctab:first-child .rd-doctab-name");
      await page.waitForFunction(() => reader.activeTab === reader.tabs[0]);
      const before = fs.readFileSync(cfg.closePath);
      await page.evaluate(() => reader.rotatePages([1], 90));
      await page.waitForFunction(() => reader.activeTab.dirty, null, { timeout: 20000 });
      await page.click(".rd-doctab:first-child .rd-doctab-x");
      await page.waitForSelector("dialog.rd-dialog[open]");
      out.closeDialog = await page.evaluate(() => [...document.querySelectorAll("dialog[open] .actions button")].map((b) => [b.textContent, b.className]));
      await shot(page, "light-1280-close-dialog-save");
      await page.click("dialog[open] button.primary");
      await page.waitForFunction(() => reader.tabs.length === 1, null, { timeout: 20000 });
      out.closeSaved = { tabs: await page.evaluate(() => reader.tabs.map((t) => t.name)), changed: !fs.readFileSync(cfg.closePath).equals(before) };
      await ctx.close();
    }
    for (const theme of ["light", "dark"]) {
      // (b, c, d, e) Search a folder: its name, relative headings, the other copy named, the form kept.
      const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 }, colorScheme: theme });
      const page = await ctx.newPage();
      watch(page);
      await page.goto(url);
      await page.waitForSelector("#home-host .recent-row, #home-host .start-empty", { timeout: 30000 });
      const s = (out["search-" + theme] = {});
      s.names = await page.evaluate(() => ({ start: document.querySelector('#home-host .multi [data-tool="search"]').textContent,
        rail: ops.find((o) => o.id === "search").label }));
      await page.evaluate(() => select("search"));
      s.title = await page.textContent("#title");
      await page.fill("#f-directory", cfg.searchDir);
      await page.fill("#f-query", "when is the water bill due");
      await page.click("#run");
      await page.waitForSelector(".search-row", { timeout: 120000 });
      await page.waitForTimeout(300);
      Object.assign(s, await page.evaluate(() => ({
        heads: [...document.querySelectorAll(".search-folder-row th")].map((th) => [th.textContent, th.title]),
        also: [...document.querySelectorAll(".search-row")].map((r) => [r.querySelector(".search-name").textContent, r.querySelector(".search-also")?.textContent || ""]),
      })));
      await shot(page, `${theme}-1280-search-duplicates`);
      await page.evaluate(() => select("compress"));
      await page.evaluate(() => select("search"));
      s.kept = await page.evaluate(() => [document.getElementById("f-directory").value, document.getElementById("f-query").value]);
      // (g) Flyout descriptions: none cut off, at 1180 too.
      for (const w of [1280, 1180]) {
        await page.setViewportSize({ width: w, height: 800 });
        for (const g of ["pages", "improve", "convert", "protect", "all"]) {
          await page.click(`.rail-btn[data-group="${g}"]`);
          await page.waitForSelector("#flyout:not([hidden])");
          await page.waitForTimeout(200);
          const cut = await page.evaluate(() => [...document.querySelectorAll("#flyout .fly-tool small")]
            .filter((x) => x.scrollHeight > x.clientHeight + 1 || x.scrollWidth > x.clientWidth + 1).map((x) => x.textContent));
          (s.cut ||= []).push(...cut);
          if (g === "improve" && w === 1180) await shot(page, `${theme}-1180-flyout-improve`);
          await page.keyboard.press("Escape");
        }
      }
      await page.setViewportSize({ width: 1280, height: 800 });
      // (h) Other words in the palette; the shortcuts sheet from it and from ?.
      s.palette = {};
      for (const q of ["shrink", "join", "combine", "lock", "protect", "unlock", "sign", "help", "shortcuts"]) {
        await page.keyboard.press("Control+k");
        await page.fill("#palette-input", q);
        await page.waitForTimeout(120);
        s.palette[q] = await page.evaluate(() => [...document.querySelectorAll(".palette-item .label")].slice(0, 2).map((l) => l.firstChild.textContent));
        await page.keyboard.press("Escape");
      }
      await page.keyboard.press("Control+k");
      await page.fill("#palette-input", "shortcuts");
      await page.keyboard.press("Enter");
      await page.waitForSelector("#keys-dialog[open]");
      s.sheet = await page.evaluate(() => ({ title: document.getElementById("keys-title").textContent,
        keys: [...document.querySelectorAll("#keys-body kbd")].map((k) => k.textContent), heads: [...document.querySelectorAll("#keys-body h3")].map((h) => h.textContent) }));
      await shot(page, `${theme}-1280-shortcuts`);
      await page.keyboard.press("Escape");
      await page.locator("body").press("?");
      s.questionMark = await page.evaluate(() => document.getElementById("keys-dialog").open);
      await page.keyboard.press("Escape");
      await page.click("#find");  // the palette, then ? typed in it: no sheet
      await page.keyboard.type("?");
      s.typedQuestionMark = await page.evaluate(() => document.getElementById("keys-dialog").open);
      await page.keyboard.press("Escape");
      await ctx.close();
    }
    {
      // In the reader, "sign" finds the pen first, then Digital ID.
      const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
      const page = await ctx.newPage();
      watch(page);
      await page.goto(url + "&open=" + cfg.plainId);
      await opened(page);
      await page.keyboard.press("Control+k");
      await page.fill("#palette-input", "sign");
      await page.waitForTimeout(150);
      out.readerSign = await page.evaluate(() => [...document.querySelectorAll(".palette-item .label")].map((l) => l.firstChild.textContent));
      await ctx.close();
    }
  } catch (e) {
    out.errors.push("script: " + (e.stack || e));
  } finally {
    await browser.close();
  }
  console.log(JSON.stringify(out));
})();
