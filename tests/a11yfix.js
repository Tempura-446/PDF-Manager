// Accessibility and usability fixes in a browser: focus clears the sticky
// action bar, focus rings, field borders, the annotate bar and Settings fit,
// focus after a search, unreadable files, skip links, Escape, Undo for
// Recent, the password field and target sizes. Run by test_a11y_fixes.py:
//   node a11yfix.js <page URL with #token=...> <JSON with ids and paths>
// Prints one JSON object with what it found.
const { chromium } = require("playwright");

(async () => {
  const [url, extraJson] = process.argv.slice(2);
  const cfg = JSON.parse(extraJson);
  const out = { errors: [] };
  const browser = await chromium.launch();
  const watch = (page) => {
    page.on("pageerror", (e) => out.errors.push(String(e.message || e)));
    page.on("console", (m) => { if (m.type() === "error" && !/Failed to load resource/.test(m.text())) out.errors.push(m.text()); });
  };
  const active = (page) => page.evaluate(() => {
    const a = document.activeElement;
    return { tag: a.tagName, id: a.id, cls: String(a.className || ""), label: a.getAttribute("aria-label") || a.title || "", text: (a.textContent || "").trim().slice(0, 80) };
  });
  // WCAG contrast of two computed colours (rgb()/rgba() over white or the given base).
  const RATIO = `(() => {
    const parse = (c) => { const n = (c.match(/[\\d.]+/g) || []).map(Number);  // rgb(0-255) or color(srgb 0-1) (from color-mix)
      return c.startsWith("color(") ? [...n.slice(0, 3).map((v) => v * 255), ...n.slice(3)] : n; };
    const lum = ([r, g, b]) => { const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }; return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b); };
    const over = (top, base) => { const [r, g, b, a = 1] = parse(top); const [R, G, B] = base; return [r * a + R * (1 - a), g * a + G * (1 - a), b * a + B * (1 - a)]; };
    const bgOf = (el) => { const layers = []; for (let e = el; e; e = e.parentElement) { const c = getComputedStyle(e).backgroundColor; const p = parse(c); if (p.length && (p[3] ?? 1) > 0) { layers.push(c); if ((p[3] ?? 1) >= 1) break; } }
      let base = parse(getComputedStyle(document.body).backgroundColor).slice(0, 3); for (const c of layers.reverse()) base = over(c, base); return base; };
    const ratio = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
    return { bgOf, ratio, over, parse };
  })()`;
  const borderRatio = (page, sel) => page.evaluate(([sel, RATIO]) => {
    const R = eval(RATIO);
    const e = document.querySelector(sel);
    if (!e) return null;
    const fill = R.bgOf(e), around = R.bgOf(e.parentElement);
    const border = R.over(getComputedStyle(e).borderTopColor, around);
    return Math.round(Math.min(R.ratio(border, fill), R.ratio(border, around)) * 100) / 100;
  }, [sel, RATIO]);
  const size = (page, sel) => page.evaluate((s) => { const e = document.querySelector(s); if (!e) return null; const r = e.getBoundingClientRect(); return [Math.round(r.width * 10) / 10, Math.round(r.height * 10) / 10]; }, sel);
  const ring = (page) => page.evaluate(() => { const cs = getComputedStyle(document.activeElement); return [cs.outlineStyle, cs.outlineWidth]; });

  try {
    for (const theme of ["light", "dark"]) {
      const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 }, colorScheme: theme });
      const page = await ctx.newPage();
      watch(page);
      await page.goto(url);
      await page.waitForSelector("#home-host .recent-row", { timeout: 30000 });
      const t = (out[theme] = {});
      await page.dispatchEvent('.tools button[data-op="search"]', "click");
      t.query = await borderRatio(page, "#f-query");
      t.how = await borderRatio(page, "#f-how");
      // The field focus ring: solid, 2px.
      await page.focus("#f-query");
      await page.keyboard.press("End");
      t.fieldRing = await page.evaluate(() => { const cs = getComputedStyle(document.activeElement); return [cs.outlineStyle, cs.outlineWidth, cs.boxShadow]; });
      await page.click("#settings-btn");
      await page.waitForSelector("#settings-dialog[open]");
      t.repo = await borderRatio(page, "#set-repo");
      t.settings = await page.evaluate(() => {
        const d = document.getElementById("settings-dialog").getBoundingClientRect(), s = document.getElementById("set-save"), r = s.getBoundingClientRect();
        const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
        return { dialogBottom: d.bottom, saveBottom: r.bottom, innerHeight, hit: hit === s || s.contains(hit) };
      });
      await page.keyboard.press("Escape");
      await ctx.close();
    }

    const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
    let page = await ctx.newPage();
    watch(page);
    await page.goto(url);
    await page.waitForSelector("#home-host .recent-row", { timeout: 30000 });

    // Skip link: the first Tab stop, shown when focused; Enter moves to the main content.
    await page.keyboard.press("Tab");
    out.skip = await page.evaluate(() => { const a = document.activeElement, r = a.getBoundingClientRect(); return { cls: String(a.className), text: a.textContent.trim(), visible: r.width > 20 && r.height > 20 && r.top >= 0 }; });
    await page.keyboard.press("Enter");
    out.skipTo = await page.evaluate(() => { const a = document.activeElement; return { inMain: !!a.closest("main"), tag: a.tagName, hash: location.hash.includes("token=") }; });

    // Recent: remove one, the toast offers Undo; Undo brings it back.
    const names = () => page.$$eval("#home-host .recent-row .recent-name", (rs) => rs.map((r) => r.textContent));
    out.recentBefore = await names();
    await page.focus("#home-host .recent-row:first-child .recent-open");
    out.recentHoverOpacity = await page.evaluate(() => { const r = document.querySelector("#home-host .recent-row"); return getComputedStyle(r.querySelector(".recent-x")).opacity; });
    out.recentX = await size(page, "#home-host .recent-row .recent-x");
    await page.hover("#home-host .recent-row:first-child");
    await page.click("#home-host .recent-row:first-child .recent-x");
    await page.waitForSelector(".toast button", { timeout: 10000 });
    out.recentToast = await page.evaluate(() => ({ text: document.querySelector(".toast > span").textContent, button: document.querySelector(".toast button").textContent }));
    out.toastUndo = await size(page, ".toast button");
    out.recentRemoved = await names();
    await page.click(".toast button");
    await page.waitForFunction((n) => document.querySelectorAll("#home-host .recent-row").length === n, out.recentBefore.length, { timeout: 10000 });
    out.recentAfter = await names();
    out.recentServer = await page.evaluate(async () => (await (await fetch("/api/recent", { headers: { "X-Pdfm-Token": token } })).json()).files.map((f) => f.name));

    // Search: focus moves to the summary; the last row, reached with Tab, clears the action bar.
    await page.dispatchEvent('.tools button[data-op="search"]', "click");
    await page.fill("#f-directory", cfg.docs);
    await page.fill("#f-query", "balance due");
    await page.selectOption("#f-how", "exact");
    await page.click("#run");
    await page.waitForSelector(".search-results", { timeout: 60000 });
    await page.waitForTimeout(500);
    out.searchFocus = await page.evaluate(() => { const a = document.activeElement, bar = document.querySelector(".action-bar").getBoundingClientRect(), r = a.getBoundingClientRect();
      return { id: a.id, inResult: !!a.closest("#result"), text: a.textContent.trim().slice(0, 60), top: r.top, bottom: r.bottom, barTop: bar.top }; });
    out.rows = await page.$$eval(".search-row", (rs) => rs.map((r) => ({ role: r.getAttribute("role"), label: r.getAttribute("aria-label") })));
    let last = null;
    for (let i = 0; i < 60; i++) {
      await page.keyboard.press("Tab");
      const info = await page.evaluate(() => { const a = document.activeElement; const rows = [...document.querySelectorAll(".search-row")]; return { idx: rows.indexOf(a), n: rows.length }; });
      if (info.idx === info.n - 1) { last = info; break; }
    }
    out.lastRow = last && await page.evaluate(() => { const a = document.activeElement, r = a.getBoundingClientRect(), bar = document.querySelector(".action-bar").getBoundingClientRect(); return { top: r.top, bottom: r.bottom, barTop: bar.top }; });
    out.rowRing = await ring(page);

    // Required field: the message is next to the field too. (The form is folded above the
    // results since 0.23: Edit opens it again.)
    await page.click("#form-recap .link-btn");
    await page.fill("#f-query", "");
    await page.click("#run");
    out.required = await page.evaluate(() => { const f = document.getElementById("f-query"); const id = f.getAttribute("aria-describedby") || ""; const m = id && document.getElementById(id.split(" ").find((x) => document.getElementById(x)?.classList.contains("field-err")) || "");
      return { invalid: f.getAttribute("aria-invalid"), msg: m ? m.textContent : null, near: m ? !!m.closest(".field") : false, banner: !document.getElementById("error").hidden }; });

    // Unreadable file: not ready; Run waits; a plain message.
    await page.evaluate(() => startOver());
    await page.dispatchEvent('.tools button[data-op="compress"]', "click");
    await page.setInputFiles("#file-input", cfg.broken);
    await page.waitForSelector(".file-card .badge.warn", { timeout: 30000 });
    out.broken = await page.evaluate(() => ({ run: document.getElementById("run").disabled, summary: document.getElementById("summary").textContent }));
    await page.keyboard.press("Control+Enter");
    await page.waitForTimeout(300);
    out.brokenRun = await page.evaluate(() => { const e = document.getElementById("error"); return { error: e.hidden ? null : e.querySelector("span").firstChild.textContent, details: !!e.querySelector("details:not([open])"), repair: !!e.querySelector("button.link-btn"), focus: document.activeElement.id }; });
    out.plain = await page.evaluate(() => plainError("broken.pdf is not a readable PDF (Stream has ended unexpectedly)"));
    // Disabled buttons don't say "busy" when nothing runs.
    out.disabledCursor = await page.evaluate(() => getComputedStyle(document.getElementById("run")).cursor);
    // Repair is for such files: it can run.
    await page.dispatchEvent('.tools button[data-op="repair"]', "click");
    out.repairRun = await page.evaluate(() => document.getElementById("run").disabled);
    // A good file and the broken one: the good one is ready.
    await page.dispatchEvent('.tools button[data-op="compress"]', "click");
    await page.setInputFiles("#file-input", cfg.good);
    await page.waitForFunction(() => document.querySelectorAll(".file-card").length === 2 && !document.querySelector(".file-card .loading"), null, { timeout: 30000 });
    out.mixed = await page.evaluate(() => ({ run: document.getElementById("run").disabled, summary: document.getElementById("summary").textContent }));
    out.cardBtn = await size(page, ".card-actions .chip-btn");
    // The password row on a tool page: a visible label and a show/hide toggle.
    await page.evaluate(() => { document.getElementById("pw-box").open = true; });
    out.pwRow = await page.evaluate(() => { const i = document.getElementById("pw"); const lab = document.querySelector('label[for="pw"]'); const tog = document.querySelector("#pw-box .pw-toggle");
      return { label: lab && lab.getClientRects().length ? lab.textContent.trim() : null, autocomplete: i.getAttribute("autocomplete"), toggle: !!tog }; });
    await page.close();

    // Reader: annotate bar fits; Escape; find; menu focus; skip link; text layers; page view focus.
    for (const [w, h] of [[1024, 700], [1280, 800], [1440, 900], [1920, 1080]]) {
      const c = await browser.newContext({ viewport: { width: w, height: h } });
      const p = await c.newPage();
      watch(p);
      await p.goto(url + "&open=" + cfg.text);
      await p.waitForSelector(".pdfViewer .page canvas", { timeout: 60000 });
      await p.click(".rd-toolbar button[title^='Annotate']");
      await p.waitForSelector(".rd-annotbar:not([hidden])");
      out["annot" + w] = await p.evaluate(() => {
        const bar = document.querySelector(".rd-annotbar"), b = bar.getBoundingClientRect();
        const btn = (title) => [...bar.querySelectorAll("button")].find((x) => (x.title || "").startsWith(title));
        const vis = (e) => { const r = e.getBoundingClientRect(); return r.width > 0 && r.left >= b.left - 1 && r.right <= b.right + 1 && r.right <= innerWidth; };
        return { save: vis(bar.querySelector(".rd-annotsave")), close: vis(btn("Close the annotation tools")), undo: vis(btn("Undo")),
          saveWord: getComputedStyle(bar.querySelector(".rd-annotsave span")).display !== "none",
          scrollW: bar.scrollWidth, clientW: bar.clientWidth, labels: getComputedStyle(bar.querySelector(".rd-tool:not(.compact) span")).display !== "none" };
      });
      await c.close();
    }

    page = await ctx.newPage();
    watch(page);
    await page.goto(url + "&open=" + cfg.text);
    await page.waitForSelector(".pdfViewer .page canvas", { timeout: 60000 });
    await page.waitForTimeout(800);
    out.textLayerTab = await page.$$eval(".textLayer", (ls) => ls.map((l) => l.tabIndex));
    out.containerFocusOnLoad = await page.evaluate(() => ({ focused: document.activeElement.classList.contains("rd-container"), visible: document.querySelector(".rd-container").matches(":focus-visible") }));
    // The reader's skip link: Tab to it (it's in the reader), Enter goes to the page view.
    await page.focus(".rd-skip");
    out.rdSkip = await page.evaluate(() => { const r = document.activeElement.getBoundingClientRect(); return { cls: String(document.activeElement.className), visible: r.width > 20 && r.height > 10 }; });
    await page.keyboard.press("Enter");
    out.rdSkipTo = await page.evaluate(() => ({ container: document.activeElement.classList.contains("rd-container"), outline: getComputedStyle(document.activeElement).outlineStyle, offset: getComputedStyle(document.activeElement).outlineOffset }));
    // Find bar: Escape returns to the Find button.
    await page.click(".rd-toolbar button[aria-label^='Find in document']");
    await page.waitForSelector(".rd-findbar:not(.rd-annotbar):not([hidden]) input[type=search]");
    out.findInput = await borderRatio(page, ".rd-findbar input[type=search]");
    out.pageInput = await borderRatio(page, ".rd-page input");
    out.zoomInput = await borderRatio(page, ".rd-zoominput");
    await page.keyboard.press("Escape");
    out.findEsc = await active(page);
    // Annotate: first Escape clears the tool, the next closes the bar (focus back on its button).
    await page.click(".rd-toolbar button[title^='Annotate']");
    await page.click(".rd-annotbar button[data-mode='HIGHLIGHT']");
    await page.focus(".rd-container");
    await page.keyboard.press("Escape");
    out.annotEsc1 = await page.evaluate(() => ({ open: !document.querySelector(".rd-annotbar").hidden, mode: document.querySelector(".rd-annotbar button[aria-pressed='true']")?.dataset.mode }));
    await page.keyboard.press("Escape");
    out.annotEsc2 = { open: await page.evaluate(() => !document.querySelector(".rd-annotbar").hidden), focus: await active(page) };
    // The More menu: a keyboard-focused item has an outline.
    await page.focus(".rd-toolbar button[aria-label='More']");
    await page.keyboard.press("Enter");
    await page.waitForSelector(".rd-menu:not([hidden]) button");
    await page.keyboard.press("ArrowDown");
    out.menuRing = await page.evaluate(() => { const a = document.activeElement, cs = getComputedStyle(a); return { inMenu: !!a.closest(".rd-menu"), style: cs.outlineStyle, width: cs.outlineWidth }; });
    await page.keyboard.press("Escape");
    // Bookmarks: the twisty is at least 24 × 24.
    await page.evaluate(() => { if (reader.sidebar.hidden) reader.toggleSidebar(); });
    await page.click(".rd-tab:nth-child(2)").catch(() => {});
    await page.waitForSelector(".rd-outline .twisty", { timeout: 15000 }).catch(() => {});
    out.twisty = await size(page, ".rd-outline .twisty");
    await page.click(".rd-tab:nth-child(1)").catch(() => {});
    await page.waitForSelector(".rd-thumb-act", { state: "attached", timeout: 20000 }).catch(() => {});
    out.thumbAct = await size(page, ".rd-thumb .rd-thumb-act");
    // "Whole paragraph" and the reader toast's button: at least 24 tall.
    out.paragraph = await page.evaluate(() => {
      const hint = document.createElement("div"); hint.className = "rd-textedit-hint";
      const b = document.createElement("button"); b.className = "rd-textedit-mode"; b.textContent = "¶ Whole paragraph"; hint.append(b);
      document.querySelector(".rd").append(hint); const r = b.getBoundingClientRect(); b.focus(); const cs = getComputedStyle(b);
      const ring = b.matches(":focus-visible") ? cs.outlineColor === cs.color : null; hint.remove(); return [r.width, r.height, ring];
    });
    await page.evaluate(() => reader.toast("Deleted page 2", 5000, ["Undo", () => {}]));
    out.rdToastUndo = await size(page, ".rd-toast button");
    await page.close();

    // Password: a visible label, a show/hide toggle, autocomplete; a wrong one is marked; Escape returns focus.
    page = await ctx.newPage();
    watch(page);
    await page.goto(url + "&open=" + cfg.locked);
    await page.waitForSelector("dialog.rd-dialog[open] input[type=password]", { timeout: 60000 });
    out.pw = await page.evaluate(() => {
      const d = document.querySelector("dialog.rd-dialog[open]"), i = d.querySelector("input[type=password]"), lab = i.closest("label") || d.querySelector(`label[for="${i.id}"]`);
      const tog = d.querySelector(".pw-toggle");
      return { label: lab ? lab.textContent.replace(tog?.textContent || "", "").trim() : null, autocomplete: i.getAttribute("autocomplete"), toggle: tog ? tog.getAttribute("aria-pressed") : null, toggleSize: tog ? [tog.getBoundingClientRect().width, tog.getBoundingClientRect().height] : null };
    });
    await page.click("dialog.rd-dialog[open] .pw-toggle");
    out.pwShown = await page.evaluate(() => { const d = document.querySelector("dialog.rd-dialog[open]"); return [d.querySelector(".pw-toggle").getAttribute("aria-pressed"), d.querySelector(".pw-toggle").previousElementSibling.type]; });
    await page.click("dialog.rd-dialog[open] .pw-toggle");
    await page.fill("dialog.rd-dialog[open] input[type=password]", "wrong");
    await page.keyboard.press("Enter");
    await page.waitForSelector("dialog.rd-dialog[open] .err", { timeout: 30000 });
    out.pwWrong = await page.evaluate(() => { const d = document.querySelector("dialog.rd-dialog[open]"), i = d.querySelector("input[type=password]"), e = d.querySelector(".err");
      return { invalid: i.getAttribute("aria-invalid"), describedby: i.getAttribute("aria-describedby"), errId: e.id, focus: document.activeElement === i }; });
    await page.keyboard.press("Escape");
    await page.waitForTimeout(300);
    out.pwEsc = await page.evaluate(() => ({ open: !!document.querySelector("dialog.rd-dialog[open]"), focus: document.activeElement.tagName, text: document.activeElement.textContent.trim().slice(0, 40) }));
    await page.close();

    // Palette: the highlighted row's subtitle reads at 4.5:1.
    page = await ctx.newPage();
    watch(page);
    await page.goto(url);
    await page.waitForSelector("#home-host .recent-row", { timeout: 30000 });
    await page.keyboard.press("Control+k");
    await page.waitForSelector("#palette[open] .palette-item .sub");
    for (let i = 0; i < 40 && !(await page.$(".palette-item[aria-selected='true'] .sub")); i++) await page.keyboard.press("ArrowDown");
    out.paletteSub = await page.evaluate((RATIO) => { const R = eval(RATIO); const s = document.querySelector(".palette-item[aria-selected='true'] .sub"); return Math.round(R.ratio(R.over(getComputedStyle(s).color, R.bgOf(s)), R.bgOf(s)) * 100) / 100; }, RATIO);
    await page.close();
  } catch (e) {
    out.errors.push(String(e.stack || e));
  } finally {
    await browser.close();
  }
  process.stdout.write(JSON.stringify(out));
})();
