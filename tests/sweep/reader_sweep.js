// The sweep's reader check (run_sweep.py --reader): each file opened in the
// reader in a fresh page; it must show its first page, the password box or
// its "can't be opened" state, without errors on the page, and pages further
// in must draw too.
//   node reader_sweep.js <page URL with #token=...> <jobs.json> <ms per file>
// Prints {file: {class, message, seconds}}.
const { chromium } = require("playwright");
const fs = require("fs");

(async () => {
  const [url, jobsFile, perFile] = process.argv.slice(2);
  const jobs = JSON.parse(fs.readFileSync(jobsFile, "utf-8"));
  const limit = +perFile || 45000;
  const out = {};
  const browser = await chromium.launch();
  for (const job of jobs) {
    const started = Date.now();
    const errors = [];
    const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    const page = await context.newPage();
    page.on("pageerror", (e) => errors.push(String(e.message || e)));
    page.on("console", (m) => {
      if (m.type() === "error" && !/Failed to load resource|status of 4\d\d/.test(m.text())) errors.push(m.text());
    });
    let state = "";
    try {
      await page.goto(`${url}&open=${job.id}`);
      const handle = await page.waitForFunction(() => {
        /* global reader */
        if (typeof reader === "undefined" || !reader) return false;
        if (document.querySelector(".rd-pwfield input")) return "password";
        if (reader.broken && !reader.broken.hidden) return "broken";
        const c = document.querySelector(".pdfViewer .page canvas");
        if (c && reader.doc && reader.loading.hidden) return "shown";
        return false;
      }, null, { timeout: limit, polling: 250 });
      state = await handle.jsonValue();
      if (state === "password") {
        await page.fill(".rd-pwfield input", "user");
        await page.keyboard.press("Enter");
        const h2 = await page.waitForFunction(() => {
          if (document.querySelector(".rd-pwfield .err")) return "password-refused";
          const c = document.querySelector(".pdfViewer .page canvas");
          return c && reader.doc && reader.loading.hidden ? "shown-after-password" : false;
        }, null, { timeout: limit, polling: 250 });
        state = await h2.jsonValue();
      }
      if (state.startsWith("shown")) {
        // Go to the last page and back: those draw as well.
        const pages = await page.evaluate(() => reader.doc.numPages);
        if (pages > 1) {
          await page.evaluate((n) => { reader.pdfViewer.currentPageNumber = n; }, Math.min(pages, 50));
          await page.waitForTimeout(800);
          await page.evaluate(() => { reader.pdfViewer.currentPageNumber = 1; });
        }
        await page.waitForTimeout(500);
        state += ` (${pages} pages)`;
      }
      const cls = errors.length ? "crash" : (state === "broken" || state === "password-refused" ? "refused" : "ok");
      out[job.file] = { class: cls, message: (errors.length ? errors.slice(0, 3).join(" | ") + " — " : "") + state,
        seconds: (Date.now() - started) / 1000 };
    } catch (e) {
      const timedOut = /Timeout/i.test(String(e));
      out[job.file] = { class: errors.length ? "crash" : (timedOut ? "hang" : "crash"),
        message: (errors.slice(0, 3).join(" | ") + " " + String(e.message || e).split("\n")[0]).trim(),
        seconds: (Date.now() - started) / 1000 };
    }
    await context.close();
  }
  await browser.close();
  process.stdout.write(JSON.stringify(out));
})();
