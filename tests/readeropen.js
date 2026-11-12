// Opening files in the reader, in a browser: a file of a few megabytes comes
// in pieces and still gets its signature and form bars; thumbnails are made
// when their panel is shown, and only near what it shows; Enter submits the
// password. Run by test_reader_open.py:
//   node readeropen.js <page URL with #token=...> <signed id> <form id> <locked id> <long id> <small id> <password>
// Prints one JSON object with what it found.
const { chromium } = require("playwright");

(async () => {
  const [url, signedId, formId, lockedId, longId, smallId, password] = process.argv.slice(2);
  const out = { errors: [] };
  const browser = await chromium.launch();
  const open = async (hash) => {
    const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
    const page = await context.newPage();
    page.on("pageerror", (e) => out.errors.push(String(e.message || e)));
    page.on("console", (m) => { if (m.type() === "error" && !/Failed to load resource/.test(m.text())) out.errors.push(m.text()); });
    const reqs = [];
    page.on("request", (r) => {
      const u = new URL(r.url());
      if (u.pathname.startsWith("/api/")) reqs.push({ path: u.pathname, method: r.method(), range: r.headers().range || "", sent: (r.postDataBuffer() || []).length });
    });
    await page.goto(url + hash);
    return { page, reqs };
  };
  const firstPage = (page) => page.waitForFunction(() => {
    const c = document.querySelector(".pdfViewer .page canvas");
    return !!c && typeof reader !== "undefined" && !!reader && !!reader.doc && reader.loading.hidden;  // eslint-disable-line no-undef
  }, null, { timeout: 60000 });
  const text = (page, sel) => page.evaluate((s) => { const e = document.querySelector(s); return e && !e.hidden ? e.textContent : null; }, sel);
  try {
    // (a) Started with a signed file of 5 MB.
    let { page, reqs } = await open(`&open=${signedId}`);
    await firstPage(page);
    await page.waitForSelector(".rd-sigbar:not([hidden])", { timeout: 60000 });
    await page.waitForFunction(() => ["none", "show", "has-fields"].includes(reader.activeTab.formHint), null, { timeout: 60000 });  // eslint-disable-line no-undef
    await page.waitForFunction(() => !!reader.activeTab.bytes, null, { timeout: 30000 }).catch(() => {});  // eslint-disable-line no-undef
    const files = reqs.filter((r) => r.path.startsWith("/api/file/"));
    const span = (r) => { const m = /bytes=(\d+)-(\d+)/.exec(r.range); return m ? +m[2] - +m[1] + 1 : Infinity; };
    out.signed = {
      size: await page.evaluate(() => reader.activeTab.size),  // eslint-disable-line no-undef
      held: await page.evaluate(() => !!reader.activeTab.bytes),  // eslint-disable-line no-undef
      ranges: files.map((r) => r.range), asked: files.reduce((a, r) => a + span(r), 0),
      bar: await text(page, ".rd-sigbar"), formHint: await page.evaluate(() => reader.activeTab.formHint),  // eslint-disable-line no-undef
      posts: reqs.filter((r) => r.method === "POST").map((r) => r.path),
      biggestUpload: Math.max(0, ...reqs.map((r) => r.sent)),
    };
    out.signed.fullLength = await page.evaluate(async () => (await reader.currentBytes()).length);  // eslint-disable-line no-undef

    // (b) Another file opened from the page that is already up: a printed form of 5 MB.
    await page.evaluate((id) => openInReaderById([id]), formId);  // eslint-disable-line no-undef
    await page.waitForFunction(() => reader.tabs.length === 2 && reader.activeTab === reader.tabs[1] && !!reader.doc, null, { timeout: 60000 });  // eslint-disable-line no-undef
    await page.waitForSelector(".rd-formbar:not([hidden])", { timeout: 60000 });
    const suggested = () => [...document.querySelectorAll(".rd-side .fp-sec:not([hidden]) .fp-card-head strong")].map((s) => s.textContent);
    await page.waitForFunction(`(${suggested})().includes("Make it fillable")`, null, { timeout: 60000 });
    out.form = {
      held: await page.evaluate(() => !!reader.tabs[1].bytes),  // eslint-disable-line no-undef
      tabs: await page.evaluate(() => reader.tabs.length),  // eslint-disable-line no-undef
      bar: await text(page, ".rd-formbar"),
      suggestions: await page.evaluate(suggested),
    };
    // Back to the first tab: its file is read in pieces again, and its bar is back.
    await page.evaluate(() => reader.showTab(reader.tabs[0]));  // eslint-disable-line no-undef
    await page.waitForFunction(() => reader.activeTab === reader.tabs[0] && !!reader.doc && reader.loading.hidden, null, { timeout: 60000 });  // eslint-disable-line no-undef
    await page.waitForSelector(".rd-sigbar:not([hidden])", { timeout: 60000 });
    out.back = { name: await page.evaluate(() => reader.activeTab.name), pages: await page.evaluate(() => reader.doc.numPages), formBar: await text(page, ".rd-formbar") };  // eslint-disable-line no-undef
    await page.context().close();

    // (c) Thumbnails, in a document of 120 pages.
    ({ page, reqs } = await open(`&open=${longId}`));
    await firstPage(page);
    await page.waitForTimeout(600);
    const rows = () => page.evaluate(() => {
      const all = [...document.querySelectorAll(".rd-thumb")];
      return { rows: all.length, first: +all[0]?.dataset.page || 0, has1: all.some((r) => r.dataset.page === "1"), drawn: all.filter((r) => r.querySelector("canvas")).length,
        current: +document.querySelector(".rd-thumb.current")?.dataset.page || 0, listHeight: document.querySelector(".rd-thumbs").offsetHeight };
    });
    const t = { pages: await page.evaluate(() => reader.doc.numPages), before: (await rows()).rows };  // eslint-disable-line no-undef
    await page.evaluate(() => reader.toggleSidebar());  // eslint-disable-line no-undef
    await page.waitForSelector(".rd-thumb canvas", { timeout: 30000 });
    const shown = await rows();
    Object.assign(t, { shown: shown.rows, first: shown.first, drawn: shown.drawn, listHeight: shown.listHeight });
    await page.evaluate(() => { reader.pdfViewer.currentPageNumber = 100; });  // eslint-disable-line no-undef
    await page.waitForSelector('.rd-thumb.current[data-page="100"] canvas', { timeout: 30000 });
    t.jumped = await rows();
    await page.focus(".rd-thumb.current");
    await page.keyboard.press("ArrowDown");
    const focused = await page.evaluate(() => +document.activeElement.dataset.page);
    await page.keyboard.press("Enter");
    await page.waitForFunction(() => reader.pdfViewer.currentPageNumber === 101, null, { timeout: 10000 });  // eslint-disable-line no-undef
    t.keys = [focused, await page.evaluate(() => reader.pdfViewer.currentPageNumber)];  // eslint-disable-line no-undef
    // Organising pages from a thumbnail still works: delete the one in view.
    page.on("dialog", (d) => d.accept());
    await page.hover('.rd-thumb[data-page="101"]');
    await page.click('.rd-thumb[data-page="101"] [data-act=delete]');
    await page.waitForFunction(() => reader.doc && reader.doc.numPages === 119 && reader.loading.hidden, null, { timeout: 60000 });  // eslint-disable-line no-undef
    await page.waitForSelector(".rd-thumb canvas", { timeout: 30000 });
    t.deleted = { pages: await page.evaluate(() => reader.doc.numPages), rows: (await rows()).rows > 0 };  // eslint-disable-line no-undef
    // The panel closed, another document opened: no thumbnails for it.
    await page.evaluate(() => reader.toggleSidebar());  // eslint-disable-line no-undef
    await page.evaluate((id) => openInReaderById([id]), smallId);  // eslint-disable-line no-undef
    await page.waitForFunction(() => reader.tabs.length === 2 && reader.activeTab === reader.tabs[1] && !!reader.doc && reader.loading.hidden, null, { timeout: 60000 });  // eslint-disable-line no-undef
    await page.waitForTimeout(500);
    t.hiddenAgain = (await rows()).rows;
    out.thumbs = t;
    await page.context().close();

    // (d) A protected file: a wrong password and Enter asks again; the right one and Enter opens it.
    ({ page, reqs } = await open(`&open=${lockedId}`));
    const box = 'dialog.rd-dialog input[type=password]';  // (named by its label)
    await page.waitForSelector(box, { timeout: 60000 });
    await page.fill(box, "not-it");
    await page.keyboard.press("Enter");
    await page.waitForSelector("dialog.rd-dialog .err", { timeout: 30000 });
    const wrong = await page.textContent("dialog.rd-dialog .err");
    await page.fill(box, password);
    await page.keyboard.press("Enter");
    await firstPage(page);
    out.locked = { wrong, pages: await page.evaluate(() => reader.doc.numPages) };  // eslint-disable-line no-undef
    await page.context().close();

    // (e) Started on the start screen, a file opened afterwards.
    ({ page, reqs } = await open(""));
    await page.waitForSelector("#home-host:not([hidden])", { timeout: 30000 });
    const early = await page.evaluate(() => !!window.zpdfEarly) || reqs.some((r) => r.path.startsWith("/api/file/"));
    await page.evaluate((id) => openInReaderById([id]), smallId);  // eslint-disable-line no-undef
    await firstPage(page);
    out.home = { early, pages: await page.evaluate(() => reader.doc.numPages), name: await page.evaluate(() => reader.activeTab.name) };  // eslint-disable-line no-undef
    await page.context().close();
  } catch (e) {
    out.errors.push(String(e.stack || e));
  }
  await browser.close();
  console.log(JSON.stringify(out));
})();
