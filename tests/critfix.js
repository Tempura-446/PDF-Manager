// Fixes from the 0.23 interface critique, in a browser: saving and finishing
// (Save… and Show in folder after a panel run, the unsaved dot, Undo after
// Save wrote over a file, the app's own dialogs instead of confirm()), the
// annotate bar's labels, More tools menu and single keys, the rail's groups,
// Search results, the compact file strip, error flags and verb buttons.
// Run by test_critique_fixes.py:
//   node critfix.js <page URL with #token=...> <JSON with ids and paths>
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
    // The browser's own dialogs must never come up: count any that do.
    page.on("dialog", (d) => { out.native.push(d.type() + ": " + d.message()); d.dismiss().catch(() => {}); });
  };
  const shots = cfg.shots || null;
  const shot = async (page, name) => { if (shots) { await page.waitForTimeout(500); await page.screenshot({ path: `${shots}/${name}.png` }); } };
  // WCAG contrast of two computed colours.
  const RATIO = `(() => {
    const parse = (c) => { const n = (c.match(/[\\d.]+/g) || []).map(Number);
      return c.startsWith("color(") ? [...n.slice(0, 3).map((v) => v * 255), ...n.slice(3)] : n; };
    const lum = ([r, g, b]) => { const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }; return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b); };
    const over = (top, base) => { const [r, g, b, a = 1] = parse(top); const [R, G, B] = base; return [r * a + R * (1 - a), g * a + G * (1 - a), b * a + B * (1 - a)]; };
    const bgOf = (el) => { const layers = []; for (let e = el; e; e = e.parentElement) { const c = getComputedStyle(e).backgroundColor; const p = parse(c); if (p.length && (p[3] ?? 1) > 0) { layers.push(c); if ((p[3] ?? 1) >= 1) break; } }
      let base = parse(getComputedStyle(document.body).backgroundColor).slice(0, 3); for (const c of layers.reverse()) base = over(c, base); return base; };
    const ratio = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
    return { bgOf, ratio, over, parse };
  })()`;
  const contrast = (page, sel) => page.evaluate(([sel, RATIO]) => {
    const R = eval(RATIO);
    const e = document.querySelector(sel);
    if (!e) return null;
    const bg = R.bgOf(e), fg = R.over(getComputedStyle(e).color, bg);
    return Math.round(R.ratio(fg, bg) * 100) / 100;
  }, [sel, RATIO]);
  // In the desktop window: pywebview's API, stubbed. Save "chooses" the path
  // given, and the browser's confirm() and alert() throw (they mustn't be used).
  const desktopContext = async (opts = {}) => {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 }, ...opts });
    await ctx.addInitScript((savePath) => {
      window.__saved = [];
      window.__native = [];
      for (const name of ["confirm", "alert", "prompt"]) {
        window[name] = (msg) => { window.__native.push(name + ": " + msg); throw new Error("window." + name + " was called"); };
      }
      window.pywebview = { api: {
        save_result: async (id, name, near) => { window.__saved.push({ id, name, near }); return savePath; },
        reveal: async () => { throw new Error("the page should ask the server"); },
      } };
    }, cfg.savePath);
    return ctx;
  };

  try {
    // ---- 1. Saving and finishing ------------------------------------------------------
    {
      const ctx = await desktopContext();
      const page = await ctx.newPage();
      watch(page);
      await page.goto(url + "&open=" + cfg.plainId);
      await page.waitForSelector(".page canvas", { timeout: 60000 });
      await page.waitForSelector(".rd-side .fp-facts:not([aria-busy])", { state: "attached", timeout: 30000 });
      // A tool run in the panel: its result opens in a new tab, not saved anywhere yet.
      await page.click('.fp-tool[data-op="encrypt"]');
      await page.fill("#fp-encrypt-new_password", "s3cret-pass");
      await page.click("#fp-opts-encrypt .fp-run");
      await page.waitForFunction(() => document.querySelectorAll(".rd-doctab").length === 2, null, { timeout: 60000 });
      await page.waitForSelector(".fp-note .fp-saveas", { timeout: 20000 });
      out.panelDone = await page.evaluate(() => {
        const save = document.querySelector(".fp-note .fp-saveas");
        const active = document.querySelector(".rd-doctab.active");
        return {
          save: save.textContent.trim(), seal: save.classList.contains("seal"), bg: getComputedStyle(save).backgroundColor,
          sealColor: getComputedStyle(document.documentElement).getPropertyValue("--seal").trim(),
          dot: !active.querySelector(".rd-dot").hidden, srText: active.querySelector(".rd-sr")?.hidden === false ? active.querySelector(".rd-sr").textContent : null,
          otherDot: !document.querySelector(".rd-doctab:not(.active) .rd-dot").hidden,
          note: document.querySelector(".fp-note").textContent,
        };
      });
      await shot(page, "light-1180-reader-panel-success");
      await page.click(".fp-note .fp-saveas");
      await page.waitForSelector(".fp-note .fp-reveal", { timeout: 10000 });
      out.panelSaved = await page.evaluate(() => ({
        saved: window.__saved, note: document.querySelector(".fp-note").textContent,
        reveal: document.querySelector(".fp-note .fp-reveal").textContent.trim(),
        dot: !document.querySelector(".rd-doctab.active .rd-dot").hidden,
        focus: document.activeElement.className,
      }));
      await page.click(".fp-note .fp-reveal");
      await page.waitForTimeout(400);
      await ctx.close();
    }
    {
      // Ctrl+S over the file: "Saved over X — Undo" puts back what it held.
      const ctx = await desktopContext();
      const page = await ctx.newPage();
      watch(page);
      await page.goto(url + "&open=" + cfg.undoId);
      await page.waitForSelector(".page canvas", { timeout: 60000 });
      const before = fs.readFileSync(cfg.undoPath);
      await page.evaluate(() => reader.rotatePages([1], 90));
      await page.waitForFunction(() => reader.activeTab.dirty, null, { timeout: 20000 });
      await page.focus(".rd-container");
      await page.keyboard.press("Control+s");
      await page.waitForFunction(() => /^Saved over/.test(document.querySelector(".rd-toast span")?.textContent || ""), null, { timeout: 20000 });
      const middle = fs.readFileSync(cfg.undoPath);
      out.overwrite = { toast: await page.textContent(".rd-toast span"), button: await page.textContent(".rd-toast button"), changed: !middle.equals(before) };
      await page.waitForTimeout(2500);  // longer than the old 2 s toast
      out.overwrite.stillThere = await page.isVisible(".rd-toast button");
      await shot(page, "light-1180-overwrite-undo-toast");
      await page.click(".rd-toast button");
      await page.waitForFunction(() => /Undone/.test(document.querySelector(".rd-toast span")?.textContent || ""), null, { timeout: 20000 });
      out.undone = { toast: await page.textContent(".rd-toast span"), restored: fs.readFileSync(cfg.undoPath).equals(before),
        dirty: await page.evaluate(() => reader.activeTab.dirty) };

      // Changed by another program meanwhile: the app's own dialog, buttons named by verb; Escape = Cancel.
      fs.appendFileSync(cfg.undoPath, "\n% changed elsewhere\n");
      const elsewhere = fs.readFileSync(cfg.undoPath);
      await page.focus(".rd-container");
      await page.keyboard.press("Control+s");
      await page.waitForSelector("dialog.rd-dialog[open]", { timeout: 20000 });
      out.conflict = await page.evaluate(() => {
        const d = document.querySelector("dialog.rd-dialog[open]");
        return { title: d.querySelector("h3").textContent, buttons: [...d.querySelectorAll(".actions button")].map((b) => b.textContent),
          main: d.querySelector(".actions button.primary")?.textContent, focus: document.activeElement.textContent };
      });
      await shot(page, "light-1180-conflict-dialog");
      await page.keyboard.press("Escape");
      await page.waitForSelector("dialog.rd-dialog[open]", { state: "detached", timeout: 5000 });
      await page.waitForTimeout(300);
      out.conflictCancel = { untouched: fs.readFileSync(cfg.undoPath).equals(elsewhere), saved: await page.evaluate(() => window.__saved.length),
        focus: await page.evaluate(() => document.activeElement.className), dirty: await page.evaluate(() => reader.activeTab.dirty) };
      // Replace file: it's written.
      await page.keyboard.press("Control+s");
      await page.waitForSelector("dialog.rd-dialog[open]", { timeout: 20000 });
      await page.click("dialog.rd-dialog[open] button.primary");
      await page.waitForFunction(() => !reader.activeTab.dirty, null, { timeout: 20000 });
      out.conflictReplace = { replaced: !fs.readFileSync(cfg.undoPath).equals(elsewhere) };

      // A signed document: the warning is the app's dialog too, and Cancel changes nothing.
      await page.evaluate(() => { reader.activeTab.signatures = [{ name: "Test" }]; reader.activeTab.signedWarned = false; });
      const version = await page.evaluate(() => reader.activeTab.version);
      page.evaluate(() => reader.rotatePages([1], 90));
      await page.waitForSelector("dialog.rd-dialog[open]", { timeout: 20000 });
      out.signed = await page.evaluate(() => {
        const d = document.querySelector("dialog.rd-dialog[open]");
        return { title: d.querySelector("h3").textContent, buttons: [...d.querySelectorAll(".actions button")].map((b) => b.textContent) };
      });
      await page.click("dialog.rd-dialog[open] .actions button:not(.primary)");
      await page.waitForTimeout(400);
      out.signed.unchanged = (await page.evaluate(() => reader.activeTab.version)) === version;
      out.nativeCalls = await page.evaluate(() => window.__native);
      await ctx.close();
    }
    {
      // The tool page's result card: Save… is the seal action, Run steps back.
      const ctx = await desktopContext();
      const page = await ctx.newPage();
      watch(page);
      await page.goto(url);
      await page.waitForSelector("#home-host", { timeout: 30000 });
      await page.dispatchEvent('.tools button[data-op="compress"]', "click");
      await page.setInputFiles("#file-input", cfg.plainPath);
      await page.waitForFunction(() => !document.querySelector("#run").disabled, null, { timeout: 30000 });
      await page.click("#run");
      await page.waitForSelector("#result:not([hidden]) .btn.seal", { timeout: 60000 });
      out.resultCard = await page.evaluate(() => {
        const save = document.querySelector("#result .btn.seal"), run = document.querySelector("#run");
        return { save: save.textContent.trim(), saveBg: getComputedStyle(save).backgroundColor, runBg: getComputedStyle(run).backgroundColor,
          seals: [...document.querySelectorAll("button, a")].filter((b) => b.getClientRects().length && getComputedStyle(b).backgroundColor === getComputedStyle(save).backgroundColor).length };
      });
      out.settingsSave = await page.evaluate(() => document.querySelector("#set-save").classList.contains("seal"));
      await ctx.close();
    }
    // ---- 2. The annotate bar: words for the tools used most, More tools, single keys ---------
    for (const [w, h] of [[1180, 820], [1280, 800], [1440, 900]]) {
      for (const theme of w === 1180 ? ["light", "dark"] : ["light"]) {
        const ctx = await browser.newContext({ viewport: { width: w, height: h }, colorScheme: theme });
        const page = await ctx.newPage();
        watch(page);
        await page.goto(url + "&open=" + cfg.plainId);
        await page.waitForSelector(".page canvas", { timeout: 60000 });
        await page.click(".rd-toolbar button[title^='Annotate']");
        await page.waitForSelector(".rd-annotbar:not([hidden])");
        out[`annot${w}${theme === "dark" ? "dark" : ""}`] = await page.evaluate(() => {
          const bar = document.querySelector(".rd-annotbar"), b = bar.getBoundingClientRect();
          const vis = (e) => { if (!e) return false; const r = e.getBoundingClientRect(); return r.width > 0 && r.left >= b.left - 1 && r.right <= b.right + 1 && r.right <= innerWidth; };
          const worded = [...bar.querySelectorAll(".rd-btn")].filter((x) => vis(x) && x.querySelector("span") && getComputedStyle(x.querySelector("span")).display !== "none")
            .map((x) => x.querySelector("span").textContent);
          return { worded, save: vis(bar.querySelector(".rd-annotsave")), close: vis(bar.querySelector("button[title^='Close the annotation']")),
            scrollW: bar.scrollWidth, clientW: bar.clientWidth, pageScroll: document.documentElement.scrollWidth > innerWidth };
        });
        if (w === 1180) await shot(page, `${theme}-1180-annotate-bar`);
        if (w === 1280 && theme === "light") {
          // More tools from the keyboard: arrows open and move, Escape closes (focus back), Enter picks.
          await page.focus(".rd-moretools");
          await page.keyboard.press("ArrowDown");
          const menu = () => page.evaluate(() => ({
            open: !document.querySelector(".rd-toolsmenu").hidden, expanded: document.querySelector(".rd-moretools").getAttribute("aria-expanded"),
            focus: document.activeElement.textContent.trim(), role: document.activeElement.getAttribute("role"),
            items: [...document.querySelectorAll(".rd-toolsmenu [role=menuitemradio]")].map((x) => x.textContent.trim()),
            button: document.querySelector(".rd-moretools").textContent.trim(), on: document.querySelector(".rd-moretools").classList.contains("on"),
            label: document.querySelector(".rd-moretools").getAttribute("aria-label"),
          }));
          out.menuOpen = await menu();
          await shot(page, "light-1180-more-tools-menu");
          await page.keyboard.press("ArrowDown");
          out.menuDown = await menu();
          await page.keyboard.press("End");
          out.menuEnd = await menu();
          await page.keyboard.press("Escape");
          out.menuEsc = await menu();
          out.menuEsc.barOpen = await page.isVisible(".rd-annotbar");
          await page.keyboard.press("Enter");
          await page.waitForSelector(".rd-toolsmenu:not([hidden])");
          await page.keyboard.press("Home");
          for (let i = 0; i < 2; i++) await page.keyboard.press("ArrowDown");  // Underline, Strike out, Shapes
          await page.keyboard.press("Enter");
          await page.waitForTimeout(200);
          out.menuPicked = await menu();
          // Single keys, while the bar is open and focus isn't in a box.
          await page.focus(".rd-container");
          const mode = () => page.evaluate(() => reader.editMode);
          out.keys = {};
          for (const k of ["h", "c", "a", "d"]) { await page.keyboard.press(k); out.keys[k] = await mode(); }
          await page.keyboard.press("d");
          out.keys.again = await mode();
          await page.keyboard.press("h");
          out.keys.button = await page.evaluate(() => document.querySelector(".rd-annotbar [data-mode=HIGHLIGHT]").getAttribute("aria-pressed"));
          out.keys.tips = await page.evaluate(() => {
            const t = Object.fromEntries(["HIGHLIGHT", "NOTE", "FREETEXT", "INK"].map((m) => [m, document.querySelector(`.rd-annotbar [data-mode=${m}]`).title]));
            t.SIGN = document.querySelector(".rd-annotbar button[title^='Add your signature']").title;
            return t;
          });
          await page.keyboard.press("Escape");
          const pageBefore = await page.evaluate(() => reader.pdfViewer.currentPageNumber);
          await page.keyboard.press("n");
          out.keys.next = (await page.evaluate(() => reader.pdfViewer.currentPageNumber)) === pageBefore + 1;
          // In a box, a key is just a letter.
          await page.click(".rd-page input");
          await page.keyboard.press("h");
          out.keys.typing = await mode();
          await page.focus(".rd-container");
          await page.keyboard.press("s");
          await page.waitForSelector("dialog.rd-dialog[open]", { timeout: 5000 });
          out.keys.sign = await page.textContent("dialog.rd-dialog[open] h3");
          await page.keyboard.press("Escape");
          // The bar closed: the keys do nothing.
          await page.waitForTimeout(200);
          await page.click(".rd-toolbar button[title^='Annotate']");
          await page.focus(".rd-container");
          await page.keyboard.press("h");
          out.keys.closed = { mode: await mode(), bar: await page.isVisible(".rd-annotbar") };
        }
        await ctx.close();
      }
    }
    // ---- 3. The rail by intent: Improve; folder tools under no rail group ---------------------
    {
      const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
      const page = await ctx.newPage();
      watch(page);
      await page.goto(url);
      await page.waitForSelector("#home-host .start-drop", { timeout: 30000 });
      out.rail = { groups: await page.$$eval(".rail-btn[data-group]", (bs) => bs.map((b) => b.textContent.trim())), flyouts: {} };
      for (const group of ["improve", "convert", "all"]) {
        await page.click(`.rail-btn[data-group="${group}"]`);
        await page.waitForSelector("#flyout:not([hidden])");
        await page.waitForTimeout(100);
        out.rail.flyouts[group] = await page.evaluate(() => ({
          tools: [...document.querySelectorAll("#flyout .fly-tool")].map((b) => b.dataset.op),
          heads: [...document.querySelectorAll("#flyout .fly-sec")].map((h) => h.textContent),
          cue: document.getElementById("flyout").classList.contains("more-below"),
          fits: document.getElementById("flyout").getBoundingClientRect().bottom <= innerHeight,
        }));
        if (group === "improve") await shot(page, "light-1280-improve-flyout");
        if (group === "all") {
          await page.evaluate(() => { const l = document.getElementById("flyout-list"); l.scrollTop = l.scrollHeight; });
          await page.waitForTimeout(150);
          out.rail.flyouts.all.cueAtEnd = await page.evaluate(() => document.getElementById("flyout").classList.contains("more-below"));
          out.rail.flyouts.all.cueAbove = await page.evaluate(() => document.getElementById("flyout").classList.contains("more-above"));
        }
        await page.keyboard.press("Escape");
      }
      const lit = () => page.$$eval(".rail-btn[data-group]", (bs) => bs.filter((b) => b.getAttribute("aria-current") === "true").map((b) => b.dataset.group));
      for (const id of ["compress", "search", "organize", "recipe", "to-word", "flatten", "merge"]) {
        await page.dispatchEvent(`.tools button[data-op="${id}"]`, "click");
        out.rail["lit-" + id] = await lit();
      }
      // The start screen's More: the tools for several files and folders; Escape goes back to it.
      await page.click("#brand");
      await page.waitForSelector("#home-host .multi .more");
      await page.click("#home-host .multi .more");
      await page.waitForSelector("#flyout:not([hidden])");
      out.rail.several = await page.evaluate(() => ({ title: document.getElementById("flyout-title").textContent,
        tools: [...document.querySelectorAll("#flyout .fly-tool")].map((b) => b.dataset.op) }));
      await page.keyboard.press("Escape");
      out.rail.several.focusBack = await page.evaluate(() => document.activeElement.classList.contains("more"));
      await ctx.close();
    }
  } catch (e) {
    out.errors.push("FATAL " + (e.stack || e));
  }
  await browser.close();
  process.stdout.write(JSON.stringify(out));
})();
