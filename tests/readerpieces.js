/* Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE. */
// A file read in pieces whose pieces can't be had, in a browser. Run by test_reader_pieces.py:
//   node readerpieces.js <page URL with #token=...> <rules.json> <{name: {id, path}}> <small id> <other.pdf>
// Prints one JSON object: {errors, scenarios: {name: what was seen}}.
const { chromium } = require("playwright");
const fs = require("fs");

(async () => {
  const [url, rulesFile, filesJson, smallId, otherPdf] = process.argv.slice(2);
  const files = JSON.parse(filesJson);
  const out = { errors: [], scenarios: {} };
  const rules = (list) => fs.writeFileSync(rulesFile, JSON.stringify(list));
  const MB4 = 4 << 20;
  const browser = await chromium.launch();
  const within = (p, ms) => Promise.race([p.then(() => true, () => false), new Promise((r) => setTimeout(() => r(false), ms))]);

  const start = async () => {
    rules([]);
    const context = await browser.newContext({ viewport: { width: 1280, height: 800 }, acceptDownloads: true });
    const page = await context.newPage();
    page.dialogs = [];
    page.on("dialog", (d) => { page.dialogs.push(d.message()); d.dismiss().catch(() => {}); });
    page.on("pageerror", (e) => out.errors.push(String(e.message || e)));
    await page.goto(url);
    await page.waitForSelector("#home-host:not([hidden])", { timeout: 30000 });
    await page.evaluate(() => {
      window.__toasts = [];
      new MutationObserver((ms) => { for (const m of ms) for (const n of m.addedNodes) if (n.nodeType === 1 && /toast/.test(n.className || "")) window.__toasts.push(n.textContent); })
        .observe(document.body, { childList: true, subtree: true });
    });
    return page;
  };
  /* eslint-disable no-undef */
  const open = (page, id) => page.evaluate((i) => { openInReaderById([i]); }, id);
  const shownName = (page, name, ms = 30000) => page.waitForFunction((n) => !!reader && reader.activeTab?.name === n && !!reader.doc && reader.loading.hidden, name, { timeout: ms });
  const state = (page) => page.evaluate(() => ({ tabs: reader.tabs.map((t) => t.name), active: reader.activeTab?.name || null, busy: !reader.loading.hidden,
    toasts: [...new Set(window.__toasts)] }));
  const held = (page, name) => page.evaluate((n) => { const t = reader.tabs.find((x) => x.name === n); return t ? !!t.bytes : null; }, name);
  const openAnother = (page) => within((async () => { await open(page, smallId); await shownName(page, "Small.pdf", 12000); })(), 14000);
  const closeIt = (page, name) => within((async () => {
    page.evaluate((n) => { reader.closeTab(reader.tabs.find((t) => t.name === n)); }, name).catch(() => {});
    await page.waitForFunction((n) => !reader.tabs.some((t) => t.name === n) && reader.loading.hidden, name, { timeout: 12000 });
  })(), 14000);
  // (the last page is the one with the big picture: see lazy_pdf)
  const farPage = (page) => page.evaluate(() => { reader.pdfViewer.currentPageNumber = reader.doc.numPages; });
  const scenario = async (name, fn) => {
    let page = null;
    try {
      page = await start();
      out.scenarios[name] = await fn(page);
    } catch (e) {
      out.scenarios[name] = { failed: String(e.message || e).split("\n")[0] };
    } finally {
      rules([]);
      await page?.context().close();
    }
  };

  // A piece fails (twice: once more is asked) after the file is open, and the background read too.
  await scenario("midread", async (page) => {
    rules([{ mode: "whole" }]);
    await open(page, files.midread.id);
    await shownName(page, "midread.pdf");
    rules([{ mode: "after", at: MB4 }]);
    await farPage(page);
    await page.waitForFunction(() => window.__toasts.some((t) => t.includes("Couldn't read more")), null, { timeout: 30000 });
    await page.waitForTimeout(2000);
    const r = { held: await held(page, "midread.pdf") };
    const saved = await within(page.evaluate(() => reader.saveCopy()), 15000);
    r.saveSays = saved ? (await page.evaluate(() => document.querySelector(".rd-toast")?.textContent || "")) : "no answer";
    Object.assign(r, await state(page));
    r.openAnother = await openAnother(page);
    r.closeIt = await closeIt(page, "midread.pdf");
    r.toasts = (await state(page)).toasts;
    return r;
  });

  // The pieces fail while the file is being opened.
  await scenario("opening", async (page) => {
    rules([{ mode: "after", at: MB4 }]);
    await open(page, files.opening.id);
    await page.waitForFunction(() => window.__toasts.some((t) => /Could not open|isn't a valid/.test(t)), null, { timeout: 30000 });
    await page.waitForTimeout(500);
    const r = await state(page);
    rules([]);
    r.openAnother = await openAnother(page);
    return r;
  });

  // A piece never comes (the server doesn't answer).
  await scenario("hang", async (page) => {
    rules([{ mode: "whole" }]);
    await open(page, files.hang.id);
    await shownName(page, "hang.pdf");
    rules([{ mode: "whole" }, { mode: "after", at: MB4, how: "hang", secs: 40 }]);
    await farPage(page);
    await page.waitForTimeout(1500);
    const r = { openAnother: await openAnother(page) };
    r.closeIt = await closeIt(page, "hang.pdf");
    Object.assign(r, await state(page));
    return r;
  });

  // Held (read in full in the background), then the file is deleted / replaced while another tab is shown.
  for (const how of ["deleted", "replaced"]) {
    await scenario("held-" + how, async (page) => {
      const name = `held-${how}.pdf`, f = files["held-" + how];
      await open(page, f.id);
      await shownName(page, name);
      await page.waitForFunction((n) => !!reader.tabs.find((t) => t.name === n)?.bytes, name, { timeout: 30000 });
      await open(page, smallId);
      await shownName(page, "Small.pdf");
      if (how === "deleted") fs.unlinkSync(f.path); else fs.copyFileSync(otherPdf, f.path);
      await page.evaluate((n) => reader.showTab(reader.tabs.find((t) => t.name === n)), name);
      await shownName(page, name);
      await page.waitForTimeout(500);
      const r = { held: true, back: await page.evaluate(() => ({ name: reader.activeTab.name, pages: reader.doc.numPages })) };
      r.size = await page.evaluate(() => reader.activeTab.size);
      r.bytes = await page.evaluate(async () => (await reader.currentBytes()).length);
      if (how === "replaced") {
        await page.evaluate(() => reader.rotatePages([1], 90));
        await page.waitForFunction(() => reader.activeTab.dirty && reader.loading.hidden, null, { timeout: 30000 });
        // The app's own dialog says so (not the browser's confirm()); Escape is Cancel.
        const saving = page.evaluate(() => reader.save());
        await page.waitForSelector("dialog.rd-dialog[open]", { timeout: 15000 });
        r.asked = await page.textContent("dialog.rd-dialog[open] h3");
        await page.keyboard.press("Escape");
        await within(saving, 15000);
      }
      r.dialogs = page.dialogs;
      Object.assign(r, await state(page));
      return r;
    });
  }

  // Not all read yet (the background read fails), then the file is deleted / replaced while another tab is shown.
  for (const how of ["deleted", "replaced"]) {
    await scenario(how, async (page) => {
      const name = `${how}.pdf`, f = files[how];
      rules([{ mode: "whole" }]);
      await open(page, f.id);
      await shownName(page, name);
      await page.waitForTimeout(1000);
      const r = { held: await held(page, name) };
      await open(page, smallId);
      await shownName(page, "Small.pdf");
      if (how === "deleted") fs.unlinkSync(f.path); else fs.copyFileSync(otherPdf, f.path);
      await page.evaluate((n) => reader.showTab(reader.tabs.find((t) => t.name === n)), name);
      await page.waitForFunction(() => window.__toasts.some((t) => t.includes("Can't show")), null, { timeout: 30000 });
      await page.waitForFunction(() => reader.loading.hidden && !!reader.doc, null, { timeout: 15000 });
      Object.assign(r, await state(page));
      r.closeIt = await closeIt(page, name);
      return r;
    });
  }

  // The same file opened again, from the file box, before and after it is all here.
  await scenario("twice", async (page) => {
    await open(page, files.twice.id);
    await shownName(page, "twice.pdf");
    const box = await page.evaluateHandle(() => reader.fileInput);
    const tabs = () => page.evaluate(() => reader.tabs.length);
    const r = {};
    await page.evaluate(() => { const t = reader.activeTab; t.__wasHeld = !!t.bytes; });
    await box.setInputFiles(files.twice.path);
    await page.waitForTimeout(1500);
    await page.evaluate(() => reader.chain);
    r.wasHeldAtFirst = await page.evaluate(() => reader.tabs[0].__wasHeld);
    r.beforeHeld = await tabs();
    await page.waitForFunction(() => !!reader.tabs[0].bytes, null, { timeout: 30000 });
    await box.setInputFiles(files.twice.path);
    await page.waitForTimeout(1500);
    await page.evaluate(() => reader.chain);
    r.afterHeld = await tabs();
    return r;
  });
  /* eslint-enable no-undef */
  await browser.close();
  console.log(JSON.stringify(out));
})();
