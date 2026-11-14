/* Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE. */
// 0.35.0: ZPDF's setup window (packaging/windows/setup.html) in a browser,
// its installer stood in for by a small script. Run by test_setup_ui.py:
//   node setupwin.js <JSON {ok, extras, bad: page addresses, shots}>
// Prints one JSON object with what it found.
const { chromium } = require("playwright");

function luminance([r, g, b]) {
  const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
}

(async () => {
  const cfg = JSON.parse(process.argv[2]);
  const out = { errors: [] };
  const browser = await chromium.launch();
  try {
    const open = async (url, options = {}) => {
      const ctx = await browser.newContext({ viewport: { width: 760, height: 640 }, ...options });
      const page = await ctx.newPage();
      page.on("pageerror", (e) => out.errors.push(String(e.message || e)));
      page.on("console", (m) => { if (m.type() === "error") out.errors.push(m.text()); });
      await page.goto(url);
      await page.waitForFunction(() => document.getElementById("where").textContent.length > 0);
      await page.evaluate(() => document.fonts.ready);
      return page;
    };
    const shot = async (page, name) => { if (cfg.shots) await page.screenshot({ path: `${cfg.shots}/setup-${name}.png` }); };
    // The contrast of some text against what is behind it (both as computed colours).
    const contrast = (page, selector) => page.evaluate((selector) => {
      const rgb = (c) => c.match(/[\d.]+/g).slice(0, 3).map(Number);
      const node = document.querySelector(selector);
      let back = node;
      while (back && /rgba?\(0, 0, 0, 0\)|transparent/.test(getComputedStyle(back).backgroundColor)) back = back.parentElement;
      return [rgb(getComputedStyle(node).color), rgb(getComputedStyle(back || document.body).backgroundColor)];
    }, selector).then(([a, b]) => {
      const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
      return Math.round(((hi + 0.05) / (lo + 0.05)) * 100) / 100;
    });
    const fits = (page) => page.evaluate(() => {
      const card = document.querySelector(".card").getBoundingClientRect();
      const main = document.querySelector("main");
      return document.documentElement.scrollWidth <= innerWidth && card.bottom <= innerHeight && card.top >= 0
        && main.scrollHeight <= main.clientHeight + 1
        && [...document.querySelectorAll("footer button:not([hidden])")].every((b) => { const r = b.getBoundingClientRect(); return r.right <= innerWidth && r.bottom <= innerHeight; });
    });
    const welcome = async (page) => ({
      title: await page.textContent("#title"), primary: await page.textContent("#primary"),
      focus: await page.evaluate(() => document.activeElement.id),
      choices: await page.evaluate(() => [...document.querySelectorAll(".choice")].map((c) => [c.querySelector("b").textContent, c.querySelector("input").checked])),
      fits: await fits(page), mark: await page.evaluate(() => !!document.querySelector("#mark svg")),
      fontLoaded: await page.evaluate(() => document.fonts.check('600 24px "Instrument Sans"')),
      targets: await page.evaluate(() => [...document.querySelectorAll(".choice, footer button:not([hidden])")].map((n) => Math.round(n.getBoundingClientRect().height))),
      contrast: await contrast(page, ".lead"), mutedContrast: await contrast(page, ".choice span"),
    });
    const end = async (page) => ({
      title: await page.textContent("#title"), primary: await page.textContent("#primary"), secondary: await page.textContent("#secondary"),
      focus: await page.evaluate(() => document.activeElement.id), msg: await page.textContent("#result-msg"),
      live: await page.textContent("#live"), copy: await page.isVisible("#copy"),
      facts: await page.evaluate(() => [...document.querySelectorAll("#facts li")].map((li) => [li.firstElementChild.textContent, li.lastElementChild.textContent])),
      log: await page.textContent("#fail-log"),
    });

    // ---- an installation from start to end
    let page = await open(cfg.ok);
    out.welcome = await welcome(page);
    await shot(page, "light-welcome");
    out.tabbed = [];
    await page.evaluate(() => document.activeElement.blur());  // from the top of the page
    for (let i = 0; i < 7; i++) { await page.keyboard.press("Tab"); out.tabbed.push(await page.evaluate(() => document.activeElement.id)); }
    await page.focus("#opt-desktop");
    out.ring = await page.evaluate(() => { document.getElementById("opt-desktop").focus(); return true; })
      && await page.evaluate(async () => { const b = document.getElementById("secondary"); b.focus(); return b.matches(":focus"); });
    await page.focus("#opt-extras");
    await page.keyboard.press("Space");
    out.spaceTicks = await page.isChecked("#opt-extras");
    await page.keyboard.press("Space");
    await page.click("#primary");
    await page.waitForSelector("#installing:not([hidden])");
    const progress = [];
    let installing = null;
    await page.click("#details summary");
    while (await page.isVisible("#installing")) {
      progress.push(Number(await page.getAttribute("#bar", "aria-valuenow")));
      const now = await page.evaluate(() => document.querySelector(".step.now b")?.textContent || "");
      if (now && !installing) {
        installing = {
          title: await page.textContent("#title"),
          steps: await page.evaluate(() => [...document.querySelectorAll(".step b")].map((b) => b.textContent)),
          buttonsHidden: !(await page.isVisible("#primary")) && !(await page.isVisible("#secondary")),
          foot: await page.textContent("#foot"), current: now,
          detailsOpen: await page.evaluate(() => document.getElementById("details").open),
        };
      }
      if (now === "ZPDF and its packages") await shot(page, "light-installing");
      await page.waitForTimeout(60);
    }
    out.installing = { ...installing, progress, log: await page.textContent("#log") };
    await page.waitForSelector("#done:not([hidden])");
    out.done = await end(page);
    await shot(page, "light-done");
    await page.context().close();

    // ---- with the two programs, and without the desktop shortcut
    page = await open(cfg.extras);
    await page.uncheck("#opt-desktop");
    await page.check("#opt-extras");
    await page.click("#primary");
    await page.waitForSelector("#done:not([hidden])", { timeout: 30000 });
    out.extras = { ...(await end(page)), steps: await page.evaluate(() => [...document.querySelectorAll(".step b")].map((b) => b.textContent)) };
    await shot(page, "light-done-extras");
    await page.context().close();

    // ---- a failure
    page = await open(cfg.bad);
    await page.click("#primary");
    await page.waitForSelector("#done:not([hidden])", { timeout: 30000 });
    out.failed = { ...(await end(page)), more: await page.textContent("#result-more") };
    await page.click("#fail-details summary");
    await shot(page, "light-failed");
    await page.context().close();

    // ---- dark, and a small window
    page = await open(cfg.fresh, { colorScheme: "dark" });
    out.dark = { contrast: await contrast(page, ".lead"), mutedContrast: await contrast(page, ".choice span"), fits: await fits(page) };
    await shot(page, "dark-welcome");
    await page.context().close();
    page = await open(cfg.fresh, { viewport: { width: 608, height: 512 } });
    out.small = { fits: await fits(page), targets: await page.evaluate(() => [...document.querySelectorAll(".choice, footer button:not([hidden])")].map((n) => Math.round(n.getBoundingClientRect().height))) };
    await shot(page, "small-welcome");
    await page.context().close();
    // What Edge leaves of the window it is asked for (780 x 720), and of the 760 x 640 the Windows check saw scroll.
    for (const [name, viewport] of [["asked", { width: 764, height: 681 }], ["old", { width: 744, height: 601 }]]) {
      page = await open(cfg.fresh, { viewport });
      out[name] = { fits: await fits(page) };
      await shot(page, `${name}-welcome`);
      await page.context().close();
    }
  } catch (e) {
    out.errors.push(String(e.stack || e));
  } finally {
    await browser.close();
  }
  console.log(JSON.stringify(out));
})();
