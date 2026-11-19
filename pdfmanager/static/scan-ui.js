/* Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE. */
// ---- Scan: paper to a searchable PDF --------------------------------------------------------
// Pages come from a scanner (Windows: WIA, scanner.py) or from phone photos
// (each page found and flattened, pagephoto.py). They gather in a strip,
// to put in order, turn, take out or scan again; Make PDF cleans them up,
// reads their text and opens the PDF in the reader, not saved yet. The
// pages are kept by the server (a "box") while the app is open.
const scanState = { box: null, pages: [], known: new Set(), status: null, devices: null, busy: false, job: null,
  thumbs: new Map(), built: false, polled: 0, cancelled: false };
const inScan = () => current && current.files === "scan";
const SCAN_DEFAULTS = { source: "scanner", mode: "grey", dpi: "300", from: "flatbed", look: "document", paper: "auto",
  straighten: true, remove_blank: true, upright: true, ocr: true };
function scanPrefs() {
  let kept = {};
  try { kept = JSON.parse(localStorage.getItem("pdfm-scan") || "{}") || {}; } catch (e) { kept = {}; }
  return { ...SCAN_DEFAULTS, ...kept };
}
const scanValue = (name) => document.querySelector(`#scan-host input[name="scan-${name}"]:checked`)?.value;
function scanSettings() {
  return { source: scanValue("source") || "scanner", mode: scanValue("mode") || "grey", dpi: $("scan-dpi")?.value || "300",
    from: scanValue("from") || "flatbed", look: scanValue("look") || "document", paper: $("scan-paper")?.value || "auto",
    straighten: !!$("scan-straighten")?.checked, remove_blank: !!$("scan-blank")?.checked,
    upright: !!$("scan-upright")?.checked, ocr: !!$("scan-ocr")?.checked };
}
function keepScanPrefs() { try { localStorage.setItem("pdfm-scan", JSON.stringify(scanSettings())); } catch (e) { /* not kept */ } }
/** A few choices as radio buttons in a row (a segmented control). */
function scanSeg(name, legend, choices, value, { big = false, describedBy = null } = {}) {
  return el("fieldset", { class: "scan-field", id: `scan-${name}-set` }, el("legend", {}, legend),
    el("div", { class: "seg" + (big ? " big" : "") }, ...choices.map(([v, text, ic]) => el("label", {},
      el("input", { type: "radio", name: `scan-${name}`, value: v, checked: v === value, id: `scan-${name}-${v}`,
        "aria-describedby": describedBy }), ic ? icon(ic) : null, el("span", {}, text)))));
}
function scanCheck(id, label, on, hint = "") {
  const box = el("input", { type: "checkbox", id, checked: on });
  const hintId = id + "-hint";
  if (hint) box.setAttribute("aria-describedby", hintId);
  return el("div", { class: "field check" }, box, el("label", { for: id }, label), hint ? el("div", { class: "hint", id: hintId }, hint) : null);
}
async function scanBox() {
  if (!scanState.box) scanState.box = (await jsonApi("/api/scan/box", {})).box;
  return scanState.box;
}
function buildScan() {
  const host = $("scan-host"), prefs = scanPrefs();
  const photoInput = el("input", { type: "file", id: "scan-photo-input", accept: "image/jpeg,image/png,image/*", multiple: true, hidden: true });
  photoInput.addEventListener("change", () => { if (photoInput.files.length) scanAddPhotos(photoInput.files); photoInput.value = ""; });
  const replaceInput = el("input", { type: "file", id: "scan-replace-input", accept: "image/jpeg,image/png,image/*", hidden: true });
  replaceInput.addEventListener("change", () => { if (replaceInput.files.length) scanAddPhotos(replaceInput.files, replaceInput.dataset.page); replaceInput.value = ""; });
  const drop = el("div", { class: "scan-drop", id: "scan-drop", role: "button", tabindex: "0", "aria-label": "Add photos of pages" },
    icon("from-images"), el("div", {}, el("b", {}, "Drop photos of pages here"), el("br"), "one page a photo, in order"),
    el("button", { type: "button", class: "btn secondary", tabindex: "-1", onclick: (e) => { e.stopPropagation(); photoInput.click(); } }, icon("upload"), "Choose photos"));
  drop.addEventListener("click", () => photoInput.click());
  drop.addEventListener("keydown", (e) => { if (e.target === drop && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); photoInput.click(); } });
  drop.addEventListener("dragover", (e) => { if (hasFiles(e)) { e.preventDefault(); e.stopPropagation(); drop.classList.add("over"); } });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", (e) => { drop.classList.remove("over"); if (hasFiles(e)) { e.preventDefault(); e.stopPropagation(); scanAddPhotos(e.dataTransfer.files); } });

  const scannerPart = el("fieldset", { class: "scan-part", id: "scan-scanner-part" },
    el("legend", { class: "sr-only" }, "Scanner settings"),
    el("div", { class: "scan-field field" }, el("label", { for: "scan-device" }, "Scanner"),
      el("div", { class: "scan-row" }, el("select", { id: "scan-device" }, el("option", { value: "" }, "Looking for scanners…")),
        el("button", { type: "button", class: "chip-btn", id: "scan-refresh", title: "Look for scanners again", "aria-label": "Look for scanners again",
          onclick: () => scanDevices(true) }, icon("rotate"))),
      el("p", { class: "hint scan-links", id: "scan-device-hint" },
        el("button", { type: "button", class: "link-btn", id: "scan-add-network", onclick: () => scanAddNetwork() }, "Add a network scanner…"),
        el("button", { type: "button", class: "link-btn", id: "scan-forget-network", hidden: true, onclick: () => scanForgetNetwork() }, "Forget this one"),
        el("button", { type: "button", class: "link-btn", id: "scan-not-listed", "aria-expanded": "false", "aria-controls": "scan-folder-guide",
          onclick: () => scanFolderGuide() }, "My scanner isn’t listed…")),
      el("div", { class: "scan-row", id: "scan-add-row", hidden: true },
        el("input", { type: "text", id: "scan-address", placeholder: "192.168.1.40", spellcheck: "false", autocomplete: "off",
          "aria-label": "The scanner’s address on your network", "aria-describedby": "scan-add-hint",
          onkeydown: (e) => { if (e.key === "Enter") { e.preventDefault(); scanAddNetworkGo(); } else if (e.key === "Escape") { e.stopPropagation(); scanAddNetwork(false); } } }),
        el("button", { type: "button", class: "btn secondary pb-small", id: "scan-address-add", onclick: () => scanAddNetworkGo() }, "Add")),
      el("p", { class: "hint", id: "scan-add-hint", hidden: true },
        "The address its display or your router shows. Works with scanners and printers that offer AirScan (also called eSCL or Mopria Scan); no driver is needed.")),
    el("div", { class: "scan-guide", id: "scan-folder-guide", hidden: true, role: "group", "aria-labelledby": "scan-guide-title" },
      el("strong", { id: "scan-guide-title" }, "Any scanner: let it save into a folder"),
      el("p", { class: "hint" }, "Every scanner can save its scans into a folder: with its Scan button, or the program that came with it. "
        + "Tell ZPDF which folder, and each scan put there becomes a finished PDF in a “Done” folder inside it, while ZPDF is open."),
      el("ul", { class: "scan-guide-list", id: "scan-guide-list", hidden: true, "aria-label": "Folders watched for scans" }),
      el("div", { class: "scan-field field" }, el("label", { for: "scan-folder" }, "The folder your scanner saves to"),
        el("div", { class: "scan-row" }, el("input", { type: "text", id: "scan-folder", spellcheck: "false", autocomplete: "off", placeholder: "C:\\Users\\you\\Documents\\Scans" }),
          el("button", { type: "button", class: "btn secondary pb-small", id: "scan-folder-browse", hidden: true, onclick: () => scanFolderBrowse() }, "Browse…"))),
      el("div", { class: "scan-field field" }, el("label", { for: "scan-folder-do" }, "Each scan is"),
        el("select", { id: "scan-folder-do" }, el("option", { value: "tool:ocr" }, "Made searchable (OCR)"))),
      el("div", { class: "scan-checks" }, scanCheck("scan-folder-batch", "Pages scanned together become one document", true,
        "For a scanner that saves a file per page: the files of one stack, arriving within a few seconds of each other, make one PDF.")),
      el("div", { class: "scan-row" }, el("button", { type: "button", class: "btn secondary", id: "scan-folder-add", onclick: () => scanFolderAdd() }, "Watch this folder"),
        el("button", { type: "button", class: "link-btn", onclick: () => { scanFolderGuide(false); $("settings-btn").click(); } }, "All watch folders…")),
      el("p", { class: "hint", id: "scan-folder-said", "aria-live": "polite" }),
      // The second way, for a scanner that only has a TWAIN driver: NAPS2 drives it, ZPDF asks NAPS2.
      el("p", { class: "hint", id: "scan-naps2-hint" })),
    scanSeg("mode", "Colour", [["grey", "Greyscale"], ["colour", "Colour"], ["bw", "Black and white"]], prefs.mode),
    el("div", { class: "scan-field field" }, el("label", { for: "scan-dpi" }, "Resolution"),
      el("select", { id: "scan-dpi" }, ...[["150", "150 dpi: quick, smaller"], ["200", "200 dpi"], ["300", "300 dpi: best for documents"]]
        .map(([v, t]) => el("option", { value: v, selected: v === String(prefs.dpi) }, t)))),
    scanSeg("from", "Paper from", [["flatbed", "Glass (flatbed)"], ["feeder", "Document feeder"]], prefs.from, { describedBy: "scan-from-hint" }),
    el("p", { class: "hint", id: "scan-from-hint", hidden: true }),
    el("div", { class: "scan-checks", id: "scan-duplex-row", hidden: true }, scanCheck("scan-duplex", "Both sides of each sheet", false)),
    el("button", { type: "button", class: "btn scan-go", id: "scan-go", onclick: () => scanAcquire() }, icon("scan"), el("span", { id: "scan-go-label" }, "Scan")));
  const photosPart = el("div", { class: "scan-part", id: "scan-photos-part", role: "group", "aria-label": "Photo settings" },
    drop, photoInput, replaceInput,
    scanSeg("look", "Look", [["document", "Document"], ["colour", "Keep colours"], ["photo", "As taken"]], prefs.look, { describedBy: "scan-look-hint" }),
    el("p", { class: "hint", id: "scan-look-hint" }, "Document evens out shadows and makes the paper white and the ink dark."),
    el("div", { class: "scan-field field" }, el("label", { for: "scan-paper" }, "Page size"),
      el("select", { id: "scan-paper" }, ...[["auto", "A4 or Letter, by the page’s shape"], ["a4", "A4"], ["letter", "Letter"]]
        .map(([v, t]) => el("option", { value: v, selected: v === prefs.paper }, t)))));
  const setup = el("section", { class: "card scan-setup", "aria-label": "Where the pages come from" },
    scanSeg("source", "Pages from", [["scanner", "Scanner", "scan"], ["photos", "Photos", "from-images"]], prefs.source, { big: true, describedBy: "scan-why" }),
    el("p", { class: "scan-note", id: "scan-why", hidden: true }),
    scannerPart, photosPart,
    el("div", { class: "scan-sep", role: "presentation" }),
    el("fieldset", { class: "scan-field" }, el("legend", {}, "When making the PDF"),
      el("div", { class: "scan-checks" },
        scanCheck("scan-straighten", "Straighten crooked pages", prefs.straighten),
        scanCheck("scan-blank", "Take out blank pages", prefs.remove_blank),
        scanCheck("scan-upright", "Turn pages the right way up", prefs.upright),
        scanCheck("scan-ocr", "Make the text searchable", prefs.ocr))));

  const pagesCard = el("section", { class: "card scan-pages", "aria-labelledby": "scan-pages-title" },
    el("div", { class: "card-head" }, el("h2", { id: "scan-pages-title" }, "Pages"), el("span", { class: "sub", id: "scan-count" })),
    el("div", { class: "message err scan-msg", id: "scan-msg", role: "alert", hidden: true }),
    el("ol", { class: "scan-strip", id: "scan-strip", "aria-label": "Pages, in order", "aria-describedby": "scan-keys" }),
    el("p", { class: "sr-only", id: "scan-keys" }, "On a page: Alt and the arrow keys move it, R turns it right, Shift+R left, Delete takes it out. Ctrl+Enter makes the PDF."),
    el("div", { class: "scan-empty", id: "scan-empty" }, icon("scan"), el("b", {}, "No pages yet"),
      el("p", { id: "scan-empty-text" }, "Scan a page, or add photos of pages. They show up here as they arrive.")),
    el("div", { class: "scan-foot" },
      el("div", { class: "scan-state" }, el("p", { class: "scan-status", id: "scan-status", role: "status" }),
        el("div", { class: "scan-bar", id: "scan-bar", role: "progressbar", "aria-label": "Progress", "aria-valuemin": "0", "aria-valuemax": "100", hidden: true }, el("i"))),
      el("button", { type: "button", class: "btn secondary", id: "scan-cancel", hidden: true, onclick: scanCancel }, "Cancel"),
      el("button", { type: "button", class: "btn ghost", id: "scan-clear", onclick: scanClear }, "Clear pages"),
      el("button", { type: "button", class: "btn seal", id: "scan-make", onclick: () => scanMake() },
        el("span", { class: "spinner" }), icon("file", "i btn-icon"), "Make PDF")),
    el("span", { class: "sr-only", id: "scan-said", "aria-live": "polite" }));
  host.replaceChildren(setup, pagesCard);
  host.addEventListener("change", (e) => {
    if (e.target.name === "scan-source" || e.target.name === "scan-from") scanShowParts();
    if (e.target.id === "scan-device") scanShowParts();
    if (e.target.name === "scan-look") scanRender();
    keepScanPrefs();
  });
  scanState.built = true;
}
/** What can be chosen now: the scanner's settings or the photos', the feeder when the scanner has one. */
function scanShowParts() {
  const s = scanSettings(), st = scanState.status;
  const scannerOk = !!st?.scanner?.available;
  const scannerRadio = $("scan-source-scanner");
  scannerRadio.disabled = !scannerOk;
  if (!scannerOk && s.source === "scanner" && st) { $("scan-source-photos").checked = true; }
  const source = scanValue("source");
  const why = $("scan-why");
  why.hidden = scannerOk || !st;
  if (!scannerOk && st) why.replaceChildren(icon("alert"), el("span", {}, `The scanner can’t be used here. ${st.scanner.reason} Photos work on any computer.`));
  $("scan-scanner-part").hidden = source !== "scanner";
  $("scan-photos-part").hidden = source !== "photos";
  const device = (scanState.devices || []).find((d) => d.id === $("scan-device").value);
  const feeder = $("scan-from-feeder"), hint = $("scan-from-hint");
  feeder.disabled = !!device && !device.feeder;
  $("scan-from-flatbed").disabled = !!device && !device.flatbed;
  if (feeder.disabled && feeder.checked) $("scan-from-flatbed").checked = true;
  hint.hidden = !(device && !device.feeder);
  hint.textContent = device && !device.feeder ? "This scanner has no document feeder." : "";
  // Both sides: a network scanner whose feeder turns the sheet, with the feeder chosen.
  $("scan-duplex-row").hidden = !(device && device.duplex && scanValue("from") === "feeder");
  $("scan-forget-network").hidden = !(device && device.found === "added");
  $("scan-go").disabled = scanState.busy || !device;
  $("scan-go-label").textContent = scanState.pages.length && scanValue("from") === "flatbed" ? "Scan another page" : "Scan";
  $("scan-upright").disabled = !st?.upright;
  $("scan-ocr").disabled = !st?.ocr;
  for (const [id, off, text] of [["scan-upright", !st?.upright, "Needs the built-in OCR engine or Tesseract."],
    ["scan-ocr", !st?.ocr, "Needs the built-in OCR engine (see the OCR tool)."]]) {
    const box = $(id), row = box.closest(".field");
    row.querySelector(".hint")?.remove();
    if (off && st) { row.append(el("div", { class: "hint", id: id + "-hint" }, text)); box.setAttribute("aria-describedby", id + "-hint"); }
    else box.removeAttribute("aria-describedby");
  }
  scanRender();
}
async function renderScan() {
  if (!scanState.built) buildScan();
  $("scan-host").hidden = false;
  scanShowParts();
  if (!scanState.status) {
    try { scanState.status = await (await api("/api/scan/status")).json(); }
    catch (e) { scanState.status = { scanner: { available: false, reason: String(e.message || e) }, ocr: null, upright: false }; }
    scanShowParts();
    if (scanState.status.scanner.available) scanDevices();
  }
}
async function scanDevices(again = false) {
  const select = $("scan-device"), was = select.value || scanPrefs().device;
  select.replaceChildren(el("option", { value: "" }, "Looking for scanners…"));
  $("scan-refresh").disabled = true;
  try {
    const { devices } = await (await api("/api/scan/devices")).json();
    scanState.devices = devices;
    select.replaceChildren(...(devices.length ? devices.map((d) => el("option", { value: d.id, selected: d.id === was }, d.name))
      : [el("option", { value: "" }, "No scanner found")]));
    if (!devices.length) {
      const wia = scanState.status?.scanner?.wia;
      scanSay("No scanner was found. Check that it is switched on and connected, then press ↻ to look again; a network scanner can also be added by its address, and any scanner can save into a folder ZPDF watches (My scanner isn’t listed). Or use Photos."
        + (wia && !wia.available ? ` (Scanners with a Windows driver can’t be used here: ${wia.reason})` : ""), true);
    }
    else if (again) scanSay(`${plural(devices.length, "scanner")} found.`);
  } catch (e) {
    scanState.devices = [];
    select.replaceChildren(el("option", { value: "" }, "No scanner found"));
    scanSay(capital(e.message || e), true);
  } finally { $("scan-refresh").disabled = false; scanShowParts(); }
}
/** "My scanner isn't listed": any scanner can save into a folder, and ZPDF
 *  watches that folder (a watch folder that takes pictures too). */
