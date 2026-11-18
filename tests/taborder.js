/* Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE. */
// Tab order in Prepare form mode: numbered badges, sidebar Fields list,
// click to select, rename in place, drag to reorder, Order by rows/columns,
// and Tab key traversal in pdf.js.
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
    page.on("response", async (r) => { if (r.url().includes("/api/form/") && !r.ok()) out.errors.push(`${r.status()} ${r.url()}: ${await r.text().catch(() => "")}`); });

    await page.goto(url + "&open=" + fileId);
    await page.waitForSelector('.page[data-page-number="1"] canvas', { timeout: 60000 });
    await page.waitForFunction(() => reader.doc && reader.loading.hidden, null, { timeout: 30000 });

    await page.keyboard.press("Control+k");
    await page.waitForSelector("#palette-input");
    await page.fill("#palette-input", "Prepare form");
    await page.keyboard.press("Enter");
    await page.waitForSelector(".rd-formmodebar:not([hidden])");
    await page.waitForSelector('.rd-formfield[data-name="First"]');

    // Check numbered badges on fields
    out.initialBadges = await page.$$eval(".rd-formfield", (els) =>
      els.map((e) => ({ name: e.dataset.name, badge: e.querySelector(".rd-formbadge")?.textContent }))
    );

    // Open sidebar -> Fields tab
    await page.evaluate(() => { reader._setSidebar(true); reader._showSidebarTab("fields"); });
    await page.waitForSelector(".rd-fielditem");
    out.sidebarNames = await page.$$eval(".rd-fielditem .rd-fielditem-name", (els) => els.map((e) => e.textContent));

    // Click Third in sidebar -> selects Third
    await page.click('.rd-fielditem:has-text("Third")');
    await page.waitForSelector('.rd-formfield.selected[data-name="Third"]');

    // Double-click Third to rename in place -> Last
    await page.dblclick('.rd-fielditem:has-text("Third") .rd-fielditem-name');
    await page.waitForSelector(".rd-fielditem-edit");
    await page.fill(".rd-fielditem-edit", "Last");
    await page.keyboard.press("Enter");
    await page.waitForSelector('.rd-formfield[data-name="Last"]');

    // Drag Last above First in the sidebar list
    await page.evaluate(() => {
      const src = document.querySelector('.rd-fielditem[data-index="2"]');
      const dst = document.querySelector('.rd-fielditem[data-index="0"]');
      const dt = new DataTransfer();
      src.dispatchEvent(new DragEvent("dragstart", { bubbles: true, cancelable: true, dataTransfer: dt }));
      dst.dispatchEvent(new DragEvent("dragover", { bubbles: true, cancelable: true, dataTransfer: dt }));
      dst.dispatchEvent(new DragEvent("drop", { bubbles: true, cancelable: true, dataTransfer: dt }));
    });
    await page.waitForFunction(() => {
      const items = [...document.querySelectorAll(".rd-fielditem .rd-fielditem-name")].map((e) => e.textContent);
      return items[0] === "Last";
    });
    out.afterDragOrder = await page.$$eval(".rd-fielditem .rd-fielditem-name", (els) => els.map((e) => e.textContent));

    // Save and exit Prepare form mode
    await page.click('.rd-formmodebar button:has-text("Save")');
    await page.waitForFunction(() => !reader.activeTab?.dirty, null, { timeout: 15000 });
    await page.click('.rd-formmodebar button[title="Close Prepare form"]');
    await page.waitForSelector(".rd-formmodebar", { state: "hidden" });

    // Verify Tab order in pdf.js annotationLayer
    await page.waitForSelector('.annotationLayer input[name="Last"]');
    out.domOrder = await page.$$eval(".annotationLayer input", (els) => els.map((e) => e.getAttribute("name")));
    await page.click('.annotationLayer input[name="Last"]');
    const activeInfo = () => page.evaluate(() => {
      const a = document.activeElement;
      return a?.getAttribute("name") || `${a?.tagName}.${a?.className}`;
    });
    const tabSeq = [await activeInfo()];
    await page.keyboard.press("Tab");
    tabSeq.push(await activeInfo());
    await page.keyboard.press("Tab");
    tabSeq.push(await activeInfo());
    out.tabSeq = tabSeq;

    // Re-enter Prepare form and test "Order by rows" reset
    await page.keyboard.press("Control+k");
    await page.waitForSelector("#palette-input");
    await page.fill("#palette-input", "Prepare form");
    await page.keyboard.press("Enter");
    await page.waitForSelector(".rd-formmodebar:not([hidden])");
    await page.evaluate(() => { reader._setSidebar(true); reader._showSidebarTab("fields"); });
    await page.click('.rd-fields-actions button:has-text("Order by rows")');
    await page.waitForFunction(() => {
      const items = [...document.querySelectorAll(".rd-fielditem .rd-fielditem-name")].map((e) => e.textContent);
      return items[0] === "First";
    });
    out.afterRowsReset = await page.$$eval(".rd-fielditem .rd-fielditem-name", (els) => els.map((e) => e.textContent));
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
