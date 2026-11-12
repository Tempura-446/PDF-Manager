// Stage 3 in a browser: one Recent list (start screen and the reader's empty
// state), the drop-first pages of the tools that start from several files,
// the panel's "Make it fillable" and "Sign it" suggestions, motion off when the
// system asks for less, toasts, no requests beyond this computer, nothing
// scrolling sideways. Run by test_polish.py:
//   node polish.js <page URL with #token=...> <flat form id> <to-sign id> <a.pdf> <b.pdf> [shots folder] [light|dark]
// Prints one JSON object with what it found. With a shots folder it also
// saves screenshots there (named <theme>-*.png).
const { chromium } = require("playwright");

(async () => {
  const [url, formId, signId, pdfA, pdfB, shots, theme = "light"] = process.argv.slice(2);
  const out = { requests: [], errors: [] };
  const shot = async (page, name) => { if (shots) { await page.waitForTimeout(600); await page.screenshot({ path: `${shots}/${theme}-${name}.png` }); } };
  const browser = await chromium.launch();
  const watch = (page) => {
    page.on("pageerror", (e) => out.errors.push(String(e.message || e)));
    page.on("console", (m) => { if (m.type() === "error" && !/Failed to load resource/.test(m.text())) out.errors.push(m.text()); });
  };
  const rows = (page, scope) => page.$$eval(`${scope} .recent-row`, (rs) => rs.map((r) => ({
    name: r.querySelector(".recent-name")?.textContent, facts: r.querySelector(".recent-facts")?.textContent || "",
    when: r.querySelector(".recent-when")?.textContent || "" })));
  const shown = (page, sel) => page.evaluate((s) => { const e = document.querySelector(s); return !!e && e.getClientRects().length > 0 && getComputedStyle(e).visibility !== "hidden"; }, sel);
  const width = (page) => page.evaluate(() => [document.documentElement.scrollWidth, innerWidth]);
  try {
    const context = await browser.newContext({ viewport: { width: 1280, height: 800 }, colorScheme: theme });
    context.on("request", (r) => out.requests.push(r.url()));
    let page = await context.newPage();
    watch(page);
    await page.goto(url);
    await page.waitForSelector("#home-host .recent-row", { timeout: 30000 });
    await page.evaluate(() => document.fonts.ready);

    // (a) One Recent list: the reader's empty state shows the start screen's rows.
    out.homeRecent = await rows(page, "#home-host");
    out.width1280Home = await width(page);
    await page.dispatchEvent('.tools button[data-op="reader"]', "click");
    await page.waitForSelector(".rd-recent:not([hidden]) .recent-row", { timeout: 30000 });
    out.readerRecent = await rows(page, ".rd-recent");
    out.readerHint = await page.evaluate(() => { const k = document.querySelector(".rd-empty .keys"); return [getComputedStyle(k).textWrap || getComputedStyle(k).textWrapStyle, k.getClientRects().length]; });
    await shot(page, "1-reader-empty-recent");
    // Remove the last one there: a toast says so; the start screen no longer lists it.
    const gone = out.readerRecent.at(-1).name;
    await page.hover(".rd-recent .recent-row:last-child");
    await page.click(".rd-recent .recent-row:last-child .recent-x");
    await page.waitForSelector(".rd-toast", { timeout: 10000 });
    out.readerToast = await page.textContent(".rd-toast > span");
    out.readerAfter = await rows(page, ".rd-recent");
    await page.click("#brand");
    await page.waitForFunction((n) => document.querySelectorAll("#home-host .recent-row").length === n, out.readerAfter.length, { timeout: 30000 });
    out.homeAfter = await rows(page, "#home-host");
    out.removed = gone;
    // Remove one on the start screen: the app's own toast, same look.
    await page.hover("#home-host .recent-row:last-child");
    await page.click("#home-host .recent-row:last-child .recent-x");
    await page.waitForSelector(".toast", { timeout: 10000 });
    out.homeToast = await page.evaluate(() => {
      const t = document.querySelector(".toast"), cs = getComputedStyle(t);
      return { text: t.querySelector("span").textContent, blur: cs.backdropFilter, radius: cs.borderTopLeftRadius, position: cs.position, anim: cs.animationName };
    });
    await shot(page, "2-start-toast");
    await page.waitForFunction(() => !document.querySelector(".toast"), null, { timeout: 10000 });
    out.toastGone = true;
    // The palette over the start screen (frosted).
    await page.keyboard.press("Control+k");
    await page.waitForTimeout(300);
    out.palette = await page.evaluate(() => { const cs = getComputedStyle(document.getElementById("palette")); return { bg: cs.backgroundColor, border: cs.borderTopColor, shadow: cs.boxShadow }; });
    await shot(page, "3-palette");
    await page.keyboard.press("Escape");

    // (b) Merge: drop first; options and Run once there are files.
    const layout = () => page.evaluate(() => {
      const vis = (s) => { const e = document.querySelector(s); return !!e && e.getClientRects().length > 0; };
      return {
        body: ["drop-first", "opts-later", "has-files"].filter((c) => document.body.classList.contains(c)),
        title: document.getElementById("title").textContent, help: document.getElementById("help").textContent,
        drop: document.getElementById("drop-title").textContent, hint: document.getElementById("drop-hint").textContent,
        choose: vis("#drop-choose"), filesHead: vis("#files-card .card-head"), options: vis("#options-card"), bar: vis(".action-bar"), run: vis("#run"),
        runDisabled: document.getElementById("run").disabled, dropHeight: Math.round(document.getElementById("drop").getBoundingClientRect().height),
        cards: document.querySelectorAll(".file-card").length, summary: document.getElementById("summary").textContent,
      };
    });
    await page.dispatchEvent('.tools button[data-op="merge"]', "click");
    await page.mouse.move(1270, 790);
    out.mergeBefore = await layout();
    out.width1280Merge = await width(page);
    await shot(page, "4-merge-before");
    // Keyboard: Tab reaches "Choose files".
    await page.evaluate(() => document.activeElement?.blur());
    for (let i = 0; i < 30 && !out.chooseByTab; i++) {
      await page.keyboard.press("Tab");
      out.chooseByTab = await page.evaluate(() => document.activeElement?.id === "drop-choose");
    }
    await page.setInputFiles("#file-input", pdfA);
    await page.waitForSelector(".file-card");
    out.mergeOne = await layout();
    await page.setInputFiles("#file-input", pdfB);
    await page.waitForFunction(() => document.querySelectorAll(".file-card").length === 2 && !document.querySelector(".file-card .loading"));
    out.mergeAfter = await layout();
    await shot(page, "5-merge-after");
    await page.click("#run");
    await page.waitForSelector(".result-actions", { timeout: 60000 });
    out.mergeResult = await page.evaluate(() => getComputedStyle(document.getElementById("result")).animationName);
    await page.evaluate(() => startOver());  // (files carry over from one tool to the next)
    for (const id of ["compare", "interleave", "from-images", "from-office", "web", "organize", "split"]) {
      await page.dispatchEvent(`.tools button[data-op="${id}"]`, "click");
      out["page_" + id] = await layout();
    }
    // Rotate (the end-to-end script's tool): the same page as before, and its steps.
    await page.dispatchEvent('.tools button[data-op="rotate"]', "click");
    out.rotate = await layout();
    await page.setInputFiles("#file-input", pdfA);
    await page.waitForSelector(".file-card", { timeout: 30000 });
    await page.click("#run");
    await page.waitForSelector(".result-actions", { timeout: 60000 });
    out.rotateDone = await shown(page, "#result");
    await page.setViewportSize({ width: 1000, height: 800 });
    await page.dispatchEvent('.tools button[data-op="merge"]', "click");
    out.width1000Merge = await width(page);
    await page.click("#brand");
    await page.waitForSelector("#home-host .recent-row, #home-host .start-empty");
    out.width1000Home = await width(page);
    await page.setViewportSize({ width: 1280, height: 800 });
    await page.close();

    // (c) A printed form: "Make it fillable" in the panel, and the bar still there.
    page = await context.newPage();
    watch(page);
    await page.goto(url + "&open=" + formId);
    await page.waitForSelector(".page canvas", { timeout: 60000 });
    await page.waitForSelector('.rd-side .fp-card[data-op="fillable"]', { timeout: 60000 });
    await page.waitForSelector(".rd-side .fp-facts:not([aria-busy])", { state: "attached", timeout: 30000 });
    out.form = await page.evaluate(() => ({
      suggestions: [...document.querySelectorAll(".rd-side .fp-sec:not([hidden]) .fp-card-head strong")].map((s) => s.textContent),
      why: document.querySelector('.fp-card[data-op="fillable"] .fp-card-head span span')?.textContent,
      bar: !document.querySelector(".rd-formbar").hidden,
      barText: document.querySelector(".rd-formbar")?.textContent,
    }));
    out.width1280Reader = await width(page);
    await shot(page, "6-panel-fillable");
    await page.click('.fp-card-head[data-op="fillable"]');
    await page.waitForSelector(".rd-toast", { timeout: 60000 });
    out.formToast = await page.textContent(".rd-toast");
    await page.waitForFunction(() => !document.querySelector('.fp-card[data-op="fillable"]'), null, { timeout: 30000 });
    out.formAfter = await page.evaluate(() => ({ card: !!document.querySelector('.fp-card[data-op="fillable"]'), bar: !document.querySelector(".rd-formbar").hidden,
      dirty: !!document.querySelector(".rd-dot:not([hidden])") || document.querySelector(".rd-savenow") !== null }));
    // Add a password in the panel: the long "Changes" choice.
    await page.click('.fp-tool[data-op="encrypt"]');
    await page.waitForTimeout(500);
    out.changes = await page.evaluate(() => {
      const s = document.getElementById("fp-encrypt-changes"), cs = getComputedStyle(s);
      const c = document.createElement("canvas").getContext("2d");
      c.font = `${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`;
      const room = s.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
      return { text: s.selectedOptions[0].textContent, fontSize: cs.fontSize, room: Math.round(room),
        needs: Math.round(c.measureText(s.selectedOptions[0].textContent).width), long: s.closest(".field").classList.contains("fp-long") };
    });
    await page.selectOption("#fp-encrypt-changes", "any");
    await page.evaluate(() => document.getElementById("fp-opts-encrypt").scrollIntoView({ block: "center" }));
    await shot(page, "7-panel-password");
    await page.close();

    // (c) A file with an empty signature field: "Sign it", which opens the signing options.
    page = await context.newPage();
    watch(page);
    await page.goto(url + "&open=" + signId);
    await page.waitForSelector(".page canvas", { timeout: 60000 });
    await page.waitForSelector('.rd-side .fp-card-head[data-op="sign-cert"]', { timeout: 30000 });
    out.sign = await page.evaluate(() => [...document.querySelectorAll(".rd-side .fp-sec:not([hidden]) .fp-card-head strong")].map((s) => s.textContent));
    await page.click('.fp-card-head[data-op="sign-cert"]');
    await page.waitForSelector("#fp-opts-sign-cert .fp-run", { timeout: 10000 });
    out.signOpen = await page.evaluate(() => ({
      expanded: document.querySelector('.fp-card-head[data-op="sign-cert"]').getAttribute("aria-expanded"),
      grid: document.querySelector('.fp-tool[data-op="sign-cert"]').getAttribute("aria-expanded"),
      pfx: !!document.getElementById("fp-sign-cert-pfx"), inCard: !!document.querySelector('.fp-card[data-op="sign-cert"] #fp-opts-sign-cert'),
      ids: document.querySelectorAll("#fp-opts-sign-cert").length,
    }));
    // The common Sign button now opens and closes the same options.
    await page.click('.fp-tool[data-op="sign-cert"]');
    await page.waitForTimeout(500);
    out.signClosed = await page.evaluate(() => [document.querySelector('.fp-card-head[data-op="sign-cert"]').getAttribute("aria-expanded"),
      document.getElementById("fp-opts-sign-cert").hidden]);
    // A result opened beside the original: the note's link back, focused from the keyboard.
    await page.click('.fp-tool[data-op="encrypt"]');
    await page.fill("#fp-encrypt-new_password", "secret");
    await page.click("#fp-opts-encrypt .fp-run");
    await page.waitForSelector(".fp-note .link-btn", { timeout: 60000 });
    await page.keyboard.press("Shift");
    await page.evaluate(() => document.querySelector(".fp-note .link-btn").focus());
    out.noteLink = await page.evaluate(() => {
      const b = document.querySelector(".fp-note .link-btn"), cs = getComputedStyle(b);
      return { focusVisible: b.matches(":focus-visible"), offset: cs.outlineOffset, height: Math.round(b.getBoundingClientRect().height),
        line: parseFloat(getComputedStyle(b.closest(".fp-done")).lineHeight) };
    });
    if (shots) {
      await page.waitForTimeout(600);
      const box = await page.$(".fp-note");
      await box.screenshot({ path: `${shots}/${theme}-8-note-focus.png` });
    }
    await page.close();
    await context.close();

    // (d) Less motion: no animation or transition anywhere it would show.
    const calm = await browser.newContext({ viewport: { width: 1280, height: 800 }, colorScheme: theme, reducedMotion: "reduce" });
    calm.on("request", (r) => out.requests.push(r.url()));
    page = await calm.newPage();
    watch(page);
    const timing = (sel) => page.evaluate((s) => { const e = document.querySelector(s); if (!e) return null; const cs = getComputedStyle(e); return [cs.animationDuration, cs.transitionDuration]; }, sel);
    await page.goto(url);
    await page.waitForSelector("#home-host .recent-row, #home-host .start-empty", { timeout: 30000 });
    out.calm = {};
    await page.click('.rail-btn[data-group="pages"]');
    out.calm.flyout = await timing("#flyout");
    await page.keyboard.press("Escape");
    await page.keyboard.press("Control+k");
    out.calm.palette = await timing("#palette");
    await page.keyboard.press("Escape");
    await page.evaluate(() => appToast("Saved"));
    out.calm.toast = await timing(".toast");
    await page.close();
    page = await calm.newPage();
    watch(page);
    await page.goto(url + "&open=" + signId);
    await page.waitForSelector('.rd-side .fp-card-head[data-op="sign-cert"]', { timeout: 60000 });
    out.calm.card = await timing('.fp-card[data-op="sign-cert"]');
    await page.click('.fp-card-head[data-op="sign-cert"]');
    out.calm.opts = await page.evaluate(() => { const b = document.getElementById("fp-opts-sign-cert"); return [b.getAnimations().length, b.classList.contains("sliding"), b.hidden]; });
    await page.evaluate(() => reader.toast("Hello"));
    out.calm.readerToast = await timing(".rd-toast");
    out.calm.progress = await page.evaluate(() => {
      const bar = document.querySelector("#fp-opts-sign-cert .fp-bar i");
      return bar ? getComputedStyle(bar).animationName : null;
    });
    await page.close();
    await calm.close();
  } finally {
    await browser.close();
  }
  console.log(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(1); });
