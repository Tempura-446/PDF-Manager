/* Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE. */
// 0.38.0 in a browser: the reader's markups for drawings. A cloud through
// clicked corners, an arrow with corners, a dimension that snaps to the
// drawing and reads its scale, a callout, a review stamp; Esc gives a
// half-drawn cloud up.
// Run by test_drawing_markups_ui.py:
//   node drawmarks.js <page URL with #token=...> <id of an opened drawing> [shots folder]
// Prints one JSON object with what it found.
const { chromium } = require("playwright");

(async () => {
  const [url, id, shots] = process.argv.slice(2);
  const out = { errors: [], steps: {} };
  const browser = await chromium.launch();
  try {
    const ctx = await browser.newContext({ viewport: { width: 1400, height: 1000 } });
    const page = await ctx.newPage();
    page.on("pageerror", (e) => out.errors.push(String(e.message || e)));
    page.on("console", (m) => { if (m.type() === "error" && !/Failed to load resource/.test(m.text())) out.errors.push(m.text()); });
    await page.goto(url + "&open=" + id);
    await page.waitForSelector(".page canvas", { timeout: 60000 });
    await page.waitForFunction(() => reader.doc && reader.loading.hidden, null, { timeout: 30000 });
    await page.waitForTimeout(800);

    // The whole page in view with the annotate bar open, so every point can be clicked.
    await page.evaluate(() => { reader.openAnnotate(); reader.pdfViewer.currentScaleValue = "page-fit"; });
    await page.waitForTimeout(600);

    // Where a point of the page (PDF points) is on screen.
    const at = (x, y) => page.evaluate(([px, py]) => {
      const view = reader.pdfViewer.getPageView(0), r = view.div.getBoundingClientRect();
      const [vx, vy] = view.viewport.convertToViewportPoint(px, py);
      return [r.left + view.div.clientLeft + vx, r.top + view.div.clientTop + vy];
    }, [x, y]);
    const annots = () => page.evaluate(async () => (await (await reader.doc.getPage(1)).getAnnotations()).map((a) => ({
      subtype: a.subtype, text: a.contentsObj?.str || "", line: a.lineCoordinates || null,
      vertices: a.vertices ? Array.from(a.vertices) : null, ends: a.lineEndings || null, rect: a.rect })));
    const grown = async (n) => {
      // (Asked from here: a page function that returns a promise counts as true at once.)
      for (let tries = 0; ; tries++) {
        const have = await page.evaluate(async () => (reader.doc ? (await (await reader.doc.getPage(1)).getAnnotations()).length : 0)).catch(() => 0);
        if (have >= n) break;
        if (tries > 150) throw new Error(`still ${have} markups, not ${n}`);
        await page.waitForTimeout(200);
      }
      await page.waitForFunction(() => reader.loading.hidden && document.querySelector(".page canvas"), null, { timeout: 30000 });
      await page.waitForTimeout(500);
    };
    const shape = async (kind) => {
      await page.evaluate((k) => {
        if (reader.annotbar.hidden) reader.openAnnotate();
        reader._setEditMode("SHAPE");
        reader.shapeSelect.value = k;
        reader.shapeSelect.dispatchEvent(new Event("change"));
      }, kind);
      await page.waitForTimeout(200);
    };
    const click = async (x, y, options) => { const [sx, sy] = await at(x, y); await page.mouse.click(sx, sy, options); await page.waitForTimeout(120); };
    const drag = async (from, to) => {
      const [ax, ay] = await at(...from), [bx, by] = await at(...to);
      await page.mouse.move(ax, ay);
      await page.mouse.down();
      await page.mouse.move((ax + bx) / 2, (ay + by) / 2, { steps: 3 });
      await page.mouse.move(bx, by, { steps: 3 });
    };

    out.options = await page.evaluate(() => ({
      shapes: [...reader.shapeSelect.options].map((o) => o.value), stamps: [...reader.stampSelect.options].map((o) => o.value) }));

    // A cloud given up with Esc, then one through four corners (Enter ends it).
    await shape("polycloud");
    await click(330, 620);
    await click(500, 620);
    out.steps.drafting = await page.evaluate(() => !!document.querySelector(".rd-shapedraft path"));
    await page.keyboard.press("Escape");
    out.steps.givenUp = await page.evaluate(() => !document.querySelector(".rd-shapedraft"));
    for (const [x, y] of [[330, 620], [500, 620], [500, 740], [330, 740]]) await click(x, y);
    await page.keyboard.press("Enter");
    await grown(1);

    // An arrow with corners (a double click ends it).
    await shape("polyarrow");
    await click(330, 560);
    await click(420, 560);
    await click(420, 500, { clickCount: 2 });
    await grown(2);

    // A dimension along the box's bottom edge: started and ended a little off
    // its corners, it snaps onto them; the sheet's scale gives the length.
    await page.evaluate(() => { reader.activeTab.scales = { ...(reader.activeTab.scales || {}), "*": { ratio: 48, units: "ft", label: "1/4\" = 1'-0\"" } }; });
    await shape("dimension");
    // (Choosing Dimension reads the drawing's lines ahead, so the first press snaps too.)
    await page.waitForFunction(() => !!reader.activeTab.geometry?.get(1), null, { timeout: 30000 });
    await drag([101.5, 301.5], [245, 299]);
    out.steps.dimDraft = await page.evaluate(() => document.querySelector(".rd-dimdraft")?.textContent || "");
    await page.mouse.up();
    await grown(3);

    // A callout: dragged from what it points at to where its words go.
    await shape("callout");
    await drag([244, 400], [360, 450]);
    await page.mouse.up();
    await page.waitForSelector(".rd-calloutbox textarea");
    await page.fill(".rd-calloutbox textarea", "Move this wall");
    if (shots) await page.screenshot({ path: `${shots}/callout-box.png` });
    await page.click(".rd-calloutbox .rd-btn.primary");
    await grown(4);

    // A review stamp.
    await page.evaluate(() => { reader._setEditMode("STAMPS"); reader.stampSelect.value = "REVISE AND RESUBMIT"; });
    await page.waitForTimeout(200);
    await click(420, 200);
    await grown(5);

    out.annots = await annots();
    if (shots) await page.screenshot({ path: `${shots}/drawing-markups.png` });
  } catch (e) {
    out.errors.push(String(e.stack || e));
  } finally {
    await browser.close();
  }
  console.log(JSON.stringify(out));
})();
