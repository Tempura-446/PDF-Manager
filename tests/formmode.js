/* Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE. */
// Prepare form mode in a browser: open via command palette, add fields by
// dragging and clicking, marquee-select, duplicate (Ctrl+D), move, resize,
// arrow-nudge, copy/paste across pages (Ctrl+C/Ctrl+V), delete, undo/redo,
// and run Find fields.
// Run by test_form_mode_ui.py:
//   node formmode.js <page URL with #token=...> <2-page blank PDF> <printed form PDF>
const { chromium } = require("playwright");

(async () => {
  const [url, blankId, printedId] = process.argv.slice(2);
  const out = { errors: [], apiCalls: [] };
  const browser = await chromium.launch();
  try {
    const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    await context.addInitScript(() => {
      window.__formApi = [];
      const real = window.fetch;
      window.fetch = function (input, init) {
        const u = String(input);
        if (u.includes("/api/form/")) window.__formApi.push(u);
        return real.apply(this, arguments);
      };
    });
    const page = await context.newPage();
    page.on("pageerror", (e) => out.errors.push(String(e.message || e)));
    page.on("console", (m) => { if (m.type() === "error" && !/Failed to load resource/.test(m.text())) out.errors.push(m.text()); });

    await page.goto(url + "&open=" + blankId);
    await page.waitForSelector('.page[data-page-number="1"] canvas', { timeout: 60000 });
    await page.waitForFunction(() => reader.doc && reader.loading.hidden, null, { timeout: 30000 });

    // Open Prepare form via command palette (Ctrl+K)
    await page.keyboard.press("Control+k");
    await page.waitForSelector('#palette-input');
    await page.fill('#palette-input', "Prepare form");
    await page.keyboard.press("Enter");
    await page.waitForSelector(".rd-formmodebar:not([hidden])");

    out.tools = await page.$$eval(".rd-formmodebar [data-form-tool]", (els) => els.map((e) => e.dataset.formTool));

    const p1 = await page.locator('.page[data-page-number="1"]').boundingBox();
    // Drag to add a Text field
    await page.click('.rd-formmodebar [data-form-tool="text"]');
    await page.mouse.move(p1.x + 80, p1.y + 80);
    await page.mouse.down();
    await page.mouse.move(p1.x + 240, p1.y + 110);
    await page.mouse.up();
    await page.waitForSelector('.rd-formfield[data-page="1"]');

    // Click to add a Checkbox field
    await page.click('.rd-formmodebar [data-form-tool="checkbox"]');
    await page.mouse.click(p1.x + 80, p1.y + 160);
    await page.waitForFunction(() => document.querySelectorAll('.rd-formfield[data-page="1"]').length === 2);
    out.afterAdd = await page.$$eval('.rd-formfield[data-page="1"]', (els) => els.map((e) => ({ type: e.dataset.type, name: e.dataset.name })));

    // Marquee-select both fields
    await page.mouse.move(p1.x + 50, p1.y + 50);
    await page.mouse.down();
    await page.mouse.move(p1.x + 270, p1.y + 200);
    await page.mouse.up();
    await page.waitForTimeout(100);
    out.marqueeSelected = await page.$$eval('.rd-formfield[data-page="1"].selected', (els) => els.length);

    // Duplicate (Ctrl+D) -> 4 fields on page 1
    await page.keyboard.press("Control+d");
    await page.waitForFunction(() => document.querySelectorAll('.rd-formfield[data-page="1"]').length === 4);
    await page.waitForTimeout(250);
    out.afterDup = await page.$$eval('.rd-formfield[data-page="1"]', (els) => els.length);

    // Click first field to select only 1, drag to move, resize, and nudge
    const f0 = await page.locator('.rd-formfield[data-page="1"]').first().boundingBox();
    await page.mouse.click(f0.x + f0.width / 2, f0.y + f0.height / 2);
    await page.waitForSelector('.rd-formfield[data-page="1"].selected .rd-formhandle.se');

    const beforeMove = await page.locator('.rd-formfield[data-page="1"].selected').boundingBox();
    await page.mouse.move(beforeMove.x + beforeMove.width / 2, beforeMove.y + beforeMove.height / 2);
    await page.mouse.down();
    await page.mouse.move(beforeMove.x + beforeMove.width / 2 + 40, beforeMove.y + beforeMove.height / 2 + 20);
    await page.mouse.up();
    await page.waitForTimeout(400);
    const afterMove = await page.locator('.rd-formfield[data-page="1"].selected').boundingBox();
    out.moved = { dx: Math.round(afterMove.x - beforeMove.x), dy: Math.round(afterMove.y - beforeMove.y) };

    // Resize via SE handle
    const se = await page.locator('.rd-formfield[data-page="1"].selected .rd-formhandle.se').boundingBox();
    await page.mouse.move(se.x + se.width / 2, se.y + se.height / 2);
    await page.mouse.down();
    await page.mouse.move(se.x + se.width / 2 + 30, se.y + se.height / 2 + 12);
    await page.mouse.up();
    await page.waitForTimeout(400);
    const afterResize = await page.locator('.rd-formfield[data-page="1"].selected').boundingBox();
    out.resized = { dw: Math.round(afterResize.width - afterMove.width), dh: Math.round(afterResize.height - afterMove.height) };

    // Arrow nudge (Shift+ArrowRight)
    await page.keyboard.press("Shift+ArrowRight");
    await page.waitForTimeout(600);
    const afterNudge = await page.locator('.rd-formfield[data-page="1"].selected').boundingBox();
    out.nudgedDx = Math.round(afterNudge.x - afterResize.x);

    // Copy (Ctrl+C), go to page 2, Paste (Ctrl+V)
    await page.keyboard.press("Control+c");
    await page.locator('.page[data-page-number="2"]').scrollIntoViewIfNeeded();
    await page.waitForSelector('.page[data-page-number="2"] canvas');
    await page.waitForTimeout(200);
    await page.keyboard.press("Control+v");
    await page.waitForSelector('.rd-formfield[data-page="2"]');
    out.pastedOnPage2 = await page.$$eval('.rd-formfield[data-page="2"]', (els) => els.length);

    // Delete on page 2, Undo (Ctrl+Z), Redo (Ctrl+Y)
    await page.keyboard.press("Delete");
    await page.waitForFunction(() => document.querySelectorAll('.rd-formfield[data-page="2"]').length === 0);
    out.afterDeleteP2 = await page.$$eval('.rd-formfield[data-page="2"]', (els) => els.length);

    await page.keyboard.press("Control+z");
    await page.waitForSelector('.rd-formfield[data-page="2"]');
    out.afterUndoP2 = await page.$$eval('.rd-formfield[data-page="2"]', (els) => els.length);

    await page.keyboard.press("Control+y");
    await page.waitForFunction(() => document.querySelectorAll('.rd-formfield[data-page="2"]').length === 0);
    out.afterRedoP2 = await page.$$eval('.rd-formfield[data-page="2"]', (els) => els.length);

    out.savePrimary = await page.$eval(".rd-formmodebar .rd-btn:has-text('Save')", (b) => b.classList.contains("primary"));

    // Now open a printed form in a fresh page and click Prepare form -> Find fields
    const page2 = await context.newPage();
    page2.on("pageerror", (e) => out.errors.push(String(e.message || e)));
    page2.on("console", (m) => { if (m.type() === "error" && !/Failed to load resource/.test(m.text())) out.errors.push(m.text()); });
    await page2.goto(url + "&open=" + printedId);
    await page2.waitForSelector('.rd-formbar:not([hidden])', { timeout: 60000 });
    await page2.click('.rd-formbar button:has-text("Prepare form")');
    await page2.waitForSelector('.rd-formmodebar:not([hidden])');
    await page2.click('.rd-formmodebar button:has-text("Find fields")');
    await page2.waitForSelector('.rd-formfield[data-page="1"]');
    out.foundFields = await page2.$$eval('.rd-formfield[data-page="1"]', (els) => els.length);

    out.apiCalls = await page.evaluate(() => window.__formApi);
  } finally {
    await browser.close();
  }
  console.log(JSON.stringify(out));
})();
