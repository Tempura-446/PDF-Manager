/* Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE. */
// 0.32.0 in a browser: the reader's Layers tab. It shows only for a PDF with
// layers, in the file's nested order, a locked layer can't be switched and a
// radio group keeps one on. Hiding "Dimensions" stops the page drawing it
// (pixels), hides its words in the text layer, and snapping no longer finds
// its line; each tab keeps its own choice across a tab switch; printing
// draws the layers shown; Save layer visibility is a change with Undo.
// Run by test_layers32_browser.py:
//   node fix32.js <page URL with #token=...> <JSON with ids>
// Prints one JSON object with what it found.
const { chromium } = require("playwright");

(async () => {
  const [url, extraJson] = process.argv.slice(2);
  const cfg = JSON.parse(extraJson);
  const out = { errors: [], native: [] };
  const browser = await chromium.launch();
  try {
    const watch = (page) => {
      page.on("pageerror", (e) => out.errors.push(String(e.stack || e)));
      page.on("console", (m) => { if (m.type() === "error" && !/Failed to load resource/.test(m.text())) out.errors.push(m.text()); });
      page.on("dialog", (d) => { out.native.push(d.type() + ": " + d.message()); d.dismiss().catch(() => {}); });
    };
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 860 } });
    const page = await ctx.newPage();
    watch(page);
    const showTab = async (n) => {
      await page.click(`.rd-doctab:nth-child(${n}) .rd-doctab-name`);
      await page.waitForFunction((i) => reader.doc && reader.activeTab === reader.tabs[i] && reader.loading.hidden && document.querySelector(".page canvas"), n - 1, { timeout: 30000 });
      await page.waitForTimeout(1200);
    };
    const layersReady = () => page.waitForFunction(() => reader.layers && reader.layers.doc === reader.doc && reader.layers.locked.size === 1
      && !reader.layersTab.hidden, null, { timeout: 30000 });
    // What the Layers tab and page 1 show now.
    const state = () => page.evaluate(async () => {
      const pv = reader.pdfViewer.getPageView(0);
      const canvas = document.querySelector(".page[data-page-number='1'] canvas");
      const k = canvas.width / pv.viewport.width;
      const pixel = (x, y) => {
        const [vx, vy] = pv.viewport.convertToViewportPoint(x, y);
        return [...canvas.getContext("2d").getImageData(Math.round(vx * k), Math.round(vy * k), 1, 1).data].slice(0, 3);
      };
      const shown = (text) => {
        const span = [...document.querySelectorAll(".page[data-page-number='1'] .textLayer span:not(.markedContent)")].find((s) => s.textContent.includes(text));
        return span ? span.getClientRects().length > 0 : null;
      };
      const g = reader.activeTab.geometry?.get(1);
      const hit = (x, y) => { const h = g ? g.snap([x, y], 6) : undefined; return h === undefined ? "no geometry" : h && [h.kind, Math.round(h.x), Math.round(h.y)]; };
      const boxes = Object.fromEntries([...document.querySelectorAll(".rd-layers input[data-layer]")].map((i) => [i.closest("label").textContent.trim(),
        { type: i.type, checked: i.checked, disabled: i.disabled }]));
      return {
        tab: !reader.layersTab.hidden, boxes, dimsLine: pixel(300, 600), wallsLine: pixel(300, 150),
        text: { dims: shown("DIMENSION"), walls: shown("WALLS"), schemeA: shown("SCHEME A"), schemeB: shown("SCHEME B") },
        snapDims: hit(300, 602), snapWalls: hit(300, 152),
        dirty: !!reader.activeTab.dirty, undo: reader.activeTab.undoDepth,
      };
    });
    const settle = async (pred) => {  // the pages drawn again
      await page.waitForFunction(pred, null, { timeout: 15000 }).catch(() => {});
      await page.waitForTimeout(600);
    };
    const geometry = () => page.evaluate(async () => {
      for (let i = 0; i < 100 && !reader.activeTab.geometry?.get(1); i++) { reader._geometry(1); await new Promise((r) => setTimeout(r, 100)); }
    });

    // A PDF without layers: no Layers tab.
    await page.goto(url + "&open=" + cfg.plain);
    await page.waitForSelector(".page canvas", { timeout: 60000 });
    await page.waitForTimeout(1500);
    out.plain = await page.evaluate(() => ({ tab: !reader.layersTab.hidden, layers: reader.layers }));

    // The drawing with layers, in a second tab.
    await page.evaluate((id) => reader.openById(id), cfg.plan);
    await page.waitForFunction(() => reader.tabs.length === 2 && reader.activeTab === reader.tabs[1] && document.querySelector(".page canvas"), null, { timeout: 30000 });
    await layersReady();
    await page.evaluate(() => { if (reader.sidebar.hidden) reader.toggleSidebar(); });
    await page.click(".rd-tabs .rd-tab:has-text('Layers')");
    await page.waitForTimeout(800);
    await geometry();
    out.tree = await page.evaluate(() => {
      const walk = (ul) => [...ul.children].map((li) => li.classList.contains("rd-layerhead")
        ? { head: li.querySelector(".rd-layerlabel").textContent, items: walk(li.querySelector("ul")) }
        : { name: li.querySelector("label").textContent.trim(), items: li.querySelector("ul") ? walk(li.querySelector("ul")) : [] });
      return walk(document.querySelector(".rd-layers"));
    });
    out.before = await state();
    if (cfg.shots) {
      await page.screenshot({ path: cfg.shots + "/light-layers-tab.png" });
      await page.locator(".rd-sidebar").screenshot({ path: cfg.shots + "/light-layers-panel.png" });
    }

    // Hide Dimensions.
    await page.click(".rd-layers label:has-text('Dimensions') input");
    await settle(() => {
      const pv = reader.pdfViewer.getPageView(0), c = document.querySelector(".page[data-page-number='1'] canvas");
      const k = c.width / pv.viewport.width, [x, y] = pv.viewport.convertToViewportPoint(300, 600);
      return c.getContext("2d").getImageData(Math.round(x * k), Math.round(y * k), 1, 1).data[2] > 200
        && c.getContext("2d").getImageData(Math.round(x * k), Math.round(y * k), 1, 1).data[0] > 200;
    });
    out.hidden = await state();

    // A locked layer can't be switched; a radio group keeps one on.
    out.lockedClick = await page.evaluate(() => { const i = [...document.querySelectorAll(".rd-layers input")].find((x) => x.closest("label").textContent.includes("Walls")); i.click(); return i.checked; });
    await page.click(".rd-layers label:has-text('Scheme B') input");
    await page.waitForTimeout(1000);
    out.radio = await state();

    // Another tab and back: this tab's choice stays.
    await showTab(1);
    out.otherTab = await page.evaluate(() => ({ tab: !reader.layersTab.hidden }));
    await showTab(2);
    await layersReady();
    await page.waitForTimeout(1500);
    await geometry();
    out.back = await state();
    out.backPanel = await page.evaluate(() => !reader.layersPanel.hidden && !reader.sidebar.hidden);

    // Printing draws the layers shown.
    await page.evaluate(async () => {
      window.__renders = [];
      const proto = Object.getPrototypeOf(await reader.doc.getPage(1));
      const render = proto.render;
      proto.render = function (params) {
        const rec = { intent: params.intent || "display", width: params.canvas?.width, config: null };
        window.__renders.push(rec);
        Promise.resolve(params.optionalContentConfigPromise).then((c) => {
          if (!c) return;
          rec.config = Object.fromEntries([...c].map(([, g]) => [g.name, g.visible]));
          rec.renderingIntent = c.renderingIntent;
        });
        return render.call(this, params);
      };
      window.print = () => { window.__printed = true; };
    });
    await page.evaluate(() => reader.print());
    await page.waitForFunction(() => window.__printed, null, { timeout: 20000 });
    await page.waitForTimeout(1500);  // (the pages made for printing are put away after a second)
    out.print = await page.evaluate(() => window.__renders.filter((r) => r.intent === "print"));

    // The thumbnails follow too.
    await page.evaluate(() => { window.__renders = []; });
    await page.click(".rd-layers label:has-text('Dimensions') input");
    await page.waitForTimeout(1500);
    out.thumbs = await page.evaluate(() => window.__renders.filter((r) => r.width && r.width < 400));
    await page.click(".rd-layers label:has-text('Dimensions') input");  // (hidden again)
    await page.waitForTimeout(1200);

    // Save layer visibility: a change, with Undo.
    await page.click(".rd-layeractions button:has-text('Save layer visibility')");
    await page.waitForFunction(() => reader.activeTab.dirty && reader.activeTab.undoDepth === 1, null, { timeout: 20000 });
    await layersReady();
    await page.waitForTimeout(1500);
    out.saved = await state();
    out.savedDefault = await page.evaluate(async () => {
      const c = await reader.doc.getOptionalContentConfig();  // as the file opens now
      return Object.fromEntries([...c].map(([, g]) => [g.name, g.visible]));
    });
    await page.evaluate(() => reader._undo());
    await page.waitForFunction(() => reader.activeTab.undoDepth === 0 && !reader.activeTab.dirty, null, { timeout: 20000 });
    await layersReady();
    out.undone = await page.evaluate(async () => {
      const c = await reader.doc.getOptionalContentConfig();
      return Object.fromEntries([...c].map(([, g]) => [g.name, g.visible]));
    });
    if (cfg.shots) {
      for (const theme of ["light", "dark"]) {
        const c2 = await browser.newContext({ viewport: { width: 1180, height: 820 }, colorScheme: theme });
        const p2 = await c2.newPage();
        watch(p2);
        await p2.goto(url + "&open=" + cfg.plan);
        await p2.waitForSelector(".page canvas", { timeout: 60000 });
        await p2.waitForFunction(() => reader.layers && reader.layers.locked.size === 1, null, { timeout: 30000 });
        await p2.evaluate(() => { if (reader.sidebar.hidden) reader.toggleSidebar(); });
        await p2.click(".rd-tabs .rd-tab:has-text('Layers')");
        await p2.click(".rd-layers label:has-text('Dimensions') input");
        await p2.waitForTimeout(1500);
        await p2.screenshot({ path: `${cfg.shots}/${theme}-1180-layers.png` });
        await p2.locator(".rd-sidebar").screenshot({ path: `${cfg.shots}/${theme}-layers-panel.png` });
        await c2.close();
      }
    }
    await ctx.close();
  } catch (e) {
    out.errors.push("script: " + (e.stack || e));
  } finally {
    await browser.close();
  }
  console.log(JSON.stringify(out));
})();
