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
  } catch (e) {
    out.errors.push("FATAL " + (e.stack || e));
  }
  await browser.close();
  process.stdout.write(JSON.stringify(out));
})();
