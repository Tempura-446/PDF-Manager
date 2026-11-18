/* Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE. */
// Field properties panel in a browser: edit General, Look, Text, Format,
// Calculation (Pick fields on page), multi-select shared properties, Save,
// and verify filling in (required, comb, currency format, sum).
// Run by test_form_props_ui.py:
//   node formprops.js <page URL with #token=...> <pdf file id>
const { chromium } = require("playwright");

async function waitSaved(page) {
  await page.waitForFunction(() => !reader.activeTab?.dirty, null, { timeout: 15000 });
}

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

    // Open Prepare form via command palette
    await page.keyboard.press("Control+k");
    await page.waitForSelector("#palette-input");
    await page.fill("#palette-input", "Prepare form");
    await page.keyboard.press("Enter");
    await page.waitForSelector(".rd-formmodebar:not([hidden])");
    await page.waitForSelector('.rd-formfield[data-name="Box 1"]');

    // 1) Select Box 1 -> rename to Qty, mark Required, Comb, Max length 4
    await page.click('.rd-formfield[data-name="Box 1"]');
    await page.waitForSelector(".fp-formprops:not([hidden])");

    const setInput = async (labelText, val) => {
      const inp = page.locator(`.fp-prop-row:has(.fp-prop-lbl:text-is("${labelText}")) input`);
      await inp.fill(String(val));
      await inp.press("Enter");
      await inp.dispatchEvent("change");
    };
    const setCheck = async (labelText) => {
      await page.locator(`.fp-prop-check:has-text("${labelText}") input`).click();
    };
    const setSelect = async (labelText, val) => {
      await page.locator(`.fp-prop-row:has(.fp-prop-lbl:text-is("${labelText}")) select`).selectOption(String(val));
    };

    await setInput("Name", "Qty");
    await page.waitForSelector('.rd-formfield.selected[data-name="Qty"]');
    await setCheck("Required");
    await page.waitForTimeout(200);
    await setCheck("Comb (one box per character)");
    await page.waitForTimeout(200);
    await setInput("Max length", "4");
    await page.waitForTimeout(200);

    // 2) Select Box 2 -> rename to Price, Format = currency ($), Decimals = 2
    await page.click('.rd-formfield[data-name="Box 2"]');
    await setInput("Name", "Price");
    await page.waitForSelector('.rd-formfield.selected[data-name="Price"]');
    await setSelect("Format", "currency");
    await page.waitForTimeout(250);
    await setInput("Symbol", "$");
    await page.waitForTimeout(200);

    // 3) Select Box 3 -> rename to Total, Calculate = sum, Pick fields on page
    await page.click('.rd-formfield[data-name="Box 3"]');
    await setInput("Name", "Total");
    await page.waitForSelector('.rd-formfield.selected[data-name="Total"]');
    await setSelect("Calculate", "sum");
    await page.waitForTimeout(250);
    await page.click('.fp-formprops button:has-text("Pick fields on page")');
    // Uncheck default-picked or pick Qty & Price: let's read current checkboxes, clear if needed, or click Price/Qty
    // Actually when switching to "sum", formprops auto-picks first 2 fields (Qty, Price). Let's verify pick-on-page toggles!
    await page.click('.rd-formfield[data-name="Qty"]');
    await page.waitForTimeout(250);
    await page.click('.rd-formfield[data-name="Qty"]');
    await page.waitForTimeout(250);
    await page.click('.fp-formprops button:has-text("Done picking")');

    // 4) Multi-select Qty and Price (Shift+click) -> Alignment = right
    await page.click('.rd-formfield[data-name="Qty"]');
    await page.keyboard.down("Shift");
    await page.click('.rd-formfield[data-name="Price"]');
    await page.keyboard.up("Shift");
    out.multiBadge = await page.locator(".fp-prop-title .fp-fact").textContent();
    await setSelect("Alignment", "right");
    await page.waitForTimeout(250);

    // 5) Save and exit Prepare form mode
    await page.click('.rd-formmodebar button:has-text("Save")');
    await waitSaved(page);
    await page.click('.rd-formmodebar button[title="Close Prepare form"]');
    await page.waitForSelector(".rd-formmodebar", { state: "hidden" });

    // 6) Verify filling in in the reader (annotationLayer inputs)
    await page.waitForSelector('.annotationLayer input[data-element-id]', { timeout: 15000 });
    const inputs = page.locator('.annotationLayer input[type="text"], .annotationLayer input:not([type])');
    const count = await inputs.count();
    out.widgetCount = count;
    // Qty is first widget, Price is second, Total is third
    const qtyIn = inputs.nth(0);
    const priceIn = inputs.nth(1);
    const totalIn = inputs.nth(2);

    out.qtyMaxLen = await qtyIn.getAttribute("maxlength");
    out.qtyRequired = await qtyIn.evaluate((el) => el.required || el.closest(".textWidgetAnnotation")?.classList.contains("required") || el.getAttribute("aria-required") === "true");

    await qtyIn.fill("2");
    await qtyIn.press("Tab");
    await priceIn.fill("5");
    await priceIn.press("Tab");
    await page.waitForTimeout(300);

    out.priceVal = await priceIn.inputValue();
    out.totalVal = await totalIn.inputValue();

    console.log(JSON.stringify(out));
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
