// A document closed while work for it is still on the way. Open one PDF, open
// a second in another tab, go back to the first and turn a page: each step
// swaps the pdf.js document while pdf.js's viewer (handing pages to the page
// views, the scripting manager), thumbnails, outline, the signature and form
// checks, comments and the file panel may still have work for the old one.
// Nothing may reach into a destroyed document: pdf.js then throws "Cannot read
// properties of null (reading 'sendWithPromise')", which nothing catches.
// Run by test_critique_fixes.py:
//   node staledoc.js <page URL with #token=...> <JSON with ids>
// Prints one JSON object with what it found.
const { chromium } = require("playwright");

(async () => {
  const [url, extraJson] = process.argv.slice(2);
  const cfg = JSON.parse(extraJson);
  const out = { errors: [], stale: [], closed: [] };
  const browser = await chromium.launch();
  try {
    // In the desktop window (pywebview's API, stubbed): the file panel shows too.
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
    await ctx.addInitScript(() => {
      window.pywebview = { api: { save_result: async () => null } };
      globalThis.__stale = [];
      Error.stackTraceLimit = 40;
    });
    const page = await ctx.newPage();
    page.on("pageerror", (e) => out.errors.push(String(e.stack || e.message || e)));
    page.on("console", (m) => {
      // (pdf.js's viewer says so itself when a page it was still asking for
      // went with the document: its own message, handled, not an error here.)
      if (m.type() === "error" && !/Failed to load resource|Unable to get page \d+ to initialize viewer/.test(m.text())) out.errors.push(m.text());
    });
    // Every call pdf.js makes through a document's link to its worker after that
    // link is gone is noted (with where it came from), caught or not.
    await page.route("**/pdfjs/build/pdf.min.mjs", async (route) => {
      const res = await route.fetch();
      const body = (await res.text()).replace(/this\.messageHandler\.sendWithPromise\(/g,
        "(this.messageHandler||(globalThis.__stale.push(new Error('a call into a destroyed document').stack),null)).sendWithPromise(");
      out.watched = (body.match(/globalThis\.__stale\.push/g) || []).length;
      await route.fulfill({ response: res, body, headers: { ...res.headers(), "content-type": "text/javascript" } });
    });
    for (let round = 0; round < cfg.rounds; round++) {
      await page.goto(url + "&open=" + cfg.firstId + "&round=" + round);
      await page.waitForSelector(".page canvas", { timeout: 60000 });
      await page.evaluate((id) => reader.openById(id), cfg.secondId);
      await page.waitForFunction(() => reader.tabs.length === 2 && reader.doc && reader.activeTab === reader.tabs[1], null, { timeout: 60000 });
      await page.evaluate(() => { window.__second = reader.doc; });
      await page.click(".rd-doctab:first-child .rd-doctab-name");
      await page.waitForFunction(() => reader.activeTab === reader.tabs[0] && reader.doc && reader.doc !== window.__second, null, { timeout: 30000 });
      // The second tab's document is gone now: asking it anything is refused
      // (a rejection, which callers handle), not a TypeError thrown at once.
      if (cfg.probe !== false) out.closed.push(await page.evaluate(async () => {
        const old = window.__second, said = {};
        for (const [name, ask] of [["getPage", () => old.getPage(1)], ["getOutline", () => old.getOutline()],
          ["getPageLabels", () => old.getPageLabels()], ["getFieldObjects", () => old.getFieldObjects()],
          ["getData", () => old.getData()]]) {
          try { said[name] = await ask().then(() => "answered", (e) => (e.closed ? "refused" : "rejected: " + e.message)); }
          catch (e) { said[name] = "threw: " + e.message; }
        }
        // pdf.js's viewer hands the scripting manager documents without
        // waiting for the answer: one that's gone mustn't make an error.
        try { said.scripting = await reader.scripting.setDocument(old).then(() => "quiet", (e) => "rejected: " + e.message); }
        catch (e) { said.scripting = "threw: " + e.message; }
        await reader.scripting.setDocument(reader.doc);
        return said;
      }));
      await page.evaluate(() => reader.rotatePages([1], 90));
      await page.waitForFunction(() => reader.activeTab.dirty, null, { timeout: 60000 });
      await page.waitForTimeout(3000);
      out.stale.push(...await page.evaluate(() => globalThis.__stale.splice(0)));
      out["round" + round] = await page.evaluate(() => ({
        tabs: reader.tabs.length, dirty: !!reader.activeTab.dirty, canvas: !!document.querySelector(".page canvas"),
      }));
    }
    await ctx.close();
  } catch (e) {
    out.errors.push("FATAL " + (e.stack || e));
  }
  await browser.close();
  process.stdout.write(JSON.stringify(out));
})();
