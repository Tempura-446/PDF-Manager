/* Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE. */
// 0.33.0 in a browser: the reader's paragraph editor shows which words are
// bold, italic or coloured; changing a plain word leaves them so; Ctrl+B on
// a chosen word makes just that word bold. Run by test_reflow_styles.py:
//   node fix33.js <page URL with #token=...> <JSON with ids>
// Prints one JSON object with what it found (the changed files as base64).
const { chromium } = require("playwright");

(async () => {
  const [url, extraJson] = process.argv.slice(2);
  const cfg = JSON.parse(extraJson);
  const out = { errors: [], native: [] };
  const browser = await chromium.launch();
  try {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
    const page = await ctx.newPage();
    page.on("pageerror", (e) => out.errors.push(String(e.message || e)));
    page.on("console", (m) => { if (m.type() === "error" && !/Failed to load resource/.test(m.text())) out.errors.push(m.text()); });
    page.on("dialog", (d) => { out.native.push(d.type() + ": " + d.message()); d.dismiss().catch(() => {}); });
    const settled = () => page.waitForFunction(() => reader.doc && reader.loading.hidden && document.querySelector(".page canvas"), null, { timeout: 30000 });
    const bytes = () => page.evaluate(async () => {
      const b = new Uint8Array(await reader._currentBytes());
      let s = "";
      for (let i = 0; i < b.length; i += 0x8000) s += String.fromCharCode(...b.subarray(i, i + 0x8000));
      return btoa(s);
    });
    // Shift+click a line of the paragraph: the whole paragraph, with its words' styles.
    const openParagraph = async () => {
      await page.evaluate(() => reader.useTool("EDITTEXT"));
      await page.waitForTimeout(400);
      const at = await page.evaluate(() => {
        const s = [...document.querySelectorAll(".page[data-page-number='1'] .textLayer span")].find((x) => /quarterly/.test(x.textContent));
        s.scrollIntoView({ block: "center" });
        const r = s.getBoundingClientRect();
        return [r.left + 10, r.top + r.height / 2];
      });
      await page.waitForTimeout(300);
      await page.keyboard.down("Shift");
      await page.mouse.click(at[0], at[1]);
      await page.keyboard.up("Shift");
      await page.waitForSelector(".rd-textedit-box .rd-textedit-rich", { timeout: 10000 });
      await page.waitForFunction(() => document.querySelectorAll(".rd-textedit-rich span").length > 1, null, { timeout: 10000 });
    };
    const pieces = () => page.evaluate(() => [...document.querySelectorAll(".rd-textedit-rich span")].map((s) => {
      const st = getComputedStyle(s);
      return [s.textContent, parseInt(st.fontWeight, 10) >= 600, /italic|oblique/.test(st.fontStyle), st.color];
    }));
    // Choose a word in the field (by its text).
    const choose = (word) => page.evaluate((word) => {
      const field = document.querySelector(".rd-textedit-rich");
      const walker = document.createTreeWalker(field, NodeFilter.SHOW_TEXT);
      for (let n = walker.nextNode(); n; n = walker.nextNode()) {
        const i = n.data.indexOf(word);
        if (i < 0) continue;
        const r = document.createRange();
        r.setStart(n, i); r.setEnd(n, i + word.length);
        getSelection().removeAllRanges(); getSelection().addRange(r);
        return true;
      }
      return false;
    }, word);
    const applied = async () => {
      await page.waitForFunction(() => reader.activeTab.dirty && reader.loading.hidden && reader.doc && !document.querySelector(".rd-textedit-box"), null, { timeout: 30000 });
      await settled();
      await page.waitForTimeout(600);
    };

    await page.goto(url + "&open=" + cfg.a);
    await page.waitForSelector(".page canvas", { timeout: 60000 });
    await settled();
    await page.waitForTimeout(800);

    // 1. The editor shows the styles; a plain word is changed by typing.
    await openParagraph();
    out.shown = await pieces();
    if (cfg.shots) await page.screenshot({ path: cfg.shots + "/paragraph-editor.png" });
    out.value = await page.evaluate(() => document.querySelector(".rd-textedit-rich").value);
    // A colour picked for the whole paragraph shows on its plain words at once; "record" keeps its red.
    out.picked = await page.evaluate(() => {
      const c = document.querySelector(".rd-tf-color");
      c.value = "#0000ff"; c.dispatchEvent(new Event("input", { bubbles: true }));
      const col = (word) => getComputedStyle([...document.querySelectorAll(".rd-textedit-rich span")].find((s) => s.textContent.includes(word))).color;
      const got = { plain: col("quarterly"), red: col("record") };
      c.value = "#000000"; c.dispatchEvent(new Event("input", { bubbles: true }));
      return got;
    });
    out.chose = await choose("new");
    await page.keyboard.type("returning");
    await page.keyboard.press("Control+Enter");
    await applied();
    out.typed = await bytes();

    // 2. Ctrl+B on a chosen word: only that word.
    await openParagraph();
    await choose("growth");
    await page.keyboard.press("Control+b");
    out.afterBold = await pieces();
    await page.keyboard.press("Control+Enter");
    await applied();
    out.bolded = await bytes();

    // 4 (below) needs this tab left as it is; 3. Undo twice: the page as it was.
    await page.evaluate(() => reader.useTool("EDITTEXT"));
    await page.focus(".rd-container");
    await page.keyboard.press("Control+z");
    await page.waitForTimeout(1500);
    await page.keyboard.press("Control+z");
    await page.waitForFunction(() => !reader.activeTab.dirty, null, { timeout: 15000 });
    out.undone = true;

    // 4. Two paragraphs set without a gap: Shift+click takes the one clicked.
    //    And with a caret already on the page, Shift+click still opens the editor.
    await page.goto("about:blank");  // (only the part after # differs: a fresh load)
    await page.goto(url + "&open=" + cfg.b);
    await page.waitForSelector(".page canvas", { timeout: 60000 });
    await settled();
    await page.waitForTimeout(800);
    await page.evaluate(() => reader.useTool("EDITTEXT"));
    await page.waitForFunction(() => [...document.querySelectorAll(".page[data-page-number='1'] .textLayer span")].some((x) => x.textContent.includes("please")), null, { timeout: 15000 });
    await page.waitForTimeout(400);
    const lineAt = (word) => page.evaluate((word) => {
      const s = [...document.querySelectorAll(".page[data-page-number='1'] .textLayer span")].find((x) => x.textContent.includes(word));
      const r = s.getBoundingClientRect();
      return [r.left + 12, r.top + r.height / 2];
    }, word);
    const shiftClick = async (word) => {
      const at = await lineAt(word);
      await page.keyboard.down("Shift");
      await page.mouse.click(at[0], at[1]);
      await page.keyboard.up("Shift");
      await page.waitForSelector(".rd-textedit-box .rd-textedit-rich", { timeout: 10000 });
      await page.waitForTimeout(300);
      return page.evaluate(() => document.querySelector(".rd-textedit-rich").value);
    };
    // A caret on the page first (as a click or an earlier change leaves one).
    out.caret = await page.evaluate(() => {
      const s = [...document.querySelectorAll(".page[data-page-number='1'] .textLayer span")].find((x) => x.textContent.includes("Elsewhere"));
      const r = document.createRange();
      r.setStart(s.firstChild, 2); r.collapse(true);
      getSelection().removeAllRanges(); getSelection().addRange(r);
      return getSelection().type;
    });
    out.second = await shiftClick("please");
    // The box takes the look of the paragraph's main words, not of its bold first word.
    out.noteLook = await page.evaluate(() => {
      const f = document.querySelector(".rd-textedit-rich");
      return { weight: parseInt(getComputedStyle(f).fontWeight, 10), pressed: document.querySelector(".rd-tf-bold").getAttribute("aria-pressed"),
        first: parseInt(getComputedStyle(f.querySelector("span")).fontWeight, 10) };
    });
    await page.keyboard.press("Escape");
    await page.waitForTimeout(300);
    out.first = await shiftClick("workshop");
    await page.keyboard.press("Escape");
  } catch (e) {
    out.errors.push(String(e.stack || e));
  } finally {
    await browser.close();
  }
  console.log(JSON.stringify(out));
})();