let scanFolderTimer = 0;
async function scanFolderGuide(show = $("scan-folder-guide").hidden) {
  $("scan-folder-guide").hidden = !show;
  $("scan-not-listed").setAttribute("aria-expanded", String(show));
  clearInterval(scanFolderTimer);
  if (!show) return;
  // (What the watched folders are doing is kept up to date while the guide is open.)
  scanFolderTimer = setInterval(async () => {
    if ($("scan-folder-guide").hidden || $("scan-host").hidden) { clearInterval(scanFolderTimer); return; }
    try { scanFolderShow(await jsonApi("/api/watch")); } catch (e) { /* next time */ }
  }, 4000);
  $("scan-naps2-hint").textContent = scanState.status?.naps2
    ? "NAPS2 is installed: scanners that only have a TWAIN driver are in the list above, marked “through NAPS2”."
    : "Or scan from ZPDF itself: install NAPS2 (free, naps2.com), and scanners that only have a TWAIN driver show in the list above the next time you open this page.";
  $("scan-folder-browse").hidden = !(isDesktop() && window.pywebview.api.pick_folder);
  $("scan-folder").focus();
  try { scanFolderShow(await jsonApi("/api/watch")); } catch (e) { /* the list is a nicety */ }
}
function scanFolderShow(info) {
  const pick = $("scan-folder-do"), was = pick.value;
  pick.replaceChildren(el("option", { value: "tool:ocr" }, "Made searchable (OCR)"),
    ...info.tools.filter((t) => t.id !== "ocr").map((t) => el("option", { value: "tool:" + t.id }, t.label)),
    ...(info.recipes.length ? [el("optgroup", { label: "Put through a saved recipe" }, ...info.recipes.map((r) => el("option", { value: "recipe:" + r }, r)))] : []));
  if ([...pick.options].some((o) => o.value === was)) pick.value = was;
  const mine = info.folders.filter((f) => f.images), list = $("scan-guide-list");
  list.replaceChildren(...mine.map((f) => el("li", {}, el("span", { class: "wi-folder" }, f.folder), " ", el("span", { class: "wi-state" }, watchState(f)))));
  list.hidden = !mine.length;
}
async function scanFolderBrowse() {
  try { const chosen = await window.pywebview.api.pick_folder(); if (chosen) $("scan-folder").value = chosen; } catch (e) { /* closed */ }
}
async function scanFolderAdd() {
  const folder = $("scan-folder").value.trim(), said = $("scan-folder-said");
  const [kind, ...rest] = $("scan-folder-do").value.split(":");
  said.classList.remove("err");
  if (!folder) { said.textContent = "Type the folder your scanner saves to, or choose it with Browse."; said.classList.add("err"); return; }
  try {
    const info = await jsonApi("/api/watch/add", { folder, [kind]: rest.join(":"), results: "done", originals: "keep", images: true,
      batch: $("scan-folder-batch").checked });
    watchInfo = info;
    scanFolderShow(info);
    $("scan-folder").value = "";
    said.textContent = `ZPDF now watches ${folder}. Scan with your scanner’s own button or program: each scan saved there shows up as a PDF in its “Done” folder, while ZPDF is open.`;
    scheduleWatchNews(1500);
  } catch (e) { said.textContent = capital(e.message || e); said.classList.add("err"); }
}
/** A network scanner that didn't answer the search: asked for by its address
 *  (driverless scanning: AirScan / eSCL), remembered from then on. */
