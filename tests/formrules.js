/* Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE. */
// 0.56.0 in a browser: on the Add form fields page, a text box the form
// already has shows what it takes (Format) and what it adds up (Sum of),
// read from the file, and both can be changed; the run sends the changes.
// Run by test_form_rules_ui.py:
//   node formrules.js <page URL with #token=...> <a form> [shots folder]
// Prints one JSON object with what it found.
const { chromium } = require("playwright");

(async () => {
  const [url, formPath, shots] = process.argv.slice(2);
  const out = { errors: [] };
  const browser = await chromium.launch();
  try {
    const context = await browser.newContext({ viewport: { width: 1280, height: 900 }, locale: "en-US" });
    const page = await context.newPage();
    page.on("pageerror", (e) => out.errors.push(String(e.message || e)));
    page.on("console", (m) => { if (m.type() === "error" && !/Failed to load resource/.test(m.text())) out.errors.push(m.text()); });
    await page.goto(url);
    await page.waitForSelector('.tools button[data-op="form"]', { state: "attached" });
    await page.dispatchEvent('.tools button[data-op="form"]', "click");
    await page.setInputFiles("#file-input", formPath);
    await page.waitForSelector(".re-old");
    await page.waitForTimeout(400);  // the field list may be given once more as the page settles
    const pick = async (index) => {
      const b = await page.evaluate((i) => {
        const box = document.querySelectorAll(".re-old")[i];
        box.scrollIntoView({ block: "center" });
        const r = box.getBoundingClientRect();
        return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
      }, index);
      await page.mouse.click(b.x, b.y);
    };
    const rules = () => page.evaluate(() => {
      const box = document.querySelector(".re-textopts"), format = box.querySelector("select"), sum = box.querySelector("input");
      return { shown: !box.hidden, format: format.value, label: format.selectedOptions[0]?.textContent || "",
        choices: [...format.options].map((o) => o.value), sum: sum.value, hint: sum.placeholder };
    });
    const format = (value) => page.selectOption(".re-textopts select", value);
    const sum = (text) => page.fill(".re-textopts input", text);

    const pageTop = () => page.evaluate(() => Math.round(document.querySelector(".re-stage").getBoundingClientRect().top + window.scrollY));
    out.tops = [await pageTop()];
    await pick(0);                       // Price: an amount
    out.price = await rules();
    await format("percent");
    await format("amount");              // (and back: nothing to send)
    await pick(3);                       // When: a date the way the file has it
    out.when = await rules();
    if (shots) await page.screenshot({ path: `${shots}/form-rules.png` });
    await format("date");
    out.whenNow = await rules();
    await pick(2);                       // Total: the sum of Price and Tax
    out.total = await rules();
    out.tops.push(await pageTop());
    await sum("Price");
    await pick(4);                       // Notes: any text
    out.notes = await rules();
    await format("number");
    await sum("Price, Tax");
    await pick(1);                       // Tax: money
    out.tax = await rules();
    await format("");
    await pick(5);                       // Agree: a tick box has neither
    out.agree = await rules();
    out.tops.push(await pageTop());
    await pick(4);
    out.notesNow = await rules();
    out.status = await page.textContent(".pb-status");
    out.edits = JSON.parse(await page.inputValue("#f-edits"));
    await page.click("#run");
    await page.waitForSelector("#result:not([hidden])", { timeout: 60000 });
    out.result = await page.textContent("#result");
  } catch (e) {
    out.errors.push(String(e.stack || e));
  } finally {
    await browser.close();
  }
  console.log(JSON.stringify(out));
})();
