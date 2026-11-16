/* Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE. */
// 0.47.0 in a browser: a markup changed after it's placed. With Shapes on,
// a press on a rectangle selects it (handles show); a corner dragged makes
// it bigger; the arrow keys nudge it; a dimension's end dragged gives it
// its new length in words; the bar's colour recolours it; Delete removes a
// line; a press on empty paper lets go, and a drag there still draws.
// Run by test_markup_change_ui.py:
//   node markedit.js <page URL with #token=...> <id of an opened drawing> [shots folder]
// Prints one JSON object with what it found.
const { chromium } = require("playwright");

(async () => {
  const [url, id, shots] = process.argv.slice(2);
  const out = { errors: [] };
  const browser = await chromium.launch();
  try {
    const page = await (await browser.newContext({ viewport: { width: 1400, height: 1000 } })).newPage();
    page.on("pageerror", (e) => out.errors.push(String(e.message || e)));
    page.on("console", (m) => { if (m.type() === "error" && !/Failed to load resource/.test(m.text())) out.errors.push(m.text()); });
    await page.goto(url + "&open=" + id);
    await page.waitForSelector(".page canvas", { timeout: 60000 });
    await page.waitForFunction(() => reader.doc && reader.loading.hidden, null, { timeout: 30000 });
    await page.evaluate(() => {
      reader.openAnnotate();
      reader.pdfViewer.currentScaleValue = "page-fit";
      reader.activeTab.scales = { ...(reader.activeTab.scales || {}), "*": { ratio: 48, units: "ft", label: "1/4\" = 1'-0\"" } };
      reader._setEditMode("SHAPE");
      reader.shapeSelect.value = "rect";
      reader.shapeSelect.dispatchEvent(new Event("change"));
    });
    await page.waitForTimeout(600);

    const at = (x, y) => page.evaluate(([px, py]) => {
      const view = reader.pdfViewer.getPageView(0), r = view.div.getBoundingClientRect();
      const [vx, vy] = view.viewport.convertToViewportPoint(px, py);
      return [r.left + view.div.clientLeft + vx, r.top + view.div.clientTop + vy];
    }, [x, y]);
    const annots = () => page.evaluate(async () => (await (await reader.doc.getPage(1)).getAnnotations()).filter((a) => a.subtype !== "Popup").map((a) => ({
      subtype: a.subtype, text: a.contentsObj?.str || "", line: a.lineCoordinates ? Array.from(a.lineCoordinates) : null,
      rect: a.rect.map((v) => Math.round(v)), color: a.color ? Array.from(a.color) : null })));
    // The markups of this version of the document are known, and the page is shown.
    const ready = async () => {
      await page.waitForFunction(() => reader.loading.hidden && document.querySelector(".page canvas")
        && reader.markCache && reader.markCache.tab === reader.activeTab && reader.markCache.version === (reader.activeTab.version || 0),
      null, { timeout: 30000 });
      await page.waitForTimeout(300);
    };
    // The document was written again since ``version``, and is ready.
    const rewritten = async (version) => {
      await page.waitForFunction((v) => (reader.activeTab.version || 0) > v, version, { timeout: 30000 });
      await ready();
    };
    const version = () => page.evaluate(() => reader.activeTab.version || 0);
    const selected = () => page.evaluate(() => (reader.markSel ? { kind: reader.markSel.shape.kind, index: reader.markSel.index,
      grips: document.querySelectorAll(".rd-markedit .rd-markgrip").length } : null));
    const click = async (x, y) => { const [sx, sy] = await at(x, y); await page.mouse.click(sx, sy); await page.waitForTimeout(150); };
    const drag = async (from, to, options = {}) => {
      const [ax, ay] = await at(...from), [bx, by] = await at(...to);
      await page.mouse.move(ax, ay);
      await page.mouse.down();
      await page.mouse.move((ax + bx) / 2, (ay + by) / 2, { steps: 3 });
      await page.mouse.move(bx, by, { steps: 3 });
      if (options.during) await options.during();
      await page.mouse.up();
    };

    await ready();
    out.start = await annots();

    // A press on the rectangle's top edge selects it: four handles.
    await click(150, 560);
    out.rectSelected = await selected();
    if (shots) await page.screenshot({ path: `${shots}/markup-selected.png` });

    // Its top right corner dragged out: bigger, and still selected.
    let v = await version();
    await drag([200, 560], [260, 600], { during: async () => { out.moving = await page.evaluate(() => !!document.querySelector(".rd-markedit.rd-markmoving")); } });
    await rewritten(v);
    await page.waitForFunction(() => !!reader.markSel, null, { timeout: 10000 });
    out.afterResize = { annots: await annots(), selected: await selected() };

    // Shift + right arrow: ten points to the right.
    v = await version();
    await page.keyboard.press("Shift+ArrowRight");
    await rewritten(v);
    await page.waitForFunction(() => !!reader.markSel, null, { timeout: 10000 });
    out.afterNudge = (await annots())[0].rect;

    // The bar's blue: the selected rectangle takes it.
    v = await version();
    await page.evaluate(() => reader._setColor(1));
    await rewritten(v);
    out.afterColour = (await annots())[0].color;

    // The dimension: its right end dragged from 244 to 316 (Alt: exactly there) reads 12 feet.
    await click(170, 300);
    out.dimSelected = await selected();
    v = await version();
    await page.keyboard.down("Alt");
    await drag([244, 300], [316, 300]);
    await page.keyboard.up("Alt");
    await rewritten(v);
    out.afterDim = (await annots()).find((a) => a.line && a.text);

    // The plain line: selected and deleted.
    await page.waitForFunction(() => !!reader.markSel, null, { timeout: 10000 });
    await click(200, 420);
    out.lineSelected = await selected();
    v = await version();
    await page.keyboard.press("Delete");
    await rewritten(v);
    out.afterDelete = (await annots()).length;

    // Empty paper: a press lets go; a drag still draws a new rectangle.
    await click(150, 560 + 60);
    await page.waitForTimeout(100);
    await click(400, 700);
    out.letGo = (await selected()) === null && (await page.evaluate(() => !document.querySelector(".rd-markedit")));
    v = await version();
    await drag([380, 680], [460, 740]);
    await rewritten(v);
    out.afterDraw = (await annots()).length;
  } catch (e) {
    out.errors.push(String(e.stack || e));
  } finally {
    await browser.close();
  }
  console.log(JSON.stringify(out));
})();