function scanAddNetwork(show = $("scan-add-row").hidden) {
  $("scan-add-row").hidden = $("scan-add-hint").hidden = !show;
  if (show) $("scan-address").focus(); else $("scan-add-network").focus();
}
async function scanAddNetworkGo() {
  const address = $("scan-address").value.trim();
  if (!address) { scanSay("Type the scanner’s address first, for example 192.168.1.40.", true); return; }
  scanSay("Asking the scanner what it can do…");
  $("scan-address-add").disabled = true;
  try {
    const res = await api("/api/scan/add-network", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ address }) });
    const { device } = await res.json();
    scanState.devices = [...(scanState.devices || []).filter((d) => d.id !== device.id), device];
    $("scan-device").replaceChildren(...scanState.devices.map((d) => el("option", { value: d.id, selected: d.id === device.id }, d.name)));
    $("scan-address").value = "";
    scanAddNetwork(false);
    scanSay(`${device.name} was added.`);
  } catch (e) { scanSay(capital(e.message || e), true); }
  finally { $("scan-address-add").disabled = false; }
  scanShowParts();
}
async function scanForgetNetwork() {
  const device = $("scan-device").value;
  if (!device) return;
  try {
    await api("/api/scan/forget-network", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ device }) });
    await scanDevices();
  } catch (e) { scanSay(capital(e.message || e), true); }
}
/** A line under the pages: what's happening, or (``bad``) what went wrong. */
function scanSay(text, bad = false) {
  const msg = $("scan-msg");
  if (bad) { msg.replaceChildren(icon("alert"), el("span", {}, text)); msg.hidden = false; $("scan-status").textContent = ""; }
  else { msg.hidden = true; $("scan-status").textContent = text; }
}
function scanBusy(on, cancellable = true) {
  scanState.busy = on;
  $("scan-make").disabled = on || !scanState.pages.length;
  $("scan-make").classList.toggle("busy", on && scanState.making);
  $("scan-clear").disabled = on || !scanState.pages.length;
  $("scan-cancel").hidden = !on || !cancellable;
  $("scan-cancel").disabled = false;
  $("scan-cancel").textContent = "Cancel";
  if (!on) { $("scan-bar").hidden = true; scanState.making = false; }
  document.querySelectorAll("#scan-strip .scan-acts .chip-btn, .scan-add").forEach((b) => { b.disabled = on; });
  scanShowParts();
}
const scanJobUi = {
  start(id) { scanState.job = id; scanState.cancelled = false; },
  message(text) { $("scan-status").textContent = capital(text); if (scanState.pollPages) scanPoll(); },
  progress(fraction) {
    const bar = $("scan-bar"), pct = Math.round(fraction * 100);
    bar.hidden = false; bar.firstChild.style.width = `${Math.max(3, pct)}%`; bar.setAttribute("aria-valuenow", String(pct));
  },
  cancelled() { scanState.cancelled = true; },
  end() { scanState.job = null; },
};
async function scanCancel() {
  if (!scanState.job) return;
  $("scan-cancel").disabled = true; $("scan-cancel").textContent = "Cancelling…";
  try { await api(`/api/job/${scanState.job}/cancel`, { method: "POST" }); } catch (e) { /* it may just have finished */ }
}
/** Pages the server has that the strip doesn't yet (they arrive one by one from a feeder). */
async function scanPoll(force = false) {
  if (!scanState.box || (!force && Date.now() - scanState.polled < 600)) return;
  scanState.polled = Date.now();
  try {
    const { pages } = await (await api(`/api/scan/pages?box=${encodeURIComponent(scanState.box)}`)).json();
    scanTake(pages);
  } catch (e) { /* next time */ }
}
function scanTake(pages, at = null) {
  let added = 0;
  for (const p of pages) {
    if (scanState.known.has(p.id)) continue;
    scanState.known.add(p.id);
    const page = { ...p, turn: 0, fresh: true };
    if (at === null) scanState.pages.push(page); else scanState.pages.splice(at + added, 0, page);
    added++;
  }
  if (added) { scanRender(); $("scan-said").textContent = `${plural(scanState.pages.length, "page")}`; }
  return added;
}
async function scanAcquire(replace = null) {
  const device = $("scan-device").value;
  if (!device) { scanSay("Choose a scanner first.", true); return; }
  if (scanState.busy) return;
  const s = scanSettings();
  const feeder = !replace && s.from === "feeder";
  scanSay(feeder ? "Scanning the pages in the feeder…" : "Scanning…");
  scanBusy(true, feeder);
  scanState.pollPages = !replace;
  const before = scanState.pages.length;
  try {
    const box = await scanBox();
    const res = await runJob("/api/scan/acquire", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ box, device, mode: s.mode, dpi: Number(s.dpi), source: feeder ? "feeder" : "flatbed",
        duplex: feeder && !$("scan-duplex-row").hidden && $("scan-duplex").checked }) }, scanJobUi);
    if (!res) { await scanPoll(true); scanSay(`Stopped. ${scanState.pages.length > before ? "The pages scanned so far are kept." : "Nothing was scanned."}`); return; }
    const data = await res.json();
    if (replace) {
      const at = scanState.pages.findIndex((p) => p.id === replace);
      const [fresh] = data.pages;
      if (fresh && at >= 0) {
        scanState.known.add(fresh.id);
        scanState.pages.splice(at, 1, { ...fresh, turn: 0, fresh: true });
        scanRender();
        scanSay(`Page ${at + 1} scanned again.`);
        scanFocusTile(at);
      }
      return;
    }
    scanTake(data.pages);
    const got = scanState.pages.length - before;
    scanSay(feeder ? `${plural(got, "page")} scanned from the feeder.`
      : `Page ${scanState.pages.length} scanned. Put the next page on the glass and press Scan another page, or Make PDF when you’re done.`);
  } catch (e) {
    await scanPoll(true);
    const kept = scanState.pages.length > before ? " The pages scanned so far are kept." : "";
    scanSay(capital(e.message || e) + kept, true);
  } finally { scanState.pollPages = false; scanBusy(false); }
}
async function scanAddPhotos(list, replace = null) {
  const files = [...list].filter((f) => isImage(f) || /\.(jpe?g|png|tiff?|bmp|webp)$/i.test(f.name));
  if (!files.length) { scanSay("Choose photos of pages (JPG or PNG).", true); return; }
  if (scanState.busy) return;
  if (scanValue("source") !== "photos") { $("scan-source-photos").checked = true; scanShowParts(); keepScanPrefs(); }
  scanSay(`Finding the page in ${files.length === 1 ? "the photo" : plural(files.length, "photo")}…`);
  scanBusy(true);
  try {
    const box = await scanBox();
    const body = new FormData();
    body.append("box", box);
    body.append("straighten", $("scan-straighten").checked ? "true" : "false");
    for (const f of replace ? files.slice(0, 1) : files) body.append("files", f, f.name);
    const res = await runJob("/api/scan/photos", { method: "POST", body }, scanJobUi);
    if (!res) { scanSay("Stopped: no photos were added."); return; }
    const data = await res.json();
    if (replace) {
      const at = scanState.pages.findIndex((p) => p.id === replace);
      const [fresh] = data.pages;
      if (fresh && at >= 0) { scanState.known.add(fresh.id); scanState.pages.splice(at, 1, { ...fresh, turn: 0, fresh: true }); scanRender(); scanFocusTile(at); }
    } else scanTake(data.pages);
    const lost = data.pages.filter((p) => !p.found).length;
    const parts = [`${plural(data.pages.length, "photo")} added.`];
    if (lost) parts.push(`In ${lost === 1 ? "one" : lost} of them the page’s edges weren’t clear, so ${lost === 1 ? "it is" : "they are"} kept whole (only straightened). A darker background behind the page helps.`);
    if (data.skipped.length) {
      scanSay(`${data.skipped.join(", ")} couldn’t be read: use JPG or PNG photos (on an iPhone: Settings → Camera → Formats → Most Compatible). ${parts.join(" ")}`, true);
    } else scanSay(parts.join(" "));
  } catch (e) {
    scanSay(capital(e.message || e), true);
  } finally { scanBusy(false); }
}
function scanThumbUrl(p) {
  const look = p.photo ? scanValue("look") || "document" : "document";
  const key = `${p.id}|${look}`;
  const known = scanState.thumbs.get(key);
  if (known) return known;
  const pending = api(`/api/scan/thumb?box=${encodeURIComponent(scanState.box)}&page=${encodeURIComponent(p.id)}&look=${look}`)
    .then((r) => r.blob()).then((b) => URL.createObjectURL(b)).catch(() => "");
  scanState.thumbs.set(key, pending);
  return pending;
}
/** The picture turned ``turn`` degrees, as big as its box allows. */
function scanTurnStyle(p) {
  const w = p.width || 1, h = p.height || 1.25, bw = 1, bh = 1.25;
  const flat = Math.min(bw / w, bh / h), side = Math.min(bw / h, bh / w);
  const scale = p.turn % 180 ? side / flat : 1;
  return `rotate(${p.turn}deg) scale(${scale.toFixed(3)})`;
}
let scanDragId = null;
function scanRender() {
  if (!scanState.built) return;
  const strip = $("scan-strip"), n = scanState.pages.length;
  $("scan-count").textContent = n ? plural(n, "page") : "";
  $("scan-empty").hidden = !!n;
  strip.hidden = !n;
  $("scan-make").disabled = scanState.busy || !n;
  $("scan-clear").disabled = scanState.busy || !n;
  const canScan = !!scanState.status?.scanner?.available && !!$("scan-device")?.value;
  const tiles = scanState.pages.map((p, i) => {
    const img = el("img", { alt: "", draggable: "false", style: `transform: ${scanTurnStyle(p)}` });
    const thumb = el("div", { class: "scan-thumb" }, el("span", { class: "loading", "aria-hidden": "true" }));
    Promise.resolve(scanThumbUrl(p)).then((url) => { if (url) { img.src = url; thumb.replaceChildren(img); } });
    const num = i + 1;
    const act = (cls, ic, label, fn) => el("button", { type: "button", class: "chip-btn " + cls, title: label, "aria-label": `${label}: page ${num}`,
      disabled: scanState.busy, onclick: (e) => { e.stopPropagation(); fn(); } }, icon(ic));
    const again = p.photo
      ? act("again", "upload", "Replace with another photo", () => { $("scan-replace-input").dataset.page = p.id; $("scan-replace-input").click(); })
      : canScan ? act("again", "scan", "Scan this page again", () => scanAcquire(p.id)) : null;
    const label = [`Page ${num} of ${n}`, p.turn ? `turned ${p.turn}°` : "", p.photo && p.found === false ? "edges not found, photo kept whole" : ""].filter(Boolean).join(", ");
    const tile = el("li", { class: "scan-tile" + (p.fresh ? " fresh" : ""), tabindex: "0", draggable: "true", "data-id": p.id, "aria-label": label,
      "aria-roledescription": "page" },
      el("span", { class: "pb-pos scan-num", "aria-hidden": "true" }, String(num)),
      thumb,
      el("div", { class: "scan-cap", "aria-hidden": "true" }, p.photo ? (p.found === false ? el("span", { class: "scan-flag", title: "The page’s edges weren’t found: the photo is kept whole" }, "No edges found") : "Photo")
        : `${p.dpi || ""} dpi`),
      el("div", { class: "scan-acts" },
        act("turn-left", "rotate", "Turn left", () => scanTurn(p.id, -90)),
        act("turn-right", "rotate", "Turn right", () => scanTurn(p.id, 90)),
        again,
        act("remove", "delete", "Take out", () => scanRemove(p.id))));
    p.fresh = false;
    tile.addEventListener("keydown", (e) => scanTileKey(e, p.id));
    tile.addEventListener("dragstart", (e) => { scanDragId = p.id; tile.classList.add("dragging"); e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/plain", p.id); });
    tile.addEventListener("dragend", () => { scanDragId = null; document.querySelectorAll(".scan-tile").forEach((t) => t.classList.remove("dragging", "drop-before", "drop-after")); });
    tile.addEventListener("dragover", (e) => {
      if (!scanDragId || scanDragId === p.id) return;
      e.preventDefault();
      const r = tile.getBoundingClientRect(), after = e.clientX > r.left + r.width / 2;
      tile.classList.toggle("drop-before", !after); tile.classList.toggle("drop-after", after);
    });
    tile.addEventListener("dragleave", () => tile.classList.remove("drop-before", "drop-after"));
    tile.addEventListener("drop", (e) => {
      if (!scanDragId) return;
      e.preventDefault(); e.stopPropagation();
      const after = tile.classList.contains("drop-after");
      const from = scanState.pages.findIndex((x) => x.id === scanDragId);
      let to = scanState.pages.findIndex((x) => x.id === p.id) + (after ? 1 : 0);
      if (from < to) to--;
      scanMove(from, to);
    });
    return tile;
  });
  const extra = [];
  const s = scanSettings();
  if (n && s.source === "scanner" && s.from === "flatbed" && canScan) {
    extra.push(el("li", { class: "scan-more" }, el("button", { type: "button", class: "scan-add", id: "scan-add", disabled: scanState.busy, onclick: () => scanAcquire() },
      icon("scan"), "Scan another page")));
  } else if (n && s.source === "photos") {
    extra.push(el("li", { class: "scan-more" }, el("button", { type: "button", class: "scan-add", id: "scan-add", disabled: scanState.busy, onclick: () => $("scan-photo-input").click() },
      icon("upload"), "Add more photos")));
  }
  strip.replaceChildren(...tiles, ...extra);
}
function scanFocusTile(i) { requestAnimationFrame(() => document.querySelectorAll("#scan-strip .scan-tile")[i]?.focus()); }
function scanMove(from, to) {
  if (from < 0 || to < 0 || to >= scanState.pages.length || from === to) return;
  const [p] = scanState.pages.splice(from, 1);
  scanState.pages.splice(to, 0, p);
  scanRender();
  $("scan-said").textContent = `Page moved to position ${to + 1} of ${scanState.pages.length}`;
  scanFocusTile(to);
}
function scanTurn(id, by) {
  const p = scanState.pages.find((x) => x.id === id);
  if (!p) return;
  p.turn = (p.turn + by + 360) % 360;
  const i = scanState.pages.indexOf(p);
  const img = document.querySelectorAll("#scan-strip .scan-tile")[i]?.querySelector("img");
  if (img) img.style.transform = scanTurnStyle(p);  // turns in place, no redraw
  const tile = document.querySelectorAll("#scan-strip .scan-tile")[i];
  tile?.setAttribute("aria-label", `Page ${i + 1} of ${scanState.pages.length}${p.turn ? `, turned ${p.turn}°` : ""}`);
  $("scan-said").textContent = `Page ${i + 1} turned ${by > 0 ? "right" : "left"}`;
}
function scanRemove(id) {
  const i = scanState.pages.findIndex((x) => x.id === id);
  if (i < 0) return;
  const [gone] = scanState.pages.splice(i, 1);
  scanRender();
  appToast(`Page ${i + 1} taken out`, 6000, ["Undo", () => { scanState.pages.splice(Math.min(i, scanState.pages.length), 0, gone); scanRender(); scanFocusTile(i); }]);
  if (scanState.pages.length) scanFocusTile(Math.min(i, scanState.pages.length - 1)); else $("scan-go")?.focus();
}
function scanClear() {
  if (!scanState.pages.length) return;
  const kept = scanState.pages.slice();
  scanState.pages = [];
  scanRender();
  scanSay("");
  appToast(`${plural(kept.length, "page")} cleared`, 6000, ["Undo", () => { scanState.pages = kept; scanRender(); }]);
}
function scanTileKey(e, id) {
  const i = scanState.pages.findIndex((x) => x.id === id);
  if (e.target !== e.currentTarget) return;
  if (e.altKey && (e.key === "ArrowLeft" || e.key === "ArrowUp")) { e.preventDefault(); scanMove(i, i - 1); }
  else if (e.altKey && (e.key === "ArrowRight" || e.key === "ArrowDown")) { e.preventDefault(); scanMove(i, i + 1); }
  else if (e.key === "ArrowLeft" || e.key === "ArrowUp") { e.preventDefault(); scanFocusTile(Math.max(0, i - 1)); }
  else if (e.key === "ArrowRight" || e.key === "ArrowDown") { e.preventDefault(); scanFocusTile(Math.min(scanState.pages.length - 1, i + 1)); }
  else if (e.key === "Delete" || e.key === "Backspace") { e.preventDefault(); scanRemove(id); }
  else if (e.key.toLowerCase() === "r" && !e.ctrlKey && !e.metaKey) { e.preventDefault(); scanTurn(id, e.shiftKey ? -90 : 90); }
}
async function scanMake() {
  if (scanState.busy || !scanState.pages.length) return;
  const s = scanSettings();
  scanState.making = true;
  scanSay(s.ocr && !$("scan-ocr").disabled ? "Making the PDF and reading its text: this can take a while…" : "Making the PDF…");
  scanBusy(true);
  try {
    const box = await scanBox();
    const body = { box, pages: scanState.pages.map((p) => ({ id: p.id, turn: p.turn })), straighten: s.straighten, remove_blank: s.remove_blank,
      upright: s.upright && !$("scan-upright").disabled, ocr: s.ocr && !$("scan-ocr").disabled, look: s.look, paper: s.paper };
    const res = await runJob("/api/scan/make", { method: "POST", body: JSON.stringify(body),
      headers: { "Content-Type": "application/json", ...(isDesktop() ? { "X-Pdfm-Keep": "1" } : {}) } }, scanJobUi);
    if (!res) { scanSay("Stopped: no PDF was made. The pages are still here."); return; }
    const name = decodeURIComponent(res.headers.get("X-Pdfm-Filename") || "") || "Scan.pdf";
    const notes = decodeURIComponent(res.headers.get("X-Pdfm-Note") || "").split("\n").filter(Boolean);
    const bytes = new Uint8Array(await res.arrayBuffer());
    scanSay(`Made ${name}: it’s open in the reader, not saved yet. The pages stay here until you clear them.`);
    await select("reader");
    const tab = await reader.openResult(bytes, name, null, null);
    if (!tab) { scanSay("The PDF was made but couldn’t be shown.", true); return; }
    tab.made({ op: "scan", label: "Scan", from: null, pages: scanState.pages.length,
      sizes: `${plural(scanState.pages.length, "page")} scanned, ${humanSize(bytes.length).replace(" ", " ")}`, notes });
    if (fp.tab === tab) fpShowNote(tab);
    reader.toast(`Opened ${name} in a new tab. It isn’t saved yet: Save asks where.`, 6000);
  } catch (e) {
    scanSay(capital(e.message || e), true);
  } finally { scanBusy(false); }
}

