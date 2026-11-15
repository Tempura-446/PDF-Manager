/* Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE. */
// 0.39.0 in a browser: a PDF that could do harm is said so in the file
// panel, with Make it safe offered first; a link out of the document shows
// its real address before anything opens, and Cancel opens nothing.
// Run by test_risks_ui.py:
//   node riskui.js <page URL with #token=...> <id of the risky file> [shots folder]
// Prints one JSON object with what it found.
const { chromium } = require("playwright");

(async () => {
  const [url, id, shots] = process.argv.slice(2);
  const out = { errors: [], opened: [] };
  const browser = await chromium.launch();
  try {
    const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 } });
    ctx.on("page", (p) => out.opened.push(p.url()));       // anything a link opened
    const page = await ctx.newPage();
    out.native = [];  // alert boxes: a script of the PDF's that ran
    page.on("dialog", (d) => { out.native.push(d.type() + ": " + d.message()); d.dismiss().catch(() => {}); });
    page.on("pageerror", (e) => out.errors.push(String(e.message || e)));
    page.on("console", (m) => { if (m.type() === "error" && !/Failed to load resource/.test(m.text())) out.errors.push(m.text()); });
    await page.goto(url + "&open=" + id);
    await page.waitForSelector(".page canvas", { timeout: 60000 });
    await page.waitForFunction(() => reader.doc && reader.loading.hidden, null, { timeout: 30000 });

    // The form works (a format, a total) and nothing else in the file runs.
    const field = (name) => `.annotationLayer input[name="${name}"]`;
    await page.waitForSelector(field("Price"), { timeout: 30000 });
    await page.waitForTimeout(1500);  // the sandbox is ready a moment after the page
    await page.click(field("Price"));
    await page.keyboard.type("12.5", { delay: 150 });
    await page.click(field("Other"));
    await page.keyboard.type("abc", { delay: 150 });
    await page.evaluate(() => document.activeElement?.blur());  // (a field shows its format once it's left)
    await page.waitForTimeout(800);
    out.form = await page.evaluate((names) => Object.fromEntries(names.map((n) =>
      [n.toLowerCase(), document.querySelector(`.annotationLayer input[name="${n}"]`).value])), ["Price", "Total", "Other"]);

    // The file panel: a chip that says it, and Make it safe as the first suggestion.
    await page.waitForSelector(".fp-fact.bad", { timeout: 30000 });
    out.chip = await page.$eval(".fp-fact.bad", (e) => ({ text: e.textContent, title: e.title }));
    await page.waitForSelector(".fp-card[data-op='makesafe']", { timeout: 30000 });
    out.first = await page.$eval(".fp-sec:not([hidden]) .fp-list .fp-card", (e) => ({ op: e.dataset.op, words: e.textContent }));
    if (shots) await page.screenshot({ path: `${shots}/risk-panel.png` });

    // The link on the page: asked first, with where it really goes.
    await page.waitForSelector(".annotationLayer a[href^='http']", { timeout: 30000 });
    const opening = (await ctx.pages()).length;
    await page.click(".annotationLayer a[href^='http']");
    await page.waitForSelector("dialog[open] .rd-linkaddr");
    out.dialog = await page.$eval("dialog[open]", (d) => ({ title: d.querySelector("h3").textContent,
      address: d.querySelector(".rd-linkaddr").textContent, host: d.querySelector(".rd-linkaddr strong").textContent,
      notes: [...d.querySelectorAll(".rd-linknote")].map((n) => n.textContent) }));
    if (shots) await page.screenshot({ path: `${shots}/risk-link.png` });
    await page.click("dialog[open] button:has-text('Cancel')");
    await page.waitForTimeout(400);
    out.afterCancel = (await ctx.pages()).length - opening;

    // Not asking: the menu's switch, then the link opens at once (to a page that can't load here: only that it was tried counts).
    await ctx.route(/mybank\.com\.evil-site\.net/, (route) => route.fulfill({ status: 200, contentType: "text/html", body: "<title>arrived</title>" }));
    await page.evaluate(() => localStorage.setItem("pdfm-link-ask", "false"));
    out.stored = await page.evaluate(() => localStorage.getItem("pdfm-link-ask"));
    const popup = ctx.waitForEvent("page", { timeout: 15000 }).catch(() => null);
    await page.click(".annotationLayer a[href^='http']");
    const arrived = await popup;
    out.noAsk = { dialog: await page.$("dialog[open] .rd-linkaddr") !== null, opened: !!arrived };
  } catch (e) {
    out.errors.push(String(e.stack || e));
  } finally {
    await browser.close();
  }
  console.log(JSON.stringify(out));
})();
