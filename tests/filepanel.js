// The reader's file panel in a browser: facts and suggestions for the shown
// file, a tool opened and run in the panel (by the file's id, or with its
// bytes), a failure, a result that isn't a PDF, the T key, a narrow window.
// Run by test_filepanel.py:
//   node filepanel.js <page URL with #token=...> <scanned id> <plain id> <a PDF to open from bytes> [shots folder] [light|dark]
// Prints one JSON object with what it found. With a shots folder it also
// saves screenshots there (named <theme>-*.png).
const { chromium } = require("playwright");

(async () => {
  const [url, scanId, plainId, uploadPath, shots, theme = "light"] = process.argv.slice(2);
  const out = { requests: [], errors: [], runs: [] };
  const shot = async (page, name) => { if (shots) { await page.waitForTimeout(700); await page.screenshot({ path: `${shots}/${theme}-${name}.png` }); } };
  const browser = await chromium.launch();
  try {
    const context = await browser.newContext({ viewport: { width: 1280, height: 800 }, colorScheme: theme });
    // What each /api/run request carried: the form's fields, and which were files.
    await context.addInitScript(() => {
      window.__runs = [];
      const real = window.fetch;
      window.fetch = function (input, init) {
        if (String(input).includes("/api/run") && init && init.body instanceof FormData) {
          window.__runs.push([...init.body.entries()].map(([k, v]) => [k, typeof v === "string" ? "text" : "file"]));
        }
        return real.apply(this, arguments);
      };
    });
    context.on("request", (r) => {
      out.requests.push(r.url());
      if (r.url().includes("/api/run") && r.method() === "POST") out.runs.push(r.postData());
    });
    const watch = (page) => {
      page.on("pageerror", (e) => out.errors.push(String(e.message || e)));
      page.on("console", (m) => { if (m.type() === "error" && !/Failed to load resource/.test(m.text())) out.errors.push(m.text()); });
    };
    const open = async (id, page = null) => {
      page = page || await context.newPage();
      watch(page);
      await page.goto(url + "&open=" + id);
      await page.waitForSelector(".page canvas", { timeout: 60000 });
      return page;
    };
    const factsShown = (page) => page.waitForSelector(".rd-side .fp-facts:not([aria-busy])", { state: "attached", timeout: 30000 });
    const panel = (page) => page.evaluate(() => {
      const side = document.querySelector(".rd-side");
      const visible = (e) => !!e && !e.closest("[hidden]") && e.getClientRects().length > 0;
      return {
        visible: visible(side), overlay: side.classList.contains("overlay"), label: side.getAttribute("aria-label"), role: side.getAttribute("role"),
        name: side.querySelector(".fp-name")?.textContent,
        chips: [...side.querySelectorAll(".fp-fact:not(.wait)")].map((c) => c.textContent),
        flagged: [...side.querySelectorAll(".fp-fact.flag")].map((c) => c.textContent),
        headings: [...side.querySelectorAll("h3")].filter(visible).map((h) => h.textContent),
        suggestions: [...side.querySelectorAll(".fp-sec:not([hidden]) .fp-card-head strong")].map((s) => s.textContent),
        common: [...side.querySelectorAll(".fp-tool")].map((b) => [b.dataset.op, b.textContent, b.getAttribute("aria-expanded")]),
        all: side.querySelector(".fp-all")?.textContent,
        scrim: visible(document.querySelector(".rd-side-scrim")),
        button: [document.querySelector(".rd-panel-btn")?.getAttribute("aria-label"), document.querySelector(".rd-panel-btn")?.getAttribute("aria-expanded")],
      };
    });
    const tabs = (page) => page.$$eval(".rd-doctab", (ts) => ts.map((t) => [t.querySelector(".rd-doctab-label").textContent, t.classList.contains("active")]));

    // (a) A scanned file opened by id: its name, "scanned, no text yet", "Make it searchable".
    let page = await open(scanId);
    await factsShown(page);
    out.scan = await panel(page);
    await shot(page, "1-scanned");
    // A suggestion opens in place to its options, and closes again (focus in, then back).
    await page.click('.fp-card-head[data-op="ocr"]');
    out.ocrOpen = await page.evaluate(() => ({
      expanded: document.querySelector('.fp-card-head[data-op="ocr"]').getAttribute("aria-expanded"),
      run: !!document.querySelector("#fp-opts-ocr .fp-run")?.getClientRects().length,
      focusInside: !!document.activeElement.closest("#fp-opts-ocr"),
      fields: [...document.querySelectorAll("#fp-opts-ocr .field")].filter((f) => !f.hidden).map((f) => f.dataset.key),
    }));
    await shot(page, "2-suggestion-open");
    await page.click('.fp-card-head[data-op="ocr"]');
    out.ocrClosed = await page.evaluate(() => [document.querySelector('.fp-card-head[data-op="ocr"]').getAttribute("aria-expanded"),
      document.activeElement === document.querySelector('.fp-card-head[data-op="ocr"]')]);
    // Keyboard: Tab from the page walks into the panel.
    // (pdf.js makes each drawn page's text tabbable: they come first.)
    await page.focus(".rd-container");
    for (let i = 0; i < 12 && !out.tabIntoPanel; i++) {
      await page.keyboard.press("Tab");
      out.tabIntoPanel = await page.evaluate(() => document.activeElement.closest(".rd-side") ? document.activeElement.textContent.trim() : null);
    }
    await page.close();

    // (a) A plain text PDF: no "Suggested" heading.
    page = await open(plainId);
    await factsShown(page);
    out.plain = await panel(page);
    await shot(page, "3-plain");

    // (b) "Page numbers: this document" from the palette opens it in the panel; Run.
    await page.keyboard.press("Control+k");
    await page.fill("#palette-input", "page numbers this document");
    await page.click('.palette-item:has-text("Page numbers: this document")');
    await page.waitForSelector("#fp-opts-number .fp-run", { timeout: 10000 });
    out.chosen = await page.evaluate(() => ({
      heading: [...document.querySelectorAll(".rd-side h3")].filter((h) => !h.closest("[hidden]")).map((h) => h.textContent),
      expanded: document.querySelector('.fp-card-head[data-op="number"]')?.getAttribute("aria-expanded"),
      focusInside: !!document.activeElement.closest("#fp-opts-number"),
    }));
    const runsBefore = await page.evaluate(() => window.__runs.length);
    await page.click("#fp-opts-number .fp-run");
    await page.waitForFunction(() => document.querySelectorAll(".rd-doctab").length === 2, null, { timeout: 60000 });
    await page.waitForSelector(".rd-toast", { timeout: 10000 });
    out.afterRun = { tabs: await tabs(page), toast: await page.textContent(".rd-toast"), title: await page.textContent(".rd-title"),
      fields: (await page.evaluate(() => window.__runs))[runsBefore], postData: out.runs.at(-1) };
    await factsShown(page);
    out.afterRun.panel = await panel(page);
    out.afterRun.note = await page.textContent(".fp-note");
    await shot(page, "4-after-run");

    // (f) A tool that fails: Add a password with no password and nothing limited.
    await page.click('.fp-tool[data-op="encrypt"]');
    out.encryptFocus = await page.evaluate(() => !!document.activeElement.closest("#fp-opts-encrypt"));
    await page.click("#fp-opts-encrypt .fp-run");
    await page.waitForSelector('#fp-opts-encrypt [role="alert"]:not([hidden])', { timeout: 30000 });
    out.failure = await page.evaluate(() => ({
      text: document.querySelector('#fp-opts-encrypt [role="alert"]').textContent,
      optionsKept: !document.querySelector("#fp-opts-encrypt").hidden && !!document.querySelector("#fp-new_password, #fp-encrypt-new_password"),
      tabs: document.querySelectorAll(".rd-doctab").length,
    }));
    await shot(page, "5-failure");

    // (c) A file opened from bytes (the reader's file box) runs with its bytes;
    // (g) To Word, built-in: not a PDF, so Save is offered and no tab opens.
    await page.setInputFiles("#reader-host input[type=file]", uploadPath);
    await page.waitForFunction(() => document.querySelectorAll(".rd-doctab").length === 3, null, { timeout: 60000 });
    await factsShown(page);
    out.uploaded = { tabs: await tabs(page), panel: await panel(page) };
    await page.click('.fp-tool[data-op="to-word"]');
    await page.selectOption("#fp-to-word-engine", "basic");
    const runsBeforeWord = await page.evaluate(() => window.__runs.length);
    await page.click("#fp-opts-to-word .fp-run");
    await page.waitForSelector("#fp-opts-to-word .fp-done:not([hidden])", { timeout: 120000 });
    out.word = await page.evaluate(() => ({
      done: document.querySelector("#fp-opts-to-word .fp-done").innerText,
      save: [...document.querySelectorAll("#fp-opts-to-word .fp-save .btn")].map((b) => b.textContent.trim()),
      error: document.querySelector('#fp-opts-to-word [role="alert"]').hidden ? "" : document.querySelector('#fp-opts-to-word [role="alert"]').textContent,
      tabs: document.querySelectorAll(".rd-doctab").length,
    }));
    out.word.fields = (await page.evaluate(() => window.__runs))[runsBeforeWord];
    await shot(page, "6-word");
    // A file field and a password field in the panel: sign the original with a digital ID.
    if (process.env.PFX) {
      await page.click(".rd-doctab:first-child .rd-doctab-name");
      await page.waitForFunction(() => document.querySelector(".fp-name")?.textContent === "Quarterly report.pdf");
      await page.evaluate(() => fpChoose("sign-cert"));  // Digital ID, chosen from the palette or All tools
      await page.setInputFiles("#fp-sign-cert-pfx", process.env.PFX);
      await page.fill("#fp-sign-cert-pfx_password", process.env.PFX_PASSWORD || "");
      const runsBeforeSign = await page.evaluate(() => window.__runs.length);
      await page.click("#fp-opts-sign-cert .fp-run");
      await page.waitForFunction(() => document.querySelectorAll(".rd-doctab").length === 4
        || !document.querySelector('#fp-opts-sign-cert [role="alert"]')?.hidden, null, { timeout: 120000 });
      out.signed = { tabs: await tabs(page), fields: (await page.evaluate(() => window.__runs))[runsBeforeSign],
        error: await page.evaluate(() => { const e = document.querySelector('#fp-opts-sign-cert [role="alert"]'); return e && !e.hidden ? e.textContent : ""; }) };
    }
    out.scrollWidth1280 = await page.evaluate(() => [document.documentElement.scrollWidth, innerWidth]);
    out.moreButtonHeights = await page.evaluate(() => [...document.querySelectorAll('.fp-more button')].map((b) => Math.round(b.getBoundingClientRect().height)));
    out.toolbarFits = await page.evaluate(() => { const t = document.querySelector(".rd-toolbar"); return [t.scrollWidth, t.clientWidth]; });
    await page.close();

    // (d) T closes the panel, and that stays so after a reload; T opens it again.
    page = await open(plainId);
    await factsShown(page);
    await page.focus(".rd-container");
    await page.keyboard.press("t");
    out.tCloses = await panel(page);
    await page.reload();
    await page.waitForSelector(".page canvas", { timeout: 60000 });
    out.afterReloadClosed = await panel(page);
    await page.focus(".rd-container");
    await page.keyboard.press("t");
    out.tOpens = await panel(page);
    await page.reload();
    await page.waitForSelector(".page canvas", { timeout: 60000 });
    out.afterReloadOpen = await panel(page);
    await page.close();

    // (e) 1000 px wide: closed at first; it comes over the page with a scrim; Escape closes it.
    page = await context.newPage();
    await page.setViewportSize({ width: 1000, height: 800 });
    await open(plainId, page);
    out.narrowStart = await panel(page);
    await page.click(".rd-panel-btn");
    await page.waitForSelector(".rd-side .fp-facts:not([aria-busy])", { state: "attached", timeout: 30000 });
    out.narrowOpen = await panel(page);
    out.narrowFocusInside = await page.evaluate(() => !!document.activeElement.closest(".rd-side"));
    await shot(page, "7-narrow");
    out.scrollWidth1000 = await page.evaluate(() => [document.documentElement.scrollWidth, innerWidth]);
    await page.keyboard.press("Escape");
    out.narrowEscape = await panel(page);
    await page.click(".rd-panel-btn");
    await page.mouse.click(60 + 100, 400);  // the scrim, left of the sheet
    out.narrowScrim = await panel(page);
    await page.close();
  } finally {
    await browser.close();
  }
  console.log(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(1); });
