/* Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE. */
// 0.41.0 in a browser: a PDF protected for particular people isn't called
// damaged. The reader says what it is and offers Open with my Digital ID;
// the wrong ID is refused in plain words, the right one opens an
// unprotected copy in a new tab.
// Run by test_certcrypt_ui.py:
//   node certopen.js <page URL with #token=...> <id of the protected file> <Eve's .pfx> <Ann's .pfx> <password> [shots folder]
// Prints one JSON object with what it found.
const { chromium } = require("playwright");

(async () => {
  const [url, id, wrongId, rightId, password, shots] = process.argv.slice(2);
  const out = { errors: [] };
  let page0 = null;
  const browser = await chromium.launch();
  try {
    const page = await (await browser.newContext({ viewport: { width: 1400, height: 900 } })).newPage();
    page0 = page;
    page.on("pageerror", (e) => out.errors.push(String(e.message || e)));
    await page.goto(url + "&open=" + id);
    await page.waitForSelector(".rd-broken:not([hidden])", { timeout: 60000 });
    await page.waitForSelector(".rd-broken-openid:not([hidden])", { timeout: 30000 });
    out.card = await page.evaluate(() => ({ why: document.querySelector(".rd-broken-why").textContent,
      repair: !document.querySelector(".rd-broken-repair").hidden, passwordDialog: !!document.querySelector("dialog[open]") }));
    if (shots) await page.screenshot({ path: `${shots}/for-people.png` });

    const withId = async (file) => {
      await page.click(".rd-broken-openid");
      await page.waitForSelector("dialog[open] input[type=file]");
      await page.setInputFiles("dialog[open] input[type=file]", file);
      await page.fill("dialog[open] input[type=password]", password);
      await page.click("dialog[open] button:has-text('Open')");
    };
    await withId(wrongId);
    await page.waitForFunction(() => document.querySelector(".rd-broken-said").textContent.length > 0, null, { timeout: 30000 });
    out.wrong = await page.textContent(".rd-broken-said");

    await withId(rightId);
    await page.waitForSelector(".page canvas", { timeout: 60000 });
    await page.waitForFunction(() => reader.doc && reader.loading.hidden, null, { timeout: 30000 });
    await page.waitForSelector(".textLayer span", { timeout: 30000 });
    out.opened = await page.evaluate(() => ({ tabs: reader.tabs.map((t) => t.name), active: reader.activeTab.name,
      text: [...document.querySelectorAll(".textLayer span")].map((s) => s.textContent).join(" "),
      unsaved: !!reader.activeTab.unsaved }));
  } catch (e) {
    out.errors.push(String(e.stack || e));
    out.said = await page0?.evaluate(() => ({ said: document.querySelector(".rd-broken-said")?.textContent, tabs: reader.tabs.map((t) => [t.name, !!t.broken]), dialog: document.querySelector("dialog[open]")?.textContent })).catch(() => null);
  } finally {
    await browser.close();
  }
  console.log(JSON.stringify(out));
})();
