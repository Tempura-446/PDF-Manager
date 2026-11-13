// 0.26.0 in a browser: the Scan page. From the start screen's "Scan paper":
// scanners listed (a fake WIA behind the server), every page from the
// feeder arriving one by one, flatbed pages one per press with "Scan
// another page", pages turned, taken out, dragged into another order and
// scanned again; an empty feeder, a busy scanner and Cancel said plainly;
// phone photos added (one page found, one kept as taken); Make PDF opening
// the PDF in the reader, not saved yet. With cfg.mode "nowia": the Scanner
// choice is off, with the reason, and Photos still works.
// Run by test_scan26_web.py:
//   node scan26.js <page URL with #token=...> <JSON config>
// Prints one JSON object with what it found.
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
    const shot = async (name) => {
      if (!cfg.shots) return;
      await page.waitForTimeout(500);
      await page.screenshot({ path: `${cfg.shots}/${name}.png` });
    };
    const theme = async (v) => { await page.evaluate((t) => applyTheme(t), v); await page.waitForTimeout(250); };
    const state = () => page.evaluate(() => ({
      count: document.querySelectorAll("#scan-strip .scan-tile").length,
      ids: [...document.querySelectorAll("#scan-strip .scan-tile")].map((t) => t.dataset.id),
      labels: [...document.querySelectorAll("#scan-strip .scan-tile")].map((t) => t.getAttribute("aria-label")),
      caps: [...document.querySelectorAll("#scan-strip .scan-cap")].map((t) => t.textContent),
      status: document.getElementById("scan-status").textContent,
      msg: document.getElementById("scan-msg").hidden ? "" : document.getElementById("scan-msg").textContent,
      go: document.getElementById("scan-go-label")?.textContent,
      busy: scanState.busy,
    }));
    const pickScanner = async (name) => {
      await page.selectOption("#scan-device", { label: name });
      await page.waitForTimeout(150);
    };
    const idle = () => page.waitForFunction(() => !scanState.busy, null, { timeout: 120000 });

    await page.goto(url);
    await page.waitForSelector("#home-host .start-drop", { timeout: 30000 });
    out.start = await page.evaluate(() => [...document.querySelectorAll("#home-host .start-acts button")].map((b) => b.textContent.trim()));
    await page.click("#home-host button[data-tool='scan']");
    await page.waitForSelector("#scan-host .scan-setup", { timeout: 10000 });
    await page.waitForFunction(() => scanState.status, null, { timeout: 10000 });
    out.title = await page.evaluate(() => document.getElementById("title").textContent);
    out.status = await page.evaluate(() => scanState.status);
    out.scannerRadio = await page.evaluate(() => ({ disabled: document.getElementById("scan-source-scanner").disabled,
      why: document.getElementById("scan-why").hidden ? "" : document.getElementById("scan-why").textContent,
      photosShown: !document.getElementById("scan-photos-part").hidden }));
    // Keyboard: the source choice is a radio group reached with Tab.
    out.tabbable = await page.evaluate(() => [...document.querySelectorAll("#scan-host input, #scan-host select, #scan-host button, #scan-host [tabindex='0']")]
      .filter((x) => x.offsetParent !== null && !x.disabled && x.tabIndex >= 0).length);
    await theme("light");
    await shot("scan-empty-light");
    await theme("dark");
    await shot("scan-empty-dark");
    await theme("light");

    if (cfg.mode === "full") {
      await page.waitForFunction(() => scanState.devices && scanState.devices.length, null, { timeout: 15000 });
      out.devices = await page.evaluate(() => [...document.querySelectorAll("#scan-device option")].map((o) => o.textContent));
      // Every page in the feeder, arriving one by one.
      await pickScanner("Office scanner");
      await page.check("#scan-from-feeder");
      await page.click("#scan-go");
      const seen = new Set();
      while (await page.evaluate(() => scanState.busy)) {
        seen.add((await state()).count);
        await page.waitForTimeout(120);
      }
      out.feederSeen = [...seen].sort();
      out.feeder = await state();
      // The glass: one page a press, then "Scan another page".
      await page.check("#scan-from-flatbed");
      await page.click("#scan-go");
      await idle();
      out.flatbed = await state();
      await page.click("#scan-add");
      await idle();
      out.another = await state();
      // Turn page 2 right, take page 3 out (and put it back with Undo, then out again).
      await page.click("#scan-strip .scan-tile:nth-child(2) .turn-right", { force: true });
      out.turned = await page.evaluate(() => ({ turn: scanState.pages[1].turn,
        transform: document.querySelector("#scan-strip .scan-tile:nth-child(2) img")?.style.transform }));
      const third = (await state()).ids[2];
      await page.click("#scan-strip .scan-tile:nth-child(3) .remove", { force: true });
      out.removed = await state();
      out.removedThird = !out.removed.ids.includes(third);
      // Keyboard: Alt+Right moves the focused page.
      await page.focus("#scan-strip .scan-tile:nth-child(1)");
      const firstId = (await state()).ids[0];
      await page.keyboard.press("Alt+ArrowRight");
      out.keyMoved = (await state()).ids.indexOf(firstId);
      out.keyFocus = await page.evaluate(() => document.activeElement?.dataset?.id);
      out.keyFocusId = firstId;
      // Drag the last page in front of the first.
      const before = (await state()).ids;
      await page.locator("#scan-strip .scan-tile").last().dragTo(page.locator("#scan-strip .scan-tile").first(), { targetPosition: { x: 10, y: 60 } });
      await page.waitForTimeout(300);
      out.dragged = { before, after: (await state()).ids };
      // Scan page 1 again: same place, a new picture.
      const oldFirst = (await state()).ids[0];
      await page.click("#scan-strip .scan-tile:nth-child(1) .again", { force: true });
      await idle();
      out.rescanned = { old: oldFirst, after: await state() };
      await theme("light");
      await shot("scan-pages-light");
      await theme("dark");
      await shot("scan-pages-dark");
      await theme("light");
      // An empty feeder, and a busy scanner: said plainly.
      await pickScanner("Empty feeder");
      await page.check("#scan-from-feeder");
      await page.click("#scan-go");
      await idle();
      out.empty = await state();
      await pickScanner("Busy scanner");
      await page.click("#scan-go");
      await idle();
      out.busyScanner = await state();
      // Cancel while the feeder is going: the pages so far are kept.
      const had = (await state()).count;
      await pickScanner("Slow feeder");
      await page.check("#scan-from-feeder");
      await page.click("#scan-go");
      await page.waitForFunction((n) => document.querySelectorAll("#scan-strip .scan-tile").length > n, had, { timeout: 30000 });
      out.cancelShown = await page.evaluate(() => !document.getElementById("scan-cancel").hidden);
      await page.click("#scan-cancel");
      await idle();
      out.cancelled = { had, after: await state() };
    }

    // Photos: one with a page in it, one without.
    await page.check("#scan-source-photos");
    const beforePhotos = (await state()).count;
    await page.setInputFiles("#scan-photo-input", cfg.photos);
    await idle();
    out.photos = { before: beforePhotos, after: await state() };
    out.photoFlags = await page.evaluate(() => scanState.pages.filter((p) => p.photo).map((p) => p.found));
    if (cfg.mode === "nowia") {
      await theme("dark");
      await shot("scan-photos-dark");
      await theme("light");
      await shot("scan-photos-light");
    }
    // Keep every page (the desk photo isn't blank, but it's not a page either), then Make PDF.
    await page.uncheck("#scan-blank");
    const pagesBefore = (await state()).count;
    await page.click("#scan-make");
    await page.waitForFunction(() => (reader && reader.activeTab && /^Scan /.test(reader.activeTab.name) && reader.doc)
      || (!scanState.busy && !document.getElementById("scan-msg").hidden), null, { timeout: 240000 })
      .catch(async (e) => { throw new Error("Make PDF didn't finish: " + JSON.stringify(await page.evaluate(() => ({ busy: scanState.busy,
        status: document.getElementById("scan-status").textContent, tabs: reader ? reader.tabs.map((t) => t.name) : null,
        doc: !!(reader && reader.doc), mode: document.body.className })))); });
    const failed = await page.evaluate(() => document.getElementById("scan-msg").hidden ? "" : document.getElementById("scan-msg").textContent);
    if (failed) throw new Error("Make PDF failed: " + failed);
    await page.waitForTimeout(800);
    out.made = await page.evaluate(() => ({ name: reader.activeTab.name, unsaved: !!reader.activeTab.unsaved, pages: reader.doc.numPages,
      madeBy: reader.activeTab.madeBy && { op: reader.activeTab.madeBy.op, notes: reader.activeTab.madeBy.notes, from: reader.activeTab.madeBy.from },
      readerMode: document.body.classList.contains("reader-mode"),
      note: document.querySelector(".fp-note")?.textContent || "" }));
    out.made.expected = pagesBefore;
    // Back to Scan: the pages are still there.
    await page.evaluate(() => select("scan"));
    await page.waitForTimeout(300);
    out.back = await state();
  } finally {
    await browser.close();
  }
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(1); });
