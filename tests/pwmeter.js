/* Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE. */
// 0.40.0 in a browser: Add password says how strong the password is while
// it's typed, and nothing before one is typed.
// Run by test_protection_ui.py:
//   node pwmeter.js <page URL with #token=...> [shots folder]
// Prints one JSON object with what it found.
const { chromium } = require("playwright");

(async () => {
  const [url, shots] = process.argv.slice(2);
  const out = { errors: [], steps: [] };
  const browser = await chromium.launch();
  try {
    const page = await (await browser.newContext({ viewport: { width: 1280, height: 900 } })).newPage();
    page.on("pageerror", (e) => out.errors.push(String(e.message || e)));
    page.on("console", (m) => { if (m.type() === "error" && !/Failed to load resource/.test(m.text())) out.errors.push(m.text()); });
    await page.goto(url);
    await page.waitForSelector('.tools button[data-op="encrypt"]', { state: "attached" });
    await page.dispatchEvent('.tools button[data-op="encrypt"]', "click");
    await page.waitForSelector("#f-new_password");
    const meter = () => page.$eval(".pw-meter", (m) => ({ score: m.dataset.score, words: m.querySelector(".pw-words").textContent,
      lit: [...m.querySelectorAll(".pw-bars i")].filter((i) => getComputedStyle(i).backgroundColor !== getComputedStyle(m.querySelector(".pw-bars i:last-child")).backgroundColor || m.dataset.score === "4").length }));
    out.steps.push(await meter());
    for (const typed of ["password", "Tr0ub4dor", "correct horse battery staple", ""]) {
      await page.fill("#f-new_password", typed);
      await page.waitForFunction((want) => {
        const m = document.querySelector(".pw-meter");
        return want ? m.dataset.score !== "" && m.querySelector(".pw-words").textContent : m.dataset.score === "";
      }, typed, { timeout: 10000 });
      await page.waitForTimeout(350);
      out.steps.push(await meter());
      if (shots && typed.startsWith("correct")) await page.screenshot({ path: `${shots}/pw-meter.png` });
    }
    out.meters = await page.$$eval(".pw-meter", (l) => l.length);  // only on the password to open
  } catch (e) {
    out.errors.push(String(e.stack || e));
  } finally {
    await browser.close();
  }
  console.log(JSON.stringify(out));
})();
