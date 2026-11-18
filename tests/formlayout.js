/* Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE. */
// Layout help in Prepare form mode: snapping guides while dragging,
// Align left, Same size, Distribute, Copy to pages, and Make a table.
// Run by test_form_layout_ui.py:
//   node formlayout.js <page URL with #token=...> <2-page PDF id>
const { chromium } = require("playwright");

(async () => {
  const [url, fileId] = process.argv.slice(2);
  const out = { errors: [] };
  const browser = await chromium.launch();
  try {
    const context = await browser.newContext({ viewport: { width: 1360, height: 900 } });
    const page = await context.newPage();
    page.on("pageerror", (e) => out.errors.push(String(e.message || e)));
    page.on("console", (m) => { if (m.type() === "error" && !/Failed to load resource/.test(m.text())) out.errors.push(m.text()); });

    await page.goto(url + "&open=" + fileId);
    await page.waitForSelector('.page[data-page-number="1"] canvas', { timeout: 60000 });
    await page.waitForFunction(() => reader.doc && reader.loading.hidden, null, { timeout: 30000 });

    await page.keyboard.press("Control+k");
    await page.waitForSelector("#palette-input");
    await page.fill("#palette-input", "Prepare form");
    await page.keyboard.press("Enter");
    await page.waitForSelector(".rd-formmodebar:not([hidden])");
    await page.waitForSelector('.rd-formfield[data-name="A"]');

    // 1) Drag B horizontally near A's left edge -> snapping guide appears!
    const boxA = await page.locator('.rd-formfield[data-name="A"]').boundingBox();
    const boxB = await page.locator('.rd-formfield[data-name="B"]').boundingBox();
    await page.mouse.move(boxB.x + boxB.width / 2, boxB.y + boxB.height / 2);
    await page.mouse.down();
    await page.mouse.move(boxA.x + boxB.width / 2 + 2, boxB.y + boxB.height / 2, { steps: 6 });
    out.guideCountDuringDrag = await page.locator(".rd-formguide").count();
    await page.mouse.up();
    await page.waitForTimeout(250);

    // 2) Multi-select A, B, C -> Align left, Same size, Distribute down
    await page.click('.rd-formfield[data-name="A"]');
    await page.keyboard.down("Shift");
    await page.click('.rd-formfield[data-name="B"]');
    await page.click('.rd-formfield[data-name="C"]');
    await page.keyboard.up("Shift");
    await page.waitForSelector(".fp-prop-btngrid");
    await page.click('.fp-formprops button:has-text("Align left")');
    await page.waitForTimeout(250);
    await page.click('.fp-formprops button:has-text("Same size")');
    await page.waitForTimeout(250);
    await page.click('.fp-formprops button:has-text("Distribute down")');
    await page.waitForTimeout(250);

    // 3) Select A -> Copy to pages (all, same name)
    await page.click('.rd-formfield[data-name="A"]');
    await page.waitForSelector('.fp-formprops button:has-text("Copy to pages")');
    await page.click('.fp-formprops button:has-text("Copy to pages")');
    await page.waitForTimeout(300);

    // 4) Select C -> Make a table (2x2)
    await page.click('.rd-formfield[data-name="C"]');
    await page.waitForSelector('.fp-formprops button:has-text("Make a table")');
    await page.click('.fp-formprops button:has-text("Make a table")');
    await page.waitForTimeout(350);

    // Save to disk
    await page.click('.rd-formmodebar button:has-text("Save")');
    await page.waitForFunction(() => !reader.activeTab?.dirty, null, { timeout: 15000 });

    console.log(JSON.stringify(out));
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
