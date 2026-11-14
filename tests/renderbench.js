/* Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE. */
// How long pdf.js takes to draw a page in the reader's own browser engine
// (see renderbench.py): for each file, three runs of a first draw at 100 %
// (a fresh load, reading the page included) and a second at 200 %.
//   node renderbench.js <page URL with #token=...> <JSON {files, scale, headed}>
// Prints {file name: [[first ms, again ms], ...]}.
const { chromium } = require("playwright");

(async () => {
  const [url, extraJson] = process.argv.slice(2);
  const cfg = JSON.parse(extraJson);
  const out = {};
  const browser = await chromium.launch({ headless: !cfg.headed });
  try {
    for (const [name, id] of Object.entries(cfg.files)) {
      out[name] = [];
      for (let run = 0; run < 3; run++) {
        const ctx = await browser.newContext({ viewport: { width: 1280, height: 860 } });
        const page = await ctx.newPage();
        await page.goto(url + "&open=" + id);
        await page.waitForFunction(() => typeof reader !== "undefined" && reader && reader.doc && reader.loading.hidden, null, { timeout: 120000 });
        out[name].push(await page.evaluate(async (scale) => {
          const draw = async (pdfPage, s) => {
            const viewport = pdfPage.getViewport({ scale: s });
            const canvas = document.createElement("canvas");
            canvas.width = Math.ceil(viewport.width); canvas.height = Math.ceil(viewport.height);
            const t0 = performance.now();
            await pdfPage.render({ canvasContext: canvas.getContext("2d"), viewport }).promise;
            // (Read a pixel: the drawing is finished, not only queued.)
            canvas.getContext("2d").getImageData(0, 0, 1, 1);
            return performance.now() - t0;
          };
          // A second copy of the document, so the viewer's own drawing of page 1 doesn't count.
          const bytes = await reader._currentBytes();
          const { getDocument } = await import("/static/pdfjs/build/pdf.min.mjs");
          const task = getDocument({ data: new Uint8Array(bytes).slice() });
          const fresh = await task.promise;
          const pdfPage = await fresh.getPage(1);
          const first = await draw(pdfPage, scale);
          const again = await draw(pdfPage, scale * 2);
          await task.destroy();
          return [first, again];
        }, cfg.scale));
        await ctx.close();
      }
    }
  } finally {
    await browser.close();
  }
  console.log(JSON.stringify(out));
})().catch((e) => { console.error(String(e.stack || e)); process.exit(1); });
