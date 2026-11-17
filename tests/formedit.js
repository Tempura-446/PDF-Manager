/* Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE. */
// The Add form fields page in a browser: the fields a form already has are
// selected, renamed, moved, made required, switched to several lines,
// deleted and put back, next to a new field; the run sends the changes.
// With two files the file's own fields can't be changed.
// Run by test_form_edit_ui.py:
//   node formedit.js <page URL with #token=...> <a form> <another PDF> [shots folder]
// Prints one JSON object with what it found.
const { chromium } = require("playwright");

(async () => {
  const [url, formPath, otherPath, shots] = process.argv.slice(2);
  const out = { errors: [] };
  const browser = await chromium.launch();
  try {
    const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    await context.addInitScript(() => {
      window.__runs = [];
      const real = window.fetch;
      window.fetch = function (input, init) {
        if (String(input).includes("/api/run") && init && init.body instanceof FormData) {
          window.__runs.push(Object.fromEntries([...init.body.entries()].filter(([, v]) => typeof v === "string")));
        }
        return real.apply(this, arguments);
      };
    });
    const page = await context.newPage();
    page.on("pageerror", (e) => out.errors.push(String(e.message || e)));
    page.on("console", (m) => { if (m.type() === "error" && !/Failed to load resource/.test(m.text())) out.errors.push(m.text()); });
    await page.goto(url);
    await page.waitForSelector('.tools button[data-op="form"]', { state: "attached" });
    await page.dispatchEvent('.tools button[data-op="form"]', "click");
    await page.setInputFiles("#file-input", formPath);
    await page.waitForSelector(".re-old");
    await page.waitForTimeout(400);  // the field list may be given once more as the page settles
    const olds = () => page.$$eval(".re-old", (list) => list.map((b) => ({ cls: b.className, label: b.textContent, type: b.dataset.type })));
    const bar = () => page.evaluate(() => {
      const box = document.querySelector(".re-name-box"), type = box.querySelector("select.re-name");
      return { shown: box.style.visibility !== "hidden", name: box.querySelector("input.re-name").value, type: type.value,
        typeOff: type.disabled, types: [...type.options].filter((o) => !o.disabled).map((o) => o.value),
        required: !box.querySelector(".re-required").hidden && box.querySelector(".re-required input").checked,
        requiredShown: !box.querySelector(".re-required").hidden };
    });
    const centre = async (index) => {
      // (The boxes are drawn again whenever the page is: found afresh each time.)
      const b = await page.evaluate((i) => {
        const box = document.querySelectorAll(".re-old")[i];
        box.scrollIntoView({ block: "center" });  // clear of the bar at the bottom
        const r = box.getBoundingClientRect();
        return { x: r.x, y: r.y, width: r.width, height: r.height };
      }, index);
      return { x: b.x + b.width / 2, y: b.y + b.height / 2, box: b };
    };
    const pick = async (index) => { const c = await centre(index); await page.mouse.click(c.x, c.y); };
    // Where the page sits: it mustn't move when a field is picked or marked.
    const pageTop = () => page.evaluate(() => Math.round(document.querySelector(".re-stage").getBoundingClientRect().top + window.scrollY));
    out.tops = [await pageTop()];
    out.start = await olds();
    out.status0 = await page.textContent(".pb-status");

    // Price: renamed and moved.
    await pick(0);
    out.price = await bar();
    out.tops.push(await pageTop());
    await page.fill(".re-name-box input.re-name", "Net price");
    const place = () => page.$eval(".re-old", (e) => [e.offsetLeft, e.offsetTop]);  // on the page shown
    const from = await place();
    const c = await centre(0);
    await page.mouse.move(c.x, c.y);
    await page.mouse.down();
    await page.mouse.move(c.x + 150, c.y + 24, { steps: 4 });
    await page.mouse.up();
    const to = await place();
    out.movedBy = [Math.round(to[0] - from[0]), Math.round(to[1] - from[1])];

    // Tax: moved, then put back with its button.
    const t = await centre(1);
    await page.mouse.move(t.x, t.y);
    await page.mouse.down();
    await page.mouse.move(t.x + 60, t.y + 200, { steps: 3 });
    await page.mouse.up();
    out.taxMoved = (await olds())[1].cls.includes("changed");
    await page.click(".re-old.selected .re-revert");
    out.taxBack = !(await olds())[1].cls.includes("changed");
    // Total: required, and several lines.
    await pick(2);
    out.total = await bar();
    await page.check(".re-required input");
    await page.selectOption(".re-name-box select.re-name", "multiline");
    // Tax: sent to page 2 (it keeps its place on the paper).
    await pick(1);
    out.pageChoice = await page.$eval(".re-topage select", (el) => ({ shown: !el.closest("label").hidden, value: el.value, pages: el.options.length }));
    await page.selectOption(".re-topage select", "2");
    out.taxGoes = (await olds())[1].cls.includes("changed");
    // Agree: deleted with its button.
    await pick(3);
    out.agree = await bar();
    await page.click(".re-old.selected .re-delete");
    out.afterDelete = { status: await page.textContent(".pb-status"), note: await page.textContent(".re-note"), bar: (await bar()).shown };
    out.tops.push(await pageTop());
    // A radio button: deleted with the key, then kept after all.
    await pick(4);
    out.radio = await bar();
    await page.keyboard.press("Delete");
    out.radioDeleted = (await olds())[4].cls.includes("deleted");
    await page.click(".re-old.selected .re-delete");
    out.radioKept = !(await olds())[4].cls.includes("deleted");
    // A new field, on an empty part of the page.
    const stage = await (await page.$(".re-overlay")).boundingBox();
    await page.mouse.click(stage.x + stage.width * 0.7, stage.y + stage.height * 0.3);
    out.newFields = await page.$$eval(".re-field", (l) => l.length);
    out.end = await olds();
    out.status = await page.textContent(".pb-status");
    out.edits = JSON.parse(await page.inputValue("#f-edits"));
    if (shots) await page.screenshot({ path: `${shots}/form-edit.png` });

    await page.click("#run");
    await page.waitForSelector("#result:not([hidden])", { timeout: 60000 });
    out.result = await page.textContent("#result");
    out.sent = await page.evaluate(() => window.__runs.at(-1));

    // A second file: the first one's own fields are only shown.
    await page.setInputFiles("#file-input", otherPath);
    await page.waitForFunction(() => !document.querySelector(".re-old") && document.querySelector(".re-existing"), null, { timeout: 30000 });
    out.two = { edits: await page.inputValue("#f-edits"), striped: await page.$$eval(".re-existing", (l) => l.length) };
  } catch (e) {
    out.errors.push(String(e.stack || e));
  } finally {
    await browser.close();
  }
  console.log(JSON.stringify(out));
})();
