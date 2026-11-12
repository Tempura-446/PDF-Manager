// Fixes from the Windows check of 0.22.1, in a browser: the file chooser
// from the keyboard, Undo toasts that wait, forced colours (high contrast),
// Settings' buttons at the bottom, the check row, the damaged-file message,
// the reader's password label and a file opened twice. Run by
// test_windows_fixes.py:
//   node winfix.js <page URL with #token=...> <JSON with ids and paths>
// Prints one JSON object with what it found.
const { chromium } = require("playwright");
const path = require("path");

(async () => {
  const [url, extraJson] = process.argv.slice(2);
  const cfg = JSON.parse(extraJson);
  const out = { errors: [] };
  let step = "";
  const browser = await chromium.launch();
  const watch = (page) => {
    page.on("pageerror", (e) => out.errors.push(step + ": " + String(e.message || e)));
    page.on("console", (m) => { if (m.type() === "error" && !/Failed to load resource/.test(m.text())) out.errors.push(step + ": " + m.text()); });
  };
  const shot = async (page, name) => { if (cfg.shots) await page.screenshot({ path: path.join(cfg.shots, name + ".png") }); };
  const fresh = async (opts = {}) => {
    const ctx = await browser.newContext({ viewport: { width: 1180, height: 820 }, ...opts });
    const page = await ctx.newPage();
    watch(page);
    page.on("filechooser", () => {});  // file choosers are caught from the start (none opens for real)
    return { ctx, page };
  };
  const home = async (page, extra = "") => {
    await page.goto(url + extra);
    if (!extra) await page.waitForSelector("#home-host .recent-row", { timeout: 30000 });
  };
  const chooser = async (page, press) => {
    try { const [fc] = await Promise.all([page.waitForEvent("filechooser", { timeout: 5000 }), press()]); return fc; } catch (e) { return null; }
  };
  const tabTo = async (page, test, max = 80) => {
    await page.evaluate(() => document.activeElement?.blur());
    for (let i = 0; i < max; i++) {
      await page.keyboard.press("Tab");
      if (await page.evaluate(test)) return true;
    }
    return false;
  };

  try {
    // ---- A. the file chooser from the keyboard ----
    step = "A. the file chooser from the keyboard";
    let { ctx, page } = await fresh();
    await home(page);
    await page.dispatchEvent('.tools button[data-op="compress"]', "click");
    await page.mouse.move(1170, 400);
    const dropFocused = await tabTo(page, () => document.activeElement?.id === "drop");
    out.dropTab = await page.evaluate((focused) => {
      const d = document.getElementById("drop"), cs = getComputedStyle(d), r = d.getBoundingClientRect();
      return { focused, role: d.getAttribute("role"), name: document.getElementById(d.getAttribute("aria-labelledby")).textContent,
        ring: [cs.outlineStyle, cs.outlineWidth], visible: d.matches(":focus-visible") && r.height > 20 };
    }, dropFocused);
    out.dropEnter = !!(await chooser(page, () => page.keyboard.press("Enter")));
    const fc = await chooser(page, () => page.keyboard.press(" "));
    out.dropSpace = !!fc;
    if (fc) await fc.setFiles(cfg.small);
    await page.waitForSelector(".file-card:not(:has(.loading))", { timeout: 30000 });
    await page.focus("#drop");
    out.addMore = { name: await page.textContent("#drop-title"), focused: await page.evaluate(() => document.activeElement.id === "drop" && document.getElementById("drop").tabIndex === 0),
      chooser: !!(await chooser(page, () => page.keyboard.press("Enter"))) };
    // A click: no focus ring for the mouse.
    await page.evaluate(() => document.activeElement?.blur());
    await chooser(page, () => page.click("#drop strong"));
    out.mouseLook = await page.evaluate(() => ({ outline: getComputedStyle(document.getElementById("drop")).outlineStyle }));
    // Drop-first tools: "Choose files" is the stop; with files, the drop area again.
    for (const [key, op] of [["mergeFirst", "merge"], ["imagesFirst", "from-images"]]) {
      await page.evaluate(() => startOver());
      await page.dispatchEvent(`.tools button[data-op="${op}"]`, "click");
      const focused = await tabTo(page, () => document.activeElement?.id === "drop-choose");
      out[key] = { chooseFocused: focused, dropTabbable: await page.evaluate(() => document.getElementById("drop").hasAttribute("tabindex")),
        chooser: !!(await chooser(page, () => page.keyboard.press("Enter"))) };
    }
    await page.evaluate(() => startOver());
    await page.dispatchEvent('.tools button[data-op="merge"]', "click");
    await page.setInputFiles("#file-input", cfg.small);
    await page.waitForSelector(".file-card");
    out.mergeAddMore = await page.evaluate(() => ({ dropTabbable: document.getElementById("drop").tabIndex === 0 && document.getElementById("drop").getAttribute("role") === "button",
      chooseHidden: !document.getElementById("drop-choose").getClientRects().length }));

    // F2. the check row on Compress, light and dark (screenshots).
    await page.evaluate(() => startOver());
    await page.dispatchEvent('.tools button[data-op="compress"]', "click");
    await page.setInputFiles("#file-input", cfg.small);
    await page.waitForSelector(".file-card:not(:has(.loading))", { timeout: 30000 });
    out.checkRow = await page.evaluate(() => {
      const row = document.querySelector('.field.check[data-key="sharp_scans"]'), lab = row.querySelector("label"), hint = row.querySelector(".hint");
      const range = document.createRange(); range.selectNodeContents(lab);
      const lines = new Set([...range.getClientRects()].map((r) => Math.round(r.top))).size;
      return { labelLines: lines, hintBelow: hint.getBoundingClientRect().top >= lab.getBoundingClientRect().bottom - 1, labelWidth: lab.getBoundingClientRect().width };
    });
    await page.locator('.field.check[data-key="sharp_scans"]').scrollIntoViewIfNeeded();
    await shot(page, "compress-light");

    // F3. a damaged file: said once in a polite status.
    await page.evaluate(() => startOver());
    await page.setInputFiles("#file-input", cfg.broken);
    await page.waitForSelector(".file-card .badge.warn", { timeout: 30000 });
    out.live = await page.evaluate(async () => {
      const live = document.getElementById("summary-live");
      let repaints = 0;
      const mo = new MutationObserver((rs) => { repaints += rs.length; });
      mo.observe(live, { childList: true, characterData: true, subtree: true });
      updateSummary(); updateSummary(); renderFiles();
      await new Promise((r) => setTimeout(r, 100));
      mo.disconnect();
      return { role: live.getAttribute("role"), text: live.textContent, repaints };
    });
    await ctx.close();

    ({ ctx, page } = await fresh({ colorScheme: "dark" }));
    await home(page);
    await page.dispatchEvent('.tools button[data-op="compress"]', "click");
    await page.setInputFiles("#file-input", cfg.small);
    await page.waitForSelector(".file-card:not(:has(.loading))", { timeout: 30000 });
    await page.locator('.field.check[data-key="sharp_scans"]').scrollIntoViewIfNeeded();
    await shot(page, "compress-dark");
    await ctx.close();

    // ---- F1. Settings at 1180 × 640 ----
    step = "F1. Settings at 1180 × 640";
    for (const theme of ["light", "dark"]) {
      ({ ctx, page } = await fresh({ viewport: { width: 1180, height: 640 }, colorScheme: theme }));
      await home(page);
      await page.click("#settings-btn");
      await page.waitForSelector("#settings-dialog[open]");
      const measure = () => page.evaluate(() => {
        const f = document.getElementById("settings-form"), b = f.querySelector(".dlg-buttons"), d = document.getElementById("settings-dialog");
        return { scrolls: f.scrollHeight > f.clientHeight + 1, formBottom: f.getBoundingClientRect().bottom, buttonsBottom: b.getBoundingClientRect().bottom, dialogBottom: d.getBoundingClientRect().bottom };
      });
      if (theme === "light") out.settings = await measure();
      await page.waitForTimeout(600);  // (its opening fade done)
      await shot(page, "settings-" + theme);
      if (theme === "light") {
        await page.evaluate(() => { const f = document.getElementById("settings-form"); f.scrollTop = f.scrollHeight; });
        const end = await measure();
        out.settingsEnd = end;
      }
      await ctx.close();
    }

    // ---- C. Undo toasts: 10 s, held while pointed at or focused ----
    step = "C. Undo toasts: 10 s, held while pointed at or focused";
    ({ ctx, page } = await fresh());
    await page.clock.install();
    await home(page);
    const there = () => page.evaluate(() => !!document.querySelector(".toast:not(.out)"));
    const t = (out.toast = {});
    const undo = () => page.evaluate(() => appToast("Removed Report.pdf from Recent", 5000, ["Undo", () => {}]));
    await page.mouse.move(600, 100);
    await undo();
    await page.clock.runFor(6000); t.at6 = await there();
    await page.clock.runFor(3000); t.at9 = await there();
    await page.clock.runFor(2000); t.at11 = await there();
    await undo();
    await page.clock.runFor(3000);
    await page.hover(".toast");
    await page.clock.runFor(17000); t.hoverAt20 = await there();
    await page.mouse.move(600, 100);
    await page.clock.runFor(2000); t.leftAt22 = await there();
    await page.clock.runFor(18000); t.leftAt40 = await there();
    await undo();
    await page.focus(".toast button");
    await page.clock.runFor(30000); t.focusAt30 = await there();
    await page.evaluate(() => document.activeElement.blur());
    await page.clock.runFor(15000); t.blurAt45 = await there();
    await page.evaluate(() => appToast("Saved", 3000));
    await page.clock.runFor(4000); t.plainGone = !(await there());
    await ctx.close();

    ({ ctx, page } = await fresh());
    await page.clock.install();
    await home(page, "&open=" + cfg.smallId);
    await page.waitForSelector(".pdfViewer .page canvas", { timeout: 60000 });
    const rdThere = () => page.evaluate(() => !!document.querySelector(".rd-toast:not(.out)"));
    const rt = (out.rdToast = {});
    await page.mouse.move(600, 100);
    await page.evaluate(() => reader.toast("Deleted page 2", 5000, ["Undo", () => {}]));
    await page.clock.runFor(9000); rt.at9 = await rdThere();
    await page.clock.runFor(3000); rt.at12 = await rdThere();
    await page.evaluate(() => reader.toast("Deleted page 2", 5000, ["Undo", () => {}]));
    await page.clock.runFor(3000);
    await page.hover(".rd-toast");
    await page.clock.runFor(17000); rt.hoverAt20 = await rdThere();
    await page.mouse.move(600, 100);
    await page.clock.runFor(20000); rt.leftAt40 = await rdThere();

    // A. Insert PDF in the sidebar: a button that opens the chooser from the keyboard.
    await page.evaluate(() => { if (reader.sidebar.hidden) reader.toggleSidebar(); });
    await page.waitForSelector(".rd-pagebar button[aria-label^='Insert a PDF']", { timeout: 20000 });
    await page.focus(".rd-pagebar button[aria-label^='Insert a PDF']");
    out.insertPdf = { tag: await page.evaluate(() => document.activeElement.tagName), chooser: !!(await chooser(page, () => page.keyboard.press("Enter"))) };
    await ctx.close();

    // ---- E. forced colours (Windows high contrast) ----
    step = "E. forced colours (Windows high contrast)";
    for (const theme of ["light", "dark"]) {
      ({ ctx, page } = await fresh({ colorScheme: theme }));
      await page.emulateMedia({ forcedColors: "active" });
      await home(page, "&open=" + cfg.smallId);
      await page.waitForSelector(".pdfViewer .page canvas", { timeout: 60000 });
      await page.evaluate(() => { if (reader.sidebar.hidden) reader.toggleSidebar(); });
      await page.click(".rd-toolbar button[title^='Annotate']");
      await page.waitForSelector(".rd-annotbar:not([hidden])");
      await page.click(".rd-annotbar button[data-mode='HIGHLIGHT']");
      await page.mouse.move(600, 500);
      await page.waitForTimeout(800);  // background transitions done
      if (theme === "light") {
        out.forced = await page.evaluate(() => {
          const bg = (e) => e ? getComputedStyle(e).backgroundColor : null;
          const bar = document.querySelector(".rd-annotbar");
          const zoom = document.querySelector(".rd-zoom");
          const swatch = document.querySelector(".rd-swatch");
          return { active: matchMedia("(forced-colors: active)").matches,
            pressedBg: bg(document.querySelector(".rd-toolbar button[title^='Annotate'][aria-pressed='true']")),
            plainBg: bg(document.querySelector(".rd-toolbar button[aria-label^='Find in document']")),
            annotPressed: bg(bar.querySelector("button[aria-pressed='true'][data-mode]")), annotPlain: bg(bar.querySelector("button[aria-pressed='false'][data-mode]")),
            tabOn: bg(document.querySelector(".rd-tab[aria-selected='true']")), tabOff: bg(document.querySelector(".rd-tab[aria-selected='false']")),
            zoomAppearance: zoom ? getComputedStyle(zoom).appearance : null, swatchAdjust: swatch ? getComputedStyle(swatch).forcedColorAdjust : null };
        });
      }
      await shot(page, "reader-forced-" + theme);
      if (theme === "light") {
        await ctx.close();  // (a tool page of its own: leaving a reader that has just opened makes pdf.js complain)
        ({ ctx, page } = await fresh({ colorScheme: theme }));
        await page.emulateMedia({ forcedColors: "active" });
        await home(page);
        await page.dispatchEvent('.tools button[data-op="compress"]', "click");
        await page.waitForSelector("#f-level");
        await page.mouse.move(900, 300);
        await page.waitForTimeout(800);
        Object.assign(out.forced, await page.evaluate(() => {
          const s = getComputedStyle(document.getElementById("f-level"));
          const on = document.querySelector(".rail-btn[aria-current='true'], .rail-btn[aria-expanded='true']") , off = document.querySelector(".rail-btn:not([aria-current='true']):not([aria-expanded='true'])");
          return { selectAppearance: s.appearance, selectImage: s.backgroundImage,
            railOn: on ? getComputedStyle(on).backgroundColor : "none-current", railOff: off ? getComputedStyle(off).backgroundColor : null };
        }));
        await page.emulateMedia({ forcedColors: "none" });
        out.forced.normalAppearance = await page.evaluate(() => getComputedStyle(document.getElementById("f-level")).appearance);
        await page.emulateMedia({ forcedColors: "active" });
        await shot(page, "compress-forced");
      }
      await ctx.close();
    }

    // ---- F4. the reader's password label ----
    step = "F4. the reader's password label";
    ({ ctx, page } = await fresh());
    await home(page, "&open=" + cfg.locked);
    await page.waitForSelector("dialog.rd-dialog[open] input[type=password]", { timeout: 60000 });
    out.pwLabel = await page.evaluate(() => {
      const d = document.querySelector("dialog.rd-dialog[open]"), i = d.querySelector("input[type=password]"), tog = d.querySelector(".pw-toggle");
      const lab = i.labels[0];
      return { labelText: lab ? lab.textContent.trim() : null, buttonInLabel: !!(lab && lab.contains(tog)), inputLabelled: i.labels.length === 1, ariaLabel: i.getAttribute("aria-label") };
    });
    await ctx.close();

    // ---- F5. a file opened from a tool's card, then from Recent: one tab ----
    step = "F5. a file opened from a tool's card, then from Recent: one tab";
    for (const [key, file, id] of [["dupSmall", cfg.small, cfg.smallId], ["dupLarge", cfg.large, cfg.largeId]]) {
      ({ ctx, page } = await fresh());
      await home(page);
      await page.dispatchEvent('.tools button[data-op="compress"]', "click");
      await page.setInputFiles("#file-input", file);
      await page.waitForSelector(".file-card:not(:has(.loading))", { timeout: 60000 });
      await page.dispatchEvent(".file-card button[title='Open in reader']", "click");
      await page.waitForSelector(".pdfViewer .page canvas", { timeout: 60000 });
      await page.waitForFunction(() => reader.tabs.length === 1, null, { timeout: 30000 });
      await page.evaluate((id) => openInReaderById([id]), id);
      await page.waitForTimeout(800);
      out[key] = await page.evaluate(() => ({ tabs: reader.tabs.length, names: reader.tabs.map((t) => t.name) }));
      await ctx.close();
    }
  } catch (e) {
    out.errors.push(String(e.stack || e));
  } finally {
    await browser.close();
  }
  process.stdout.write(JSON.stringify(out));
})();
