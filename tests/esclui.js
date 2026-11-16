/* Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE. */
// 0.44.0 in a browser: a network scanner on the Scan page. Added by its
// address (a fake scanner answers), it is chosen; with the feeder, "Both
// sides" appears; Scan brings every page; a wrong address is refused in
// plain words; Forget takes it off the list.
// Run by test_escl_ui.py:
//   node esclui.js <page URL with #token=...> <the fake scanner's address> [shots folder]
// Prints one JSON object with what it found.
const { chromium } = require("playwright");

(async () => {
  const [url, address, shots] = process.argv.slice(2);
  const out = { errors: [] };
  const browser = await chromium.launch();
  try {
    const page = await (await browser.newContext({ viewport: { width: 1280, height: 900 } })).newPage();
    page.on("pageerror", (e) => out.errors.push(String(e.message || e)));
    await page.goto(url);
    await page.waitForSelector("#home-host .start-drop", { timeout: 30000 });
    await page.click("#home-host button[data-tool='scan']");
    await page.waitForSelector("#scan-host .scan-setup", { timeout: 10000 });
    await page.waitForFunction(() => scanState.status && scanState.devices, null, { timeout: 20000 });
    const msg = () => page.evaluate(() => (document.getElementById("scan-msg").hidden ? "" : document.getElementById("scan-msg").textContent)
      || document.getElementById("scan-said")?.textContent || "");
    out.before = await page.evaluate(() => ({ scannerOff: document.getElementById("scan-source-scanner").disabled,
      devices: scanState.devices.length, addHidden: document.getElementById("scan-add-row").hidden }));
    out.noneSaid = await msg();
    await page.check("#scan-source-scanner");

    // A wrong address first.
    await page.click("#scan-add-network");
    await page.waitForSelector("#scan-add-row:not([hidden])");
    await page.fill("#scan-address", "127.0.0.1:9");
    await page.click("#scan-address-add");
    await page.waitForFunction(() => /didn.t answer/.test(document.getElementById("scan-msg").textContent), null, { timeout: 30000 });
    out.wrong = await msg();

    await page.fill("#scan-address", address);
    await page.keyboard.press("Enter");
    await page.waitForFunction(() => scanState.devices.length === 1, null, { timeout: 30000 });
    out.added = await page.evaluate(() => ({ name: document.getElementById("scan-device").selectedOptions[0].textContent,
      addHidden: document.getElementById("scan-add-row").hidden, forget: !document.getElementById("scan-forget-network").hidden,
      duplexShown: !document.getElementById("scan-duplex-row").hidden, go: !document.getElementById("scan-go").disabled }));
    await page.check("#scan-from-feeder");
    await page.waitForTimeout(150);
    out.duplexWithFeeder = await page.evaluate(() => !document.getElementById("scan-duplex-row").hidden);
    await page.check("#scan-duplex");
    if (shots) await page.screenshot({ path: `${shots}/network-scanner.png` });
    await page.click("#scan-go");
    await page.waitForFunction(() => !scanState.busy && scanState.pages.length > 0, null, { timeout: 60000 });
    out.pages = await page.evaluate(() => document.querySelectorAll("#scan-strip .scan-tile").length);

    await page.click("#scan-forget-network");
    await page.waitForFunction(() => scanState.devices.length === 0, null, { timeout: 20000 });
    out.afterForget = await page.evaluate(() => document.getElementById("scan-device").selectedOptions[0].textContent);
  } catch (e) {
    out.errors.push(String(e.stack || e));
  } finally {
    await browser.close();
  }
  console.log(JSON.stringify(out));
})();
